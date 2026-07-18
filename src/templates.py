"""Per-event editable message texts.

Admins edit these through /manage_events → «Тексты сообщений». Each template
declares the placeholders it accepts; anything else is rejected when the admin
submits it. A stored template that still fails at send time falls back to the
default, so a bad edit can never brick the payment flow for users.

Rendering deliberately does not use str.format: the text comes from an admin,
and format() on an attacker-controlled string exposes attribute access.
"""

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from textwrap import dedent
from typing import Dict, FrozenSet, List
import re

from loguru import logger

# Keep the editor deliberately narrower than Telegram's full HTML dialect. The
# admin UI documents only <b>, and strict validation keeps malformed formatting
# from degrading a payment message to literal markup.
ALLOWED_TAGS = frozenset({"b"})
ALLOWED_ENTITIES = frozenset({"lt", "gt", "amp", "quot"})

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class TemplateSpec:
    key: str
    title: str
    placeholders: FrozenSet[str]
    default: str


TEMPLATE_SPECS: Dict[str, TemplateSpec] = {
    "payment_intro": TemplateSpec(
        key="payment_intro",
        title="Оплата: формула",
        placeholders=frozenset({"city", "formula"}),
        default=dedent(
            """
            💰 Оплата мероприятия

            Для оплаты мероприятия используется следующая формула:

            {city} → {formula}
            """
        ),
    ),
    "payment_price_early": TemplateSpec(
        key="payment_price_early",
        title="Оплата: цена (ранняя регистрация)",
        placeholders=frozenset(
            {
                "price_label",
                "regular_amount",
                "deadline",
                "discount",
                "discounted_amount",
                "season",
            }
        ),
        default=dedent(
            """
            {price_label}: {regular_amount} руб.

            При ранней регистрации (до {deadline}) скидка {discount} руб!
            <b>Стоимость билета при ранней регистрации - {discounted_amount} руб.</b>

            Очень ждём вас на {season} встрече! 😊
            """
        ),
    ),
    "payment_price_regular": TemplateSpec(
        key="payment_price_regular",
        title="Оплата: цена",
        placeholders=frozenset({"price_label", "regular_amount", "season"}),
        default=dedent(
            """
            <b>{price_label}: {regular_amount} руб.</b>

            Очень ждём вас на {season} встрече! 😊
            """
        ),
    ),
    "payment_details": TemplateSpec(
        key="payment_details",
        title="Оплата: реквизиты",
        placeholders=frozenset({"pay_url", "phone", "name"}),
        default=dedent(
            """
            Оплатить взнос на сайте (удобно картой):
            {pay_url}

            Запасной вариант — перевод по номеру:
            В Сбербанк по номеру телефона
            Номер телефона - {phone}
            Получатель - {name}

            После оплаты отправьте скриншот подтверждения в этот чат.
            """
        ),
    ),
}


class _HtmlChecker(HTMLParser):
    """Collects unsupported and unbalanced tags."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: List[str] = []
        self.bad_tags: List[str] = []
        self.bad_attributes: List[str] = []
        self.bad_entities: List[str] = []
        self.raw_markup: List[str] = []
        self.unclosed: List[str] = []
        self.stray_close: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.bad_tags.append(tag)
            return
        if attrs:
            self.bad_attributes.append(tag)
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag not in ALLOWED_TAGS:
            self.bad_tags.append(tag)
            return
        if not self.stack or self.stack[-1] != tag:
            self.stray_close.append(tag)
            return
        self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        self.raw_markup.append(f"<{tag}/>")

    def handle_data(self, data):
        self.raw_markup.extend(char for char in "<>&" if char in data)

    def handle_entityref(self, name):
        if name not in ALLOWED_ENTITIES:
            self.bad_entities.append(name)

    def handle_charref(self, name):
        # Telegram accepts numeric entities.
        return

    def handle_comment(self, data):
        self.raw_markup.append("comment")

    def handle_decl(self, _decl):
        self.raw_markup.append("declaration")

    def handle_pi(self, data):
        self.raw_markup.append("processing instruction")

    def unknown_decl(self, data):
        self.raw_markup.append("declaration")

    def finish(self):
        self.close()
        self.unclosed = list(self.stack)
        return self


def validate_template(spec: TemplateSpec, text: str) -> List[str]:
    """Admin-facing reasons the text is unusable. Empty list means it's fine."""
    errors: List[str] = []

    if not text.strip():
        errors.append("Текст не может быть пустым.")
        return errors

    used = set(PLACEHOLDER_RE.findall(text))
    unknown = used - spec.placeholders
    if unknown:
        allowed = ", ".join("{" + p + "}" for p in sorted(spec.placeholders))
        got = ", ".join("{" + p + "}" for p in sorted(unknown))
        errors.append(f"Неизвестные подстановки: {got}. Доступны: {allowed}")

    missing = spec.placeholders - used
    if missing:
        required = ", ".join("{" + p + "}" for p in sorted(missing))
        errors.append(f"Обязательные подстановки пропущены: {required}")

    # A lone "{" or "}" that isn't part of a placeholder is almost always a typo
    # that would silently ship to users as a stray brace.
    without = PLACEHOLDER_RE.sub("", text)
    if "{" in without or "}" in without:
        errors.append("Непарная скобка { или }. Подстановки пишутся как {name}.")

    checker = _HtmlChecker()
    try:
        checker.feed(text)
        checker.finish()
    except Exception as e:  # pragma: no cover - HTMLParser is lenient
        errors.append(f"Не удалось разобрать HTML: {e}")
        return errors

    if checker.bad_tags:
        tags = ", ".join(sorted(set(checker.bad_tags)))
        errors.append(
            f"Telegram не поддерживает теги: {tags}. "
            f"Можно: {', '.join(sorted(ALLOWED_TAGS))}. "
            "Обычный текст со знаком < нужно писать как &lt;"
        )
    if checker.bad_attributes:
        errors.append("У тега <b> не должно быть атрибутов.")
    if checker.bad_entities:
        entities = ", ".join("&" + e + ";" for e in sorted(set(checker.bad_entities)))
        errors.append(f"Неподдерживаемые HTML-сущности: {entities}")
    if checker.raw_markup:
        errors.append(
            "Символы <, > и & в обычном тексте нужно экранировать как &lt;, &gt; и &amp;."
        )
    if checker.unclosed:
        errors.append(f"Незакрытые теги: {', '.join(checker.unclosed)}")
    if checker.stray_close:
        errors.append(f"Лишние закрывающие теги: {', '.join(checker.stray_close)}")

    return errors


def _substitute(text: str, context: Dict[str, object]) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name in context:
            return escape(str(context[name]), quote=True)
        return match.group(0)

    return PLACEHOLDER_RE.sub(replace, text)


def get_template(event, key: str) -> str:
    """The admin's text for this event, or the default when unset/invalid."""
    spec = TEMPLATE_SPECS[key]
    stored = (event or {}).get("templates", {}).get(key)
    if not stored:
        return spec.default
    if validate_template(spec, stored):
        logger.warning(
            f"Event template {key!r} is stored but invalid; using default. "
            "Admin should re-edit it."
        )
        return spec.default
    return stored


def render(event, key: str, context: Dict[str, object]) -> str:
    """Render an event's template, falling back to the default on any failure."""
    spec = TEMPLATE_SPECS[key]
    try:
        return _substitute(get_template(event, key), context)
    except Exception:
        logger.exception(f"Failed to render template {key!r}; using default")
        return _substitute(spec.default, context)
