# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .config import Project
from .errors import LpmError

TARGET_ARCHITECTURES = {"cm0": "arm64", "k230": "riscv64", "all": None}


def load_target(project: Project) -> str | None:
    path = project.state_dir / "target.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise LpmError(f"cannot read {path}: {exc}; run lpm set-target again") from exc
    target = data.get("target") if isinstance(data, dict) else None
    if not isinstance(target, str) or target not in TARGET_ARCHITECTURES:
        raise LpmError(f"invalid target in {path}; run lpm set-target cm0, k230, or all")
    return target


def save_target(project: Project, target: str) -> None:
    if target not in TARGET_ARCHITECTURES:
        raise LpmError("target must be cm0, k230, or all")
    path = project.state_dir / "target.json"
    temporary: Path | None = None
    try:
        project.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=project.state_dir,
            prefix=".target-", suffix=".json", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps({"target": target}, indent=2) + "\n")
        temporary.replace(path)
    except OSError as exc:
        raise LpmError(f"cannot save target to {path}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def resolve_target(
    project: Project, *, command: str, architecture: str | None = None,
    all_architectures: bool = False, explicit_selection: bool = False,
) -> tuple[str | None, bool]:
    """Use the saved project target only when the command has no selection."""
    if architecture is not None or all_architectures or explicit_selection:
        return architecture, all_architectures
    target = load_target(project)
    if target is None:
        return None, False
    if target == "all":
        if command in {"deploy", "unpublish"}:
            raise LpmError(
                f"lpm {command} requires one architecture; target is 'all'. "
                "Run lpm set-target cm0 or lpm set-target k230, "
                "or override with --arch"
            )
        return None, True
    return TARGET_ARCHITECTURES[target], False
