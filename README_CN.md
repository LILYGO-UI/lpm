# LPM

[English](README.md) | 简体中文

`lpm` 是 LILYGO UI 的标准项目管理 CLI。它用 Python 3 开发，统一接管应用模板初始化、CMake 主机仿真、测试、AArch64 / RISC-V 64 交叉编译、Debian 打包和 SSH 部署。运行库可通过脚本后端调用自身的构建和打包工具。

## 环境要求

- Python 3.11 或更高版本
- 默认 CMake 后端需要 CMake 3.21 或更高版本（包含 `ctest`、`cpack`）
- Git（从 GitHub 直接安装或从远程模板初始化项目时需要）
- `dpkg-deb`（发布时需要）
- SSH 与 SCP（部署时需要）
- LILYGO UI 模板要求的编译器、SDL2 和交叉编译环境，或脚本项目自身要求的工具

## 安装

直接从 GitHub 安装：

```sh
# 使用 pip 安装
python3 -m pip install "git+https://github.com/LILYGO-UI/lpm.git"
lpm --version

# 推荐使用 uv 进行安装
uv tool install "git+https://github.com/LILYGO-UI/lpm.git"
lpm --version
```

也可以从当前源码安装：

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

### `lpm set-target`

为当前项目保存目标平台，后续命令无需重复指定架构：

```sh
lpm set-target cm0       # arm64
lpm pack
lpm deploy --host cm0.local

lpm set-target k230      # riscv64
lpm pack
lpm deploy --host 192.168.8.151 --user wing

lpm set-target all       # 项目声明的全部架构
lpm pack
lpm publish
```

目标保存在项目的 `.lpm/target.json`，重开终端仍然有效，各项目独立保存。
`pack`、`metadata`、`deploy`、`publish`、`unpublish` 默认使用该目标。
显式传入 `--arch`、`--all-architectures`、`pack --preset` 或包文件路径时，
仅覆盖本次命令的选择，不修改保存的目标。未设置目标时沿用原有默认行为。

`all` 表示批量打包、发布项目声明的全部架构，或输出完整元数据，不是 Debian
架构无关包的 `all` 类型。部署和下架一次只接受一个架构，需要切换到 `cm0` / `k230`
或使用 `--arch`。`deploy --target` 仍单独表示 SSH 别名。
模拟器命令和测试继续使用主机 preset；脚本后端的打包平台仍由项目脚本配置决定。

### `lpm start`

配置并构建 `host-simulator` preset，执行可选的项目准备命令，然后在后台启动模拟器：

```sh
lpm start
lpm start --no-build -- --fullscreen
```

运行状态保存在 `.lpm/simulator.json`，日志写入 `.lpm/simulator.log`。使用 `--foreground` 可让模拟器保持在当前终端。`--preset` 和 `--executable` 可覆盖配置。

项目可以在 UI 启动前准备真实的本地依赖：

```toml
[run]
prepare_command = ["{python}", "tools/prepare-dev-environment.py"]

[run.environment]
CLASH_DEMO = "0"
CLASH_API_URL = "http://127.0.0.1:19090"
CLASH_CONFIG_PATH = "{project}/.cache/mihomo-dev/config.yaml"
```

LPM 会在构建完成后、前台或后台启动模拟器前执行 `prepare_command`。命令数组不经过 shell；`{python}` 和 `{project}` 是支持的占位符。配置的环境变量会同时传给准备命令和 UI，当前 shell 中已有的同名变量优先。`lpm stop` 只停止 UI，不停止准备命令启动的外部服务。

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

默认报告位置为 `.lpm/test-results.xml`。设置 `build.backend = "script"` 时，LPM 调用 `build.test_command`，脚本必须在 `{report}` 位置写入本次运行的 JUnit XML 报告。`--preset` 仅适用于 CMake 后端。

### `lpm pack`

在应用目录执行一次 `lpm set-target k230`，之后用 `lpm pack` 打包。LPM 在配置应用之前检查交叉编译器和 Debian 打包工具，缺少时通过本机的 Homebrew 或 APT 安装，并初始化缺失的 Git 子模块。Linux 主机安装系统软件包时可能需要 sudo。LPM 从 `LILYGO-UI/canaan-gen` Release 下载固定版本的 sysroot 并校验 SHA-256，将压缩包和解压内容保存在当前项目的 `.cache/k230-bsp/20261116T104343Z/`，再把 sysroot 的绝对路径交给 CMake。缓存就绪后可以离线打包。如需使用自备 sysroot，在执行 `lpm pack` 时设置 `LILYGO_UI_DEBIAN_SYSROOT`。

为保存的目标平台构建 Debian 包。未设置目标且没有显式选择时，使用
`build.cross_preset`（默认 `cm0-cross`）及其 CPack 配置：

```sh
lpm pack
lpm pack --output dist
lpm pack --arch arm64
lpm pack --arch riscv64
lpm pack --all-architectures
```

双平台项目需配置如下映射，并在 `CMakePresets.json` 中提供同名 configure/build preset：

```toml
[project.compatibility]
architectures = ["arm64", "riscv64"]
min_appkit_version = "0.1.0"

[build.cross_presets]
arm64 = "cm0-cross"
riscv64 = "k230-debian"
```

未设置目标时，`lpm pack` 保留原有 `cross_preset` 默认选择；`set-target all` 或
`--all-architectures` 顺序构建全部架构。单架构项目可以继续只配置 `cross_preset`，
无需补充 `cross_presets` 映射。
每个架构在独立临时目录打包，核对 Debian control 中的包名、版本和架构后复制到
`dist/<package>_<version>_<architecture>.deb`。`--output` 可以覆盖 `dist`。
K230 Debian preset 需要 Debian riscv64 工具链和 sysroot；`k230-cross` 面向 Buildroot。
多架构选择参数仅支持 CMake 后端。

默认产物目录为 `dist/`。命令结束时会列出本次生成的所有 `.deb` 文件。设置 `build.backend = "script"` 时，LPM 调用 `build.pack_command`，将 `{output}` 替换为输出目录的绝对路径。必须生成或更新至少一个 `.deb`；未变化的已有包不计入本次产物。`--preset` 仅适用于 CMake 后端。

### `lpm metadata`

校验 `lpm.toml` 中的项目元数据，并渲染所需格式：

```sh
lpm metadata --format cmake
lpm metadata --format publish-json --output publish.json
```

CMake 格式由应用模板在配置阶段使用；运行库项目只输出运行库身份变量。应用项目的 JSON 格式符合 registry 发布 schema，也是 `lpm publish` 使用的已校验清单；运行库项目输出本地身份快照，因为版本、架构、大小和摘要会在 `lpm publish` 时从 `.deb` 读取。它是生成产物，不应提交或手工编辑，其中永远不会包含构建 preset 和部署主机等信息。

### `lpm deploy`

通过 SCP 上传 Debian 包，并经 SSH 执行 `sudo apt-get install --reinstall`：

```sh
lpm deploy --host cm0.local --user pi
lpm deploy --arch riscv64 --host 192.168.8.151 --user wing
lpm deploy dist/lilygo-ui-weather-station_0.1.0_arm64.deb --host 192.168.1.42
lpm deploy --target cm0-lab
```

保存了单平台目标或指定 `--arch` 时，读取 DEB control 元数据，选择架构、项目包名和配置版本均匹配的最新包，排除 CPack 暂存目录。同时传入文件路径和 `--arch` 时，会在上传前校验实际架构。多架构项目需要保存的单平台目标、`--arch` 或 DEB 路径。

未保存目标的单架构项目未指定包及 `--arch` 时使用 `dist/` 下最新的 `.deb`。SSH 使用系统现有的密钥、agent 和 `~/.ssh/config`，LPM 不保存密码。目标用户已经是 root 时可加 `--no-sudo`。APT 会强制重新安装上传的包，即使目标机已安装相同版本，并从已配置的软件源解析缺失依赖；LPM 不会更新软件包索引，也不会启用私有图形驱动。安装成功后，上传到目标机 `/tmp` 的文件会被删除。运行库也使用同一部署流程；服务配置仍由设备或镜像负责。

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
lpm publish --runtime dist/meshtasticd_2.8.0.0~local4528018+lr2021cad1_arm64.deb
lpm unpublish 1.0.0 --reason "Critical startup failure"
# 下架 PR 合并后，使用重新构建的包替换该版本：
lpm publish --force dist/lilygo-ui-weather-station_1.0.0_arm64.deb
```

发布过程完全依赖 GitHub，不需要独立 Registry API。LPM 会在本地校验清单和
Debian control 字段，创建或复用当前 GitHub 用户的公开 `packages` Fork，将
`.deb` 上传到 Fork 的公开 prerelease，提交内容寻址的图片与版本记录，最后向
`LILYGO-UI/packages` 创建 Pull Request。中央仓库在无 Secret 的 PR 工作流中校验
提交；如果用户的 `packages` Fork 落后于中央仓库，LPM 会先快进同步其默认分支。审核合并后，受信任工作流把 `.deb` 转存到中央 Release，并部署新的 GitHub
Pages 快照。

`project.compatibility.architectures` 可声明多个架构，如 `["arm64", "riscv64"]`。
每个 DEB 的 `Architecture` 必须属于这个列表，发布记录则只声明该 DEB 的一个架构。
架构无关包使用 `["all"]`，不能与具体架构混用；拒绝架构通配符和 `source`。
新记录使用 `releases/<version>_<architecture>.json`，兼容现有 `<version>.json`。
同版本不同架构可以并存；同版本同架构仍需先下架、再 `publish --force`。
发布前先合并 `packages` 的校验器与 schema 更新，再更新 LPM。
完整规则见[配置文档](docs/configuration.md)。

```sh
# 自动选择 dist 中匹配当前包名、版本和各架构的包，提交一个 PR
lpm publish --all-architectures
# 或明确指定两个产物
lpm publish dist/lilygo-ui-demo_1.0.0_arm64.deb dist/lilygo-ui-demo_1.0.0_riscv64.deb
# 仅下架其中一个架构
lpm unpublish 1.0.0 --arch riscv64 --reason "K230 startup failure"
```

多架构项目执行 `publish` 时可以先通过 `set-target` 保存平台，或指定文件、`--arch`
或 `--all-architectures`；`deploy` 使用保存的单个平台，也可指定 `--arch` 或目标架构的
DEB 路径，避免从共享 `dist` 误选最新包。

`publish --runtime` 会读取 DEB control 中的包名、版本和架构，把类似 `meshtasticd`
的运行库提交到 `runtimes/<package>/`；它可以从应用源码仓库执行，因为运行库身份
由 DEB 本身确定。

`publish --force` 允许用新包替换已下架版本，应用的包名、版本号、应用 ID 和架构必须
保持一致。下架 PR 必须先合并；不能替换仍为 `published` 或已 `revoked` 的版本。
该命令提交新的审核 PR，更新清单与包哈希，合并和转存后恢复上架；旧的内容寻址
DEB 和 Git 历史仍保留。不加 `--force` 仍拒绝已存在的版本；版本尚不存在时，
`--force` 按普通发布处理。提交替换前，必须先将对应的中央校验器更新合并到
`packages/main`。只按版本号判断升级的客户端需要显式重新安装，才能使用同版本新包。

由于中央工作流没有第三方 Fork 的私有凭证，暂存 prerelease 必须公开。
`unpublish` 同样创建 Pull Request；它保留历史记录与 Debian 文件，只把选定版本
改为 `yanked`。如需使用已有 Token，可设置 `GH_TOKEN`，否则 LPM 使用 `lpm login`
保存的凭证。登录通过内置的 LILYGO OAuth App 执行 GitHub OAuth Device Flow，不依赖
GitHub CLI；由于发布需要同步用户 `packages` Fork 中的 workflow 文件，登录会请求
`public_repo` 和 `workflow` 权限；开发时可用 `LPM_GITHUB_CLIENT_ID` 覆盖其 Client ID。在第三方项目的
GitHub Actions 中，应把具备公开仓库权限和 `workflow` 权限的用户 PAT
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

# 可选的本地启动准备；不会进入发布元数据。
[run]
prepare_command = ["{python}", "tools/prepare-dev-environment.py"]
```

`lpm.toml` 是开发者唯一需要维护的入口，统一保存应用身份、发布元数据、构建 preset 和部署默认值。CMake 从 `[project]` 派生 Launcher、desktop、AppStream 和 Debian 包元数据。直接执行 `lpm deploy` 时默认部署到 `pi@raspberrypi.local:22`，命令行参数优先于 `[deploy]`。

当前模板项目不能删除 `[project]`：元数据生成和 CMake 配置都依赖它，`lpm start` 也用 `project.package` 推导默认模拟器可执行文件。没有 `lpm.toml` 的旧 CMake 工程仍可使用部分工作流命令；`--executable`、旧版 `project.executable` 和 `cmake/AppIdentity.cmake` 继续作为 `start` 的兼容回退。完整字段说明见 [配置参考](docs/configuration.md)。

## 运行库与脚本工作流

`meshtasticd` 等运行库可以通过以下配置调用仓库已有工具：

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

随后使用 `lpm test`、`lpm pack` 和 `lpm deploy --host <board> --user pi`。命令数组在项目根目录直接执行，不经过 shell。完整参数 `{python}` 替换为 LPM 使用的 Python 解释器，`{report}` 和 `{output}` 分别替换为测试报告和包输出目录的绝对路径；不会展开 shell 变量、通配符、管道或嵌入字符串的占位符。工作流适配脚本及其构建依赖由运行库仓库提供。

`project.kind` 默认为 `"application"`，`build.backend` 默认为 `"cmake"`，现有应用配置保持原有行为。运行库的 `[project]` 只包含 `kind` 和 `package`，版本及 Debian 元数据由原生打包工具维护；应用身份和发布元数据仍以 `lpm.toml` 为唯一入口。

运行库支持本地测试、打包、SSH 部署、metadata、registry 发布和下架。`start`、`stop` 会明确报告不支持。运行库的 `metadata --format publish-json` 输出本地身份快照；发布时再从生成的 Debian control 字段读取包名、版本和架构，并向 packages registry 提交 `runtimes/<package>/` 记录。脚本接口详见[配置参考](docs/configuration.md)。

## 开发与测试

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m lpm --help
```

测试覆盖配置解析、模板初始化、CMake/CTest/CPack 与脚本工作流、运行库命令边界、后台进程安全、包选择、SSH 命令构造、registry 提交和 CLI 错误处理。
