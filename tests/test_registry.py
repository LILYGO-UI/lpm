# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lpm.config import Project
from lpm.errors import LpmError
from lpm.registry import DebianArtifact, inspect_debian_package, publish, unpublish


def make_project(root: Path) -> Project:
    (root / "assets").mkdir()
    (root / "assets" / "icon.png").write_bytes(b"png icon")
    (root / "assets" / "preview.webp").write_bytes(b"webp preview")
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
                "authors": [{"name": "Alice", "github": "alice"}],
                "categories": ["Utilities"],
                "license": "MIT",
                "source_repo": "https://github.com/alice/demo",
                "homepage": "https://example.com/demo",
                "assets": {
                    "icon": "assets/icon.png",
                    "screenshots": [
                        {"path": "assets/preview.webp", "caption": "Main screen"}
                    ],
                },
                "permissions": [],
                "compatibility": {
                    "architectures": ["arm64"],
                    "min_appkit_version": "0.1.0",
                },
                "launcher": {"order": 50},
            },
            "build": {"package_dir": "dist"},
        },
    )


class FakeGitHub:
    def __init__(self) -> None:
        self.registry_files: dict[str, dict[str, object]] = {}
        self.commits: list[dict[str, object]] = []
        self.pull_requests: list[dict[str, str]] = []

    def current_user(self) -> str:
        return "alice"

    def branch_base(self, repository: str) -> tuple[str, str, str]:
        return "main", "a" * 40, "b" * 40

    def file_json(self, repository: str, path: str, *, ref: str):
        return self.registry_files.get(path)

    def ensure_fork(self, upstream: str, owner: str) -> str:
        return "alice/packages"

    def commit_files(self, repository: str, **kwargs: object) -> str:
        self.commits.append({"repository": repository, **kwargs})
        return "c" * 40

    def create_release(self, repository: str, **kwargs: object):
        return {"id": 12, "assets": [], **kwargs}

    def ensure_release_asset(
        self,
        repository: str,
        release: dict[str, object],
        *,
        path: Path,
        name: str,
        content_type: str,
    ) -> str:
        files = self.commits[-1]["files"]
        release_record = json.loads(files["apps/lilygo-ui-demo/releases/1.2.3.json"])
        return release_record["artifact"]["source"]["url"]

    def create_pull_request(self, upstream: str, **kwargs: str) -> str:
        self.pull_requests.append({"upstream": upstream, **kwargs})
        return "https://github.com/LILYGO-UI/packages/pull/42"


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = make_project(self.root)
        self.package = self.root / "dist" / "demo.deb"
        self.package.parent.mkdir()
        self.package.write_bytes(b"debian package")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_stages_release_and_opens_pr(self, inspect: Mock) -> None:
        inspect.return_value = DebianArtifact(
            self.package,
            "lilygo-ui-demo",
            "1.2.3",
            "arm64",
            self.package.stat().st_size,
            "d" * 64,
        )
        client = FakeGitHub()

        result = publish(self.project, package_path=self.package, client=client)

        self.assertEqual(
            result.pull_request_url, "https://github.com/LILYGO-UI/packages/pull/42"
        )
        files = client.commits[0]["files"]
        self.assertIn("apps/lilygo-ui-demo/app.json", files)
        release = json.loads(files["apps/lilygo-ui-demo/releases/1.2.3.json"])
        self.assertEqual(release["submitted_by"], "alice")
        self.assertEqual(release["artifact"]["sha256"], "d" * 64)
        self.assertEqual(
            release["artifact"]["central"]["tag"], "apt-pool-lilygo-ui-demo"
        )
        icon = release["manifest"]["assets"]["icon"]
        self.assertRegex(icon, r"^assets/sha256/[a-f0-9]{2}/[a-f0-9]{64}\.png$")
        self.assertIn(icon, files)

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_rejects_debian_identity_mismatch(self, inspect: Mock) -> None:
        inspect.return_value = DebianArtifact(
            self.package, "other", "1.2.3", "arm64", 1, "d" * 64
        )
        with self.assertRaisesRegex(LpmError, "Debian Package"):
            publish(self.project, package_path=self.package, client=FakeGitHub())

    def test_unpublish_only_changes_release_state(self) -> None:
        client = FakeGitHub()
        client.registry_files["apps/lilygo-ui-demo/app.json"] = {
            "package": "lilygo-ui-demo",
            "app_id": "cc.lilygo.ui.Demo",
            "owners": ["alice"],
        }
        original = {
            "status": "published",
            "submitted_by": "alice",
            "manifest": {"version": "1.2.3"},
            "artifact": {"sha256": "d" * 64},
        }
        client.registry_files["apps/lilygo-ui-demo/releases/1.2.3.json"] = original

        result = unpublish(
            self.project,
            version="1.2.3",
            reason="Critical startup failure",
            client=client,
        )

        self.assertEqual(result.version, "1.2.3")
        files = client.commits[0]["files"]
        changed = json.loads(files["apps/lilygo-ui-demo/releases/1.2.3.json"])
        self.assertEqual(changed["status"], "yanked")
        self.assertEqual(changed["yank"]["requested_by"], "alice")
        self.assertEqual(changed["artifact"], original["artifact"])

    @patch("lpm.registry.require_tool")
    @patch("lpm.registry.run")
    def test_inspect_debian_package(self, run_mock: Mock, require: Mock) -> None:
        run_mock.side_effect = [
            Mock(stdout="lilygo-ui-demo\n", returncode=0),
            Mock(stdout="1.2.3\n", returncode=0),
            Mock(stdout="arm64\n", returncode=0),
        ]
        artifact = inspect_debian_package(self.package)
        self.assertEqual(artifact.package, "lilygo-ui-demo")
        self.assertEqual(artifact.version, "1.2.3")
        self.assertEqual(artifact.architecture, "arm64")
        self.assertEqual(len(artifact.sha256), 64)
        self.assertEqual(
            [call.args[0][-1] for call in run_mock.call_args_list],
            ["Package", "Version", "Architecture"],
        )


if __name__ == "__main__":
    unittest.main()
