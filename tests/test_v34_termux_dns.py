"""Mocked Android/Termux DNS compatibility tests for MongoDB SRV lookup."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import dns.resolver

from storage import db


TERMUX_PREFIX = "/data/data/com.termux/files/usr"


class TestTermuxDnsConfiguration(unittest.TestCase):
    def setUp(self):
        self.env_patch = mock.patch.dict(
            os.environ,
            {"PREFIX": TERMUX_PREFIX},
            clear=True,
        )
        self.env_patch.start()
        self.platform_patch = mock.patch.object(sys, "platform", "linux")
        self.platform_patch.start()
        self.prefix_patch = mock.patch.object(sys, "prefix", TERMUX_PREFIX)
        self.prefix_patch.start()
        self.default_resolver_patch = mock.patch.object(
            dns.resolver,
            "default_resolver",
            object(),
        )
        self.default_resolver_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self.platform_patch.stop)
        self.addCleanup(self.prefix_patch.stop)
        self.addCleanup(self.default_resolver_patch.stop)

    def test_android_termux_detection_uses_platform_and_prefix(self):
        self.assertTrue(db._is_android_termux())
        with mock.patch.object(sys, "platform", "android"):
            self.assertTrue(db._is_android_termux())
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertFalse(db._is_android_termux())
        with mock.patch.object(sys, "platform", "win32"):
            self.assertFalse(db._is_android_termux())
        with mock.patch.dict(os.environ, {"PREFIX": "/usr/local"}):
            with mock.patch.object(sys, "prefix", "/usr"):
                self.assertFalse(db._is_android_termux())

    def test_nameserver_parser_accepts_ip_addresses_and_ignores_bad_lines(self):
        contents = """
            # managed by Termux
            nameserver 1.1.1.1 # IPv4 resolver
            nameserver 2001:4860:4860::8888 ; IPv6 resolver
            nameserver not-an-address
            nameserver
            nameserver 8.8.8.8 extra
            search example.invalid
            nameserver 1.1.1.1
        """
        self.assertEqual(
            db._parse_termux_nameservers(contents),
            ["1.1.1.1", "2001:4860:4860::8888"],
        )

    def test_phone_resolver_file_path_parses_exact_ipv4_entries(self):
        file_path = Path(TERMUX_PREFIX) / "etc" / "resolv.conf"
        with mock.patch.object(Path, "is_file", return_value=True):
            with mock.patch.object(
                Path,
                "read_text",
                return_value="nameserver 8.8.8.8\nnameserver 8.8.4.4\n",
            ):
                self.assertEqual(
                    db._parse_termux_nameservers(str(file_path)),
                    ["8.8.8.8", "8.8.4.4"],
                )

    def test_android_platform_configures_resolver_from_phone_contents(self):
        resolver = mock.Mock()
        phone_resolver_contents = "nameserver 8.8.8.8\nnameserver 8.8.4.4\n"
        with mock.patch.object(sys, "platform", "android"):
            with mock.patch.object(
                db.Path,
                "read_text",
                return_value=phone_resolver_contents,
                autospec=True,
            ):
                with mock.patch.object(
                    dns.resolver,
                    "Resolver",
                    return_value=resolver,
                ) as resolver_factory:
                    db._configure_termux_dns()

        resolver_factory.assert_called_once_with(configure=False)
        self.assertEqual(resolver.nameservers, ["8.8.8.8", "8.8.4.4"])
        self.assertIs(dns.resolver.default_resolver, resolver)

    def test_dnspython_module_lookup_uses_configured_default_resolver(self):
        phone_resolver_contents = "nameserver 8.8.8.8\nnameserver 8.8.4.4\n"
        with mock.patch.object(sys, "platform", "android"):
            with mock.patch.object(
                db.Path,
                "read_text",
                return_value=phone_resolver_contents,
                autospec=True,
            ):
                db._configure_termux_dns()

        configured_resolver = dns.resolver.default_resolver
        with mock.patch.object(
            configured_resolver,
            "resolve",
            return_value=mock.sentinel.answer,
        ) as resolve:
            answer = dns.resolver.resolve("example.invalid", "A")

        self.assertIs(answer, mock.sentinel.answer)
        resolve.assert_called_once()
        self.assertEqual(resolve.call_args.args[:2], ("example.invalid", "A"))

    def test_termux_resolver_is_initialized_with_bounded_settings(self):
        resolver = mock.Mock()
        with mock.patch.object(
            db.Path,
            "read_text",
            return_value="nameserver 1.1.1.1\nnameserver 2001:4860:4860::8888\n",
            autospec=True,
        ) as read_resolver_file:
            with mock.patch.object(dns.resolver, "Resolver", return_value=resolver) as factory:
                db._configure_termux_dns()

        resolver_file = str(read_resolver_file.call_args.args[0]).replace("\\", "/")
        self.assertTrue(resolver_file.endswith("/com.termux/files/usr/etc/resolv.conf"))
        factory.assert_called_once_with(configure=False)
        self.assertEqual(resolver.nameservers, ["1.1.1.1", "2001:4860:4860::8888"])
        self.assertEqual(resolver.timeout, db.TERMUX_DNS_TIMEOUT_SECONDS)
        self.assertEqual(resolver.lifetime, db.TERMUX_DNS_LIFETIME_SECONDS)
        self.assertIs(dns.resolver.default_resolver, resolver)

    def test_resolver_uses_sys_prefix_when_prefix_environment_is_missing(self):
        resolver = mock.Mock()
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(sys, "prefix", TERMUX_PREFIX):
                self.assertTrue(db._is_android_termux())
                with mock.patch.object(
                    db.Path,
                    "read_text",
                    return_value="nameserver 1.1.1.1\n",
                    autospec=True,
                ) as read_resolver_file:
                    with mock.patch.object(
                        dns.resolver,
                        "Resolver",
                        return_value=resolver,
                    ):
                        db._configure_termux_dns()

        resolver_file = str(read_resolver_file.call_args.args[0]).replace("\\", "/")
        self.assertEqual(resolver_file, f"{TERMUX_PREFIX}/etc/resolv.conf")
        self.assertEqual(resolver.nameservers, ["1.1.1.1"])
        self.assertIs(dns.resolver.default_resolver, resolver)

    def test_missing_termux_resolver_file_has_actionable_error(self):
        with mock.patch.object(db.Path, "read_text", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, r"\$PREFIX/etc/resolv\.conf"):
                db._configure_termux_dns()

    def test_empty_or_malformed_termux_resolver_file_fails_clearly(self):
        for contents in ("", "# only a comment\n", "nameserver invalid\n", "search local\n"):
            with self.subTest(contents=contents):
                with mock.patch.object(db.Path, "read_text", return_value=contents):
                    with mock.patch.object(dns.resolver, "Resolver") as factory:
                        with self.assertRaisesRegex(RuntimeError, "No valid nameserver"):
                            db._configure_termux_dns()
                factory.assert_not_called()

    def test_non_android_platforms_leave_dns_resolver_unchanged(self):
        original = dns.resolver.default_resolver
        for platform in ("win32", "darwin", "linux"):
            with self.subTest(platform=platform):
                with mock.patch.object(sys, "platform", platform):
                    if platform == "linux":
                        with mock.patch.dict(os.environ, {"PREFIX": "/usr/local"}):
                            with mock.patch.object(sys, "prefix", "/usr"):
                                db._configure_termux_dns()
                    else:
                        db._configure_termux_dns()
        self.assertIs(dns.resolver.default_resolver, original)

    def test_dns_is_configured_before_mongo_client_construction(self):
        calls = []

        def configure_dns():
            calls.append("dns")

        def construct_client(*args, **kwargs):
            calls.append("mongo")
            return mock.sentinel.client

        with mock.patch.object(db, "_client", None):
            with mock.patch.object(db, "_configure_termux_dns", side_effect=configure_dns):
                with mock.patch.object(db, "MongoClient", side_effect=construct_client):
                    client = db.get_mongo_client(uri="mongodb+srv://unit.test/example")

        self.assertIs(client, mock.sentinel.client)
        self.assertEqual(calls, ["dns", "mongo"])


if __name__ == "__main__":
    unittest.main()
