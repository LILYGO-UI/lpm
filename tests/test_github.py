# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from urllib.parse import parse_qs

from lpm.errors import LpmError
from lpm.github import GitHubClient, _oauth_request, credentials_path, github_login


class GitHubAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.config_home = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self.config_home)}, clear=True
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    @patch("lpm.github.webbrowser.open")
    @patch("lpm.github.time.sleep")
    @patch("lpm.github.time.monotonic", side_effect=[100.0, 100.0, 101.0])
    @patch("lpm.github._oauth_request")
    def test_device_flow_saves_credentials(
        self, request, monotonic, sleep, open_browser
    ) -> None:
        request.side_effect = [
            {
                "device_code": "device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            },
            {"error": "authorization_pending"},
            {"access_token": "oauth-token", "token_type": "bearer"},
        ]
        output = io.StringIO()

        path = github_login(client_id="client-id", output=output)

        self.assertEqual(path, credentials_path())
        self.assertIn("ABCD-EFGH", output.getvalue())
        open_browser.assert_called_once_with("https://github.com/login/device")
        self.assertEqual(sleep.call_args_list, [call(5), call(5)])
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["github.com"]["oauth_token"],
            "oauth-token",
        )
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    @patch("lpm.github.webbrowser.open")
    @patch("lpm.github.time.sleep")
    @patch("lpm.github.time.monotonic", side_effect=[100.0, 100.0, 101.0])
    @patch("lpm.github._oauth_request")
    def test_device_flow_honors_slow_down(
        self, request, monotonic, sleep, open_browser
    ) -> None:
        request.side_effect = [
            {
                "device_code": "device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            },
            {"error": "slow_down"},
            {"access_token": "oauth-token"},
        ]

        github_login(client_id="client-id", output=io.StringIO())

        self.assertEqual(sleep.call_args_list, [call(5), call(10)])

    @patch("lpm.github.webbrowser.open")
    @patch("lpm.github.time.sleep")
    @patch("lpm.github.time.monotonic", side_effect=[100.0, 100.0])
    @patch("lpm.github._oauth_request")
    def test_device_flow_reports_denied_authorization(
        self, request, monotonic, sleep, open_browser
    ) -> None:
        request.side_effect = [
            {
                "device_code": "device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            },
            {"error": "access_denied"},
        ]

        with self.assertRaisesRegex(LpmError, "authorization was denied"):
            github_login(client_id="client-id", output=io.StringIO())

    @patch("lpm.github._oauth_request", side_effect=LpmError("stop"))
    def test_login_uses_bundled_oauth_client(self, request) -> None:
        with self.assertRaisesRegex(LpmError, "stop"):
            github_login(output=io.StringIO())
        request.assert_called_once_with(
            "https://github.com/login/device/code",
            {
                "client_id": "Ov23li2je0k6yeO2cisv",
                "scope": "public_repo",
            },
        )

    @patch("lpm.github.urlopen")
    def test_oauth_request_posts_form_and_requests_json(self, urlopen) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"device_code":"code"}'
        urlopen.return_value = response

        payload = _oauth_request(
            "https://github.com/login/device/code",
            {"client_id": "client-id", "scope": "public_repo"},
        )

        request = urlopen.call_args.args[0]
        self.assertEqual(payload, {"device_code": "code"})
        self.assertEqual(
            parse_qs(request.data.decode("ascii")),
            {"client_id": ["client-id"], "scope": ["public_repo"]},
        )
        self.assertEqual(request.get_header("Accept"), "application/json")

    @patch("lpm.github._read_stored_token", return_value="stored-token")
    def test_authenticated_client_uses_stored_token(self, read_token) -> None:
        client = GitHubClient.authenticated()
        self.assertEqual(client.token, "stored-token")

    @patch("lpm.github._read_stored_token", return_value="stored-token")
    def test_environment_token_takes_precedence(self, read_token) -> None:
        with patch.dict(
            os.environ,
            {"GH_TOKEN": "environment-token", "GITHUB_TOKEN": "actions-token"},
        ):
            client = GitHubClient.authenticated()
        self.assertEqual(client.token, "environment-token")
        read_token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
