from __future__ import annotations

import copy
from dataclasses import replace
import json
import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lpm.commands import deploy, pack
from lpm.config import Project, render_config
from lpm.errors import LpmError
from lpm.metadata import build_publish_manifest, render_cmake_metadata
from lpm.registry import DebianArtifact, inspect_debian_package, publish, unpublish
from test_registry import FakeGitHub, make_project


class MultiArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = make_project(self.root)
        self.project.data["project"]["compatibility"]["architectures"] = [
            "arm64",
            "riscv64",
        ]
        self.project.data["build"]["cross_presets"] = {
            "arm64": "cm0-cross",
            "riscv64": "k230-debian",
        }
        self.paths = [
            self.root / f"dist/demo_{arch}.deb" for arch in ("arm64", "riscv64")
        ]
        self.paths[0].parent.mkdir()
        for path in self.paths:
            path.write_bytes(b"deb fixture")
        self.artifacts = [
            DebianArtifact(path, "lilygo-ui-demo", "1.2.3", arch, 11, digit * 64)
            for path, arch, digit in zip(self.paths, ("arm64", "riscv64"), ("d", "e"))
        ]

    def test_metadata_and_config_round_trip(self):
        data = tomllib.loads(render_config(self.project.data))
        self.assertEqual(data["build"]["cross_presets"], self.project.cross_presets())
        self.assertEqual(
            build_publish_manifest(Project(self.root, data))["compatibility"][
                "architectures"
            ],
            ["arm64", "riscv64"],
        )
        content = render_cmake_metadata(self.project)
        self.assertIn("unset(LILYGO_UI_PACKAGE_ARCHITECTURE)", content)
        self.assertIn("[[arm64;riscv64]]", content)
        self.assertIn(
            "set(LILYGO_UI_PACKAGE_ARCHITECTURE [[riscv64]])",
            render_cmake_metadata(self.project, architecture="riscv64"),
        )
        with self.assertRaisesRegex(LpmError, "not declared"):
            build_publish_manifest(self.project, architecture="amd64")

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_both_in_one_commit_and_pr(self, inspect):
        inspect.side_effect = self.artifacts
        client = FakeGitHub()
        publish(self.project, package_paths=self.paths, client=client)
        self.assertEqual(len(client.commits), 1)
        self.assertEqual(len(client.pull_requests), 1)
        files = client.commits[0]["files"]
        for artifact in self.artifacts:
            record = json.loads(
                files[
                    f"apps/lilygo-ui-demo/releases/1.2.3_{artifact.architecture}.json"
                ]
            )
            self.assertEqual(
                record["manifest"]["compatibility"]["architectures"],
                [artifact.architecture],
            )
            self.assertEqual(record["artifact"]["sha256"], artifact.sha256)
        self.assertIn("arm64, riscv64", client.pull_requests[0]["title"])

    @patch("lpm.registry.inspect_debian_package")
    def test_publish_all_selects_current_version_and_ignores_cpack_staging(
        self, inspect
    ):
        staging = self.paths[0].parent / "_CPack_Packages" / "stale.deb"
        staging.parent.mkdir()
        staging.touch()
        inspect.side_effect = self.artifacts
        publish(self.project, all_architectures=True, client=FakeGitHub())
        self.assertEqual(inspect.call_count, 2)

    @patch("lpm.registry.inspect_debian_package")
    def test_batch_preflight_rejects_incomplete_or_duplicate_inputs(self, inspect):
        cases = [
            (self.artifacts[:1], {"all_architectures": True}, "every declared"),
            (
                [self.artifacts[0], self.artifacts[0]],
                {},
                "one artifact per architecture",
            ),
        ]
        for artifacts, options, message in cases:
            with self.subTest(message=message):
                inspect.side_effect = artifacts
                client = Mock()
                with self.assertRaisesRegex(LpmError, message):
                    publish(
                        self.project,
                        package_paths=self.paths[: len(artifacts)],
                        client=client,
                        **options,
                    )
                client.current_user.assert_not_called()

    @patch("lpm.registry.inspect_debian_package")
    def test_existing_variant_blocks_entire_batch_before_mutation(self, inspect):
        inspect.side_effect = self.artifacts
        client = FakeGitHub()
        client.registry_files["apps/lilygo-ui-demo/releases/1.2.3_riscv64.json"] = {
            "status": "published"
        }
        with self.assertRaisesRegex(LpmError, "already exists"):
            publish(self.project, package_paths=self.paths, client=client)
        self.assertEqual(client.commits, [])

    @patch("lpm.registry.inspect_debian_package")
    def test_runtime_batch_uses_debian_identity(self, inspect):
        inspect.side_effect = self.artifacts
        runtime = Project(
            self.root,
            {
                "schema_version": 1,
                "project": {"kind": "runtime", "package": "lilygo-ui-demo"},
            },
        )
        client = FakeGitHub()
        publish(runtime, package_paths=self.paths, client=client)
        files = client.commits[0]["files"]
        self.assertIn("runtimes/lilygo-ui-demo/releases/1.2.3_arm64.json", files)
        self.assertIn("runtimes/lilygo-ui-demo/releases/1.2.3_riscv64.json", files)

    @patch("lpm.registry.inspect_debian_package")
    def test_yank_and_republish_only_selected_variant(self, inspect):
        inspect.side_effect = self.artifacts
        client = FakeGitHub()
        publish(self.project, package_paths=self.paths, client=client)
        client.registry_files = {
            path: json.loads(value)
            for path, value in client.commits[0]["files"].items()
            if path.endswith(".json")
        }
        original = copy.deepcopy(client.registry_files)
        unpublish(
            self.project,
            version="1.2.3",
            architecture="riscv64",
            reason="Broken startup",
            client=client,
        )
        path = "apps/lilygo-ui-demo/releases/1.2.3_riscv64.json"
        self.assertEqual(set(client.commits[1]["files"]), {path})
        self.assertEqual(client.registry_files, original)
        client.registry_files[path] = json.loads(client.commits[1]["files"][path])
        inspect.side_effect = [self.artifacts[1]]
        publish(self.project, package_path=self.paths[1], force=True, client=client)
        self.assertEqual(
            json.loads(client.commits[2]["files"][path])["status"], "published"
        )
        self.assertNotIn(
            "apps/lilygo-ui-demo/releases/1.2.3_arm64.json", client.commits[2]["files"]
        )

    def test_ambiguous_default_publication_is_rejected(self):
        client = Mock()
        with self.assertRaisesRegex(LpmError, "require --arch"):
            publish(self.project, client=client)
        client.current_user.assert_not_called()
        with self.assertRaisesRegex(LpmError, "require --arch"):
            unpublish(self.project, version="1.2.3", reason="broken", client=client)
        client.current_user.assert_not_called()

    def test_deploy_requires_explicit_package_for_multi_architecture_project(self):
        with self.assertRaisesRegex(LpmError, "explicit Debian package"):
            deploy(self.project, host="example.local")

    @patch("lpm.commands.run")
    @patch("lpm.registry.inspect_debian_package")
    def test_deploy_arch_selects_control_metadata_and_ignores_staging(self, inspect, run):
        os.utime(self.paths[0], (200, 200))
        os.utime(self.paths[1], (100, 100))
        staging = self.paths[0].parent / "_CPack_Packages" / "stale.deb"
        staging.parent.mkdir()
        staging.touch()
        # Deliberately disagree with the filename: control metadata is authoritative.
        artifacts = {item.path: replace(item, architecture=arch)
                     for item, arch in zip(self.artifacts, ("riscv64", "arm64"))}
        inspect.side_effect = lambda path: artifacts[path]
        with patch("lpm.commands.require_tool"):
            selected = deploy(self.project, architecture="riscv64", host="example.local")
        self.assertEqual(selected, self.paths[0].resolve())
        self.assertEqual(inspect.call_count, 2)
        self.assertIn(self.paths[0].resolve(), run.call_args_list[0].args[0])

    @patch("lpm.commands.run")
    @patch("lpm.registry.inspect_debian_package")
    def test_deploy_arch_ignores_other_projects_and_versions(self, inspect, run):
        for wrong in (replace(self.artifacts[1], version="1.2.2"),
                      replace(self.artifacts[1], package="lilygo-ui-other")):
            with self.subTest(artifact=wrong):
                artifacts = {item.path: item for item in (self.artifacts[0], wrong)}
                inspect.side_effect = lambda path: artifacts[path]
                with self.assertRaisesRegex(LpmError, "no riscv64 Debian package"):
                    deploy(self.project, architecture="riscv64", host="example.local")
        run.assert_not_called()

    @patch("lpm.commands.run")
    @patch("lpm.registry.inspect_debian_package")
    def test_deploy_explicit_arch_mismatch_rejected_before_upload(self, inspect, run):
        inspect.return_value = self.artifacts[0]
        with self.assertRaisesRegex(LpmError, "Architecture is arm64, expected riscv64"):
            deploy(self.project, package=self.paths[0], architecture="riscv64")
        run.assert_not_called()

    @patch("lpm.commands.run")
    @patch("lpm.registry.inspect_debian_package")
    def test_deploy_undeclared_arch_rejected_before_package_inspection(self, inspect, run):
        with self.assertRaisesRegex(LpmError, "not declared"):
            deploy(self.project, architecture="amd64")
        inspect.assert_not_called()
        run.assert_not_called()

    @patch("lpm.commands._pack_cmake")
    def test_missing_mapping_rejected_before_any_build(self, build):
        del self.project.data["build"]["cross_presets"]["riscv64"]
        with self.assertRaisesRegex(LpmError, "every architecture"):
            pack(self.project, all_architectures=True)
        build.assert_not_called()

    @patch("lpm.registry.inspect_debian_package")
    @patch("lpm.commands._pack_cmake")
    def test_wrong_architecture_never_copied_to_dist(self, build, inspect):
        build.return_value = [self.paths[0]]
        inspect.return_value = self.artifacts[0]
        with self.assertRaisesRegex(
            LpmError, "Architecture is arm64, expected riscv64"
        ):
            pack(self.project, architecture="riscv64")
        self.assertFalse((self.root / "dist/lilygo-ui-demo_1.2.3_riscv64.deb").exists())

    @unittest.skipUnless(
        shutil.which("cmake") and shutil.which("cpack") and shutil.which("dpkg-deb"),
        "CMake and dpkg-deb required",
    )
    def test_real_cpack_builds_two_control_architectures(self):
        for path in self.paths:
            path.unlink()
        (self.root / "payload.txt").write_text("packaging smoke fixture\n")
        (
            self.root / "CMakeLists.txt"
        ).write_text("""cmake_minimum_required(VERSION 3.21)
project(lpm_fixture NONE)
install(FILES payload.txt DESTINATION share/lilygo-ui-demo)
set(CPACK_GENERATOR DEB)
set(CPACK_PACKAGE_NAME lilygo-ui-demo)
set(CPACK_PACKAGE_VERSION 1.2.3)
set(CPACK_PACKAGE_CONTACT "Test <test@example.com>")
set(CPACK_DEBIAN_FILE_NAME DEB-DEFAULT)
set(CPACK_DEBIAN_PACKAGE_ARCHITECTURE "${TEST_ARCH}")
include(CPack)
""")
        presets = {"version": 3, "configurePresets": [], "buildPresets": []}
        for arch, preset in self.project.cross_presets().items():
            presets["configurePresets"].append(
                {
                    "name": preset,
                    "generator": "Unix Makefiles",
                    "binaryDir": "${sourceDir}/build/" + preset,
                    "cacheVariables": {"TEST_ARCH": arch},
                }
            )
            presets["buildPresets"].append({"name": preset, "configurePreset": preset})
        (self.root / "CMakePresets.json").write_text(json.dumps(presets))
        with patch("lpm.k230_sdk.prepare_pack", return_value=()) as sdk:
            packages = pack(self.project, all_architectures=True)
        sdk.assert_called_once_with(self.project.root, self.project.root / "build/k230-debian")
        self.assertEqual(
            [inspect_debian_package(path).architecture for path in packages],
            ["arm64", "riscv64"],
        )
        self.assertEqual(
            [path.name for path in packages],
            ["lilygo-ui-demo_1.2.3_arm64.deb", "lilygo-ui-demo_1.2.3_riscv64.deb"],
        )
        with patch("lpm.commands.run") as remote, patch("lpm.commands.require_tool"):
            selected = deploy(self.project, architecture="riscv64", host="example.local")
        self.assertEqual(selected, packages[1])
        self.assertIn(packages[1], remote.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
