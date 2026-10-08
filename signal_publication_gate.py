"""Pure publication gates for production signal scheduling.

Kept free of Modal/Qlib imports so trading-calendar freshness semantics can be
unit-tested in lightweight CI.
"""
from __future__ import annotations


def _publication_decision(cal, data_date: str, signal_date: str, fallback_days: int = 0):
    """Generic legacy publication gate using trading-session lag."""
    if data_date >= signal_date:
        return (
            "raise",
            f"异常数据日期：data_date={data_date} 必须早于 signal_date={signal_date}；拒绝发布",
        )
    if cal is not None:
        if signal_date not in cal:
            return (
                "skip",
                f"{signal_date} 非 A 股交易日（假日），不发布榜单；最近排名见最近一份已发布信号",
            )
        if data_date not in cal:
            return (
                "publish",
                f"⚠️ 交易日历不含数据日 {data_date}，日历源口径差异，fail-open 照常发布",
            )
        lag = sum(1 for c in cal if data_date < c < signal_date)
        if lag >= 2:
            return (
                "raise",
                f"数据滞后 {lag} 个交易日（{data_date} → {signal_date}），疑似数据源故障；不发布陈旧榜单",
            )
        if lag == 1:
            return (
                "publish",
                f"⚠️ 数据滞后 1 个交易日（{data_date} → {signal_date}，数据源漏发一轮），以最近可得数据发布",
            )
        return (
            "publish",
            f"{signal_date} 是交易日，数据为前一交易日收盘（lag=0），正常发布",
        )
    if fallback_days > 12:
        return (
            "raise",
            f"交易日历获取失败且数据滞后 {fallback_days} 自然日>12，不发布",
        )
    return (
        "publish",
        f"⚠️ 交易日历获取失败，fail-open 降级（数据距今 {fallback_days} 自然日 ≤12），照常发布",
    )


def _csi1000_canonical_publication_decision(cal, data_date: str, signal_date: str):
    """Require exact prior-session data for Stage-B winner canonical signals."""
    if cal is None:
        return (
            "raise",
            "CSI1000 Stage-B canonical requires a verified trading calendar; "
            "calendar unavailable, refusing publication",
        )
    if signal_date not in cal:
        return ("skip", f"{signal_date} 非 A 股交易日，不发布 CSI1000 canonical signal")
    if data_date not in cal:
        return (
            "raise",
            f"CSI1000 Stage-B canonical data_date={data_date} 不在交易日历；拒绝 fail-open",
        )
    if data_date >= signal_date:
        return (
            "raise",
            f"CSI1000 Stage-B canonical requires data_date < signal_date: "
            f"{data_date} >= {signal_date}",
        )
    lag = sum(1 for c in cal if data_date < c < signal_date)
    if lag != 0:
        return (
            "raise",
            f"CSI1000 Stage-B canonical requires lag=0 for T-close -> T+1-open; "
            f"got lag={lag} ({data_date} -> {signal_date})",
        )
    return (
        "publish",
        f"CSI1000 Stage-B canonical freshness PASS: {data_date} close -> "
        f"{signal_date} open (lag=0)",
    )
