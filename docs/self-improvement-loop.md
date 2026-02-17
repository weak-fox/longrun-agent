# longrun-agent 自我改进执行方案

## 目标

让 harness 能基于最近会话的真实失败信号，自动做“小步、可回滚”的配置优化，并持续输出下一轮改进计划。

## 已落地能力（v1）

- 新命令：`longrun-agent self-improve`
- 读取最近 N 次会话（`--window`）与 remediation 报告
- 识别 gate 失败模式（例如 `verification_commands_pass`）
- 生成改进计划文件：`.longrun/artifacts/self-improvement-plan.md`
- 安全自动调参（默认开启，可 `--no-apply` 关闭）
  - 当前仅自动开启：`gates.repair_on_verification_failure = true`
  - 触发条件：窗口内出现 `verification_commands_pass` 失败且当前未开启

## 建议日常节奏

1. 运行一批会话
   - `longrun-agent run-loop --max-sessions 10 --continue-on-failure`
2. 运行自我改进
   - `longrun-agent self-improve --window 20`
3. 查看计划文件并执行非自动项
   - `.longrun/artifacts/self-improvement-plan.md`
4. 再跑下一批会话验证效果

## 守护原则

- 只做“可解释 + 可回滚”的自动改动
- 自动改动只覆盖明确高收益且低风险配置
- 所有建议必须附带证据（会话计数、gate 计数）

## 下一步扩展（v2）

- 新增“实验回放”模式：对比改动前后窗口指标（失败率、无进展率）
- 对 `pre_coding_commands_pass` 高频失败做半自动治理建议模板
- 加入“目标达成效率”指标（每 10 会话通过 feature 增量）
- 支持将建议输出为结构化 JSON，供上层调度系统消费
