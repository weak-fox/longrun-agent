#!/usr/bin/env bash
set -euo pipefail

# End-to-end CLI verification matrix for longrun-agent.
#
# What this script verifies:
# - command surface (--help for all subcommands)
# - bootstrap/configure/run-session/run-loop/status/go happy paths
# - critical gate/config behaviors (commit/progress/repair/clean-git/max-progress limits)
# - runtime override propagation (backend_model/model_reasoning_effort)
# - default external config path separation when local config is absent
#
# Notes:
# - This script intentionally uses isolated temp directories and a fake agent backend.
# - Interactive-only flows that require a TTY (e.g. bootstrap --guided, first-time go wizard)
#   are excluded from this automated script and should be validated manually.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LR_BIN="${LR_BIN:-$ROOT_DIR/.venv-longrun/bin/longrun-agent}"
PY_BIN="${PY_BIN:-$ROOT_DIR/.venv-longrun/bin/python}"
WORK_ROOT_RAW="$(mktemp -d "${TMPDIR:-/tmp}/lr-verify-matrix-XXXXXX")"
WORK_ROOT="$(cd "$WORK_ROOT_RAW" && pwd -P)"
KEEP_TMP="${KEEP_TMP:-0}"

cleanup() {
  if [[ "$KEEP_TMP" == "1" ]]; then
    echo "[INFO] KEEP_TMP=1, preserving: $WORK_ROOT"
    return
  fi
  rm -rf "$WORK_ROOT"
}
trap cleanup EXIT

log() {
  echo "[INFO] $*"
}

ok() {
  echo "[OK] $*"
}

fail() {
  echo "[FAIL] $*" >&2
  echo "[FAIL] work root: $WORK_ROOT" >&2
  exit 1
}

expect_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    echo "[FAIL] $label (missing: $needle)" >&2
    echo "----- output -----" >&2
    echo "$haystack" >&2
    echo "[FAIL] work root: $WORK_ROOT" >&2
    exit 1
  fi
}

expect_file_contains() {
  local file="$1"
  local needle="$2"
  local label="$3"
  [[ -f "$file" ]] || fail "$label (missing file: $file)"
  local content
  content="$(cat "$file")"
  expect_contains "$content" "$needle" "$label"
}

require_tools() {
  [[ -x "$LR_BIN" ]] || fail "longrun-agent binary not found/executable: $LR_BIN"
  [[ -x "$PY_BIN" ]] || fail "python binary not found/executable: $PY_BIN"
  command -v git >/dev/null 2>&1 || fail "git is required"
}

make_agent() {
  local project="$1"
  cat >"$project/fake_agent.py" <<'PYCODE'
import json
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])
phase = sys.argv[2]
mode = sys.argv[3] if len(sys.argv) > 3 else "pass_one"
backend_model = sys.argv[4] if len(sys.argv) > 4 else ""
reasoning = sys.argv[5] if len(sys.argv) > 5 else ""

with (project_dir / "agent_args.log").open("a") as handle:
    handle.write(f"phase={phase} mode={mode} model={backend_model} reasoning={reasoning}\n")

if phase == "initializer":
    feature_count_file = project_dir / "feature_count.txt"
    feature_count = int(feature_count_file.read_text().strip()) if feature_count_file.exists() else 3
    features = [
        {
            "category": "functional",
            "description": f"Feature {index + 1}",
            "steps": ["step 1"],
            "passes": False,
        }
        for index in range(feature_count)
    ]
    (project_dir / "feature_list.json").write_text(json.dumps(features, indent=2))
    if mode != "initializer_missing_artifacts":
        (project_dir / "init.sh").write_text("#!/usr/bin/env bash\necho init\n")
    if not (project_dir / "claude-progress.txt").exists():
        (project_dir / "claude-progress.txt").write_text("# Agent Session Progress\n\n")
    raise SystemExit(0)

if phase == "repair":
    if mode == "repair":
        (project_dir / "repaired.ok").write_text("ok\n")
    elif mode == "stuck":
        # no-change confirmation contract requires a strict one-line JSON decision.
        print('{"decision":"continue","reason":"feature still pending"}')
    raise SystemExit(0)

features_path = project_dir / "feature_list.json"
features = json.loads(features_path.read_text())

if mode in {"pass_one", "touch_progress", "dirty_git", "no_commit", "verify_fail", "args_capture", "precheck_fail"}:
    for item in features:
        if not item["passes"]:
            item["passes"] = True
            break
elif mode == "pass_two":
    changed = 0
    for item in features:
        if not item["passes"] and changed < 2:
            item["passes"] = True
            changed += 1
elif mode == "stuck":
    pass
elif mode == "mutate":
    if features:
        features[0]["description"] = "tampered"
elif mode == "repair":
    for item in features:
        if not item["passes"]:
            item["passes"] = True
            break

if mode == "touch_progress":
    progress = project_dir / "claude-progress.txt"
    before = progress.read_text() if progress.exists() else ""
    progress.write_text(before + "agent touched progress\n")

if mode == "dirty_git":
    (project_dir / "dirty-from-agent.txt").write_text("dirty\n")

features_path.write_text(json.dumps(features, indent=2))
raise SystemExit(0)
PYCODE
  chmod +x "$project/fake_agent.py"
}

write_config() {
  local cfg="$1"
  local project="$2"
  local state="$3"
  local mode="$4"
  local commit_req="$5"
  local progress_req="$6"
  local repair_on_fail="$7"
  local clean_git="$8"
  local feature_target="$9"
  local max_no_progress="${10}"
  local max_features="${11}"
  local pre_cmds="${12}"
  local verify_cmds="${13}"
  local bearings_cmds="${14}"
  local profile="${15}"
  local model="${16}"
  local reasoning="${17}"
  local timeout_secs="${18}"

  cat >"$cfg" <<EOF
[agent]
command = ["$PY_BIN", "$project/fake_agent.py", "{project_dir}", "{phase}", "$mode", "{backend_model}", "{model_reasoning_effort}"]
timeout_seconds = $timeout_secs

[runtime]
backend = "codex_cli"
profile = "$profile"
backend_model = "$model"
model_reasoning_effort = "$reasoning"

[backends.codex_cli]
command = ["$PY_BIN", "$project/fake_agent.py", "{project_dir}", "{phase}", "$mode", "{backend_model}", "{model_reasoning_effort}"]
model = "$model"
timeout_seconds = $timeout_secs

[backends.claude_sdk]
model = "claude-sonnet-4-5-20250929"

[gates]
commit_required = $commit_req
progress_update_required = $progress_req
repair_on_verification_failure = $repair_on_fail

[harness]
project_dir = "$project"
state_dir = "$state"
auto_continue_delay_seconds = 0
feature_target = $feature_target
max_no_progress_sessions = $max_no_progress
max_features_per_session = $max_features
require_clean_git = $clean_git
bearings_commands = $bearings_cmds
pre_coding_commands = $pre_cmds
verification_commands = $verify_cmds
EOF
}

verify_help_matrix() {
  log "verify command help matrix"
  for sub in "" "bootstrap" "configure" "run-session" "run-loop" "status" "go"; do
    if [[ -z "$sub" ]]; then
      "$LR_BIN" --help >/dev/null
    else
      "$LR_BIN" "$sub" --help >/dev/null
    fi
  done
  ok "all help commands"
}

verify_configure_non_interactive() {
  local project="$WORK_ROOT/project-configure"
  local cfg="$WORK_ROOT/configure.toml"
  mkdir -p "$project"
  make_agent "$project"

  "$LR_BIN" --config "$cfg" bootstrap --project-dir "$project" >/dev/null
  local output
  output=$("$LR_BIN" --config "$cfg" configure --non-interactive \
    --backend codex_cli \
    --profile article \
    --backend-model gpt-5.3-codex \
    --model-reasoning-effort medium \
    --project-dir "$project" \
    --state-dir "$WORK_ROOT/state-configure" \
    --codex-timeout-seconds 123 \
    --codex-command "$PY_BIN $project/fake_agent.py {project_dir} {phase} pass_one {backend_model} {model_reasoning_effort}" \
    --commit-required \
    --progress-update-required \
    --repair-on-verification-failure)

  expect_contains "$output" "Updated config:" "configure output"
  expect_contains "$output" "backend=codex_cli profile=article model=gpt-5.3-codex" "configure backend/profile/model"
  expect_contains "$output" "model_reasoning_effort=medium" "configure reasoning"
  expect_contains "$output" "state_dir=$WORK_ROOT/state-configure" "configure state_dir"

  local parsed
  parsed=$("$PY_BIN" - <<PYCODE
import tomllib
from pathlib import Path
cfg = Path("$cfg")
data = tomllib.loads(cfg.read_text())
print(data["runtime"]["backend"], data["runtime"]["profile"], data["runtime"]["backend_model"], data["runtime"]["model_reasoning_effort"])
print(data["gates"]["commit_required"], data["gates"]["progress_update_required"], data["gates"]["repair_on_verification_failure"])
print(data["harness"]["state_dir"], data["backends"]["codex_cli"]["timeout_seconds"])
PYCODE
)
  expect_contains "$parsed" "codex_cli article gpt-5.3-codex medium" "configure persisted runtime"
  expect_contains "$parsed" "True True True" "configure persisted gates"
  expect_contains "$parsed" "$WORK_ROOT/state-configure 123" "configure persisted state/timeout"
  ok "configure flags persisted"
}

verify_run_session_status_and_state_dir() {
  local project="$WORK_ROOT/project-run-success"
  local cfg="$WORK_ROOT/run-success.toml"
  mkdir -p "$project"
  make_agent "$project"
  printf 'Build app\n' >"$project/app_spec.txt"
  printf '3\n' >"$project/feature_count.txt"

  write_config "$cfg" "$project" "$WORK_ROOT/state-run-success" "pass_one" false false false false 3 5 1 "[]" "[]" '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120

  local first second status_text status_json
  first=$("$LR_BIN" --config "$cfg" run-session)
  second=$("$LR_BIN" --config "$cfg" run-session)
  expect_contains "$first" "phase=initializer success=True" "run-session initializer"
  expect_contains "$second" "phase=coding success=True" "run-session coding"

  [[ -d "$WORK_ROOT/state-run-success/sessions/session-0001" ]] || fail "state_dir session missing"
  [[ ! -d "$project/.longrun" ]] || fail "project .longrun should not exist when state_dir is configured"
  expect_file_contains "$WORK_ROOT/state-run-success/sessions/session-0001/prompt.md" "Layered repository reading (MANDATORY)" "initializer layered reading guidance"
  expect_file_contains "$WORK_ROOT/state-run-success/sessions/session-0001/prompt.md" 'Read `AGENTS.md` first' "initializer codex instruction file guidance"
  expect_file_contains "$WORK_ROOT/state-run-success/sessions/session-0002/prompt.md" "Do not read the entire repository by default." "coding layered reading guidance"

  status_text=$("$LR_BIN" --config "$cfg" status)
  expect_contains "$status_text" "project-run-success" "status text project"
  expect_contains "$status_text" "sessions: 2" "status text sessions"
  status_json=$("$LR_BIN" --config "$cfg" status --json)
  expect_contains "$status_json" '"session_count": 2' "status json session_count"
  ok "run-session + status + state_dir"
}

verify_gates_and_limits() {
  # progress_update_required
  local p3="$WORK_ROOT/project-progress-gate"
  local c3="$WORK_ROOT/progress-gate.toml"
  mkdir -p "$p3"
  make_agent "$p3"
  printf 'Build app\n' >"$p3/app_spec.txt"
  cat >"$p3/feature_list.json" <<'EOF'
[
  {"category":"functional","description":"A","steps":["s1"],"passes":false}
]
EOF
  write_config "$c3" "$p3" "$WORK_ROOT/state-progress-gate" "pass_one" false true false false 1 5 1 "[]" "[]" '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  set +e
  local out3
  out3=$("$LR_BIN" --config "$c3" run-session 2>&1)
  local rc3=$?
  set -e
  [[ $rc3 -eq 1 ]] || fail "progress gate rc expected 1, got $rc3"
  expect_contains "$out3" "progress_update_required" "progress gate message"
  ok "progress_update_required gate"

  # commit_required
  local p4="$WORK_ROOT/project-commit-gate"
  local c4="$WORK_ROOT/commit-gate.toml"
  mkdir -p "$p4"
  make_agent "$p4"
  printf 'Build app\n' >"$p4/app_spec.txt"
  cat >"$p4/feature_list.json" <<'EOF'
[
  {"category":"functional","description":"A","steps":["s1"],"passes":false}
]
EOF
  (
    cd "$p4"
    git init -q
    git config user.email test@example.com
    git config user.name test
    git add app_spec.txt feature_list.json
    git commit -qm "init"
  )
  write_config "$c4" "$p4" "$WORK_ROOT/state-commit-gate" "no_commit" true false false false 1 5 1 "[]" "[]" '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  set +e
  local out4
  out4=$("$LR_BIN" --config "$c4" run-session 2>&1)
  local rc4=$?
  set -e
  [[ $rc4 -eq 1 ]] || fail "commit gate rc expected 1, got $rc4"
  expect_contains "$out4" "commit_required" "commit gate message"
  ok "commit_required gate"

  # repair_on_verification_failure
  local p5="$WORK_ROOT/project-repair"
  local c5="$WORK_ROOT/repair.toml"
  mkdir -p "$p5"
  make_agent "$p5"
  printf 'Build app\n' >"$p5/app_spec.txt"
  cat >"$p5/feature_list.json" <<'EOF'
[
  {"category":"functional","description":"A","steps":["s1"],"passes":false}
]
EOF
  write_config "$c5" "$p5" "$WORK_ROOT/state-repair" "repair" false false true false 1 5 1 "[]" '["test -f repaired.ok"]' '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  local out5
  out5=$("$LR_BIN" --config "$c5" run-session)
  expect_contains "$out5" "success=True" "repair success"
  [[ -f "$p5/repaired.ok" ]] || fail "repaired.ok not created"
  ok "repair_on_verification_failure"

  # require_clean_git
  local p6="$WORK_ROOT/project-clean-git"
  local c6="$WORK_ROOT/clean-git.toml"
  mkdir -p "$p6"
  make_agent "$p6"
  printf 'Build app\n' >"$p6/app_spec.txt"
  cat >"$p6/feature_list.json" <<'EOF'
[
  {"category":"functional","description":"A","steps":["s1"],"passes":false}
]
EOF
  (
    cd "$p6"
    git init -q
    git config user.email test@example.com
    git config user.name test
    git add app_spec.txt feature_list.json
    git commit -qm "init"
  )
  write_config "$c6" "$p6" "$WORK_ROOT/state-clean-git" "dirty_git" false false false true 1 5 1 "[]" "[]" '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  set +e
  local out6
  out6=$("$LR_BIN" --config "$c6" run-session 2>&1)
  local rc6=$?
  set -e
  [[ $rc6 -eq 1 ]] || fail "clean-git gate rc expected 1, got $rc6"
  expect_contains "$out6" "git_clean_required" "clean-git gate message"
  ok "require_clean_git gate"

  # max_features_per_session
  local p7="$WORK_ROOT/project-max-features"
  local c7="$WORK_ROOT/max-features.toml"
  mkdir -p "$p7"
  make_agent "$p7"
  printf 'Build app\n' >"$p7/app_spec.txt"
  cat >"$p7/feature_list.json" <<'EOF'
[
  {"category":"functional","description":"A","steps":["s1"],"passes":false},
  {"category":"functional","description":"B","steps":["s1"],"passes":false},
  {"category":"functional","description":"C","steps":["s1"],"passes":false}
]
EOF
  write_config "$c7" "$p7" "$WORK_ROOT/state-max-features" "pass_two" false false false false 1 5 1 "[]" "[]" '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  set +e
  local out7
  out7=$("$LR_BIN" --config "$c7" run-session 2>&1)
  local rc7=$?
  set -e
  [[ $rc7 -eq 1 ]] || fail "max-features gate rc expected 1, got $rc7"
  expect_contains "$out7" "max_features_per_session" "max-features gate message"
  ok "max_features_per_session gate"

  # max_no_progress_sessions in run-loop
  local p8="$WORK_ROOT/project-no-progress-loop"
  local c8="$WORK_ROOT/no-progress-loop.toml"
  mkdir -p "$p8"
  make_agent "$p8"
  printf 'Build app\n' >"$p8/app_spec.txt"
  printf '2\n' >"$p8/feature_count.txt"
  write_config "$c8" "$p8" "$WORK_ROOT/state-no-progress-loop" "stuck" false false false false 2 2 1 "[]" "[]" '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  set +e
  local out8
  out8=$("$LR_BIN" --config "$c8" run-loop --max-sessions 8 2>&1)
  local rc8=$?
  set -e
  [[ $rc8 -eq 1 ]] || fail "no-progress loop rc expected 1, got $rc8"
  expect_contains "$out8" "No progress detected for 2 consecutive coding sessions" "no-progress loop stop reason"
  ok "max_no_progress_sessions loop stop"

  # pre_coding_commands
  local p9="$WORK_ROOT/project-pre-coding"
  local c9="$WORK_ROOT/pre-coding.toml"
  mkdir -p "$p9"
  make_agent "$p9"
  printf 'Build app\n' >"$p9/app_spec.txt"
  cat >"$p9/feature_list.json" <<'EOF'
[
  {"category":"functional","description":"A","steps":["s1"],"passes":false}
]
EOF
  write_config "$c9" "$p9" "$WORK_ROOT/state-pre-coding" "precheck_fail" false false false false 1 5 1 '["exit 1"]' "[]" '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  set +e
  local out9
  out9=$("$LR_BIN" --config "$c9" run-session 2>&1)
  local rc9=$?
  set -e
  [[ $rc9 -eq 1 ]] || fail "pre-coding gate rc expected 1, got $rc9"
  expect_contains "$out9" "pre-coding check failed" "pre-coding gate message"
  ok "pre_coding_commands gate"

  # verification_commands (without repair)
  local p10="$WORK_ROOT/project-verification"
  local c10="$WORK_ROOT/verification.toml"
  mkdir -p "$p10"
  make_agent "$p10"
  printf 'Build app\n' >"$p10/app_spec.txt"
  cat >"$p10/feature_list.json" <<'EOF'
[
  {"category":"functional","description":"A","steps":["s1"],"passes":false}
]
EOF
  write_config "$c10" "$p10" "$WORK_ROOT/state-verification" "verify_fail" false false false false 1 5 1 "[]" '["exit 1"]' '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  set +e
  local out10
  out10=$("$LR_BIN" --config "$c10" run-session 2>&1)
  local rc10=$?
  set -e
  [[ $rc10 -eq 1 ]] || fail "verification gate rc expected 1, got $rc10"
  expect_contains "$out10" "verification_commands_pass" "verification gate message"
  ok "verification_commands gate"
}

verify_runtime_overrides_and_go() {
  # run-session model/reasoning overrides
  local p11="$WORK_ROOT/project-args-capture"
  local c11="$WORK_ROOT/args-capture.toml"
  mkdir -p "$p11"
  make_agent "$p11"
  printf 'Build app\n' >"$p11/app_spec.txt"
  cat >"$p11/feature_list.json" <<'EOF'
[
  {"category":"functional","description":"A","steps":["s1"],"passes":false}
]
EOF
  write_config "$c11" "$p11" "$WORK_ROOT/state-args-capture" "args_capture" false false false false 1 5 1 "[]" "[]" '["echo bearings-ok"]' "default" "old-model" "" 120
  local out11
  out11=$("$LR_BIN" --config "$c11" run-session --backend-model new-model --model-reasoning-effort xhigh)
  expect_contains "$out11" "success=True" "run-session override success"
  local last_args
  last_args="$(tail -n 1 "$p11/agent_args.log")"
  expect_contains "$last_args" "model=new-model" "backend-model override propagation"
  expect_contains "$last_args" "reasoning=xhigh" "reasoning override propagation"
  ok "run-session overrides applied"

  # go command (non-interactive)
  local p12="$WORK_ROOT/project-go"
  local c12="$WORK_ROOT/go.toml"
  mkdir -p "$p12"
  make_agent "$p12"
  write_config "$c12" "$p12" "$WORK_ROOT/state-go" "pass_one" false false false false 3 5 1 "[]" "[]" '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  local out12
  out12=$("$LR_BIN" --config "$c12" go \
    --goal "做一个给小团队 用的任务看板" \
    --non-interactive --skip-brainstorm --yes --allow-any-python \
    --max-sessions 2 --feature-target 3)
  expect_contains "$out12" "Updated app spec:" "go updates app_spec"
  expect_contains "$out12" "session=0001 phase=coding success=True" "go first session"
  expect_contains "$out12" "session=0002 phase=coding success=True" "go second session"
  expect_contains "$(cat "$p12/app_spec.txt")" "## Product Goal" "go app_spec generated"
  expect_file_contains "$p12/.longrun/guided-goal/goal-draft.prompt.md" "Layered repository reading (MANDATORY)" "go draft layered guidance"
  expect_file_contains "$p12/.longrun/guided-goal/goal-draft.prompt.md" 'Read `AGENTS.md` first' "go draft codex instruction file guidance"
  ok "go command behavior"
}

verify_layered_reading_output_for_claude_profile() {
  log "verify layered reading prompt output for claude backend guidance"
  local output
  output=$("$PY_BIN" - <<'PYCODE'
from longrun_agent.cli import _build_goal_expansion_prompt, _build_goal_question_prompt
from longrun_agent.runtime.prompt_provider import PromptProvider

goal_prompt = _build_goal_expansion_prompt("Build app", backend_name="claude_sdk")
question_prompt = _build_goal_question_prompt("Build app", history=[], backend_name="claude_sdk")
provider = PromptProvider(profile="default", backend_name="claude_sdk")
coding_prompt = provider.build_coding_prompt(
    app_spec="Build app",
    feature_index=0,
    feature={"category": "functional", "description": "Do thing", "steps": ["a"], "passes": False},
    passing=0,
    total=1,
)

print("===goal===")
print(goal_prompt)
print("===question===")
print(question_prompt)
print("===coding===")
print(coding_prompt)
PYCODE
)
  expect_contains "$output" 'Read `claude.md` first' "claude guidance file selected"
  expect_contains "$output" 'treat `AGENTS.md` as inactive guidance' "claude inactive alternate file"
  expect_contains "$output" "Layered repository reading (MANDATORY)" "claude layered reading section"
  expect_contains "$output" "Do not read the entire repository by default." "claude non-full-read rule"
  ok "claude layered reading prompt output"
}

verify_run_loop_and_external_config_path() {
  # run-loop clean success
  local p13="$WORK_ROOT/project-loop-success"
  local c13="$WORK_ROOT/loop-success.toml"
  mkdir -p "$p13"
  make_agent "$p13"
  printf 'Build app\n' >"$p13/app_spec.txt"
  cat >"$p13/feature_list.json" <<'EOF'
[
  {"category":"functional","description":"A","steps":["s1"],"passes":false}
]
EOF
  write_config "$c13" "$p13" "$WORK_ROOT/state-loop-success" "pass_one" false false false false 1 5 1 "[]" "[]" '["echo bearings-ok"]' "default" "gpt-5.2-codex" "" 120
  local out13
  out13=$("$LR_BIN" --config "$c13" run-loop --max-sessions 5)
  expect_contains "$out13" "All features passing. Loop stopped cleanly." "run-loop clean stop"
  ok "run-loop success path"

  # default external config path when local config is absent
  local p14="$WORK_ROOT/project-config-separation"
  local fake_home="$WORK_ROOT/fake-home"
  mkdir -p "$p14" "$fake_home"
  local out14
  out14=$(
    cd "$p14"
    HOME="$fake_home" "$LR_BIN" configure --non-interactive --project-dir "$p14"
  )
  local cfg_path
  cfg_path="$(echo "$out14" | awk '/Updated config:/ {print $3}')"
  [[ -n "$cfg_path" ]] || fail "cannot parse config path from configure output"
  expect_contains "$cfg_path" "$fake_home/.longrun-agent/configs/" "external default config path"
  [[ ! -f "$p14/longrun-agent.toml" ]] || fail "local config should not be created"
  ok "external default config path separation"
}

main() {
  require_tools
  log "work root: $WORK_ROOT"
  verify_help_matrix
  verify_configure_non_interactive
  verify_run_session_status_and_state_dir
  verify_gates_and_limits
  verify_runtime_overrides_and_go
  verify_layered_reading_output_for_claude_profile
  verify_run_loop_and_external_config_path
  ok "CLI/config verification matrix passed"
}

main "$@"
