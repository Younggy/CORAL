"""Frozen baseline features; no labels, cross-stock joins or time_id predictors."""
import numpy as np
import pandas as pd

WINDOWS = (0, 300, 450)


def book_features(frame):
    frame = frame.sort_values(['time_id', 'seconds_in_bucket'], kind='stable').copy()
    for level in (1, 2):
        bid, ask = frame[f'bid_price{level}'], frame[f'ask_price{level}']
        bs, az = frame[f'bid_size{level}'], frame[f'ask_size{level}']
        frame[f'wap{level}'] = (bid * az + ask * bs) / (bs + az)
        frame[f'imbalance{level}'] = (bs - az) / (bs + az)
    frame['relative_spread'] = (frame.ask_price1 - frame.bid_price1) / ((frame.ask_price1 + frame.bid_price1) / 2)
    frame['depth'] = frame[['bid_size1', 'ask_size1', 'bid_size2', 'ask_size2']].sum(axis=1)
    outputs = []
    for start in WINDOWS:
        part = frame[(frame.seconds_in_bucket >= start) & (frame.seconds_in_bucket < 600)].copy()
        for level in (1, 2):
            # Differences never cross a bucket OR the start of a selected window.
            part[f'log_return{level}_sq'] = np.log(part[f'wap{level}'].astype('float64')).groupby(part.time_id).diff().pow(2)
        agg = part.groupby('time_id').agg(
            rv1=('log_return1_sq', 'sum'), rv2=('log_return2_sq', 'sum'),
            wap1_mean=('wap1', 'mean'), wap1_std=('wap1', 'std'),
            spread_mean=('relative_spread', 'mean'), spread_max=('relative_spread', 'max'),
            imbalance1_mean=('imbalance1', 'mean'), imbalance2_mean=('imbalance2', 'mean'),
            depth_mean=('depth', 'mean'), book_count=('seconds_in_bucket', 'count'))
        agg[['rv1', 'rv2']] = np.sqrt(agg[['rv1', 'rv2']])
        outputs.append(agg.add_prefix(f'book_{start}_'))
    return pd.concat(outputs, axis=1)


def trade_features(frame):
    frame = frame.sort_values(['time_id', 'seconds_in_bucket'], kind='stable')
    outputs = []
    for start in WINDOWS:
        part = frame[(frame.seconds_in_bucket >= start) & (frame.seconds_in_bucket < 600)].copy()
        part['return_sq'] = np.log(part.price.astype('float64')).groupby(part.time_id).diff().pow(2)
        agg = part.groupby('time_id').agg(rv=('return_sq', 'sum'), volume=('size', 'sum'),
            trade_count=('seconds_in_bucket', 'count'), order_count=('order_count', 'sum'),
            price_std=('price', 'std'))
        agg['rv'] = np.sqrt(agg['rv'])
        outputs.append(agg.add_prefix(f'trade_{start}_'))
    return pd.concat(outputs, axis=1)


def combine(book, trade):
    result = book_features(book).join(trade_features(trade), how='left')
    for start in WINDOWS:
        # These zeros mean an empty observed event set, not imputed score/label values.
        for name in ('volume', 'trade_count', 'order_count'):
            col = f'trade_{start}_{name}'
            result[col] = result[col].fillna(0)
    return result
