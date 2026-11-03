# LPM 配置参考

LPM 从当前目录向父目录查找最近的 `lpm.toml`。它是当前 LILYGO UI
应用唯一需要人工维护的元数据入口，同时包含公开的产品与发布信息，以及仅在本地使用的构建、部署配置。运行库使用 `project.kind = "runtime"`，可仅记录包名和本地工作流，将版本与 Debian 元数据留在自身打包工具中。各命令如何消费这些配置见[命令业务逻辑](commands.md)。

相对路径的解析规则：

- `lpm.toml` 中的资源路径和 `build.package_dir` 相对于项目根目录。
- 命令行传入的 `--output`、`--report` 等路径相对于执行命令时的工作目录。
- CMake preset 名称直接交给项目根目录下的 CMake 处理。
- 脚本命令的工作目录是项目根目录，`{report}` 和 `{output}` 均展开为绝对路径。

## 完整示例

```toml
schema_version = 1

[project]
package = "lilygo-ui-weather-station"
version = "1.0.0"
app_id = "cc.lilygo.ui.WeatherStation"
title = "Weather Station"
summary = "Local weather and environmental information"
description = "View local weather, temperature, humidity, and forecasts."
license = "MIT"
source_repo = "https://github.com/LILYGO-UI/lilygo-ui-weather-station"
homepage = "https://github.com/LILYGO-UI/lilygo-ui-weather-station"
categories = ["Utilities"]

[[project.authors]]
name = "LILYGO"
github = "LILYGO"

[project.assets]
icon = "assets/app-icon.png"

[[project.assets.screenshots]]
path = "assets/previews/main.png"
caption = "Main weather screen"

[[project.permissions]]
id = "network"
reason = "Download weather observations and forecasts"

[[project.permissions]]
id = "filesystem"
access = "read-write"
scope = "app-data"
reason = "Store application settings and cached observations"

[project.compatibility]
architectures = ["arm64"]
min_appkit_version = "0.1.0"

[project.launcher]
order = 90

[build]
host_preset = "host-simulator"
cross_preset = "cm0-cross"
package_dir = "dist"

[deploy]
host = "raspberrypi.local"
user = "pi"
port = 22
```

没有权限需求时，在 `[project]` 中写 `permissions = []`，不要创建空的
`[[project.permissions]]` 表。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | `lpm.toml` 格式版本，当前必须为 `1` |
| `project` | table | 当前模板构建、应用发布及运行库项目必填 | 项目类型；应用身份与发布信息，或运行库包名 |
| `build` | table | 否 | CMake preset 或脚本工作流，以及包输出目录；缺失字段使用默认值 |
| `deploy` | table | 否 | 默认 SSH 部署目标；缺失或空值使用默认值 |

`schema_version` 描述的是 `lpm.toml` 格式。生成的 publish JSON 也包含独立的
`schema_version = 1` 和固定 `$schema` URL。

## `[project]`

`kind` 接受 `"application"` 或 `"runtime"`，省略时为 `"application"`。
应用的字段要求如下；运行库使用后面的[运行库配置](#运行库配置)。

### 基本字段

| 字段 | 类型 | 必填 | 约束与用途 |
| --- | --- | --- | --- |
| `kind` | string | 否 | 项目类型，默认 `"application"` |
| `package` | string | 是 | Debian 包名和默认可执行文件名；匹配 `lilygo-ui-[a-z0-9]+(-[a-z0-9]+)*`，最长 128 字符 |
| `version` | string | 是 | Debian 兼容版本，最长 128 字符；例如 `1.0.0`、`1.0.0-rc.1` 或 `1:1.0.0-1` |
| `app_id` | string | 是 | 稳定应用 ID；匹配 `cc.lilygo.ui.<PascalCase>`，最长 255 字符 |
| `title` | string | 是 | 用户可见应用名称，1 到 80 字符 |
| `summary` | string | 是 | Launcher、desktop 和 Debian 包摘要，1 到 160 字符 |
| `description` | string | 是 | AppStream 长描述，1 到 4000 字符 |
| `license` | string | 是 | 许可证标识或表达式，1 到 128 字符；应使用 SPDX 表达式 |
| `source_repo` | HTTPS URL | 是 | 公开源代码仓库，必须包含 HTTPS 主机，最长 4096 字符 |
| `homepage` | HTTPS URL | 是 | 应用主页，必须包含 HTTPS 主机，最长 4096 字符 |
| `categories` | string array | 是 | 1 到 11 个分类，不允许重复 |
| `executable` | string | 否，兼容字段 | 旧项目的主机模拟器名称；新项目使用 `package`，该字段不会进入 publish JSON |

`package` 和 `app_id` 是发布后的稳定接口。变更它们等同于包或应用身份迁移，不能作为普通版本更新处理。

`categories` 只接受以下值：

```text
Development
Education
Games
Graphics
Lifestyle
Multimedia
Network
Office
Science
System
Utilities
```

### `[[project.authors]]`

至少 1 项，最多 20 项。每项只允许以下字段：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `name` | string | 是 | 1 到 100 字符 |
| `email` | string | 与 `github` 至少一个 | 有效电子邮件格式，最长 254 字符 |
| `github` | string | 与 `email` 至少一个 | 1 到 39 字符；字母、数字和单个连字符组成，首尾必须是字母或数字 |

CMake 和 Debian Maintainer 当前使用第一位作者的 `name`；完整作者列表进入 publish JSON。

### `[project.assets]`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `icon` | repository path | 是 | 仓库内现有的 PNG 文件，最长 4096 字符 |
| `screenshots` | array of tables | 是 | 1 到 20 张截图 |

每个 `[[project.assets.screenshots]]` 包含：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `path` | repository path | 是 | 仓库内现有的 `.png`、`.webp`、`.jpg` 或 `.jpeg` 文件，最长 4096 字符 |
| `caption` | string | 是 | 截图说明，1 到 160 字符 |

资源路径必须是仓库相对路径，不能是绝对路径，不能包含 `..`，只能使用字母、数字、点、下划线、连字符和 `/`。文件扩展名使用小写，以符合 publish schema。

LPM 生成元数据时会检查图标和截图是否真实存在，因此移动资源后必须同步修改 `lpm.toml`。

### `[[project.permissions]]`

`project.permissions` 必填，但可以是空数组；最多 32 项。每项包含：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | enum string | 是 | 权限标识 |
| `reason` | string | 是 | 面向审核和用户的用途说明，1 到 500 字符 |
| `access` | enum string | 仅 `filesystem` | `read`、`write` 或 `read-write` |
| `scope` | enum string | 仅 `filesystem` | `app-data`、`shared-data` 或 `removable-storage` |

支持的权限 ID：

```text
network
filesystem
background-service
bluetooth
camera
microphone
location
serial
gpio
i2c
spi
imu
external-display
```

`filesystem` 必须同时提供 `access` 和 `scope`；其他权限禁止设置这两个字段。

### `[project.compatibility]`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `architectures` | string array | 是 | 恰好一个架构名，例如 `["arm64"]`、`["amd64"]`、`["armhf"]`、`["riscv64"]` 或 `["all"]` |
| `min_appkit_version` | string | 是 | 最低 AppKit 版本，最长 128 字符；格式为三段版本号，可带 prerelease 和 build 信息 |

合法示例包括 `0.1.0`、`1.2.0-rc.1` 和 `1.2.0+build.4`。

架构名最长 64 字符，由小写字母、数字和分隔各段的单个连字符组成；
支持 `hurd-i386` 这类名称。不维护 CPU 名称白名单，但拒绝 `source`、`any`
以及 `linux-any`、`any-amd64` 等含 `any` 段的架构通配符。
发布时还会检查 DEB 的 `Architecture` 与配置精确一致。
架构无关包必须声明 `["all"]`，不会自动匹配声明为 `["arm64"]` 的项目。
每个包版本仍只存储一个 DEB，不能用另一架构覆盖或追加已存在的同一版本。

### `[project.launcher]`

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `order` | integer | 是 | Launcher 排序值，范围 0 到 1000；数值越小越靠前 |

运行 `lpm metadata`、`lpm init` 的模板校验或当前模板的 CMake 配置时，
`[project]`、作者、资源、截图、权限、兼容性和 Launcher 表中的未知字段会被拒绝，以避免拼写错误被静默忽略。

## 运行库配置

运行库的 `[project]` 只接受 `kind` 和 `package`；包名使用合法的 Debian 包名。它不要求应用 ID、图标、截图或 Launcher 信息，也不从应用发布格式生成 Debian 元数据。运行库版本由自身的构建和打包配置维护，例如 Meshtasticd 的版本和 Debian 打包修订号。

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

[deploy]
host = "raspberrypi.local"
user = "pi"
port = 22
```

运行库支持 `test`、`pack`、`deploy`、`metadata`、`publish` 和 `unpublish`。`start`、`stop` 会明确报错，不会要求补充应用身份。`metadata --format publish-json` 输出本地运行库身份快照；版本和架构仍由 `publish` 从 Debian control 字段读取。`init` 仍用于从应用模板初始化应用。运行库发布提交 packages registry 的 `runtimes/<package>/` 记录。

## 派生输出

应用项目的派生输出包含完整的发布清单和 CMake 身份变量。运行库项目也可以使用
`lpm metadata --format publish-json` 或 `--format cmake`，但只输出运行库身份；版本、
架构、文件大小和 SHA-256 必须从实际 `.deb` 读取。

开发者不应再维护独立的 `publish.json`、应用身份 CMake 文件或在 `data/`
模板中重复填写产品信息。LPM 从 `[project]` 生成两类输出：

```sh
# 输出到 stdout
lpm metadata --format publish-json
lpm metadata --format cmake

# 写入文件，并自动创建父目录
lpm metadata --format publish-json --output publish.json
lpm metadata --format cmake --output build/generated/lpm_metadata.cmake
```

`--output` 相对执行命令时的工作目录解析。`publish.json` 是临时发布产物，应加入
`.gitignore`，不应手工编辑或提交。`lpm publish` 直接复用同一套校验和转换逻辑，
在内存中生成提交清单，不要求项目保存独立的 `publish.json`。

publish JSON 包含 `[project]` 对应的公开字段、`$schema` 和 publish
`schema_version`。它绝不会包含 `[build]`、`[deploy]`、SSH 主机、用户、端口或旧版 `project.executable`。

`--format cmake` 定义以下变量，供当前应用模板使用：

```text
LILYGO_UI_PACKAGE_NAME
LILYGO_UI_VERSION
LILYGO_UI_APP_ID
LILYGO_UI_APP_NAME
LILYGO_UI_APP_SUMMARY
LILYGO_UI_APP_DESCRIPTION
LILYGO_UI_APP_LICENSE
LILYGO_UI_APP_HOMEPAGE
LILYGO_UI_APP_ICON
LILYGO_UI_APP_ORDER
LILYGO_UI_MIN_APPKIT_VERSION
LILYGO_UI_PACKAGE_ARCHITECTURE
LILYGO_UI_PACKAGE_CONTACT
LILYGO_UI_EXECUTABLE_NAME
LILYGO_UI_CMAKE_PROJECT_NAME
LILYGO_UI_CATEGORIES
```

主要派生关系如下：

| `lpm.toml` 来源 | publish JSON | CMake/安装包用途 |
| --- | --- | --- |
| `package` | `package` | CMake project、可执行文件名、Debian 包名 |
| `version` | `version` | CPack/DEB 版本 |
| `app_id` | `app_id` | Launcher ID、desktop 文件名、图标名、AppStream ID |
| `title` | `title` | Launcher、desktop、AppStream 应用名称 |
| `summary` | `summary` | Launcher/desktop 描述、AppStream 摘要、DEB Description |
| `description` | `description` | AppStream 长描述 |
| `license` | `license` | AppStream project license |
| `homepage` | `homepage` | CPack/DEB Homepage |
| 第一位作者的 `name` | `authors[0].name` | CPack/DEB Maintainer |
| `categories` | `categories` | desktop Categories |
| `assets.icon` | `assets.icon` | 安装图标来源 |
| `compatibility` | `compatibility` | DEB 架构和最低 AppKit 依赖 |
| `launcher.order` | `launcher.order` | Launcher 排序 |
| `source_repo`、截图、权限和完整作者列表 | 对应字段 | 仅用于发布清单和 registry 审核 |

## `[build]`

CMake 后端的字段可省略，并使用表中的默认值。脚本后端需提供对应工作流的命令数组：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `backend` | string | `cmake` | `cmake` 或 `script`；决定 `test` 和 `pack` 的工作流 |
| `host_preset` | string | `host-simulator` | `start` 和 CMake `test` 使用的 configure/build preset |
| `cross_preset` | string | `cm0-cross` | CMake `pack` 使用的 configure/build preset |
| `test_command` | string array | 无 | 脚本后端的测试命令，必须包含独立参数 `{report}` |
| `pack_command` | string array | 无 | 脚本后端的打包命令，必须包含独立参数 `{output}` |
| `package_dir` | string | `dist` | `pack` 的默认输出目录，以及 `deploy` 和 `publish` 的默认包搜索目录 |

覆盖关系：

- `lpm start --preset` 和 `lpm test --preset` 覆盖 `host_preset`。
- `lpm pack --preset` 覆盖 `cross_preset`。
- `lpm pack --output` 覆盖 `package_dir`，但不会永久修改配置。
- `lpm deploy` 或 `lpm publish` 未显式指定包时，会递归选择 `package_dir` 下修改时间最新的 `.deb`。

### 脚本命令约定

命令为非空字符串参数数组，在项目根目录直接启动进程，不经过 shell。可以把解释器或可执行文件及其参数分开填写；不要把完整命令行写成一个字符串。支持的占位符必须独占一个参数：

| 占位符 | 可用命令 | 替换值 |
| --- | --- | --- |
| `{python}` | `test_command`、`pack_command` | 当前运行 LPM 的 Python 解释器 |
| `{report}` | `test_command`，必需 | 本次测试报告的绝对路径 |
| `{output}` | `pack_command`，必需 | 本次打包输出目录的绝对路径 |

例如使用 `["--report", "{report}"]`，不要使用 `"--report={report}"`。不会展开 shell 变量、通配符、`~`、管道或重定向；复杂步骤应写在仓库自己的脚本里。

`test_command` 必须运行测试并为本次运行写入有效的 JUnit XML 报告。LPM 不生成脚本测试结果，也不会用旧报告判定成功。`pack_command` 必须在输出目录内生成或更新至少一个 `.deb`，未变化的旧包不计入结果。脚本返回非零状态、测试报告缺失或无效、报告含失败或错误、打包无新产物都会使命令失败。

`start` 只支持 CMake 应用。脚本后端不接受 `test --preset` 或 `pack --preset`；改用脚本自身的配置管理工具链和构建选项。`--report`、`--output` 和 `[build].package_dir` 的路径及优先级规则不变。

## `[run]`

`[run]` 只影响本地 `lpm start`，不会进入应用发布清单。它可以在启动主机模拟器前准备外部服务，例如启动开发用的 Mihomo 容器：

```toml
[run]
prepare_command = ["{python}", "tools/prepare-dev-environment.py"]

[run.environment]
CLASH_DEMO = "0"
CLASH_API_URL = "http://127.0.0.1:19090"
CLASH_API_SECRET = "lilygo-local-dev"
CLASH_CONFIG_PATH = "{project}/.cache/mihomo-dev/config.yaml"
CLASH_CONTROLLER_CONFIG_PATH = "/root/.config/mihomo/config.yaml"
```

`prepare_command` 必须是非空字符串参数数组。LPM 在构建完成后、启动模拟器前直接执行它，不经过 shell；准备命令返回非零时，模拟器不会启动。`{python}` 替换为运行 LPM 的 Python 解释器，`{project}` 替换为项目根目录绝对路径。占位符必须作为独立参数使用。

`environment` 中的字符串会同时传给准备命令和模拟器。值中的 `{python}` 和 `{project}` 会展开；当前 shell 已存在的同名变量优先于项目配置，因此可以临时覆盖本地默认值。`lpm stop` 只停止模拟器进程，不会停止准备命令启动的外部服务。

## `[deploy]`

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `host` | string | `raspberrypi.local` | 目标主机名、IPv4 地址或 SSH Host 别名 |
| `user` | string | `pi` | SSH 用户名 |
| `port` | integer | `22` | SSH 端口，范围 1 到 65535 |

未配置或配置为空时，普通 `lpm deploy` 使用
`pi@raspberrypi.local:22`。主机和用户只能包含字母、数字、点、下划线和连字符，且必须以字母或数字开头、结尾。

命令行覆盖关系：

```sh
lpm deploy package.deb --host 192.168.1.42 --user pi --port 22
lpm deploy --target cm0-lab
lpm deploy --target cm0-lab --user root --port 2222 --no-sudo
```

- `--host`、`--user`、`--port` 的优先级高于 `[deploy]`。
- `--host` 和 `--target` 互斥。
- 使用 `--target` 时，默认不继承 `[deploy].user` 和 `[deploy].port`，用户与端口交给 SSH config；命令行显式值仍然优先。
- `--no-sudo` 使远端直接执行 `apt-get install --reinstall --yes`；默认通过 `sudo` 安装。相同版本也会重新安装。

LPM 不读取或保存密码、私钥内容。认证由系统 `ssh`、`scp`、SSH agent 和 SSH config 完成。部署应用与运行库时均使用 SCP 和 `apt-get install --reinstall --yes` 安装本地 `.deb`，即使目标机已安装相同版本也会重新安装，并由目标机 APT 从已配置的软件源解析缺失依赖；LPM 不会运行 `apt-get update`、设置 Meshtasticd 运行库路径或重启服务，这些操作由设备或镜像配置负责。

### 项目级 SSH config

多个目标板可以写入 `.lpm/ssh_config`：

```sshconfig
Host cm0-lab
    HostName 192.168.1.42
    User pi
    Port 22
    IdentityFile ~/.ssh/cm0_lab

Host cm0-test
    HostName 192.168.1.43
    User root

# 可选：继续加载用户级配置。项目中的已设置项优先。
Include ~/.ssh/config
```

只要该文件存在，LPM 就会为当前项目的 `ssh` 和 `scp` 加上
`-F .lpm/ssh_config`。该文件会替代命令默认读取的 `~/.ssh/config`；如需同时使用全局配置，应显式添加 `Include ~/.ssh/config`。

`.lpm/` 还包含模拟器状态、日志和测试报告，通常应加入 `.gitignore`。不要把私钥放入项目目录或提交到版本库。

## GitHub 认证

`lpm login`、`lpm publish` 和 `lpm unpublish` 不从 `lpm.toml` 读取 GitHub Token。
`publish` 和 `unpublish` 按以下顺序选择认证来源：

1. `GH_TOKEN` 环境变量。
2. `GITHUB_TOKEN` 环境变量。
3. `${XDG_CONFIG_HOME:-~/.config}/lpm/credentials.json` 中由 `lpm login` 保存的 Token。

`lpm login` 使用 GitHub OAuth Device Flow，不依赖 `gh`。LPM 内置了由
`LILYGO-UI` 组织持有且已启用 Device Flow 的 OAuth App Client ID。Client ID 不是
Secret；开发或测试其他 OAuth App 时可通过 `LPM_GITHUB_CLIENT_ID` 覆盖：

```sh
# 使用内置 Client ID 进行本机交互登录
lpm login

# 可选：开发时覆盖内置 Client ID
LPM_GITHUB_CLIENT_ID="<oauth-app-client-id>" lpm login

# CI 中由 Secret 注入，不写入 lpm.toml
GH_TOKEN="$LPM_PUBLISH_TOKEN" lpm publish dist/application.deb
```

登录时 LPM 请求 `public_repo` 和 `workflow` 权限，在终端显示一次性验证码并打开
GitHub 验证页面。`workflow` 用于同步用户 `packages` Fork 中的
`.github/workflows` 文件；如果旧 Token 没有该权限，需要重新运行 `lpm login`。
授权成功后，凭证目录权限为 `0700`，凭证文件权限为 `0600`；再次登录会原子替换旧
Token。不要提交、共享或手工编辑该文件。

发布 Token 代表的 GitHub 用户将成为首次发布应用的 owner，并需要创建或写入该用户
的公开 `packages` Fork，以及向 `LILYGO-UI/packages` 创建 Pull Request。第三方仓库
GitHub Actions 自动提供的 `GITHUB_TOKEN` 通常只对当前源码仓库有效，不能完成这些
跨仓库操作；应改用具备相应公开仓库权限和 `workflow` 权限的用户 PAT，并通过 Secret 注入
`GH_TOKEN`。Token 不应写入 `lpm.toml`、生成的 publish JSON、日志或版本库。

## 命令与配置依赖

下表描述默认 CMake 应用和旧项目；运行库与脚本后端的要求见上文。

| 命令 | 使用的配置 | 缺少 `[project]` 时的行为 |
| --- | --- | --- |
| `lpm init` | 模板的全部配置 | 模板必须包含有效 `[project]`；初始化会更新身份、版本和描述，并重置部署默认值 |
| `lpm metadata` | `schema_version`、`[project]` | 失败；无法生成 CMake 或 publish JSON |
| `lpm start` | `build.host_preset`、`project.package` | 当前模板的正常构建会失败；仅 `--no-build --executable <path>` 等兼容方式可绕过产品身份解析 |
| `lpm stop` | `.lpm/simulator.json` | 不直接依赖 `[project]` |
| `lpm test` | `build.host_preset` | 当前模板会在 CMake 配置阶段失败，因为 CMake 需要项目元数据 |
| `lpm pack` | `build.cross_preset`、`build.package_dir` | 当前模板会在 CMake 配置阶段失败，因为 CMake/CPack 需要项目元数据 |
| `lpm deploy` | `build.package_dir`、`[deploy]` | 不直接依赖 `[project]` |
| `lpm login` | 不读取 `lpm.toml`；使用 `LPM_GITHUB_CLIENT_ID` | 不读取项目，可在任意目录执行 |
| `lpm publish` | `schema_version`、有效 `[project]`、`build.package_dir`（未指定包时） | 失败；无法确认项目包名或 Debian 包身份 |
| `lpm unpublish` | `schema_version`、有效 `[project]`（应用使用 `app_id`，运行库使用 `package`） | 失败；无法确认 Registry 身份；命令行 `VERSION` 可与 `project.version` 不同 |

因此，当前模板项目不能删除 `[project]`。它不仅服务于未来发布，还负责构建期的应用身份、安装元数据和包信息。

## 旧项目兼容

若目录中没有 `lpm.toml`，LPM 会把最近包含 `CMakeLists.txt` 的目录识别为旧项目，并为工作流字段使用默认值。不过：

- `lpm metadata` 必须有 `schema_version = 1` 和完整 `[project]`，因此不能用于无配置旧项目。
- `lpm publish` 和 `lpm unpublish` 同样需要完整、有效的 `[project]`，不能用于无配置旧项目。
- `start` 的可执行文件解析顺序是 `--executable`、`project.package`、旧版 `project.executable`、`cmake/AppIdentity.cmake`。
- 旧项目的 `test`、`pack` 是否可用取决于它自己的 CMake preset 和 CPack 配置。
- 新项目不应再创建 `project.executable` 或 `cmake/AppIdentity.cmake`。
