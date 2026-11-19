# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import (
    DEFAULT_DEPLOY_HOST,
    DEFAULT_DEPLOY_PORT,
    DEFAULT_DEPLOY_USER,
    Project,
    load_toml,
    pascal_case,
    render_config,
    validate_slug,
    validate_version,
)
from .errors import LpmError
from .metadata import (
    build_publish_manifest,
    render_cmake_metadata,
    render_publish_json,
)
from .process import (
    is_running,
    process_matches,
    require_tool,
    run,
    spawn,
    stop_process_group,
)
from .script_workflow import pack_script, test_script

DEFAULT_TEMPLATE = "https://github.com/LILYGO-UI/template.git"
_COPY_IGNORE = shutil.ignore_patterns(".git", "build", "dist", ".cache", ".lpm")


def initialize(
    target: Path,
    *,
    name: str,
    version: str,
    description: str,
    template: str = DEFAULT_TEMPLATE,
) -> Path:
    name = validate_slug(name)
    version = validate_version(version)
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise LpmError(f"target directory is not empty: {target}")

    with tempfile.TemporaryDirectory(prefix=".lpm-init-", dir=target.parent) as temp_name:
        temp_dir = Path(temp_name)
        source = _fetch_template(template, temp_dir)
        staging = temp_dir / "project"
        staging.mkdir()
        for item in source.iterdir():
            if item.name in {".git", "build", "dist", ".cache", ".lpm"}:
                continue
            destination = staging / item.name
            if item.is_dir():
                shutil.copytree(item, destination, symlinks=True, ignore=_COPY_IGNORE)
            else:
                shutil.copy2(item, destination, follow_symlinks=False)
        _customize_template(staging, name=name, version=version, description=description)
        for item in staging.iterdir():
            destination = target / item.name
            if item.is_dir():
                shutil.copytree(item, destination, symlinks=True)
            else:
                shutil.copy2(item, destination, follow_symlinks=False)
    return target


def _fetch_template(template: str, temp_dir: Path) -> Path:
    parsed = urlparse(template)
    local = Path(template).expanduser()
    if parsed.scheme == "file":
        local = Path(parsed.path)
    if parsed.scheme in {"", "file"} and local.is_dir():
        return local.resolve()

    require_tool("git")
    destination = temp_dir / "template"
    run(
        ["git", "clone", "--recurse-submodules", "--depth", "1", template, destination],
        cwd=temp_dir,
    )
    return destination


def _customize_template(target: Path, *, name: str, version: str, description: str) -> None:
    config_path = target / "lpm.toml"
    try:
        config = load_toml(config_path)
        project = config["project"]
    except (LpmError, KeyError, TypeError) as exc:
        raise LpmError(f"template has no valid project metadata: {config_path}") from exc
    if not isinstance(project, dict):
        raise LpmError(f"template project metadata must be a table: {config_path}")

    title = pascal_case(name)
    application_description = description or f"{title} application for LILYGO UI"
    project.update(
        {
            "package": f"lilygo-ui-{name}",
            "version": version,
            "app_id": f"cc.lilygo.ui.{title}",
            "title": title,
            "summary": application_description,
            "description": application_description,
        }
    )
    config["deploy"] = {
        "host": DEFAULT_DEPLOY_HOST,
        "user": DEFAULT_DEPLOY_USER,
        "port": DEFAULT_DEPLOY_PORT,
    }
    build_publish_manifest(Project(target, config))
    config_path.write_text(render_config(config), encoding="utf-8")


def generate_metadata(project: Project, *, output_format: str, architecture: str | None = None) -> str:
    if output_format == "cmake":
        return render_cmake_metadata(project, architecture=architecture)
    if output_format == "publish-json":
        return render_publish_json(project, architecture=architecture)
    raise LpmError(f"unsupported metadata format: {output_format}")


def _cmake_lpm_executable(project: Project) -> Path:
    entrypoint = Path(os.path.abspath(sys.argv[0]))
    if (
        entrypoint.name in {"lpm", "lpm.exe"}
        and entrypoint.is_file()
        and os.access(entrypoint, os.X_OK)
    ):
        return entrypoint

    # python -m lpm and callers importing this module have no console script.
    # Keep CMake on this interpreter and package instead of another lpm on PATH.
    launcher = project.state_dir / "cmake-lpm"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    source_root = str(Path(__file__).resolve().parent.parent)
    code = (
        f"import sys; sys.path.insert(0, {source_root!r}); "
        "from lpm.cli import main; raise SystemExit(main())"
    )
    launcher.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(code)} \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher.absolute()


def configure_and_build(project: Project, preset: str, *, configure_args: tuple[str, ...] = ()) -> None:
    require_tool("cmake")
    configure = ["cmake", "--preset", preset, f"-DLPM_EXECUTABLE:FILEPATH={_cmake_lpm_executable(project)}"]
    configure.extend(configure_args)
    run(configure, cwd=project.root)
    command = ["cmake", "--build", "--preset", preset]
    # Bare --parallel overrides CMake's environment limit with the build
    # tool's default (unlimited jobs for Make). Let CMake read the caller's
    # limit itself when it is supplied, including its documented empty value.
    if "CMAKE_BUILD_PARALLEL_LEVEL" not in os.environ:
        command.append("--parallel")
    run(command, cwd=project.root)


def start(
    project: Project,
    *,
    preset: str | None = None,
    executable: str | None = None,
    build: bool = True,
    foreground: bool = False,
    arguments: list[str] | None = None,
) -> Path | int:
    project.require_application("start")
    if project.build_backend != "cmake":
        raise LpmError("lpm start requires the cmake build backend")
    preset = preset or str(project.get("build", "host_preset", "host-simulator"))
    prepare_command = project.run_prepare_command()
    configured_environment = project.run_environment()
    runtime_environment = _runtime_environment(project, configured_environment)
    if build:
        configure_and_build(project, preset)
    binary = resolve_executable(project, preset=preset, executable=executable)
    command = [str(binary), *(arguments or [])]
    if foreground:
        _run_prepare(project, prepare_command, runtime_environment)
        if runtime_environment is None:
            return run(command, cwd=project.root).returncode
        return run(command, cwd=project.root, env=runtime_environment).returncode

    project.state_dir.mkdir(parents=True, exist_ok=True)
    pid_path = project.state_dir / "simulator.json"
    old_state = _read_state(pid_path)
    if old_state:
        try:
            old_pid = int(old_state["pid"])
            executable_value = str(old_state["executable"])
            old_executable = Path(executable_value).resolve()
            state_is_valid = bool(executable_value) and old_state.get("project") == str(project.root)
        except (KeyError, TypeError, ValueError):
            old_pid = 0
            old_executable = Path("/")
            state_is_valid = False
        if state_is_valid and is_running(old_pid) and process_matches(old_pid, old_executable):
            raise LpmError(f"simulator is already running with PID {old_pid}")
        pid_path.unlink(missing_ok=True)

    _run_prepare(project, prepare_command, runtime_environment)
    log_path = project.state_dir / "simulator.log"
    with log_path.open("ab") as log:
        if runtime_environment is None:
            process = spawn(command, cwd=project.root, log=log)
        else:
            process = spawn(command, cwd=project.root, log=log, env=runtime_environment)
    time.sleep(0.1)
    if process.poll() is not None:
        raise LpmError(f"simulator exited immediately; inspect {log_path}")
    state = {
        "pid": process.pid,
        "executable": str(binary),
        "project": str(project.root),
        "started_at": time.time(),
    }
    pid_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return log_path


def _run_prepare(
    project: Project,
    command: list[str] | None,
    environment: dict[str, str] | None,
) -> None:
    if not command:
        return
    expanded = _expand_prepare_command(project, command)
    if environment is None:
        run(expanded, cwd=project.root)
    else:
        run(expanded, cwd=project.root, env=environment)


def _runtime_environment(project: Project, configured: dict[str, str]) -> dict[str, str] | None:
    """Merge project defaults with the invoking shell environment.

    Returning ``None`` when no project values are configured preserves the
    inherited environment and avoids changing the subprocess call shape for
    legacy projects.  Explicit shell values always win over project defaults.
    """
    if not configured:
        return None
    environment = os.environ.copy()
    substitutions = {
        "{python}": sys.executable,
        "{project}": str(project.root.resolve()),
    }
    for key, value in configured.items():
        if key not in environment:
            expanded = value
            for placeholder, replacement in substitutions.items():
                expanded = expanded.replace(placeholder, replacement)
            environment[key] = expanded
    return environment


def _expand_prepare_command(project: Project, command: list[str]) -> list[str]:
    substitutions = {
        "{python}": sys.executable,
        "{project}": str(project.root.resolve()),
    }
    # Placeholder expansion is argument based; embedded text remains a
    # literal so the command never gains shell-like interpolation semantics.
    return [substitutions.get(argument, argument) for argument in command]


def resolve_executable(project: Project, *, preset: str, executable: str | None) -> Path:
    configured = (
        executable
        or project.get("project", "package")
        or project.get("project", "executable")
        or _identity_executable(project.root)
    )
    build_dir = project.root / "build" / preset
    if configured:
        candidate = Path(str(configured)).expanduser()
        if not candidate.is_absolute():
            candidate = build_dir / candidate
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
        matches = [path for path in build_dir.rglob(Path(str(configured)).name) if path.is_file()]
        executable_matches = [path for path in matches if os.access(path, os.X_OK)]
        if len(executable_matches) == 1:
            return executable_matches[0].resolve()
        raise LpmError(f"simulator executable was not found: {candidate}")

    raise LpmError(
        "cannot determine the simulator executable; set project.package in lpm.toml "
        "or pass --executable"
    )


def _identity_executable(root: Path) -> str | None:
    path = root / "cmake" / "AppIdentity.cmake"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^set\(LILYGO_UI_COMPONENT_SLUG\s+"([^"]+)"\)', content, re.MULTILINE)
    return f"lilygo-ui-{match.group(1)}" if match else None


def _read_state(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def stop(project: Project, *, timeout: float = 5.0) -> bool:
    project.require_application("stop")
    pid_path = project.state_dir / "simulator.json"
    state = _read_state(pid_path)
    if not state:
        return False
    try:
        pid = int(state["pid"])
        executable = Path(str(state["executable"])).resolve()
    except (KeyError, TypeError, ValueError):
        raise LpmError(f"invalid simulator state file: {pid_path}") from None
    if state.get("project") != str(project.root):
        raise LpmError(f"simulator state belongs to another project: {pid_path}")
    if not is_running(pid):
        pid_path.unlink(missing_ok=True)
        return False
    if not process_matches(pid, executable):
        raise LpmError(f"PID {pid} no longer belongs to this simulator; refusing to signal it")
    stopped = stop_process_group(pid, timeout)
    pid_path.unlink(missing_ok=True)
    return stopped


def test(project: Project, *, preset: str | None = None, report: Path | None = None) -> Path:
    if project.build_backend == "script":
        return test_script(project, preset=preset, report=report)
    preset = preset or str(project.get("build", "host_preset", "host-simulator"))
    configure_and_build(project, preset)
    require_tool("ctest")
    report = (report or project.state_dir / "test-results.xml").resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    run(
        ["ctest", "--preset", preset, "--output-on-failure", "--output-junit", report],
        cwd=project.root,
    )
    return report


def pack(
    project: Project, *, preset: str | None = None, output: Path | None = None,
    architecture: str | None = None, all_architectures: bool = False,
) -> list[Path]:
    if all_architectures and (architecture is not None or preset is not None):
        raise LpmError("--all-architectures cannot be combined with --arch or --preset")
    presets = project.cross_presets()
    if project.build_backend == "script":
        if architecture is not None or all_architectures:
            raise LpmError("--arch and --all-architectures require the cmake build backend")
        return pack_script(project, preset=preset, output=output)
    # Existing single-architecture projects only define cross_preset. Allow
    # target selection without requiring them to add a redundant mapping.
    declared = project.get("project", "compatibility", {}).get("architectures", [])
    if not presets and len(declared) == 1 and (architecture is not None or all_architectures):
        presets = {declared[0]: str(project.get("build", "cross_preset", "cm0-cross"))}
    if all_architectures:
        architectures = (
            build_publish_manifest(project)["compatibility"]["architectures"]
            if project.kind == "application" else list(presets)
        )
        if not architectures or any(arch not in presets for arch in architectures):
            raise LpmError("build.cross_presets must define every architecture before packing all architectures")
    else:
        if architecture is None:
            selected = preset or str(project.get("build", "cross_preset", "cm0-cross"))
            architecture = next((arch for arch, value in presets.items() if value == selected), None)
        architectures = [architecture] if architecture is not None else []
    if not architectures:
        declared = project.get("project", "compatibility", {}).get("architectures", [])
        if len(declared) > 1:
            raise LpmError("multi-architecture projects require a matching build.cross_presets entry")
        return _pack_cmake(project, preset=preset, output=output)

    for arch in architectures:
        if arch not in presets:
            raise LpmError(f"build.cross_presets has no preset for {arch!r}")
        if preset is not None and preset != presets[arch]:
            raise LpmError(f"--preset does not match the configured preset for {arch}")
        if project.kind == "application":
            build_publish_manifest(project, architecture=arch)

    from .registry import inspect_debian_package

    destination = (output or project.root / str(project.get("build", "package_dir", "dist"))).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    packages: list[Path] = []
    # Isolate each invocation: timestamp scans of a shared dist directory can
    # otherwise pick up the previous architecture's package.
    for arch in architectures:
        with tempfile.TemporaryDirectory(prefix="lpm-pack-") as temporary:
            produced = _pack_cmake(project, preset=presets[arch], output=Path(temporary))
            artifacts = [inspect_debian_package(path) for path in produced]
            for artifact in artifacts:
                if artifact.architecture != arch:
                    raise LpmError(f"Debian Architecture is {artifact.architecture}, expected {arch}")
                if project.kind == "application":
                    manifest = build_publish_manifest(project, architecture=arch)
                    if artifact.package != manifest["package"] or artifact.version != manifest["version"]:
                        raise LpmError("generated Debian package identity does not match lpm.toml")
            for artifact in artifacts:
                target = destination / f"{artifact.package}_{artifact.version}_{artifact.architecture}.deb"
                shutil.copy2(artifact.path, target)
                packages.append(target)
    return packages


def _pack_cmake(project: Project, *, preset: str | None, output: Path | None) -> list[Path]:
    preset = preset or str(project.get("build", "cross_preset", "cm0-cross"))
    if preset == "k230-debian" or preset == project.cross_presets().get("riscv64"):
        from .k230_sdk import prepare_pack

        options = prepare_pack(project.root, project.root / "build" / preset)
        configure_and_build(project, preset, configure_args=options)
    else:
        configure_and_build(project, preset)
    require_tool("cpack")
    output = (output or project.root / str(project.get("build", "package_dir", "dist"))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = project.root / "build" / preset / "CPackConfig.cmake"
    if not config.is_file():
        raise LpmError(f"CPack configuration was not generated: {config}")
    started = time.time() - 1.0
    run(["cpack", "--config", config, "-B", output], cwd=project.root)
    packages = sorted(
        (
            path.resolve()
            for path in output.rglob("*.deb")
            if path.stat().st_mtime >= started and "_CPack_Packages" not in path.parts
        ),
        key=lambda path: path.stat().st_mtime,
    )
    if not packages:
        raise LpmError(f"CPack completed but no Debian package was produced under {output}")
    return packages


def deploy(
    project: Project,
    *,
    package: Path | None = None,
    architecture: str | None = None,
    host: str | None = None,
    target: str | None = None,
    user: str | None = None,
    port: int | None = None,
    sudo: bool = True,
) -> Path:
    project.build_backend
    if target is not None:
        host = target
        user = user if user is not None else ""
    else:
        host = host or project.get("deploy", "host") or DEFAULT_DEPLOY_HOST
        if user is None:
            user = project.get("deploy", "user") or DEFAULT_DEPLOY_USER
        if port is None:
            try:
                configured_port = project.get("deploy", "port", DEFAULT_DEPLOY_PORT)
                port = int(configured_port if configured_port not in (None, "") else DEFAULT_DEPLOY_PORT)
            except (TypeError, ValueError):
                raise LpmError("deploy port must be an integer") from None
    _validate_remote(host, user, port)
    declared = project.get("project", "compatibility", {}).get("architectures", [])
    if architecture is not None and declared and architecture not in declared:
        raise LpmError(f"architecture {architecture!r} is not declared by lpm.toml")
    explicit_package = package is not None
    package = package.resolve() if package else _latest_package(project, architecture=architecture)
    if not package.is_file() or package.suffix != ".deb":
        raise LpmError(f"Debian package does not exist: {package}")
    if explicit_package and architecture is not None:
        from .registry import inspect_debian_package

        artifact = inspect_debian_package(package)
        if artifact.architecture != architecture:
            raise LpmError(
                f"Debian Architecture is {artifact.architecture}, expected {architecture}"
            )

    require_tool("scp")
    require_tool("ssh")
    destination = f"{user}@{host}" if user else str(host)
    package_id = hashlib.sha256(str(package).encode("utf-8")).hexdigest()[:12]
    remote_path = f"/tmp/lpm-{package_id}.deb"
    ssh_config = project.state_dir / "ssh_config"
    common_options: list[str | Path] = ["-F", ssh_config] if ssh_config.is_file() else []
    scp_options: list[str | Path] = [*common_options]
    ssh_options: list[str | Path] = [*common_options]
    if port is not None:
        scp_options.extend(["-P", str(port)])
        ssh_options.extend(["-p", str(port)])
    if sudo:
        # sudo prompts for a password through the remote terminal.  An SSH
        # command does not allocate one by default, so force a pty for the
        # interactive install while keeping --no-sudo suitable for pipes and
        # other non-interactive callers.
        ssh_options.append("-tt")
    run(["scp", *scp_options, package, f"{destination}:{remote_path}"], cwd=project.root)
    # Install the local package through APT so dependencies can be resolved
    # from the target's configured repositories.
    install = ["apt-get", "install", "--reinstall", "--yes", remote_path]
    if sudo:
        install = ["sudo", "--", *install]
    remote_command = "set -e; " + shlex.join(install) + "; " + shlex.join(
        ["rm", "-f", "--", remote_path]
    )
    run(["ssh", *ssh_options, destination, remote_command], cwd=project.root)
    return package


def _latest_package(project: Project, *, architecture: str | None = None) -> Path:
    if architecture is None and len(project.get("project", "compatibility", {}).get("architectures", [])) > 1:
        raise LpmError(
            "multi-architecture deployment requires --arch or an explicit Debian package path"
        )
    output = project.root / str(project.get("build", "package_dir", "dist"))
    packages = [
        path for path in output.rglob("*.deb")
        if path.is_file() and "_CPack_Packages" not in path.relative_to(output).parts
    ]
    if architecture is not None:
        from .registry import inspect_debian_package

        name = project.get("project", "package")
        version = project.get("project", "version")
        artifacts = [inspect_debian_package(path) for path in packages]
        packages = [
            item.path for item in artifacts
            if item.architecture == architecture
            and (not name or item.package == name)
            and (not version or item.version == version)
        ]
        if not packages:
            raise LpmError(
                f"no {architecture} Debian package for this project/version under {output}; "
                f"run lpm pack --arch {architecture} first or pass a package path explicitly"
            )
    if not packages:
        raise LpmError(f"no Debian package found under {output}; run lpm pack first")
    return max(packages, key=lambda path: path.stat().st_mtime).resolve()


def _validate_remote(host: object, user: object, port: int | None) -> None:
    remote_pattern = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?")
    if not isinstance(host, str) or not remote_pattern.fullmatch(host):
        raise LpmError("deploy host is required and must be a hostname or IPv4 address")
    if not isinstance(user, str) or (user and not remote_pattern.fullmatch(user)):
        raise LpmError("deploy user contains unsupported characters")
    if port is not None and not 1 <= port <= 65535:
        raise LpmError("deploy port must be between 1 and 65535")
