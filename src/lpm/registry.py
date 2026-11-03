# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import Project
from .errors import LpmError
from .github import GitHubClient
from .metadata import build_publish_manifest
from .process import require_tool, run

DEFAULT_REGISTRY = "LILYGO-UI/packages"
MAX_ICON_BYTES = 1024 * 1024
MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
MAX_DEB_BYTES = 2 * 1024 * 1024 * 1024 - 1
_REGISTRY_VERSION_PATTERN = re.compile(
    r"(?:[0-9]+:)?[0-9][A-Za-z0-9.+~]*(?:-[A-Za-z0-9.+~]+)?"
)
APP_SCHEMA_URL = (
    "https://raw.githubusercontent.com/LILYGO-UI/packages/main/app.schema.json"
)
RELEASE_SCHEMA_URL = "https://raw.githubusercontent.com/LILYGO-UI/packages/main/registry-release.schema.json"
RUNTIME_SCHEMA_URL = (
    "https://raw.githubusercontent.com/LILYGO-UI/packages/main/runtime.schema.json"
)
RUNTIME_RELEASE_SCHEMA_URL = (
    "https://raw.githubusercontent.com/LILYGO-UI/packages/main/runtime-release.schema.json"
)


@dataclass(frozen=True)
class DebianArtifact:
    path: Path
    package: str
    version: str
    architecture: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Submission:
    pull_request_url: str
    package: str
    version: str
    source_url: str | None = None


def inspect_debian_package(path: Path) -> DebianArtifact:
    path = path.resolve()
    if not path.is_file() or path.suffix != ".deb":
        raise LpmError(f"Debian package does not exist: {path}")
    if path.stat().st_size > MAX_DEB_BYTES:
        raise LpmError("Debian package must be smaller than 2 GiB")
    require_tool("dpkg-deb")
    fields: list[str] = []
    for field_name in ("Package", "Version", "Architecture"):
        result = run(
            ["dpkg-deb", "--field", path, field_name],
            cwd=path.parent,
            capture=True,
        )
        value = (result.stdout or "").strip()
        if not value or "\n" in value:
            raise LpmError(f"dpkg-deb did not return a valid {field_name} field")
        fields.append(value)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return DebianArtifact(
        path=path,
        package=fields[0].strip(),
        version=fields[1].strip(),
        architecture=fields[2].strip(),
        size=path.stat().st_size,
        sha256=digest.hexdigest(),
    )


def publish(
    project: Project,
    *,
    package_path: Path | None = None,
    force: bool = False,
    runtime: bool = False,
    registry: str = DEFAULT_REGISTRY,
    client: GitHubClient | None = None,
) -> Submission:
    package_path = package_path.resolve() if package_path else _latest_package(project)
    artifact = inspect_debian_package(package_path)
    if runtime or project.kind == "runtime":
        return _publish_runtime(
            project,
            artifact,
            force=force,
            registry=registry,
            client=client,
        )
    project.require_application("publish")
    manifest = build_publish_manifest(project)
    _match_artifact(manifest, artifact)

    client = client or GitHubClient.authenticated()
    login = client.current_user()
    base_branch, base_sha, base_tree = client.branch_base(registry)
    package_name = str(manifest["package"])
    version = str(manifest["version"])
    app_path = f"apps/{package_name}/app.json"
    release_path = f"apps/{package_name}/releases/{version}.json"
    app = client.file_json(registry, app_path, ref=base_sha)
    previous = client.file_json(registry, release_path, ref=base_sha)
    if previous is not None:
        if not force:
            hint = (
                "; use lpm publish --force to replace this yanked version"
                if previous.get("status") == "yanked"
                else ""
            )
            raise LpmError(f"{package_name} {version} already exists in the registry{hint}")
        if previous.get("status") != "yanked":
            raise LpmError(
                f"{package_name} {version}: --force can only replace a yanked version; "
                "unpublish must be merged first"
            )
        if app is None:
            raise LpmError(f"registry ownership record does not exist: {package_name}")
    app_file = _app_file(app, package_name, str(manifest["app_id"]), login)
    if previous is not None:
        if any(
            previous["manifest"].get(key) != manifest[key]
            for key in ("package", "version", "app_id")
        ):
            raise LpmError("--force cannot change the existing release identity")
        if previous["artifact"]["architecture"] != artifact.architecture:
            raise LpmError("--force cannot change the existing release architecture")

    fork = client.ensure_fork(registry, login)
    client.sync_fork(fork, branch=base_branch)
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-") or "version"
    asset_name = f"sha256-{artifact.sha256}.deb"
    tag = f"lpm-staging-{package_name}-{safe_version}-{artifact.sha256[:12]}"
    source_url = _release_download_url(fork, tag, asset_name)
    central_tag = f"apt-pool-{package_name}"
    central_url = _release_download_url(registry, central_tag, asset_name)

    registry_manifest, asset_files = _registry_assets(project, manifest)
    release_record = {
        "$schema": RELEASE_SCHEMA_URL,
        "schema_version": 1,
        "status": "published",
        "submitted_by": login,
        "manifest": registry_manifest,
        "artifact": {
            "architecture": artifact.architecture,
            "size": artifact.size,
            "sha256": artifact.sha256,
            "media_type": "application/vnd.debian.binary-package",
            "source": {
                "repository": fork,
                "tag": tag,
                "asset": asset_name,
                "url": source_url,
            },
            "central": {
                "repository": registry,
                "tag": central_tag,
                "asset": asset_name,
                "url": central_url,
            },
        },
    }
    files = {
        release_path: _json_bytes(release_record),
        **asset_files,
    }
    if app is None:
        files[app_path] = _json_bytes(app_file)

    operation = "Republish" if previous is not None else "Publish"
    branch = f"lpm/{operation.lower()}/{package_name}/{safe_version}-{secrets.token_hex(4)}"
    commit_sha = client.commit_files(
        fork,
        branch=branch,
        parent_sha=base_sha,
        base_tree_sha=base_tree,
        files=files,
        message=f"{operation} {package_name} {version}",
    )
    release = client.create_release(
        fork,
        tag=tag,
        target=commit_sha,
        title=f"LPM staging: {package_name} {version}",
        body="Temporary public artifact used by the LILYGO packages review workflow.",
        prerelease=True,
    )
    uploaded_url = client.ensure_release_asset(
        fork,
        release,
        path=artifact.path,
        name=asset_name,
        content_type="application/vnd.debian.binary-package",
    )
    if uploaded_url != source_url:
        raise LpmError(
            "GitHub changed the staging asset URL unexpectedly; the submission was not opened"
        )
    replacement_note = (
        "Replace the previously yanked package without changing its version or architecture.\n\n"
        f"Previous SHA-256: `{previous['artifact']['sha256']}`\n\n"
        f"Replacement SHA-256: `{artifact.sha256}`\n\n"
        "The previous content-addressed Debian asset is retained.\n\n"
        if previous is not None
        else ""
    )
    pr_url = client.create_pull_request(
        registry,
        title=f"{operation} {package_name} {version}",
        body=(
            f"{operation} `{package_name}` version `{version}`.\n\n"
            f"{replacement_note}"
            f"Submitted by `lpm` as `{login}`. The artifact is staged in the submitter's "
            "public fork and must be copied by the trusted promotion workflow."
        ),
        head=f"{login}:{branch}",
        base=base_branch,
    )
    return Submission(pr_url, package_name, version, source_url)


def unpublish(
    project: Project,
    *,
    version: str,
    reason: str,
    registry: str = DEFAULT_REGISTRY,
    client: GitHubClient | None = None,
) -> Submission:
    if not _REGISTRY_VERSION_PATTERN.fullmatch(version):
        raise LpmError(f"invalid Debian version: {version}")
    if not 1 <= len(reason.strip()) <= 500:
        raise LpmError("unpublish reason must contain between 1 and 500 characters")
    if project.kind == "runtime":
        return _unpublish_runtime(
            project,
            version=version,
            reason=reason,
            registry=registry,
            client=client,
        )
    project.require_application("unpublish")
    manifest = build_publish_manifest(project)
    package_name = str(manifest["package"])
    client = client or GitHubClient.authenticated()
    login = client.current_user()
    base_branch, base_sha, base_tree = client.branch_base(registry)
    app_path = f"apps/{package_name}/app.json"
    release_path = f"apps/{package_name}/releases/{version}.json"
    app = client.file_json(registry, app_path, ref=base_branch)
    _app_file(app, package_name, str(manifest["app_id"]), login)
    release = client.file_json(registry, release_path, ref=base_branch)
    if release is None:
        raise LpmError(f"registry release does not exist: {package_name} {version}")
    if release.get("status") == "yanked":
        raise LpmError(f"{package_name} {version} is already unpublished")
    if release.get("status") != "published":
        raise LpmError(
            f"{package_name} {version} cannot be unpublished from its current state"
        )
    release["status"] = "yanked"
    release["yank"] = {"reason": reason.strip(), "requested_by": login}

    fork = client.ensure_fork(registry, login)
    client.sync_fork(fork, branch=base_branch)
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-") or "version"
    branch = f"lpm/unpublish/{package_name}/{safe_version}-{secrets.token_hex(4)}"
    client.commit_files(
        fork,
        branch=branch,
        parent_sha=base_sha,
        base_tree_sha=base_tree,
        files={release_path: _json_bytes(release)},
        message=f"Unpublish {package_name} {version}",
    )
    pr_url = client.create_pull_request(
        registry,
        title=f"Unpublish {package_name} {version}",
        body=(
            f"Yank `{package_name}` version `{version}`.\n\n"
            f"Reason: {reason.strip()}\n\nRequested by `{login}`."
        ),
        head=f"{login}:{branch}",
        base=base_branch,
    )
    return Submission(pr_url, package_name, version)


def _publish_runtime(
    project: Project,
    artifact: DebianArtifact,
    *,
    force: bool,
    registry: str,
    client: GitHubClient | None,
) -> Submission:
    configured_package = str(project.get("project", "package", ""))
    package_name = artifact.package if project.kind != "runtime" else configured_package
    if project.kind == "runtime" and artifact.package != package_name:
        raise LpmError(
            f"Debian Package is {artifact.package}, expected {package_name}"
        )

    client = client or GitHubClient.authenticated()
    login = client.current_user()
    base_branch, base_sha, base_tree = client.branch_base(registry)
    runtime_path = f"runtimes/{package_name}/runtime.json"
    release_path = f"runtimes/{package_name}/releases/{artifact.version}.json"
    runtime = client.file_json(registry, runtime_path, ref=base_sha)
    previous = client.file_json(registry, release_path, ref=base_sha)
    if previous is not None:
        if not force:
            hint = (
                "; use lpm publish --force to replace this yanked version"
                if previous.get("status") == "yanked"
                else ""
            )
            raise LpmError(f"{package_name} {artifact.version} already exists in the registry{hint}")
        if previous.get("status") != "yanked":
            raise LpmError(
                f"{package_name} {artifact.version}: --force can only replace a yanked version; "
                "unpublish must be merged first"
            )
        if runtime is None:
            raise LpmError(f"registry ownership record does not exist: {package_name}")
        if previous["manifest"].get("package") != package_name or previous["manifest"].get("version") != artifact.version:
            raise LpmError("--force cannot change the existing release identity")
        if previous["artifact"]["architecture"] != artifact.architecture:
            raise LpmError("--force cannot change the existing release architecture")
    runtime_file = _runtime_file(runtime, package_name, login)

    fork = client.ensure_fork(registry, login)
    client.sync_fork(fork, branch=base_branch)
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", artifact.version).strip("-") or "version"
    asset_name = f"sha256-{artifact.sha256}.deb"
    tag = f"lpm-staging-{package_name}-{safe_version}-{artifact.sha256[:12]}"
    source_url = _release_download_url(fork, tag, asset_name)
    central_tag = f"apt-pool-{package_name}"
    central_url = _release_download_url(registry, central_tag, asset_name)
    manifest: dict[str, Any] = {
        "$schema": RUNTIME_RELEASE_SCHEMA_URL,
        "schema_version": 1,
        "package": package_name,
        "version": artifact.version,
        "compatibility": {"architectures": [artifact.architecture]},
    }
    release_record = {
        "$schema": RUNTIME_RELEASE_SCHEMA_URL,
        "schema_version": 1,
        "status": "published",
        "submitted_by": login,
        "manifest": manifest,
        "artifact": {
            "architecture": artifact.architecture,
            "size": artifact.size,
            "sha256": artifact.sha256,
            "media_type": "application/vnd.debian.binary-package",
            "source": {
                "repository": fork,
                "tag": tag,
                "asset": asset_name,
                "url": source_url,
            },
            "central": {
                "repository": registry,
                "tag": central_tag,
                "asset": asset_name,
                "url": central_url,
            },
        },
    }
    files = {release_path: _json_bytes(release_record)}
    if runtime is None:
        files[runtime_path] = _json_bytes(runtime_file)

    operation = "Republish" if previous is not None else "Publish"
    branch = f"lpm/{operation.lower()}/{package_name}/{safe_version}-{secrets.token_hex(4)}"
    commit_sha = client.commit_files(
        fork,
        branch=branch,
        parent_sha=base_sha,
        base_tree_sha=base_tree,
        files=files,
        message=f"{operation} {package_name} {artifact.version}",
    )
    release = client.create_release(
        fork,
        tag=tag,
        target=commit_sha,
        title=f"LPM staging: {package_name} {artifact.version}",
        body="Temporary public artifact used by the LILYGO packages review workflow.",
        prerelease=True,
    )
    uploaded_url = client.ensure_release_asset(
        fork,
        release,
        path=artifact.path,
        name=asset_name,
        content_type="application/vnd.debian.binary-package",
    )
    if uploaded_url != source_url:
        raise LpmError(
            "GitHub changed the staging asset URL unexpectedly; the submission was not opened"
        )
    replacement_note = (
        "Replace the previously yanked package without changing its version or architecture.\n\n"
        f"Previous SHA-256: `{previous['artifact']['sha256']}`\n\n"
        f"Replacement SHA-256: `{artifact.sha256}`\n\n"
        "The previous content-addressed Debian asset is retained.\n\n"
        if previous is not None
        else ""
    )
    pr_url = client.create_pull_request(
        registry,
        title=f"{operation} {package_name} {artifact.version}",
        body=(
            f"{operation} runtime `{package_name}` version `{artifact.version}`.\n\n"
            f"{replacement_note}"
            f"Submitted by `lpm` as `{login}`. The artifact is staged in the submitter's "
            "public fork and must be copied by the trusted promotion workflow."
        ),
        head=f"{login}:{branch}",
        base=base_branch,
    )
    return Submission(pr_url, package_name, artifact.version, source_url)


def _unpublish_runtime(
    project: Project,
    *,
    version: str,
    reason: str,
    registry: str,
    client: GitHubClient | None,
) -> Submission:
    package_name = str(project.get("project", "package", ""))
    client = client or GitHubClient.authenticated()
    login = client.current_user()
    base_branch, base_sha, base_tree = client.branch_base(registry)
    runtime_path = f"runtimes/{package_name}/runtime.json"
    release_path = f"runtimes/{package_name}/releases/{version}.json"
    runtime = client.file_json(registry, runtime_path, ref=base_branch)
    _runtime_file(runtime, package_name, login)
    release = client.file_json(registry, release_path, ref=base_branch)
    if release is None:
        raise LpmError(f"registry release does not exist: {package_name} {version}")
    if release.get("status") == "yanked":
        raise LpmError(f"{package_name} {version} is already unpublished")
    if release.get("status") != "published":
        raise LpmError(
            f"{package_name} {version} cannot be unpublished from its current state"
        )
    release["status"] = "yanked"
    release["yank"] = {"reason": reason.strip(), "requested_by": login}
    fork = client.ensure_fork(registry, login)
    client.sync_fork(fork, branch=base_branch)
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-") or "version"
    branch = f"lpm/unpublish/{package_name}/{safe_version}-{secrets.token_hex(4)}"
    client.commit_files(
        fork,
        branch=branch,
        parent_sha=base_sha,
        base_tree_sha=base_tree,
        files={release_path: _json_bytes(release)},
        message=f"Unpublish {package_name} {version}",
    )
    pr_url = client.create_pull_request(
        registry,
        title=f"Unpublish {package_name} {version}",
        body=(
            f"Yank runtime `{package_name}` version `{version}`.\n\n"
            f"Reason: {reason.strip()}\n\nRequested by `{login}`."
        ),
        head=f"{login}:{branch}",
        base=base_branch,
    )
    return Submission(pr_url, package_name, version)


def _app_file(
    current: dict[str, Any] | None,
    package: str,
    app_id: str,
    login: str,
) -> dict[str, Any]:
    if current is None:
        return {
            "$schema": APP_SCHEMA_URL,
            "schema_version": 1,
            "package": package,
            "app_id": app_id,
            "owners": [login],
        }
    if current.get("package") != package or current.get("app_id") != app_id:
        raise LpmError(f"registry identity does not match local project: {package}")
    owners = current.get("owners")
    if not isinstance(owners, list) or login.lower() not in {
        str(owner).lower() for owner in owners
    }:
        raise LpmError(f"GitHub user {login} is not an owner of {package}")
    return current


def _runtime_file(
    current: dict[str, Any] | None,
    package: str,
    login: str,
) -> dict[str, Any]:
    if current is None:
        return {
            "$schema": RUNTIME_SCHEMA_URL,
            "schema_version": 1,
            "package": package,
            "owners": [login],
        }
    if current.get("package") != package:
        raise LpmError(f"registry identity does not match local runtime project: {package}")
    owners = current.get("owners")
    if not isinstance(owners, list) or login.lower() not in {
        str(owner).lower() for owner in owners
    }:
        raise LpmError(f"GitHub user {login} is not an owner of {package}")
    return current


def _match_artifact(manifest: dict[str, Any], artifact: DebianArtifact) -> None:
    expected_architectures = manifest["compatibility"]["architectures"]
    if artifact.package != manifest["package"]:
        raise LpmError(
            f"Debian Package is {artifact.package}, expected {manifest['package']}"
        )
    if artifact.version != manifest["version"]:
        raise LpmError(
            f"Debian Version is {artifact.version}, expected {manifest['version']}"
        )
    if artifact.architecture not in expected_architectures:
        raise LpmError(
            f"Debian Architecture is {artifact.architecture}, expected "
            f"{', '.join(expected_architectures)}"
        )


def _registry_assets(
    project: Project, manifest: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, bytes]]:
    normalized = json.loads(json.dumps(manifest))
    files: dict[str, bytes] = {}

    def add(relative: str, *, maximum: int) -> str:
        source = (project.root / relative).resolve()
        try:
            source.relative_to(project.root.resolve())
        except ValueError as exc:
            raise LpmError(
                f"project asset resolves outside the project: {relative}"
            ) from exc
        content = source.read_bytes()
        if len(content) > maximum:
            raise LpmError(f"project asset exceeds {maximum} bytes: {relative}")
        digest = hashlib.sha256(content).hexdigest()
        extension = source.suffix.lower()
        media_type = mimetypes.guess_type(source.name)[0]
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise LpmError(f"unsupported project asset type: {relative}")
        registry_path = f"assets/sha256/{digest[:2]}/{digest}{extension}"
        files.setdefault(registry_path, content)
        return registry_path

    normalized["assets"]["icon"] = add(
        str(manifest["assets"]["icon"]), maximum=MAX_ICON_BYTES
    )
    for source, target in zip(
        manifest["assets"]["screenshots"],
        normalized["assets"]["screenshots"],
        strict=True,
    ):
        target["path"] = add(str(source["path"]), maximum=MAX_SCREENSHOT_BYTES)
    return normalized, files


def _latest_package(project: Project) -> Path:
    output = project.root / str(project.get("build", "package_dir", "dist"))
    packages = [path for path in output.rglob("*.deb") if path.is_file()]
    if not packages:
        raise LpmError(f"no Debian package found under {output}; run lpm pack first")
    return max(packages, key=lambda path: path.stat().st_mtime).resolve()


def _release_download_url(repository: str, tag: str, asset: str) -> str:
    owner, name = repository.split("/", 1)
    return (
        f"https://github.com/{quote(owner, safe='')}/{quote(name, safe='')}/releases/download/"
        f"{quote(tag, safe='')}/{quote(asset, safe='')}"
    )


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
