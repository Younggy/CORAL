"""Task boundaries; these tests never launch a research agent or train a model."""

import json
import socket
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from optiver_coral_grader.grader import DTYPE, Grader, metric, read_predictions, sandbox_profile

from coral.config import CoralConfig

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "examples/optiver_rv"
ARCHIVE = Path.home() / "Projects/evalforge"


def frames():
    return (
        pd.DataFrame({"stock_id": [1, 2], "time_id": [3, 4], "target": [1.0, 2.0]}),
        pd.DataFrame({"stock_id": [1, 2], "time_id": [3, 4], "prediction": [1.1, 1.8]}),
    )


def test_metric_known_value_and_order():
    labels, predictions = frames()
    assert metric(labels, predictions.iloc[::-1]) == pytest.approx(0.1)


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "missing",
        "extra",
        "unknown",
        "float_key",
        "nan",
        "infinity",
        "negative",
        "zero_target",
        "nan_target",
    ],
)
def test_metric_rejects_invalid_boundary(case):
    labels, predictions = frames()
    if case == "duplicate":
        predictions.iloc[1] = predictions.iloc[0]
    elif case == "missing":
        predictions = predictions.iloc[:1]
    elif case == "extra":
        predictions = pd.concat([predictions, predictions.iloc[:1]])
    elif case == "unknown":
        predictions.loc[0, "time_id"] = 99
    elif case == "float_key":
        predictions["stock_id"] = predictions.stock_id.astype(float)
    elif case in ["nan", "infinity", "negative"]:
        predictions.loc[0, "prediction"] = {"nan": np.nan, "infinity": np.inf, "negative": -1}[case]
    elif case == "zero_target":
        labels.loc[0, "target"] = 0
    elif case == "nan_target":
        labels.loc[0, "target"] = np.nan
    with pytest.raises(ValueError):
        metric(labels, predictions)


@pytest.mark.parametrize("case", ["valid", "symlink", "object", "wrong_shape", "trailing"])
def test_prediction_file_boundary(tmp_path, case):
    path = tmp_path / "predictions.npy"
    array = np.array([(1, 3, 1.1), (2, 4, 1.8)], dtype=DTYPE)
    if case == "object":
        array = np.array([{}, {}], dtype=object)
    elif case == "wrong_shape":
        array = array[:1]
    np.save(path, array)
    if case == "trailing":
        with path.open("ab") as f:
            f.write(b"x")
    elif case == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    if case == "valid":
        assert len(read_predictions(path, 2)) == 2
    else:
        with pytest.raises((ValueError, OSError)):
            read_predictions(path, 2)


def test_native_config_and_no_legacy_imports():
    config = CoralConfig.from_yaml(TASK / "task.yaml")
    assert config.grader.direction == "minimize"
    assert config.grader.parallel.max_workers == 1
    assert [a.runtime for a in config.agents.assignments] == ["codex", "dsh"]
    assert Path(config.workspace.results_dir) == ROOT / "results/optiver_rv"
    assert config.agents.research is True
    assert [h.name for h in config.agents.heartbeat] == [
        "reflect",
        "consolidate",
        "pivot",
        "lint_wiki",
    ]
    for path in (TASK / "grader/src").rglob("*.py"):
        assert "from evalforge" not in path.read_text()
        assert "import evalforge" not in path.read_text()


def test_failed_candidate_is_null_score(tmp_path, monkeypatch):
    config = CoralConfig.from_yaml(TASK / "task.yaml")
    grader = Grader(config.grader)
    grader.codebase_path = str(tmp_path / "candidate")
    grader.private_dir = str(tmp_path / ".coral/private")

    def failed(*args, **kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr("optiver_coral_grader.grader.run_candidate", failed)
    assert grader.evaluate().scores["eval"].value is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt required")
def test_actual_worker_label_and_network_isolation(tmp_path):
    config = CoralConfig.from_yaml(TASK / "task.yaml")
    python = config.grader.args["worker_python"]
    inputs = Path(config.grader.args["inputs"])
    candidate, output = tmp_path / "candidate", tmp_path / "output"
    candidate.mkdir()
    output.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    secret = private / "label"
    secret.write_text("canary")
    (candidate / "link").symlink_to(secret)
    profile = tmp_path / "worker.sb"
    profile.write_text(sandbox_profile(python, inputs, candidate, output))
    code = """
import json,os,socket,sys
from pathlib import Path
allowed,denied,out,port = json.loads(sys.argv[1])
with open(allowed,'rb') as f: f.read(1)
for p in denied:
 try:
  with open(p,'rb') as f: f.read(1)
 except PermissionError: pass
 else: raise RuntimeError('private label readable')
try: os.fork()
except PermissionError: pass
else: raise RuntimeError('fork allowed')
connection = socket.socket()
connection.settimeout(2)
try: connection.connect(('127.0.0.1',port))
except PermissionError: pass
else: raise RuntimeError('network allowed')
Path(out).write_text('PASS')
print('PASS')
"""
    denied = [
        secret,
        candidate / "link",
        TASK / "taskdata/research_labels.parquet",
        ARCHIVE / ".evalforge/rv9-1014/private/research_labels.parquet",
        ARCHIVE / "data/optiver_rv/train.csv",
    ]
    assert all(p.exists() for p in denied)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-f",
                str(profile),
                python,
                "-I",
                "-c",
                code,
                json.dumps(
                    [
                        str(inputs / "research_keys.parquet"),
                        list(map(str, denied)),
                        str(output / "proof"),
                        listener.getsockname()[1],
                    ]
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert result.returncode == 0, result.stderr
    assert (output / "proof").read_text() == "PASS"
