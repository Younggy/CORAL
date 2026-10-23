"""Exercise heartbeat cooldown through the manager's per-evaluation lookup."""

from pathlib import Path

import pytest

from coral.agent.manager import AgentManager
from coral.config import CoralConfig
from coral.hub.heartbeat import write_agent_heartbeat, write_global_heartbeat
from coral.workspace import ProjectPaths


@pytest.fixture
def manager(tmp_path: Path) -> AgentManager:
    config = CoralConfig.from_dict(
        {
            "task": {"name": "heartbeat-test", "description": "No agents are launched."},
            "agents": {"count": 2},
        }
    )
    manager = AgentManager(config)
    manager.paths = ProjectPaths(
        results_dir=tmp_path / "results",
        task_dir=tmp_path,
        run_dir=tmp_path,
        coral_dir=tmp_path / ".coral",
        agents_dir=tmp_path / "agents",
        repo_dir=tmp_path / "repo",
    )
    return manager


def _set_pivot(manager, agent_id, *, every=5, epsilon=0.0, prompt="pivot"):
    write_agent_heartbeat(
        manager.paths.coral_dir,
        agent_id,
        [
            {
                "name": "pivot",
                "every": every,
                "trigger": "plateau",
                "options": {"epsilon": epsilon},
                "prompt": prompt,
            }
        ],
    )


def _check(manager, agent_id, scores, *, minimize=False):
    return manager._get_heartbeat_runner(agent_id).check(
        local_eval_count=len(scores),
        global_eval_count=len(scores),
        score_history=scores,
        minimize=minimize,
    )


def test_manager_preserves_plateau_cooldown_per_agent(manager):
    agents = [spec.agent_id for spec in manager.specs]
    fired = {agent_id: [] for agent_id in agents}
    for agent_id in agents:
        _set_pivot(manager, agent_id)

    for count in range(1, 13):
        for agent_id in agents:
            actions = _check(manager, agent_id, [3.0] * count)
            if any(action.name == "pivot" for action in actions):
                fired[agent_id].append(count)

    assert fired == {agent_id: [6, 11] for agent_id in agents}


@pytest.mark.parametrize("minimize", [False, True])
def test_manager_resets_plateau_cooldown_after_improvement(manager, minimize):
    agent_id = manager.specs[0].agent_id
    _set_pivot(manager, agent_id, every=2)
    scores = [1.0] * 4 + ([0.5] if minimize else [2.0]) * 4
    fired = []
    for count in range(1, len(scores) + 1):
        actions = _check(manager, agent_id, scores[:count], minimize=minimize)
        if any(action.name == "pivot" for action in actions):
            fired.append(count)
    assert fired == [3, 7]


def test_manager_reloads_local_options_and_prompt(manager):
    agent_id = manager.specs[0].agent_id
    _set_pivot(manager, agent_id, every=2)
    assert any(a.name == "pivot" for a in _check(manager, agent_id, [1.0] * 3))

    _set_pivot(manager, agent_id, every=2, epsilon=0.1, prompt="changed {agent_id}")
    actions = _check(manager, agent_id, [1.0] * 3 + [1.05])
    assert [a.prompt for a in actions if a.name == "pivot"] == [f"changed {agent_id}"]
    actions = _check(manager, agent_id, [1.0] * 3 + [1.05] * 2)
    assert not any(a.name == "pivot" for a in actions)


def test_manager_reloads_global_interval(manager):
    agent_id = manager.specs[0].agent_id
    for every in (3, 4):
        write_global_heartbeat(
            manager.paths.coral_dir,
            [{"name": "consolidate", "every": every, "prompt": f"every {every}"}],
        )
        actions = _check(manager, agent_id, [1.0] * every)
        assert [(a.prompt, a.is_global) for a in actions] == [(f"every {every}", True)]


def test_manager_reloads_heartbeat_after_island_change(manager):
    agent_id = manager.specs[0].agent_id
    for island_id, every in (("0", 3), ("1", 4)):
        write_global_heartbeat(
            manager.paths.coral_dir,
            [{"name": "consolidate", "every": every, "prompt": f"island {island_id}"}],
            island_id=island_id,
        )
        manager._agent_island[agent_id] = island_id
        actions = _check(manager, agent_id, [1.0] * every)
        assert [a.prompt for a in actions] == [f"island {island_id}"]
