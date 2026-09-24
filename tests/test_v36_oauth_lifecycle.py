"""
tests/test_v36_oauth_lifecycle.py
---------------------------------
Unit tests for ReelForge V3.6 OAuth callback server lifecycle and configuration:
- Configurable bind host, port, and redirect URI.
- Idempotent and thread-safe instance lifecycle (start, stop).
- Global lifecycle helpers (start_oauth_server, stop_oauth_server, get_oauth_server).
- Clean resource release on shutdown (socket closure, thread termination).
- Bot lifecycle integration (post_init, post_shutdown, error handling, missing secret).
"""

from __future__ import annotations

import asyncio
import os
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import urllib.request
import urllib.error

import config
import auth.oauth_server as osrv
import bot.telegram_bot as tb


class TestOAuthServerConfiguration(unittest.TestCase):
    def test_default_configuration_matches_config(self):
        server = osrv.OAuthCallbackServer()
        self.assertEqual(server.host, config.OAUTH_HOST)
        self.assertEqual(server.port, config.OAUTH_LISTEN_PORT)
        self.assertEqual(server.redirect_uri, config.OAUTH_REDIRECT_URI)
        self.assertEqual(server.path, "/auth/callback")
        self.assertFalse(server.is_running)

    def test_custom_host_and_port(self):
        server = osrv.OAuthCallbackServer(
            host="0.0.0.0",
            port=9090,
            redirect_uri="https://reelforge.example.com/oauth/callback",
        )
        self.assertEqual(server.host, "0.0.0.0")
        self.assertEqual(server.port, 9090)
        self.assertEqual(server.redirect_uri, "https://reelforge.example.com/oauth/callback")
        self.assertEqual(server.path, "/oauth/callback")

    def test_reelforge_start_oauth_server_config_toggle(self):
        # Default is False (0) to preserve local development compatibility
        self.assertFalse(config.REELFORGE_START_OAUTH_SERVER)


class TestOAuthServerLifecycle(unittest.TestCase):
    def tearDown(self):
        osrv.stop_oauth_server()

    def test_start_and_stop_lifecycle(self):
        server = osrv.OAuthCallbackServer(host="127.0.0.1", port=0)
        self.assertFalse(server.is_running)

        server.start()
        self.assertTrue(server.is_running)
        bound_port = server.port
        self.assertGreater(bound_port, 0)

        # Verify the server actively responds on the bound socket
        url = f"http://127.0.0.1:{bound_port}/not-found-path"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                self.assertEqual(resp.status, 404)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

        server.stop()
        self.assertFalse(server.is_running)

    def test_instance_start_is_idempotent(self):
        server = osrv.OAuthCallbackServer(host="127.0.0.1", port=0)
        server.start()
        self.assertTrue(server.is_running)
        initial_port = server.port
        initial_thread = server._thread

        # Calling start again on the same instance returns self without recreating
        res = server.start()
        self.assertIs(res, server)
        self.assertEqual(server.port, initial_port)
        self.assertIs(server._thread, initial_thread)

        server.stop()

    def test_instance_stop_is_idempotent(self):
        server = osrv.OAuthCallbackServer(host="127.0.0.1", port=0)
        server.start()
        self.assertTrue(server.is_running)

        server.stop()
        self.assertFalse(server.is_running)

        # Repeated stop calls should not raise
        server.stop()
        self.assertFalse(server.is_running)

    def test_context_manager_support(self):
        with osrv.OAuthCallbackServer(host="127.0.0.1", port=0) as server:
            self.assertTrue(server.is_running)
            self.assertGreater(server.port, 0)

        self.assertFalse(server.is_running)


class TestGlobalOAuthServerLifecycle(unittest.TestCase):
    def tearDown(self):
        osrv.stop_oauth_server()

    def test_global_start_stop(self):
        self.assertIsNone(osrv.get_oauth_server())

        server = osrv.start_oauth_server(host="127.0.0.1", port=0)
        self.assertTrue(server.is_running)
        self.assertIs(osrv.get_oauth_server(), server)

        # Calling start_oauth_server again returns the existing running server (no duplicate)
        dup = osrv.start_oauth_server(host="127.0.0.1", port=0)
        self.assertIs(dup, server)

        osrv.stop_oauth_server()
        self.assertIsNone(osrv.get_oauth_server())
        self.assertFalse(server.is_running)

        # Stop when already stopped is a safe no-op
        osrv.stop_oauth_server()
        self.assertIsNone(osrv.get_oauth_server())

    def test_thread_safety_concurrent_start(self):
        servers = []
        errors = []

        def worker():
            try:
                s = osrv.start_oauth_server(host="127.0.0.1", port=0)
                servers.append(s)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)

        self.assertEqual(errors, [])
        self.assertEqual(len(servers), 5)
        # All concurrent threads must have received the EXACT same instance
        for s in servers:
            self.assertIs(s, servers[0])

        osrv.stop_oauth_server()


class TestBotOAuthLifecycleIntegration(unittest.TestCase):
    def tearDown(self):
        osrv.stop_oauth_server()

    def test_bot_lifecycle_hooks_disabled_when_both_worker_and_oauth_disabled(self):
        ab = MagicMock()
        ab.token.return_value = ab
        app = MagicMock()
        ab.build.return_value = app

        with (
            patch.object(tb, "REELFORGE_EMBEDDED_WORKER", False),
            patch.object(tb, "REELFORGE_START_OAUTH_SERVER", False),
            patch.object(tb, "ApplicationBuilder", return_value=ab),
        ):
            tb.run_bot()

        ab.post_init.assert_not_called()
        ab.post_shutdown.assert_not_called()

    def test_bot_lifecycle_hooks_registered_when_oauth_enabled(self):
        ab = MagicMock()
        ab.token.return_value = ab
        ab.post_init.return_value = ab
        ab.post_shutdown.return_value = ab
        app = MagicMock()
        ab.build.return_value = app

        with (
            patch.object(tb, "REELFORGE_EMBEDDED_WORKER", False),
            patch.object(tb, "REELFORGE_START_OAUTH_SERVER", True),
            patch.object(tb, "ApplicationBuilder", return_value=ab),
        ):
            tb.run_bot()

        ab.post_init.assert_called_once()
        ab.post_shutdown.assert_called_once()

    def test_bot_post_init_and_post_shutdown_execution(self):
        ab = MagicMock()
        ab.token.return_value = ab
        ab.post_init.return_value = ab
        ab.post_shutdown.return_value = ab
        app = MagicMock()
        ab.build.return_value = app

        post_init_fn = None
        post_shutdown_fn = None

        def capture_post_init(fn):
            nonlocal post_init_fn
            post_init_fn = fn
            return ab

        def capture_post_shutdown(fn):
            nonlocal post_shutdown_fn
            post_shutdown_fn = fn
            return ab

        ab.post_init.side_effect = capture_post_init
        ab.post_shutdown.side_effect = capture_post_shutdown

        with (
            patch.object(tb, "REELFORGE_EMBEDDED_WORKER", False),
            patch.object(tb, "REELFORGE_START_OAUTH_SERVER", True),
            patch.object(config, "OAUTH_STATE_SECRET", "test-secret-32-bytes-minimum!"),
            patch.object(config, "OAUTH_HOST", "127.0.0.1"),
            patch.object(config, "OAUTH_LISTEN_PORT", 0),
            patch.object(tb, "ApplicationBuilder", return_value=ab),
        ):
            tb.run_bot()

            self.assertIsNotNone(post_init_fn)
            self.assertIsNotNone(post_shutdown_fn)

            mock_app = MagicMock()
            # Execute post_init while config patches (especially port=0) are active
            asyncio.run(post_init_fn(mock_app))
            server = osrv.get_oauth_server()
            self.assertIsNotNone(server)
            self.assertTrue(server.is_running)

            # Execute post_shutdown
            asyncio.run(post_shutdown_fn(mock_app))
            self.assertIsNone(osrv.get_oauth_server())
            self.assertFalse(server.is_running)

    def test_bot_post_init_skips_when_secret_unset(self):
        ab = MagicMock()
        ab.token.return_value = ab
        ab.post_init.return_value = ab
        ab.post_shutdown.return_value = ab
        app = MagicMock()
        ab.build.return_value = app

        post_init_fn = None
        ab.post_init.side_effect = lambda fn: (setattr(ab, "_fn", fn), fn)[1] and ab

        with (
            patch.object(tb, "REELFORGE_EMBEDDED_WORKER", False),
            patch.object(tb, "REELFORGE_START_OAUTH_SERVER", True),
            patch.object(config, "OAUTH_STATE_SECRET", ""),
            patch.object(tb, "ApplicationBuilder", return_value=ab),
        ):
            tb.run_bot()

            fn = getattr(ab, "_fn")
            mock_app = MagicMock()
            # Running post_init with empty secret should NOT raise and should NOT start server
            asyncio.run(fn(mock_app))
            self.assertIsNone(osrv.get_oauth_server())

    def test_bot_post_init_handles_bind_error_gracefully(self):
        ab = MagicMock()
        ab.token.return_value = ab
        ab.post_init.return_value = ab
        ab.post_shutdown.return_value = ab
        app = MagicMock()
        ab.build.return_value = app

        ab.post_init.side_effect = lambda fn: (setattr(ab, "_fn", fn), fn)[1] and ab

        with (
            patch.object(tb, "REELFORGE_EMBEDDED_WORKER", False),
            patch.object(tb, "REELFORGE_START_OAUTH_SERVER", True),
            patch.object(config, "OAUTH_STATE_SECRET", "test-secret-32-bytes-minimum!"),
            patch.object(tb, "start_oauth_server", side_effect=OSError("Address already in use")),
            patch.object(tb, "ApplicationBuilder", return_value=ab),
        ):
            tb.run_bot()

            fn = getattr(ab, "_fn")
            mock_app = MagicMock()
            # Should catch OSError, log warning, and not raise
            asyncio.run(fn(mock_app))


if __name__ == "__main__":
    unittest.main()
