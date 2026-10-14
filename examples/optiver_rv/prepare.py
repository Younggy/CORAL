"""Prepare data only. Never starts CORAL agents or grades a candidate."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

TASK = Path(__file__).resolve().parent
ROOT = TASK.parents[1]
ARCHIVE = Path.home() / 'Projects/evalforge'
BASELINE = '90a13aef6126708f1fcce87b1ed4612fcbc6c000'


def main():
    sources = {}
    for name in ('features.py', 'train.py', 'config.json'):
        content = subprocess.check_output(['git', '-C', str(ARCHIVE / '.evalforge/rv9-1014/repo'),
                                           'show', f'{BASELINE}:{name}'])
        destination = TASK / 'seed' / name
        if destination.read_bytes() != content:
            raise ValueError('seed differs from historical initial commit: ' + name)
        sources[name] = hashlib.sha256(content).hexdigest()
    source = ARCHIVE / '.evalforge/rv9-1014/private/research_labels.parquet'
    target = TASK / 'taskdata/research_labels.parquet'
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        if target.read_bytes() != source.read_bytes():
            raise ValueError('existing task labels differ; refusing overwrite')
    else:
        shutil.copyfile(source, target)
    record = dict(baseline_commit=BASELINE, seed_sha256=sources,
                  research_labels_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                  inputs=str(ARCHIVE / 'tasks/optiver_rv/results/integration_spike/inputs'),
                  formal_research_started=False)
    (target.parent / 'PREPARATION.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
