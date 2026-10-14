"""Adapted from Optiver _ml_worker.py; executed only inside Seatbelt."""
import importlib.util
import json
import random
import runpy
import torch
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from threadpoolctl import threadpool_limits

DTYPE = np.dtype([('stock_id', '<i4'), ('time_id', '<i4'), ('prediction', '<f8')])


def main():
    inputs, candidate, output = map(Path, sys.argv[1:4])
    threads = int(sys.argv[4])
    execution = json.loads((candidate / 'config.json').read_text())['execution']
    if execution['device'] not in ('cpu', 'mps'):
        raise ValueError('unsupported device on this host')
    seed = execution['seed']
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError('execution.seed must be uint32')
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    if execution['device'] == 'mps':
        if not torch.backends.mps.is_available():
            raise RuntimeError('requested MPS unavailable; no silent CPU fallback')
        torch.mps.manual_seed(seed)
        torch.mps.set_per_process_memory_fraction(0.25)
    (output / 'runtime.json').write_text(json.dumps(dict(seed=seed, device=execution['device'],
        python=sys.version, torch=torch.__version__, numpy=np.__version__)))
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    manifest = json.loads((inputs / 'input_manifest.json').read_text())
    train_labels = pd.read_parquet(inputs / 'train_labels.parquet')
    research_keys = pd.read_parquet(inputs / 'research_keys.parquet')
    keys = pd.concat([train_labels[['stock_id', 'time_id']], research_keys]).sort_values(['stock_id', 'time_id'])
    sys.path.insert(0, str(candidate.resolve()))
    spec = importlib.util.spec_from_file_location('features', candidate / 'features.py')
    features_module = importlib.util.module_from_spec(spec)
    sys.modules['features'] = features_module
    spec.loader.exec_module(features_module)  # Only inside confinement; never in scorer.
    started = time.monotonic()
    parts = []
    for stock in manifest['stock_ids']:
        book = pq.ParquetFile(inputs / f'book/stock_id={stock}.parquet').read(use_threads=False).to_pandas()
        trade = pq.ParquetFile(inputs / f'trade/stock_id={stock}.parquet').read(use_threads=False).to_pandas()
        frame = features_module.combine(book, trade).reset_index()
        frame.insert(0, 'stock_id', stock)
        parts.append(frame)
        del book, trade
        if len(parts) % 16 == 0:
            print('feature partitions', len(parts), flush=True)
    features = pd.concat(parts, ignore_index=True).sort_values(['stock_id', 'time_id'])
    if features[['stock_id', 'time_id']].values.tolist() != keys.values.tolist():
        raise ValueError('feature key coverage mismatch')
    if np.isinf(features.select_dtypes(include='number').to_numpy()).any():
        raise ValueError('nonfinite feature')
    train = features[features.time_id.isin(manifest['train_time_ids'])].merge(train_labels, on=['stock_id', 'time_id'], validate='one_to_one')
    research = features[features.time_id.isin(manifest['research_time_ids'])]
    train_file, infer_file = output / 'train_features.parquet', output / 'research_features.parquet'
    train.to_parquet(train_file, index=False)
    research.to_parquet(infer_file, index=False)
    feature_seconds = time.monotonic() - started
    del features, parts, keys, train, research, train_labels, research_keys
    # Preserve the frozen train.py source/config and its actual fit path; never load an old model.
    sys.argv = [str(candidate / 'train.py'), str(train_file), str(infer_file), str(candidate / 'config.json'), str(output)]
    with threadpool_limits(limits=threads):
        runpy.run_path(str(candidate / 'train.py'), run_name='__main__')
    prediction = pd.read_csv(output / 'predictions.csv', float_precision='round_trip')
    if list(prediction) != ['stock_id', 'time_id', 'prediction']:
        raise ValueError('invalid prediction CSV schema')
    if any(prediction[k].dtype.kind not in 'iu' for k in ('stock_id', 'time_id')):
        raise ValueError('prediction CSV keys must be integers')
    array = np.empty(len(prediction), dtype=DTYPE)
    for name in array.dtype.names:
        array[name] = prediction[name].to_numpy()
    np.save(output / 'predictions.npy', array, allow_pickle=False)
    (output / 'worker_summary.json').write_text(json.dumps({'fresh_fit': True, 'feature_wall_seconds': feature_seconds,
        'threads': threads, 'inference_rows': len(prediction), 'private_eval_labels_available': False}, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
