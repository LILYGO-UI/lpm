# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from lpm.commands import (
    deploy,
    initialize,
    pack,
    resolve_executable,
    start,
    stop,
    test,
)
from lpm.config import Project, load_toml, render_config
from lpm.errors import CommandError, LpmError
from lpm.metadata import build_publish_manifest


def make_template(root: Path) -> Path:
    template = root / "template"
    template.mkdir()
    (template / "assets" / "previews").mkdir(parents=True)
    (template / "assets" / "app-icon.png").touch()
    (template / "assets" / "previews" / "preview.png").touch()
    config = {
        "schema_version": 1,
        "project": {
            "package": "lilygo-ui-template",
            "version": "0.1.0",
            "app_id": "cc.lilygo.ui.Template",
            "title": "Template",
            "summary": "Starter",
            "description": "Starter",
            "license": "MIT",
            "source_repo": "https://example.com/template",
            "homepage": "https://example.com/template",
            "categories": ["Development"],
            "authors": [{"name": "Tester", "email": "test@example.com"}],
            "assets": {
                "icon": "assets/app-icon.png",
                "screenshots": [
                    {"path": "assets/previews/preview.png", "caption": "Preview"}
                ],
            },
            "permissions": [],
            "compatibility": {
                "architectures": ["arm64"],
                "min_appkit_version": "0.1.0",
            },
            "launcher": {"order": 90},
        },
        "build": {
            "host_preset": "host-simulator",
            "cross_preset": "cm0-cross",
            "package_dir": "dist",
        },
        "deploy": {"host": "template.local", "user": "owner", "port": 2222},
    }
    (template / "lpm.toml").write_text(render_config(config), encoding="utf-8")
    (template / "CMakeLists.txt").write_text("project(lilygo_ui_app_template)\n", encoding="utf-8")
    (template / "src").mkdir()
    (template / "src" / "main.cpp").touch()
    (template / "build").mkdir()
    (template / "build" / "ignored").touch()
    return template


class InitializeTests(unittest.TestCase):
    def test_initialize_local_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = make_template(root)
            target = root / "my-app"
            result = initialize(
                target,
                name="weather-station",
                version="1.2.3",
                description='Weather \\ "display" ${HOME}\nnow',
                template=str(template),
            )
            config = load_toml(target / "lpm.toml")
            manifest = build_publish_manifest(Project(target, config))
        self.assertEqual(result, target.resolve())
        self.assertEqual(manifest["package"], "lilygo-ui-weather-station")
        self.assertEqual(manifest["version"], "1.2.3")
        self.assertEqual(manifest["app_id"], "cc.lilygo.ui.WeatherStation")
        self.assertEqual(manifest["title"], "WeatherStation")
        self.assertEqual(manifest["summary"], 'Weather \\ "display" ${HOME}\nnow')
        self.assertEqual(manifest["description"], 'Weather \\ "display" ${HOME}\nnow')
        self.assertEqual(config["deploy"]["host"], "raspberrypi.local")
        self.assertFalse((target / "publish.json").exists())
        self.assertFalse((target / "build").exists())

    def test_initialize_refuses_nonempty_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = make_template(root)
            target = root / "target"
            target.mkdir()
            (target / "owned.txt").touch()
            with self.assertRaisesRegex(LpmError, "not empty"):
                initialize(target, name="demo", version="0.1.0", description="", template=str(template))


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project(
            self.root,
            {
                "project": {
                    "package": "lilygo-ui-demo",
                    "executable": "lilygo-ui-legacy",
                },
                "build": {
                    "host_preset": "host-simulator",
                    "cross_preset": "cm0-cross",
                    "package_dir": "dist",
                },
                "deploy": {"host": "cm0.local", "user": "pi", "port": 2222},
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_resolve_configured_executable(self) -> None:
        binary = self.root / "build" / "host-simulator" / "lilygo-ui-demo"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        self.assertEqual(resolve_executable(self.project, preset="host-simulator", executable=None), binary.resolve())

    def test_resolve_project_package_executable(self) -> None:
        binary = self.root / "build" / "host-simulator" / "lilygo-ui-demo"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        self.assertEqual(
            resolve_executable(self.project, preset="host-simulator", executable=None),
            binary.resolve(),
        )

    def test_project_package_takes_precedence_over_legacy_executable(self) -> None:
        binary = self.root / "build" / "host-simulator" / "lilygo-ui-demo"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        self.assertEqual(
            resolve_executable(self.project, preset="host-simulator", executable=None),
            binary.resolve(),
        )

    def test_explicit_executable_does_not_require_project_metadata(self) -> None:
        binary = self.root / "build" / "host-simulator" / "custom-simulator"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        project = Project(self.root, {})
        self.assertEqual(
            resolve_executable(
                project, preset="host-simulator", executable="custom-simulator"
            ),
            binary.resolve(),
        )

    def test_default_executable_requires_project_identity(self) -> None:
        project = Project(self.root, {})
        with self.assertRaisesRegex(LpmError, "project.package"):
            resolve_executable(project, preset="host-simulator", executable=None)

    @patch("lpm.commands.time.sleep")
    @patch("lpm.commands.spawn")
    @patch("lpm.commands.resolve_executable")
    @patch("lpm.commands.configure_and_build")
    def test_start_records_background_process(self, build: Mock, resolve: Mock, spawn_mock: Mock, sleep: Mock) -> None:
        binary = self.root / "demo"
        resolve.return_value = binary
        process = Mock(pid=1234)
        process.poll.return_value = None
        spawn_mock.return_value = process
        log_path = start(self.project)
        state = json.loads((self.root / ".lpm" / "simulator.json").read_text(encoding="utf-8"))
        build.assert_called_once_with(self.project, "host-simulator")
        self.assertEqual(state["pid"], 1234)
        self.assertEqual(log_path, self.root / ".lpm" / "simulator.log")

    @patch("lpm.commands.run")
    @patch("lpm.commands.resolve_executable")
    @patch("lpm.commands.configure_and_build")
    def test_start_runs_preparation_and_passes_environment_to_foreground_ui(
        self, build: Mock, resolve: Mock, run_mock: Mock
    ) -> None:
        binary = self.root / "demo"
        resolve.return_value = binary
        run_mock.side_effect = [Mock(returncode=0), Mock(returncode=0)]
        self.project.data["run"] = {
            "prepare_command": ["{python}", "tools/prepare.py", "{project}"],
            "environment": {"LPM_TEST_DEFAULT": "configured", "LPM_TEST_SHELL": "configured"},
        }
        with patch.dict(os.environ, {"LPM_TEST_SHELL": "shell"}, clear=False):
            result = start(self.project, foreground=True, arguments=["--orientation", "landscape"])
        self.assertEqual(result, 0)
        build.assert_called_once_with(self.project, "host-simulator")
        self.assertEqual(run_mock.call_count, 2)
        prepare_command = run_mock.call_args_list[0].args[0]
        self.assertEqual(prepare_command[0], sys.executable)
        self.assertEqual(prepare_command[1:], ["tools/prepare.py", str(self.root.resolve())])
        self.assertEqual(run_mock.call_args_list[0].kwargs["env"]["LPM_TEST_DEFAULT"], "configured")
        self.assertEqual(run_mock.call_args_list[0].kwargs["env"]["LPM_TEST_SHELL"], "shell")
        self.assertEqual(run_mock.call_args_list[1].args[0], [str(binary), "--orientation", "landscape"])
        self.assertIs(run_mock.call_args_list[0].kwargs["env"], run_mock.call_args_list[1].kwargs["env"])

    @patch("lpm.commands.time.sleep")
    @patch("lpm.commands.spawn")
    @patch("lpm.commands.resolve_executable")
    @patch("lpm.commands.configure_and_build")
    @patch("lpm.commands.run")
    def test_start_runs_preparation_before_background_ui_with_environment(
        self, run_mock: Mock, build: Mock, resolve: Mock, spawn_mock: Mock, sleep: Mock
    ) -> None:
        binary = self.root / "demo"
        resolve.return_value = binary
        process = Mock(pid=1235)
        process.poll.return_value = None
        spawn_mock.return_value = process
        self.project.data["run"] = {
            "prepare_command": ["prepare", "{project}"],
            "environment": {
                "LPM_TEST_DEFAULT": "configured",
                "LPM_TEST_PROJECT": "{project}/.cache",
            },
        }
        start(self.project)
        build.assert_called_once_with(self.project, "host-simulator")
        run_mock.assert_called_once_with(
            ["prepare", str(self.root.resolve())],
            cwd=self.root,
            env=spawn_mock.call_args.kwargs["env"],
        )
        spawn_mock.assert_called_once()
        self.assertEqual(spawn_mock.call_args.args[0], [str(binary)])
        self.assertEqual(spawn_mock.call_args.kwargs["env"]["LPM_TEST_DEFAULT"], "configured")
        self.assertEqual(spawn_mock.call_args.kwargs["env"]["LPM_TEST_PROJECT"], f"{self.root.resolve()}/.cache")

    @patch("lpm.commands.resolve_executable")
    @patch("lpm.commands.configure_and_build")
    def test_start_does_not_launch_ui_when_preparation_fails(
        self, build: Mock, resolve: Mock
    ) -> None:
        self.project.data["run"] = {"prepare_command": ["prepare"]}
        with patch("lpm.commands.run", side_effect=CommandError(["prepare"], 7)) as run_mock:
            with self.assertRaises(CommandError):
                start(self.project)
        run_mock.assert_called_once_with(["prepare"], cwd=self.root)
        resolve.assert_called_once()

    @patch("lpm.commands.time.sleep")
    @patch("lpm.commands.spawn")
    @patch("lpm.commands.resolve_executable")
    @patch("lpm.commands.is_running", return_value=True)
    def test_start_replaces_corrupt_state(
        self, running: Mock, resolve: Mock, spawn_mock: Mock, sleep: Mock
    ) -> None:
        self.project.state_dir.mkdir()
        (self.project.state_dir / "simulator.json").write_text(
            '{"pid": "not-a-number"}\n', encoding="utf-8"
        )
        resolve.return_value = self.root / "demo"
        process = Mock(pid=4321)
        process.poll.return_value = None
        spawn_mock.return_value = process
        start(self.project, build=False)
        state = json.loads((self.project.state_dir / "simulator.json").read_text(encoding="utf-8"))
        self.assertEqual(state["pid"], 4321)

    @patch("lpm.commands.stop_process_group", return_value=True)
    @patch("lpm.commands.process_matches", return_value=True)
    @patch("lpm.commands.is_running", return_value=True)
    def test_stop_only_signals_matching_process(self, running: Mock, matches: Mock, stop_group: Mock) -> None:
        self.project.state_dir.mkdir()
        state_path = self.project.state_dir / "simulator.json"
        state_path.write_text(
            json.dumps({"pid": 42, "executable": str(self.root / "demo"), "project": str(self.root)}),
            encoding="utf-8",
        )
        self.assertTrue(stop(self.project, timeout=1.5))
        stop_group.assert_called_once_with(42, 1.5)
        self.assertFalse(state_path.exists())

    @patch("lpm.commands.stop_process_group")
    @patch("lpm.commands.process_matches", return_value=False)
    @patch("lpm.commands.is_running", return_value=True)
    def test_stop_refuses_reused_pid(self, running: Mock, matches: Mock, stop_group: Mock) -> None:
        self.project.state_dir.mkdir()
        (self.project.state_dir / "simulator.json").write_text(
            json.dumps({"pid": 42, "executable": str(self.root / "demo"), "project": str(self.root)}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(LpmError, "refusing"):
            stop(self.project)
        stop_group.assert_not_called()

    @patch("lpm.commands.run")
    @patch("lpm.commands.require_tool")
    @patch("lpm.commands.configure_and_build")
    def test_test_builds_and_writes_junit(self, build: Mock, tool: Mock, run_mock: Mock) -> None:
        report = test(self.project)
        self.assertEqual(report, (self.root / ".lpm" / "test-results.xml").resolve())
        build.assert_called_once_with(self.project, "host-simulator")
        run_mock.assert_called_once_with(
            ["ctest", "--preset", "host-simulator", "--output-on-failure", "--output-junit", report],
            cwd=self.root,
        )

    @patch("lpm.commands.require_tool")
    @patch("lpm.commands.configure_and_build")
    def test_pack_returns_generated_deb(self, build: Mock, tool: Mock) -> None:
        config = self.root / "build" / "cm0-cross" / "CPackConfig.cmake"
        config.parent.mkdir(parents=True)
        config.touch()

        def fake_run(command: list[object], *, cwd: Path) -> Mock:
            output = Path(command[-1])
            (output / "lilygo-ui-demo.deb").touch()
            staging = output / "_CPack_Packages" / "Linux" / "DEB"
            staging.mkdir(parents=True)
            (staging / "lilygo-ui-demo.deb").touch()
            return Mock(returncode=0)

        with patch("lpm.commands.run", side_effect=fake_run) as run_mock:
            packages = pack(self.project)
        self.assertEqual(packages, [(self.root / "dist" / "lilygo-ui-demo.deb").resolve()])
        run_mock.assert_called_once_with(
            ["cpack", "--config", config, "-B", (self.root / "dist").resolve()],
            cwd=self.root,
        )

    @patch("lpm.commands.require_tool")
    @patch("lpm.commands.run")
    def test_deploy_uses_config_and_installs_package(self, run_mock: Mock, tool: Mock) -> None:
        package = self.root / "dist" / "demo package.deb"
        package.parent.mkdir()
        package.touch()
        result = deploy(self.project, package=package)
        self.assertEqual(result, package.resolve())
        package_id = hashlib.sha256(str(package.resolve()).encode("utf-8")).hexdigest()[:12]
        remote_path = f"/tmp/lpm-{package_id}.deb"
        self.assertEqual(
            run_mock.call_args_list[0],
            call(
                ["scp", "-P", "2222", package.resolve(), f"pi@cm0.local:{remote_path}"],
                cwd=self.root,
            ),
        )
        remote_command = run_mock.call_args_list[1].args[0][-1]
        self.assertIn(f"sudo -- apt-get install --reinstall --yes {remote_path}", remote_command)
        self.assertIn(f"rm -f -- {remote_path}", remote_command)

    @patch("lpm.commands.require_tool")
    @patch("lpm.commands.run")
    def test_deploy_no_sudo_does_not_allocate_tty(self, run_mock: Mock, tool: Mock) -> None:
        package = self.root / "dist" / "demo.deb"
        package.parent.mkdir()
        package.touch()

        deploy(self.project, package=package, sudo=False)

        ssh_command = run_mock.call_args_list[1].args[0]
        self.assertNotIn("-tt", ssh_command)
        self.assertIn("apt-get install --reinstall --yes", ssh_command[-1])

    @patch("lpm.commands.require_tool")
    @patch("lpm.commands.run")
    def test_deploy_uses_default_remote(self, run_mock: Mock, tool: Mock) -> None:
        package = self.root / "demo.deb"
        package.touch()
        project = Project(self.root, {"deploy": {"host": "", "user": ""}})

        deploy(project, package=package)

        package_id = hashlib.sha256(str(package.resolve()).encode("utf-8")).hexdigest()[:12]
        remote_path = f"/tmp/lpm-{package_id}.deb"
        self.assertEqual(
            run_mock.call_args_list[0].args[0],
            ["scp", "-P", "22", package.resolve(), f"pi@raspberrypi.local:{remote_path}"],
        )
        self.assertEqual(
            run_mock.call_args_list[1].args[0][0:5],
            ["ssh", "-p", "22", "-tt", "pi@raspberrypi.local"],
        )

    @patch("lpm.commands.require_tool")
    @patch("lpm.commands.run")
    def test_deploy_target_uses_project_ssh_config(self, run_mock: Mock, tool: Mock) -> None:
        package = self.root / "dist" / "demo.deb"
        package.parent.mkdir()
        package.touch()
        self.project.state_dir.mkdir()
        ssh_config = self.project.state_dir / "ssh_config"
        ssh_config.write_text(
            "Host cm0-lab\n    HostName 192.168.1.42\n    User root\n    Port 2222\n",
            encoding="utf-8",
        )

        deploy(self.project, package=package, target="cm0-lab")

        package_id = hashlib.sha256(str(package.resolve()).encode("utf-8")).hexdigest()[:12]
        remote_path = f"/tmp/lpm-{package_id}.deb"
        self.assertEqual(
            run_mock.call_args_list[0],
            call(
                ["scp", "-F", ssh_config, package.resolve(), f"cm0-lab:{remote_path}"],
                cwd=self.root,
            ),
        )
        self.assertEqual(
            run_mock.call_args_list[1].args[0][:5],
            ["ssh", "-F", ssh_config, "-tt", "cm0-lab"],
        )

    @patch("lpm.commands.require_tool")
    @patch("lpm.commands.run")
    def test_deploy_target_allows_explicit_user_and_port(self, run_mock: Mock, tool: Mock) -> None:
        package = self.root / "demo.deb"
        package.touch()

        deploy(self.project, package=package, target="cm0-lab", user="admin", port=2200)

        self.assertEqual(run_mock.call_args_list[0].args[0][0:4], ["scp", "-P", "2200", package.resolve()])
        self.assertIn("admin@cm0-lab:", run_mock.call_args_list[0].args[0][-1])
        self.assertEqual(
            run_mock.call_args_list[1].args[0][0:5],
            ["ssh", "-p", "2200", "-tt", "admin@cm0-lab"],
        )

    def test_deploy_rejects_option_injection(self) -> None:
        with self.assertRaisesRegex(LpmError, "deploy host"):
            deploy(self.project, host="-oProxyCommand=bad")

    def test_deploy_rejects_invalid_configured_port(self) -> None:
        project = Project(self.root, {"deploy": {"host": "cm0.local", "port": "ssh"}})
        with self.assertRaisesRegex(LpmError, "must be an integer"):
            deploy(project)


if __name__ == "__main__":
    unittest.main()
