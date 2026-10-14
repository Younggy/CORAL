"""Fresh candidate execution and private RMSPE; no EvalForge research imports."""
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import uuid

from coral.grader import TaskGrader
import numpy as np
import pandas as pd
import psutil

DTYPE = np.dtype([('stock_id', '<i4'), ('time_id', '<i4'), ('prediction', '<f8')])
WORKER = Path(__file__).with_name('worker.py')


def metric(labels, predictions):
    keys = ['stock_id', 'time_id']
    if list(labels) != keys + ['target'] or labels.duplicated(keys).any():
        raise ValueError('invalid private label schema')
    if list(predictions) != keys + ['prediction'] or predictions.duplicated(keys).any():
        raise ValueError('invalid prediction schema or duplicate keys')
    if any(frame[k].dtype.kind not in 'iu' for frame in (labels, predictions) for k in keys):
        raise ValueError('keys must be integers')
    labels, predictions = (f.sort_values(keys) for f in (labels, predictions))
    if not np.array_equal(labels[keys].to_numpy(), predictions[keys].to_numpy()):
        raise ValueError('missing, extra or unknown prediction keys')
    y, p = labels.target.to_numpy(dtype='float64'), predictions.prediction.to_numpy(dtype='float64')
    if not len(y) or not np.isfinite(y).all() or (y <= 0).any():
        raise ValueError('private targets must be finite and positive')
    if not np.isfinite(p).all() or (p < 0).any():
        raise ValueError('predictions must be finite and nonnegative')
    with np.errstate(over='ignore', invalid='ignore'):
        result = float(np.sqrt(np.mean(np.square((y - p) / y), dtype='float64')))
    if not np.isfinite(result):
        raise ValueError('nonfinite RMSPE')
    return result


def read_predictions(path, count):
    # Reuse the existing fixed, bounded NumPy transport, without importing its controller.
    limit = 4096 + count * DTYPE.itemsize
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError('predictions must be a bounded regular file')
        raw = stream.read(limit + 1)
    buffer = io.BytesIO(raw)
    if np.lib.format.read_magic(buffer) != (1, 0):
        raise ValueError('unsupported prediction format')
    shape, fortran, dtype = np.lib.format.read_array_header_1_0(buffer, max_header_size=4096)
    if shape != (count,) or fortran or dtype != DTYPE or dtype.hasobject:
        raise ValueError('invalid prediction shape or dtype')
    if len(raw) - buffer.tell() != count * DTYPE.itemsize:
        raise ValueError('truncated or trailing prediction bytes')
    return pd.DataFrame(np.frombuffer(raw, dtype=DTYPE, offset=buffer.tell()).copy())


def sandbox_profile(python, inputs, candidate, output):
    python, inputs, candidate, output = map(lambda p: Path(p).absolute(), (python, inputs, candidate, output))
    base = Path(json.loads(subprocess.check_output(
        [str(python), '-I', '-c', 'import sys,json;print(json.dumps(sys.base_prefix))'],
        text=True, timeout=15)))
    read = [base, python.parent.parent, inputs, candidate, output, WORKER,
            Path('/usr'), Path('/System'), Path('/Library'), Path('/private/etc'),
            Path('/private/var/db/dyld'), Path('/dev')]
    rules = ['(version 1)', '(deny default)', '(allow sysctl-read)', '(allow mach-lookup)',
             '(allow process-info*)', '(allow file-read* (literal "/"))']
    for path in read:
        rules.append(f'(allow file-read* (subpath {json.dumps(str(path.resolve()))}))')
    for parent in sorted({str(p) for root in read for p in root.resolve().parents}):
        rules.append(f'(allow file-read-metadata (literal {json.dumps(parent)}))')
    for path in [python, python.resolve(), base / 'Resources/Python.app/Contents/MacOS/Python']:
        rules.append(f'(allow process-exec (literal {json.dumps(str(path))}))')
    rules += [f'(allow file-write* (subpath {json.dumps(str(output.resolve()))}))',
              '(allow file-write* (literal "/dev/null"))']
    return '\n'.join(rules) + '\n'


def run_candidate(python, inputs, candidate, output, log_dir, *, threads, timeout, rss_bytes,
                  host_reserve_bytes, output_bytes):
    if sys.platform != 'darwin':
        raise RuntimeError('this task currently requires macOS Seatbelt')
    output.mkdir(exist_ok=False, parents=True)
    log_dir.mkdir(exist_ok=True, parents=True)
    profile = log_dir / 'worker.sb'
    profile.write_text(sandbox_profile(python, inputs, candidate, output))
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(output), 'TMPDIR': str(output),
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
           **{k: str(threads) for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                                      'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    command = ['/usr/bin/sandbox-exec', '-f', str(profile), str(python), '-I', str(WORKER),
               str(inputs), str(candidate), str(output), str(threads)]
    start = time.monotonic()
    peak = 0
    with (log_dir / 'stdout.log').open('wb') as stdout, (log_dir / 'stderr.log').open('wb') as stderr:
        process = subprocess.Popen(command, cwd=candidate, env=env, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            while process.poll() is None:
                if time.monotonic() - start > timeout:
                    raise TimeoutError('candidate execution timeout')
                try:
                    peak = max(peak, psutil.Process(process.pid).memory_info().rss)
                except psutil.NoSuchProcess:
                    pass
                if peak > rss_bytes or psutil.virtual_memory().available < host_reserve_bytes:
                    raise RuntimeError('candidate memory boundary exceeded')
                size = sum(p.lstat().st_size for p in output.rglob('*') if not p.is_symlink())
                size += stdout.tell() + stderr.tell()
                if size > output_bytes:
                    raise RuntimeError('candidate output boundary exceeded')
                time.sleep(.1)
            if process.returncode:
                raise RuntimeError(f'candidate exited {process.returncode}; see execution logs')
            if sum(p.lstat().st_size for p in output.rglob('*') if not p.is_symlink()) + stdout.tell() + stderr.tell() > output_bytes:
                raise RuntimeError('candidate output boundary exceeded')
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return {'wall_seconds': time.monotonic() - start, 'peak_sampled_rss_bytes': peak,
            'compute_threads': threads, 'network': 'denied', 'process_fork': 'denied'}


class Grader(TaskGrader):
    def evaluate(self):
        candidate = Path(self.codebase_path).resolve()
        private = Path(self.private_dir).resolve()
        artifact = private / 'optiver_artifacts' / (candidate.name + '-' + uuid.uuid4().hex[:12])
        artifact.mkdir(parents=True)
        output = artifact / 'output'
        try:
            execution = run_candidate(
                Path(self.args['worker_python']), Path(self.args['inputs']), candidate, output, artifact,
                threads=int(self.args['threads']), timeout=max(1, (self.timeout or 1800) - 20),
                rss_bytes=int(self.args['rss_bytes']), host_reserve_bytes=int(self.args['host_reserve_bytes']),
                output_bytes=int(self.args['output_bytes']))
            labels = pd.read_parquet(private / 'taskdata/research_labels.parquet')
            predictions = read_predictions(output / 'predictions.npy', len(labels))
            value = metric(labels, predictions)
            model_name = json.loads((candidate / 'config.json').read_text())['execution']['model_artifact']
            if not isinstance(model_name, str) or Path(model_name).name != model_name or model_name in ('', '.', '..'):
                raise ValueError('invalid model artifact filename')
            model = output / model_name
            info = model.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not info.st_size:
                raise ValueError('trained model must be a nonempty regular file')
            summary = dict(rmspe=value, n=len(labels), execution=execution,
                           prediction_sha256=hashlib.sha256((output / 'predictions.npy').read_bytes()).hexdigest(),
                           model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                           interpretation='fixed_research_split_single_fit', budget_class=self.budget_class)
            (artifact / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
            (self.eval_logs_dir / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
            return self.score(value, f'Global RMSPE on {len(labels)} research_eval rows.', metadata=summary)
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            message = str(exc)
            (artifact / 'failure.json').write_text(json.dumps({'error': message}) + '\n')
            return self.fail(message)
        finally:
            for name in ('stdout.log', 'stderr.log'):
                source = artifact / name
                if source.is_file():
                    # Candidate stdout cannot contain targets the candidate could not read.
                    with source.open('rb') as stream:
                        (self.eval_logs_dir / name).write_bytes(stream.read(1024 * 1024))
