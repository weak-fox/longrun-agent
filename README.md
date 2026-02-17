# longrun-agent

一个用于“长时间多轮编码”的通用 harness。  
它把一次性上下文很短的 coding agent，变成可以跨多轮会话持续推进、可审计、可恢复的执行系统。

## 这个项目解决什么问题

长跑型 agent 开发常见问题：
- 每轮上下文重置，不知道上轮做到哪了
- 特性清单被误改，后续方向漂移
- 会话失败后很难恢复
- 没有统一门禁，容易出现“看起来完成，实际没完成”

`longrun-agent` 的做法：
- 用 `feature_list.json` 做单一事实源（source of truth）
- 每轮生成会话工件（prompt、日志、元数据）
- 提供硬门禁（gate）和失败修正（remediation）
- 支持多后端（`codex_cli` / `claude_sdk`）共用一套上层编排

## 命令

```bash
longrun-agent bootstrap
longrun-agent bootstrap --guided
longrun-agent configure [--non-interactive ...]
longrun-agent go --goal "..."
longrun-agent run-session [--backend ... --profile ... --backend-model ... --model-reasoning-effort ...]
longrun-agent run-loop [--max-sessions ... --continue-on-failure --backend ... --profile ... --backend-model ... --model-reasoning-effort ...]
longrun-agent status [--json]
```

## 自动验证脚本

仓库提供了一个真实 CLI 回归脚本（隔离临时目录运行）：

```bash
./scripts/verify_cli_matrix.sh
```

脚本会覆盖：
- 所有子命令 `--help`
- `bootstrap/configure/run-session/run-loop/status/go` 主路径
- 关键 gate 与限制项（`commit/progress/repair/clean_git/max_features/max_no_progress/pre_coding/verification`）
- 运行时覆盖参数传递（`--backend-model`、`--model-reasoning-effort`）
- 默认本地配置路径（无显式 `--config` 时使用当前目录 `./longrun-agent.toml`）

可选环境变量：
- `LR_BIN`：指定 `longrun-agent` 可执行文件路径
- `PY_BIN`：指定 Python 路径
- `KEEP_TMP=1`：失败后保留临时目录，便于排查

Codex 沙箱说明：
- 默认命令显式使用 `--sandbox workspace-write`。
- 若日志出现 “read-only sandbox”，harness 会自动重试一次（workspace-write 强制覆盖）。

提示：
- Anthropic “文章同款”模式请使用：`--backend claude_sdk --profile article`
- 想要最省心的一键流程请使用：`longrun-agent go --goal "..."`
- 若当前目录没有本地 `longrun-agent.toml`，CLI 会在当前目录创建该文件

## 核心概念（务必先读）

运行目录中关键文件：
- `.longrun/artifacts/app_spec.txt`：你要构建什么
- `.longrun/artifacts/feature_list.json`：特性/测试清单（只允许更新 `passes`；禁止修改描述、步骤、顺序）
- `.longrun/artifacts/claude-progress.txt`：每轮摘要
- `.longrun/sessions/session-XXXX/`：会话工件目录
- `.longrun/remediation/session-XXXX.json`：门禁失败修正报告

会话阶段：
- `initializer`：首轮初始化（创建基础状态）
- `coding`：后续持续实现
- `repair`：当验证失败且允许自动修复时触发

## 快速开始（推荐路径）

### 0) 本地初始化（推荐先执行）

```bash
bash .longrun/artifacts/init.sh
```

`.longrun/artifacts/init.sh` 会自动：
- 创建/复用 `.venv-longrun`
- 安装当前项目（editable 模式）
- 在缺少核心文件时执行 `longrun-agent bootstrap`
- 校验 `feature_list.json` 结构

初始化完成后：

```bash
source .venv-longrun/bin/activate
longrun-agent status
longrun-agent run-session
```

### 1) 安装

```bash
python -m pip install -e .
```

建议使用项目独立虚拟环境（避免污染系统 Python）：

```bash
python3 -m venv .venv-longrun
source .venv-longrun/bin/activate
python -m pip install -e .
```

若使用 `claude_sdk` 后端，还需要：

```bash
python -m pip install -e .[article]
npm install -g @anthropic-ai/claude-code
export ANTHROPIC_API_KEY='your-api-key'
```

### 2) 初始化配置和基础文件

```bash
longrun-agent bootstrap
```

如果你想用引导式问答来生成任务目标和 `app_spec.txt`（默认写到 `.longrun/artifacts/`）：

```bash
longrun-agent bootstrap --guided
```

`--guided` 现在是 agent 协作模式：
- 你只输入一句 `Product goal`
- harness 调用后端 agent 自动补全 users / flows / constraints / done criteria
- 你确认草案后，仅调整 `feature_target`（首轮建议 `20-80`）

如果 agent 补全失败，会自动降级到手动问题模式。

会生成/准备：
- `longrun-agent.toml`
- `.longrun/artifacts/app_spec.txt`
- `.longrun/artifacts/claude-progress.txt`
- `.longrun/` 状态目录

### 3) 修改 `.longrun/artifacts/app_spec.txt`

把你的产品目标、主要流程、约束写清楚。

### 4) 配置后端（推荐先用向导）

交互式配置（文本向导）：

```bash
longrun-agent configure
```

非交互配置（适合脚本/CI）：

```bash
longrun-agent configure \
  --non-interactive \
  --backend codex_cli \
  --profile default \
  --backend-model gpt-5.2-codex \
  --model-reasoning-effort xhigh \
  --codex-timeout-seconds 3600
```

### 5) 或手动编辑 `longrun-agent.toml`

最常用：`codex_cli`

```toml
[runtime]
backend = "codex_cli"
profile = "default"
backend_model = "gpt-5.2-codex"
model_reasoning_effort = "xhigh"

[backends.codex_cli]
command = ["bash", "-lc", "LONGRUN_PHASE=\"{phase}\" codex exec --skip-git-repo-check --full-auto --sandbox workspace-write -m \"{backend_model}\" -C \"{project_dir}\" < \"{prompt_file}\""]
model = "gpt-5.2-codex"
timeout_seconds = 3600
```

如果要用 Anthropic 风格 profile：

```toml
[runtime]
backend = "claude_sdk"
profile = "article"
backend_model = "claude-sonnet-4-5-20250929"
```

### 6) 运行

跑一轮：

```bash
longrun-agent run-session
```

持续跑：

```bash
longrun-agent run-loop --max-sessions 50
```

查看状态：

```bash
longrun-agent status
longrun-agent status --json
```

## 一键模式（推荐新手）

```bash
longrun-agent go --goal "做一个给小团队用的任务看板"
```

默认行为：
- 使用当前配置的 backend/model（可命令行覆盖）
- 引导式补全 `app_spec.txt`（默认写到 `.longrun/artifacts/`，支持 agent 反问澄清需求）
- 自动跑 `run-loop`（默认 `--max-sessions 20`，且默认 `--continue-on-failure`）
- 默认要求在项目 `.venv-longrun` 中运行（可用 `--allow-any-python` 跳过，属于高级参数）
- 首次 `go`（无配置文件）会先进入必填配置向导，再继续执行

首次必填向导会询问：
- `Backend`
- `Profile`
- `Backend model`
- `Project dir`
- `State dir`（默认 `<project_dir>/.longrun`）
- `commit/progress/repair` 三个 gate 开关

常用参数：
- `--goal`：产品目标一句话（当 `.longrun/artifacts/app_spec.txt` 未有明确目标时建议必传）
- `--backend` / `--backend-model`
- `--max-sessions`
- `--continue-on-failure` / `--no-continue-on-failure`
- `--feature-target`

高级参数：
- `--brainstorm-rounds`：反问轮数（默认 `2`）
- `--skip-brainstorm`：跳过反问
- `--non-interactive`：全程不提问，仅使用传参与默认值
- `--yes`：自动接受草案建议

## `configure` 参数说明

- `--backend`：`codex_cli` / `claude_sdk`
- `--profile`：`default` / `article`
- `--backend-model`：后端模型名
- `--model-reasoning-effort`：模型推理强度（`codex_cli` 生效）
- `--project-dir`：设置项目目录
- `--state-dir`：设置 harness 状态目录（`sessions/lock/remediation`），可与项目目录分离
- `--artifacts-dir`：设置生成文件目录（`app_spec.txt` / `feature_list.json` / `claude-progress.txt` / `init.sh`），默认在 `<state_dir>/artifacts`
- `--codex-command`：Codex 命令模板（字符串，内部用 shell 规则切分）
- `--codex-timeout-seconds`：Codex 命令超时
- `--commit-required` / `--no-commit-required`
- `--progress-update-required` / `--no-progress-update-required`
- `--repair-on-verification-failure` / `--no-repair-on-verification-failure`
- `--non-interactive`：只应用显式参数，不弹交互问题

## 统一运行时覆盖参数

你可以不改配置文件，直接在命令行临时覆盖：

```bash
longrun-agent run-loop \
  --backend claude_sdk \
  --profile article \
  --backend-model claude-sonnet-4-5-20250929 \
  --max-sessions 20

# 连续失败时不中断（需要设置 max-sessions）
longrun-agent run-loop --max-sessions 20 --continue-on-failure
```

`run-session` 也支持同样的覆盖参数。

## 配置说明（`longrun-agent.toml`）

### `[runtime]`
- `backend`：`codex_cli` 或 `claude_sdk`
- `profile`：`default` 或 `article`
- `backend_model`：当前运行后端的模型名（`codex_cli` 和 `claude_sdk` 都可用）
- `model_reasoning_effort`：可选模型推理强度（当前仅 `codex_cli` 使用，其他后端忽略）

### `[backends.codex_cli]`
- `command`：命令模板，支持占位符：
  - `{project_dir}`
  - `{session_dir}`
  - `{prompt_file}`
  - `{phase}`
  - `{backend_model}`
  - `{model_reasoning_effort}`（原始值，字符串）
  - `{model_reasoning_effort_toml}`（`model_reasoning_effort="xhigh"` 形式）
- `model`：Codex 默认模型（当 `runtime.backend_model` 缺省时使用）
- `timeout_seconds`：会话超时

### `[backends.claude_sdk]`
- `model`：默认模型（会被 `runtime.backend_model` 或 CLI `--backend-model` 覆盖）

### `[gates]`
- `commit_required`：每轮必须产生新 commit（默认 `false`）
- `progress_update_required`：每轮必须更新进度文件（默认 `false`）
- `repair_on_verification_failure`：验证失败时是否自动跑一次 repair（默认 `false`）

### `[harness]`
- `project_dir`：项目目录
- `state_dir`：harness 状态目录（`sessions/lock/remediation`）；留空时默认 `<project_dir>/.longrun`
- `artifacts_dir`：业务生成文件目录（`app_spec.txt` / `feature_list.json` / `claude-progress.txt` / `init.sh`）；留空时默认 `<state_dir>/artifacts`
- `feature_target`：initializer 最少 feature 数（默认 `200`）
- `max_features_per_session`：单轮最多新增通过 feature 数（默认 `3`）
- `max_no_progress_sessions`：连续无进展熔断阈值（默认 `5`）
- `pre_coding_commands`：coding 前回归命令（失败即中止当轮）
- `verification_commands`：会话后验证命令（失败触发 gate）
- `bearings_commands`：每轮定位命令
- `require_clean_git`：是否要求会话结束后工作区干净

## 会话目录与审计信息

每轮会写到（`state_dir` 留空时是 `<project_dir>/.longrun/`）：

```text
.longrun/sessions/session-XXXX/
├── prompt.md
├── agent.command.txt
├── agent.stdout.log
├── agent.stderr.log
├── session.json
├── bearings.log          # coding phase
├── pre-coding.log        # 配置了 pre_coding_commands 时
├── verification.log      # 配置了 verification_commands 时
└── git-status.log        # 开启 require_clean_git 时
```

门禁失败时还会写：

```text
.longrun/remediation/session-XXXX.json
```

包含：失败 gate、证据、已执行的修正动作。

## 硬门禁与修正行为（简版）

已实现的关键门禁：
- initializer 必需产物检查（`feature_list.json` / `init.sh` / `claude-progress.txt`）
- coding 阶段 `feature_list.json` 不变量检查
  - 禁止改 `category/description/steps/顺序/数量`
  - 仅允许改 `passes`
  - 单轮 `passes` 增量受 `max_features_per_session` 限制
- verification 命令失败门禁
- 可选门禁：commit、progress 更新、git clean

失败后的修正：
- 可执行回滚（例如恢复 `feature_list.json`）
- 可写 remediation 报告
- 可选触发一次 repair 会话（按配置）

## 常见问题

### 1) `run-session` 直接失败：`claude-code-sdk is required`

你使用了 `claude_sdk` 后端，但没有安装 article 依赖：

```bash
python -m pip install -e .[article]
```

### 2) `ANTHROPIC_API_KEY environment variable not set`

设置 API key：

```bash
export ANTHROPIC_API_KEY='your-api-key'
```

### 3) `commit_required` 打开后总是失败

这是预期行为：每轮必须有新 commit。  
若你当前只是调试流程，先关闭：

```toml
[gates]
commit_required = false
```

### 4) 初始化总被 `required_artifacts_initializer` 拦截

说明 initializer 没产出必需文件，查看：
- `session-XXXX/agent.stdout.log`
- `session-XXXX/agent.stderr.log`
- `.longrun/remediation/session-XXXX.json`

### 5) `run-session` 报 `Agent process exited with non-zero status`

说明后端命令执行失败（常见是 `codex` 不在 PATH、模型参数错误、CLI 登录状态失效）。  
优先看当前会话日志：
- `.longrun/sessions/session-XXXX/agent.command.txt`
- `.longrun/sessions/session-XXXX/agent.stdout.log`
- `.longrun/sessions/session-XXXX/agent.stderr.log`

## 开发与测试

运行测试：

```bash
PYTHONPATH=src pytest -q
```
