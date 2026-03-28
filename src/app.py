import re
from aiogram.types import Message
from datetime import datetime
from enum import Enum
from loguru import logger
from pydantic import SecretStr, BaseModel
from pydantic_settings import BaseSettings
from typing import Optional, Tuple, List, Dict

from botspot import get_database
from botspot.utils import send_safe


class EventStatus(str, Enum):
    UPCOMING = "upcoming"
    REGISTRATION_CLOSED = "registration_closed"
    PASSED = "passed"
    ARCHIVED = "archived"


class PricingType(str, Enum):
    FIXED_BY_YEAR = "fixed_by_year"
    FORMULA = "formula"
    FREE = "free"


class GraduateType(str, Enum):
    GRADUATE = "GRADUATE"
    TEACHER = "TEACHER"
    NON_GRADUATE = "NON_GRADUATE"
    ORGANIZER = "ORGANIZER"


# Mapping for human-readable graduate types
GRADUATE_TYPE_MAP = {
    GraduateType.GRADUATE.value: "Выпускник",
    GraduateType.TEACHER.value: "Учитель",
    GraduateType.NON_GRADUATE.value: "Друг",
    GraduateType.ORGANIZER.value: "Организатор",
}
GRADUATE_TYPE_MAP_PLURAL = {
    GraduateType.GRADUATE.value: "Выпускники",
    GraduateType.TEACHER.value: "Учителя",
    GraduateType.NON_GRADUATE.value: "Друзья",
    GraduateType.ORGANIZER.value: "Организаторы",
}

# Mapping for payment statuses
PAYMENT_STATUS_MAP = {
    "confirmed": "Оплачено",
    "pending": "Оплачу позже",
    "declined": "Отклонено",
    None: "Не оплачено",
    "Не оплачено": "Не оплачено",  # For backward compatibility with existing data
}

# City prepositional case mapping for event creation
CITY_PREPOSITIONAL_MAP = {
    "Москва": "Москве",
    "Пермь": "Перми",
    "Санкт-Петербург": "Санкт-Петербурге",
    "Белград": "Белграде",
    "Казань": "Казани",
    "Новосибирск": "Новосибирске",
    "Екатеринбург": "Екатеринбурге",
    "Нижний Новгород": "Нижнем Новгороде",
    "Тбилиси": "Тбилиси",
}


class AppSettings(BaseSettings):
    """Basic src configuration"""

    telegram_bot_token: SecretStr
    spreadsheet_id: Optional[str] = None
    logs_chat_id: Optional[int] = None
    events_chat_id: int
    payment_phone_number: str
    payment_name: str

    delay_messages: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


class RegisteredUser(BaseModel):
    full_name: str
    graduation_year: int
    class_letter: str
    target_city: str  # Denormalized display data from event
    event_id: str  # Primary routing key — references events collection
    user_id: Optional[int] = None
    username: Optional[str] = None
    graduate_type: GraduateType = GraduateType.GRADUATE


class FeedbackData(BaseModel):
    """Model for feedback data"""

    user_id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    event_id: Optional[str] = None
    city: Optional[str] = None
    attended: Optional[bool] = None
    recommendation_level: Optional[str] = None
    recommendation_feedback: Optional[str] = None
    venue_rating: Optional[str] = None
    venue_feedback: Optional[str] = None
    food_rating: Optional[str] = None
    food_feedback: Optional[str] = None
    entertainment_rating: Optional[str] = None
    entertainment_feedback: Optional[str] = None
    help_interest: Optional[str] = None
    comments: Optional[str] = None
    feedback_format_preference: Optional[str] = None

    class Config:
        extra = "ignore"  # Ignore extra fields in the dict


class App:
    name = "146 Meetup Register Bot"

    registration_collection_name = "registered_users"
    event_logs_collection_name = "event_logs"
    deleted_users_collection_name = "deleted_users"
    events_collection_name = "events"

    def __init__(self, **kwargs):
        from src.export import SheetExporter

        self.settings = AppSettings(**kwargs)
        self.sheet_exporter = SheetExporter(
            self.settings.spreadsheet_id or "", app=self
        )

        self._collection = None
        self._event_logs = None
        self._deleted_users = None
        self._events_col = None

    async def startup(self):
        """Run startup tasks like fixing the database and initializing collections."""
        logger.info("Running src startup tasks...")

        # Initialize collections
        _ = self.collection
        _ = self.event_logs
        _ = self.deleted_users
        _ = self.events_col

        # Run database migrations (includes seeding new events + archiving old ones)
        from src.migrations import run_migrations

        await run_migrations(self)

        # Auto-update event statuses (mark passed events)
        await self._update_event_statuses()

        # Fix database (SPb, Belgrade, teachers should have 'confirmed' payment status)
        fix_results = await self._fix_database()
        if fix_results["total_fixed"] > 0:
            logger.info(
                f"Database fix applied to {fix_results['total_fixed']} records:"
            )
            logger.info(f"- SPb: {fix_results['spb_fixed']}")
            logger.info(f"- Belgrade: {fix_results['belgrade_fixed']}")
            logger.info(f"- Teachers: {fix_results['teachers_fixed']}")

    @property
    def collection(self):
        if self._collection is None:
            self._collection = get_database().get_collection(
                self.registration_collection_name
            )
        return self._collection

    @property
    def event_logs(self):
        if self._event_logs is None:
            self._event_logs = get_database().get_collection(
                self.event_logs_collection_name
            )
        return self._event_logs

    @property
    def deleted_users(self):
        if self._deleted_users is None:
            self._deleted_users = get_database().get_collection(
                self.deleted_users_collection_name
            )
        return self._deleted_users

    @property
    def events_col(self):
        if self._events_col is None:
            self._events_col = get_database().get_collection(
                self.events_collection_name
            )
        return self._events_col

    # ---- Event methods ----

    async def get_active_events(self) -> List[Dict]:
        """Get all events that are upcoming or have open registration."""
        cursor = self.events_col.find(
            {
                "status": {"$in": ["upcoming", "registration_closed"]},
            }
        ).sort("date", 1)
        return await cursor.to_list(length=None)

    async def get_enabled_events(self) -> List[Dict]:
        """Get all events that are enabled for registration (upcoming + enabled)."""
        cursor = self.events_col.find(
            {
                "status": "upcoming",
                "enabled": True,
            }
        ).sort("date", 1)
        return await cursor.to_list(length=None)

    async def get_event_by_id(self, event_id: str) -> Optional[Dict]:
        """Get a single event by its MongoDB _id."""
        from bson import ObjectId

        try:
            return await self.events_col.find_one({"_id": ObjectId(event_id)})
        except Exception:
            return None

    async def get_event_by_city_and_date(
        self, city: str, date: datetime
    ) -> Optional[Dict]:
        """Find an event matching city and date."""
        return await self.events_col.find_one({"city": city, "date": date})

    async def get_all_events(self) -> List[Dict]:
        """Get all events (for admin)."""
        cursor = self.events_col.find().sort("date", -1)
        return await cursor.to_list(length=None)

    async def create_event(self, event_data: Dict) -> str:
        """Create a new event. Returns the inserted _id as string."""
        event_data["created_at"] = datetime.now()
        event_data["updated_at"] = datetime.now()
        result = await self.events_col.insert_one(event_data)
        return str(result.inserted_id)

    async def update_event(self, event_id: str, updates: Dict) -> bool:
        """Update event fields. Returns True if modified."""
        from bson import ObjectId

        updates["updated_at"] = datetime.now()
        result = await self.events_col.update_one(
            {"_id": ObjectId(event_id)}, {"$set": updates}
        )
        return result.modified_count > 0

    def is_event_passed(self, event: Dict) -> bool:
        """Check if an event's date has passed."""
        return datetime.now() > event["date"]

    async def get_event_for_registration(self, registration: Dict) -> Optional[Dict]:
        """Get the event associated with a registration.

        Tries event_id first, then falls back to target_city lookup for legacy data.
        """
        if registration.get("event_id"):
            return await self.get_event_by_id(registration["event_id"])

        # Legacy fallback: find an event matching target_city
        target_city = registration.get("target_city", "")
        # For old registrations, try to find matching archived events
        event = await self.events_col.find_one(
            {"$or": [{"city": target_city}, {"name": {"$regex": target_city}}]}
        )
        return event

    async def get_registration_count_for_event(self, event_id: str) -> int:
        """Count registrations for a specific event."""
        return await self.collection.count_documents({"event_id": event_id})

    def calculate_event_payment(
        self,
        event: Dict,
        graduation_year: int,
        graduate_type: str = GraduateType.GRADUATE.value,
    ) -> Tuple[int, int, int, int]:
        """Calculate payment for an event based on its pricing config.

        Returns: (regular_amount, discount, discounted_amount, formula_amount)
        """
        # Teachers and organizers free if specified
        if graduate_type in event.get("free_for_types", []):
            return 0, 0, 0, 0

        pricing_type = event.get("pricing_type", "formula")

        if pricing_type == "free":
            return 0, 0, 0, 0
        elif pricing_type == "fixed_by_year":
            year_map = event.get("year_price_map", {})
            # MongoDB stores keys as strings
            amount = year_map.get(str(graduation_year), 0)
            if amount == 0:
                # Try max price for years not in map
                amounts = [int(v) for v in year_map.values() if v]
                amount = max(amounts) if amounts else 0
            return amount, 0, amount, amount
        elif pricing_type == "formula":
            base = event.get("price_formula_base", 0)
            rate = event.get("price_formula_rate", 0)
            ref_year = event.get("price_formula_reference_year", 2026)
            step = event.get("price_formula_step", 1)
            years_since = max(0, ref_year - graduation_year)
            formula_amount = base + rate * (years_since // step)

            regular_amount = formula_amount

            # Non-graduates: use guest_price_minimum if set, else formula for ~15 years ago
            if graduate_type == GraduateType.NON_GRADUATE.value:
                guest_min = event.get("guest_price_minimum", 0)
                if guest_min > 0:
                    regular_amount = guest_min
                else:
                    ref_years = 15
                    regular_amount = base + rate * (ref_years // step)
                formula_amount = regular_amount

            # Early bird discount
            early_bird_discount = event.get("early_bird_discount", 0)
            early_bird_deadline = event.get("early_bird_deadline")
            if (
                early_bird_deadline
                and datetime.now().date() <= early_bird_deadline.date()
                and early_bird_discount > 0
            ):
                discount = early_bird_discount
                discounted_amount = max(0, regular_amount - discount)
            else:
                discount = 0
                discounted_amount = regular_amount

            return regular_amount, discount, discounted_amount, formula_amount

        return 0, 0, 0, 0

    def calculate_guest_price(
        self, event: Dict, registrant_price: int
    ) -> Tuple[int, int]:
        """Calculate the price for a single guest.

        Returns: (regular_price, discounted_price)
        regular_price = max(guest_price_minimum, registrant_price)
        discounted_price = regular_price - early_bird_discount (if applicable)
        """
        minimum = event.get("guest_price_minimum", 0)
        regular_price = max(minimum, registrant_price)

        # Apply early bird discount to guest price
        early_bird_discount = event.get("early_bird_discount", 0)
        early_bird_deadline = event.get("early_bird_deadline")
        if (
            early_bird_deadline
            and datetime.now().date() <= early_bird_deadline.date()
            and early_bird_discount > 0
        ):
            discounted_price = max(0, regular_price - early_bird_discount)
        else:
            discounted_price = regular_price

        return regular_price, discounted_price

    async def _update_event_statuses(self):
        """Mark events as 'passed' if their date is in the past."""
        now = datetime.now()
        result = await self.events_col.update_many(
            {
                "date": {"$lt": now},
                "status": {"$in": ["upcoming", "registration_closed"]},
            },
            {"$set": {"status": "passed", "updated_at": now}},
        )
        if result.modified_count > 0:
            logger.info(f"Marked {result.modified_count} events as passed.")

    async def get_user_active_registrations(self, user_id: int) -> List[Dict]:
        """Get registrations for a user, filtering out archived events."""
        registrations = await self.get_user_registrations(user_id)
        active = []
        for reg in registrations:
            event = await self.get_event_for_registration(reg)
            if event and event.get("status") != "archived":
                active.append(reg)
        return active

    async def save_registered_user(
        self,
        registered_user: RegisteredUser,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
    ):
        """Save a user registration with optional user_id and username"""
        # Convert the model to a dict for MongoDB storage
        data = registered_user.model_dump()
        # Convert the enums to their string values for MongoDB storage
        if "graduate_type" in data and isinstance(data["graduate_type"], GraduateType):
            data["graduate_type"] = data[
                "graduate_type"
            ].value.upper()  # Ensure uppercase

        # Add user_id and username if provided
        if user_id is not None:
            data["user_id"] = user_id
        if username is not None:
            data["username"] = username

        # Copy data for event log (exclude some fields for brevity)
        log_data = {
            "action": "save_registration",
            "full_name": data.get("full_name"),
            "graduation_year": data.get("graduation_year"),
            "class_letter": data.get("class_letter"),
            "target_city": data.get("target_city"),
            "graduate_type": data.get("graduate_type"),
        }

        # Check if user is already registered for this specific event
        is_update = False
        if user_id is not None:
            existing = await self.collection.find_one(
                {"user_id": user_id, "event_id": data["event_id"]}
            )

            if existing:
                # Update existing registration for this city
                await self.collection.update_one(
                    {"_id": existing["_id"]}, {"$set": data}
                )
                log_data["action"] = "update_registration"
                log_data["existing_id"] = str(existing["_id"])
                is_update = True

        if not is_update:
            # Insert new record
            result = await self.collection.insert_one(data)
            log_data["new_id"] = str(result.inserted_id)

        # Log the registration action
        await self.save_event_log("user_registration", log_data, user_id, username)

    async def save_registration_guests(
        self, user_id: int, event_id: str, guests: List[Dict]
    ):
        """Save guest list to an existing registration.

        Args:
            user_id: Telegram user ID
            event_id: Event ID from events collection
            guests: list of {"name": str, "price": int}
        """
        await self.collection.update_one(
            {"user_id": user_id, "event_id": event_id},
            {"$set": {"guests": guests, "guest_count": len(guests)}},
        )

    async def get_user_registrations(self, user_id: int):
        """Get all registrations for a user"""
        cursor = self.collection.find({"user_id": user_id})
        return await cursor.to_list(length=None)

    async def get_user_registration(self, user_id: int):
        """Get existing registration for a user (returns first one found)"""
        registrations = await self.get_user_registrations(user_id)
        return registrations[0] if registrations else None

    async def delete_user_registration(
        self,
        user_id: int,
        event_id: Optional[str] = None,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
    ):
        """
        Move a user's registration to deleted_users collection instead of permanent deletion

        Args:
            user_id: The user's Telegram ID
            event_id: Optional event_id to delete specific registration. If None, deletes all.
            username: Optional user's Telegram username for logging
            full_name: Optional user's full name for logging
        """
        # Log the deletion event
        log_data = {"action": "delete_registration"}
        if event_id:
            log_data["event_id"] = event_id
        if full_name:
            log_data["full_name"] = full_name

        await self.save_event_log("user_deletion", log_data, user_id, username)

        # Move the user to deleted_users collection
        return await self.move_user_to_deleted(user_id, event_id)

    def validate_full_name(self, full_name: str) -> Tuple[bool, str]:
        """
        Validate a user's full name

        Args:
            full_name: The full name to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check if full_name is None
        if full_name is None:
            return False, "Отсутствует имя. Пожалуйста, попробуйте ещё раз."

        # Check if name has at least 2 words
        words = full_name.strip().split()
        if len(words) < 2:
            return False, "Пожалуйста, укажите хотя бы имя и фамилию :)"

        # Check if name contains only Russian letters, spaces, and hyphens
        if not re.match(r"^[а-яА-ЯёЁ\s\-]+$", full_name):
            return False, "По-русски, пожалуйста :)"

        return True, ""

    def validate_graduation_year(self, year: int) -> Tuple[bool, str]:
        """
        Validate a graduation year

        Args:
            year: The graduation year to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        current_year = datetime.now().year
        current_month = datetime.now().month

        # Check if year is in valid range
        if year < 1995:
            return False, "Год выпуска должен быть не раньше 1995."

        # If year is this year
        if year == current_year:
            if current_month >= 6:
                return True, ""
            else:
                return (
                    False,
                    "Извините, регистрация только для выпускников. Приходите после выпуска!",
                )

        if year > current_year:
            if year <= current_year + 4:
                return (
                    False,
                    "Извините, регистрация только для выпускников. Приходите после выпуска!",
                )
            else:
                return False, f"Год выпуска не может быть позже {current_year + 4}."

        return True, ""

    def validate_class_letter(self, letter: str) -> Tuple[bool, str]:
        """
        Validate a class letter

        Args:
            letter: The class letter to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not letter:
            return False, "Пожалуйста, укажите букву класса."

        # Check if letter contains only Russian letters
        if not re.match(r"^[а-яА-ЯёЁ]+$", letter):
            return False, "Буква класса должна быть на русском языке."

        # Check if letter is only one character
        if len(letter) > 1:
            return False, "Буква класса должна быть только одним символом."

        return True, ""

    def parse_graduation_year_and_class_letter(
        self, year_and_class: str
    ) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """
        Parse graduation year and class letter from user input

        Args:
            year_and_class: The user input to parse

        Returns:
            Tuple of (year, class_letter, error_message)
        """
        year_and_class = year_and_class.strip()

        # Case 0: the class letter is not specified
        if year_and_class.isdigit():
            year = int(year_and_class)
            valid, error = self.validate_graduation_year(year)
            if not valid:
                return None, None, error
            return year, "", "Пожалуйста, укажите также букву класса."

        try:
            # Case 1 - "2025 Б"
            parts = year_and_class.split()
            if len(parts) == 2:
                year = int(parts[0])
                letter = parts[1].upper()
            # Case 2 - "2025Б"
            elif len(year_and_class) > 4 and year_and_class[0:4].isdigit():
                year = int(year_and_class[0:4])
                letter = year_and_class[4:].upper()
            # Case 3: fallback to simple split
            else:
                year, letter = year_and_class.split(maxsplit=1)
                year = int(year)
                letter = letter.upper()

            # Validate year
            valid_year, error_year = self.validate_graduation_year(year)
            if not valid_year:
                return None, None, error_year

            # Validate letter
            valid_letter, error_letter = self.validate_class_letter(letter)
            if not valid_letter:
                return None, None, error_letter

            return year, letter, None

        except (ValueError, IndexError):
            return (
                None,
                None,
                "Неверный формат. Пожалуйста, введите год выпуска и букву класса (например, '2003 Б').",
            )

    def export_registered_users_to_google_sheets(self, event_id: Optional[str] = None):
        return self.sheet_exporter.export_registered_users(event_id=event_id)

    async def export_to_csv(self, event_id: Optional[str] = None):
        """Export registered users to CSV"""
        return await self.sheet_exporter.export_to_csv(event_id=event_id)

    async def export_deleted_users_to_csv(self, event_id: Optional[str] = None):
        """Export deleted users to CSV"""
        return await self.sheet_exporter.export_deleted_users_to_csv(event_id=event_id)

    async def export_feedback_to_sheets(self, event_id: Optional[str] = None):
        """Export feedback to Google Sheets"""
        return await self.sheet_exporter.export_feedback_to_sheets(event_id=event_id)

    async def export_feedback_to_csv(self, event_id: Optional[str] = None):
        """Export feedback to CSV"""
        return await self.sheet_exporter.export_feedback_to_csv(event_id=event_id)

    async def log_to_chat(
        self, message: str, chat_type: str = "logs"
    ) -> Optional[Message]:
        """
        Log a message to the specified chat

        Args:
            message: The message to log
            chat_type: Either "logs" or "events"

        Returns:
            The sent message object or None if chat ID is not configured
        """
        chat_id = None
        if chat_type == "logs" and self.settings.logs_chat_id:
            chat_id = self.settings.logs_chat_id
        elif chat_type == "events" and self.settings.events_chat_id:
            chat_id = self.settings.events_chat_id

        if not chat_id:
            return None

        try:
            return await send_safe(chat_id, message)
        except Exception as e:
            logger.error(f"Failed to log to {chat_type} chat: {e}")
            return None

    async def log_registration_step(
        self, user_id: int, username: str | None, step: str, data: str = ""
    ) -> Optional[Message]:
        """
        Log a registration step to the logs chat

        Args:
            user_id: User's Telegram ID
            username: User's Telegram username
            step: The registration step being performed
            data: Additional data about the step

        Returns:
            The sent message object or None if logs chat ID is not configured
        """
        message = f"👤 Пользователь: {username or user_id}\n"
        message += f"🔄 Шаг: {step}\n"

        if data:
            message += f"📝 Данные: {data}"

        return await self.log_to_chat(message, "logs")

    async def log_registration_completed(
        self,
        user_id: int,
        username: str,
        full_name: str,
        graduation_year: int,
        class_letter: str,
        city: str,
        graduate_type: str = GraduateType.GRADUATE.value,
        guests: Optional[List[Dict]] = None,
    ) -> None:
        """
        Log a completed registration to the events chat

        Args:
            user_id: User's Telegram ID
            username: User's Telegram username
            full_name: User's full name
            graduation_year: User's graduation year
            class_letter: User's class letter
            city: The city of the event
            graduate_type: Type of participant (graduate, teacher, non_graduate)
        """
        message = "✅ НОВАЯ РЕГИСТРАЦИЯ\n\n"
        message += f"👤 Пользователь: {username or user_id}\n"
        message += f"📋 ФИО: {full_name}\n"

        # Format graduation info based on graduate type
        if graduate_type == GraduateType.TEACHER.value:
            message += "👨‍🏫 Статус: Учитель\n"
        elif graduate_type == GraduateType.NON_GRADUATE.value:
            message += "👥 Статус: Не выпускник\n"
        elif graduate_type == GraduateType.ORGANIZER.value:
            message += "🛠️ Статус: Организатор\n"
        else:
            message += f"🎓 Выпуск: {graduation_year} {class_letter}\n"

        message += f"🏙️ Город: {city}\n"

        # Add payment status for different participant types
        if graduate_type == GraduateType.TEACHER.value:
            message += "💰 Оплата: Бесплатно (учитель)\n"
        elif graduate_type == GraduateType.ORGANIZER.value:
            message += "💰 Оплата: Бесплатно (организатор)\n"
        elif city == "Белград":
            message += "💰 Оплата: За свой счет (Белград)\n"

        if guests:
            message += f"\n👥 Гости ({len(guests)}):\n"
            for g in guests:
                message += f"  • {g['name']} — {g['price']}₽\n"

        await self.log_to_chat(message, "events")

    async def log_registration_canceled(
        self,
        user_id: int,
        username: str,
        full_name: str,
        city: Optional[str] = None,
    ) -> None:
        """
        Log a canceled registration to the events chat

        Args:
            user_id: User's Telegram ID
            username: User's Telegram username
            city: The city of the canceled registration (or None if all)
        """
        message = "❌ ОТМЕНА РЕГИСТРАЦИИ\n\n"
        message += f"👤 Пользователь: {username or user_id}\n"
        message += f"📋 ФИО: {full_name}\n"

        if city:
            message += f"🏙️ Город: {city}\n"
        else:
            message += "🏙️ Все города\n"

        await self.log_to_chat(message, "events")

    async def save_payment_info(
        self,
        user_id: int,
        event_id: Optional[str] = None,
        discounted_amount: Optional[int] = None,
        regular_amount: Optional[int] = None,
        screenshot_message_id: Optional[int] = None,
        formula_amount: Optional[int] = None,
        username: Optional[str] = None,
        payment_status: Optional[str] = None,
    ):
        """
        Save payment information for a user

        Args:
            user_id: The user's Telegram ID
            event_id: The event ID
            discounted_amount: The payment amount with early discount
            regular_amount: The regular payment amount without discount
            screenshot_message_id: ID of the message containing the payment screenshot
            formula_amount: The payment amount calculated by formula
            username: Optional user's Telegram username for logging
            payment_status: Payment status (pending, confirmed, None for just registered)
        """
        # Update the user's registration with payment info
        update_data = {
            "discounted_payment_amount": discounted_amount,
            "regular_payment_amount": regular_amount,
            "payment_screenshot_id": screenshot_message_id,
            "payment_status": payment_status,
            "payment_timestamp": datetime.now().isoformat(),
        }

        # Add formula amount if provided
        if formula_amount is not None:
            update_data["formula_payment_amount"] = formula_amount

        # Get user registration for logging
        user_data = await self.collection.find_one(
            {"user_id": user_id, "event_id": event_id}
        )
        full_name = user_data.get("full_name") if user_data else None

        # Log payment info submission
        log_data = {
            "action": "save_payment_info",
            "event_id": event_id,
            "discounted_amount": discounted_amount,
            "regular_amount": regular_amount,
            "has_screenshot": screenshot_message_id is not None,
            "full_name": full_name,
        }
        if formula_amount is not None:
            log_data["formula_amount"] = formula_amount

        await self.save_event_log("payment_info", log_data, user_id, username)

        # Update the database
        await self.collection.update_one(
            {"user_id": user_id, "event_id": event_id},
            {"$set": update_data},
        )

    async def update_payment_status(
        self,
        user_id: int,
        event_id: Optional[str] = None,
        status: Optional[str] = None,
        admin_comment: Optional[str] = None,
        payment_amount: Optional[int] = None,
        admin_id: Optional[int] = None,
        admin_username: Optional[str] = None,
    ):
        """
        Update the payment status for a user

        Args:
            user_id: The user's Telegram ID
            event_id: The event ID
            status: The new payment status (confirmed, declined, pending)
            admin_comment: Optional comment from admin
            payment_amount: Amount paid by the user (in rubles)
            admin_id: Optional admin's Telegram ID for logging
            admin_username: Optional admin's Telegram username for logging
        """
        update_data = {
            "payment_status": status,
            "payment_verified_at": datetime.now().isoformat(),
        }

        if admin_comment:
            update_data["admin_comment"] = admin_comment

        # Get user registration for logging
        registration = None
        if payment_amount is not None or status:
            registration = await self.collection.find_one(
                {"user_id": user_id, "event_id": event_id}
            )

        # Prepare log data
        log_data = {
            "action": "update_payment_status",
            "user_id": user_id,
            "event_id": event_id,
            "new_status": status,
        }

        if registration:
            log_data["full_name"] = registration.get("full_name")
            log_data["previous_status"] = registration.get("payment_status")

        if admin_comment:
            log_data["admin_comment"] = admin_comment

        if admin_id:
            log_data["admin_id"] = admin_id

        if admin_username:
            log_data["admin_username"] = admin_username

        total_payment = None
        if payment_amount is not None:
            # Add payment amount to log data
            log_data["payment_amount"] = payment_amount

            if registration and "payment_amount" in registration:
                # Add the new payment to the existing amount
                total_payment = registration["payment_amount"] + payment_amount
                update_data["payment_amount"] = total_payment
                log_data["previous_amount"] = registration["payment_amount"]
                log_data["total_after"] = total_payment

                # Store payment history as an array of individual payments
                payment_history = registration.get("payment_history", [])
                payment_history.append(
                    {
                        "amount": payment_amount,
                        "timestamp": datetime.now().isoformat(),
                        "total_after": total_payment,
                    }
                )
                update_data["payment_history"] = payment_history
            else:
                # First payment
                total_payment = payment_amount
                update_data["payment_amount"] = payment_amount
                update_data["payment_history"] = [
                    {
                        "amount": payment_amount,
                        "timestamp": datetime.now().isoformat(),
                        "total_after": payment_amount,
                    }
                ]
                log_data["total_after"] = payment_amount

        # Log the payment status update
        await self.save_event_log(
            "payment_status_update", log_data, admin_id or user_id, admin_username
        )

        # Update the database
        await self.collection.update_one(
            {"user_id": user_id, "event_id": event_id}, {"$set": update_data}
        )

    async def normalize_graduate_types(
        self, admin_id: Optional[int] = None, admin_username: Optional[str] = None
    ):
        """One-time fix to normalize all graduate_type values to uppercase in the database."""
        result = await self.collection.update_many(
            {"graduate_type": {"$exists": True}},
            [{"$set": {"graduate_type": {"$toUpper": "$graduate_type"}}}],
        )

        # Log the normalization operation
        log_data = {
            "action": "normalize_graduate_types",
            "modified_count": result.modified_count,
        }

        if admin_id:
            log_data["admin_id"] = admin_id

        if admin_username:
            log_data["admin_username"] = admin_username

        await self.save_event_log("admin_action", log_data, admin_id, admin_username)

        return result.modified_count

    async def _get_users_base(
        self,
        payment_status: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        Base method to get users with various filters.

        Args:
            payment_status: Filter by payment status ("confirmed", "pending", "declined", None for any)
            event_id: Filter by event_id or None for all

        Returns:
            List of user registrations matching the criteria
        """
        query = {}

        # Build the query conditions
        and_conditions = []

        # Filter by payment status
        if payment_status == "unpaid":
            and_conditions.append({"payment_status": {"$ne": "confirmed"}})
        elif payment_status == "paid":
            and_conditions.append({"payment_status": "confirmed"})

        # Filter by event_id
        if event_id and event_id != "all":
            and_conditions.append({"event_id": event_id})

        # Add the conditions to the query if we have any
        if and_conditions:
            query["$and"] = and_conditions

        cursor = self.collection.find(query)
        return await cursor.to_list(length=None)

    async def get_unpaid_users(self, event_id: Optional[str] = None) -> List[Dict]:
        """Get all users who have not paid yet (payment_status is not "confirmed")"""
        return await self._get_users_base(payment_status="unpaid", event_id=event_id)

    async def get_users_without_feedback(
        self, event_id: Optional[str] = None
    ) -> List[Dict]:
        """Get all users who have not provided feedback yet."""
        all_users = await self._get_users_base(event_id=event_id)
        users_without_feedback = []

        for user in all_users:
            if not await self.has_provided_feedback(user["user_id"]):
                users_without_feedback.append(user)

        return users_without_feedback

    async def get_users_with_feedback(
        self, event_id: Optional[str] = None
    ) -> List[Dict]:
        """Get all users who have provided feedback."""
        all_users = await self._get_users_base(event_id=event_id)
        users_with_feedback = []

        for user in all_users:
            if await self.has_provided_feedback(user["user_id"]):
                users_with_feedback.append(user)

        return users_with_feedback

    async def get_paid_users(self, event_id: Optional[str] = None) -> List[Dict]:
        """Get all users who have paid (payment_status is "confirmed")."""
        return await self._get_users_base(payment_status="paid", event_id=event_id)

    async def get_all_users(self, event_id: Optional[str] = None) -> List[Dict]:
        """Get all users regardless of payment status."""
        return await self._get_users_base(event_id=event_id)

    async def _fix_database(self) -> Dict[str, int]:
        """
        Fix the database by setting payment_status to "confirmed" for:
        1. Registrations for free events (pricing_type == "free")
        2. Registrations where the user's graduate_type is in event.free_for_types

        Uses event properties instead of hardcoded city names or graduate types.

        Returns:
            Dictionary with counts of fixed records for each category
        """
        results = {
            "free_events_fixed": 0,
            "free_types_fixed": 0,
            "total_fixed": 0,
        }

        # Get all events to build lookup
        all_events = await self.get_all_events()

        # Fix registrations for free events
        free_event_ids = [
            str(ev["_id"]) for ev in all_events if ev.get("pricing_type") == "free"
        ]
        if free_event_ids:
            free_result = await self.collection.update_many(
                {
                    "event_id": {"$in": free_event_ids},
                    "payment_status": {"$ne": "confirmed"},
                },
                {"$set": {"payment_status": "confirmed"}},
            )
            results["free_events_fixed"] = free_result.modified_count

        # Fix registrations where graduate_type is in event's free_for_types
        for ev in all_events:
            free_types = ev.get("free_for_types", [])
            if not free_types:
                continue
            event_id = str(ev["_id"])
            type_result = await self.collection.update_many(
                {
                    "event_id": event_id,
                    "graduate_type": {"$in": free_types},
                    "payment_status": {"$ne": "confirmed"},
                },
                {"$set": {"payment_status": "confirmed"}},
            )
            results["free_types_fixed"] += type_result.modified_count

        results["total_fixed"] = (
            results["free_events_fixed"] + results["free_types_fixed"]
        )

        # Log the fix operation if any records were updated
        if results["total_fixed"] > 0:
            log_data = {
                "action": "fix_database",
                "modified_count": results["total_fixed"],
                "free_events_fixed": results["free_events_fixed"],
                "free_types_fixed": results["free_types_fixed"],
            }
            await self.save_event_log("admin_action", log_data)

        return results

    async def save_event_log(
        self,
        event_type: str,
        data: Dict,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
    ) -> None:
        """
        Save a record to the event logs collection

        Args:
            event_type: Type of the event (e.g., 'registration', 'payment', 'cancellation')
            data: Additional data about the event
            user_id: Optional user's Telegram ID
            username: Optional user's Telegram username
        """
        log_entry = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }

        if user_id is not None:
            log_entry["user_id"] = user_id
        if username is not None:
            log_entry["username"] = username

        await self.event_logs.insert_one(log_entry)

    async def save_feedback(
        self,
        feedback_data: dict | FeedbackData,
    ) -> str:
        """
        Save a user's feedback to the database

        Args:
            feedback_data: Dictionary or FeedbackData model containing feedback information

        Returns:
            ID of the inserted feedback document
        """
        # Convert dict to FeedbackData model if needed
        if isinstance(feedback_data, dict):
            feedback_data = FeedbackData(**feedback_data)

        # Get user info if full_name is not provided
        if not feedback_data.full_name:
            user_info = await self.collection.find_one(
                {"user_id": feedback_data.user_id}
            )
            if user_info and "full_name" in user_info:
                feedback_data.full_name = user_info.get("full_name")

        # Create the feedback document
        feedback = feedback_data.model_dump(exclude_none=True)
        feedback["timestamp"] = datetime.now().isoformat()

        # Create or get feedback collection
        if not hasattr(self, "_feedback_collection"):
            self._feedback_collection = get_database().get_collection("feedback")

        # Insert the feedback document
        result = await self._feedback_collection.insert_one(feedback)

        # Also log this event
        await self.save_event_log(
            "feedback_submitted",
            {
                "user_id": feedback_data.user_id,
                "username": feedback_data.username,
                "feedback_id": str(result.inserted_id),
                "attended": feedback_data.attended,
                "city": feedback_data.city,
                "feedback_format_preference": feedback_data.feedback_format_preference,
            },
            feedback_data.user_id,
            feedback_data.username,
        )

        return str(result.inserted_id)

    async def move_user_to_deleted(
        self, user_id: int, event_id: Optional[str] = None
    ) -> bool:
        """
        Move a user from registered_users to deleted_users collection

        Args:
            user_id: The user's Telegram ID
            event_id: Optional event_id to move specific registration. If None, moves all registrations.

        Returns:
            Boolean indicating whether any records were moved
        """
        # Build query
        query: dict[str, int | str] = {"user_id": user_id}
        if event_id:
            query["event_id"] = event_id

        # Find records to move
        cursor = self.collection.find(query)
        records = await cursor.to_list(length=None)

        if not records:
            logger.warning(
                f"No records found to move for user_id={user_id}, event_id={event_id}"
            )
            return False

        # Add deletion timestamp to each record
        now = datetime.now().isoformat()
        for record in records:
            record["deletion_timestamp"] = now

        # Insert into deleted_users collection
        if len(records) == 1:
            await self.deleted_users.insert_one(records[0])
            logger.info(
                f"Moved 1 record to deleted_users: user_id={user_id}, event_id={event_id}"
            )
        else:
            await self.deleted_users.insert_many(records)
            logger.info(
                f"Moved {len(records)} records to deleted_users: user_id={user_id}"
            )

        # Now that records are backed up, delete from main collection
        if event_id:
            result = await self.collection.delete_one(query)
        else:
            result = await self.collection.delete_many(query)

        return result.deleted_count > 0

    async def has_provided_feedback(
        self, user_id: int, event_id: Optional[str] = None
    ) -> bool:
        """
        Check if a user has already provided feedback.

        Args:
            user_id: The user's Telegram ID
            event_id: Optional event ID to check feedback for a specific event

        Returns:
            True if user has provided feedback, False otherwise
        """
        if not hasattr(self, "_feedback_collection"):
            self._feedback_collection = get_database().get_collection("feedback")

        query: Dict = {"user_id": user_id}
        if event_id:
            query["event_id"] = event_id
        feedback = await self._feedback_collection.find_one(query)
        return feedback is not None
