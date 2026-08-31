# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import LpmError

DEFAULT_DEPLOY_HOST = "raspberrypi.local"
DEFAULT_DEPLOY_USER = "pi"
DEFAULT_DEPLOY_PORT = 22


@dataclass(frozen=True)
class Project:
    root: Path
    data: dict[str, Any]

    def get(self, section: str, key: str, default: Any = None) -> Any:
        value = self.data.get(section, {})
        return value.get(key, default) if isinstance(value, dict) else default

    @property
    def state_dir(self) -> Path:
        return self.root / ".lpm"


def find_project(start: Path | None = None) -> Project:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        config_path = candidate / "lpm.toml"
        if config_path.is_file():
            return Project(candidate, load_toml(config_path))
        if (candidate / "CMakeLists.txt").is_file():
            return Project(candidate, {})
    raise LpmError("not inside an LPM or CMake project")


def load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LpmError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise LpmError(f"invalid configuration in {path}")
    return data


def validate_slug(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise LpmError("project name must be lowercase kebab-case")
    return value


def pascal_case(slug: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in slug.split("-"))


def validate_version(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(
        r"(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){1,2}(?:[-+][0-9A-Za-z.-]+)?",
        value,
    ):
        raise LpmError("version must look like 1.0.0")
    return value


def quote_toml(value: str) -> str:
    # TOML basic strings and JSON strings share the escapes used here.
    return json.dumps(value, ensure_ascii=False)


def _toml_string_array(values: list[object]) -> str:
    return "[" + ", ".join(quote_toml(str(value)) for value in values) + "]"


def render_config(data: dict[str, Any]) -> str:
    project = data["project"]
    assets = project["assets"]
    compatibility = project["compatibility"]
    launcher = project["launcher"]
    build = data.get("build", {})
    deploy = data.get("deploy", {})

    lines = [
        f'schema_version = {int(data.get("schema_version", 1))}',
        "",
        "[project]",
    ]
    for key in (
        "package",
        "version",
        "app_id",
        "title",
        "summary",
        "description",
        "license",
        "source_repo",
        "homepage",
    ):
        lines.append(f"{key} = {quote_toml(str(project[key]))}")
    lines.append(f'categories = {_toml_string_array(project["categories"])}')
    if not project["permissions"]:
        lines.append("permissions = []")

    for author in project["authors"]:
        lines.extend(["", "[[project.authors]]"])
        for key in ("name", "email", "github"):
            if key in author:
                lines.append(f"{key} = {quote_toml(str(author[key]))}")

    lines.extend(
        [
            "",
            "[project.assets]",
            f'icon = {quote_toml(str(assets["icon"]))}',
        ]
    )
    for screenshot in assets["screenshots"]:
        lines.extend(
            [
                "",
                "[[project.assets.screenshots]]",
                f'path = {quote_toml(str(screenshot["path"]))}',
                f'caption = {quote_toml(str(screenshot["caption"]))}',
            ]
        )

    for permission in project["permissions"]:
        lines.extend(["", "[[project.permissions]]"])
        for key in ("id", "access", "scope", "reason"):
            if key in permission:
                lines.append(f"{key} = {quote_toml(str(permission[key]))}")

    lines.extend(
        [
            "",
            "[project.compatibility]",
            f'architectures = {_toml_string_array(compatibility["architectures"])}',
            "min_appkit_version = "
            + quote_toml(str(compatibility["min_appkit_version"])),
            "",
            "[project.launcher]",
            f'order = {int(launcher["order"])}',
            "",
            "[build]",
            "host_preset = "
            + quote_toml(str(build.get("host_preset", "host-simulator"))),
            "cross_preset = "
            + quote_toml(str(build.get("cross_preset", "cm0-cross"))),
            "package_dir = "
            + quote_toml(str(build.get("package_dir", "dist"))),
            "",
            "[deploy]",
            "host = " + quote_toml(str(deploy.get("host", DEFAULT_DEPLOY_HOST))),
            "user = " + quote_toml(str(deploy.get("user", DEFAULT_DEPLOY_USER))),
            f'port = {int(deploy.get("port", DEFAULT_DEPLOY_PORT))}',
        ]
    )
    return "\n".join(lines) + "\n"
