import json
from pathlib import Path

from longrun_agent.harness import Harness, HarnessConfig
from longrun_agent.runtime.contracts import AgentRunResult


class _FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.invocations: list[str] = []
        self.reasoning_efforts: list[str | None] = []

    def run(self, request):
        self.invocations.append(request.phase)
        self.reasoning_efforts.append(request.model_reasoning_effort)
        request.session_dir.mkdir(parents=True, exist_ok=True)
        stdout = request.session_dir / "agent.stdout.log"
        stderr = request.session_dir / "agent.stderr.log"
        stdout.write_text(f"phase={request.phase}")
        stderr.write_text("")

        feature_path = request.project_dir / "feature_list.json"
        if request.phase == "initializer":
            features = [
                {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
                {"category": "functional", "description": "B", "steps": ["s1"], "passes": False},
            ]
            feature_path.write_text(json.dumps(features, indent=2))
            (request.project_dir / "init.sh").write_text("#!/usr/bin/env bash\necho init\n")
        else:
            features = json.loads(feature_path.read_text())
            for item in features:
                if not item["passes"]:
                    item["passes"] = True
                    break
            feature_path.write_text(json.dumps(features, indent=2))

        return AgentRunResult(
            backend=self.name,
            return_code=0,
            timeout=False,
            stdout_path=stdout,
            stderr_path=stderr,
            metadata={},
        )


class _ReadonlyRetryBackend:
    name = "codex_cli"

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def run(self, request):
        forced_write = bool(request.metadata.get("force_workspace_write"))
        self.calls.append((request.phase, forced_write))
        request.session_dir.mkdir(parents=True, exist_ok=True)
        stdout = request.session_dir / "agent.stdout.log"
        stderr = request.session_dir / "agent.stderr.log"

        feature_path = request.project_dir / "feature_list.json"
        if request.phase == "initializer":
            features = [
                {"category": "functional", "description": "A", "steps": ["s1"], "passes": False},
                {"category": "functional", "description": "B", "steps": ["s1"], "passes": False},
            ]
            feature_path.write_text(json.dumps(features, indent=2))
            (request.project_dir / "init.sh").write_text("#!/usr/bin/env bash\necho init\n")
            stdout.write_text("initialized")
            stderr.write_text("")
            return AgentRunResult(
                backend=self.name,
                return_code=0,
                timeout=False,
                stdout_path=stdout,
                stderr_path=stderr,
                metadata={},
            )

        if request.phase == "coding" and not forced_write:
            stdout.write_text("")
            stderr.write_text("workspace is read-only sandbox")
            return AgentRunResult(
                backend=self.name,
                return_code=23,
                timeout=False,
                stdout_path=stdout,
                stderr_path=stderr,
                metadata={},
            )

        features = json.loads(feature_path.read_text())
        for item in features:
            if not item["passes"]:
                item["passes"] = True
                break
        feature_path.write_text(json.dumps(features, indent=2))
        stdout.write_text("coding ok")
        stderr.write_text("")
        return AgentRunResult(
            backend=self.name,
            return_code=0,
            timeout=False,
            stdout_path=stdout,
            stderr_path=stderr,
            metadata={},
        )


def test_harness_uses_backend_factory_for_session_execution(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")

    fake_backend = _FakeBackend()
    monkeypatch.setattr(
        "longrun_agent.harness.create_backend",
        lambda **kwargs: fake_backend,
        raising=False,
    )

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=["bash", "-lc", "exit 99"],
            feature_target=2,
            model_reasoning_effort="xhigh",
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    first = harness.run_session()
    second = harness.run_session()

    assert first.success is True
    assert second.success is True
    assert fake_backend.invocations == ["initializer", "coding"]
    assert fake_backend.reasoning_efforts == ["xhigh", "xhigh"]


def test_harness_retries_once_with_workspace_write_when_readonly_detected(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "app_spec.txt").write_text("Build a basic task app")

    fake_backend = _ReadonlyRetryBackend()
    monkeypatch.setattr(
        "longrun_agent.harness.create_backend",
        lambda **kwargs: fake_backend,
        raising=False,
    )

    harness = Harness(
        HarnessConfig(
            project_dir=tmp_path,
            agent_command=["bash", "-lc", "exit 99"],
            feature_target=2,
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )
    )

    first = harness.run_session()
    second = harness.run_session()

    assert first.success is True
    assert second.success is True
    assert "recovered from read-only sandbox" in second.message
    assert fake_backend.calls == [
        ("initializer", False),
        ("coding", False),
        ("coding", True),
    ]
