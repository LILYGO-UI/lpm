# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from lpm.cli import main
from lpm.errors import LpmError
from lpm.registry import Submission


class CliTests(unittest.TestCase):
    @patch("lpm.cli.github_login")
    def test_login_uses_github_device_flow(self, login_mock) -> None:
        with redirect_stdout(io.StringIO()):
            code = main(["login"])
        self.assertEqual(code, 0)
        login_mock.assert_called_once_with()

    @patch("lpm.cli.find_project")
    @patch("lpm.cli.start")
    def test_start_forwards_program_arguments(self, start_mock, project_mock) -> None:
        start_mock.return_value = Path("/tmp/simulator.log")
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["start", "--no-build", "--", "--fullscreen", "sample.txt"])
        self.assertEqual(code, 0)
        self.assertEqual(
            start_mock.call_args.kwargs["arguments"], ["--fullscreen", "sample.txt"]
        )
        self.assertFalse(start_mock.call_args.kwargs["build"])

    @patch("lpm.cli.find_project", side_effect=LpmError("not a project"))
    def test_expected_error_has_clean_message(self, project_mock) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = main(["stop"])
        self.assertEqual(code, 1)
        self.assertEqual(errors.getvalue(), "lpm: error: not a project\n")

    @patch("lpm.cli.find_project")
    @patch("lpm.cli.deploy")
    def test_deploy_no_sudo(self, deploy_mock, project_mock) -> None:
        deploy_mock.return_value = Path("/tmp/demo.deb")
        with redirect_stdout(io.StringIO()):
            code = main(["deploy", "--host", "cm0.local", "--no-sudo"])
        self.assertEqual(code, 0)
        self.assertFalse(deploy_mock.call_args.kwargs["sudo"])

    @patch("lpm.cli.find_project")
    @patch("lpm.cli.deploy")
    def test_deploy_forwards_named_target(self, deploy_mock, project_mock) -> None:
        deploy_mock.return_value = Path("/tmp/demo.deb")
        with redirect_stdout(io.StringIO()):
            code = main(["deploy", "--target", "cm0-lab"])
        self.assertEqual(code, 0)
        self.assertEqual(deploy_mock.call_args.kwargs["target"], "cm0-lab")
        self.assertIsNone(deploy_mock.call_args.kwargs["host"])

    @patch("lpm.cli.find_project")
    @patch("lpm.cli.generate_metadata", return_value='{"package":"lilygo-ui-demo"}\n')
    def test_metadata_writes_stdout(self, metadata_mock, project_mock) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["metadata", "--format", "publish-json"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), '{"package":"lilygo-ui-demo"}\n')
        metadata_mock.assert_called_once_with(
            project_mock.return_value, output_format="publish-json"
        )

    @patch("lpm.cli.find_project")
    @patch(
        "lpm.cli.generate_metadata", return_value="set(LILYGO_UI_VERSION [[1.2.3]])\n"
    )
    def test_metadata_writes_output_file(self, metadata_mock, project_mock) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_path = Path(temp) / "generated" / "metadata.cmake"
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    ["metadata", "--format", "cmake", "--output", str(output_path)]
                )
            content = output_path.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertEqual(content, "set(LILYGO_UI_VERSION [[1.2.3]])\n")
        self.assertIn(f"Metadata: {output_path.resolve()}", output.getvalue())

    @patch("lpm.cli.find_project")
    @patch("lpm.cli.publish")
    def test_publish_prints_pending_submission(
        self, publish_mock, project_mock
    ) -> None:
        publish_mock.return_value = Submission(
            "https://github.com/LILYGO-UI/packages/pull/42",
            "lilygo-ui-demo",
            "1.2.3",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["publish", "dist/demo.deb"])
        self.assertEqual(code, 0)
        self.assertIn("pull/42", output.getvalue())
        self.assertIn("pending_review", output.getvalue())
        self.assertEqual(
            publish_mock.call_args.kwargs["package_path"], Path("dist/demo.deb")
        )

    @patch("lpm.cli.find_project")
    @patch("lpm.cli.unpublish")
    def test_unpublish_requires_reason_and_opens_submission(
        self, unpublish_mock, project_mock
    ) -> None:
        unpublish_mock.return_value = Submission(
            "https://github.com/LILYGO-UI/packages/pull/43",
            "lilygo-ui-demo",
            "1.2.3",
        )
        with redirect_stdout(io.StringIO()):
            code = main(["unpublish", "1.2.3", "--reason", "Critical startup failure"])
        self.assertEqual(code, 0)
        self.assertEqual(unpublish_mock.call_args.kwargs["version"], "1.2.3")
        self.assertEqual(
            unpublish_mock.call_args.kwargs["reason"], "Critical startup failure"
        )


if __name__ == "__main__":
    unittest.main()
