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
RUNTIME_RELEASE_SCHEMA_URL = "https://raw.githubusercontent.com/LILYGO-UI/packages/main/runtime-release.schema.json"


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


def _release_record(
    client: GitHubClient,
    registry: str,
    prefix: str,
    version: str,
    architecture: str | None,
    ref: str,
) -> tuple[str, dict[str, Any] | None]:
    legacy_path = f"{prefix}/releases/{version}.json"
    legacy = client.file_json(registry, legacy_path, ref=ref)
    if architecture is None:
        if legacy is None:
            raise LpmError("specify --arch to select the release architecture")
        return legacy_path, legacy
    if (
        not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", architecture)
        or len(architecture) > 64
        or architecture == "source"
        or "any" in architecture.split("-")
    ):
        raise LpmError("invalid Debian architecture")
    path = f"{prefix}/releases/{version}_{architecture}.json"
    current = client.file_json(registry, path, ref=ref)
    if legacy is not None and legacy["artifact"]["architecture"] == architecture:
        if current is not None:
            raise LpmError("registry contains duplicate version/architecture records")
        return legacy_path, legacy
    return path, current


def _publication_artifacts(
    project: Project,
    *,
    package_path: Path | None,
    package_paths: list[Path] | None,
    architecture: str | None,
    all_architectures: bool,
    runtime: bool,
) -> list[DebianArtifact]:
    if package_path is not None and package_paths:
        raise LpmError("specify package_path or package_paths, not both")
    if all_architectures and architecture is not None:
        raise LpmError("--all-architectures cannot be combined with --arch")
    manifest = (
        None
        if runtime or project.kind == "runtime"
        else build_publish_manifest(project)
    )
    declared = manifest["compatibility"]["architectures"] if manifest else []
    if architecture is not None and declared and architecture not in declared:
        raise LpmError(f"architecture {architecture!r} is not declared by lpm.toml")
    paths = package_paths or ([package_path] if package_path is not None else [])
    if paths:
        artifacts = [inspect_debian_package(path) for path in paths]
    elif architecture is not None or all_architectures:
        if all_architectures and not declared:
            raise LpmError("runtime publication requires explicit Debian package paths")
        output = project.root / str(project.get("build", "package_dir", "dist"))
        candidates = [
            inspect_debian_package(path)
            for path in sorted(output.rglob("*.deb"))
            if path.is_file()
            and "_CPack_Packages" not in path.relative_to(output).parts
        ]
        selected = declared if all_architectures else [architecture]
        artifacts = []
        for arch in selected:
            matches = [
                item
                for item in candidates
                if item.architecture == arch
                and (
                    manifest is None
                    or (
                        item.package == manifest["package"]
                        and item.version == manifest["version"]
                    )
                )
            ]
            if len(matches) != 1:
                raise LpmError(
                    f"expected exactly one {arch} package for this project/version in {output}; pass package paths explicitly"
                )
            artifacts.extend(matches)
    else:
        if len(declared) > 1:
            raise LpmError(
                "multi-architecture projects require --arch, --all-architectures, or explicit package paths"
            )
        artifacts = [inspect_debian_package(_latest_package(project))]
    identities = {(item.package, item.version) for item in artifacts}
    arches = [item.architecture for item in artifacts]
    if len(identities) != 1 or len(set(arches)) != len(arches):
        raise LpmError(
            "publish requires one package/version and one artifact per architecture"
        )
    if "all" in arches and len(arches) > 1:
        raise LpmError(
            "cannot publish 'all' alongside concrete architectures for one version"
        )
    if architecture is not None and arches != [architecture]:
        raise LpmError(f"Debian Architecture must match --arch {architecture}")
    if all_architectures and set(arches) != set(declared):
        raise LpmError("packages must cover every declared architecture")
    for artifact in artifacts:
        if manifest is not None:
            _match_artifact(manifest, artifact)
        elif project.kind == "runtime" and artifact.package != project.get(
            "project", "package"
        ):
            raise LpmError(
                f"Debian Package is {artifact.package}, expected {project.get('project', 'package')}"
            )
        if not _REGISTRY_VERSION_PATTERN.fullmatch(artifact.version):
            raise LpmError(f"invalid Debian version: {artifact.version}")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9+.-]*[a-z0-9])?", artifact.package):
            raise LpmError("invalid Debian package name")
    return artifacts


def publish(
    project: Project,
    *,
    package_path: Path | None = None,
    package_paths: list[Path] | None = None,
    architecture: str | None = None,
    all_architectures: bool = False,
    force: bool = False,
    runtime: bool = False,
    registry: str = DEFAULT_REGISTRY,
    client: GitHubClient | None = None,
) -> Submission:
    artifacts = _publication_artifacts(
        project,
        package_path=package_path,
        package_paths=package_paths,
        architecture=architecture,
        all_architectures=all_architectures,
        runtime=runtime,
    )
    is_runtime = runtime or project.kind == "runtime"
    package_name, version = artifacts[0].package, artifacts[0].version
    prefix = f"{'runtimes' if is_runtime else 'apps'}/{package_name}"
    ownership_path = f"{prefix}/{'runtime' if is_runtime else 'app'}.json"
    manifest = None
    asset_files: dict[str, bytes] = {}
    if not is_runtime:
        manifest, asset_files = _registry_assets(
            project, build_publish_manifest(project)
        )
    client = client or GitHubClient.authenticated()
    login = client.current_user()
    base_branch, base_sha, base_tree = client.branch_base(registry)
    ownership = client.file_json(registry, ownership_path, ref=base_sha)
    ownership_file = (
        _runtime_file(ownership, package_name, login)
        if is_runtime
        else _app_file(ownership, package_name, str(manifest["app_id"]), login)
    )
    plans = []
    for artifact in artifacts:
        path, previous = _release_record(
            client, registry, prefix, version, artifact.architecture, base_sha
        )
        if previous is not None:
            if not force:
                hint = (
                    "; use lpm publish --force to replace this yanked version"
                    if previous.get("status") == "yanked"
                    else ""
                )
                raise LpmError(
                    f"{package_name} {version} {artifact.architecture} already exists in the registry{hint}"
                )
            if previous.get("status") != "yanked":
                raise LpmError(
                    f"{package_name} {version}: --force can only replace a yanked version; unpublish must be merged first"
                )
            if ownership is None:
                raise LpmError(
                    f"registry ownership record does not exist: {package_name}"
                )
            expected = {"package": package_name, "version": version}
            if manifest is not None:
                expected["app_id"] = manifest["app_id"]
            if any(
                previous["manifest"].get(key) != value
                for key, value in expected.items()
            ):
                raise LpmError("--force cannot change the existing release identity")
            if previous["artifact"]["architecture"] != artifact.architecture:
                raise LpmError(
                    "--force cannot change the existing release architecture"
                )
        plans.append((artifact, path, previous))

    fork = client.ensure_fork(registry, login)
    client.sync_fork(fork, branch=base_branch)
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-") or "version"
    files: dict[str, bytes] = dict(asset_files)
    staged = []
    notes = []
    for artifact, path, previous in plans:
        asset_name = f"sha256-{artifact.sha256}.deb"
        tag = f"lpm-staging-{package_name}-{safe_version}-{artifact.sha256[:12]}"
        source_url = _release_download_url(fork, tag, asset_name)
        central_tag = f"apt-pool-{package_name}"
        if is_runtime:
            registry_manifest = {
                "$schema": RUNTIME_RELEASE_SCHEMA_URL,
                "schema_version": 1,
                "package": package_name,
                "version": version,
                "compatibility": {"architectures": [artifact.architecture]},
            }
        else:
            registry_manifest = json.loads(json.dumps(manifest))
            registry_manifest["compatibility"]["architectures"] = [
                artifact.architecture
            ]
        record = {
            "$schema": RUNTIME_RELEASE_SCHEMA_URL if is_runtime else RELEASE_SCHEMA_URL,
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
                    "url": _release_download_url(registry, central_tag, asset_name),
                },
            },
        }
        files[path] = _json_bytes(record)
        staged.append((artifact, tag, asset_name, source_url))
        if previous is not None:
            notes.append(
                f"{artifact.architecture}: Previous SHA-256: `{previous['artifact']['sha256']}`; Replacement SHA-256: `{artifact.sha256}`. The previous content-addressed Debian asset is retained."
            )
    if ownership is None:
        files[ownership_path] = _json_bytes(ownership_file)
    operation = (
        "Republish"
        if any(previous is not None for _, _, previous in plans)
        else "Publish"
    )
    arch_label = ", ".join(item.architecture for item in artifacts)
    title = f"{operation} {package_name} {version} ({arch_label})"
    branch = (
        f"lpm/{operation.lower()}/{package_name}/{safe_version}-{secrets.token_hex(4)}"
    )
    commit_sha = client.commit_files(
        fork,
        branch=branch,
        parent_sha=base_sha,
        base_tree_sha=base_tree,
        files=files,
        message=title,
    )
    for artifact, tag, asset_name, source_url in staged:
        release = client.create_release(
            fork,
            tag=tag,
            target=commit_sha,
            title=f"LPM staging: {package_name} {version} ({artifact.architecture})",
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
    pr_url = client.create_pull_request(
        registry,
        title=title,
        body=(
            f"{operation} `{package_name}` version `{version}` for `{arch_label}`.\n\n"
            + "\n\n".join(notes)
            + f"\n\nSubmitted by `lpm` as `{login}`. The artifacts are staged in the submitter's public fork and must be copied by the trusted promotion workflow."
        ),
        head=f"{login}:{branch}",
        base=base_branch,
    )
    return Submission(pr_url, package_name, version, staged[0][3])


def unpublish(
    project: Project,
    *,
    version: str,
    reason: str,
    architecture: str | None = None,
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
            architecture=architecture,
            registry=registry,
            client=client,
        )
    project.require_application("unpublish")
    manifest = build_publish_manifest(project)
    package_name = str(manifest["package"])
    declared = manifest["compatibility"]["architectures"]
    if architecture is None and len(declared) > 1:
        raise LpmError("multi-architecture projects require --arch when unpublishing")
    architecture = architecture or declared[0]
    client = client or GitHubClient.authenticated()
    login = client.current_user()
    base_branch, base_sha, base_tree = client.branch_base(registry)
    app_path = f"apps/{package_name}/app.json"
    release_path, release = _release_record(
        client, registry, f"apps/{package_name}", version, architecture, base_sha
    )
    app = client.file_json(registry, app_path, ref=base_sha)
    _app_file(app, package_name, str(manifest["app_id"]), login)
    if release is None:
        raise LpmError(f"registry release does not exist: {package_name} {version}")
    if release.get("status") == "yanked":
        raise LpmError(f"{package_name} {version} is already unpublished")
    if release.get("status") != "published":
        raise LpmError(
            f"{package_name} {version} cannot be unpublished from its current state"
        )
    release = json.loads(json.dumps(release))
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
        message=f"Unpublish {package_name} {version} ({release['artifact']['architecture']})",
    )
    pr_url = client.create_pull_request(
        registry,
        title=f"Unpublish {package_name} {version} ({release['artifact']['architecture']})",
        body=(
            f"Yank `{package_name}` version `{version}`.\n\n"
            f"Reason: {reason.strip()}\n\nRequested by `{login}`."
        ),
        head=f"{login}:{branch}",
        base=base_branch,
    )
    return Submission(pr_url, package_name, version)


def _unpublish_runtime(
    project: Project,
    *,
    version: str,
    reason: str,
    architecture: str | None,
    registry: str,
    client: GitHubClient | None,
) -> Submission:
    package_name = str(project.get("project", "package", ""))
    client = client or GitHubClient.authenticated()
    login = client.current_user()
    base_branch, base_sha, base_tree = client.branch_base(registry)
    runtime_path = f"runtimes/{package_name}/runtime.json"
    release_path, release = _release_record(
        client, registry, f"runtimes/{package_name}", version, architecture, base_sha
    )
    runtime = client.file_json(registry, runtime_path, ref=base_sha)
    _runtime_file(runtime, package_name, login)
    if release is None:
        raise LpmError(f"registry release does not exist: {package_name} {version}")
    if release.get("status") == "yanked":
        raise LpmError(f"{package_name} {version} is already unpublished")
    if release.get("status") != "published":
        raise LpmError(
            f"{package_name} {version} cannot be unpublished from its current state"
        )
    release = json.loads(json.dumps(release))
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
        message=f"Unpublish {package_name} {version} ({release['artifact']['architecture']})",
    )
    pr_url = client.create_pull_request(
        registry,
        title=f"Unpublish {package_name} {version} ({release['artifact']['architecture']})",
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
        raise LpmError(
            f"registry identity does not match local runtime project: {package}"
        )
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
    packages = [
        path
        for path in output.rglob("*.deb")
        if path.is_file() and "_CPack_Packages" not in path.relative_to(output).parts
    ]
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
