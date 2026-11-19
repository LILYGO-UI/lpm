# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Project
from .errors import LpmError

CONFIG_SCHEMA_VERSION = 1
PUBLISH_SCHEMA_VERSION = 1
PUBLISH_SCHEMA_URL = (
    "https://raw.githubusercontent.com/LILYGO-UI/packages/main/publish.schema.json"
)

_PACKAGE_PATTERN = re.compile(r"lilygo-ui-[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION_PATTERN = re.compile(
    r"(?:[0-9]+:)?[0-9][A-Za-z0-9.+~]*(?:-[A-Za-z0-9.+~]+)?"
)
_APP_ID_PATTERN = re.compile(r"cc\.lilygo\.ui\.[A-Z][A-Za-z0-9]*")
_APPKIT_VERSION_PATTERN = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
)
_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_GITHUB_PATTERN = re.compile(r"(?=.{1,39}$)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
_REPOSITORY_PATH_PATTERN = re.compile(r"[A-Za-z0-9._/-]+")
_ARCHITECTURE_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

_CATEGORIES = {
    "Development",
    "Education",
    "Games",
    "Graphics",
    "Lifestyle",
    "Multimedia",
    "Network",
    "Office",
    "Science",
    "System",
    "Utilities",
}
_PERMISSION_IDS = {
    "network",
    "filesystem",
    "background-service",
    "bluetooth",
    "camera",
    "microphone",
    "location",
    "serial",
    "gpio",
    "i2c",
    "spi",
    "imu",
    "external-display",
}
_FILESYSTEM_ACCESS = {"read", "write", "read-write"}
_FILESYSTEM_SCOPES = {"app-data", "shared-data", "removable-storage"}
_PROJECT_FIELDS = {
    "kind",
    "package",
    "version",
    "app_id",
    "title",
    "summary",
    "description",
    "authors",
    "categories",
    "license",
    "source_repo",
    "homepage",
    "assets",
    "permissions",
    "compatibility",
    "launcher",
    "executable",
}


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LpmError(f"{path} must be a table")
    return value


def _reject_unknown(table: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise LpmError(f"{path} contains unsupported fields: {', '.join(unknown)}")


def _list(value: object, path: str, *, minimum: int = 0, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise LpmError(f"{path} must contain between {minimum} and {maximum} items")
    return value


def _string(
    table: dict[str, Any], key: str, path: str, *, minimum: int = 1, maximum: int
) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise LpmError(
            f"{path}.{key} must be a string between {minimum} and {maximum} characters"
        )
    return value


def _pattern(value: str, pattern: re.Pattern[str], path: str) -> str:
    if not pattern.fullmatch(value):
        raise LpmError(f"{path} has an invalid value: {value}")
    return value


def _https_url(value: str, path: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise LpmError(f"{path} must be a public HTTPS URL")
    return value


def _repository_path(value: str, path: str, extensions: tuple[str, ...]) -> str:
    parts = Path(value).parts
    if (
        not value
        or Path(value).is_absolute()
        or ".." in parts
        or not _REPOSITORY_PATH_PATTERN.fullmatch(value)
        or not value.lower().endswith(extensions)
    ):
        raise LpmError(f"{path} must be a repository-relative {extensions} path")
    return value


def _validate_assets(project: Project, assets: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(assets, {"icon", "screenshots"}, "project.assets")
    icon = _repository_path(
        _string(assets, "icon", "project.assets", maximum=4096),
        "project.assets.icon",
        (".png",),
    )
    screenshots_data = _list(
        assets.get("screenshots"),
        "project.assets.screenshots",
        minimum=1,
        maximum=20,
    )
    screenshots: list[dict[str, str]] = []
    for index, item in enumerate(screenshots_data):
        path = f"project.assets.screenshots[{index}]"
        screenshot = _mapping(item, path)
        if set(screenshot) - {"path", "caption"}:
            raise LpmError(f"{path} contains unsupported fields")
        screenshot_path = _repository_path(
            _string(screenshot, "path", path, maximum=4096),
            f"{path}.path",
            (".png", ".webp", ".jpg", ".jpeg"),
        )
        screenshots.append(
            {
                "path": screenshot_path,
                "caption": _string(screenshot, "caption", path, maximum=160),
            }
        )

    for relative in (icon, *(item["path"] for item in screenshots)):
        if not (project.root / relative).is_file():
            raise LpmError(f"project asset does not exist: {relative}")
    return {"icon": icon, "screenshots": screenshots}


def _validate_authors(value: object) -> list[dict[str, str]]:
    authors_data = _list(value, "project.authors", minimum=1, maximum=20)
    authors: list[dict[str, str]] = []
    for index, item in enumerate(authors_data):
        path = f"project.authors[{index}]"
        author = _mapping(item, path)
        if set(author) - {"name", "email", "github"}:
            raise LpmError(f"{path} contains unsupported fields")
        normalized = {"name": _string(author, "name", path, maximum=100)}
        email = author.get("email")
        github = author.get("github")
        if email is None and github is None:
            raise LpmError(f"{path} requires email or github")
        if email is not None:
            if (
                not isinstance(email, str)
                or len(email) > 254
                or not _EMAIL_PATTERN.fullmatch(email)
            ):
                raise LpmError(f"{path}.email must be a valid email address")
            normalized["email"] = email
        if github is not None:
            if not isinstance(github, str) or not _GITHUB_PATTERN.fullmatch(github):
                raise LpmError(f"{path}.github must be a valid GitHub username")
            normalized["github"] = github
        authors.append(normalized)
    return authors


def _validate_permissions(value: object) -> list[dict[str, str]]:
    permissions_data = _list(value, "project.permissions", maximum=32)
    permissions: list[dict[str, str]] = []
    for index, item in enumerate(permissions_data):
        path = f"project.permissions[{index}]"
        permission = _mapping(item, path)
        if set(permission) - {"id", "reason", "access", "scope"}:
            raise LpmError(f"{path} contains unsupported fields")
        permission_id = _string(permission, "id", path, maximum=64)
        if permission_id not in _PERMISSION_IDS:
            raise LpmError(f"{path}.id is not a supported permission")
        normalized = {
            "id": permission_id,
            "reason": _string(permission, "reason", path, maximum=500),
        }
        if permission_id == "filesystem":
            access = permission.get("access")
            scope = permission.get("scope")
            if access not in _FILESYSTEM_ACCESS or scope not in _FILESYSTEM_SCOPES:
                raise LpmError(
                    f"{path} filesystem permission requires valid access and scope"
                )
            normalized.update({"access": str(access), "scope": str(scope)})
        elif "access" in permission or "scope" in permission:
            raise LpmError(f"{path} access and scope are only valid for filesystem")
        permissions.append(normalized)
    return permissions


def build_publish_manifest(project: Project, *, architecture: str | None = None) -> dict[str, Any]:
    project.require_application("metadata")
    config_version = project.data.get("schema_version")
    if type(config_version) is not int or config_version != CONFIG_SCHEMA_VERSION:
        raise LpmError(
            f"lpm.toml schema_version must be {CONFIG_SCHEMA_VERSION}"
        )
    metadata = _mapping(project.data.get("project"), "project")
    _reject_unknown(metadata, _PROJECT_FIELDS, "project")

    package = _pattern(
        _string(metadata, "package", "project", maximum=128),
        _PACKAGE_PATTERN,
        "project.package",
    )
    version = _pattern(
        _string(metadata, "version", "project", maximum=128),
        _VERSION_PATTERN,
        "project.version",
    )
    app_id = _pattern(
        _string(metadata, "app_id", "project", maximum=255),
        _APP_ID_PATTERN,
        "project.app_id",
    )
    title = _string(metadata, "title", "project", maximum=80)
    summary = _string(metadata, "summary", "project", maximum=160)
    description = _string(metadata, "description", "project", maximum=4000)
    authors = _validate_authors(metadata.get("authors"))

    categories_data = _list(
        metadata.get("categories"), "project.categories", minimum=1, maximum=11
    )
    if any(not isinstance(item, str) or item not in _CATEGORIES for item in categories_data):
        raise LpmError("project.categories contains an unsupported category")
    if len(set(categories_data)) != len(categories_data):
        raise LpmError("project.categories must not contain duplicates")
    categories = list(categories_data)

    license_name = _string(metadata, "license", "project", maximum=128)
    source_repo = _https_url(
        _string(metadata, "source_repo", "project", maximum=4096),
        "project.source_repo",
    )
    homepage = _https_url(
        _string(metadata, "homepage", "project", maximum=4096),
        "project.homepage",
    )
    assets = _validate_assets(
        project, _mapping(metadata.get("assets"), "project.assets")
    )
    permissions = _validate_permissions(metadata.get("permissions"))

    compatibility = _mapping(
        metadata.get("compatibility"), "project.compatibility"
    )
    _reject_unknown(
        compatibility,
        {"architectures", "min_appkit_version"},
        "project.compatibility",
    )
    architectures_data = _list(
        compatibility.get("architectures"),
        "project.compatibility.architectures",
        minimum=1,
        maximum=64,
    )
    for value in architectures_data:
        if (
            not isinstance(value, str)
            or len(value) > 64
            or not _ARCHITECTURE_PATTERN.fullmatch(value)
            or value == "source"
            or "any" in value.split("-")
        ):
            raise LpmError("project.compatibility.architectures must contain concrete Debian architecture names or 'all'")
    if len(set(architectures_data)) != len(architectures_data):
        raise LpmError("project.compatibility.architectures must not contain duplicates")
    if "all" in architectures_data and len(architectures_data) != 1:
        raise LpmError("project.compatibility.architectures cannot combine 'all' with concrete architectures")
    if architecture is not None and architecture not in architectures_data:
        raise LpmError(f"architecture {architecture!r} is not declared in project.compatibility.architectures")
    min_appkit_version = _pattern(
        _string(
            compatibility,
            "min_appkit_version",
            "project.compatibility",
            maximum=128,
        ),
        _APPKIT_VERSION_PATTERN,
        "project.compatibility.min_appkit_version",
    )

    launcher = _mapping(metadata.get("launcher"), "project.launcher")
    _reject_unknown(launcher, {"order"}, "project.launcher")
    order = launcher.get("order")
    if type(order) is not int or not 0 <= order <= 1000:
        raise LpmError("project.launcher.order must be an integer from 0 to 1000")

    return {
        "$schema": PUBLISH_SCHEMA_URL,
        "schema_version": PUBLISH_SCHEMA_VERSION,
        "package": package,
        "version": version,
        "app_id": app_id,
        "title": title,
        "summary": summary,
        "description": description,
        "authors": authors,
        "categories": categories,
        "license": license_name,
        "source_repo": source_repo,
        "homepage": homepage,
        "assets": assets,
        "permissions": permissions,
        "compatibility": {
            "architectures": [architecture] if architecture is not None else list(architectures_data),
            "min_appkit_version": min_appkit_version,
        },
        "launcher": {"order": order},
    }


def build_runtime_metadata(project: Project) -> dict[str, Any]:
    """Build the local metadata snapshot for a runtime project.

    Runtime release records cannot be generated from ``lpm.toml`` alone: the
    version, architecture, size, and digest come from the Debian artifact,
    while the registry ownership record is completed with the GitHub account
    during ``lpm publish``.  This output therefore describes the local runtime
    identity and is intentionally separate from the registry schemas.
    """
    if project.kind != "runtime":
        raise LpmError("runtime metadata is only available for runtime projects")
    config_version = project.data.get("schema_version")
    if type(config_version) is not int or config_version != CONFIG_SCHEMA_VERSION:
        raise LpmError(f"lpm.toml schema_version must be {CONFIG_SCHEMA_VERSION}")
    metadata = _mapping(project.data.get("project"), "project")
    _reject_unknown(metadata, {"kind", "package"}, "project")
    package = metadata.get("package")
    if not isinstance(package, str) or not package:
        raise LpmError("project.package must be a valid Debian package name")
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "kind": "runtime",
        "package": package,
    }


def render_publish_json(project: Project, *, architecture: str | None = None) -> str:
    if project.kind == "runtime":
        payload = build_runtime_metadata(project)
    else:
        payload = build_publish_manifest(project, architecture=architecture)
    return json.dumps(payload, ensure_ascii=False, indent=4) + "\n"


def _cmake_quote(value: object) -> str:
    text = str(value)
    equals = ""
    while f"]{equals}]" in text:
        equals += "="
    return f"[{equals}[{text}]{equals}]"


def render_cmake_metadata(project: Project, *, architecture: str | None = None) -> str:
    if project.kind == "runtime":
        metadata = build_runtime_metadata(project)
        package = str(metadata["package"])
        lines = [
            "# Generated by lpm from lpm.toml. Do not edit.",
            "set(LILYGO_UI_PROJECT_KIND [[runtime]])",
            f"set(LILYGO_UI_PACKAGE_NAME {_cmake_quote(package)})",
            f"set(LILYGO_UI_EXECUTABLE_NAME {_cmake_quote(package)})",
            f"set(LILYGO_UI_CMAKE_PROJECT_NAME {_cmake_quote(package.replace('-', '_'))})",
        ]
        return "\n".join(lines) + "\n"
    manifest = build_publish_manifest(project, architecture=architecture)
    package = str(manifest["package"])
    variables: list[tuple[str, object]] = [
        ("LILYGO_UI_PACKAGE_NAME", package),
        ("LILYGO_UI_VERSION", manifest["version"]),
        ("LILYGO_UI_APP_ID", manifest["app_id"]),
        ("LILYGO_UI_APP_NAME", manifest["title"]),
        ("LILYGO_UI_APP_SUMMARY", manifest["summary"]),
        ("LILYGO_UI_APP_DESCRIPTION", manifest["description"]),
        ("LILYGO_UI_APP_LICENSE", manifest["license"]),
        ("LILYGO_UI_APP_HOMEPAGE", manifest["homepage"]),
        ("LILYGO_UI_APP_ICON", manifest["assets"]["icon"]),
        ("LILYGO_UI_APP_ORDER", manifest["launcher"]["order"]),
        (
            "LILYGO_UI_MIN_APPKIT_VERSION",
            manifest["compatibility"]["min_appkit_version"],
        ),
        ("LILYGO_UI_PACKAGE_CONTACT", manifest["authors"][0]["name"]),
        ("LILYGO_UI_EXECUTABLE_NAME", package),
        ("LILYGO_UI_CMAKE_PROJECT_NAME", package.replace("-", "_")),
    ]
    lines = ["# Generated by lpm from lpm.toml. Do not edit."]
    architectures = manifest["compatibility"]["architectures"]
    if len(architectures) == 1:
        variables.append(("LILYGO_UI_PACKAGE_ARCHITECTURE", architectures[0]))
    else:
        # Metadata is imported before project(), when the toolchain has not
        # loaded yet. Let the target's CMake packaging choose the architecture.
        lines.append("unset(LILYGO_UI_PACKAGE_ARCHITECTURE)")
    variables.append(("LILYGO_UI_PACKAGE_ARCHITECTURES", ";".join(architectures)))
    lines.extend(f"set({name} {_cmake_quote(value)})" for name, value in variables)
    categories = " ".join(_cmake_quote(value) for value in manifest["categories"])
    lines.append(f"set(LILYGO_UI_CATEGORIES {categories})")
    return "\n".join(lines) + "\n"
