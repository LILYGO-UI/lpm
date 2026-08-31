# LPM

English | [简体中文](README_CN.md)

`lpm` is the standard project management CLI for LILYGO UI. Written in Python 3, it provides a single workflow for initializing applications from a template, running CMake host simulations and tests, cross-compiling for AArch64, creating Debian packages, and deploying over SSH.

## Requirements

- Python 3.11 or later
- CMake 3.21 or later, including `ctest` and `cpack`
- Git, when initializing a project from a remote template
- `dpkg-deb` when publishing
- SSH and SCP, when deploying
- The compiler, SDL2, and cross-compilation environment required by the LILYGO UI template

## Installation

Install from the current source tree:

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

### `lpm start`

Configure and build the `host-simulator` preset, then start the simulator in the background:

```sh
lpm start
lpm start --no-build -- --fullscreen
```

Runtime state is stored in `.lpm/simulator.json`, and logs are written to `.lpm/simulator.log`. Use `--foreground` to keep the simulator attached to the current terminal. The `--preset` and `--executable` options override the configured values.

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

The default report path is `.lpm/test-results.xml`.

### `lpm pack`

Configure and build the `cm0-cross` preset, then use `build/cm0-cross/CPackConfig.cmake` to generate Debian packages:

```sh
lpm pack
lpm pack --output dist
```

The default output directory is `dist/`. When the command completes, it lists every `.deb` file generated during the run.

### `lpm metadata`

Validate the project metadata in `lpm.toml` and render a derived format:

```sh
lpm metadata --format cmake
lpm metadata --format publish-json --output publish.json
```

The CMake format is consumed by the application template during configuration. The JSON format matches the registry publish schema and is the same validated manifest used by `lpm publish`; it is generated output and should not be committed or edited. Build presets and deployment hosts are never included in it.

### `lpm deploy`

Upload a Debian package with SCP, then install it over SSH using `sudo dpkg --install`:

```sh
lpm deploy --host cm0.local --user pi
lpm deploy dist/lilygo-ui-weather-station_0.1.0_arm64.deb --host 192.168.1.42
lpm deploy --target cm0-lab
```

When no package is specified, LPM uses the newest `.deb` file in `dist/`. SSH uses the system's existing keys, agent, and `~/.ssh/config`; LPM does not store passwords. Add `--no-sudo` when the target user is already root. After a successful installation, the uploaded file is removed from the target's `/tmp` directory.

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
lpm unpublish 1.0.0 --reason "Critical startup failure"
```

Publishing is GitHub-only and does not require an independent Registry API. LPM
validates the local manifest and Debian control fields, creates or reuses the
authenticated user's public `packages` fork, uploads the `.deb` to a public
prerelease in that fork, commits content-addressed images and a release record,
then opens a pull request against `LILYGO-UI/packages`. The central repository
validates the submission without secrets. After review and merge, its trusted
workflow copies the `.deb` into the central package Release and deploys a new
GitHub Pages snapshot.

The staging prerelease is necessarily public because the central workflow has no
private credential for a contributor's fork. `unpublish` also opens a pull
request; it preserves the historical record and Debian asset while changing the
selected version to `yanked`. Set `GH_TOKEN` to use an existing token instead of
the credentials saved by `lpm login`. Login uses GitHub OAuth Device Flow and
requires `LPM_GITHUB_CLIENT_ID` to identify an OAuth App with Device Flow enabled;
it does not require GitHub CLI. In a third-party GitHub Actions workflow, store
a user PAT with public-repository access as a repository secret and expose it as
`GH_TOKEN`; that workflow's automatic `GITHUB_TOKEN` is scoped to the application
repository and cannot write the user's `packages` fork.

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
```

`lpm.toml` is the only developer-maintained source for application identity, release metadata, build presets, and deployment defaults. CMake derives Launcher, desktop, AppStream, and Debian package metadata from `[project]`. Running `lpm deploy` without destination options deploys to `pi@raspberrypi.local:22`; command-line options take precedence over `[deploy]`.

Do not remove `[project]` from a current template project: metadata generation and CMake configuration require it, and `lpm start` uses `project.package` as the default simulator executable. A plain legacy CMake project can still use workflow commands without `lpm.toml`; `--executable`, legacy `project.executable`, and `cmake/AppIdentity.cmake` remain supported start fallbacks. See the [configuration reference](docs/configuration.md) for all fields.

## Development and Testing

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m lpm --help
```

The test suite covers configuration parsing, template initialization, CMake/CTest/CPack workflows, background process safety, package selection, SSH command construction, registry submissions, and CLI error handling.
