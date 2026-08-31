# LPM

[English](README.md) | 简体中文

`lpm` 是 LILYGO UI 的标准项目管理 CLI。它用 Python 3 开发，统一接管应用模板初始化、CMake 主机仿真、测试、AArch64 交叉编译、Debian 打包和 SSH 部署。

## 环境要求

- Python 3.11 或更高版本
- CMake 3.21 或更高版本（包含 `ctest`、`cpack`）
- Git（从远程模板初始化项目时需要）
- `dpkg-deb`（发布时需要）
- SSH 与 SCP（部署时需要）
- LILYGO UI 模板要求的编译器、SDL2 和交叉编译环境

## 安装

从当前源码安装：

```sh
uv tool install .
lpm --version
```

开发模式：

```sh
uv venv
uv pip install -e .
```

`lpm` 本身没有运行时第三方依赖。

## 快速开始

在空目录中创建应用。交互模式会询问名称、版本和描述：

```sh
mkdir weather-station
cd weather-station
lpm init
```

也可以完全非交互执行：

```sh
lpm init weather-station \
  --name weather-station \
  --app-version 0.1.0 \
  --description "Weather station for LILYGO UI" \
  --yes
```

初始化使用官方 [LILYGO UI template](https://github.com/LILYGO-UI/template)，递归拉取其 Git 子模块，并更新 `lpm.toml` 中的应用身份和版本。目标目录必须为空。开发或离线测试时可通过 `--template /path/to/template` 使用本地模板。

## 命令

下面给出常用操作；参数优先级、内部调用顺序、状态文件和失败处理见[命令业务逻辑](docs/commands.md)。

### `lpm start`

配置并构建 `host-simulator` preset，然后在后台启动模拟器：

```sh
lpm start
lpm start --no-build -- --fullscreen
```

运行状态保存在 `.lpm/simulator.json`，日志写入 `.lpm/simulator.log`。使用 `--foreground` 可让模拟器保持在当前终端。`--preset` 和 `--executable` 可覆盖配置。

### `lpm stop`

向 `lpm start` 创建的进程组发送 `SIGTERM`；超时后发送 `SIGKILL`。停止前会校验 PID 对应的命令，避免误伤被系统复用的进程号。

```sh
lpm stop
lpm stop --timeout 10
```

### `lpm test`

构建主机 preset 并运行 CTest。失败测试会输出详情，同时生成 JUnit XML 报告：

```sh
lpm test
lpm test --report build/test-results.xml
```

默认报告位置为 `.lpm/test-results.xml`。

### `lpm pack`

配置并构建 `cm0-cross` preset，再读取 `build/cm0-cross/CPackConfig.cmake` 生成 Debian 包：

```sh
lpm pack
lpm pack --output dist
```

默认产物目录为 `dist/`。命令结束时会列出本次生成的所有 `.deb` 文件。

### `lpm metadata`

校验 `lpm.toml` 中的项目元数据，并渲染所需格式：

```sh
lpm metadata --format cmake
lpm metadata --format publish-json --output publish.json
```

CMake 格式由应用模板在配置阶段使用。JSON 格式符合 registry 的发布 schema，也是 `lpm publish` 实际使用的同一份已校验清单；它是生成产物，不应提交或手工编辑，其中永远不会包含构建 preset 和部署主机等信息。

### `lpm deploy`

通过 SCP 上传 Debian 包，并经 SSH 执行 `sudo dpkg --install`：

```sh
lpm deploy --host cm0.local --user pi
lpm deploy dist/lilygo-ui-weather-station_0.1.0_arm64.deb --host 192.168.1.42
lpm deploy --target cm0-lab
```

未指定包时使用 `dist/` 下最新的 `.deb`。SSH 使用系统现有的密钥、agent 和 `~/.ssh/config`，LPM 不保存密码。目标用户已经是 root 时可加 `--no-sudo`。安装成功后，上传到目标机 `/tmp` 的文件会被删除。

需要在同一项目中切换多块目标板时，可将标准 OpenSSH 配置写入 `.lpm/ssh_config`：

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

随后使用 `lpm deploy --target cm0-lab` 或 `lpm deploy --target cm0-test`。LPM 会通过 `ssh -F` 和 `scp -F` 自动加载该文件，因此它只影响当前项目。`--target` 不继承 `lpm.toml` 的部署用户和端口；显式传入 `--user` 或 `--port` 时仍以命令行为准。项目 SSH 配置会替代本次命令对 `~/.ssh/config` 的默认读取，如需同时加载全局配置，可在文件末尾添加 `Include ~/.ssh/config`。

### `lpm login`、`lpm publish` 与 `lpm unpublish`

登录 GitHub、提交 Debian 包审核，或请求下架某个已发布版本：

```sh
lpm login
lpm publish
lpm publish dist/lilygo-ui-weather-station_1.0.0_arm64.deb
lpm unpublish 1.0.0 --reason "Critical startup failure"
```

发布过程完全依赖 GitHub，不需要独立 Registry API。LPM 会在本地校验清单和
Debian control 字段，创建或复用当前 GitHub 用户的公开 `packages` Fork，将
`.deb` 上传到 Fork 的公开 prerelease，提交内容寻址的图片与版本记录，最后向
`LILYGO-UI/packages` 创建 Pull Request。中央仓库在无 Secret 的 PR 工作流中校验
提交；审核合并后，受信任工作流把 `.deb` 转存到中央 Release，并部署新的 GitHub
Pages 快照。

由于中央工作流没有第三方 Fork 的私有凭证，暂存 prerelease 必须公开。
`unpublish` 同样创建 Pull Request；它保留历史记录与 Debian 文件，只把选定版本
改为 `yanked`。如需使用已有 Token，可设置 `GH_TOKEN`，否则 LPM 使用 `lpm login`
保存的凭证。登录通过 GitHub OAuth Device Flow 完成，不依赖 GitHub CLI；需要用
`LPM_GITHUB_CLIENT_ID` 指定已启用 Device Flow 的 OAuth App。在第三方项目的
GitHub Actions 中，应把具备公开仓库权限的用户 PAT
保存为仓库 Secret，并以 `GH_TOKEN` 注入；工作流自动获得的 `GITHUB_TOKEN` 只对
应用源码仓库有效，不能写入用户的 `packages` Fork。

## 项目配置

`lpm init` 生成以下配置：

```toml
schema_version = 1

[project]
package = "lilygo-ui-weather-station"
version = "0.1.0"
app_id = "cc.lilygo.ui.WeatherStation"
title = "Weather Station"
# 后续还包括作者、资源、权限、兼容性和 Launcher 元数据。

[build]
host_preset = "host-simulator"
cross_preset = "cm0-cross"
package_dir = "dist"

[deploy]
host = "raspberrypi.local"
user = "pi"
port = 22
```

`lpm.toml` 是开发者唯一需要维护的入口，统一保存应用身份、发布元数据、构建 preset 和部署默认值。CMake 从 `[project]` 派生 Launcher、desktop、AppStream 和 Debian 包元数据。直接执行 `lpm deploy` 时默认部署到 `pi@raspberrypi.local:22`，命令行参数优先于 `[deploy]`。

当前模板项目不能删除 `[project]`：元数据生成和 CMake 配置都依赖它，`lpm start` 也用 `project.package` 推导默认模拟器可执行文件。没有 `lpm.toml` 的旧 CMake 工程仍可使用部分工作流命令；`--executable`、旧版 `project.executable` 和 `cmake/AppIdentity.cmake` 继续作为 `start` 的兼容回退。完整字段说明见 [配置参考](docs/configuration.md)。

## 开发与测试

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m lpm --help
```

测试覆盖配置解析、模板初始化、CMake/CTest/CPack 工作流、后台进程安全、包选择、SSH 命令构造、registry 提交和 CLI 错误处理。
