# LPM 命令业务逻辑

本文说明 LPM 各命令在参数解析之后实际执行的业务流程，包括配置优先级、外部命令、状态文件、产物和失败处理。字段格式与完整取值范围见[配置参考](configuration.md)。

## 通用执行模型

除 `lpm init` 和不读取项目的 `lpm login` 外，命令首先从当前工作目录逐级向父目录查找项目。每一级目录都先检查 `lpm.toml`，再检查 `CMakeLists.txt`，发现任意一个后立即停止：

1. 遇到最近的 `lpm.toml` 时，以其所在目录为项目根目录并解析配置。
2. 当前级没有 `lpm.toml`，但有 `CMakeLists.txt` 时，把该目录识别为旧 CMake 项目，并使用空配置和工作流默认值；不会继续寻找更上层的 `lpm.toml`。
3. 两者都找不到时，命令失败并报告当前目录不属于 LPM 或 CMake 项目。

所有 CMake、CTest、CPack、Git、SSH 和 SCP 子进程都以项目根目录或明确的临时目录作为工作目录。LPM 不通过 shell 拼接本地命令，而是以参数数组直接启动进程，保留子进程的标准输出和标准错误。

### 配置优先级

通常按以下顺序取值，越靠前优先级越高：

```text
命令行显式参数
        ↓
lpm.toml 对应字段
        ↓
LPM 内置默认值
        ↓
旧项目兼容推导（仅特定功能）
```

`start` 的可执行文件是例外，它按 `--executable`、`project.package`、旧版 `project.executable`、旧版 `cmake/AppIdentity.cmake` 的顺序解析。

### 外部命令检查

LPM 在使用外部工具前通过当前 `PATH` 查找可执行文件。找不到时会报告 `required command is not installed`。当前命令可能使用：

| LPM 命令 | 外部命令 |
| --- | --- |
| `init` | 远程模板需要 `git`；本地模板不需要 |
| `start` | 构建时需要 `cmake`；`--no-build` 不需要 |
| `stop` | Unix 信号；进程校验可能调用 `ps` |
| `test` | `cmake`、`ctest` |
| `pack` | `cmake`、`cpack` |
| `metadata` | 无 |
| `deploy` | `scp`、`ssh`；远端需要 `dpkg`，默认还需要 `sudo` |
| `login` | 无；通过 HTTPS 执行 GitHub OAuth Device Flow |
| `publish` | `dpkg-deb` |
| `unpublish` | 无 |

外部命令返回非零状态时，LPM 立即停止当前流程，不继续执行后续步骤。

### 全局选项

- `lpm --version` 直接输出 LPM 版本，不发现或读取项目。
- `lpm --help` 和 `lpm <command> --help` 由参数解析器生成帮助，也不执行对应业务流程。

## 命令总览

| 命令 | 核心职责 | 主要输入 | 主要产物或状态 |
| --- | --- | --- | --- |
| `lpm init` | 从模板创建独立应用项目 | 路径、名称、版本、描述、模板 | 新项目目录和定制后的 `lpm.toml` |
| `lpm metadata` | 校验并派生项目元数据 | `lpm.toml [project]` | CMake 文本或 publish JSON |
| `lpm start` | 构建并运行主机模拟器 | host preset、可执行文件、运行参数 | 模拟器进程、日志和 PID 状态 |
| `lpm stop` | 安全停止后台模拟器 | PID 状态、超时 | 清理模拟器 PID 状态 |
| `lpm test` | 构建并运行主机测试 | host preset、报告路径 | JUnit XML 报告 |
| `lpm pack` | 交叉构建并生成 Debian 包 | cross preset、输出目录 | 一个或多个 `.deb` |
| `lpm deploy` | 上传并安装已有 Debian 包 | 包路径、SSH 目标 | 目标设备上的已安装包 |
| `lpm login` | 登录 GitHub | OAuth App Client ID、浏览器授权 | LPM 管理的 GitHub 凭证文件 |
| `lpm publish` | 提交应用版本审核 | `lpm.toml`、`.deb`、资源文件 | Fork prerelease 与发布 PR |
| `lpm unpublish` | 请求下架某个版本 | 版本号、公开原因 | 将版本改为 `yanked` 的 PR |

## `lpm init`

### 参数决策

```sh
lpm init [PATH] \
  [--name NAME] \
  [--app-version VERSION] \
  [--description TEXT] \
  [--template URL_OR_PATH] \
  [--yes]
```

- `PATH` 默认为当前目录。
- 默认项目名由目标目录名转为小写 kebab-case；无法得到名称时使用 `application`。
- 默认版本是 `0.1.0`。
- 默认描述是 `<name> application for LILYGO UI`。
- 在交互终端且未传 `--yes` 时，LPM 会询问缺失的名称、版本和描述。
- 非交互环境或 `--yes` 模式不会询问，直接使用命令行值或默认值。

名称必须是小写 kebab-case；名称应以字母开头，因为生成的应用 ID 最后一段必须以大写字母开头。初始化版本必须是两段或三段数字版本，可带 prerelease 或 build 后缀。

### 执行流程

1. 规范化并校验名称、版本。
2. 创建目标目录；目录必须为空，否则停止，不覆盖已有内容。
3. 在目标目录同级创建 `.lpm-init-*` 临时目录。
4. 获取模板：
   - 本地目录或 `file:` URL 直接使用。
   - 其他地址执行 `git clone --recurse-submodules --depth 1`。
5. 将模板复制到临时 staging 目录，同时跳过 `.git`、`build`、`dist`、`.cache` 和 `.lpm`。
6. 读取模板的 `lpm.toml`，更新：
   - `project.package = lilygo-ui-<name>`
   - `project.version`
   - `project.app_id = cc.lilygo.ui.<PascalCaseName>`
   - `project.title`
   - `project.summary`
   - `project.description`
7. 将 `[deploy]` 重置为 `pi@raspberrypi.local:22`，避免复制模板作者的设备地址。
8. 用 publish 元数据规则校验定制后的 `[project]`，包括资源文件是否存在。
9. 重新渲染完整 `lpm.toml`，再把 staging 内容复制到目标目录。
10. 临时目录自动删除，CLI 输出最终项目绝对路径。

### 失败边界

- 模板缺少有效 `lpm.toml [project]` 时不会生成降级项目。
- 模板中的作者、资源、权限、兼容性等非身份字段会被保留，但它们也必须通过校验。
- LPM 不初始化新的 Git 仓库；远程模板自身的 `.git` 不会复制到目标项目。

## `lpm metadata`

```sh
lpm metadata --format cmake|publish-json [--output PATH]
```

### 执行流程

1. 发现项目并加载 `lpm.toml`。
2. 要求 `schema_version = 1` 和完整 `[project]`。
3. 校验包名、版本、应用 ID、作者、分类、资源、权限、兼容性和 Launcher 顺序。
4. 检查图标和所有截图文件真实存在。
5. 根据 `--format` 渲染：
   - `cmake`：生成一组 `set(LILYGO_UI_...)` 变量，供模板配置阶段 include。
   - `publish-json`：生成符合 publish schema 的 JSON，不包含 `[build]`、`[deploy]` 或旧版 `project.executable`。
6. 未指定 `--output` 时写到 stdout；指定后创建父目录并写入该文件。

`--output` 是相对于执行命令时工作目录解析的路径。`publish.json` 是临时派生产物，不是新的配置入口。

### 与 CMake 的关系

当前应用模板在 `project()` 之前执行 `lpm metadata --format cmake`，把输出写到构建目录并 include。因此 `start`、`test` 和 `pack` 只要触发当前模板的 CMake 配置，就会间接执行同一套项目元数据校验。

## 共享构建阶段

`start`、`test` 和 `pack` 都复用同一个 configure/build 流程：

```sh
cmake --preset <preset>
cmake --build --preset <preset> --parallel
```

配置失败时不会进入构建；构建失败时不会进入启动、测试或打包阶段。LPM 不自动清理构建目录，也不隐式重新初始化 Git submodule 或安装编译依赖。

## `lpm start`

```sh
lpm start \
  [--preset PRESET] \
  [--executable NAME_OR_PATH] \
  [--no-build] \
  [--foreground] \
  [-- PROGRAM_ARGS...]
```

### 执行流程

1. 选择 preset：`--preset`、`build.host_preset`、`host-simulator`。
2. 默认执行共享构建阶段；`--no-build` 完全跳过 CMake。
3. 解析模拟器可执行文件：
   - `--executable`
   - `project.package`
   - 旧版 `project.executable`
   - 从旧版 `cmake/AppIdentity.cmake` 的组件 slug 推导
4. 相对可执行文件名在 `build/<preset>/` 下查找；直接路径不存在时，会递归查找同名且可执行的文件。
5. 只有一个可执行匹配时使用它；没有唯一结果时失败。
6. `--` 后的参数原样传给模拟器。
7. 根据运行模式进入前台或后台流程。

### 前台模式

`--foreground` 使用普通子进程运行，继承当前终端输入输出。LPM 等待模拟器退出，并把模拟器退出码作为命令结果。前台模式不创建 PID 状态和日志文件。

### 后台模式

后台启动前读取 `.lpm/simulator.json`：

- 状态指向同一项目、PID 仍存活且命令行包含预期可执行文件时，拒绝重复启动。
- 过期或不完整的可解析状态会被删除。
- PID 已被系统复用且命令不匹配时，不向该进程发送信号，并允许启动新模拟器。

随后 LPM：

1. 以独立 session/进程组启动模拟器，stdin 指向 `/dev/null`。
2. 将 stdout 和 stderr 追加到 `.lpm/simulator.log`。
3. 等待 0.1 秒；若进程已经退出，报告立即退出并要求检查日志。
4. 进程仍存活时写入 `.lpm/simulator.json`：

```json
{
  "pid": 1234,
  "executable": "/absolute/path/to/lilygo-ui-demo",
  "project": "/absolute/path/to/project",
  "started_at": 1780000000.0
}
```

## `lpm stop`

```sh
lpm stop [--timeout SECONDS]
```

`--timeout` 默认为 5 秒，必须大于 0。

### 执行流程

1. 读取 `.lpm/simulator.json`；没有可读状态时报告模拟器未运行。
2. 校验 PID、可执行文件和项目路径字段。
3. 若状态属于另一个项目，拒绝操作。
4. 若 PID 已不存在，删除状态文件并报告未运行。
5. 读取 `/proc/<pid>/cmdline`；没有 `/proc` 时使用 `ps`。命令行不包含记录的可执行文件时，认为 PID 已被复用并拒绝发送信号。
6. 向整个进程组发送 `SIGTERM`。
7. 每 50 毫秒检查一次，最多等待 `--timeout`。
8. 超时后向整个进程组发送 `SIGKILL`。
9. 删除 `.lpm/simulator.json`。

使用进程组而不是单个 PID，可以同时结束模拟器创建的子进程。命令匹配检查是尽力保护机制，无法确认身份时宁可拒绝停止，也不会向未知进程发送信号。

## `lpm test`

```sh
lpm test [--preset PRESET] [--report PATH]
```

### 执行流程

1. 选择 preset：`--preset`、`build.host_preset`、`host-simulator`。
2. 执行共享 CMake configure/build 阶段。
3. 创建报告父目录。
4. 执行：

```sh
ctest --preset <preset> \
  --output-on-failure \
  --output-junit <report>
```

默认报告是 `<project>/.lpm/test-results.xml`。显式 `--report` 相对于执行命令时的工作目录解析。任何测试失败都会使 `ctest` 返回非零，LPM 随即以失败结束。

## `lpm pack`

```sh
lpm pack [--preset PRESET] [--output DIRECTORY]
```

### 执行流程

1. 选择 preset：`--preset`、`build.cross_preset`、`cm0-cross`。
2. 执行共享 CMake configure/build 阶段。
3. 选择输出目录：`--output`、`build.package_dir`、`dist`，并创建目录。
4. 要求 `build/<preset>/CPackConfig.cmake` 已由配置阶段生成。
5. 执行：

```sh
cpack --config build/<preset>/CPackConfig.cmake -B <output>
```

6. 递归扫描输出目录，只收集本次 CPack 执行期间生成或更新的 `.deb`。
7. 排除 `_CPack_Packages` 内部 staging 副本，按修改时间排序并逐个输出最终包路径。

CPack 成功但没有发现新的 Debian 包时，LPM 仍判定 `pack` 失败。LPM 当前只返回 `.deb`，不会把其他 CPack generator 的产物作为成功结果。

## `lpm deploy`

```sh
lpm deploy [PACKAGE.deb] \
  [--host HOST | --target SSH_ALIAS] \
  [--user USER] \
  [--port PORT] \
  [--no-sudo]
```

`deploy` 只安装已有包，不会自动执行 `pack`。

### 包选择

1. 指定 `PACKAGE.deb` 时，相对于执行命令的工作目录解析。
2. 未指定时，在 `<project>/<build.package_dir>/` 下递归选择修改时间最新的 `.deb`。
3. 默认目录是 `dist`。
4. 文件必须存在且扩展名精确为 `.deb`。

### 目标选择

普通主机模式的优先级：

```text
--host / --user / --port
        ↓
[deploy].host / user / port
        ↓
raspberrypi.local / pi / 22
```

`--host` 和 `--target` 互斥。使用 `--target` 时：

- `--target` 值直接作为 SSH Host 别名。
- 默认不继承 `[deploy].user` 和 `[deploy].port`，交给 SSH config 或 SSH 默认值处理。
- 显式 `--user`、`--port` 仍然有效。

主机、别名和用户会在调用外部命令前校验，端口必须在 1 到 65535 之间。

### SSH 配置

如果项目存在 `.lpm/ssh_config`，SCP 和 SSH 都会自动增加：

```sh
-F <project>/.lpm/ssh_config
```

该参数会让项目配置替代 SSH 默认配置文件；需要全局配置时，应在项目文件中显式 `Include ~/.ssh/config`。

### 上传与安装

1. 根据本地包绝对路径计算 12 位 SHA-256 前缀，生成稳定且不易冲突的远端路径：

```text
/tmp/lpm-<12-hex-digits>.deb
```

2. 通过 SCP 上传：

```sh
scp [SSH_OPTIONS] <package> <user@host:/tmp/lpm-....deb>
```

3. 通过 SSH 执行远端命令：

```sh
set -e; sudo -- dpkg --install /tmp/lpm-....deb; rm -f -- /tmp/lpm-....deb
```

`--no-sudo` 会移除 `sudo --`。远端参数经过 shell quoting，避免包路径被解释为额外命令。

只有安装成功后才执行远端清理。若 SCP 成功但 SSH、sudo 或 dpkg 失败，LPM 返回失败，远端临时包可能保留在 `/tmp`，需要人工检查。LPM 不做安装回滚，也不会自动卸载旧版本。

## 状态文件与产物

| 路径 | 创建者 | 生命周期与用途 |
| --- | --- | --- |
| `lpm.toml` | 模板、`init` | 唯一人工维护的项目与工作流配置 |
| `.lpm/simulator.json` | 后台 `start` | 当前模拟器 PID 和身份；成功 `stop` 后删除 |
| `.lpm/simulator.log` | 后台 `start` | 模拟器 stdout/stderr，采用追加写入 |
| `.lpm/test-results.xml` | `test` | 默认 JUnit 报告，可由 `--report` 覆盖 |
| `.lpm/ssh_config` | 开发者 | 项目级 OpenSSH 配置，LPM 只读取 |
| `build/<preset>/` | CMake | 配置与构建输出，LPM 不主动清理 |
| `dist/*.deb` | `pack`/CPack | 默认 Debian 包输出；目录可配置或覆盖 |
| `publish.json` | `metadata` | 可选的临时派生清单，应忽略且不提交 |

## 退出状态与错误处理

| 情况 | CLI 退出码 |
| --- | --- |
| 命令成功 | `0` |
| LPM 校验、文件、状态或外部命令错误 | `1` |
| 用户发送 `Ctrl-C` | `130` |
| argparse 参数错误 | `2` |

预期错误统一写到 stderr，格式为：

```text
lpm: error: <原因>
```

外部命令失败时，错误会包含退出码和实际参数。前台模拟器返回非零状态也按外部命令失败处理，CLI 最终返回 `1`。LPM 不吞掉 CMake、CTest、CPack、SSH 等工具的正常输出，便于直接定位底层问题。

## `lpm login`

```sh
lpm login
```

LPM 直接执行 GitHub OAuth Device Flow，不依赖 GitHub CLI：

1. 从 `LPM_GITHUB_CLIENT_ID` 读取已启用 Device Flow 的 GitHub OAuth App Client ID。
2. 请求 `public_repo` 权限，并在终端显示一次性验证码和验证地址。
3. 尝试打开浏览器；浏览器未自动打开时，可手工访问终端显示的地址。
4. 按 GitHub 返回的间隔轮询授权结果；遇到 `slow_down` 时自动延长轮询间隔。
5. 授权成功后，把 Token 保存到
   `${XDG_CONFIG_HOME:-~/.config}/lpm/credentials.json`。

OAuth App Client ID 不是 Secret，可以由正式发行版内置；当前源码未内置 Client ID，
因此运行前必须设置：

```sh
export LPM_GITHUB_CLIENT_ID="<oauth-app-client-id>"
lpm login
```

LPM 创建凭证目录时使用 `0700` 权限，凭证文件使用 `0600` 权限。再次登录会原子替换
已有 Token。`publish` 和 `unpublish` 按 `GH_TOKEN`、`GITHUB_TOKEN`、LPM 凭证文件的
顺序选择认证来源。

在第三方项目的 GitHub Actions 中，默认 `GITHUB_TOKEN` 只对当前应用仓库有效，
不能创建或写入用户的 `packages` Fork，也不能向中央仓库提交发布 PR。CI 发布必须
把具备公开仓库权限的用户 PAT 保存为 Actions Secret，并以 `GH_TOKEN` 注入；CI
不应依赖开发者机器上的 LPM 凭证文件。不要把 Token 写入 `lpm.toml`、日志或发布清单。

## `lpm publish`

```sh
lpm publish [PACKAGE.deb]
```

未指定包时，按 `deploy` 相同的规则选择 `build.package_dir` 中修改时间最新的
`.deb`。显式传入的相对路径以执行命令时的工作目录为基准。发布不会自动运行
`pack`，也不会使用预先生成的 `publish.json`。

### 本地阶段

1. 用 `metadata --format publish-json` 相同的规则构建内存清单。
2. 使用 `dpkg-deb --field` 读取 `Package`、`Version` 和 `Architecture`。
3. 要求三个字段分别匹配清单中的包名、版本和架构。
4. 要求 `.deb` 小于 2 GiB，并流式计算文件大小与 SHA-256；中央文件名为
   `sha256-<64-hex-digits>.deb`。
5. 图标和截图改写为 `assets/sha256/<prefix>/<digest>.<ext>`，避免不同版本覆盖
   同名资源。图标最大 1 MiB，单张截图最大 2 MiB。

### GitHub 阶段

1. 读取 `LILYGO-UI/packages` 默认分支和当前 GitHub 用户。
2. 创建或复用 `<user>/packages` Fork，并确认它的 parent/source 是中央仓库。
3. 从中央默认分支创建唯一发布分支，提交 `app.json`、版本记录和资源。
4. 在 Fork 创建公开 prerelease，把 `.deb` 作为 Release asset 上传。
5. 向中央默认分支创建发布 Pull Request，输出 URL 和 `pending_review` 状态。

首次发布会创建应用所有权文件，唯一 owner 是 PR 作者。已有应用要求当前 GitHub
用户在 owner 列表中。同一个版本文件一旦存在便拒绝覆盖，即使该版本已下架。

Fork prerelease 是审核暂存区，不是 AppStore 的正式下载位置。PR 合并后，中央仓库
Action 会重新下载并验证包，把它转存到 `apt-pool-<package>` Release；只有转存
成功并确认所有 `published` asset 存在后才部署 Pages。

## `lpm unpublish`

```sh
lpm unpublish VERSION --reason REASON
```

`VERSION` 必须是合法的 Debian 版本，但不必等于本地 `project.version`；它指定中央
Registry 中要下架的历史版本。`REASON` 去除首尾空白后必须为 1 到 500 个字符，并会
公开出现在版本记录和 Pull Request 中。

该命令要求当前 GitHub 用户是应用 owner，然后读取中央版本记录并创建 Fork 分支和
Pull Request。变更只能把 `status` 从 `published` 改为 `yanked`，同时写入公开原因
和请求者；manifest、哈希、下载地址及其他历史字段保持不变。

合并后 Pages 索引不再把该版本作为安装或升级候选，但应用详情仍保留下架状态、
原因和中央下载记录。中央 Release asset 不会物理删除。
