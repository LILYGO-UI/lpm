# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lpm.config import Project
from lpm.errors import LpmError
from lpm.metadata import (
    build_publish_manifest,
    render_cmake_metadata,
    render_publish_json,
)


def make_project(root: Path) -> Project:
    (root / "assets").mkdir()
    (root / "assets" / "app-icon.png").touch()
    (root / "assets" / "preview.png").touch()
    return Project(
        root,
        {
            "schema_version": 1,
            "project": {
                "package": "lilygo-ui-demo",
                "version": "1.2.3",
                "app_id": "cc.lilygo.ui.Demo",
                "title": "Demo",
                "summary": "Demo application",
                "description": "A demonstration application.",
                "authors": [{"name": "Tester", "email": "test@example.com"}],
                "categories": ["Development", "Utilities"],
                "license": "MIT",
                "source_repo": "https://example.com/demo",
                "homepage": "https://example.com/demo",
                "assets": {
                    "icon": "assets/app-icon.png",
                    "screenshots": [
                        {"path": "assets/preview.png", "caption": "Main screen"}
                    ],
                },
                "permissions": [
                    {
                        "id": "filesystem",
                        "access": "read-write",
                        "scope": "app-data",
                        "reason": "Store application state",
                    }
                ],
                "compatibility": {
                    "architectures": ["arm64"],
                    "min_appkit_version": "0.1.0",
                },
                "launcher": {"order": 90},
            },
            "build": {"host_preset": "secret-build-preset"},
            "deploy": {
                "host": "private-device.local",
                "user": "private-user",
                "port": 2222,
            },
        },
    )


class MetadataTests(unittest.TestCase):
    def test_project_metadata_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Project(Path(temp), {"schema_version": 1})
            with self.assertRaisesRegex(LpmError, "project must be a table"):
                build_publish_manifest(project)

    def test_publish_manifest_contains_only_publish_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(Path(temp))
            payload = build_publish_manifest(project)
            rendered = render_publish_json(project)
        self.assertNotIn("build", payload)
        self.assertNotIn("deploy", payload)
        self.assertNotIn("private-device.local", rendered)
        self.assertNotIn("private-user", rendered)
        self.assertNotIn("secret-build-preset", rendered)
        self.assertEqual(json.loads(rendered), payload)

    def test_missing_asset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(Path(temp))
            project.data["project"]["assets"]["icon"] = "assets/missing.png"
            with self.assertRaisesRegex(LpmError, "asset does not exist"):
                build_publish_manifest(project)

    def test_invalid_filesystem_permission_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(Path(temp))
            project.data["project"]["permissions"][0]["access"] = "execute"
            with self.assertRaisesRegex(LpmError, "valid access and scope"):
                build_publish_manifest(project)

    def test_unknown_project_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(Path(temp))
            project.data["project"]["deploy_host"] = "device.local"
            with self.assertRaisesRegex(LpmError, "unsupported fields: deploy_host"):
                build_publish_manifest(project)

    def test_unknown_nested_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(Path(temp))
            project.data["project"]["launcher"]["label"] = "Demo"
            with self.assertRaisesRegex(LpmError, "unsupported fields: label"):
                build_publish_manifest(project)

    def test_cmake_metadata_contains_derived_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            content = render_cmake_metadata(make_project(Path(temp)))
        self.assertIn("set(LILYGO_UI_PACKAGE_NAME [[lilygo-ui-demo]])", content)
        self.assertIn("set(LILYGO_UI_VERSION [[1.2.3]])", content)
        self.assertIn("set(LILYGO_UI_CMAKE_PROJECT_NAME [[lilygo_ui_demo]])", content)
        self.assertIn("set(LILYGO_UI_CATEGORIES [[Development]] [[Utilities]])", content)


if __name__ == "__main__":
    unittest.main()
