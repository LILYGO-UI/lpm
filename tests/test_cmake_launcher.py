# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lpm.commands import _cmake_lpm_executable, configure_and_build
from lpm.config import Project, render_config
from lpm.metadata import render_cmake_metadata


class CMakeLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="lpm launcher ' ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = Project(
            self.root,
            {
                "schema_version": 1,
                "project": {"kind": "runtime", "package": "lilygo-launcher-fixture"},
            },
        )
        (self.root / "lpm.toml").write_text(render_config(self.project.data), encoding="utf-8")

    def test_configure_uses_current_console_entrypoint(self) -> None:
        for name in ("lpm", "lpm.exe"):
            with self.subTest(name=name):
                entrypoint = self.root / name
                entrypoint.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                entrypoint.chmod(0o755)
                with (
                    patch("lpm.commands.sys.argv", [os.path.relpath(entrypoint)]),
                    patch("lpm.commands.require_tool"),
                    patch("lpm.commands.run") as run,
                ):
                    configure_and_build(self.project, "k230-debian")
                self.assertEqual(
                    run.call_args_list[0].args[0],
                    ["cmake", "--preset", "k230-debian", f"-DLPM_EXECUTABLE:FILEPATH={entrypoint.absolute()}"],
                )
                self.assertFalse(self.project.state_dir.exists())

    def test_module_launcher_preserves_interpreter_and_handles_spaces(self) -> None:
        interpreter = self.root / "python with spaces"
        interpreter.symlink_to(sys.executable)
        bad_bin = self.root / "unrelated bin"
        bad_bin.mkdir()
        unrelated = bad_bin / "lpm"
        unrelated.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        unrelated.chmod(0o755)
        environment = {**os.environ, "PATH": str(bad_bin)}
        # Also ensure the launcher can import the active source without a
        # PYTHONPATH inherited from the test runner.
        environment.pop("PYTHONPATH", None)
        with (
            patch("lpm.commands.sys.argv", ["/source/lpm/__main__.py"]),
            patch("lpm.commands.sys.executable", str(interpreter)),
        ):
            launcher = _cmake_lpm_executable(self.project)
        command = shlex.split(launcher.read_text(encoding="utf-8").splitlines()[1])
        self.assertEqual(command[1], str(interpreter))
        result = subprocess.run(
            [str(launcher), "metadata", "--format", "cmake"],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout, render_cmake_metadata(self.project))

    def test_nonexecutable_console_entrypoint_uses_launcher(self) -> None:
        entrypoint = self.root / "lpm"
        entrypoint.write_text("not executable\n", encoding="utf-8")
        with patch("lpm.commands.sys.argv", [str(entrypoint)]):
            launcher = _cmake_lpm_executable(self.project)
        self.assertEqual(launcher, self.project.state_dir / "cmake-lpm")
        self.assertTrue(os.access(launcher, os.X_OK))

    @unittest.skipUnless(shutil.which("cmake") and shutil.which("make"), "CMake and Make required")
    def test_real_cmake_overrides_cached_entrypoint_with_missing_interpreter(self) -> None:
        stale = self.root / ".venv" / "bin" / "lpm"
        stale.parent.mkdir(parents=True)
        stale.write_text("#!/missing/old-project/.venv/bin/python\n", encoding="utf-8")
        stale.chmod(0o755)
        (self.root / "CMakeLists.txt").write_text(
            """cmake_minimum_required(VERSION 3.21)
project(lpm_launcher_fixture NONE)
find_program(LPM_EXECUTABLE NAMES lpm HINTS "${CMAKE_SOURCE_DIR}/.venv/bin")
execute_process(
    COMMAND "${LPM_EXECUTABLE}" metadata --format cmake
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    RESULT_VARIABLE result
    OUTPUT_VARIABLE metadata
    ERROR_VARIABLE error)
if(NOT result EQUAL 0)
    message(FATAL_ERROR "Metadata command failed (${result}): ${error}")
endif()
file(WRITE "${CMAKE_BINARY_DIR}/metadata.cmake" "${metadata}")
""",
            encoding="utf-8",
        )
        (self.root / "CMakePresets.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "configurePresets": [
                        {
                            "name": "fixture",
                            "generator": "Unix Makefiles",
                            "binaryDir": "${sourceDir}/build/fixture",
                        }
                    ],
                    "buildPresets": [{"name": "fixture", "configurePreset": "fixture"}],
                }
            ),
            encoding="utf-8",
        )
        failed = subprocess.run(
            ["cmake", "--preset", "fixture"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("no such file or directory", failed.stderr.lower())
        cache = self.root / "build" / "fixture" / "CMakeCache.txt"
        self.assertIn(f"LPM_EXECUTABLE:FILEPATH={stale}", cache.read_text(encoding="utf-8"))

        with patch("lpm.commands.sys.argv", ["/source/lpm/__main__.py"]):
            configure_and_build(self.project, "fixture")

        self.assertEqual(
            (cache.parent / "metadata.cmake").read_text(encoding="utf-8"),
            render_cmake_metadata(self.project),
        )
        self.assertIn(
            f"LPM_EXECUTABLE:FILEPATH={self.project.state_dir / 'cmake-lpm'}",
            cache.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
