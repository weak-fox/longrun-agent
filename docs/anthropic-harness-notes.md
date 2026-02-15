# Notes from Anthropic "Effective Harnesses for Long-Running Agents"

## Core ideas applied in this repo

1. Use a dedicated initializer phase to create durable state (`feature_list.json`, setup script, project scaffold).
2. Treat `feature_list.json` as a strict source of truth across sessions.
3. Run coding in many short sessions with fresh context, never assuming memory carry-over.
4. Begin each coding session with orientation commands (pwd, file scan, logs, git history).
5. Complete one high-priority pending feature per session.
6. Verify before marking progress complete.
7. Persist everything needed for restart: prompt, output, progress notes, and git history.

## Article-exact runtime path

- `longrun-agent article-run` now follows the Anthropic quickstart structure directly:
  - Claude Agent SDK session loop
  - `initializer_prompt.md` / `coding_prompt.md`
  - Puppeteer MCP tool set
  - sandbox + permission policy + bash allowlist hook

## Additional guardrails implemented

- Forbidden mutation detection in `feature_list.json` (auto rollback).
- Optional verification command gate (tests/lint/build).
- Optional git-clean gate to avoid hidden drift.
- Session artifacts under `.longrun/sessions/` for debugging and auditability.
- Process lock under `.longrun/lock.json` to prevent concurrent writers.
- Stagnation breaker: stop loop after N consecutive coding sessions without progress.
- Read-only status snapshot command for operational visibility.
- Pre-coding regression checks: fail fast before new implementation when baseline is broken.
- Per-session scope guard: cap how many features can be marked passing in one coding session.
