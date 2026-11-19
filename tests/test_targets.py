# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from lpm.cli import main
from lpm.config import find_project, render_config
from lpm.registry import DebianArtifact, Submission
from lpm.targets import load_target, save_target
from test_registry import FakeGitHub, make_project


class TargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = make_project(self.root)
        self.project.data["project"]["compatibility"]["architectures"] = ["arm64", "riscv64"]
        self.project.data["build"]["cross_presets"] = {
            "arm64": "cm0-cross", "riscv64": "k230-debian",
        }
        self.config_path = self.root / "lpm.toml"
        self.write_config()
        self.output = io.StringIO()
        self.errors = io.StringIO()

    def write_config(self) -> None:
        self.config_path.write_text(render_config(self.project.data), encoding="utf-8")

    def invoke(self, *args: str, cwd: Path | None = None) -> int:
        with chdir(cwd or self.root), redirect_stdout(self.output), redirect_stderr(self.errors):
            return main(list(args))

    def test_target_persists_from_child_and_does_not_change_project_config(self) -> None:
        original = self.config_path.read_bytes()
        child = self.root / "src" / "nested"
        child.mkdir(parents=True)
        for target in ("cm0", "k230", "all"):
            with self.subTest(target=target):
                self.assertEqual(self.invoke("set-target", target, cwd=child), 0)
                self.assertEqual(load_target(find_project(child)), target)
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertEqual(list(child.iterdir()), [])
        self.assertIn("all declared architectures", self.output.getvalue())

    def test_selection_survives_separate_cli_processes(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        for target, architectures in (
            ("cm0", ["arm64"]), ("k230", ["riscv64"]), ("all", ["arm64", "riscv64"]),
        ):
            with self.subTest(target=target):
                selected = subprocess.run(
                    [sys.executable, "-m", "lpm", "set-target", target],
                    cwd=self.root, env=environment, capture_output=True, text=True,
                )
                self.assertEqual(selected.returncode, 0, selected.stderr)
                metadata = subprocess.run(
                    [sys.executable, "-m", "lpm", "metadata", "--format", "publish-json"],
                    cwd=self.root, env=environment, capture_output=True, text=True,
                )
                self.assertEqual(metadata.returncode, 0, metadata.stderr)
                self.assertEqual(json.loads(metadata.stdout)["compatibility"]["architectures"], architectures)

    def test_projects_have_independent_targets(self) -> None:
        other = self.root / "other"
        other.mkdir()
        (other / "CMakeLists.txt").touch()
        self.assertIsNone(load_target(self.project))
        self.assertEqual(self.invoke("set-target", "cm0"), 0)
        self.assertIsNone(load_target(find_project(other)))
        self.assertEqual(self.invoke("set-target", "k230", cwd=other), 0)
        self.assertEqual(load_target(self.project), "cm0")
        self.assertEqual(load_target(find_project(other)), "k230")

    def test_unknown_target_does_not_overwrite_selection(self) -> None:
        save_target(self.project, "cm0")
        with redirect_stderr(self.errors), self.assertRaises(SystemExit) as error:
            main(["set-target", "unknown"])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(load_target(self.project), "cm0")

    def test_set_target_requires_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.invoke("set-target", "cm0", cwd=Path(temp)), 1)
        self.assertIn("not inside an LPM or CMake project", self.errors.getvalue())

    def test_malformed_state_is_reported_and_can_be_replaced(self) -> None:
        self.project.state_dir.mkdir()
        path = self.project.state_dir / "target.json"
        for value in (b"{", b"[]", b"{}", b'{"target": []}', b'{"target":"unknown"}', b"\xff"):
            with self.subTest(value=value):
                path.write_bytes(value)
                self.assertEqual(self.invoke("metadata", "--format", "cmake"), 1)
                self.assertEqual(self.invoke("set-target", "k230"), 0)
                self.assertEqual(load_target(self.project), "k230")
        self.assertIn("run lpm set-target", self.errors.getvalue())

    def test_failed_save_preserves_previous_selection(self) -> None:
        save_target(self.project, "cm0")
        with patch("lpm.targets.Path.replace", side_effect=PermissionError("read-only")):
            self.assertEqual(self.invoke("set-target", "k230"), 1)
        self.assertEqual(load_target(self.project), "cm0")
        self.assertEqual(list(self.project.state_dir.iterdir()), [self.project.state_dir / "target.json"])
        self.assertIn("cannot save target", self.errors.getvalue())

    def test_metadata_honors_explicit_architecture_without_changing_target(self) -> None:
        save_target(self.project, "k230")
        self.assertEqual(self.invoke("metadata", "--format", "cmake", "--arch", "arm64"), 0)
        self.assertIn("set(LILYGO_UI_PACKAGE_ARCHITECTURE [[arm64]])", self.output.getvalue())
        self.assertEqual(load_target(self.project), "k230")

    def test_unconfigured_target_preserves_full_metadata(self) -> None:
        self.assertEqual(self.invoke("metadata", "--format", "publish-json"), 0)
        self.assertEqual(
            json.loads(self.output.getvalue())["compatibility"]["architectures"],
            ["arm64", "riscv64"],
        )
        self.assertFalse(self.project.state_dir.exists())

    @patch("lpm.cli.pack", return_value=[])
    def test_pack_selection_and_explicit_overrides(self, pack) -> None:
        cases = (
            ("cm0", [], "arm64", False, None),
            ("k230", [], "riscv64", False, None),
            ("all", [], None, True, None),
            ("all", ["--arch", "arm64"], "arm64", False, None),
            ("cm0", ["--arch", "riscv64"], "riscv64", False, None),
            ("k230", ["--all-architectures"], None, True, None),
            ("all", ["--preset", "custom"], None, False, "custom"),
        )
        for target, options, architecture, all_architectures, preset in cases:
            with self.subTest(target=target, options=options):
                save_target(self.project, target)
                self.assertEqual(self.invoke("pack", *options), 0)
                self.assertEqual(pack.call_args.kwargs["architecture"], architecture)
                self.assertEqual(pack.call_args.kwargs["all_architectures"], all_architectures)
                self.assertEqual(pack.call_args.kwargs["preset"], preset)
                self.assertEqual(load_target(self.project), target)

    @patch("lpm.registry.inspect_debian_package")
    @patch("lpm.commands._pack_cmake")
    def test_pack_builds_selected_presets_and_all_architectures(self, build, inspect) -> None:
        def produce(project, *, preset, output):
            arch = {"cm0-cross": "arm64", "k230-debian": "riscv64"}[preset]
            path = output / f"{arch}.deb"
            path.write_bytes(b"package fixture")
            return [path]

        build.side_effect = produce
        inspect.side_effect = lambda path: DebianArtifact(
            path, "lilygo-ui-demo", "1.2.3", path.stem, 15, "d" * 64,
        )
        for target, presets in (
            ("cm0", ["cm0-cross"]), ("k230", ["k230-debian"]),
            ("all", ["cm0-cross", "k230-debian"]),
        ):
            with self.subTest(target=target):
                build.reset_mock()
                self.assertEqual(self.invoke("set-target", target), 0)
                self.assertEqual(self.invoke("pack"), 0, self.errors.getvalue())
                self.assertEqual([call.kwargs["preset"] for call in build.call_args_list], presets)
        for arch in ("arm64", "riscv64"):
            self.assertTrue((self.root / f"dist/lilygo-ui-demo_1.2.3_{arch}.deb").is_file())

        self.project.data["project"]["compatibility"]["architectures"] = ["arm64"]
        del self.project.data["build"]["cross_presets"]
        self.write_config()
        for target in ("cm0", "all"):
            with self.subTest(legacy_target=target):
                build.reset_mock()
                save_target(self.project, target)
                self.assertEqual(self.invoke("pack"), 0, self.errors.getvalue())
                self.assertEqual(build.call_args.kwargs["preset"], "cm0-cross")

    @patch("lpm.commands._pack_cmake")
    def test_missing_cross_preset_fails_before_build(self, build) -> None:
        del self.project.data["build"]["cross_presets"]["riscv64"]
        self.write_config()
        save_target(self.project, "all")
        self.assertEqual(self.invoke("pack"), 1)
        build.assert_not_called()
        self.assertIn("every architecture", self.errors.getvalue())

    @patch("lpm.cli.publish", return_value=Submission("https://example.com/pr/1", "demo", "1.2.3"))
    def test_publish_selection_and_explicit_overrides(self, publish) -> None:
        for target, options, architecture, all_architectures in (
            ("cm0", [], "arm64", False), ("k230", [], "riscv64", False),
            ("all", [], None, True), ("all", ["--arch", "arm64"], "arm64", False),
            ("cm0", ["--all-architectures"], None, True),
            ("all", ["one.deb", "two.deb"], None, False),
            ("cm0", ["one.deb"], None, False),
        ):
            with self.subTest(target=target, options=options):
                save_target(self.project, target)
                self.assertEqual(self.invoke("publish", *options), 0)
                self.assertEqual(publish.call_args.kwargs.get("architecture"), architecture)
                self.assertEqual(publish.call_args.kwargs.get("all_architectures", False), all_architectures)

    @patch("lpm.cli.deploy", return_value=Path("demo.deb"))
    @patch("lpm.cli.unpublish", return_value=Submission("https://example.com/pr/1", "demo", "1.2.3"))
    def test_single_architecture_commands_use_target_and_reject_all(self, unpublish, deploy) -> None:
        for command, options, handler in (
            ("deploy", ["--target", "lab-device"], deploy),
            ("unpublish", ["1.2.3", "--reason", "broken"], unpublish),
        ):
            for target, architecture in (("cm0", "arm64"), ("k230", "riscv64")):
                with self.subTest(command=command, target=target):
                    save_target(self.project, target)
                    self.assertEqual(self.invoke(command, *options), 0)
                    self.assertEqual(handler.call_args.kwargs["architecture"], architecture)
            save_target(self.project, "all")
            handler.reset_mock()
            self.assertEqual(self.invoke(command, *options), 1)
            handler.assert_not_called()
            self.assertEqual(self.invoke(command, *options, "--arch", "riscv64"), 0)
            self.assertEqual(handler.call_args.kwargs["architecture"], "riscv64")
        self.assertEqual(deploy.call_args.kwargs["target"], "lab-device")
        self.assertIn("requires one architecture", self.errors.getvalue())
        self.assertEqual(self.invoke("deploy", "explicit.deb"), 0)
        self.assertIsNone(deploy.call_args.kwargs["architecture"])

    @patch("lpm.commands.run")
    @patch("lpm.commands.require_tool")
    @patch("lpm.registry.inspect_debian_package")
    def test_deploy_selects_saved_platform_by_control_metadata(self, inspect, require_tool, run) -> None:
        output = self.root / "dist"
        output.mkdir()
        selected = output / "older.deb"
        newest = output / "newer.deb"
        for path, modified in ((selected, 10), (newest, 20)):
            path.write_bytes(b"fixture")
            os.utime(path, (modified, modified))
        artifacts = {
            selected: DebianArtifact(selected, "lilygo-ui-demo", "1.2.3", "riscv64", 7, "d" * 64),
            newest: DebianArtifact(newest, "lilygo-ui-demo", "1.2.3", "arm64", 7, "e" * 64),
        }
        inspect.side_effect = artifacts.__getitem__
        save_target(self.project, "k230")
        self.assertEqual(self.invoke("deploy", "--target", "k230-lab"), 0)
        self.assertIn(selected, run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][-2], "k230-lab")

    @patch("lpm.registry.GitHubClient.authenticated")
    @patch("lpm.registry.inspect_debian_package")
    def test_publish_all_submits_both_saved_target_architectures_in_one_pr(self, inspect, authenticated) -> None:
        output = self.root / "dist"
        output.mkdir()
        artifacts = {}
        for arch, digit in (("arm64", "d"), ("riscv64", "e")):
            path = output / f"{arch}.deb"
            path.write_bytes(b"fixture")
            artifacts[path] = DebianArtifact(path, "lilygo-ui-demo", "1.2.3", arch, 7, digit * 64)
        inspect.side_effect = artifacts.__getitem__
        client = FakeGitHub()
        authenticated.return_value = client
        save_target(self.project, "all")
        self.assertEqual(self.invoke("publish"), 0, self.errors.getvalue())
        self.assertEqual(len(client.commits), 1)
        self.assertEqual(len(client.pull_requests), 1)
        for arch in ("arm64", "riscv64"):
            self.assertIn(f"apps/lilygo-ui-demo/releases/1.2.3_{arch}.json", client.commits[0]["files"])

    @patch("lpm.cli.start")
    @patch("lpm.cli.test")
    def test_host_commands_ignore_saved_target(self, test, start) -> None:
        save_target(self.project, "all")
        self.assertEqual(self.invoke("start", "--no-build"), 0)
        self.assertEqual(self.invoke("test"), 0)
        self.assertIsNone(start.call_args.kwargs["preset"])
        self.assertIsNone(test.call_args.kwargs["preset"])

    @patch("lpm.commands.pack_script", return_value=[])
    def test_script_backend_keeps_its_own_platform_configuration(self, pack_script) -> None:
        self.config_path.write_text(
            '[project]\nkind = "runtime"\npackage = "demo-runtime"\n'
            '[build]\nbackend = "script"\n', encoding="utf-8",
        )
        save_target(self.project, "all")
        self.assertEqual(self.invoke("pack"), 0)
        pack_script.assert_called_once()
        self.assertEqual(self.invoke("pack", "--arch", "arm64"), 1)


if __name__ == "__main__":
    unittest.main()
