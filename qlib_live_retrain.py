"""Calendar-safe training splits and a reproducible monthly LightGBM cache policy.

The module has no Qlib/Modal import so boundary logic can be tested independently.
"""
from __future__ import annotations

from bisect import bisect_left
import hashlib
import json

from qlib_audit_fixes import last_matured_sample, purge_cfg_splits

RETRAIN_EVERY_SESSIONS = 20
VALIDATION_SESSIONS = 252
MODEL_CACHE_VERSION = 1


def configure_asof(cfg: dict, calendar: list[str], asof: str, *, horizon: int = 20,
                   validation_sessions: int = VALIDATION_SESSIONS,
                   train_start: str = '2016-01-01') -> dict:
    """Set expanding training, trailing validation and today's prediction.

    Only labels maturing strictly before the prediction day are used. The
    validation block is kept separate from training/processor fitting. Config
    mutation is intentional, matching Qlib's configuration builder.
    """
    if not calendar or calendar != sorted(set(calendar)):
        raise ValueError('Trading calendar must be strictly ordered and nonempty')
    if asof != calendar[-1]:
        raise ValueError('As-of date must equal the latest available trading bar')
    if horizon < 1 or validation_sessions < 60:
        raise ValueError('Invalid horizon or validation length')
    asof_i = len(calendar) - 1
    valid_end_i = asof_i - horizon - 1
    valid_start_i = valid_end_i - validation_sessions + 1
    if valid_start_i <= 0:
        raise ValueError('Insufficient history for validation and label maturity')
    valid_start = calendar[valid_start_i]
    train_end = last_matured_sample(calendar, valid_start, horizon)
    train_start_i = bisect_left(calendar, train_start)
    train_end_i = bisect_left(calendar, train_end)
    if train_end_i - train_start_i < 252:
        raise ValueError('Fewer than 252 training observations after purging')
    dk = cfg['task']['dataset']['kwargs']
    seg = dk['segments']
    handler = dk['handler']['kwargs']
    seg['train'] = [train_start, train_end]
    seg['valid'] = [valid_start, calendar[valid_end_i]]
    seg['test'] = [asof, asof]
    handler['start_time'] = min(str(handler.get('start_time', '2015-01-01')), '2015-01-01')
    handler['end_time'] = asof
    handler['fit_start_time'] = train_start
    handler['fit_end_time'] = train_end
    # Re-assert all boundaries even if a caller changes the calculation.
    purge_cfg_splits(cfg, calendar, horizon=horizon)
    return {'asof': asof, 'train_end': seg['train'][1],
            'valid_start': seg['valid'][0], 'valid_end': seg['valid'][1],
            'horizon': horizon}


def should_retrain(calendar: list[str], asof: str, previous_fit: str | None,
                   interval: int = RETRAIN_EVERY_SESSIONS) -> bool:
    """Count trading sessions, not calendar days. First run always trains."""
    if interval < 1 or not calendar or calendar != sorted(set(calendar)):
        raise ValueError('Invalid retraining interval or calendar')
    now = bisect_left(calendar, asof)
    if now >= len(calendar) or calendar[now] != asof:
        raise ValueError('As-of date not in calendar')
    if previous_fit is None:
        return True
    prior = bisect_left(calendar, previous_fit)
    if prior >= len(calendar) or calendar[prior] != previous_fit or prior > now:
        raise ValueError('Cached fit date missing from calendar or in future')
    return now - prior >= interval


def cache_signature(cfg: dict, *, horizon: int = 20,
                    train_start: str = '2016-01-01',
                    validation_sessions: int = VALIDATION_SESSIONS) -> str:
    """Change cache key if model, handler/instrument or split policy changes.

    Exclude rolling dates: otherwise every new daily bar invalidates the cache.
    """
    task = cfg['task']
    handler = task['dataset']['kwargs']['handler']
    opts = handler['kwargs']
    stable = {'cache_version': MODEL_CACHE_VERSION, 'model': task['model'],
              'handler_class': handler['class'], 'handler_module': handler.get('module_path'),
              'universe': opts['instruments'], 'labels': opts.get('label', 'Alpha158Default'),
              'infer_processors': opts.get('infer_processors', []),
              'learn_processors': opts.get('learn_processors', 'Alpha158Default'),
              'horizon': horizon, 'train_start': train_start,
              'validation_sessions': validation_sessions}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:24]
