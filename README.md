# LPM

English | [简体中文](README_CN.md)

`lpm` is the standard project management CLI for LILYGO UI. Written in Python 3, it provides a single workflow for initializing applications from a template, running CMake host simulations and tests, cross-compiling for AArch64 and RISC-V 64, creating Debian packages, and deploying over SSH. Runtime libraries can use their own build and packaging tools through the script backend.

## Requirements

- Python 3.11 or later
- CMake 3.21 or later, including `ctest` and `cpack`, for the default CMake backend
- Git, when installing directly from GitHub or initializing a project from a remote template
- `dpkg-deb` when publishing
- SSH and SCP, when deploying
- The compiler, SDL2, and cross-compilation environment required by the LILYGO UI template, or the tools required by a script-based project

## Installation

Install directly from GitHub:

```sh
# Install using pip
python3 -m pip install "git+https://github.com/LILYGO-UI/lpm.git"
lpm --version

# It is recommended to use uv for installation.
uv tool install "git+https://github.com/LILYGO-UI/lpm.git"
lpm --version
```

Alternatively, install from the current source tree:

```sh
uv tool install .
lpm --version
```

For development:

```sh
uv venv
uv pip install -e .
```

`lpm` has no third-party runtime dependencies.

## Quick Start

Create an application in an empty directory. Interactive mode prompts for the project name, version, and description:

```sh
mkdir weather-station
cd weather-station
lpm init
```

You can also run the initialization non-interactively:

```sh
lpm init weather-station \
  --name weather-station \
  --app-version 0.1.0 \
  --description "Weather station for LILYGO UI" \
  --yes
```

Initialization uses the official [LILYGO UI template](https://github.com/LILYGO-UI/template), clones its Git submodules recursively, and updates the application identity and version in `lpm.toml`. The target directory must be empty. For development or offline testing, use a local template with `--template /path/to/template`.

## Commands

The common operations are summarized below. See [command business logic](docs/commands.md) for parameter precedence, internal execution flow, state files, and failure behavior.

### `lpm set-target`

Save a platform for the current project, then omit architecture flags:

```sh
lpm set-target cm0       # arm64
lpm pack
lpm deploy --host cm0.local

lpm set-target k230      # riscv64
lpm pack
lpm deploy --host 192.168.8.151 --user wing

lpm set-target all       # every architecture declared by the project
lpm pack
lpm publish
```

The selection is stored in `.lpm/target.json`, persists across terminals, and
applies only to this project. `pack`, `metadata`, `deploy`, `publish`, and
`unpublish` use it by default. Explicit `--arch`, `--all-architectures`,
`pack --preset`, or package paths override it for that invocation.
Without a saved target, the existing defaults apply.

`all` enables batch packing/publication and full metadata; it does not mean
Debian's architecture-independent `all` package type. Deployment and unpublishing
require a single platform: switch to `cm0` or `k230`, or pass `--arch`.
`deploy --target` still selects an SSH alias independently of the platform.
Host simulator commands and tests keep their host presets; script-backend
packing continues to use the project's script configuration.

### `lpm start`

Configure and build the `host-simulator` preset, run the optional project preparation command, then start the simulator in the background:

```sh
lpm start
lpm start --no-build -- --fullscreen
```

Runtime state is stored in `.lpm/simulator.json`, and logs are written to `.lpm/simulator.log`. Use `--foreground` to keep the simulator attached to the current terminal. The `--preset` and `--executable` options override the configured values.

Projects can prepare a real local dependency before the UI starts:

```toml
[run]
prepare_command = ["{python}", "tools/prepare-dev-environment.py"]

[run.environment]
CLASH_DEMO = "0"
CLASH_API_URL = "http://127.0.0.1:19090"
CLASH_CONFIG_PATH = "{project}/.cache/mihomo-dev/config.yaml"
```

LPM executes `prepare_command` after the build and before the simulator in both foreground and background modes. Its argument array is passed without a shell; `{python}` and `{project}` are supported placeholders. The configured environment is sent to both processes, with existing shell variables taking precedence. `lpm stop` stops only the simulator.

### `lpm stop`

Send `SIGTERM` to the process group created by `lpm start`, followed by `SIGKILL` if the timeout expires. Before sending a signal, LPM verifies that the PID still belongs to the expected command to avoid terminating an unrelated process whose PID was reused.

```sh
lpm stop
lpm stop --timeout 10
```

### `lpm test`

Build the host preset and run CTest. Failed tests include detailed output, and a JUnit XML report is generated:

```sh
lpm test
lpm test --report build/test-results.xml
```

The default report path is `.lpm/test-results.xml`. With `build.backend = "script"`, LPM runs `build.test_command`, which must write a fresh JUnit XML report to `{report}`. `--preset` is only available with the CMake backend.

### `lpm pack`

Run `lpm set-target k230` once in the application repository, then `lpm pack`. Before configuring the application, LPM checks the cross compiler and Debian packaging tools, installs missing tools with Homebrew or APT, and initializes missing Git submodules. Installing host packages on Linux may require sudo. LPM downloads the pinned sysroot from the `LILYGO-UI/canaan-gen` release, checks its SHA-256, and keeps both the archive and extracted `sysroot/` in the project’s `.cache/k230-bsp/20261116T104343Z/`. It passes the prepared path to CMake. Subsequent packs reuse the cache offline. Set `LILYGO_UI_DEBIAN_SYSROOT` at pack time to use an existing sysroot.

Build Debian packages for the saved target. Without a target or explicit selection,
use `build.cross_preset` (default `cm0-cross`) and its CPack configuration:

```sh
lpm pack
lpm pack --output dist
lpm pack --arch arm64
lpm pack --arch riscv64
lpm pack --all-architectures
```

For a multi-architecture CMake project, configure:

```toml
[project.compatibility]
architectures = ["arm64", "riscv64"]
min_appkit_version = "0.1.0"

[build.cross_presets]
arm64 = "cm0-cross"
riscv64 = "k230-debian"
```

Provide matching configure/build presets. With no saved target, `lpm pack` keeps
the `cross_preset` default; `set-target all` or `--all-architectures` builds every
declared architecture sequentially. Single-architecture projects can keep using
`cross_preset` without adding a `cross_presets` mapping.
Each invocation uses a separate temporary packaging directory and validates the
DEB package/version/architecture before copying it to
`dist/<package>_<version>_<architecture>.deb`. K230 Debian builds require a Debian
riscv64 toolchain and sysroot; the Buildroot `k230-cross` preset is a separate target.
Architecture selection flags require the CMake backend.

The default output directory is `dist/`. When the command completes, it lists every `.deb` file generated during the run. With `build.backend = "script"`, LPM runs `build.pack_command` with `{output}` set to the absolute output directory. It requires a new or updated `.deb`; unchanged existing packages do not count. `--preset` is only available with the CMake backend.

### `lpm metadata`

Validate the project metadata in `lpm.toml` and render a derived format:

```sh
lpm metadata --format cmake
lpm metadata --format publish-json --output publish.json
```

The CMake format is consumed by the application template during configuration. For runtime projects, it emits only runtime identity variables. The JSON format is the complete application manifest for application projects; for runtime projects it emits a local identity snapshot because release version, architecture, size, and digest come from the `.deb` during `lpm publish`. Generated output should not be committed or edited. Build presets and deployment hosts are never included in it.

### `lpm deploy`

Upload a Debian package with SCP, then install it over SSH using `sudo apt-get install --reinstall`:

```sh
lpm deploy --host cm0.local --user pi
lpm deploy --arch riscv64 --host 192.168.8.151 --user wing
lpm deploy dist/lilygo-ui-weather-station_0.1.0_arm64.deb --host 192.168.1.42
lpm deploy --target cm0-lab
```

With a saved single-platform target or `--arch`, LPM reads the Debian control metadata and selects the newest matching package for the project's package name and configured version, excluding CPack staging directories. Combining a package path with `--arch` validates the file's architecture before uploading. Multi-architecture projects require a saved single-platform target, `--arch`, or an explicit package path.

For single-architecture projects with no saved target, package path, or `--arch`, LPM uses the newest `.deb` file in `dist/`. SSH uses the system's existing keys, agent, and `~/.ssh/config`; LPM does not store passwords. Add `--no-sudo` when the target user is already root. APT reinstalls the uploaded package even when its version is already installed, and resolves missing dependencies from the target's configured repositories; LPM does not update package indexes or activate runtime-specific services. After a successful installation, the uploaded file is removed from the target's `/tmp` directory. This workflow also supports runtime packages; service configuration remains the device or image's responsibility.

To switch between multiple target boards in the same project, add standard OpenSSH configuration to `.lpm/ssh_config`:

```sshconfig
Host cm0-lab
    HostName 192.168.1.42
    User pi
    Port 22
    IdentityFile ~/.ssh/cm0_lab

Host cm0-test
    HostName 192.168.1.43
    User root
```

Then run `lpm deploy --target cm0-lab` or `lpm deploy --target cm0-test`. LPM loads this file automatically through `ssh -F` and `scp -F`, so it applies only to the current project. The `--target` option does not inherit the deployment user or port from `lpm.toml`; explicit `--user` and `--port` options still take precedence. The project SSH configuration replaces the default use of `~/.ssh/config` for that command. To load the global configuration as well, add `Include ~/.ssh/config` at the end of the project file.

### `lpm login`, `lpm publish`, and `lpm unpublish`

Authenticate with GitHub, publish a Debian package for review, or request that one
published version be yanked:

```sh
lpm login
lpm publish
lpm publish dist/lilygo-ui-weather-station_1.0.0_arm64.deb
lpm publish --runtime dist/meshtasticd_2.8.0.0~local4528018+lr2021cad1_arm64.deb
lpm unpublish 1.0.0 --reason "Critical startup failure"
# After the unpublish PR is merged, replace that version with a rebuilt package:
lpm publish --force dist/lilygo-ui-weather-station_1.0.0_arm64.deb
```

Publishing is GitHub-only and does not require an independent Registry API. LPM
validates the local manifest and Debian control fields, creates or reuses the
authenticated user's public `packages` fork, uploads the `.deb` to a public
prerelease in that fork, commits content-addressed images and a release record,
then fast-forwards the user's `packages` fork when it is behind the central repository and opens a pull request against `LILYGO-UI/packages`. The central repository
validates the submission without secrets. After review and merge, its trusted
workflow copies the `.deb` into the central package Release and deploys a new
GitHub Pages snapshot.

Declare supported architectures in `project.compatibility.architectures`, for example
`["arm64", "riscv64"]`. Each DEB must match one declared architecture; its registry
manifest is narrowed to that architecture. Use `["all"]` alone for an architecture-independent
package. Wildcards and `source` are rejected. New releases use
`releases/<version>_<architecture>.json`, alongside existing `<version>.json` records.
The same version can have multiple architecture variants. Replacing an existing
variant still requires yanking it first and publishing with `--force`.
Merge the corresponding `packages` validator/schema update before publishing.

```sh
lpm publish --all-architectures
lpm publish dist/lilygo-ui-demo_1.0.0_arm64.deb dist/lilygo-ui-demo_1.0.0_riscv64.deb
lpm unpublish 1.0.0 --arch riscv64 --reason "K230 startup failure"
```

A batch creates one commit and review PR, after checking every artifact locally.
Multi-architecture projects can use `set-target` or select files, `--arch`, or
`--all-architectures` when publishing. Deployment requires a saved single-platform
target, `--arch`, or an explicit DEB path. See
[configuration details](docs/configuration.md).

`publish --runtime` submits a Debian runtime package such as `meshtasticd` to
`runtimes/<package>/` using the package name, version, and architecture from its
control fields. It can be invoked from an application repository because the
runtime package is identified by the DEB itself.

`publish --force` can replace a yanked version with a rebuilt package, keeping its
package name, version, application ID (for applications), and architecture. The unpublish PR must
be merged first; published or revoked versions cannot be replaced. The command
opens a new review PR with the updated manifest and artifact hash, then restores
the version to `published` when merged and promoted. Old content-addressed Debian
assets and Git history remain. Without `--force`, an existing version is rejected;
with a new version, `--force` behaves like a normal publish. The matching registry
validator update must land on `packages/main` before submitting a replacement.
Clients that detect upgrades only by version need an explicit reinstall to pick
up a replacement with the same version.

The staging prerelease is necessarily public because the central workflow has no
private credential for a contributor's fork. `unpublish` also opens a pull
request; it preserves the historical record and Debian asset while changing the
selected version to `yanked`. Set `GH_TOKEN` to use an existing token instead of
the credentials saved by `lpm login`. Login uses the bundled LILYGO OAuth App
through GitHub OAuth Device Flow; it requests the `public_repo` and `workflow`
scopes because publishing synchronizes workflow files in the user's `packages`
fork. `LPM_GITHUB_CLIENT_ID` can override its Client ID for development, and
GitHub CLI is not required. In a third-party GitHub
Actions workflow, store a user PAT with public-repository and `workflow` access
as a repository secret and expose it as `GH_TOKEN`; that workflow's automatic `GITHUB_TOKEN` is
scoped to the application repository and cannot write the user's `packages` fork.

## Project Configuration

`lpm init` generates the following configuration:

```toml
schema_version = 1

[project]
package = "lilygo-ui-weather-station"
version = "0.1.0"
app_id = "cc.lilygo.ui.WeatherStation"
title = "Weather Station"
# Authors, assets, permissions, compatibility, and Launcher metadata follow.

[build]
host_preset = "host-simulator"
cross_preset = "cm0-cross"
package_dir = "dist"

[deploy]
host = "raspberrypi.local"
user = "pi"
port = 22

# Optional local startup preparation; never included in publish metadata.
[run]
prepare_command = ["{python}", "tools/prepare-dev-environment.py"]
```

`lpm.toml` is the only developer-maintained source for application identity, release metadata, build presets, and deployment defaults. CMake derives Launcher, desktop, AppStream, and Debian package metadata from `[project]`. Running `lpm deploy` without destination options deploys to `pi@raspberrypi.local:22`; command-line options take precedence over `[deploy]`.

Do not remove `[project]` from a current template project: metadata generation and CMake configuration require it, and `lpm start` uses `project.package` as the default simulator executable. A plain legacy CMake project can still use workflow commands without `lpm.toml`; `--executable`, legacy `project.executable`, and `cmake/AppIdentity.cmake` remain supported start fallbacks. See the [configuration reference](docs/configuration.md) for all fields.

## Runtime Libraries and Script Workflows

For a runtime such as `meshtasticd`, add a configuration that invokes the repository's existing tools:

```toml
schema_version = 1

[project]
kind = "runtime"
package = "meshtasticd"

[build]
backend = "script"
package_dir = "dist"
test_command = ["{python}", "tools/lpm-workflow.py", "test", "--report", "{report}"]
pack_command = ["{python}", "tools/lpm-workflow.py", "pack", "--output-dir", "{output}"]
```

Then run `lpm test`, `lpm pack`, and `lpm deploy --host <board> --user pi`. Each command array runs directly from the project root without a shell. Whole-argument placeholders expand to LPM's Python interpreter (`{python}`), the test report path (`{report}`), or the package output directory (`{output}`). Shell variables, wildcards, pipes, and embedded placeholders are not expanded. The workflow adapter and its build dependencies belong to the runtime repository.

`project.kind` defaults to `"application"`; `build.backend` defaults to `"cmake"`, so existing application configurations keep their behavior. Runtime `[project]` contains only `kind` and `package`; its native packaging tools own the version and Debian metadata. Application identity and release metadata continue to come from `lpm.toml`.

Runtime projects support local testing, packaging, SSH deployment, metadata, registry publication, and yanking. `start` and `stop` report an explicit unsupported-operation error. `lpm metadata --format publish-json` emits the local runtime identity; publication reads the package name, version, and architecture from the generated Debian control fields and submits `runtimes/<package>/` records to the packages registry. See the [configuration reference](docs/configuration.md) for the script contract.

## Development and Testing

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m lpm --help
```

The test suite covers configuration parsing, template initialization, CMake/CTest/CPack and script workflows, runtime command boundaries, background process safety, package selection, SSH command construction, registry submissions, and CLI error handling.
