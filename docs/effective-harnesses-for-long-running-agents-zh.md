# Effective Harnesses for Long-Running Agents（详细执行版）

来源：Anthropic Engineering  
原文：<https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>  
发布时间：2025-11-26

## 1. 问题定义与目标

1. 长任务会跨多个 context window，新的会话默认“不记得上一轮发生了什么”。
2. 仅靠 context compaction 不够，常见问题是“做太多导致半成品”和“过早宣布完成”。
3. 目标不是让单轮更聪明，而是让多轮协作可持续、可恢复、可审计。
4. 成功标准是每轮都能稳定推进一个可验证的增量，并把环境交接干净。

## 2. Harness 总体结构

1. 采用两类代理，工具与系统提示基本相同，只是用户提示不同。
2. `initializer agent` 只跑首轮，负责搭环境和交接机制。
3. `coding agent` 跑后续所有轮次，负责增量开发和严格交接。
4. 通过三类持久化工件做“记忆外置”：
5. `feature_list.json`：完整功能清单与通过状态。
6. `claude-progress.txt`：每轮工作日志与下一步建议。
7. `git history`：可回滚、可审计、可追溯的代码状态。

## 3. 初始化阶段（Initializer）详细步骤

1. 读取用户规格，展开为完整端到端功能清单，不遗漏关键路径和异常路径。
2. 生成 `feature_list.json`，每条 feature 初始都设为 `passes: false`。
3. 每条 feature 包含：
4. `category`：功能类别。
5. `description`：用户可感知的功能描述。
6. `steps`：可执行、可复现的测试步骤。
7. `passes`：当前是否通过验证。
8. 生成 `init.sh`，要求包含：
9. 启动开发依赖和服务。
10. 可重复执行，不依赖人工临时命令。
11. 最好包含基础 smoke 流程或其入口说明。
12. 生成 `claude-progress.txt`，初始写入：
13. 已初始化内容。
14. 当前环境状态。
15. 后续 coding agent 的首轮建议。
16. 进行首个 git commit，明确“初始化基线”。
17. 初始化结束时保证仓库处于可启动、可测试、可接手状态。

## 4. 每轮开发阶段（Coding）标准作业流程

1. 会话开头先定位工作目录：运行 `pwd`。
2. 读取最近状态：`claude-progress.txt` + 最近 git log。
3. 读取 `feature_list.json` 并选择最高优先级且 `passes=false` 的单一 feature。
4. 运行 `init.sh` 启动环境并执行基础 E2E smoke，先确认旧功能没坏。
5. 若基线已坏，先修复基线，不直接叠加新功能。
6. 进入开发，只实现本轮目标 feature，避免多 feature 并行改动。
7. 开发后执行端到端验证，优先用浏览器自动化按真实用户路径测试。
8. 验证通过后，才允许把对应 feature 的 `passes` 改为 `true`。
9. 写入 `claude-progress.txt`，记录本轮：
10. 做了什么。
11. 如何验证。
12. 仍存在哪些风险或待办。
13. 提交 git commit，commit message 需描述清楚变更与验证范围。
14. 结束前确认环境“干净可接手”：代码可运行、状态可读、日志完整。

## 5. 状态文件治理规则（核心约束）

1. `feature_list.json` 只允许改 `passes` 字段，不允许改需求语义和测试步骤。
2. 禁止通过“删测试、改测试、弱化标准”来换取 feature 通过。
3. 采用 JSON 而非 Markdown 管理特性清单，降低被误改概率。
4. 任何 `passes=true` 必须对应实际验证证据，不接受“代码看起来没问题”。
5. `claude-progress.txt` 必须可被下一轮直接消费，避免含糊描述。

## 6. 测试策略（原文强调点）

1. 单元测试和 `curl` 检查不等于端到端可用。
2. Web 场景应显式要求浏览器自动化测试，按人类操作路径验证。
3. 先做“会话基线测试”，再做“新功能测试”。
4. 无明确 E2E 通过证据时，不得把 feature 标为通过。
5. 识别工具盲区：例如 Puppeteer MCP 对浏览器原生 alert 可见性有限。

## 7. 典型失败模式与修复策略

1. 失败模式：一次想做太多，context 用尽后留下半成品。  
2. 修复策略：每轮只做一个 feature，并强制写交接日志与提交。

3. 失败模式：看到已有进展就提前宣布全部完成。  
4. 修复策略：始终以 `feature_list.json` 为准，未全绿不得收工。

5. 失败模式：上一轮留下隐性 bug，新一轮继续叠加开发导致问题放大。  
6. 修复策略：每轮先跑基线 smoke，发现问题先回归修复。

7. 失败模式：feature 未做端到端验证就被标记通过。  
8. 修复策略：把“E2E 验证通过”设为更新 `passes` 的硬前置条件。

9. 失败模式：新会话不知道如何启动项目，浪费大量 token 和时间。  
10. 修复策略：初始化阶段产出可重复执行的 `init.sh` 并在每轮优先使用。

## 8. 每轮会话参考脚本（可落地）

1. `pwd`
2. `cat claude-progress.txt`
3. `cat feature_list.json`
4. `git log --oneline -20`
5. `bash ./init.sh`
6. 运行基础 E2E smoke（确保核心路径可用）
7. 选择一个 `passes=false` 的高优先级 feature 开发
8. 运行该 feature 的端到端验证
9. 通过后更新 `feature_list.json` 对应条目 `passes=true`
10. 更新 `claude-progress.txt`
11. `git add -A && git commit -m "<feature>: implement + e2e verified"`

## 9. 一页版执行清单（给团队直接使用）

1. 首轮必须产出：`feature_list.json`、`init.sh`、`claude-progress.txt`、初始化 commit。
2. 后续每轮固定节奏：定位环境 -> 读状态 -> 跑基线 -> 单 feature 开发 -> E2E 验证 -> 更新状态 -> 提交。
3. feature 完成判定：仅当真实端到端验证通过，且已写入日志与 commit。
4. 交接完成判定：下一轮 agent 可以不靠口头说明直接继续工作。
5. 项目完成判定：`feature_list.json` 全部 `passes=true`，且基线测试稳定通过。

## 10. 原文提出的后续方向

1. 单一 coding agent 不一定最优，多代理分工可能更好。
2. 可能进一步拆分 testing、QA、cleanup 等专职代理。
3. 当前实践主要面向全栈 Web 应用，未来可迁移到科研、金融建模等长任务场景。
