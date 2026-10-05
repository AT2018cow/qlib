"""Calendar-safe training splits and a reproducible monthly LightGBM cache policy.

The module has no Qlib/Modal import so boundary logic can be tested independently.
"""
from __future__ import annotations

from bisect import bisect_left
import hashlib
import json
from pathlib import Path
import struct

from qlib_audit_fixes import last_matured_sample, purge_cfg_splits

RETRAIN_EVERY_SESSIONS = 20
VALIDATION_SESSIONS = 252
MODEL_CACHE_VERSION = 3
# 全局重训锚点（bar 日期）：两池共用同一重训时钟的相位原点。
# 选 2026-09-18 = csi1000 现役谱系的真实 fit bar（09-21 使用日 bootstrap 重训），
# 使创业板谱系回放与生产既有节律同相位；此后每 20 个交易日两池同日重训。
# 写死于代码（版本可审计），不放进配置——改相位是一次显式的代码变更。
RETRAIN_ORIGIN = "2026-09-18"


def retrain_due_calendar(calendar: list[str], data_bar: str,
                         interval: int = RETRAIN_EVERY_SESSIONS,
                         origin: str = RETRAIN_ORIGIN) -> bool:
    """Calendar-anchored global retrain schedule shared by ALL pools.

    Due when (idx(data_bar) - idx(origin)) is a nonnegative multiple of `interval`.
    Both pools derive from the same trading calendar, so they retrain on the
    identical session forever (fit windows then coincide exactly). A pool that
    misses a due day self-heals via its own 20-session rule; the next due day
    re-aligns both phases.
    """
    if interval < 1 or not calendar or calendar != sorted(set(calendar)):
        raise ValueError("Invalid retrain interval or calendar")
    for label, d in (("origin", origin), ("data_bar", data_bar)):
        i = bisect_left(calendar, d)
        if i >= len(calendar) or calendar[i] != d:
            raise ValueError(f"{label} {d} not in calendar")
    delta = bisect_left(calendar, data_bar) - bisect_left(calendar, origin)
    if delta < 0:
        return False  # 锚点之前的谱系（回放期）不由全局时钟管——调用方自行 bootstrap
    return delta % interval == 0


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


def provider_training_fingerprint(provider_dir, market: str, cutoff: str,
                                  fields=("open", "high", "low", "close", "volume", "factor")) -> str:
    """Hash only provider data that could have been visible to a cached model.

    The hash is clipped at cutoff (normally the model fit_asof), so appending
    tomorrow's bar does not invalidate today's cached model. A historical
    value revision, bin offset change, calendar rewrite, or point-in-time
    membership change at/before the cutoff does invalidate it.
    """
    root = Path(provider_dir)
    cal_file = root / "calendars" / "day.txt"
    inst_file = root / "instruments" / f"{market}.txt"
    if not cal_file.is_file() or not inst_file.is_file():
        raise FileNotFoundError(f"provider fingerprint inputs missing: {cal_file} / {inst_file}")

    calendar = [x.strip()[:10] for x in cal_file.read_text().splitlines() if x.strip()]
    if not calendar or calendar != sorted(set(calendar)):
        raise ValueError("invalid provider calendar for fingerprint")
    cutoff_i = bisect_left(calendar, str(cutoff)[:10])
    if cutoff_i >= len(calendar) or calendar[cutoff_i] != str(cutoff)[:10]:
        raise ValueError(f"fingerprint cutoff {cutoff} not in provider calendar")

    members = []
    symbols = set()
    for raw in inst_file.read_text().splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) < 3:
            raise ValueError(f"malformed instrument row in {inst_file}: {raw!r}")
        sym, start, end = parts[0].upper(), parts[1][:10], parts[2][:10]
        if start > calendar[cutoff_i]:
            continue
        clipped_end = min(end, calendar[cutoff_i])
        if clipped_end < start:
            continue
        members.append((sym, start, clipped_end))
        symbols.add(sym)

    h = hashlib.sha256()
    h.update(f"qlib-provider-prefix-v1|{market}|{calendar[cutoff_i]}\n".encode())
    h.update("\n".join(calendar[: cutoff_i + 1]).encode())
    h.update(b"\n--members--\n")
    for row in sorted(members):
        h.update(("\t".join(row) + "\n").encode())

    h.update(b"--features--\n")
    for sym in sorted(symbols):
        base = root / "features" / sym.lower()
        for field in fields:
            p = base / f"{field}.day.bin"
            h.update(f"{sym}|{field}|".encode())
            if not p.is_file():
                h.update(b"MISSING\n")
                continue
            with p.open("rb") as fh:
                header = fh.read(4)
                if len(header) != 4:
                    raise ValueError(f"invalid qlib bin header: {p}")
                start_idx = int(round(struct.unpack("<f", header)[0]))
                n_values = max(0, cutoff_i - start_idx + 1)
                prefix = fh.read(n_values * 4)
                h.update(header)
                h.update(prefix)
                if len(prefix) != n_values * 4:
                    h.update(f"|SHORT:{len(prefix)}/{n_values * 4}|".encode())
                h.update(b"\n")
    return h.hexdigest()


def cache_signature(cfg: dict, *, horizon: int = 20,
                    train_start: str = '2016-01-01',
                    validation_sessions: int = VALIDATION_SESSIONS,
                    runtime_lineage: dict | None = None) -> str:
    """Change cache key if model semantics, universe policy or runtime lineage changes.

    Rolling dates are excluded so each new daily bar does not invalidate the model.
    runtime_lineage is for stable code/dependency fingerprints, not the latest data date.
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
              'validation_sessions': validation_sessions,
              'runtime_lineage': runtime_lineage or {}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:24]
