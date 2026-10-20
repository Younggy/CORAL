"""Antigravity's real stream shape, process boundary, and config integration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from coral.agent.builtin.antigravity import AntigravityRuntime
from coral.agent.registry import default_command_for_runtime, get_runtime
from coral.agent.runtime import AgentRuntime
from coral.config import CoralConfig, SandboxConfig
from coral.sandbox.protocol import AgentSandboxSpec
from coral.sandbox.srt import build_srt_settings
from coral.workspace.worktree import setup_git_exclude


@pytest.mark.parametrize(
    "name", ["antigravity", "agy", "ang", "coral.agent.builtin.antigravity:AntigravityRuntime"]
)
def test_runtime_config_and_protocol(name, monkeypatch):
    from coral.agent import registry

    monkeypatch.setattr(registry, "_RUNTIMES", dict(registry._RUNTIMES))
    runtime = get_runtime(name)
    assert isinstance(runtime, AntigravityRuntime)
    assert isinstance(runtime, AgentRuntime)
    cfg = CoralConfig.from_dict(
        {
            "task": {"name": "test", "description": "fixture"},
            "agents": {"assignments": [{"runtime": name, "model": "gemini-3.8-flash-high"}]},
        }
    )
    assert cfg.agents.assignments[0].model == "gemini-3.8-flash-high"
    assert runtime.instruction_filename == "AGENTS.md"
    assert runtime.shared_dir_name == ".agents"
    assert default_command_for_runtime("agy") == "agy"


@pytest.mark.parametrize(
    "event",
    [
        {"event": "init", "conversation_id": "session-1"},
        {"event": "step_update", "step_update": {"conversation_id": "session-1"}},
        {"event": "result", "result": {"conversation_id": "session-1", "status": "SUCCESS"}},
    ],
)
def test_session_id_survives_truncated_or_noisy_logs(tmp_path, event):
    log = tmp_path / "agent.log"
    log.write_text("not json\n[]\nnull\n" + json.dumps(event) + '\n{"event":')
    assert AntigravityRuntime().extract_session_id(log) == "session-1"
    assert AntigravityRuntime().extract_session_id(tmp_path / "missing") is None


@pytest.mark.parametrize(
    ("status", "exit_code", "expected"),
    [
        ("SUCCESS", 0, "clean"),
        ("SUCCESS", 1, "no_result"),
        ("ERROR", 0, "no_result"),
        ("WAITING", 0, "no_result"),
        ("INTERRUPTED", -2, "no_result"),
        ("RUNNING", 0, "no_result"),
    ],
)
def test_classification_requires_terminal_success(tmp_path, status, exit_code, expected):
    log = tmp_path / "agent.log"
    log.write_text(json.dumps({"event": "result", "result": {"status": status}}) + "\n")
    assert AntigravityRuntime().classify_exit(log, exit_code, 1) == expected
    log.write_text('{"event":"init","conversation_id":"id"}\n')
    assert AntigravityRuntime().classify_exit(log, 0, 999) == "no_result"


def test_start_preserves_prompt_resume_and_outer_boundary(tmp_path, monkeypatch):
    process = Mock(pid=4242, stdout=None, stderr=None)
    process.poll.return_value = 0
    spawn = Mock(return_value=process)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    (tmp_path / ".coral_agent_id").write_text("agent-3")
    runtime = AntigravityRuntime()
    handle = runtime.start(
        tmp_path,
        tmp_path / "AGENTS.md",
        model="gemini-3.8-flash-high",
        runtime_options={"command": "/custom/agy", "effort": "high", "print_timeout": "90s"},
        log_dir=tmp_path / ".coral/public/logs",
        resume_session_id="session-1",
        prompt="Continue exactly this task.",
        sandbox=AgentSandboxSpec(["srt", "--settings", "policy.json", "--"], {"BOUNDARY": "1"}),
    )
    cmd = spawn.call_args.args[0]
    kwargs = spawn.call_args.kwargs
    assert cmd[:5] == ["srt", "--settings", "policy.json", "--", "/custom/agy"]
    assert cmd[cmd.index("--print") + 1] == "Continue exactly this task."
    assert cmd[cmd.index("--conversation") + 1] == "session-1"
    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash-high"
    assert cmd[cmd.index("--print-timeout") + 1] == "90s"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert "--dangerously-skip-permissions" in cmd
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"]["BOUNDARY"] == "1"
    assert kwargs["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert kwargs["env"]["VIRTUAL_ENV"] == str(tmp_path / ".venv")
    assert kwargs["env"]["PATH"].startswith(str(tmp_path / ".venv/bin"))
    assert handle.err_path == tmp_path / ".coral/public/diagnostics/agent-3/agent.err"
    assert json.loads(handle.log_path.read_text())["prompt"] == "Continue exactly this task."
    handle.stop()
    assert handle._log_file.closed and handle.err_file.closed


def test_spawn_failure_closes_opened_logs(tmp_path, monkeypatch):
    opened = []
    original = Path.open

    def tracked_open(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=FileNotFoundError("agy")))
    with pytest.raises(FileNotFoundError):
        AntigravityRuntime().start(tmp_path, tmp_path / "AGENTS.md")
    assert opened and all(stream.closed for stream in opened)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_turns": 2},
        {"model": "auto"},
        {"runtime_options": {"typo": True}},
    ],
)
def test_unsupported_limits_or_options_fail_before_spawn(tmp_path, kwargs):
    with pytest.raises(ValueError):
        AntigravityRuntime().start(tmp_path, tmp_path / "AGENTS.md", **kwargs)
    assert not (tmp_path / ".agents").exists()


def test_srt_grants_only_native_state_and_keeps_private_denied(tmp_path):
    settings = build_srt_settings(
        SandboxConfig(enabled=True),
        worktree_path=tmp_path / "agents/agent-3",
        coral_dir=tmp_path / ".coral",
        repo_dir=tmp_path / "repo",
        shared_dir_name=".agents",
        proxy_port=7890,
    )
    fs = settings["filesystem"]
    assert str(tmp_path / ".coral/private") in fs["denyRead"]
    assert str(tmp_path / ".coral/private") in fs["denyWrite"]
    assert str(Path.home() / ".gemini/antigravity-cli") in fs["allowRead"]
    assert str(Path.home() / ".gemini") not in fs["allowRead"]
    assert str(Path.home() / ".ssh") not in fs["allowRead"]


def test_macos_keychain_is_read_only_and_trustd_is_runtime_specific(tmp_path, monkeypatch):
    monkeypatch.setattr("coral.sandbox.srt.sys.platform", "darwin")
    kwargs = dict(
        worktree_path=tmp_path / "agents/agent-3",
        coral_dir=tmp_path / ".coral",
        repo_dir=tmp_path / "repo",
        proxy_port=7890,
    )
    settings = build_srt_settings(SandboxConfig(enabled=True), shared_dir_name=".agents", **kwargs)
    keychain = str(Path.home() / "Library/Keychains/login.keychain-db")
    assert keychain in settings["filesystem"]["allowRead"]
    assert keychain not in settings["filesystem"]["allowWrite"]
    assert str(Path.home() / "Library/Keychains") not in settings["filesystem"]["allowRead"]
    assert settings["network"]["allowMachLookup"] == ["com.apple.trustd.agent"]
    other = build_srt_settings(SandboxConfig(enabled=True), shared_dir_name=".codex", **kwargs)
    assert keychain not in other["filesystem"]["allowRead"]
    assert "allowMachLookup" not in other["network"]


def test_native_shared_directory_is_excluded_from_attempt_commits(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    setup_git_exclude(tmp_path)
    native = tmp_path / ".agents/notes"
    native.mkdir(parents=True)
    (native / "fixture.md").write_text("shared research note")
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert result.stdout == ""


def test_optiver_three_agent_config_preserves_grader_and_data():
    task_dir = Path(__file__).parents[1] / "examples/optiver_rv"
    base = CoralConfig.from_yaml(task_dir / "task.yaml")
    cfg = CoralConfig.from_yaml(task_dir / "task.antigravity.yaml")
    assert [entry.runtime for entry in cfg.agents.assignments] == ["codex", "dsh", "antigravity"]
    assert cfg.agents.assignments[:2] == base.agents.assignments
    assert cfg.agents.assignments[2].model == "gemini-3.8-flash-high"
    assert cfg.grader == base.grader
    assert cfg.task == base.task
    assert cfg.workspace == base.workspace
    assert cfg.agents.sandbox.enabled is True
    assert cfg.agents.sandbox.allow_read == base.agents.sandbox.allow_read
    assert cfg.agents.sandbox.extra_settings["network"]["httpProxyPort"] == 7890
