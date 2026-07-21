import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.app import App, RegisteredUser, GraduateType


class TestAppRegistration:
    """Tests for App registration-related methods"""

    def setup_method(self):
        """Set up test environment before each test"""
        # Create mock collections
        self.mock_collection = AsyncMock()
        self.mock_event_logs = AsyncMock()
        self.mock_deleted_users = AsyncMock()

        # Mock the get_database().get_collection() chain
        mock_db = MagicMock()
        mock_db.get_collection = MagicMock()
        mock_db.get_collection.side_effect = lambda name: {
            "registered_users": self.mock_collection,
            "event_logs": self.mock_event_logs,
            "deleted_users": self.mock_deleted_users,
        }.get(name, AsyncMock())

        # Create a patcher for get_database
        self.db_patcher = patch("src.app.get_database", return_value=mock_db)
        self.db_patcher.start()

        # Create src instance
        self.app = App(
            telegram_bot_token="mock_token",
            payment_phone_number="1234567890",
            payment_name="Test User",
        )

        # Patch save_event_log to avoid test side effects
        self.save_event_log_patcher = patch.object(
            self.app, "save_event_log", AsyncMock()
        )
        self.save_event_log_patcher.start()

        # Sample user data
        self.sample_user = RegisteredUser(
            full_name="Иванов Иван",
            graduation_year=2010,
            class_letter="А",
            target_city="Москва",
            event_id="aabbccddeeff00112233aabb",
            user_id=123456,
            username="ivan_ivanov",
            graduate_type=GraduateType.GRADUATE,
        )

    def teardown_method(self):
        """Clean up after each test"""
        self.db_patcher.stop()
        self.save_event_log_patcher.stop()

    @pytest.mark.asyncio
    async def test_save_registered_user_new(self):
        """Test saving a new registered user"""
        # Mock find_one to return None (no existing registration)
        self.mock_collection.find_one.return_value = None

        # Mock insert_one
        self.mock_collection.insert_one.return_value.inserted_id = "mock_id"

        # Call the method
        await self.app.save_registered_user(
            self.sample_user,
            user_id=self.sample_user.user_id,
            username=self.sample_user.username,
        )

        # Check that insert_one was called with the correct data
        assert self.mock_collection.insert_one.call_count >= 1

        # Get the first call to insert_one which should be our user data
        insert_data = self.mock_collection.insert_one.call_args_list[0][0][0]

        # Verify the data is correct
        assert insert_data["full_name"] == "Иванов Иван"
        assert insert_data["graduation_year"] == 2010
        assert insert_data["class_letter"] == "А"
        assert insert_data["target_city"] == "Москва"
        assert insert_data["user_id"] == 123456
        assert insert_data["username"] == "ivan_ivanov"
        assert insert_data["graduate_type"] == GraduateType.GRADUATE.value

    @pytest.mark.asyncio
    async def test_save_registered_user_update(self):
        """Test updating an existing registered user"""
        # Mock find_one to return an existing registration
        existing_user = {
            "_id": "mock_id",
            "full_name": "Иванов Иван",
            "graduation_year": 2010,
            "class_letter": "Б",  # Different class letter
            "target_city": "Москва",
            "user_id": 123456,
            "username": "ivan_ivanov",
            "graduate_type": GraduateType.GRADUATE.value,
        }
        self.mock_collection.find_one.return_value = existing_user

        # Create updated user with different class letter
        updated_user = RegisteredUser(
            full_name="Иванов Иван",
            graduation_year=2010,
            class_letter="А",  # Changed from Б to А
            target_city="Москва",
            event_id="aabbccddeeff00112233aabb",
            user_id=123456,
            username="ivan_ivanov",
            graduate_type=GraduateType.GRADUATE,
        )

        # Call the method
        await self.app.save_registered_user(
            updated_user, user_id=updated_user.user_id, username=updated_user.username
        )

        # Check that update_one was called
        self.mock_collection.update_one.assert_called_once()

        # Check the update parameters
        call_args = self.mock_collection.update_one.call_args[0]

        # Verify the filter criteria (should find by _id)
        assert call_args[0] == {"_id": "mock_id"}

        # Verify the update data
        update_data = call_args[1]["$set"]
        assert update_data["class_letter"] == "А"  # Updated class letter

    @pytest.mark.asyncio
    async def test_get_user_registrations(self):
        """Test getting all registrations for a user"""
        # Sample registrations
        sample_registrations = [
            {
                "full_name": "Иванов Иван",
                "graduation_year": 2010,
                "class_letter": "А",
                "target_city": "Москва",
                "user_id": 123456,
                "username": "ivan_ivanov",
                "graduate_type": GraduateType.GRADUATE.value,
            },
            {
                "full_name": "Иванов Иван",
                "graduation_year": 2010,
                "class_letter": "А",
                "target_city": "Пермь",
                "user_id": 123456,
                "username": "ivan_ivanov",
                "graduate_type": GraduateType.GRADUATE.value,
            },
        ]

        # Mock find to return a cursor that will return our sample registrations
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=sample_registrations)
        self.mock_collection.find = MagicMock(return_value=mock_cursor)

        # Call the method
        registrations = await self.app.get_user_registrations(123456)

        # Verify the find query
        self.mock_collection.find.assert_called_once_with({"user_id": 123456})

        # Verify the results
        assert len(registrations) == 2
        assert registrations[0]["target_city"] == "Москва"
        assert registrations[1]["target_city"] == "Пермь"

    @pytest.mark.asyncio
    async def test_get_user_registration_exists(self):
        """Test getting a single registration when user has registrations"""
        # Mock get_user_registrations to return sample registrations
        sample_registrations = [
            {
                "full_name": "Иванов Иван",
                "graduation_year": 2010,
                "class_letter": "А",
                "target_city": "Москва",
                "user_id": 123456,
                "username": "ivan_ivanov",
                "graduate_type": GraduateType.GRADUATE.value,
            }
        ]

        # Mock find to return a cursor sorted newest-first
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=sample_registrations)
        self.mock_collection.find = MagicMock(return_value=mock_cursor)

        # Call the method
        registration = await self.app.get_user_registration(123456)

        # Verify the query and newest-first sort
        self.mock_collection.find.assert_called_once_with({"user_id": 123456})
        mock_cursor.sort.assert_called_once_with("_id", -1)

        # Verify the result
        assert registration is not None
        assert registration["target_city"] == "Москва"

    @pytest.mark.asyncio
    async def test_get_user_registration_not_exists(self):
        """Test getting a single registration when user has no registrations"""
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[])
        self.mock_collection.find = MagicMock(return_value=mock_cursor)

        # Call the method
        registration = await self.app.get_user_registration(123456)

        # Verify the result
        assert registration is None

    @pytest.mark.asyncio
    async def test_delete_user_registration_specific_event(self):
        """Test deleting a user's registration for a specific event"""
        # Mock move_user_to_deleted to return True
        with patch.object(
            self.app, "move_user_to_deleted", AsyncMock(return_value=True)
        ) as mock_move:
            # Call the method
            result = await self.app.delete_user_registration(
                123456, event_id="aabbccddeeff00112233aabb"
            )

            # Verify the move_user_to_deleted was called with correct parameters
            mock_move.assert_called_once_with(123456, "aabbccddeeff00112233aabb")

            # Verify the result
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_user_registration_all_cities(self):
        """Test deleting all of a user's registrations"""
        # Mock move_user_to_deleted to return True
        with patch.object(
            self.app, "move_user_to_deleted", AsyncMock(return_value=True)
        ) as mock_move:
            # Call the method
            result = await self.app.delete_user_registration(123456)

            # Verify the move_user_to_deleted was called with correct parameters
            mock_move.assert_called_once_with(123456, None)

            # Verify the result
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_user_registration_none_found(self):
        """Test attempting to delete registrations that don't exist"""
        # Mock move_user_to_deleted to return False (no records found)
        with patch.object(
            self.app, "move_user_to_deleted", AsyncMock(return_value=False)
        ) as mock_move:
            # Call the method
            result = await self.app.delete_user_registration(123456)

            # Verify the move_user_to_deleted was called with correct parameters
            mock_move.assert_called_once_with(123456, None)

            # Verify the result
            assert result is False

    @pytest.mark.asyncio
    async def test_move_user_to_deleted_specific_event(self):
        """Test moving a user to deleted_users collection for a specific event"""
        # Mock the retrieve user record
        user_record = {
            "_id": "mock_id",
            "user_id": 123456,
            "target_city": "Москва",
            "event_id": "aabbccddeeff00112233aabb",
            "full_name": "Test User",
        }

        # Mock find to return a cursor that will return our user
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[user_record])
        self.mock_collection.find = MagicMock(return_value=mock_cursor)

        # Mock delete_one to indicate success
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        self.mock_collection.delete_one.return_value = mock_result

        # Mock insert_one for deleted users collection
        self.mock_deleted_users.insert_one = AsyncMock()

        # Call the method
        result = await self.app.move_user_to_deleted(
            123456, event_id="aabbccddeeff00112233aabb"
        )

        # Verify that the record was retrieved with correct query
        self.mock_collection.find.assert_called_once_with(
            {"user_id": 123456, "event_id": "aabbccddeeff00112233aabb"}
        )

        # Verify that the record was inserted into deleted_users
        self.mock_deleted_users.insert_one.assert_called_once()

        # Verify deletion was called with the right parameters
        self.mock_collection.delete_one.assert_called_once_with(
            {"user_id": 123456, "event_id": "aabbccddeeff00112233aabb"}
        )

        # Verify the result
        assert result is True

    @pytest.mark.asyncio
    async def test_move_user_to_deleted_all_cities(self):
        """Test moving all of a user's registrations to deleted_users"""
        # Mock two user records
        user_records = [
            {
                "_id": "mock_id1",
                "user_id": 123456,
                "target_city": "Москва",
                "full_name": "Test User",
            },
            {
                "_id": "mock_id2",
                "user_id": 123456,
                "target_city": "Пермь",
                "full_name": "Test User",
            },
        ]

        # Mock find to return a cursor that will return our users
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=user_records)
        self.mock_collection.find = MagicMock(return_value=mock_cursor)

        # Mock delete_many to indicate success
        mock_result = MagicMock()
        mock_result.deleted_count = 2
        self.mock_collection.delete_many.return_value = mock_result

        # Mock insert_many for deleted users collection
        self.mock_deleted_users.insert_many = AsyncMock()

        # Call the method
        result = await self.app.move_user_to_deleted(123456)

        # Verify that the records were retrieved with correct query
        self.mock_collection.find.assert_called_once_with({"user_id": 123456})

        # Verify that the records were inserted into deleted_users
        self.mock_deleted_users.insert_many.assert_called_once()

        # Verify deletion was called with the right parameters
        self.mock_collection.delete_many.assert_called_once_with({"user_id": 123456})

        # Verify the result
        assert result is True

    @pytest.mark.asyncio
    async def test_move_user_to_deleted_none_found(self):
        """Test attempting to move user records that don't exist"""
        # Mock find to return empty list
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        self.mock_collection.find = MagicMock(return_value=mock_cursor)

        # Call the method
        result = await self.app.move_user_to_deleted(123456)

        # Verify that the database was queried
        self.mock_collection.find.assert_called_once_with({"user_id": 123456})

        # Verify that no insertion or deletion was performed
        self.mock_deleted_users.insert_one.assert_not_called()
        self.mock_deleted_users.insert_many.assert_not_called()
        self.mock_collection.delete_one.assert_not_called()
        self.mock_collection.delete_many.assert_not_called()

        # Verify the result
        assert result is False
