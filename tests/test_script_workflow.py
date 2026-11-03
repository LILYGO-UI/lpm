# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lpm.commands import deploy, pack, start, stop, test
from lpm.config import Project, find_project
from lpm.errors import CommandError, LpmError
from lpm.registry import publish, unpublish


_WORKFLOW = '''import json
import os
from pathlib import Path
import sys

Path("argv.json").write_text(json.dumps(sys.argv[1:]))
action, destination, mode, *extra = sys.argv[1:]
destination = Path(destination)
if action == "test":
    if mode != "missing":
        destination.write_text(mode)
    if extra and extra[0] == "fail":
        raise SystemExit(7)
else:
    internal = destination / "_CPack_Packages" / "Linux" / "DEB"
    internal.mkdir(parents=True, exist_ok=True)
    (internal / "internal.deb").write_bytes(b"internal")
    if mode == "new":
        (destination / "runtime package.deb").write_bytes(b"new package")
    elif mode == "rewrite":
        package = destination / "runtime package.deb"
        old = package.stat()
        package.write_bytes(b"new package")
        os.utime(package, ns=(old.st_atime_ns, old.st_mtime_ns))
    elif mode == "fail":
        (destination / "runtime package.deb").write_bytes(b"partial")
        raise SystemExit(7)
'''

_PASSING_XML = '<testsuites><testsuite tests="1" failures="0"><testcase name="works"/></testsuite></testsuites>'


class ScriptWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="lpm scripts ")
        self.root = Path(self.temporary.name).resolve() / "project with spaces"
        self.root.mkdir()
        self.script = self.root / "workflow tool.py"
        self.script.write_text(_WORKFLOW, encoding="utf-8")
        self.project = Project(
            self.root,
            {
                "schema_version": 1,
                "project": {"kind": "runtime", "package": "meshtasticd"},
                "build": {
                    "backend": "script",
                    "package_dir": "package output",
                    "test_command": ["{python}", self.script.name, "test", "{report}", _PASSING_XML],
                    "pack_command": ["{python}", self.script.name, "pack", "{output}", "new"],
                },
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_real_test_command_preserves_argv_and_absolute_report_path(self) -> None:
        literals = ["two words", "$(touch injected)", "${HOME}", "{python}/literal", "--report={report}"]
        self.project.data["build"]["test_command"].extend(literals)
        report = self.root / "report output" / "test results.xml"
        self.assertEqual(test(self.project, report=report), report)
        self.assertEqual(report.read_text(), _PASSING_XML)
        argv = json.loads((self.root / "argv.json").read_text())
        self.assertEqual(argv, ["test", str(report), _PASSING_XML, *literals])
        self.assertFalse((self.root / "injected").exists())

    def test_default_report_and_configured_package_dir(self) -> None:
        self.assertEqual(test(self.project), self.root / ".lpm" / "test-results.xml")
        packages = pack(self.project)
        self.assertEqual(packages, [self.root / "package output" / "runtime package.deb"])

    def test_real_pack_honors_output_and_excludes_unchanged_and_internal_debs(self) -> None:
        output = self.root / "custom packages"
        output.mkdir()
        (output / "old.deb").write_bytes(b"stale")
        (output / "directory.deb").mkdir()
        packages = pack(self.project, output=output)
        self.assertEqual(packages, [output / "runtime package.deb"])
        self.assertFalse((self.root / "package output").exists())
        self.assertEqual(json.loads((self.root / "argv.json").read_text()), ["pack", str(output), "new"])

    def test_pack_accepts_rewritten_reproducible_package_with_preserved_mtime(self) -> None:
        output = self.root / "packages"
        output.mkdir()
        package = output / "runtime package.deb"
        package.write_bytes(b"new package")
        previous_mtime = package.stat().st_mtime_ns
        self.project.data["build"]["pack_command"][-1] = "rewrite"
        self.assertEqual(pack(self.project, output=output), [package])
        self.assertEqual(package.stat().st_mtime_ns, previous_mtime)

    def test_pack_rejects_unchanged_recent_deb_and_internal_outputs(self) -> None:
        output = self.root / "packages"
        output.mkdir()
        (output / "stale.deb").write_bytes(b"existing")
        self.project.data["build"]["pack_command"][-1] = "nothing"
        with self.assertRaisesRegex(LpmError, "no new or changed Debian package"):
            pack(self.project, output=output)

    def test_pack_rejects_missing_packages(self) -> None:
        self.project.data["build"]["pack_command"][-1] = "nothing"
        with self.assertRaisesRegex(LpmError, "no new or changed Debian package"):
            pack(self.project)

    def test_pack_propagates_failure_even_with_generated_deb(self) -> None:
        self.project.data["build"]["pack_command"][-1] = "fail"
        with self.assertRaises(CommandError):
            pack(self.project)

    def test_test_requires_fresh_report(self) -> None:
        report = self.root / "stale.xml"
        report.write_text(_PASSING_XML)
        self.project.data["build"]["test_command"][-1] = "missing"
        with self.assertRaisesRegex(LpmError, "valid JUnit report"):
            test(self.project, report=report)
        self.assertFalse(report.exists())

    def test_test_rejects_invalid_or_failing_reports(self) -> None:
        for content in (
            "invalid XML", "<html/>", '<testsuite tests="one"/>',
            '<testsuite tests="-1"/>', '<testsuite failures="1"/>',
            '<testsuite><testcase name="bad"><failure/></testcase></testsuite>',
            '<testsuite errors="1"/>',
        ):
            with self.subTest(content=content):
                self.project.data["build"]["test_command"][-1] = content
                with self.assertRaises(LpmError):
                    test(self.project)

    def test_test_rejects_empty_placeholder_reports(self) -> None:
        for content in ('<testsuite/>', '<testsuites tests="0"/>', '<testsuite tests="1"/>'):
            with self.subTest(content=content):
                self.project.data["build"]["test_command"][-1] = content
                with self.assertRaisesRegex(LpmError, "no test cases"):
                    test(self.project)

    def test_test_allows_recorded_skips(self) -> None:
        content = '<testsuite tests="1" skipped="1"><testcase name="optional"><skipped/></testcase></testsuite>'
        self.project.data["build"]["test_command"][-1] = content
        self.assertEqual(test(self.project).read_text(), content)

    def test_test_propagates_failure_and_preserves_failure_report(self) -> None:
        report = self.root / "failure.xml"
        self.project.data["build"]["test_command"].extend(["fail"])
        with self.assertRaises(CommandError):
            test(self.project, report=report)
        self.assertEqual(report.read_text(), _PASSING_XML)

    def test_invalid_commands_fail_before_execution(self) -> None:
        for action, workflow in (("test", test), ("pack", pack)):
            for command in (None, "python script.py", [], [""], [1], ["python", None], ["python\0"]):
                with self.subTest(action=action, command=command):
                    self.project.data["build"][f"{action}_command"] = command
                    with self.assertRaisesRegex(LpmError, "nonempty array"), patch("lpm.script_workflow.run") as run:
                        workflow(self.project)
                    run.assert_not_called()

    def test_commands_require_separate_destination_tokens(self) -> None:
        for action, workflow, token in (("test", test, "{report}"), ("pack", pack, "{output}")):
            self.project.data["build"][f"{action}_command"] = ["{python}", self.script.name, f"--output={token}"]
            with self.subTest(action=action), self.assertRaisesRegex(LpmError, "as a separate argument"):
                workflow(self.project)

    def test_script_backend_rejects_preset(self) -> None:
        for workflow in (test, pack):
            with self.subTest(workflow=workflow.__name__), self.assertRaisesRegex(LpmError, "--preset"):
                workflow(self.project, preset="host-simulator")
        self.assertFalse((self.root / "argv.json").exists())

    def test_unknown_backend_or_kind_is_rejected(self) -> None:
        for section, key, value in (("build", "backend", "meson"), ("project", "kind", "library")):
            original = self.project.data[section][key]
            self.project.data[section][key] = value
            for workflow in (test, pack, deploy):
                with self.subTest(key=key, workflow=workflow.__name__), self.assertRaisesRegex(LpmError, f"{section}.{key}"):
                    workflow(self.project)
            self.project.data[section][key] = original

    @patch("lpm.registry.GitHubClient.authenticated")
    @patch("lpm.commands.configure_and_build")
    def test_runtime_rejects_application_commands_before_side_effects(self, build, authenticate) -> None:
        actions = [
            lambda: start(self.project), lambda: stop(self.project),
        ]
        for action in actions:
            with self.subTest(action=action), self.assertRaisesRegex(LpmError, "runtime projects"):
                action()
        build.assert_not_called()
        authenticate.assert_not_called()

    @patch("lpm.commands.require_tool")
    @patch("lpm.commands.run")
    def test_runtime_deploy_uses_configured_output_without_application_metadata(self, run, tool) -> None:
        package = pack(self.project)[0]
        result = deploy(self.project, host="device.local", user="pi")
        self.assertEqual(result, package)
        self.assertEqual(run.call_args_list[0].args[0][:4], ["scp", "-P", "22", package])
        self.assertIn("sudo -- apt-get install --reinstall --yes", run.call_args_list[1].args[0][-1])
        self.assertEqual(len(run.call_args_list), 2)

    def test_cli_discovers_script_project_and_resolves_relative_report_from_cwd(self) -> None:
        config = self.root / "lpm.toml"
        config.write_text(
            'schema_version = 1\n[project]\nkind = "runtime"\npackage = "meshtasticd"\n'
            '[build]\nbackend = "script"\ntest_command = '
            + json.dumps(self.project.data["build"]["test_command"]) + "\n",
            encoding="utf-8",
        )
        child = self.root / "child directory"
        child.mkdir()
        self.assertEqual(find_project(child).kind, "runtime")
        result = subprocess.run(
            [sys.executable, "-m", "lpm", "test", "--report", "reports/test output.xml"],
            cwd=child,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = child / "reports" / "test output.xml"
        self.assertEqual(report.read_text(), _PASSING_XML)
        self.assertIn(str(report), result.stdout)


if __name__ == "__main__":
    unittest.main()
