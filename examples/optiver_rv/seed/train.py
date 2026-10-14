"""One fixed LightGBM fit. Worker receives train labels, never evaluation labels."""
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd


def main():
    train_path, features_path, config_path, output_path = map(Path, sys.argv[1:])
    config = json.loads(config_path.read_text())
    train, evaluation = pd.read_parquet(train_path), pd.read_parquet(features_path)
    columns = [c for c in train if c not in ('time_id', 'target')]
    if 'target' in evaluation or list(evaluation.columns) != ['stock_id', 'time_id'] + [c for c in columns if c != 'stock_id']:
        raise ValueError('inference frame must contain features only in frozen order')
    stocks = sorted(map(int, train.stock_id.unique()))
    if not set(map(int, evaluation.stock_id.unique())) <= set(stocks):
        raise ValueError('unknown stock in inference data')
    for frame in (train, evaluation):
        frame['stock_id'] = pd.Categorical(frame.stock_id, categories=stocks)
    y = train.target.to_numpy(dtype='float64')
    if not np.isfinite(y).all() or (y <= 0).any():
        raise ValueError('invalid train targets')
    weights = 1 / np.square(y)
    weights /= weights.mean()  # Constant scaling does not change weighted-L2 minimizer.
    model = lgb.LGBMRegressor(**config['model'])
    start = time.monotonic()
    model.fit(train[columns], y, sample_weight=weights, categorical_feature=['stock_id'])
    fit_seconds = time.monotonic() - start
    values = model.predict(evaluation[columns])
    output_path.mkdir(exist_ok=True, parents=True)
    pd.DataFrame({'stock_id': evaluation.stock_id.astype('int64'), 'time_id': evaluation.time_id.astype('int64'),
                  'prediction': values}).to_csv(output_path / 'predictions.csv', index=False, float_format='%.17g')
    model.booster_.save_model(str(output_path / 'model.txt'))
    (output_path / 'training.json').write_text(json.dumps({'fit_count': 1, 'fit_wall_seconds': fit_seconds,
        'train_rows': len(train), 'inference_rows': len(evaluation), 'feature_columns': columns,
        'seed': config['model']['random_state'], 'stock_categories': stocks,
        'early_stopping': False, 'eval_labels_available': False}, indent=2) + '\n')
    print(json.dumps({'fit_count': 1, 'fit_wall_seconds': fit_seconds, 'prediction_rows': len(values)}), flush=True)


if __name__ == '__main__':
    main()
