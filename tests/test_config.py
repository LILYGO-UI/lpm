# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lpm.config import (
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
    def test_config_round_trip(self) -> None:
        expected = sample_config()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lpm.toml"
            path.write_text(render_config(expected), encoding="utf-8")
            data = load_toml(path)
        self.assertEqual(data, expected)

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
