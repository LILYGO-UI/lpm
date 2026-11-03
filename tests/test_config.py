# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lpm.config import (
    Project,
    find_project,
    load_toml,
    pascal_case,
    render_config,
    validate_slug,
    validate_version,
)
from lpm.errors import LpmError


def sample_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "project": {
            "package": "lilygo-ui-weather-station",
            "version": "1.2.3",
            "app_id": "cc.lilygo.ui.WeatherStation",
            "title": "Weather Station",
            "summary": 'Shows "local" weather',
            "description": 'Shows "local"\tweather\nnow',
            "license": "MIT",
            "source_repo": "https://example.com/weather-station",
            "homepage": "https://example.com/weather-station",
            "categories": ["Utilities"],
            "authors": [{"name": "Tester", "github": "tester"}],
            "assets": {
                "icon": "assets/app-icon.png",
                "screenshots": [
                    {"path": "assets/preview.png", "caption": "Main screen"}
                ],
            },
            "permissions": [
                {"id": "network", "reason": "Fetch weather data"}
            ],
            "compatibility": {
                "architectures": ["arm64"],
                "min_appkit_version": "0.1.0",
            },
            "launcher": {"order": 50},
        },
        "build": {
            "host_preset": "host-simulator",
            "cross_preset": "cm0-cross",
            "package_dir": "dist",
        },
        "deploy": {"host": "raspberrypi.local", "user": "pi", "port": 22},
    }


class ConfigTests(unittest.TestCase):
    def test_default_project_kind_and_backend_preserve_plain_cmake_projects(self) -> None:
        project = Project(Path("/project"), {})
        self.assertEqual(project.kind, "application")
        self.assertEqual(project.build_backend, "cmake")

    def test_explicit_application_kind_round_trip(self) -> None:
        expected = sample_config()
        expected["project"]["kind"] = "application"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lpm.toml"
            path.write_text(render_config(expected), encoding="utf-8")
            self.assertEqual(load_toml(path), expected)

    def test_runtime_requires_only_its_package_identity(self) -> None:
        project = Project(Path("/project"), {"project": {"kind": "runtime", "package": "meshtasticd"}})
        self.assertEqual(project.kind, "runtime")
        for package in (
            None,
            "Mesa-VC4",
            "lilygo-ui-vc4_26.2",
            "-meshtasticd",
            ".meshtasticd",
            "lilygo-ui-",
        ):
            project.data["project"]["package"] = package
            with self.subTest(package=package), self.assertRaisesRegex(LpmError, "project.package"):
                project.kind

    def test_runtime_config_round_trip(self) -> None:
        expected = {
            "schema_version": 1,
            "project": {"kind": "runtime", "package": "meshtasticd"},
            "build": {
                "backend": "script",
                "package_dir": "dist",
                "test_command": ["{python}", "tools/lpm-workflow.py", "test", "{report}"],
                "pack_command": ["{python}", "tools/lpm-workflow.py", "pack", "{output}"],
            },
            "deploy": {"host": "raspberrypi.local", "user": "pi", "port": 22},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lpm.toml"
            path.write_text(render_config(expected), encoding="utf-8")
            self.assertEqual(load_toml(path), expected)

    def test_runtime_rejects_application_metadata_and_duplicate_version(self) -> None:
        for key in ("app_id", "assets", "version"):
            project = Project(Path("/project"), {"project": {"kind": "runtime", "package": "meshtasticd", key: "unused"}})
            with self.subTest(key=key), self.assertRaisesRegex(LpmError, f"unsupported fields: {key}"):
                project.kind

    def test_config_round_trip(self) -> None:
        expected = sample_config()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lpm.toml"
            path.write_text(render_config(expected), encoding="utf-8")
            data = load_toml(path)
        self.assertEqual(data, expected)

    def test_run_configuration_validates_and_round_trips(self) -> None:
        expected = sample_config()
        expected["run"] = {
            "prepare_command": ["{python}", "tools/prepare.py", "{project}"],
            "environment": {"CLASH_API_URL": "http://127.0.0.1:19090"},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lpm.toml"
            path.write_text(render_config(expected), encoding="utf-8")
            project = Project(Path(temp), load_toml(path))
        self.assertEqual(project.run_prepare_command(), expected["run"]["prepare_command"])
        self.assertEqual(project.run_environment(), expected["run"]["environment"])

    def test_run_configuration_rejects_invalid_values(self) -> None:
        cases = (
            ({"prepare_command": []}, "run.prepare_command"),
            ({"prepare_command": ["python", 1]}, "run.prepare_command"),
            ({"environment": "CLASH_DEMO=0"}, "run.environment"),
            ({"environment": {"BAD=KEY": "value"}}, "run.environment"),
            ({"unexpected": True}, "unsupported fields"),
        )
        for run_config, message in cases:
            with self.subTest(run_config=run_config):
                project = Project(Path("/project"), {"run": run_config})
                with self.assertRaisesRegex(LpmError, message):
                    if "environment" in run_config:
                        project.run_environment()
                    else:
                        project.run_prepare_command()

    def test_find_project_from_child_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "lpm.toml").write_text(
                '[project]\npackage = "lilygo-ui-demo"\n', encoding="utf-8"
            )
            child = root / "src" / "nested"
            child.mkdir(parents=True)
            project = find_project(child)
        self.assertEqual(project.root, root.resolve())
        self.assertEqual(project.get("project", "package"), "lilygo-ui-demo")

    def test_find_plain_cmake_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "CMakeLists.txt").touch()
            project = find_project(root)
        self.assertEqual(project.root, root.resolve())
        self.assertEqual(project.data, {})

    def test_name_and_version_validation(self) -> None:
        self.assertEqual(validate_slug("weather-station"), "weather-station")
        self.assertEqual(pascal_case("weather-station"), "WeatherStation")
        self.assertEqual(validate_version("2.0.1-rc.1"), "2.0.1-rc.1")
        for invalid in ("Weather Station", "-weather", "weather_"):
            with self.subTest(invalid=invalid), self.assertRaises(LpmError):
                validate_slug(invalid)
        for invalid in ("1", "v1.0", "01.2.0"):
            with self.subTest(invalid=invalid), self.assertRaises(LpmError):
                validate_version(invalid)


if __name__ == "__main__":
    unittest.main()
