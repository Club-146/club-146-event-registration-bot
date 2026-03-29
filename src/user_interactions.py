import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Union

from aiogram import Bot, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from pydantic import BaseModel, Field

from botspot.utils.internal import get_logger

logger = get_logger()


class UserInputState(StatesGroup):
    waiting = State()


class PendingRequest(BaseModel):
    question: str
    handler_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    event: Optional[asyncio.Event] = None
    response: Optional[str] = None
    raw_response: Optional[Message] = None
    sent_message_id: Optional[int] = None
    choice_keys: List[str] = Field(default_factory=list)
    choices_dict: Dict[str, str] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, **data):
        super().__init__(**data)
        if self.event is None:
            self.event = asyncio.Event()


class UserInputManager:
    def __init__(self):
        self._pending_requests: Dict[int, Dict[str, PendingRequest]] = {}

    def add_request(
        self,
        chat_id: int,
        handler_id: str,
        question: str,
        choice_keys: Optional[List[str]] = None,
        choices_dict: Optional[Dict[str, str]] = None,
    ) -> PendingRequest:
        if chat_id not in self._pending_requests:
            self._pending_requests[chat_id] = {}

        request = PendingRequest(
            question=question,
            handler_id=handler_id,
            choice_keys=choice_keys or [],
            choices_dict=choices_dict or {},
        )
        self._pending_requests[chat_id][handler_id] = request
        return request

    def get_request(
        self,
        chat_id: int,
        handler_id: Optional[str] = None,
        message_id: Optional[int] = None,
    ) -> Optional[PendingRequest]:
        requests = self._pending_requests.get(chat_id, {})
        if not requests:
            return None

        if handler_id and handler_id in requests:
            return requests[handler_id]

        if message_id is not None:
            for request in requests.values():
                if request.sent_message_id == message_id:
                    return request

        return max(requests.values(), key=lambda x: x.created_at)

    def remove_request(self, chat_id: int, handler_id: str) -> None:
        if chat_id in self._pending_requests:
            self._pending_requests[chat_id].pop(handler_id, None)
            if not self._pending_requests[chat_id]:
                del self._pending_requests[chat_id]


input_manager = UserInputManager()


async def _ask_user_base(
    chat_id: int,
    question: str,
    state: FSMContext,
    timeout: Optional[float] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    notify_on_timeout: bool = True,
    default_choice: Optional[str] = None,
    return_raw: bool = False,
    cleanup: bool = False,
    choice_keys: Optional[List[str]] = None,
    choices_dict: Optional[Dict[str, str]] = None,
    **kwargs,
) -> Optional[Union[str, Message]]:
    from botspot.core.dependency_manager import get_dependency_manager

    if not question or question.strip() == "":
        raise ValueError("Message text cannot be empty")

    deps = get_dependency_manager()
    bot: Bot = deps.bot

    handler_id = f"ask_{datetime.now().timestamp()}"

    if default_choice is not None and return_raw:
        raise ValueError("Cannot return default choice when return_raw is True")

    await state.set_state(UserInputState.waiting)
    await state.update_data(handler_id=handler_id)

    request = input_manager.add_request(
        chat_id,
        handler_id,
        question,
        choice_keys=choice_keys,
        choices_dict=choices_dict,
    )
    assert request.event is not None

    sent_message = await bot.send_message(
        chat_id, question, reply_markup=reply_markup, **kwargs
    )
    request.sent_message_id = sent_message.message_id

    if timeout is None:
        timeout = deps.botspot_settings.ask_user.default_timeout
    if timeout == 0:
        timeout = None

    try:
        await asyncio.wait_for(request.event.wait(), timeout=timeout)
        if cleanup:
            await sent_message.delete()
            if request.raw_response:
                await request.raw_response.delete()
        elif sent_message.reply_markup:
            await sent_message.edit_text(
                text=sent_message.text or "", reply_markup=None
            )

        return (
            request.raw_response
            if (return_raw and request.raw_response is not None)
            else request.response
        )
    except asyncio.TimeoutError:
        if notify_on_timeout:
            if default_choice is not None:
                question += f"\n\n⏰ Auto-selected: {default_choice}"
            else:
                question += "\n\n⏰ No response received within the time limit."
            await sent_message.edit_text(question)
        return default_choice
    finally:
        input_manager.remove_request(chat_id, handler_id)
        await state.clear()


async def ask_user(
    chat_id: int,
    question: str,
    state: FSMContext,
    timeout: Optional[float] = 60.0,
    cleanup: bool = False,
    **kwargs,
) -> Optional[str]:
    result = await _ask_user_base(
        chat_id, question, state, timeout, cleanup=cleanup, **kwargs
    )
    return result  # type: ignore[return-value]


async def ask_user_raw(
    chat_id: int,
    question: str,
    state: FSMContext,
    timeout: Optional[float] = 60.0,
    cleanup: bool = False,
    **kwargs,
) -> Optional[Message]:
    result = await _ask_user_base(
        chat_id,
        question,
        state,
        timeout=timeout,
        return_raw=True,
        cleanup=cleanup,
        **kwargs,
    )
    return result  # type: ignore[return-value]


def _build_keyboard(
    choices: Dict[str, str],
    default_choice: Optional[str],
    highlight_default: bool,
    columns: Optional[int],
) -> InlineKeyboardMarkup:
    items = []
    rows = []

    for data, text in choices.items():
        button = InlineKeyboardButton(
            text=f"⭐ {text}" if highlight_default and data == default_choice else text,
            callback_data=f"choice_{data}",
        )
        items.append(button)
        if len(items) == columns:
            rows.append(items)
            items = []

    if items:
        rows.append(items)

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def ask_user_choice(
    chat_id: int,
    question: str,
    choices: Union[List[str], Dict[str, str]],
    state: FSMContext,
    timeout: Optional[float] = 60.0,
    default_choice: Optional[str] = None,
    highlight_default: bool = True,
    cleanup: bool = False,
    columns: Optional[int] = 1,
    **kwargs,
) -> Optional[str]:
    if isinstance(choices, list):
        choices = {choice: choice for choice in choices}

    keyboard = _build_keyboard(choices, default_choice, highlight_default, columns)

    result = await _ask_user_base(
        chat_id=chat_id,
        question=question,
        state=state,
        timeout=timeout,
        reply_markup=keyboard,
        default_choice=default_choice,
        cleanup=cleanup,
        choice_keys=list(choices.keys()),
        choices_dict=choices,
        **kwargs,
    )
    return result  # type: ignore[return-value]


async def ask_user_confirmation(
    chat_id: int,
    question: str,
    state: FSMContext,
    timeout: Optional[float] = 60.0,
    default_choice: Optional[bool] = True,
    cleanup: bool = False,
    **kwargs,
) -> Optional[bool]:
    result = await ask_user_choice(
        chat_id=chat_id,
        question=question,
        choices={"yes": "Yes", "no": "No"},
        state=state,
        timeout=timeout,
        default_choice="yes" if default_choice else "no",
        cleanup=cleanup,
        **kwargs,
    )
    if result == "yes":
        return True
    if result == "no":
        return False
    return None


async def ask_user_choice_raw(
    chat_id: int,
    question: str,
    choices: Union[List[str], Dict[str, str]],
    state: FSMContext,
    timeout: Optional[float] = 60.0,
    default_choice: Optional[str] = None,
    highlight_default: bool = True,
    cleanup: bool = False,
    add_hint: bool = False,
    columns: Optional[int] = 1,
    **kwargs,
) -> Optional[Message]:
    if isinstance(choices, list):
        choices = {choice: choice for choice in choices}

    keyboard = _build_keyboard(choices, default_choice, highlight_default, columns)
    displayed_question = (
        f"{question}\n\nTip: You can choose an option or type your own response."
        if add_hint
        else question
    )

    result = await _ask_user_base(
        chat_id=chat_id,
        question=displayed_question,
        state=state,
        timeout=timeout,
        reply_markup=keyboard,
        return_raw=True,
        cleanup=cleanup,
        choice_keys=list(choices.keys()),
        choices_dict=choices,
        **kwargs,
    )
    return result  # type: ignore[return-value]


async def handle_user_input(message: types.Message, state: FSMContext) -> None:
    from botspot.core.dependency_manager import get_dependency_manager

    if not await state.get_state() == UserInputState.waiting:
        return

    chat_id = message.chat.id
    state_data = await state.get_data()
    request = input_manager.get_request(
        chat_id, handler_id=state_data.get("handler_id")
    )

    if not request:
        deps = get_dependency_manager()
        bot: Bot = deps.bot
        await bot.send_message(
            chat_id,
            "Sorry, this response came too late or was for a different question. Please try again.",
        )
        await state.clear()
        return

    request.raw_response = message
    request.response = message.text
    assert request.event is not None
    request.event.set()


async def handle_choice_callback(
    callback_query: types.CallbackQuery, state: FSMContext
):
    assert callback_query.data is not None
    if not callback_query.data.startswith("choice_"):
        return

    assert callback_query.message is not None
    chat_id = callback_query.message.chat.id
    state_data = await state.get_data()
    request = input_manager.get_request(
        chat_id,
        handler_id=state_data.get("handler_id"),
        message_id=callback_query.message.message_id,
    )

    if not request:
        await callback_query.answer("This choice is no longer valid.")
        return

    if request.response is not None:
        await callback_query.answer("Your choice has already been recorded.")
        return

    choice = callback_query.data[7:]
    if request.choice_keys and choice not in request.choice_keys:
        await callback_query.answer("This choice is no longer valid.")
        return

    request.response = choice
    assert request.event is not None
    request.event.set()

    await callback_query.answer()
    assert not isinstance(callback_query.message, InaccessibleMessage)

    label = request.choices_dict.get(choice, choice)
    new_text = f"{callback_query.message.text}\n\nВыбрано: {label}"
    try:
        await callback_query.message.edit_text(new_text)
    except TelegramBadRequest as e:
        if "message to edit not found" not in e.message:
            logger.warning(f"Failed to edit message after choice selection: {e}")
    except Exception as e:
        logger.warning(f"Failed to edit message after choice selection: {e}")


def setup_dispatcher(dp):
    dp.message.register(handle_user_input, UserInputState.waiting)
    dp.message.register(
        handle_user_input,
        UserInputState.waiting,
        ~F.text.startswith("/"),
    )
    dp.callback_query.register(
        handle_choice_callback,
        lambda c: c.data and c.data.startswith("choice_"),
        UserInputState.waiting,
    )
