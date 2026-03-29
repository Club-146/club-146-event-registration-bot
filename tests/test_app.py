import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from unittest.mock import patch as mock_patch
from datetime import datetime

from src.app import App, GraduateType


class TestApp:
    """Tests for the App class"""

    def setup_method(self):
        """Set up test environment before each test"""
        # Create a mock collection
        self.mock_collection = AsyncMock()

        # Mock the get_database().get_collection() chain
        mock_db = MagicMock()
        mock_db.get_collection.return_value = self.mock_collection

        # Create a patcher for get_database
        self.db_patcher = patch("src.app.get_database", return_value=mock_db)
        self.db_patcher.start()

        # Create src instance
        self.app = App(
            telegram_bot_token="mock_token",
            spreadsheet_id="mock_spreadsheet_id",
            payment_phone_number="1234567890",
            payment_name="Test User",
        )

    def teardown_method(self):
        """Clean up after each test"""
        # Stop all patchers
        self.db_patcher.stop()

    def test_app_initialization(self):
        """Test the App constructor and settings"""
        # Test that settings were initialized correctly
        assert self.app.settings.telegram_bot_token.get_secret_value() == "mock_token"
        assert self.app.settings.spreadsheet_id == "mock_spreadsheet_id"
        assert self.app.settings.payment_phone_number == "1234567890"
        assert self.app.settings.payment_name == "Test User"

        # Test that exporter was initialized
        assert self.app.sheet_exporter is not None

        # Test that collection is None by default
        assert self.app._collection is None

    def test_collection_property(self):
        """Test the collection property lazy loading"""
        # Before accessing, _collection should be None
        assert self.app._collection is None

        # Accessing the property should initialize it
        collection = self.app.collection
        assert collection == self.mock_collection

        # The property should now be cached
        assert self.app._collection == self.mock_collection

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    @patch("botspot.core.dependency_manager.get_dependency_manager")
    async def test_log_to_chat_logs(self, mock_get_dependency_manager, mock_send_safe):
        """Test logging to the logs chat"""
        # Set logs chat ID
        self.app.settings.logs_chat_id = 123456

        # Mock dependency manager
        mock_deps = MagicMock()
        mock_get_dependency_manager.return_value = mock_deps

        # Mock send_safe
        mock_send_safe.return_value = "mock_message"

        # Call the method
        result = await self.app.log_to_chat("Test log message", "logs")

        # Verify the result
        mock_send_safe.assert_called_once_with(123456, "Test log message")
        assert result == "mock_message"

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    @patch("botspot.core.dependency_manager.get_dependency_manager")
    async def test_log_to_chat_events(
        self, mock_get_dependency_manager, mock_send_safe
    ):
        """Test logging to the events chat"""
        # Set events chat ID
        self.app.settings.events_chat_id = 654321

        # Mock dependency manager
        mock_deps = MagicMock()
        mock_get_dependency_manager.return_value = mock_deps

        # Mock send_safe
        mock_send_safe.return_value = "mock_message"

        # Call the method
        result = await self.app.log_to_chat("Test event message", "events")

        # Verify the result
        mock_send_safe.assert_called_once_with(654321, "Test event message")
        assert result == "mock_message"

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    @patch("botspot.core.dependency_manager.get_dependency_manager")
    async def test_log_to_chat_invalid_type(
        self, mock_get_dependency_manager, mock_send_safe
    ):
        """Test logging with an invalid chat type"""
        # Explicitly set the logs and events chat IDs to None
        self.app.settings.logs_chat_id = None
        self.app.settings.events_chat_id = None

        # Mock dependency manager
        mock_deps = MagicMock()
        mock_get_dependency_manager.return_value = mock_deps

        # Call the method with an invalid chat type
        result = await self.app.log_to_chat("Test log message", "invalid_type")

        # Verify the result
        mock_send_safe.assert_not_called()
        assert result is None

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    @patch("botspot.core.dependency_manager.get_dependency_manager")
    async def test_log_registration_step(
        self, mock_get_dependency_manager, mock_send_safe
    ):
        """Test logging a registration step"""
        # Set logs chat ID
        self.app.settings.logs_chat_id = 123456

        # Mock dependency manager
        mock_deps = MagicMock()
        mock_get_dependency_manager.return_value = mock_deps

        # Mock send_safe
        mock_send_safe.return_value = "mock_message"

        # Call the method
        result = await self.app.log_registration_step(
            user_id=98765, username="test_user", step="Full Name", data="Иванов Иван"
        )

        # Verify the result
        mock_send_safe.assert_called_once()
        call_args = mock_send_safe.call_args[0]
        assert call_args[0] == 123456
        # Check message contains all the information
        message = call_args[1]
        assert "test_user" in message
        assert "Full Name" in message
        assert "Иванов Иван" in message
        assert result == "mock_message"

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    @patch("botspot.core.dependency_manager.get_dependency_manager")
    async def test_log_registration_completed(
        self, mock_get_dependency_manager, mock_send_safe
    ):
        """Test logging a completed registration"""
        # Set events chat ID
        self.app.settings.events_chat_id = 654321

        # Mock dependency manager
        mock_deps = MagicMock()
        mock_get_dependency_manager.return_value = mock_deps

        # Mock send_safe
        mock_send_safe.return_value = "mock_message"

        # Call the method
        await self.app.log_registration_completed(
            user_id=98765,
            username="test_user",
            full_name="Иванов Иван",
            graduation_year=2010,
            class_letter="А",
            city="Москва",
            graduate_type=GraduateType.GRADUATE.value,
        )

        # Verify the call
        mock_send_safe.assert_called_once()
        call_args = mock_send_safe.call_args[0]
        assert call_args[0] == 654321
        # Check message contains all the information
        message = call_args[1]
        assert "НОВАЯ РЕГИСТРАЦИЯ" in message
        assert "test_user" in message
        assert "Иванов Иван" in message
        assert "2010 А" in message
        assert "Москва" in message

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    @patch("botspot.core.dependency_manager.get_dependency_manager")
    async def test_log_registration_canceled(
        self, mock_get_dependency_manager, mock_send_safe
    ):
        """Test logging a canceled registration"""
        # Set events chat ID
        self.app.settings.events_chat_id = 654321

        # Mock dependency manager
        mock_deps = MagicMock()
        mock_get_dependency_manager.return_value = mock_deps

        # Mock send_safe
        mock_send_safe.return_value = "mock_message"

        # Call the method
        await self.app.log_registration_canceled(
            user_id=98765,
            username="test_user",
            full_name="Иванов Иван",
            city="Москва",
        )

        # Verify the call
        mock_send_safe.assert_called_once()
        call_args = mock_send_safe.call_args[0]
        assert call_args[0] == 654321
        # Check message contains all the information
        message = call_args[1]
        assert "ОТМЕНА РЕГИСТРАЦИИ" in message
        assert "test_user" in message
        assert "Иванов Иван" in message
        assert "Москва" in message


class TestValidateGraduationYear:
    """Tests for validate_graduation_year covering edge-case branches."""

    def setup_method(self):
        mock_db = MagicMock()
        mock_db.get_collection.return_value = AsyncMock()
        self.db_patcher = patch("src.app.get_database", return_value=mock_db)
        self.db_patcher.start()
        self.app = App(
            telegram_bot_token="tok",
            spreadsheet_id="sid",
            payment_phone_number="1",
            payment_name="N",
        )

    def teardown_method(self):
        self.db_patcher.stop()

    def test_current_year_june_or_later(self):
        current_year = datetime.now().year
        with mock_patch("src.app.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(current_year, 6, 15)
            valid, msg = self.app.validate_graduation_year(current_year)
        assert valid is True

    def test_current_year_before_june(self):
        current_year = datetime.now().year
        with mock_patch("src.app.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(current_year, 3, 1)
            valid, msg = self.app.validate_graduation_year(current_year)
        assert valid is False
        assert "после выпуска" in msg

    def test_future_year_too_far(self):
        current_year = datetime.now().year
        valid, msg = self.app.validate_graduation_year(current_year + 10)
        assert valid is False
        assert "позже" in msg


class TestParseGraduationYearCoverage:
    """Covers uncovered branches in parse_graduation_year_and_class_letter."""

    def setup_method(self):
        mock_db = MagicMock()
        mock_db.get_collection.return_value = AsyncMock()
        self.db_patcher = patch("src.app.get_database", return_value=mock_db)
        self.db_patcher.start()
        self.app = App(
            telegram_bot_token="tok",
            spreadsheet_id="sid",
            payment_phone_number="1",
            payment_name="N",
        )

    def teardown_method(self):
        self.db_patcher.stop()

    def test_digit_only_invalid_year(self):
        # year only, but year is invalid (too early) -> returns None, None, error
        year, letter, err = self.app.parse_graduation_year_and_class_letter("1980")
        assert year is None
        assert letter is None
        assert err is not None

    def test_case3_fallback_split(self):
        # Input like "03 Б" - len < 4, so goes to case 3 fallback (maxsplit=1)
        year, letter, err = self.app.parse_graduation_year_and_class_letter("2003 Б")
        assert year == 2003
        assert letter == "Б"
        assert err is None


class TestExportPassThrough:
    """Tests for export pass-through methods (lines 654, 658, 662, 666, 670)."""

    def setup_method(self):
        mock_db = MagicMock()
        mock_db.get_collection.return_value = AsyncMock()
        self.db_patcher = patch("src.app.get_database", return_value=mock_db)
        self.db_patcher.start()
        self.app = App(
            telegram_bot_token="tok",
            spreadsheet_id="sid",
            payment_phone_number="1",
            payment_name="N",
        )

    def teardown_method(self):
        self.db_patcher.stop()

    @pytest.mark.asyncio
    async def test_export_registered_users_to_google_sheets_force(self):
        self.app.sheet_exporter = MagicMock()
        self.app.sheet_exporter.export_registered_users = AsyncMock(return_value="ok")
        result = await self.app.export_registered_users_to_google_sheets(
            event_id="abc", force=True
        )
        self.app.sheet_exporter.export_registered_users.assert_called_once_with(
            event_id="abc"
        )
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_export_registered_users_to_google_sheets_debounced(self):
        self.app.sheet_exporter = MagicMock()
        self.app.sheet_exporter.export_registered_users = AsyncMock(return_value="ok")
        self.app._export_debounce_seconds = 0.1
        await self.app.export_registered_users_to_google_sheets(event_id="abc")
        # Should not have been called yet (debounce pending)
        self.app.sheet_exporter.export_registered_users.assert_not_called()
        # Wait for debounce to fire
        import asyncio

        await asyncio.sleep(0.2)
        self.app.sheet_exporter.export_registered_users.assert_called_once_with(
            event_id="abc"
        )

    @pytest.mark.asyncio
    async def test_export_to_csv(self):
        self.app.sheet_exporter = MagicMock()
        self.app.sheet_exporter.export_to_csv = AsyncMock(return_value="csv_data")
        result = await self.app.export_to_csv(event_id="abc")
        self.app.sheet_exporter.export_to_csv.assert_called_once_with(event_id="abc")
        assert result == "csv_data"

    @pytest.mark.asyncio
    async def test_export_deleted_users_to_csv(self):
        self.app.sheet_exporter = MagicMock()
        self.app.sheet_exporter.export_deleted_users_to_csv = AsyncMock(
            return_value="del_csv"
        )
        result = await self.app.export_deleted_users_to_csv(event_id="abc")
        self.app.sheet_exporter.export_deleted_users_to_csv.assert_called_once_with(
            event_id="abc"
        )
        assert result == "del_csv"

    @pytest.mark.asyncio
    async def test_export_feedback_to_sheets(self):
        self.app.sheet_exporter = MagicMock()
        self.app.sheet_exporter.export_feedback_to_sheets = AsyncMock(
            return_value="sheets_data"
        )
        result = await self.app.export_feedback_to_sheets(event_id="xyz")
        self.app.sheet_exporter.export_feedback_to_sheets.assert_called_once_with(
            event_id="xyz"
        )
        assert result == "sheets_data"

    @pytest.mark.asyncio
    async def test_export_feedback_to_csv(self):
        self.app.sheet_exporter = MagicMock()
        self.app.sheet_exporter.export_feedback_to_csv = AsyncMock(
            return_value="fb_csv"
        )
        result = await self.app.export_feedback_to_csv(event_id="xyz")
        self.app.sheet_exporter.export_feedback_to_csv.assert_called_once_with(
            event_id="xyz"
        )
        assert result == "fb_csv"
