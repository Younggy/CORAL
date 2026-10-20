"""Antigravity CLI (agy) lifecycle and stream-json session handling."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from coral.agent.process import open_agent_stderr_for_log_dir
from coral.agent.runtime import (
    AgentHandle,
    apply_run_as_user,
    apply_sandbox,
    apply_sandbox_env,
    write_coral_log_entry,
)
from coral.sandbox.protocol import AgentSandboxSpec
from coral.venv_paths import venv_bin_dir
from coral.workspace.repo import _clean_env

logger = logging.getLogger(__name__)


def _events(log_path: Path) -> Iterator[dict[str, Any]]:
    try:
        with log_path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event
    except OSError:
        return


class AntigravityRuntime:
    """Run agy with its own login, under CORAL's process and sandbox boundaries."""

    @property
    def instruction_filename(self) -> str:
        return "AGENTS.md"

    @property
    def shared_dir_name(self) -> str:
        return ".agents"

    def extract_session_id(self, log_path: Path) -> str | None:
        session_id = None
        for event in _events(log_path):
            kind = event.get("event")
            if not isinstance(kind, str):
                continue
            payload = event.get(kind) if kind in {"step_update", "result"} else event
            if kind not in {"init", "step_update", "result"} or not isinstance(payload, dict):
                continue
            value = payload.get("conversation_id")
            if isinstance(value, str) and value:
                session_id = value
        return session_id

    def classify_exit(
        self,
        log_path: Path,
        exit_code: int | None,
        uptime_seconds: float | None,
        min_clean_runtime_seconds: int = 60,
    ) -> str:
        # A long uptime or exit 0 alone does not prove a successful headless turn.
        result = None
        for event in _events(log_path):
            if event.get("event") == "result":
                result = event.get("result")
        if exit_code == 0 and isinstance(result, dict) and result.get("status") == "SUCCESS":
            return "clean"
        return "no_result"

    def start(
        self,
        worktree_path: Path,
        coral_md_path: Path,
        model: str = "gemini-3.8-flash-high",
        runtime_options: dict[str, Any] | None = None,
        max_turns: int = 0,
        log_dir: Path | None = None,
        verbose: bool = False,
        resume_session_id: str | None = None,
        prompt: str | None = None,
        prompt_source: str | None = None,
        task_name: str | None = None,
        task_description: str | None = None,
        gateway_url: str | None = None,
        gateway_api_key: str | None = None,
        run_as_user: dict[str, Any] | None = None,
        sandbox: AgentSandboxSpec | None = None,
    ) -> AgentHandle:
        if max_turns:
            raise ValueError(
                "agy does not support max_turns; use CORAL run.stop or runtime_options.print_timeout"
            )
        opts = runtime_options or {}
        unsupported = set(opts) - {"command", "effort", "print_timeout", "mounts", "role_file"}
        if unsupported:
            raise ValueError(f"Unsupported antigravity runtime options: {sorted(unsupported)}")
        if not model or model == "auto":
            raise ValueError("antigravity requires an explicit model from `agy models`")
        if gateway_url:
            logger.warning("Antigravity uses its own login and bypasses the CORAL model gateway")

        prompt = prompt or (
            "Session resumed. Continue evolving your solutions where you left off. "
            if resume_session_id
            else "Begin working on your task and iterating on the seed solution. "
        ) + (
            "Read AGENTS.md. There is no user in the loop — make decisions, run evals, "
            "accumulate knowledge, and iterate without waiting for input."
        )
        cmd = [
            str(opts.get("command") or "agy"),
            "--print",
            prompt,
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--mode",
            "accept-edits",
            # CORAL's outer sandbox contains all tools, as for the Codex runtime.
            "--dangerously-skip-permissions",
            "--print-timeout",
            str(opts.get("print_timeout", "0s")),
        ]
        if opts.get("effort"):
            cmd.extend(["--effort", str(opts["effort"])])
        if resume_session_id:
            cmd.extend(["--conversation", resume_session_id])

        env = _clean_env()
        venv = worktree_path / ".venv"
        env["VIRTUAL_ENV"] = env["UV_PROJECT_ENVIRONMENT"] = str(venv)
        env["PATH"] = str(venv_bin_dir(venv)) + os.pathsep + env.get("PATH", "")
        apply_sandbox_env(env, sandbox)
        user_kwargs = apply_run_as_user(env, run_as_user)

        id_file = worktree_path / ".coral_agent_id"
        agent_id = id_file.read_text().strip() if id_file.exists() else "unknown"
        log_dir = log_dir or worktree_path / ".agents" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_idx = len(list(log_dir.glob(f"{agent_id}*.log")))
        log_path = log_dir / f"{agent_id}.{log_idx}.log"
        cmd.extend(["--log-file", str(log_path.with_suffix(".debug"))])
        log_file = log_path.open("w", buffering=1, encoding="utf-8", errors="replace")
        # Keep stderr separate even outside the manager so stdout remains NDJSON.
        opened = open_agent_stderr_for_log_dir(log_dir, agent_id)
        err_path, err_file = opened or (
            log_path.with_suffix(".err"),
            log_path.with_suffix(".err").open("a", encoding="utf-8"),
        )
        try:
            write_coral_log_entry(
                log_file,
                prompt=prompt,
                source=prompt_source or ("restart" if resume_session_id else "start"),
                agent_id=agent_id,
                session_id=resume_session_id,
                task_name=task_name,
                task_description=task_description,
            )
            process = subprocess.Popen(
                apply_sandbox(cmd, sandbox),
                cwd=str(worktree_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if verbose else log_file,
                stderr=err_file,
                start_new_session=True,
                env=env,
                **user_kwargs,
            )
        except BaseException:
            log_file.close()
            err_file.close()
            raise

        if verbose:

            def tee() -> None:
                try:
                    if process.stdout is not None:
                        for line in iter(process.stdout.readline, b""):
                            decoded = line.decode("utf-8", errors="replace")
                            log_file.write(decoded)
                            sys.stdout.write(f"[{agent_id}] {decoded}")
                            sys.stdout.flush()
                finally:
                    log_file.close()
                    if process.stdout is not None:
                        process.stdout.close()

            threading.Thread(target=tee, daemon=True).start()

        return AgentHandle(
            agent_id=agent_id,
            process=process,
            worktree_path=worktree_path,
            log_path=log_path,
            session_id=resume_session_id,
            _log_file=None if verbose else log_file,
            err_file=err_file,
            err_path=err_path,
        )
