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
    def kind(self) -> str:
        value = self.get("project", "kind", "application")
        if value not in ("application", "runtime"):
            raise LpmError("project.kind must be 'application' or 'runtime'")
        if value == "runtime":
            metadata = self.data["project"]
            unknown = sorted(set(metadata) - {"kind", "package"})
            if unknown:
                raise LpmError(
                    "runtime project contains unsupported fields: " + ", ".join(unknown)
                )
            package = metadata.get("package")
            if (
                not isinstance(package, str)
                or len(package) > 128
                or not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9+.-]*[a-z0-9])?", package
                )
            ):
                raise LpmError("runtime project.package must be a valid Debian package name")
        return value

    @property
    def build_backend(self) -> str:
        # Validate project kind for build/deploy workflows without requiring
        # application metadata from existing plain CMake SDK projects.
        self.kind
        value = self.get("build", "backend", "cmake")
        if value not in ("cmake", "script"):
            raise LpmError("build.backend must be 'cmake' or 'script'")
        return value

    def require_application(self, command: str) -> None:
        if self.kind == "runtime":
            raise LpmError(
                f"lpm {command} is only available for application projects; "
                "runtime projects support test, pack, deploy, metadata, publish, and unpublish"
            )

    def run_prepare_command(self) -> list[str] | None:
        """Return the project startup preparation command, if configured.

        The command is deliberately kept as an argument array.  LPM executes
        it without a shell so that project configuration cannot accidentally
        turn a value into shell syntax.
        """
        run_config = self._run_config()
        if "prepare_command" not in run_config:
            return None
        command = run_config["prepare_command"]
        if not isinstance(command, list) or not command or any(
            not isinstance(argument, str) or not argument or "\0" in argument
            for argument in command
        ):
            raise LpmError("run.prepare_command must be a nonempty array of nonempty strings")
        return list(command)

    def run_environment(self) -> dict[str, str]:
        """Return validated environment defaults for startup preparation/UI."""
        run_config = self._run_config()
        configured = run_config.get("environment", {})
        if configured is None:
            return {}
        if not isinstance(configured, dict):
            raise LpmError("run.environment must be a table of string values")
        environment: dict[str, str] = {}
        for key, value in configured.items():
            if (
                not isinstance(key, str)
                or not key
                or "=" in key
                or "\0" in key
                or not isinstance(value, str)
                or "\0" in value
            ):
                raise LpmError("run.environment keys and values must be valid strings")
            environment[key] = value
        return environment

    def _run_config(self) -> dict[str, Any]:
        configured = self.data.get("run", {})
        if configured is None:
            return {}
        if not isinstance(configured, dict):
            raise LpmError("run must be a table")
        unknown = sorted(set(configured) - {"prepare_command", "environment"})
        if unknown:
            raise LpmError("run contains unsupported fields: " + ", ".join(unknown))
        return configured

    @property
    def state_dir(self) -> Path:
        return self.root / ".lpm"

    def cross_presets(self) -> dict[str, str]:
        presets = self.get("build", "cross_presets", {})
        if not isinstance(presets, dict) or any(
            not isinstance(arch, str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", arch)
            or arch == "source" or "any" in arch.split("-") or len(arch) > 64
            or not isinstance(preset, str) or not preset or "\0" in preset
            for arch, preset in presets.items()
        ):
            raise LpmError("build.cross_presets must map Debian architectures to CMake preset names")
        if len(set(presets.values())) != len(presets):
            raise LpmError("build.cross_presets must use a distinct preset for each architecture")
        return dict(presets)


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
    if project.get("kind") == "runtime":
        return _render_runtime_config(data)
    assets = project["assets"]
    compatibility = project["compatibility"]
    launcher = project["launcher"]
    build = data.get("build", {})
    deploy = data.get("deploy", {})
    run = data.get("run")

    lines = [
        f'schema_version = {int(data.get("schema_version", 1))}',
        "",
        "[project]",
    ]
    if "kind" in project:
        lines.append(f'kind = {quote_toml(str(project["kind"]))}')
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
    if "cross_presets" in build:
        lines.extend(["", "[build.cross_presets]"])
        for arch, preset in Project(Path("."), data).cross_presets().items():
            lines.append(f"{quote_toml(arch)} = {quote_toml(preset)}")
    if run is not None:
        if not isinstance(run, dict):
            raise LpmError("run must be a table")
        lines.extend(["", "[run]"])
        if "prepare_command" in run:
            command = run["prepare_command"]
            if not isinstance(command, list) or not command or any(
                not isinstance(argument, str) or not argument or "\0" in argument
                for argument in command
            ):
                raise LpmError("run.prepare_command must be a nonempty array of nonempty strings")
            lines.append(f"prepare_command = {_toml_string_array(command)}")
        environment = run.get("environment")
        if environment is not None:
            if not isinstance(environment, dict):
                raise LpmError("run.environment must be a table of string values")
            lines.extend(["", "[run.environment]"])
            for key, value in environment.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or "=" in key
                    or "\0" in key
                    or not isinstance(value, str)
                    or "\0" in value
                ):
                    raise LpmError("run.environment keys and values must be valid strings")
                lines.append(f"{key} = {quote_toml(str(value))}")
    return "\n".join(lines) + "\n"


def _render_runtime_config(data: dict[str, Any]) -> str:
    """Render the small configuration supported by runtime projects.

    Runtime package identity and Debian metadata belong to the native package
    build, so their ``[project]`` table only contains ``kind`` and ``package``.
    Keep the workflow tables that LPM consumes without requiring application
    assets, Launcher metadata, or an application version.
    """
    project = data["project"]
    package = project.get("package")
    if (
        not isinstance(package, str)
        or len(package) > 128
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9+.-]*[a-z0-9])?", package)
    ):
        raise LpmError("runtime project.package must be a valid Debian package name")
    unknown_project = sorted(set(project) - {"kind", "package"})
    if unknown_project:
        raise LpmError(
            "runtime project contains unsupported fields: " + ", ".join(unknown_project)
        )

    lines = [
        f'schema_version = {int(data.get("schema_version", 1))}',
        "",
        "[project]",
        'kind = "runtime"',
        f"package = {quote_toml(package)}",
    ]

    build = data.get("build")
    if build is not None:
        if not isinstance(build, dict):
            raise LpmError("build must be a table")
        allowed_build = {
            "backend",
            "host_preset",
            "cross_preset",
            "package_dir",
            "test_command",
            "pack_command",
            "cross_presets",
        }
        unknown_build = sorted(set(build) - allowed_build)
        if unknown_build:
            raise LpmError("build contains unsupported fields: " + ", ".join(unknown_build))
        lines.extend(["", "[build]"])
        for key in ("backend", "host_preset", "cross_preset", "package_dir"):
            if key in build:
                value = build[key]
                if not isinstance(value, str) or not value or "\0" in value:
                    raise LpmError(f"build.{key} must be a nonempty string")
                if key == "backend" and value not in {"cmake", "script"}:
                    raise LpmError("build.backend must be 'cmake' or 'script'")
                lines.append(f"{key} = {quote_toml(value)}")
        for key in ("test_command", "pack_command"):
            if key not in build:
                continue
            command = build[key]
            if not isinstance(command, list) or not command or any(
                not isinstance(argument, str) or not argument or "\0" in argument
                for argument in command
            ):
                raise LpmError(f"build.{key} must be an array of nonempty strings")
            lines.append(f"{key} = {_toml_string_array(command)}")

    if isinstance(build, dict) and "cross_presets" in build:
        lines.extend(["", "[build.cross_presets]"])
        for arch, preset in Project(Path("."), data).cross_presets().items():
            lines.append(f"{quote_toml(arch)} = {quote_toml(preset)}")

    deploy = data.get("deploy")
    if deploy is not None:
        if not isinstance(deploy, dict):
            raise LpmError("deploy must be a table")
        unknown_deploy = sorted(set(deploy) - {"host", "user", "port"})
        if unknown_deploy:
            raise LpmError("deploy contains unsupported fields: " + ", ".join(unknown_deploy))
        lines.extend(["", "[deploy]"])
        for key in ("host", "user"):
            if key in deploy:
                value = deploy[key]
                if not isinstance(value, str) or not value or "\0" in value:
                    raise LpmError(f"deploy.{key} must be a nonempty string")
                lines.append(f"{key} = {quote_toml(value)}")
        if "port" in deploy:
            port = deploy["port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise LpmError("deploy.port must be an integer between 1 and 65535")
            lines.append(f"port = {port}")

    run = data.get("run")
    if run is not None:
        if not isinstance(run, dict):
            raise LpmError("run must be a table")
        unknown_run = sorted(set(run) - {"prepare_command", "environment"})
        if unknown_run:
            raise LpmError("run contains unsupported fields: " + ", ".join(unknown_run))
        lines.extend(["", "[run]"])
        if "prepare_command" in run:
            command = run["prepare_command"]
            if not isinstance(command, list) or not command or any(
                not isinstance(argument, str) or not argument or "\0" in argument
                for argument in command
            ):
                raise LpmError("run.prepare_command must be a nonempty array of nonempty strings")
            lines.append(f"prepare_command = {_toml_string_array(command)}")
        environment = run.get("environment")
        if environment is not None:
            if not isinstance(environment, dict):
                raise LpmError("run.environment must be a table of string values")
            lines.extend(["", "[run.environment]"])
            for key, value in environment.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or "=" in key
                    or "\0" in key
                    or not isinstance(value, str)
                    or "\0" in value
                ):
                    raise LpmError("run.environment keys and values must be valid strings")
                lines.append(f"{key} = {quote_toml(value)}")

    return "\n".join(lines) + "\n"
