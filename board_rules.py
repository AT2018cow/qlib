"""Board-aware (板块) rules for A-share universe expansion: STAR (科创板) and ChiNext (创业板).

No Qlib import is required, so the functions can be unit-tested independently.
Fail-closed discipline follows qlib_audit_fixes.py: malformed inputs raise,
never silently degrade.

Price-limit facts (verified against exchange rules, 2026-09):
- STAR market (SH688*/SH689*): ±20% since launch 2019-07-22; first 5 trading
  sessions after listing: no price limit.
- ChiNext (SZ300*/SZ301*/SZ302*): ±10% before the registration reform
  (first session without limit: 2020-08-24), ±20% from 2020-08-24 onward;
  listings from 2020-08-24 onward: first 5 trading sessions no price limit.
- Main boards: ±10% (SZ00*/SZ30 pre-reform/SH60*); ST stocks ±5% everywhere
  (ST status cannot be inferred from the symbol; pass st_symbols explicitly).
- BSE (北交所, BJ*): ±30%.
Filter thresholds use the pipeline's audit口径 margin: nominal minus 0.5pp
(10%→0.095, 20%→0.195, 30%→0.295, 5%→0.045), matching the existing
|close/prev_close - 1| >= threshold filter.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from pathlib import Path

STAR_LAUNCH = "2019-07-22"
CHINEXT_REFORM = "2020-08-24"  # first session trading at ±20% on ChiNext
NEW_LISTING_SESSIONS = 5

TH_10 = 0.095
TH_20 = 0.195
TH_30 = 0.295
TH_5 = 0.045

POOL_NAME = "star_chn"
# 研究池族：合并池 star_chn + 单板池 chinext/star。拆池是批次A后的判别实验
# （合并稀释 vs 真没 α），三池全部跑完再下结论（多重检验纪律）。
POOL_BOARDS = {"star_chn": ("star", "chinext"), "chinext": ("chinext",), "star": ("star",)}
# 等权合成基准（chenditc 只含中证系指数，无创业板指/科创50；跨池基准污染归因）。
# 每池一个合成指数，纯派生自该池真实成分 close（日收益=有效前收股票等权均值），可复算可审计。
# 命名走 SZ39999x 未用号段（board_of 判为 index，不会混进股票池过滤）。
EW_BENCH = {"star_chn": "SZ399999", "chinext": "SZ399998", "star": "SZ399997"}
DEFAULT_BENCH = EW_BENCH[POOL_NAME]
BENCH_CANDIDATES = ("SZ399999", "SZ399998", "SZ399997", "SZ399006", "SH000688", "SZ399102", "SH000852")


def board_of(symbol: str) -> str:
    """Map an instruments-file symbol (SH600000/SZ300750/BJ430047/SH000852) to its board.

    Returns one of: "star", "chinext", "main", "bse", "index".
    """
    s = str(symbol).strip().upper()
    if len(s) != 8 or s[:2] not in ("SH", "SZ", "BJ"):
        raise ValueError(f"Unrecognized A-share symbol: {symbol!r}")
    body = s[2:]
    if s[:2] == "BJ":
        return "index" if body.startswith("899") else "bse"  # BJ899050=北证50 等指数
    # 指数按交易所前缀区分：SH000*/SH880*（上证综指/科创50/同花顺系列）、SZ399*（深证系列）。
    # 注意 SZ000xxx 是深市主板股票（平安银行 SZ000001），不是指数。
    if (s[:2] == "SH" and body.startswith(("000", "880"))) or (s[:2] == "SZ" and body.startswith("399")):
        return "index"
    if s[:2] == "SH" and body.startswith(("688", "689")):
        return "star"
    if s[:2] == "SZ" and body.startswith(("300", "301", "302")):
        return "chinext"
    return "main"


def limit_threshold(
    symbol: str,
    date: str,
    listing_dates: dict | None = None,
    calendar: list[str] | None = None,
    st_symbols: set | None = None,
) -> float | None:
    """Price-limit filter threshold |close/prev_close - 1| >= th for (symbol, date).

    Returns None when trading is unrestricted (no-limit listing sessions) or the
    symbol is an index/benchmark. `calendar` (sorted YYYY-MM-DD) makes the
    first-sessions rule exact; without it an approximate 7-calendar-day window
    is used (documented approximation, never raises).
    """
    b = board_of(symbol)
    if b == "index":
        return None
    if st_symbols is not None and symbol in st_symbols:
        return TH_5
    if listing_dates and symbol in listing_dates and b in ("star", "chinext"):
        if in_first_sessions(listing_dates[symbol], date, calendar=calendar):
            return None
    if b == "star":
        return TH_20
    if b == "chinext":
        return TH_20 if str(date)[:10] >= CHINEXT_REFORM else TH_10
    if b == "bse":
        return TH_30
    return TH_10


def in_first_sessions(
    listing_date: str, date: str, calendar: list[str] | None = None, n: int = NEW_LISTING_SESSIONS
) -> bool:
    """True if `date` falls within the first `n` trading sessions after `listing_date`.

    Without a calendar: approximate with n*1.4 calendar days (5 sessions ≈ 7 days).
    The listing day itself counts as session 1.
    """
    listing_date = str(listing_date)[:10]
    date = str(date)[:10]
    if date < listing_date:
        return False
    if calendar is not None:
        i0 = bisect_right(calendar, listing_date) - 1
        if i0 < 0 or calendar[i0] != listing_date:
            raise ValueError(f"listing_date {listing_date} not in calendar")
        i1 = bisect_right(calendar, date) - 1
        if i1 < i0:
            raise ValueError(f"date {date} precedes listing {listing_date} in calendar")
        return (i1 - i0) < n
    return date <= _approx_nth_session(listing_date, n)


def _approx_nth_session(listing_date: str, n: int) -> str:
    import datetime as dt

    d = dt.date.fromisoformat(listing_date) + dt.timedelta(days=int(n * 1.4))
    return d.isoformat()


def build_custom_instruments(
    all_txt_path: str | Path, out_path: str | Path, boards: tuple[str, ...] = ("star", "chinext")
) -> dict:
    """Filter an instruments file (SYMBOL\\tSTART\\tEND) by board prefix and write a new pool.

    Multi-row per-symbol span records are preserved verbatim. Fail-closed:
    missing/malformed input raises; zero matched rows raises.
    Returns {"star": n_symbols, "chinext": n_symbols, "dropped": n_rows}.
    """
    src = Path(all_txt_path)
    if not src.is_file():
        raise FileNotFoundError(f"Instruments source missing: {src}")
    rows = []
    seen: dict[str, str] = {}
    counts = {b: set() for b in boards}
    dropped = 0
    for line_no, line in enumerate(src.read_text().splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"Malformed instruments row {line_no} in {src}: {line!r}")
        sym = parts[0].strip().upper()
        b = board_of(sym)
        if b in counts:
            rows.append(line)
            counts[b].add(sym)
            seen[sym] = b
        else:
            dropped += 1
    if not rows:
        raise ValueError(f"No {boards} rows matched in {src}; pool would be empty")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows) + "\n")
    return {**{b: len(syms) for b, syms in counts.items()}, "dropped": dropped}


def board_aware_limited(
    day_ret,
    date: str,
    listing_dates: dict | None = None,
    calendar: list[str] | None = None,
    st_symbols: set | None = None,
) -> set:
    """Instruments to drop on `date` because they hit their board's price limit.

    `day_ret`: pd.Series with MultiIndex (instrument, datetime) of daily returns
    (close/prev_close - 1), the audit口径 used across the pipeline.
    Instruments without a computed return are never dropped (fail-open on data
    gaps is intentional: a missing prev_close row must not silently delete a signal).
    """
    import pandas as pd  # pylint: disable=C0415

    if not isinstance(day_ret, pd.Series):
        raise TypeError("day_ret must be a pd.Series with (instrument, datetime) MultiIndex")

    # 只评 date 当日（或 NaT）的行；异日行不属于 date 的信号集。
    # 显式逐行判定，避免 DatetimeIndex 与 Series 在布尔运算中的对齐坑。
    def _row_keep(_dt) -> bool:
        if pd.isna(_dt):
            return True
        return pd.Timestamp(_dt).strftime("%Y-%m-%d") == str(date)[:10]

    keep = [_row_keep(t) for t in day_ret.index.get_level_values(1)]
    day_ret = day_ret[keep]
    limited: set = set()
    # MultiIndex 键是 (instrument, datetime) 元组——直接 str(键) 会把整个元组当 symbol（静默失败）。
    # 阈值按每行自己的 datetime 判（管道单日取数时全行一致；NaT 行退回调用方传入的 date）。
    for (inst, _dt), ret in day_ret.dropna().items():
        row_date = _dt.strftime("%Y-%m-%d") if pd.notna(_dt) else str(date)[:10]
        th = limit_threshold(str(inst), row_date, listing_dates=listing_dates, calendar=calendar, st_symbols=st_symbols)
        if th is not None and abs(float(ret)) >= th:
            limited.add(str(inst))
    return limited


def star_chn_backtest_guard(start_time: str) -> None:
    """Backtest windows on the star_chn pool must start at/after the ChiNext reform.

    The pool mixes ±20% (STAR, post-reform ChiNext) and ±10% (pre-reform
    ChiNext) instruments; qlib's Exchange limit_threshold is a single float for
    the whole quote, so a window crossing the reform date would misprice half
    the pool. Refuse instead (fail-closed).
    """
    if str(start_time)[:10] < CHINEXT_REFORM:
        raise ValueError(
            f"star_chn backtest window start {start_time} < {CHINEXT_REFORM} "
            "(创业板注册制改革): pre-reform ChiNext trades at ±10% and the "
            "float limit_threshold cannot express per-instrument limits"
        )


def ew_index_matrix(close_matrix) -> tuple:
    """Equal-weight pool index from a calendar-aligned close matrix (fail-closed math core).

    `close_matrix`: 2D numpy array (n_symbols, n_days), NaN = no data (unlisted/
    suspended); row k aligned to the global calendar. Returns (mean_ret, idx_close):
    - mean_ret[t] = arithmetic mean over symbols with valid close[t] & close[t-1]
      (equal-weight, recomputed daily; NaN when no symbol has a valid return)
    - idx_close = cumulative product of (1 + mean_ret) with NaN treated as flat
      (1.0 before the first symbol ever has data; deterministic, reproducible)
    """
    import numpy as np  # pylint: disable=C0415

    m = np.asarray(close_matrix, dtype="<f")
    if m.ndim != 2 or m.shape[0] < 1:
        raise ValueError(f"close_matrix must be 2D (symbols, days): {m.shape}")
    n = m.shape[1]
    if n < 2:
        raise ValueError(f"close_matrix needs >= 2 days: {n}")
    prev = np.roll(m, 1, axis=1)
    prev[:, 0] = np.nan
    valid = ~np.isnan(m) & ~np.isnan(prev)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(valid, m / prev, np.nan) - 1.0
    cnt = valid.sum(axis=0)
    ssum = np.where(valid, np.nan_to_num(ratio), 0.0).sum(axis=0)
    mean_ret = np.where(cnt > 0, ssum / np.maximum(cnt, 1), np.nan)
    mean_ret[0] = np.nan
    if np.count_nonzero(cnt > 0) == 0:
        raise ValueError("No symbol produced a valid daily return in close_matrix")
    idx_close = np.cumprod(1.0 + np.nan_to_num(mean_ret, nan=0.0))
    return mean_ret, idx_close


def build_ew_bench_files(provider_dir, market: str) -> dict:
    """Build (or rebuild) the equal-weight synthetic benchmark for `market` inside a
    qlib provider dir. Single source of truth for both the Volume research path and
    the production daily container path — the two MUST NOT drift.

    Benchmark definition (R26, Option A decided 2026-10-05): **point-in-time
    reference index** — each instrument contributes only on calendar dates inside
    its membership span(s) from the pool file; suspended days are excluded from
    that day's equal-weight average (documented daily-reweighting semantics, not
    an investable buy-and-hold portfolio).

    Qlib bin layout per chenditc: file = [float32 header = calendar index of the
    first value] + values (calendar-aligned, NaN = missing bar). Reads the pool
    file instruments/<market>.txt (build it first if missing) and features/<sym>/
    close.day.bin, writes features/<bench>/{close,factor}.day.bin plus
    instruments/<bench>.txt. Fail-closed: missing calendar/pool file raises;
    zero valid returns raises. Returns a report dict.
    """
    import numpy as np  # pylint: disable=C0415
    import pandas as pd  # pylint: disable=C0415

    if market not in EW_BENCH:
        raise ValueError(f"market 须为 {'/'.join(EW_BENCH)}: {market}")
    root = Path(provider_dir)
    cal_file = root / "calendars" / "day.txt"
    if not cal_file.is_file():
        raise FileNotFoundError(f"等权基准构建需要日历: {cal_file}")
    cal = cal_file.read_text().strip().splitlines()
    if not cal:
        raise ValueError(f"空交易日历: {cal_file}")
    n = len(cal)
    pool_txt = root / "instruments" / f"{market}.txt"
    if not pool_txt.is_file():
        build_custom_instruments(root / "instruments" / "all.txt", pool_txt, boards=POOL_BOARDS[market])
    inst = pd.read_csv(pool_txt, sep="\t", header=None, names=["symbol", "start", "end"])
    symbols = sorted(inst["symbol"].unique())
    M = np.full((len(symbols), n), np.nan, dtype="<f")
    member = np.zeros((len(symbols), n), dtype=bool)  # R26 Option A: 成分区间强制
    missing = 0
    for k, sym in enumerate(symbols):
        p = root / "features" / sym.lower() / "close.day.bin"
        for _, s_row in inst[inst["symbol"] == sym].iterrows():
            i0 = bisect_left(cal, str(s_row["start"])[:10])
            i1 = bisect_right(cal, str(s_row["end"])[:10])
            if i1 > max(i0, 0):
                member[k, max(i0, 0):i1] = True
        if not p.is_file():
            missing += 1
            continue
        arr = np.fromfile(p, dtype="<f")
        if arr.size < 1:
            raise ValueError(f"close bin 为空: {p}")
        start, vals = int(arr[0]), arr[1:]
        if start < 0 or start >= n:
            continue
        take = min(len(vals), n - start)
        M[k, start: start + take] = vals[:take]
    # 区间外价格不参与（即使 bin 有数据也屏蔽）
    M = np.where(member, M, np.nan)
    mean_ret, idx_close = ew_index_matrix(M)
    n_days = int(np.count_nonzero(np.isfinite(mean_ret)))
    if n_days == 0:
        raise RuntimeError(f"{market}: 无任何有效等权收益——池文件与 features 不匹配")
    bench = EW_BENCH[market]
    out_dir = root / "features" / bench.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    np.concatenate([[0], idx_close]).astype("<f").tofile(out_dir / "close.day.bin")
    np.concatenate([[0], np.ones(n)]).astype("<f").tofile(out_dir / "factor.day.bin")
    (root / "instruments" / f"{bench.lower()}.txt").write_text(f"{bench}\t{cal[0]}\t{cal[-1]}\n")
    return {
        "market": market,
        "bench": bench,
        "n_symbols": len(symbols),
        "n_bins_missing": missing,
        "n_days_with_mean": n_days,
        "n_cal": n,
        "latest": round(float(idx_close[-1]), 6),
    }
