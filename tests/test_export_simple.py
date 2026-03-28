import pytest
from unittest.mock import MagicMock, AsyncMock

from src.export import SheetExporter


class TestExportFunctions:
    @pytest.fixture
    def mock_app(self):
        mock_app = MagicMock()
        mock_cursor = AsyncMock()
        mock_cursor.to_list.return_value = [
            {
                "full_name": "Test User 1",
                "graduation_year": 2010,
                "class_letter": "A",
                "target_city": "Москва",
                "event_id": "evt1",
                "user_id": 12345,
                "username": "user1",
                "graduate_type": "GRADUATE",
                "payment_status": "confirmed",
                "payment_amount": 2000,
            },
            {
                "full_name": "Test User 2",
                "graduation_year": 2005,
                "class_letter": "Б",
                "target_city": "Пермь",
                "event_id": "evt2",
                "user_id": 67890,
                "username": "user2",
                "graduate_type": "TEACHER",
            },
        ]
        mock_app.collection.find.return_value = mock_cursor
        mock_app.get_all_events = AsyncMock(return_value=[])
        return mock_app

    @pytest.mark.asyncio
    async def test_export_to_csv(self, mock_app):
        """Test export_to_csv function"""
        # Create exporter instance with mock src
        exporter = SheetExporter("test_sheet_id", app=mock_app)

        # Call export_to_csv
        csv_content, message = await exporter.export_to_csv()

        # Verify collection.find was called
        mock_app.collection.find.assert_called_once_with({})

        # Check that CSV content contains expected data
        assert csv_content is not None
        assert "Test User 1" in csv_content
        assert "Test User 2" in csv_content
        assert "Москва" in csv_content
        assert "Пермь" in csv_content

        # Check success message
        assert "экспортировано" in message.lower()
        assert "2 пользователей" in message
