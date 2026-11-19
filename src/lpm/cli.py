# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__
from .commands import (
    DEFAULT_TEMPLATE,
    deploy,
    generate_metadata,
    initialize,
    pack,
    start,
    stop,
    test,
)
from .config import find_project
from .errors import LpmError
from .github import github_login
from .registry import publish, unpublish
from .targets import TARGET_ARCHITECTURES, resolve_target, save_target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lpm",
        description="Build, test, package, and deploy LILYGO UI applications and runtimes.",
    )
    parser.add_argument("--version", action="version", version=f"lpm {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="create an application from the LILYGO UI template"
    )
    init_parser.add_argument(
        "path", nargs="?", default=".", type=Path, help="empty target directory"
    )
    init_parser.add_argument("--name", help="lowercase kebab-case component name")
    init_parser.add_argument(
        "--app-version", default=None, help="initial application version"
    )
    init_parser.add_argument(
        "--description", default=None, help="application description"
    )
    init_parser.add_argument(
        "--template", default=DEFAULT_TEMPLATE, help="template URL or local directory"
    )
    init_parser.add_argument(
        "-y", "--yes", action="store_true", help="accept inferred defaults"
    )
    init_parser.set_defaults(handler=_handle_init)

    target_parser = subparsers.add_parser(
        "set-target", help="save the default platform for this project"
    )
    target_parser.add_argument(
        "target", choices=tuple(TARGET_ARCHITECTURES),
        help="cm0 (arm64), k230 (riscv64), or all declared architectures",
    )
    target_parser.set_defaults(handler=_handle_set_target)

    start_parser = subparsers.add_parser(
        "start", help="build and start the host simulator"
    )
    start_parser.add_argument("--preset", help="CMake configure/build preset")
    start_parser.add_argument("--executable", help="simulator executable name or path")
    start_parser.add_argument(
        "--no-build", action="store_true", help="run the existing binary"
    )
    start_parser.add_argument(
        "--foreground", action="store_true", help="keep the simulator attached"
    )
    start_parser.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        help="arguments after -- go to the simulator",
    )
    start_parser.set_defaults(handler=_handle_start)

    stop_parser = subparsers.add_parser(
        "stop", help="stop the background host simulator"
    )
    stop_parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=5.0,
        help="seconds before forced stop",
    )
    stop_parser.set_defaults(handler=_handle_stop)

    test_parser = subparsers.add_parser("test", help="build and run host tests")
    test_parser.add_argument("--preset", help="CMake test preset")
    test_parser.add_argument("--report", type=Path, help="JUnit XML output path")
    test_parser.set_defaults(handler=_handle_test)

    pack_parser = subparsers.add_parser(
        "pack", help="cross-compile and create Debian packages"
    )
    pack_parser.add_argument("--preset", help="CMake cross-build preset")
    pack_parser.add_argument("--output", type=Path, help="package output directory")
    pack_arch = pack_parser.add_mutually_exclusive_group()
    pack_arch.add_argument("--arch", help="Debian architecture; overrides the saved target")
    pack_arch.add_argument("--all-architectures", action="store_true", help="build every declared architecture")
    pack_parser.set_defaults(handler=_handle_pack)

    metadata_parser = subparsers.add_parser(
        "metadata", help="validate lpm.toml and generate derived metadata"
    )
    metadata_parser.add_argument(
        "--format", choices=("cmake", "publish-json"), required=True
    )
    metadata_parser.add_argument(
        "--output", type=Path, help="write output to a file instead of stdout"
    )
    metadata_parser.add_argument("--arch", help="select one declared Debian architecture; overrides the saved target")
    metadata_parser.set_defaults(handler=_handle_metadata)

    deploy_parser = subparsers.add_parser(
        "deploy", help="install a Debian package over SSH"
    )
    deploy_parser.add_argument(
        "package", nargs="?", type=Path, help="package; defaults to newest in dist"
    )
    deploy_parser.add_argument("--arch", help="select a Debian architecture from dist; overrides the saved target")
    destination_group = deploy_parser.add_mutually_exclusive_group()
    destination_group.add_argument("--host", help="target hostname or IPv4 address")
    destination_group.add_argument(
        "--target",
        help="SSH Host alias; user and port come from SSH config unless explicitly set",
    )
    deploy_parser.add_argument("--user", help="SSH user")
    deploy_parser.add_argument("--port", type=int, help="SSH port")
    deploy_parser.add_argument(
        "--no-sudo", action="store_true", help="run apt-get without sudo"
    )
    deploy_parser.set_defaults(handler=_handle_deploy)

    login_parser = subparsers.add_parser(
        "login", help="authenticate with GitHub for registry operations"
    )
    login_parser.set_defaults(handler=_handle_login)

    publish_parser = subparsers.add_parser(
        "publish", help="stage a Debian package in a fork and create a registry PR"
    )
    publish_parser.add_argument(
        "packages", nargs="*", type=Path, help="Debian packages of one version, one per architecture"
    )
    publish_arch = publish_parser.add_mutually_exclusive_group()
    publish_arch.add_argument("--arch", help="publish one Debian architecture; overrides the saved target")
    publish_arch.add_argument("--all-architectures", action="store_true", help="publish every declared architecture in one PR")
    publish_parser.add_argument(
        "--force",
        action="store_true",
        help="replace a yanked version with a package of the same architecture",
    )
    publish_parser.add_argument(
        "--runtime",
        action="store_true",
        help="publish the Debian package as a runtime record",
    )
    publish_parser.set_defaults(handler=_handle_publish)

    unpublish_parser = subparsers.add_parser(
        "unpublish", help="create a registry PR that yanks one published version"
    )
    unpublish_parser.add_argument("version", help="published Debian version")
    unpublish_parser.add_argument("--reason", required=True, help="public yank reason")
    unpublish_parser.add_argument("--arch", help="architecture to yank; overrides the saved target")
    unpublish_parser.set_defaults(handler=_handle_unpublish)
    return parser


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _prompt(label: str, default: str) -> str:
    answer = input(f"{label} [{default}]: ").strip()
    return answer or default


def _handle_init(args: argparse.Namespace) -> int:
    inferred_name = re.sub(r"[^a-z0-9]+", "-", args.path.resolve().name.lower()).strip(
        "-"
    )
    inferred_name = inferred_name or "application"
    name = args.name
    version = args.app_version
    description = args.description
    if not args.yes and sys.stdin.isatty():
        name = name or _prompt("Project name", inferred_name)
        version = version or _prompt("Version", "0.1.0")
        description = (
            description
            if description is not None
            else _prompt(
                "Description", f"{name or inferred_name} application for LILYGO UI"
            )
        )
    name = name or inferred_name
    version = version or "0.1.0"
    description = (
        description if description is not None else f"{name} application for LILYGO UI"
    )
    target = initialize(
        args.path,
        name=name,
        version=version,
        description=description,
        template=args.template,
    )
    print(f"Initialized {name} in {target}")
    return 0


def _handle_set_target(args: argparse.Namespace) -> int:
    project = find_project()
    save_target(project, args.target)
    architecture = TARGET_ARCHITECTURES[args.target]
    description = architecture or "all declared architectures"
    print(f"Target set to {args.target} ({description}) for {project.root}")
    return 0


def _handle_start(args: argparse.Namespace) -> int:
    project = find_project()
    arguments = args.arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    result = start(
        project,
        preset=args.preset,
        executable=args.executable,
        build=not args.no_build,
        foreground=args.foreground,
        arguments=arguments,
    )
    if isinstance(result, Path):
        print(f"Simulator started. Log: {result}")
    return 0


def _handle_stop(args: argparse.Namespace) -> int:
    project = find_project()
    if stop(project, timeout=args.timeout):
        print("Simulator stopped.")
    else:
        print("Simulator is not running.")
    return 0


def _handle_test(args: argparse.Namespace) -> int:
    report = test(find_project(), preset=args.preset, report=args.report)
    print(f"Tests passed. Report: {report}")
    return 0


def _handle_pack(args: argparse.Namespace) -> int:
    project = find_project()
    architecture, all_architectures = resolve_target(
        project, command="pack", architecture=args.arch,
        all_architectures=args.all_architectures,
        explicit_selection=args.preset is not None or project.build_backend == "script",
    )
    packages = pack(
        project, preset=args.preset, output=args.output,
        architecture=architecture, all_architectures=all_architectures,
    )
    for package in packages:
        print(f"Package: {package}")
    return 0


def _handle_metadata(args: argparse.Namespace) -> int:
    project = find_project()
    architecture, _ = resolve_target(project, command="metadata", architecture=args.arch)
    options = {"architecture": architecture} if architecture else {}
    content = generate_metadata(project, output_format=args.format, **options)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        print(f"Metadata: {output}")
    else:
        sys.stdout.write(content)
    return 0


def _handle_deploy(args: argparse.Namespace) -> int:
    project = find_project()
    architecture, _ = resolve_target(
        project, command="deploy", architecture=args.arch,
        explicit_selection=args.package is not None,
    )
    package = deploy(
        project,
        package=args.package,
        architecture=architecture,
        host=args.host,
        target=args.target,
        user=args.user,
        port=args.port,
        sudo=not args.no_sudo,
    )
    print(f"Deployed: {package}")
    return 0


def _handle_login(args: argparse.Namespace) -> int:
    github_login()
    print("Authenticated with GitHub.")
    return 0


def _handle_publish(args: argparse.Namespace) -> int:
    project = find_project()
    architecture, all_architectures = resolve_target(
        project, command="publish", architecture=args.arch,
        all_architectures=args.all_architectures,
        explicit_selection=bool(args.packages),
    )
    options = {"force": args.force}
    if len(args.packages) > 1:
        options["package_paths"] = args.packages
    else:
        options["package_path"] = args.packages[0] if args.packages else None
    if architecture:
        options["architecture"] = architecture
    if all_architectures:
        options["all_architectures"] = True
    if args.runtime:
        options["runtime"] = True
    submission = publish(project, **options)
    print(f"Submission: {submission.pull_request_url}")
    print("Status: pending_review")
    return 0


def _handle_unpublish(args: argparse.Namespace) -> int:
    project = find_project()
    architecture, _ = resolve_target(project, command="unpublish", architecture=args.arch)
    submission = unpublish(
        project,
        version=args.version,
        reason=args.reason,
        architecture=architecture,
    )
    print(f"Submission: {submission.pull_request_url}")
    print("Status: pending_review")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except LpmError as exc:
        print(f"lpm: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
