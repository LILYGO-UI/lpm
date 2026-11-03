# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import copy
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
        self.file_reads: list[tuple[str, str]] = []
        self.commits: list[dict[str, object]] = []
        self.pull_requests: list[dict[str, str]] = []

    def current_user(self) -> str:
        return "alice"

    def branch_base(self, repository: str) -> tuple[str, str, str]:
        return "main", "a" * 40, "b" * 40

    def file_json(self, repository: str, path: str, *, ref: str):
        self.file_reads.append((path, ref))
        return self.registry_files.get(path)

    def ensure_fork(self, upstream: str, owner: str) -> str:
        return "alice/packages"

    def sync_fork(self, repository: str, *, branch: str) -> None:
        return None

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
        release_path = next(
            path for path in files
            if "/releases/" in path and path.endswith(".json")
        )
        release_record = json.loads(files[release_path])
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

    def existing_release(self, client: FakeGitHub, *, status: str = "yanked") -> dict:
        client.registry_files["apps/lilygo-ui-demo/app.json"] = {
            "package": "lilygo-ui-demo",
            "app_id": "cc.lilygo.ui.Demo",
            "owners": ["alice"],
        }
        release = {
            "status": status,
            "submitted_by": "alice",
            "manifest": {
                "package": "lilygo-ui-demo",
                "version": "1.2.3",
                "app_id": "cc.lilygo.ui.Demo",
            },
            "artifact": {"architecture": "arm64", "sha256": "a" * 64},
        }
        if status == "yanked":
            release["yank"] = {"reason": "Broken startup", "requested_by": "alice"}
        client.registry_files["apps/lilygo-ui-demo/releases/1.2.3.json"] = release
        return release

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

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_preserves_other_debian_architectures(self, inspect: Mock) -> None:
        for architecture in ("all", "amd64", "armhf", "riscv64", "hurd-i386"):
            with self.subTest(architecture=architecture):
                self.project.data["project"]["compatibility"]["architectures"] = [architecture]
                inspect.return_value = DebianArtifact(
                    self.package, "lilygo-ui-demo", "1.2.3", architecture,
                    self.package.stat().st_size, "d" * 64,
                )
                client = FakeGitHub()
                result = publish(self.project, package_path=self.package, client=client)
                release = json.loads(
                    client.commits[0]["files"]["apps/lilygo-ui-demo/releases/1.2.3.json"]
                )
                self.assertEqual(release["artifact"]["architecture"], architecture)
                self.assertEqual(
                    release["manifest"]["compatibility"]["architectures"], [architecture]
                )
                self.assertEqual(result.source_url, release["artifact"]["source"]["url"])
                self.assertEqual(len(client.pull_requests), 1)

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_rejects_architecture_mismatch_before_github(self, inspect: Mock) -> None:
        for declared, actual in (("arm64", "all"), ("all", "arm64"), ("amd64", "armhf")):
            with self.subTest(declared=declared, actual=actual):
                self.project.data["project"]["compatibility"]["architectures"] = [declared]
                inspect.return_value = DebianArtifact(
                    self.package, "lilygo-ui-demo", "1.2.3", actual, 1, "d" * 64
                )
                client = Mock()
                with self.assertRaisesRegex(LpmError, "Debian Architecture"):
                    publish(self.project, package_path=self.package, client=client)
                client.current_user.assert_not_called()

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_cannot_replace_existing_version_with_another_architecture(self, inspect: Mock) -> None:
        self.project.data["project"]["compatibility"]["architectures"] = ["amd64"]
        inspect.return_value = DebianArtifact(
            self.package, "lilygo-ui-demo", "1.2.3", "amd64", 1, "d" * 64
        )
        client = FakeGitHub()
        client.registry_files["apps/lilygo-ui-demo/releases/1.2.3.json"] = {
            "artifact": {"architecture": "arm64"},
        }
        with self.assertRaisesRegex(LpmError, "already exists"):
            publish(self.project, package_path=self.package, client=client)
        self.assertEqual(client.commits, [])
        self.assertEqual(client.pull_requests, [])

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_force_replaces_yanked_package_at_same_version(self, inspect: Mock) -> None:
        inspect.return_value = DebianArtifact(
            self.package, "lilygo-ui-demo", "1.2.3", "arm64",
            self.package.stat().st_size, "d" * 64,
        )
        client = FakeGitHub()
        previous = self.existing_release(client)
        previous["submitted_by"] = "bob"
        client.registry_files["apps/lilygo-ui-demo/app.json"]["owners"] = ["alice", "bob"]
        snapshot = copy.deepcopy(previous)

        result = publish(self.project, package_path=self.package, force=True, client=client)

        files = client.commits[0]["files"]
        release = json.loads(files["apps/lilygo-ui-demo/releases/1.2.3.json"])
        self.assertEqual(result.version, "1.2.3")
        self.assertEqual(release["status"], "published")
        self.assertNotIn("yank", release)
        self.assertEqual(release["submitted_by"], "alice")
        self.assertEqual(release["manifest"]["version"], "1.2.3")
        self.assertEqual(release["artifact"]["sha256"], "d" * 64)
        self.assertEqual(release["artifact"]["size"], self.package.stat().st_size)
        self.assertEqual(release["artifact"]["architecture"], "arm64")
        self.assertEqual(
            release["artifact"]["central"]["url"],
            "https://github.com/LILYGO-UI/packages/releases/download/"
            f"apt-pool-lilygo-ui-demo/sha256-{'d' * 64}.deb",
        )
        self.assertEqual(result.source_url, release["artifact"]["source"]["url"])
        self.assertIn(release["manifest"]["assets"]["icon"], files)
        self.assertNotIn("apps/lilygo-ui-demo/app.json", files)
        self.assertEqual(previous, snapshot)
        self.assertEqual(
            client.file_reads,
            [
                ("apps/lilygo-ui-demo/app.json", "a" * 40),
                ("apps/lilygo-ui-demo/releases/1.2.3.json", "a" * 40),
            ],
        )
        self.assertEqual(client.pull_requests[0]["title"], "Republish lilygo-ui-demo 1.2.3")
        self.assertIn("a" * 64, client.pull_requests[0]["body"])
        self.assertIn("d" * 64, client.pull_requests[0]["body"])

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_yanked_version_requires_force(self, inspect: Mock) -> None:
        inspect.return_value = DebianArtifact(
            self.package, "lilygo-ui-demo", "1.2.3", "arm64", 1, "d" * 64,
        )
        client = FakeGitHub()
        self.existing_release(client)
        with self.assertRaisesRegex(LpmError, "already exists.*--force"):
            publish(self.project, package_path=self.package, client=client)
        self.assertEqual(client.commits, [])
        self.assertEqual(client.pull_requests, [])

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_force_rejects_versions_that_are_not_yanked(self, inspect: Mock) -> None:
        inspect.return_value = DebianArtifact(
            self.package, "lilygo-ui-demo", "1.2.3", "arm64", 1, "d" * 64,
        )
        for status in ("published", "revoked", "unknown"):
            with self.subTest(status=status):
                client = FakeGitHub()
                self.existing_release(client, status=status)
                with self.assertRaisesRegex(LpmError, "only replace a yanked version"):
                    publish(self.project, package_path=self.package, force=True, client=client)
                self.assertEqual(client.commits, [])
                self.assertEqual(client.pull_requests, [])

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_force_preserves_owner_identity_and_architecture_checks(self, inspect: Mock) -> None:
        inspect.return_value = DebianArtifact(
            self.package, "lilygo-ui-demo", "1.2.3", "arm64", 1, "d" * 64,
        )
        for mismatch, message in (
            ("owner", "not an owner"),
            ("missing_app", "ownership record does not exist"),
            ("app_identity", "registry identity"),
            ("package", "release identity"),
            ("version", "release identity"),
            ("app_id", "release identity"),
            ("architecture", "release architecture"),
        ):
            with self.subTest(mismatch=mismatch):
                client = FakeGitHub()
                previous = self.existing_release(client)
                app_path = "apps/lilygo-ui-demo/app.json"
                if mismatch == "owner":
                    client.registry_files[app_path]["owners"] = ["bob"]
                elif mismatch == "missing_app":
                    del client.registry_files[app_path]
                elif mismatch == "app_identity":
                    client.registry_files[app_path]["app_id"] = "cc.lilygo.ui.Other"
                elif mismatch == "architecture":
                    previous["artifact"]["architecture"] = "amd64"
                else:
                    previous["manifest"][mismatch] = "other"
                with self.assertRaisesRegex(LpmError, message):
                    publish(self.project, package_path=self.package, force=True, client=client)
                self.assertEqual(client.commits, [])
                self.assertEqual(client.pull_requests, [])

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_force_allows_a_new_version(self, inspect: Mock) -> None:
        inspect.return_value = DebianArtifact(
            self.package, "lilygo-ui-demo", "1.2.3", "arm64", 1, "d" * 64,
        )
        client = FakeGitHub()
        publish(self.project, package_path=self.package, force=True, client=client)
        self.assertEqual(client.pull_requests[0]["title"], "Publish lilygo-ui-demo 1.2.3")

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

    @patch("lpm.registry.inspect_debian_package")
    def test_runtime_publish_uses_runtime_registry_records(self, inspect: Mock) -> None:
        runtime = Project(
            self.root,
            {
                "schema_version": 1,
                "project": {"kind": "runtime", "package": "meshtasticd"},
                "build": {"package_dir": "dist"},
            },
        )
        inspect.return_value = DebianArtifact(
            self.package,
            "meshtasticd",
            "2.8.0.0~local4528018+lr2021cad1",
            "arm64",
            self.package.stat().st_size,
            "e" * 64,
        )
        client = FakeGitHub()
        result = publish(runtime, package_path=self.package, client=client)
        files = client.commits[0]["files"]
        self.assertIn("runtimes/meshtasticd/runtime.json", files)
        release_path = "runtimes/meshtasticd/releases/2.8.0.0~local4528018+lr2021cad1.json"
        release = json.loads(files[release_path])
        self.assertEqual(release["manifest"]["package"], "meshtasticd")
        self.assertEqual(release["manifest"]["version"], "2.8.0.0~local4528018+lr2021cad1")
        self.assertEqual(release["manifest"]["compatibility"]["architectures"], ["arm64"])
        self.assertEqual(release["artifact"]["central"]["tag"], "apt-pool-meshtasticd")
        self.assertEqual(result.package, "meshtasticd")
        self.assertEqual(result.version, "2.8.0.0~local4528018+lr2021cad1")

    @patch("lpm.registry.inspect_debian_package")
    def test_runtime_publish_can_use_an_application_repository(self, inspect: Mock) -> None:
        inspect.return_value = DebianArtifact(
            self.package,
            "meshtasticd",
            "2.8.0.0~local4528018+lr2021cad1",
            "arm64",
            self.package.stat().st_size,
            "f" * 64,
        )
        client = FakeGitHub()
        result = publish(
            self.project,
            package_path=self.package,
            runtime=True,
            client=client,
        )
        files = client.commits[0]["files"]
        self.assertIn("runtimes/meshtasticd/runtime.json", files)
        self.assertIn(
            "runtimes/meshtasticd/releases/2.8.0.0~local4528018+lr2021cad1.json",
            files,
        )
        self.assertEqual(result.package, "meshtasticd")


if __name__ == "__main__":
    unittest.main()
