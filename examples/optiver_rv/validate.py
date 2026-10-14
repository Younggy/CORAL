"""One baseline wiring check using native CORAL APIs. Never launches agents."""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from coral.config import CoralConfig
from coral.grader.loader import load_grader
from coral.sandbox.protocol import AgentSandboxContext
from coral.sandbox.srt import SrtSandbox
from coral.task.validation import validate_task
from coral.types import Task
from coral.workspace.grader_env import setup_grader_env

TASK = Path(__file__).resolve().parent
ROOT = TASK.parents[1]
ARCHIVE = Path.home() / 'Projects/evalforge'
PROBE = '''
import json, pathlib, sys
allowed, denied = json.loads(sys.argv[1])
for path in allowed:
    with open(path, 'rb') as f: f.read(1)
results = {}
for path in denied:
    try:
        with open(path, 'rb') as f: f.read(1)
    except PermissionError:
        results[path] = 'DENIED'
    else:
        raise RuntimeError('private path readable: ' + path)
print(json.dumps(results))
'''


def verify_files(base, files, *, allow_symlinks=False):
    for name, expected in files.items():
        path = base / name
        if (path.is_symlink() and not allow_symlinks) or not path.is_file():
            raise ValueError('manifest file missing or symlink: ' + str(path))
        sha = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                sha.update(block)
        expected_sha = expected['sha256'] if isinstance(expected, dict) else expected
        if sha.hexdigest() != expected_sha:
            raise ValueError('manifest file hash mismatch: ' + str(path))
    return len(files)


def probe_agents(config, output, private, candidate):
    provider = SrtSandbox(config.agents.sandbox)
    provider.validate(config.agents)
    provider.start()
    results = {}
    try:
        for kind, shared in [('codex', '.codex'), ('dsh', '.dsh')]:
            context = AgentSandboxContext(kind, candidate, private.parent, candidate, shared)
            spec = provider.prepare_agent(context)
            denied = [private / 'taskdata/research_labels.parquet',
                      ARCHIVE / '.evalforge/rv9-1014/private/research_labels.parquet',
                      TASK / 'taskdata/research_labels.parquet', ARCHIVE / 'data/optiver_rv/train.csv']
            link = candidate / 'answer-link'
            link.unlink(missing_ok=True)
            link.symlink_to(denied[0])
            denied.append(link)
            if not all(p.exists() for p in denied):
                raise ValueError('isolation probe target missing')
            allowed = [candidate / 'features.py', Path(config.grader.args['inputs']) / 'research_keys.parquet']
            command = [*spec.command_prefix, config.grader.args['worker_python'], '-I', '-c', PROBE,
                       json.dumps([list(map(str, allowed)), list(map(str, denied))])]
            result = subprocess.run(command, cwd=candidate, env={**os.environ, **spec.env},
                                    capture_output=True, text=True, timeout=45)
            (output / f'{kind}-sandbox.stdout').write_text(result.stdout)
            (output / f'{kind}-sandbox.stderr').write_text(result.stderr)
            if result.returncode:
                raise RuntimeError(kind + ' native sandbox probe failed; see saved stderr')
            results[kind] = json.loads(result.stdout.strip().splitlines()[-1])
        return results
    finally:
        (candidate / 'answer-link').unlink(missing_ok=True)
        provider.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New evidence directory; never overwritten')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = CoralConfig.from_yaml(TASK / 'task.yaml')
    report = validate_task(TASK)
    if not report.valid:
        raise ValueError(str(report.diagnostics))
    private = output / '.coral/private'
    private.mkdir(parents=True)
    shutil.copytree(TASK / 'taskdata', private / 'taskdata')
    candidate = output / 'seed'
    shutil.copytree(TASK / 'seed', candidate)
    results = {'scope': 'ENVIRONMENT_SCORING_LABEL_ISOLATION_ONLY', 'research_agents_started': 0,
               'formal_research_started': False, 'candidate_fits': 0, 'structure': 'PASS'}
    try:
        inputs = Path(config.grader.args['inputs'])
        manifest = json.loads((inputs / 'input_manifest.json').read_text())
        results['input_files_verified'] = verify_files(inputs, manifest['files'])
        actual = {str(p.relative_to(inputs)) for p in inputs.rglob('*') if p.is_file() or p.is_symlink()}
        if actual != set(manifest['files']) | {'input_manifest.json'}:
            raise ValueError('unexpected files in exposed inputs')
        environment_file = Path(config.grader.args['worker_python']).parents[2] / 'environment.json'
        environment = json.loads(environment_file.read_text())
        results['worker_environment_files_verified'] = verify_files(Path('/'), environment['files'], allow_symlinks=True)
        results['agent_label_isolation'] = probe_agents(config, output, private, candidate)
        setup_grader_env(private.parent, config.grader, TASK)
        grader = load_grader(config, private.parent)
        result = asyncio.run(grader.grade(str(candidate), [Task('optiver', 'Optiver RV', config.task.description)]))
        results['candidate_fits'] = 1
        results['baseline'] = result.to_dict()
        if result.scores['eval'].value is None:
            raise RuntimeError('baseline evaluation failed; see private execution logs')
        # Compare with the historical pure formula without importing its framework.
        original = ARCHIVE / 'tasks/optiver_rv/adapter.py'
        import ast
        module = ast.parse(original.read_text())
        pure = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'rmspe')
        import math
        import numpy as np
        import pandas as pd
        namespace = {'np': np, 'math': math, 'CandidateOutputError': ValueError}
        exec(compile(ast.Module(body=[pure], type_ignores=[]), str(original), 'exec'), namespace)
        artifact = next((private / 'optiver_artifacts').glob('*/output/predictions.npy'))
        predictions = pd.DataFrame(np.load(artifact, allow_pickle=False)).sort_values(['stock_id', 'time_id'])
        labels = pd.read_parquet(private / 'taskdata/research_labels.parquet').sort_values(['stock_id', 'time_id'])
        reference = namespace['rmspe'](labels.target.to_numpy(), predictions.prediction.to_numpy())
        if reference != result.scores['eval'].value:
            raise ValueError('native score differs from historical RMSPE formula')
        results['independent_metric'] = {'value': reference, 'matches_native': True}
        results['environment'] = {n: importlib.metadata.version(n) for n in
                                  ['coral', 'numpy', 'pandas', 'pyarrow', 'psutil']}
        results['inputs_manifest_sha256'] = hashlib.sha256(
            (Path(config.grader.args['inputs']) / 'input_manifest.json').read_bytes()).hexdigest()
        results['status'] = 'PASS'
    except Exception as exc:
        results['status'] = 'FAIL'
        results['error'] = str(exc)
        raise
    finally:
        (output / 'VALIDATION.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
