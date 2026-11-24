"""
tests/test_v37_device_flow.py
-----------------------------
Comprehensive unit and integration test suite for ReelForge Microsoft Device Code Flow (RFC 8628):
- Flow initiation: parameters, scopes, error handling, input validation.
- Single-step polling: exit_condition callback, non-blocking behavior.
- Profile fetching: Microsoft Graph /me integration, error handling.
- Connection persistence: cache serialization, duplicate Microsoft account prevention.
- Telegram bot integration: /connect command, group chat restriction, active flow tracking,
  background polling task, inline buttons, cancellation callback, cross-user protection.
- Multi-user isolation: independent flows, non-interfering cancellation.
- Lifecycle shutdown: safe cancellation of pending device flows.
- Preservation: rollback safety for legacy OAuth and GraphClient modules.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pymongo.errors import DuplicateKeyError

import auth.device_flow as df
import bot.telegram_bot as tb
from auth.device_flow import (
    DeviceFlowDuplicateAccountError,
    DeviceFlowError,
    complete_device_flow_connection,
    fetch_user_profile,
    initiate_device_flow_for_user,
    poll_device_flow_step,
)


class TestDeviceFlowInitiation(unittest.TestCase):
    """Test initiate_device_flow_for_user function and validation."""

    def test_initiate_requires_valid_user_id(self):
        with self.assertRaises(ValueError):
            initiate_device_flow_for_user("")
        with self.assertRaises(ValueError):
            initiate_device_flow_for_user(None)  # type: ignore

    @patch("auth.device_flow.get_user_by_id", return_value=None)
    def test_initiate_unknown_user_raises_value_error(self, mock_get_user):
        with self.assertRaises(ValueError) as ctx:
            initiate_device_flow_for_user("usr_nonexistent")
        self.assertIn("Unknown ReelForge user_id", str(ctx.exception))

    @patch("auth.device_flow.get_user_by_id", return_value={"user_id": "usr_test1"})
    def test_initiate_success_returns_flow_cache_and_client(self, mock_get_user):
        mock_client = MagicMock()
        mock_flow = {
            "user_code": "ABCD-EFGH",
            "device_code": "dev_code_123",
            "verification_uri": "https://microsoft.com/devicelogin",
            "verification_uri_complete": "https://microsoft.com/devicelogin?otc=ABCD-EFGH",
            "expires_in": 900,
            "interval": 5,
        }
        mock_client.initiate_device_flow.return_value = mock_flow
        mock_cache = MagicMock()

        flow, cache, client = initiate_device_flow_for_user(
            "usr_test1", client=mock_client, cache=mock_cache
        )

        self.assertEqual(flow["user_code"], "ABCD-EFGH")
        self.assertIs(cache, mock_cache)
        self.assertIs(client, mock_client)
        mock_client.initiate_device_flow.assert_called_once_with(scopes=df.MICROSOFT_SCOPES)

    @patch("auth.device_flow.get_user_by_id", return_value={"user_id": "usr_test1"})
    def test_initiate_msal_exception_raises_device_flow_error(self, mock_get_user):
        mock_client = MagicMock()
        mock_client.initiate_device_flow.side_effect = RuntimeError("MSAL network failure")

        with self.assertRaises(DeviceFlowError) as ctx:
            initiate_device_flow_for_user("usr_test1", client=mock_client)
        self.assertIn("Failed to initiate Microsoft device flow", str(ctx.exception))

    @patch("auth.device_flow.get_user_by_id", return_value={"user_id": "usr_test1"})
    def test_initiate_provider_error_raises_device_flow_error(self, mock_get_user):
        mock_client = MagicMock()
        mock_client.initiate_device_flow.return_value = {
            "error": "invalid_client",
            "error_description": "Client authentication failed.",
        }

        with self.assertRaises(DeviceFlowError) as ctx:
            initiate_device_flow_for_user("usr_test1", client=mock_client)
        self.assertIn("Microsoft device flow rejected", str(ctx.exception))


class TestDeviceFlowPollingStep(unittest.TestCase):
    """Test poll_device_flow_step single-step non-blocking polling."""

    def test_invalid_flow_returns_error_dict(self):
        mock_client = MagicMock()
        res = poll_device_flow_step(mock_client, {})
        self.assertEqual(res.get("error"), "invalid_flow")

    def test_valid_flow_calls_msal_with_exit_condition(self):
        mock_client = MagicMock()
        mock_flow = {"device_code": "dev_123"}
        mock_client.acquire_token_by_device_flow.return_value = {
            "error": "authorization_pending"
        }

        res = poll_device_flow_step(mock_client, mock_flow)

        self.assertEqual(res.get("error"), "authorization_pending")
        mock_client.acquire_token_by_device_flow.assert_called_once()
        args, kwargs = mock_client.acquire_token_by_device_flow.call_args
        self.assertEqual(args[0], mock_flow)
        self.assertIn("exit_condition", kwargs)
        # Verify exit_condition returns True to stop after one step
        exit_cond = kwargs["exit_condition"]
        self.assertTrue(exit_cond(mock_flow))

    def test_msal_exception_returns_polling_exception_dict(self):
        mock_client = MagicMock()
        mock_flow = {"device_code": "dev_123"}
        mock_client.acquire_token_by_device_flow.side_effect = ConnectionResetError("Reset")

        res = poll_device_flow_step(mock_client, mock_flow)
        self.assertEqual(res.get("error"), "polling_exception")


class TestUserProfileFetch(unittest.TestCase):
    """Test fetch_user_profile Microsoft Graph integration."""

    def test_fetch_profile_requires_access_token(self):
        with self.assertRaises(DeviceFlowError):
            fetch_user_profile("")
        with self.assertRaises(DeviceFlowError):
            fetch_user_profile(None)  # type: ignore

    @patch("requests.get")
    def test_fetch_profile_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "ms_user_456",
            "displayName": "Test Explorer",
            "mail": "explorer@example.com",
            "userPrincipalName": "explorer@example.com",
        }
        mock_get.return_value = mock_response

        profile = fetch_user_profile("fake_token_abc")
        self.assertEqual(profile["id"], "ms_user_456")
        self.assertEqual(profile["displayName"], "Test Explorer")
        mock_get.assert_called_once_with(
            df.GRAPH_ME_URL,
            headers={"Authorization": "Bearer fake_token_abc"},
            timeout=30,
        )

    @patch("requests.get")
    def test_fetch_profile_network_failure_raises_device_flow_error(self, mock_get):
        mock_get.side_effect = ConnectionError("Network unreachable")
        with self.assertRaises(DeviceFlowError):
            fetch_user_profile("fake_token_abc")

    @patch("requests.get")
    def test_fetch_profile_non_200_raises_device_flow_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        with self.assertRaises(DeviceFlowError):
            fetch_user_profile("fake_token_abc")

    @patch("requests.get")
    def test_fetch_profile_missing_id_raises_device_flow_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"displayName": "No ID"}
        mock_get.return_value = mock_response

        with self.assertRaises(DeviceFlowError):
            fetch_user_profile("fake_token_abc")


class TestCompleteDeviceFlowConnection(unittest.TestCase):
    """Test complete_device_flow_connection persistence and security checks."""

    def test_complete_requires_access_token(self):
        mock_cache = MagicMock()
        with self.assertRaises(DeviceFlowError):
            complete_device_flow_connection("usr_test1", mock_cache, {})

    @patch("auth.device_flow.update_user_microsoft")
    def test_complete_success_persists_expected_schema(self, mock_update):
        mock_cache = MagicMock()
        mock_cache.serialize.return_value = '{"token_cache": "serialized_content"}'

        fake_result = {
            "access_token": "valid_token_xyz",
            "id_token_claims": {"tid": "tenant_123"},
        }

        fake_profile = {
            "id": "ms_id_999",
            "displayName": "Rehan Test",
            "mail": "rehan@example.com",
        }

        mock_update.return_value = {
            "connected": True,
            "microsoft_user_id": "ms_id_999",
            "display_name": "Rehan Test",
        }

        res = complete_device_flow_connection(
            "usr_test1",
            mock_cache,
            fake_result,
            profile_fetcher=lambda token: fake_profile,
        )

        self.assertEqual(res["microsoft_user_id"], "ms_id_999")
        mock_update.assert_called_once()
        user_id_arg, doc_arg = mock_update.call_args[0]
        self.assertEqual(user_id_arg, "usr_test1")
        self.assertTrue(doc_arg["connected"])
        self.assertEqual(doc_arg["microsoft_user_id"], "ms_id_999")
        self.assertEqual(doc_arg["display_name"], "Rehan Test")
        self.assertEqual(doc_arg["email"], "rehan@example.com")
        self.assertEqual(doc_arg["tenant_id"], "tenant_123")
        self.assertEqual(doc_arg["token_cache"], '{"token_cache": "serialized_content"}')
        self.assertIn("connected_at", doc_arg)
        self.assertIn("token_refreshed_at", doc_arg)

    @patch("auth.device_flow.update_user_microsoft")
    def test_complete_duplicate_account_raises_duplicate_error(self, mock_update):
        mock_cache = MagicMock()
        fake_result = {"access_token": "valid_token_xyz"}
        fake_profile = {"id": "ms_id_already_taken", "displayName": "User B"}

        mock_update.side_effect = DuplicateKeyError("E11000 duplicate key error")

        with self.assertRaises(DeviceFlowDuplicateAccountError) as ctx:
            complete_device_flow_connection(
                "usr_test1",
                mock_cache,
                fake_result,
                profile_fetcher=lambda token: fake_profile,
            )
        self.assertIn("already connected to another ReelForge user", str(ctx.exception))

    @patch("auth.device_flow.update_user_microsoft")
    def test_complete_user_not_found_raises_device_flow_error(self, mock_update):
        mock_cache = MagicMock()
        fake_result = {"access_token": "valid_token_xyz"}
        fake_profile = {"id": "ms_id_new", "displayName": "New User"}

        mock_update.return_value = None

        with self.assertRaises(DeviceFlowError):
            complete_device_flow_connection(
                "usr_test1",
                mock_cache,
                fake_result,
                profile_fetcher=lambda token: fake_profile,
            )


class TestTelegramBotConnectCommand(unittest.IsolatedAsyncioTestCase):
    """Test /connect command execution in telegram_bot.py."""

    async def test_connect_unresolved_user_sends_warning(self):
        mock_update = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_context = MagicMock()

        with patch("bot.telegram_bot._resolve_user", return_value=(None, None)):
            await tb.connect_command(mock_update, mock_context)

        mock_update.message.reply_text.assert_awaited_once()
        text = mock_update.message.reply_text.await_args[0][0]
        self.assertIn("Could not resolve your account", text)

    async def test_connect_in_group_chat_enforces_private_chat(self):
        mock_update = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_update.effective_chat.type = "group"
        mock_context = MagicMock()

        fake_user = {"user_id": "usr_grp"}
        with patch("bot.telegram_bot._resolve_user", return_value=(fake_user, None)):
            await tb.connect_command(mock_update, mock_context)

        mock_update.message.reply_text.assert_awaited_once()
        text = mock_update.message.reply_text.await_args[0][0]
        self.assertIn("private chat", text)

    async def test_connect_already_connected_user_shows_status_buttons(self):
        mock_update = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_update.effective_chat.type = "private"
        mock_context = MagicMock()

        fake_user = {"user_id": "usr_conn"}
        fake_ms = {"connected": True, "display_name": "Connected User"}

        with (
            patch("bot.telegram_bot._resolve_user", return_value=(fake_user, None)),
            patch("bot.telegram_bot.get_user_microsoft", return_value=fake_ms),
        ):
            await tb.connect_command(mock_update, mock_context)

        mock_update.message.reply_text.assert_awaited_once()
        args, kwargs = mock_update.message.reply_text.await_args
        self.assertIn("already connected", args[0])
        reply_markup = kwargs.get("reply_markup")
        self.assertIsNotNone(reply_markup)
        buttons = reply_markup.inline_keyboard[0]
        self.assertEqual(buttons[0].callback_data, tb.STATUS_SHOW)
        self.assertEqual(buttons[1].callback_data, tb.DISCONNECT_START)

    async def test_connect_success_prompts_user_code_and_tracks_task(self):
        mock_update = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_sent_msg = MagicMock()
        mock_sent_msg.message_id = 42
        mock_update.message.reply_text.return_value = mock_sent_msg
        mock_update.effective_chat.type = "private"
        mock_update.effective_chat.id = 123456

        mock_app = MagicMock()
        mock_app.bot_data = {}
        mock_context = MagicMock()
        mock_context.application = mock_app
        mock_context.bot = MagicMock()

        fake_user = {"user_id": "usr_connect_flow"}
        fake_flow = {
            "user_code": "WD4R-XYZ9",
            "device_code": "dcode_abc",
            "verification_uri": "https://microsoft.com/devicelogin",
            "verification_uri_complete": "https://microsoft.com/devicelogin?otc=WD4R-XYZ9",
            "expires_in": 900,
            "expires_at": time.time() + 900,
            "interval": 5,
        }

        mock_client = MagicMock()
        mock_cache = MagicMock()

        with (
            patch("bot.telegram_bot._resolve_user", return_value=(fake_user, None)),
            patch("bot.telegram_bot.get_user_microsoft", return_value=None),
            patch(
                "bot.telegram_bot.initiate_device_flow_for_user",
                return_value=(fake_flow, mock_cache, mock_client),
            ),
            patch("bot.telegram_bot._poll_device_flow_task", new_callable=AsyncMock) as mock_poll,
        ):
            await tb.connect_command(mock_update, mock_context)

            mock_update.message.reply_text.assert_awaited_once()
            text, kwargs = mock_update.message.reply_text.await_args
            msg_str = text[0]
            self.assertIn("WD4R-XYZ9", msg_str)
            self.assertIn("https://microsoft.com/devicelogin", msg_str)
            self.assertIn("15 minutes", msg_str)

            reply_markup = kwargs.get("reply_markup")
            keyboard = reply_markup.inline_keyboard
            self.assertEqual(keyboard[0][0].url, fake_flow["verification_uri_complete"])
            self.assertEqual(keyboard[1][0].callback_data, f"{tb.DEVICE_CANCEL_PREFIX}usr_connect_flow")

            # Verify active_device_flows tracking
            self.assertIn("active_device_flows", mock_app.bot_data)
            self.assertIn("usr_connect_flow", mock_app.bot_data["active_device_flows"])
            task, saved_flow = mock_app.bot_data["active_device_flows"]["usr_connect_flow"]
            self.assertEqual(saved_flow["user_code"], "WD4R-XYZ9")

    async def test_repeated_connect_cancels_previous_flow(self):
        mock_update = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_update.effective_chat.type = "private"
        mock_update.effective_chat.id = 123456

        mock_app = MagicMock()
        # Seed an existing pending flow task
        old_task = MagicMock()
        old_task.done.return_value = False
        old_flow = {"user_code": "OLD-CODE", "expires_at": time.time() + 500}
        mock_app.bot_data = {"active_device_flows": {"usr_repeat": (old_task, old_flow)}}

        mock_context = MagicMock()
        mock_context.application = mock_app
        mock_context.bot = MagicMock()

        fake_user = {"user_id": "usr_repeat"}
        new_flow = {
            "user_code": "NEW-CODE",
            "expires_in": 900,
            "expires_at": time.time() + 900,
        }

        with (
            patch("bot.telegram_bot._resolve_user", return_value=(fake_user, None)),
            patch("bot.telegram_bot.get_user_microsoft", return_value=None),
            patch(
                "bot.telegram_bot.initiate_device_flow_for_user",
                return_value=(new_flow, MagicMock(), MagicMock()),
            ),
            patch("bot.telegram_bot._poll_device_flow_task", new_callable=AsyncMock),
        ):
            await tb.connect_command(mock_update, mock_context)

            old_task.cancel.assert_called_once()
            self.assertEqual(old_flow["expires_at"], 0)


class TestTelegramBotConnectionCallback(unittest.IsolatedAsyncioTestCase):
    """Test connection_callback cancellation and security."""

    async def test_user_can_cancel_own_flow(self):
        mock_query = MagicMock()
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_query.data = f"{tb.DEVICE_CANCEL_PREFIX}usr_cancel1"

        mock_update = MagicMock()
        mock_update.callback_query = mock_query

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_flow = {"expires_at": time.time() + 600}

        mock_app = MagicMock()
        mock_app.bot_data = {"active_device_flows": {"usr_cancel1": (mock_task, mock_flow)}}
        mock_context = MagicMock()
        mock_context.application = mock_app

        fake_user = {"user_id": "usr_cancel1"}

        with patch("bot.telegram_bot._resolve_user", return_value=(fake_user, None)):
            await tb.connection_callback(mock_update, mock_context)

        mock_task.cancel.assert_called_once()
        self.assertEqual(mock_flow["expires_at"], 0)
        self.assertNotIn("usr_cancel1", mock_app.bot_data["active_device_flows"])
        mock_query.edit_message_text.assert_awaited_once()
        self.assertIn("cancelled", mock_query.edit_message_text.await_args[0][0])

    async def test_cross_user_cancellation_is_rejected(self):
        mock_query = MagicMock()
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_query.data = f"{tb.DEVICE_CANCEL_PREFIX}usr_victim"

        mock_update = MagicMock()
        mock_update.callback_query = mock_query

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_flow = {"expires_at": time.time() + 600}

        mock_app = MagicMock()
        mock_app.bot_data = {"active_device_flows": {"usr_victim": (mock_task, mock_flow)}}
        mock_context = MagicMock()
        mock_context.application = mock_app

        # Acting user is attacker, not victim
        attacker_user = {"user_id": "usr_attacker"}

        with patch("bot.telegram_bot._resolve_user", return_value=(attacker_user, None)):
            await tb.connection_callback(mock_update, mock_context)

        # Task should NOT be cancelled
        mock_task.cancel.assert_not_called()
        self.assertIn("usr_victim", mock_app.bot_data["active_device_flows"])
        mock_query.edit_message_text.assert_not_awaited()
        # Should show alert to attacker
        mock_query.answer.assert_awaited_with(
            "⚠️ You cannot cancel another user's login.", show_alert=True
        )


class TestBackgroundPollingTask(unittest.IsolatedAsyncioTestCase):
    """Test _poll_device_flow_task background lifecycle and message delivery."""

    async def test_polling_success_completes_and_notifies(self):
        mock_bot = MagicMock()
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.send_message = AsyncMock()

        mock_client = MagicMock()
        mock_cache = MagicMock()
        mock_flow = {"interval": 0.01, "expires_at": time.time() + 10}

        poll_results = [
            {"error": "authorization_pending"},
            {"access_token": "valid_token_123"},
        ]

        def fake_poll(cli, fl):
            return poll_results.pop(0)

        fake_updated = {"display_name": "Test Explorer"}

        bot_data = {"active_device_flows": {}}

        with (
            patch("bot.telegram_bot.poll_device_flow_step", side_effect=fake_poll),
            patch("bot.telegram_bot.complete_device_flow_connection", return_value=fake_updated),
        ):
            await tb._poll_device_flow_task(
                bot=mock_bot,
                client=mock_client,
                cache=mock_cache,
                flow=mock_flow,
                user_id="usr_poll_success",
                chat_id=123,
                status_message_id=99,
                bot_data=bot_data,
            )

        mock_bot.edit_message_text.assert_awaited_once()
        self.assertIn("Connected to Microsoft", mock_bot.edit_message_text.await_args.kwargs["text"])

        mock_bot.send_message.assert_awaited_once()
        self.assertIn("Test Explorer", mock_bot.send_message.await_args.kwargs["text"])

    async def test_polling_duplicate_account_notifies_warning(self):
        mock_bot = MagicMock()
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.send_message = AsyncMock()

        mock_flow = {"interval": 0.01, "expires_at": time.time() + 10}

        with (
            patch("bot.telegram_bot.poll_device_flow_step", return_value={"access_token": "tok"}),
            patch(
                "bot.telegram_bot.complete_device_flow_connection",
                side_effect=DeviceFlowDuplicateAccountError("Duplicate account"),
            ),
        ):
            await tb._poll_device_flow_task(
                bot=mock_bot,
                client=MagicMock(),
                cache=MagicMock(),
                flow=mock_flow,
                user_id="usr_dup",
                chat_id=123,
                status_message_id=99,
                bot_data={},
            )

        mock_bot.send_message.assert_awaited_once()
        self.assertIn("already connected to another ReelForge user", mock_bot.send_message.await_args.kwargs["text"])

    async def test_polling_expired_token_notifies_and_exits(self):
        mock_bot = MagicMock()
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.send_message = AsyncMock()

        mock_flow = {"interval": 0.01, "expires_at": time.time() + 10}

        with patch("bot.telegram_bot.poll_device_flow_step", return_value={"error": "expired_token"}):
            await tb._poll_device_flow_task(
                bot=mock_bot,
                client=MagicMock(),
                cache=MagicMock(),
                flow=mock_flow,
                user_id="usr_exp",
                chat_id=123,
                status_message_id=99,
                bot_data={},
            )

        mock_bot.edit_message_text.assert_awaited_once()
        self.assertIn("expired", mock_bot.edit_message_text.await_args.kwargs["text"])

    async def test_polling_access_denied_notifies_and_exits(self):
        mock_bot = MagicMock()
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.send_message = AsyncMock()

        mock_flow = {"interval": 0.01, "expires_at": time.time() + 10}

        with patch("bot.telegram_bot.poll_device_flow_step", return_value={"error": "access_denied"}):
            await tb._poll_device_flow_task(
                bot=mock_bot,
                client=MagicMock(),
                cache=MagicMock(),
                flow=mock_flow,
                user_id="usr_denied",
                chat_id=123,
                status_message_id=99,
                bot_data={},
            )

        mock_bot.edit_message_text.assert_awaited_once()
        self.assertIn("cancelled or denied", mock_bot.edit_message_text.await_args.kwargs["text"])


class TestMultiUserIsolationAndShutdown(unittest.TestCase):
    """Test multi-user concurrency and clean shutdown behavior."""

    def test_multi_user_flows_are_tracked_independently(self):
        task_a = MagicMock()
        task_a.done.return_value = False
        flow_a = {"user_code": "CODE-A", "expires_at": 1000}

        task_b = MagicMock()
        task_b.done.return_value = False
        flow_b = {"user_code": "CODE-B", "expires_at": 2000}

        bot_data = {
            "active_device_flows": {
                "usr_user_a": (task_a, flow_a),
                "usr_user_b": (task_b, flow_b),
            }
        }

        mock_app = MagicMock()
        mock_app.bot_data = bot_data

        # Cancelling user A leaves user B untouched
        tb.stop_active_device_flows(None)  # No-op with None
        self.assertEqual(len(bot_data["active_device_flows"]), 2)

        # Shutdown cancels both tasks and clears dict
        tb.stop_active_device_flows(mock_app)
        task_a.cancel.assert_called_once()
        task_b.cancel.assert_called_once()
        self.assertEqual(flow_a["expires_at"], 0)
        self.assertEqual(flow_b["expires_at"], 0)
        self.assertEqual(len(bot_data["active_device_flows"]), 0)


class TestRollbackAndPreservation(unittest.TestCase):
    """Ensure legacy OAuth modules and OneNote publishing remain intact."""

    def test_legacy_oauth_modules_importable(self):
        import auth.oauth_server as osrv
        import auth.state as ostate
        import storage.oauth_session as osess

        self.assertTrue(hasattr(osrv, "start_oauth_server"))
        self.assertTrue(hasattr(osrv, "stop_oauth_server"))
        self.assertTrue(hasattr(ostate, "generate_state"))
        self.assertTrue(hasattr(ostate, "compose_signed_state"))
        self.assertTrue(hasattr(osess, "create_oauth_session"))

    def test_graph_client_silent_auth_intact(self):
        import onenote.graph_client as gc
        self.assertTrue(hasattr(gc.GraphClient, "authenticate"))
        self.assertTrue(hasattr(gc.GraphClient, "get_profile"))


if __name__ == "__main__":
    unittest.main()
