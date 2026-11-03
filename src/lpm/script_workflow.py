# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import Project
from .errors import LpmError
from .process import run


def _command(
    project: Project, action: str, *, preset: str | None, token: str, destination: Path
) -> list[str]:
    if preset is not None:
        raise LpmError("--preset is only supported by the cmake build backend")
    key = f"{action}_command"
    configured = project.get("build", key)
    if not isinstance(configured, list) or not configured or any(
        not isinstance(argument, str) or not argument or "\0" in argument
        for argument in configured
    ):
        raise LpmError(f"build.{key} must be a nonempty array of nonempty strings")
    if token not in configured:
        raise LpmError(f"build.{key} must include {token} as a separate argument")
    substitutions = {"{python}": sys.executable, token: str(destination)}
    # Arguments are passed directly to subprocess. No shell, environment, or
    # substring interpolation is performed.
    return [substitutions.get(argument, argument) for argument in configured]


def _validate_report(report: Path) -> None:
    try:
        root = ET.parse(report).getroot()
    except (OSError, ET.ParseError) as exc:
        raise LpmError(f"script test did not produce a valid JUnit report at {report}: {exc}") from exc
    if root.tag not in {"testsuite", "testsuites"}:
        raise LpmError(f"script test report must have a testsuite or testsuites root: {report}")
    if next(root.iter("testcase"), None) is None:
        raise LpmError(f"script test report contains no test cases: {report}")
    for element in root.iter():
        if element.tag in {"failure", "error"}:
            raise LpmError(f"script test reported failures or errors in {report}")
        if element.tag not in {"testsuite", "testsuites"}:
            continue
        for key in ("tests", "failures", "errors", "skipped"):
            if key not in element.attrib:
                continue
            try:
                count = int(element.attrib[key])
            except ValueError as exc:
                raise LpmError(f"invalid JUnit {key} count in {report}") from exc
            if count < 0:
                raise LpmError(f"invalid JUnit {key} count in {report}")
            if key in {"failures", "errors"} and count:
                raise LpmError(f"script test reported failures or errors in {report}")


def test_script(
    project: Project, *, preset: str | None, report: Path | None
) -> Path:
    report = (report or project.state_dir / "test-results.xml").resolve()
    command = _command(project, "test", preset=preset, token="{report}", destination=report)
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        # Successful commands must create a report for this invocation. An old
        # report cannot turn an accidentally skipped test run into a success.
        report.unlink(missing_ok=True)
    except OSError as exc:
        raise LpmError(f"cannot prepare JUnit report {report}: {exc}") from exc
    run(command, cwd=project.root)
    _validate_report(report)
    return report


def _package_snapshot(output: Path) -> dict[Path, tuple[int, int, int, int, str]]:
    packages = {}
    for path in output.rglob("*.deb"):
        if not path.is_file() or "_CPack_Packages" in path.relative_to(output).parts:
            continue
        stat = path.stat()
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        packages[path.resolve()] = (
            stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, digest
        )
    return packages


def pack_script(
    project: Project, *, preset: str | None, output: Path | None
) -> list[Path]:
    output = (output or project.root / str(project.get("build", "package_dir", "dist"))).resolve()
    command = _command(project, "pack", preset=preset, token="{output}", destination=output)
    try:
        output.mkdir(parents=True, exist_ok=True)
        before = _package_snapshot(output)
        run(command, cwd=project.root)
        after = _package_snapshot(output)
    except OSError as exc:
        raise LpmError(f"cannot inspect script package output {output}: {exc}") from exc
    packages = sorted(
        (path for path, signature in after.items() if before.get(path) != signature),
        key=lambda path: (after[path][2], str(path)),
    )
    if not packages:
        raise LpmError(f"script pack completed but no new or changed Debian package was produced under {output}")
    return packages
