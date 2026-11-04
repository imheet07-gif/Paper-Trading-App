"""Phase 4 features and the training table (FINDINGS.md, 2026-11-04, "Phase 3").

One row per (start, security) in the universe at that month-end, with the 22
registered features (percentile-ranked within the start, except the two flags),
momentum_quality's score, and the Phase 1 label. Everything is computed from
data to the start's close. The universe, the features and every window are the
ones fixed in FINDINGS.md before any model run; the choices the entry left open
(minimum observations in a rolling window, carried closes) are in FINDINGS.md
"implementation notes", written before the first run.
"""

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import labels as labels_mod  # noqa: E402
import panel as panel_mod  # noqa: E402
import signals as signals_mod  # noqa: E402

FLOOR = 2.5e6  # Rs 25 lakh mean daily traded value over 60 sessions
MIN_TRADED = 40  # sessions with a close among the last 60
MIN_HISTORY = 252
CARRY = 20  # sessions a close is carried (as the labels)
CHUNK = 800  # securities per block, to keep the rolling frames small

FEATURES = ("ret_1m", "ret_3m", "ret_6m", "ret_12m", "mom_12_1",
            "prox_52w_high", "prox_52w_low", "ma200_ratio", "ma50_over_ma200",
            "vol_1y", "vol_3m", "downside_vol_1y", "max_drawdown_1y",
            "log_value_60", "value_trend", "log_price",
            "delivery_60", "delivery_trend",
            "age_months", "pre_store", "lock_days_60", "be_share_252", "is_sme")
FLAGS = ("pre_store", "is_sme")
RANKED = tuple(f for f in FEATURES if f not in FLAGS)
SME_CODES = tuple(float(panel_mod.SERIES_CODE[s]) for s in ("SM", "ST", "SZ"))
BE_CODES = tuple(float(panel_mod.SERIES_CODE[s]) for s in ("BE", "BZ"))


def _chunk(p, cols, pos, first_close, mq):
    """Raw feature values at the sessions `pos`, for the securities `cols`, universe rows only."""
    close, raw, hi, lo = p.close[cols], p.raw_close[cols], p.high[cols], p.low[cols]
    val, dlv, code = p.value[cols], p.deliv[cols], p.series_code[cols]
    traded = close.notna()
    cf = close.ffill(limit=CARRY)
    r = cf.pct_change(fill_method=None)

    def roll(s, w, mp):
        return s.rolling(w, min_periods=mp)

    hi252 = roll(cf, 252, 200).max()
    ma200, ma50 = roll(cf, 200, 160).mean(), roll(cf, 50, 40).mean()
    tv60 = val.fillna(0.0).rolling(60, min_periods=1).sum() / 60.0
    tv250 = val.fillna(0.0).rolling(250, min_periods=1).sum() / 250.0
    d60, d250 = roll(dlv, 60, 10).mean(), roll(dlv, 250, 50).mean()
    code_last = code.ffill(limit=CARRY)
    lock = (hi.notna() & (hi == lo) & (r.abs() >= 0.02)).astype(float)
    be = code.isin(BE_CODES).astype(float)
    f = {
        "ret_1m": cf / cf.shift(21) - 1, "ret_3m": cf / cf.shift(63) - 1, "ret_6m": cf / cf.shift(126) - 1,
        "ret_12m": cf / cf.shift(252) - 1, "mom_12_1": cf.shift(21) / cf.shift(252) - 1,
        "prox_52w_high": cf / hi252, "prox_52w_low": cf / roll(cf, 252, 200).min(),
        "ma200_ratio": cf / ma200, "ma50_over_ma200": ma50 / ma200,
        "vol_1y": roll(r, 252, 200).std(), "vol_3m": roll(r, 63, 50).std(),
        "downside_vol_1y": roll(r.clip(upper=0.0) ** 2, 252, 200).mean() ** 0.5,
        "max_drawdown_1y": roll(cf / hi252 - 1, 252, 200).min(),
        "log_value_60": np.log(tv60.where(tv60 > 0)), "value_trend": tv60 / tv250.where(tv250 > 0),
        "log_price": np.log(raw.ffill(limit=CARRY).where(raw.ffill(limit=CARRY) > 0)),
        "delivery_60": d60, "delivery_trend": d60 / d250.where(d250 > 0),
        "lock_days_60": roll(lock, 60, 1).sum(),
        "be_share_252": roll(be, 252, 1).sum() / roll(traded.astype(float), 252, 1).sum().clip(lower=1.0),
        "is_sme": code_last.isin(SME_CODES).astype(float),
    }
    universe = (cf.notna() & (tv60 >= FLOOR) & (roll(traded.astype(float), 60, 1).sum() >= MIN_TRADED)
                & (traded.cumsum() >= MIN_HISTORY) & ~p.data_issue[cols] & code_last.notna())
    company = np.array([labels_mod.is_equity_share(c) for c in universe.columns])  # not ETFs, rights or bonds
    mask = (universe.values & company[None, :])[pos]
    ii, jj = np.nonzero(mask)
    if not len(ii):
        return pd.DataFrame()
    out = {"start": p.dates[pos][ii], "sid": np.asarray(cols)[jj]}
    for name, frame in f.items():
        a = frame.values[pos][mask]
        out[name] = np.where(np.isfinite(a), a, np.nan)
    fc = first_close.reindex(np.asarray(cols)[jj]).values
    out["age_months"] = np.minimum(120.0, (out["start"].values - fc).astype("timedelta64[D]").astype(float) / 30.4375)
    out["pre_store"] = (fc == np.datetime64(p.dates[0])).astype(float)
    out["raw_close"] = raw.ffill(limit=CARRY).values[pos][mask]
    out["traded_value"] = tv60.values[pos][mask]
    out["mq"] = mq[cols].values[pos][mask]
    last = code_last.values[pos][mask]
    names = {v: k for k, v in panel_mod.SERIES_CODE.items()}
    out["series_group"] = [labels_mod.SERIES_GROUP[names[int(x)]] for x in last]
    return pd.DataFrame(out)


def starts_in(p, first, last):
    me = panel_mod.month_ends(p.dates)
    return me[(me >= pd.Timestamp(first)) & (me <= pd.Timestamp(last))]


def build(p, first, last, with_labels=True):
    """The training table for month-end starts from `first` to `last`. Rows have the ranked features (0 to 1
    within the start; a missing value is 0.5), the two flags, `mq` (momentum_quality's raw score), and, with labels,
    `multiple`, `delisted` and `issue` from labels.label_rows. Rows whose window holds a failed corporate action
    check are dropped from the table (listed apart, as in Phase 1)."""
    starts = starts_in(p, first, last)
    pos = p.dates.get_indexer(starts)
    first_close = p.close.notna().idxmax().where(p.close.notna().any())
    mq = signals_mod.SIGNALS["momentum_quality"](p)
    cols = list(p.close.columns)
    parts = [_chunk(p, cols[k:k + CHUNK], pos, first_close, mq) for k in range(0, len(cols), CHUNK)]
    df = pd.concat([x for x in parts if len(x)], ignore_index=True)
    df["start"] = pd.to_datetime(df["start"])
    for c in RANKED:
        df[c] = df.groupby("start")[c].rank(pct=True).fillna(0.5)
    if with_labels:
        lab = labels_mod.label_rows(p, first, last)
        lab["start"] = pd.to_datetime(lab["start"])
        df = df.merge(lab[["start", "sid", "symbol", "multiple", "delisted", "issue"]], on=["start", "sid"], how="left")
        df = df[df["multiple"].notna() & ~df["issue"].fillna(False).astype(bool)].reset_index(drop=True)
    return df
