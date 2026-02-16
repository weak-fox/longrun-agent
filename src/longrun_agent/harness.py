"""Core harness orchestration for long-running agent sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Literal

from .backends.factory import create_backend
from .config import HarnessConfig
from .feature_list import (
    first_pending_feature,
    load_feature_list,
    progress_counts,
)
from .gates.checks import (
    check_feature_list_coding_invariants,
    check_required_artifacts_initializer,
)
from .gates.engine import GateResult
from .gates.remediation import RemediationEngine
from .runtime.contracts import AgentRunRequest
from .runtime.prompt_provider import PromptProvider


APP_SPEC_TEMPLATE = """# Application Spec

Describe the product you want the agent to build.
Include goals, core flows, constraints, and non-functional requirements.
"""


@dataclass(slots=True)
class SessionResult:
    session_id: int
    phase: str
    success: bool
    message: str
    passing: int = 0
    total: int = 0
    progress_made: bool | None = None
    return_code: int | None = None


@dataclass(slots=True)
class NoChangeDecision:
    action: Literal["continue", "mark_complete"]
    reason: str
    log_path: Path


class Harness:
    """Manages initializer/coding sessions with persisted state."""

    def __init__(self, config: HarnessConfig):
        self.config = config
        self.project_dir = config.project_dir.resolve()
        if config.state_dir is None:
            state_dir = self.project_dir / ".longrun"
        elif config.state_dir.is_absolute():
            state_dir = config.state_dir
        else:
            state_dir = self.project_dir / config.state_dir
        if config.artifacts_dir is None:
            artifacts_dir = self.project_dir
        elif config.artifacts_dir.is_absolute():
            artifacts_dir = config.artifacts_dir
        else:
            artifacts_dir = self.project_dir / config.artifacts_dir
        self.state_dir = state_dir.resolve()
        self.artifacts_dir = artifacts_dir.resolve()
        self.sessions_dir = self.state_dir / "sessions"
        self.lock_file = self.state_dir / "lock.json"
        self.feature_file = self.artifacts_dir / "feature_list.json"
        self.progress_file = self.artifacts_dir / "claude-progress.txt"
        self.spec_file = self.artifacts_dir / "app_spec.txt"
        self.init_file = self.artifacts_dir / "init.sh"
        self.last_loop_stop_reason: str | None = None
        self.prompt_provider = PromptProvider(
            profile=self.config.profile,
            backend_name=self.config.backend_name,
            app_spec_path=self._display_path(self.spec_file),
            feature_list_path=self._display_path(self.feature_file),
            progress_path=self._display_path(self.progress_file),
            init_script_path=self._display_path(self.init_file),
        )
        self.remediation_engine = RemediationEngine(
            state_dir=self.state_dir,
            feature_file=self.feature_file,
        )
        self.backend = create_backend(
            backend_name=self.config.backend_name,
            project_dir=self.project_dir,
            command_template=self.config.agent_command,
            model=self.config.backend_model,
        )

    def bootstrap(self) -> None:
        """Create baseline files and directories needed by the harness."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        if not self.spec_file.exists():
            self.spec_file.write_text(APP_SPEC_TEMPLATE)

        if not self.progress_file.exists():
            self.progress_file.write_text("# Agent Session Progress\n\n")

    def run_session(self) -> SessionResult:
        """Run a single initializer or coding session with process lock."""
        self.bootstrap()
        self.last_loop_stop_reason = None

        lock_error = self._acquire_lock()
        if lock_error is not None:
            return SessionResult(
                session_id=0,
                phase="lock",
                success=False,
                message=lock_error,
            )

        try:
            return self._run_session_unlocked()
        finally:
            self._release_lock()

    def _run_session_unlocked(self) -> SessionResult:
        """Run one session assuming lock ownership is already held."""
        self.bootstrap()

        phase = "initializer" if not self.feature_file.exists() else "coding"
        session_id = self._next_session_id()
        session_dir = self.sessions_dir / f"session-{session_id:04d}"
        session_dir.mkdir(parents=True, exist_ok=True)

        app_spec = self.spec_file.read_text().strip()
        prompt_file = session_dir / "prompt.md"
        git_head_before = self._current_git_head()
        git_status_before = self._git_status_porcelain()
        progress_before_text = self.progress_file.read_text() if self.progress_file.exists() else ""

        before_features: list[dict[str, Any]] | None = None
        pending_index: int | None = None
        pending_feature: dict[str, Any] | None = None

        if phase == "initializer":
            prompt = self.prompt_provider.build_initializer_prompt(
                app_spec=app_spec,
                feature_target=self.config.feature_target,
            )
        else:
            try:
                before_features = load_feature_list(self.feature_file)
            except Exception as exc:
                result = SessionResult(
                    session_id=session_id,
                    phase=phase,
                    success=False,
                    message=f"feature_list.json cannot be loaded: {exc}",
                )
                self._write_session_metadata(session_dir, result)
                self._append_progress(result)
                return result

            passing_before, total = progress_counts(before_features)
            try:
                pending_index, pending_feature = first_pending_feature(before_features)
            except ValueError:
                result = SessionResult(
                    session_id=session_id,
                    phase=phase,
                    success=True,
                    message="All features already passing",
                    passing=passing_before,
                    total=total,
                )
                self._write_session_metadata(session_dir, result)
                self._append_progress(result)
                return result

            prompt = self.prompt_provider.build_coding_prompt(
                app_spec=app_spec,
                feature_index=pending_index,
                feature=pending_feature,
                passing=passing_before,
                total=total,
            )
            self._run_bearings_commands(session_dir)
            pre_coding_error = self._run_pre_coding_commands(session_dir)
            if pre_coding_error is not None:
                result = SessionResult(
                    session_id=session_id,
                    phase=phase,
                    success=False,
                    message=pre_coding_error,
                    passing=passing_before,
                    total=total,
                )
                self._write_session_metadata(session_dir, result)
                self._append_progress(result)
                return result

            # Keep a recovery copy so we can roll back forbidden mutations.
            shutil.copy(self.feature_file, session_dir / "feature_list.before.json")

        prompt_file.write_text(prompt)

        try:
            run = self._run_agent_command(phase, prompt_file, session_dir)
        except Exception as exc:  # pragma: no cover - defensive guard
            result = SessionResult(
                session_id=session_id,
                phase=phase,
                success=False,
                message=f"Agent command failed to launch: {exc}",
            )
            self._write_session_metadata(session_dir, result)
            self._append_progress(result)
            return result

        readonly_retry_attempted = False
        readonly_retry_recovered = False
        if not run["timeout"] and run["return_code"] != 0:
            retried = self._retry_if_readonly_sandbox_failure(
                phase=phase,
                prompt_file=prompt_file,
                session_dir=session_dir,
                failed_run=run,
            )
            if retried is not None:
                readonly_retry_attempted = True
                run = retried
                readonly_retry_recovered = not run["timeout"] and run["return_code"] == 0

        if run["timeout"]:
            timeout_note = (
                " (workspace-write retry attempted)"
                if readonly_retry_attempted
                else ""
            )
            result = SessionResult(
                session_id=session_id,
                phase=phase,
                success=False,
                message=(
                    f"Session timed out after {self.config.agent_timeout_seconds}s{timeout_note}. "
                    f"Check logs: {self._display_path(run['stdout_path'])}, "
                    f"{self._display_path(run['stderr_path'])}"
                ),
                return_code=None,
            )
            self._write_session_metadata(session_dir, result)
            self._append_progress(result)
            return result

        if run["return_code"] != 0:
            stderr_tail = self._last_non_empty_log_line(run["stderr_path"])
            retry_note = " (workspace-write retry attempted)" if readonly_retry_attempted else ""
            hint = (
                " If this keeps failing, use a codex command with "
                "--dangerously-bypass-approvals-and-sandbox in an isolated environment."
                if readonly_retry_attempted
                else ""
            )
            result = SessionResult(
                session_id=session_id,
                phase=phase,
                success=False,
                message=(
                    "Agent process exited with non-zero status "
                    f"(code={run['return_code']}){retry_note}. "
                    f"Check logs: {self._display_path(run['stdout_path'])}, "
                    f"{self._display_path(run['stderr_path'])}"
                    + (f"; last stderr: {stderr_tail}" if stderr_tail else "")
                    + hint
                ),
                return_code=run["return_code"],
            )
            self._write_session_metadata(session_dir, result)
            self._append_progress(result)
            return result

        if phase == "initializer":
            gate = check_required_artifacts_initializer(
                project_dir=self.project_dir,
                feature_target=self.config.feature_target,
                artifacts_dir=self.artifacts_dir,
            )
        else:
            if before_features is None:
                gate = GateResult(
                    gate_id="feature_list_coding_context",
                    passed=False,
                    message="internal error: before_features missing for coding phase",
                    remediation=["write_report", "stop"],
                )
            else:
                gate = check_feature_list_coding_invariants(
                    feature_file=self.feature_file,
                    before_features=before_features,
                    max_features_per_session=self.config.max_features_per_session,
                )
        if not gate.passed:
            return self._fail_with_gate(
                session_id=session_id,
                phase=phase,
                gate=gate,
                session_dir=session_dir,
                before_features=before_features,
                return_code=run["return_code"],
            )

        verify_result = self._run_verification_commands(session_dir)
        if verify_result is not None:
            if self.config.repair_on_verification_failure:
                repair_error = self._run_repair_session(
                    session_dir=session_dir,
                    reason=verify_result,
                )
                if repair_error is None:
                    verify_result = self._run_verification_commands(session_dir)
                else:
                    verify_result = f"{verify_result}; repair failed: {repair_error}"

        if verify_result is not None:
            gate = GateResult(
                gate_id="verification_commands_pass",
                passed=False,
                message=verify_result,
                remediation=["write_report", "stop"],
            )
            return self._fail_with_gate(
                session_id=session_id,
                phase=phase,
                gate=gate,
                session_dir=session_dir,
                before_features=before_features,
                return_code=run["return_code"],
            )

        after_features = load_feature_list(self.feature_file)
        passing, total = progress_counts(after_features)
        progress_made = None
        previous_passing: int | None = None
        if phase == "coding" and before_features is not None:
            previous_passing, _ = progress_counts(before_features)
            progress_made = passing > previous_passing

        current_head = self._current_git_head()
        git_status_after = self._git_status_porcelain()
        current_progress_text = self.progress_file.read_text() if self.progress_file.exists() else ""
        no_change_decision: NoChangeDecision | None = None

        if (
            phase == "coding"
            and before_features is not None
            and pending_index is not None
            and previous_passing is not None
            and self._session_has_no_observable_changes(
                before_features=before_features,
                after_features=after_features,
                progress_before_text=progress_before_text,
                progress_after_text=current_progress_text,
                git_head_before=git_head_before,
                git_head_after=current_head,
                git_status_before=git_status_before,
                git_status_after=git_status_after,
            )
        ):
            no_change_decision, confirm_error = self._run_no_change_confirmation(
                session_dir=session_dir,
                app_spec=app_spec,
                feature_index=pending_index,
                feature=pending_feature,
                passing=passing,
                total=total,
            )
            if confirm_error is not None:
                gate = GateResult(
                    gate_id="no_change_confirmation",
                    passed=False,
                    message=confirm_error,
                    remediation=["write_report", "stop"],
                )
                return self._fail_with_gate(
                    session_id=session_id,
                    phase=phase,
                    gate=gate,
                    session_dir=session_dir,
                    before_features=before_features,
                    return_code=run["return_code"],
                    passing=passing,
                    total=total,
                    progress_made=progress_made,
                )

            if no_change_decision.action == "mark_complete":
                mark_error = self._mark_feature_complete(pending_index)
                if mark_error is not None:
                    gate = GateResult(
                        gate_id="no_change_confirmation",
                        passed=False,
                        message=mark_error,
                        remediation=["write_report", "stop"],
                    )
                    return self._fail_with_gate(
                        session_id=session_id,
                        phase=phase,
                        gate=gate,
                        session_dir=session_dir,
                        before_features=before_features,
                        return_code=run["return_code"],
                        passing=passing,
                        total=total,
                        progress_made=progress_made,
                    )
                after_features = load_feature_list(self.feature_file)
                passing, total = progress_counts(after_features)
                progress_made = passing > previous_passing
            else:
                progress_made = False

        clean_check = self._verify_git_clean_if_enabled(session_dir)
        if clean_check is not None:
            gate = GateResult(
                gate_id="git_clean_required",
                passed=False,
                message=clean_check,
                remediation=["write_report", "stop"],
            )
            return self._fail_with_gate(
                session_id=session_id,
                phase=phase,
                gate=gate,
                session_dir=session_dir,
                before_features=before_features,
                return_code=run["return_code"],
                passing=passing,
                total=total,
                progress_made=progress_made,
            )

        if self.config.commit_required and no_change_decision is None:
            if current_head is None:
                gate = GateResult(
                    gate_id="commit_required",
                    passed=False,
                    message="commit_required is enabled but repository has no valid HEAD",
                    remediation=["write_report", "stop"],
                )
                return self._fail_with_gate(
                    session_id=session_id,
                    phase=phase,
                    gate=gate,
                    session_dir=session_dir,
                    before_features=before_features,
                    return_code=run["return_code"],
                    passing=passing,
                    total=total,
                    progress_made=progress_made,
                )
            if git_head_before == current_head:
                gate = GateResult(
                    gate_id="commit_required",
                    passed=False,
                    message="no commit was created during this session",
                    remediation=["write_report", "stop"],
                )
                return self._fail_with_gate(
                    session_id=session_id,
                    phase=phase,
                    gate=gate,
                    session_dir=session_dir,
                    before_features=before_features,
                    return_code=run["return_code"],
                    passing=passing,
                    total=total,
                    progress_made=progress_made,
                )

        if self.config.progress_update_required and no_change_decision is None:
            if current_progress_text == progress_before_text:
                gate = GateResult(
                    gate_id="progress_update_required",
                    passed=False,
                    message="progress file was not updated during this session",
                    remediation=["write_report", "stop"],
                )
                return self._fail_with_gate(
                    session_id=session_id,
                    phase=phase,
                    gate=gate,
                    session_dir=session_dir,
                    before_features=before_features,
                    return_code=run["return_code"],
                    passing=passing,
                    total=total,
                    progress_made=progress_made,
                )

        completion_notes: list[str] = []
        if no_change_decision is not None:
            completion_notes.append(
                f"no-change confirmation: {no_change_decision.action}; reason: {no_change_decision.reason}"
            )
        if readonly_retry_recovered:
            completion_notes.append("recovered from read-only sandbox via workspace-write retry")

        message = "Session completed"
        if completion_notes:
            message = f"Session completed ({'; '.join(completion_notes)})"

        result = SessionResult(
            session_id=session_id,
            phase=phase,
            success=True,
            message=message,
            passing=passing,
            total=total,
            progress_made=progress_made,
            return_code=run["return_code"],
        )
        self._write_session_metadata(session_dir, result)
        self._append_progress(result)
        return result

    def run_loop(
        self,
        max_sessions: int | None = None,
        continue_on_failure: bool = False,
    ) -> list[SessionResult]:
        """Run repeated sessions until completion, failure, or loop policy stop."""
        self.bootstrap()
        self.last_loop_stop_reason = None

        lock_error = self._acquire_lock()
        if lock_error is not None:
            return [
                SessionResult(
                    session_id=0,
                    phase="lock",
                    success=False,
                    message=lock_error,
                )
            ]

        results: list[SessionResult] = []
        session_count = 0
        no_progress_streak = 0

        try:
            while True:
                if max_sessions is not None and session_count >= max_sessions:
                    break

                result = self._run_session_unlocked()
                results.append(result)
                session_count += 1

                if not result.success:
                    if continue_on_failure:
                        time.sleep(max(self.config.auto_continue_delay_seconds, 0))
                        continue
                    break

                if result.total > 0 and result.passing == result.total:
                    break

                if result.phase == "coding":
                    if result.progress_made is True:
                        no_progress_streak = 0
                    else:
                        no_progress_streak += 1

                    if (
                        self.config.max_no_progress_sessions > 0
                        and no_progress_streak >= self.config.max_no_progress_sessions
                    ):
                        self.last_loop_stop_reason = (
                            "No progress detected for "
                            f"{no_progress_streak} consecutive coding sessions"
                        )
                        break

                time.sleep(max(self.config.auto_continue_delay_seconds, 0))
        finally:
            self._release_lock()

        return results

    def status_summary(self) -> dict[str, Any]:
        """Return summary data suitable for dashboards/CLI status output."""
        passing = 0
        total = 0
        next_pending: dict[str, Any] | None = None

        if self.feature_file.exists():
            try:
                features = load_feature_list(self.feature_file)
                passing, total = progress_counts(features)
                try:
                    pending_index, pending_feature = first_pending_feature(features)
                    next_pending = {
                        "index": pending_index,
                        "category": pending_feature.get("category"),
                        "description": pending_feature.get("description"),
                    }
                except ValueError:
                    next_pending = None
            except Exception:
                # Keep status command resilient even if feature list is malformed.
                passing = 0
                total = 0

        session_dirs = (
            sorted(self.sessions_dir.glob("session-*")) if self.sessions_dir.exists() else []
        )
        last_session: dict[str, Any] | None = None
        if session_dirs:
            session_file = session_dirs[-1] / "session.json"
            if session_file.exists():
                try:
                    last_session = json.loads(session_file.read_text())
                except Exception:
                    last_session = None

        lock_info: dict[str, Any] | None = None
        if self.lock_file.exists():
            try:
                lock_info = json.loads(self.lock_file.read_text())
            except Exception:
                lock_info = {"raw": self.lock_file.read_text()}

        return {
            "project_dir": str(self.project_dir),
            "progress": {
                "passing": passing,
                "total": total,
                "percentage": ((passing / total) * 100.0) if total > 0 else 0.0,
            },
            "next_pending": next_pending,
            "session_count": len(session_dirs),
            "last_session": last_session,
            "lock": lock_info,
        }

    def _acquire_lock(self) -> str | None:
        """Acquire process lock, auto-recovering stale lock files."""
        self.state_dir.mkdir(parents=True, exist_ok=True)

        if self.lock_file.exists():
            existing = self._read_lock_info()
            existing_pid = existing.get("pid")
            if isinstance(existing_pid, int) and existing_pid != os.getpid():
                if self._pid_is_running(existing_pid):
                    return f"Another harness instance is running (pid={existing_pid})"
            try:
                self.lock_file.unlink()
            except FileNotFoundError:
                pass

        payload = {
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat(),
            "project_dir": str(self.project_dir),
        }

        try:
            fd = os.open(self.lock_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return "Another harness instance is running (lock already exists)"

        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload, indent=2))
            f.write("\n")

        return None

    def _release_lock(self) -> None:
        """Release process lock if owned by this process."""
        if not self.lock_file.exists():
            return

        info = self._read_lock_info()
        owner_pid = info.get("pid")
        if isinstance(owner_pid, int) and owner_pid != os.getpid():
            return

        try:
            self.lock_file.unlink()
        except FileNotFoundError:
            pass

    def _read_lock_info(self) -> dict[str, Any]:
        try:
            data = json.loads(self.lock_file.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _next_session_id(self) -> int:
        ids: list[int] = []
        for child in self.sessions_dir.glob("session-*"):
            try:
                ids.append(int(child.name.split("-")[1]))
            except (IndexError, ValueError):
                continue
        return max(ids, default=0) + 1

    def _run_bearings_commands(self, session_dir: Path) -> None:
        log_path = session_dir / "bearings.log"
        lines: list[str] = []
        env = os.environ.copy()
        env.update(
            {
                "LONGRUN_ARTIFACTS_DIR": self._display_path(self.artifacts_dir),
                "LONGRUN_APP_SPEC_PATH": self._display_path(self.spec_file),
                "LONGRUN_FEATURE_LIST_PATH": self._display_path(self.feature_file),
                "LONGRUN_PROGRESS_PATH": self._display_path(self.progress_file),
                "LONGRUN_INIT_SCRIPT_PATH": self._display_path(self.init_file),
            }
        )

        for command in self.config.bearings_commands:
            completed = subprocess.run(
                ["bash", "-lc", command],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                env=env,
            )
            lines.append(f"$ {command}\n")
            if completed.stdout:
                lines.append(completed.stdout)
            if completed.stderr:
                lines.append(completed.stderr)
            lines.append(f"[exit {completed.returncode}]\n\n")

        log_path.write_text("".join(lines))

    def _run_agent_command(
        self,
        phase: str,
        prompt_file: Path,
        session_dir: Path,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_result = self.backend.run(
            AgentRunRequest(
                phase=phase,
                project_dir=self.project_dir,
                prompt_file=prompt_file,
                session_dir=session_dir,
                timeout_seconds=self.config.agent_timeout_seconds,
                backend_model=self.config.backend_model,
                model_reasoning_effort=self.config.model_reasoning_effort,
                metadata=metadata or {},
            )
        )
        return {
            "timeout": run_result.timeout,
            "return_code": run_result.return_code,
            "stdout_path": run_result.stdout_path,
            "stderr_path": run_result.stderr_path,
        }

    def _retry_if_readonly_sandbox_failure(
        self,
        phase: str,
        prompt_file: Path,
        session_dir: Path,
        failed_run: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self.config.backend_name != "codex_cli":
            return None
        if phase not in {"initializer", "coding"}:
            return None
        if not self._is_readonly_sandbox_error(
            stderr_path=failed_run["stderr_path"],
            stdout_path=failed_run["stdout_path"],
        ):
            return None

        retry_dir = session_dir / "retry-workspace-write"
        retry_dir.mkdir(parents=True, exist_ok=True)
        return self._run_agent_command(
            phase=phase,
            prompt_file=prompt_file,
            session_dir=retry_dir,
            metadata={"force_workspace_write": True, "retry_reason": "readonly_sandbox"},
        )

    @staticmethod
    def _is_readonly_sandbox_error(stderr_path: Path, stdout_path: Path) -> bool:
        chunks: list[str] = []
        for path in (stderr_path, stdout_path):
            try:
                chunks.append(path.read_text())
            except Exception:
                continue
        text = "\n".join(chunks).lower()
        markers = (
            "read-only",
            "read only",
            "sandbox: read-only",
            "workspace is read-only",
            "readonly sandbox",
            "只读",
        )
        return any(marker in text for marker in markers)

    def _session_has_no_observable_changes(
        self,
        before_features: list[dict[str, Any]],
        after_features: list[dict[str, Any]],
        progress_before_text: str,
        progress_after_text: str,
        git_head_before: str | None,
        git_head_after: str | None,
        git_status_before: str | None,
        git_status_after: str | None,
    ) -> bool:
        if git_status_before is not None and git_status_after is not None:
            return git_head_before == git_head_after and git_status_before == git_status_after

        return before_features == after_features and progress_before_text == progress_after_text

    def _run_no_change_confirmation(
        self,
        session_dir: Path,
        app_spec: str,
        feature_index: int,
        feature: dict[str, Any] | None,
        passing: int,
        total: int,
    ) -> tuple[NoChangeDecision | None, str | None]:
        confirm_dir = session_dir / "no-change-confirmation"
        confirm_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = confirm_dir / "prompt.md"
        prompt_file.write_text(
            self._build_no_change_confirmation_prompt(
                app_spec=app_spec,
                feature_index=feature_index,
                feature=feature or {},
                passing=passing,
                total=total,
            )
        )

        run = self._run_agent_command("repair", prompt_file, confirm_dir)
        if run["timeout"]:
            return (
                None,
                "no-change confirmation timed out "
                f"(logs: {self._display_path(run['stdout_path'])}, "
                f"{self._display_path(run['stderr_path'])})",
            )
        if run["return_code"] != 0:
            stderr_tail = self._last_non_empty_log_line(run["stderr_path"])
            suffix = f"; last stderr: {stderr_tail}" if stderr_tail else ""
            return (
                None,
                "no-change confirmation returned non-zero exit status "
                f"(logs: {self._display_path(run['stdout_path'])}, "
                f"{self._display_path(run['stderr_path'])}){suffix}",
            )

        decision, parse_error = self._parse_no_change_decision(
            stdout_path=run["stdout_path"],
            stderr_path=run["stderr_path"],
        )
        if parse_error is not None:
            return None, parse_error

        return decision, None

    def _build_no_change_confirmation_prompt(
        self,
        app_spec: str,
        feature_index: int,
        feature: dict[str, Any],
        passing: int,
        total: int,
    ) -> str:
        steps = feature.get("steps", [])
        rendered_steps = "\n".join(f"- {step}" for step in steps if isinstance(step, str))
        if not rendered_steps:
            rendered_steps = "- (no explicit steps provided)"

        return (
            "## ROLE: NO-CHANGE CONFIRMATION\n\n"
            "The previous coding session made no repository changes. "
            "You must decide whether to continue coding or mark the current feature complete.\n\n"
            f"- Current progress: {passing}/{total}\n"
            f"- Target feature index: {feature_index}\n"
            f"- Category: {feature.get('category', 'functional')}\n"
            f"- Description: {feature.get('description', '')}\n"
            "- Steps:\n"
            f"{rendered_steps}\n\n"
            "### Decision rules\n"
            '- Return `"continue"` if more implementation or verification is still needed.\n'
            '- Return `"mark_complete"` only if the target feature is already satisfied.\n\n'
            "### Output contract (strict)\n"
            "Print exactly one JSON object on a single line and nothing else:\n"
            '{"decision":"continue|mark_complete","reason":"short explanation"}\n\n'
            "### App spec reminder\n"
            f"{app_spec}\n"
        )

    def _parse_no_change_decision(
        self,
        stdout_path: Path,
        stderr_path: Path,
    ) -> tuple[NoChangeDecision | None, str | None]:
        try:
            lines = stdout_path.read_text().splitlines()
        except Exception:
            lines = []

        for line in reversed(lines):
            text = line.strip()
            if not (text.startswith("{") and text.endswith("}")):
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue

            decision_raw = str(payload.get("decision", "")).strip().lower()
            reason = str(payload.get("reason", "")).strip()
            if decision_raw not in {"continue", "mark_complete"}:
                continue

            action: Literal["continue", "mark_complete"]
            if decision_raw == "continue":
                action = "continue"
            else:
                action = "mark_complete"

            return (
                NoChangeDecision(
                    action=action,
                    reason=reason or "no reason provided",
                    log_path=stdout_path,
                ),
                None,
            )

        stderr_tail = self._last_non_empty_log_line(stderr_path)
        suffix = f"; last stderr: {stderr_tail}" if stderr_tail else ""
        return (
            None,
            "no-change confirmation did not return a valid JSON decision "
            f"(logs: {self._display_path(stdout_path)}, {self._display_path(stderr_path)}){suffix}",
        )

    def _mark_feature_complete(self, feature_index: int) -> str | None:
        try:
            features = load_feature_list(self.feature_file)
        except Exception as exc:
            return f"cannot load feature_list.json for mark_complete: {exc}"

        if feature_index < 0 or feature_index >= len(features):
            return f"feature index out of range for mark_complete: {feature_index}"

        feature = features[feature_index]
        if bool(feature.get("passes", False)):
            return None

        feature["passes"] = True
        self.feature_file.write_text(json.dumps(features, ensure_ascii=False, indent=2) + "\n")
        return None

    def _git_status_porcelain(self) -> str | None:
        git_dir = self.project_dir / ".git"
        if not git_dir.exists():
            return None

        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.project_dir,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.project_dir))
        except ValueError:
            return str(path)

    @staticmethod
    def _last_non_empty_log_line(path: Path) -> str:
        try:
            lines = path.read_text().splitlines()
        except Exception:
            return ""
        for line in reversed(lines):
            text = line.strip()
            if text:
                if len(text) > 300:
                    return text[-300:]
                return text
        return ""

    def _run_repair_session(self, session_dir: Path, reason: str) -> str | None:
        """Run one bounded repair attempt after failed verification."""
        repair_prompt = session_dir / "repair.prompt.md"
        repair_prompt.write_text(
            "\n".join(
                [
                    "## ROLE: REPAIR AGENT",
                    "A hard verification gate failed. Fix only the regression and re-verify.",
                    f"Failure reason: {reason}",
                    "Do not edit feature descriptions/steps/order.",
                ]
            )
        )
        run = self._run_agent_command("repair", repair_prompt, session_dir)
        if run["timeout"]:
            return f"repair session timed out after {self.config.agent_timeout_seconds}s"
        if run["return_code"] != 0:
            return "repair session exited with non-zero status"
        return None

    def _fail_with_gate(
        self,
        session_id: int,
        phase: str,
        gate: GateResult,
        session_dir: Path,
        before_features: list[dict[str, Any]] | None,
        return_code: int | None,
        passing: int = 0,
        total: int = 0,
        progress_made: bool | None = None,
    ) -> SessionResult:
        self.remediation_engine.apply(
            session_id=session_id,
            phase=phase,
            gate=gate,
            before_features=before_features,
        )
        stdout_path = session_dir / "agent.stdout.log"
        stderr_path = session_dir / "agent.stderr.log"
        message = f"{gate.gate_id}: {gate.message}"
        if stdout_path.exists() or stderr_path.exists():
            message = (
                f"{message}. Check logs: {self._display_path(stdout_path)}, "
                f"{self._display_path(stderr_path)}"
            )
            stderr_tail = self._last_non_empty_log_line(stderr_path)
            stdout_tail = self._last_non_empty_log_line(stdout_path)
            if stderr_tail:
                message = f"{message}; last stderr: {stderr_tail}"
            elif stdout_tail:
                message = f"{message}; last stdout: {stdout_tail}"
        result = SessionResult(
            session_id=session_id,
            phase=phase,
            success=False,
            message=message,
            passing=passing,
            total=total,
            progress_made=progress_made,
            return_code=return_code,
        )
        self._write_session_metadata(session_dir, result)
        self._append_progress(result)
        return result

    def _current_git_head(self) -> str | None:
        git_dir = self.project_dir / ".git"
        if not git_dir.exists():
            return None
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project_dir,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    def _run_pre_coding_commands(self, session_dir: Path) -> str | None:
        if not self.config.pre_coding_commands:
            return None

        log_path = session_dir / "pre-coding.log"
        lines: list[str] = []

        for command in self.config.pre_coding_commands:
            completed = subprocess.run(
                ["bash", "-lc", command],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
            )
            lines.append(f"$ {command}\n")
            if completed.stdout:
                lines.append(completed.stdout)
            if completed.stderr:
                lines.append(completed.stderr)
            lines.append(f"[exit {completed.returncode}]\n\n")

            if completed.returncode != 0:
                log_path.write_text("".join(lines))
                return f"pre-coding check failed: {command}"

        log_path.write_text("".join(lines))
        return None

    def _run_verification_commands(self, session_dir: Path) -> str | None:
        if not self.config.verification_commands:
            return None

        log_path = session_dir / "verification.log"
        lines: list[str] = []

        for command in self.config.verification_commands:
            completed = subprocess.run(
                ["bash", "-lc", command],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
            )
            lines.append(f"$ {command}\n")
            if completed.stdout:
                lines.append(completed.stdout)
            if completed.stderr:
                lines.append(completed.stderr)
            lines.append(f"[exit {completed.returncode}]\n\n")

            if completed.returncode != 0:
                log_path.write_text("".join(lines))
                return f"verification command failed: {command}"

        log_path.write_text("".join(lines))
        return None

    def _verify_git_clean_if_enabled(self, session_dir: Path) -> str | None:
        if not self.config.require_clean_git:
            return None

        git_dir = self.project_dir / ".git"
        if not git_dir.exists():
            return None

        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.project_dir,
            capture_output=True,
            text=True,
        )
        (session_dir / "git-status.log").write_text(completed.stdout + completed.stderr)

        if completed.returncode != 0:
            return "git status check failed"

        if completed.stdout.strip():
            return "working tree is not clean at end of session"

        return None

    def _write_session_metadata(self, session_dir: Path, result: SessionResult) -> None:
        payload = asdict(result)
        payload["timestamp"] = datetime.now(UTC).isoformat()
        (session_dir / "session.json").write_text(json.dumps(payload, indent=2) + "\n")

    def _append_progress(self, result: SessionResult) -> None:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        summary = (
            f"## Session {result.session_id:04d} - {timestamp}\n"
            f"- phase: {result.phase}\n"
            f"- success: {result.success}\n"
            f"- message: {result.message}\n"
            f"- progress: {result.passing}/{result.total}\n"
        )

        if result.return_code is not None:
            summary += f"- return_code: {result.return_code}\n"

        summary += "\n"
        with self.progress_file.open("a") as f:
            f.write(summary)
