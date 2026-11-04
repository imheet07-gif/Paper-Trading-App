"""Phase 4 candidates, scoring and the pass bar (FINDINGS.md, 2026-11-04, "Phase 3").

Candidates: M1 the momentum_quality reference, M2 a scorecard, M3 logistic regression, M4 gradient boosting.
Every setting below was fixed in FINDINGS.md before any run; none is tuned.
"""

import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import features as feat  # noqa: E402

HORIZON = 36  # months
MULTIPLE = 3.0
TOP_SHARE = 0.05
MODELS = ("M1", "M2", "M3", "M4")
M2_TERMS = (("mom_12_1", 1), ("prox_52w_high", 1), ("ma200_ratio", 1), ("delivery_60", 1),
            ("vol_1y", -1), ("log_value_60", -1))
LOGISTIC = {"C": 1.0, "solver": "lbfgs", "max_iter": 1000}
BOOSTING = {"max_depth": 3, "learning_rate": 0.05, "max_iter": 200, "min_samples_leaf": 500,
            "l2_regularization": 1.0, "early_stopping": False, "random_state": 0}
# the pass bar
DEV_MIN_YEARS_ABOVE_ONE = 2  # of the three dev test years
VALID_MIN_POOLED_LIFT = 1.5
HALVED_TOLERANCE = 0.05
DEV_TEST_YEARS = (2017, 2018, 2019)
VALID_TRAIN = ("2013-01-01", "2016-12-31")
VALID_TEST = ("2020-01-01", "2021-12-31")


def purge_cutoff(first_test_start):
    """The last training start: its window (36 months) must end on or before the first test start."""
    return pd.Timestamp(first_test_start) - pd.DateOffset(months=HORIZON)


def dev_folds(df):
    """[(test year, train mask, test mask)] for the dev walk-forward on the training table `df`."""
    out = []
    for y in DEV_TEST_YEARS:
        test = df["start"].dt.year == y
        if not test.any():
            continue
        cutoff = purge_cutoff(df.loc[test, "start"].min())
        train = df["start"] <= cutoff
        out.append((y, train, test))
    return out


def validation_fold(df):
    lo, hi = (pd.Timestamp(x) for x in VALID_TRAIN)
    t0, t1 = (pd.Timestamp(x) for x in VALID_TEST)
    return (df["start"] >= lo) & (df["start"] <= hi), (df["start"] >= t0) & (df["start"] <= t1)


def target(df):
    return (df["multiple"] >= MULTIPLE).astype(int)


def m2_score(df):
    parts = [df[c] if sign > 0 else 1.0 - df[c] for c, sign in M2_TERMS]
    return sum(parts) / len(parts)


def fit_predict(model, train, test):
    """A score per test row (higher = more likely to triple). M3 and M4 return the predicted probability."""
    if model == "M1":
        return test["mq"].fillna(-np.inf).values
    if model == "M2":
        return m2_score(test).values
    cols = list(feat.FEATURES)
    y = target(train)
    if y.nunique() < 2:
        raise ValueError("the training starts hold no multibagger or only multibaggers")
    if model == "M3":
        from sklearn.linear_model import LogisticRegression
        clf = LogisticRegression(**LOGISTIC)
    elif model == "M4":
        from sklearn.ensemble import HistGradientBoostingClassifier
        clf = HistGradientBoostingClassifier(**BOOSTING)
    else:
        raise ValueError("unknown model %r" % model)
    clf.fit(train[cols].values, y.values)
    return clf.predict_proba(test[cols].values)[:, 1]


def top_k(n, share=TOP_SHARE):
    return max(1, math.ceil(share * n))


def picks(test, score):
    """The top K of every start by score (ties broken by security id)."""
    t = test.assign(_score=np.asarray(score, dtype=float)).sort_values(["start", "_score", "sid"],
                                                                      ascending=[True, False, True])
    ranked = t.groupby("start").cumcount()
    size = t.groupby("start")["sid"].transform("size")
    k = np.ceil(TOP_SHARE * size).clip(lower=1)
    return t[ranked < k]


def _rates(g):
    m = g["multiple"]
    return {"n": int(len(g)), "hit": float((m >= MULTIPLE).mean()) if len(g) else float("nan"),
            "halved": float((m <= 0.5).mean()) if len(g) else float("nan"),
            "delisted": float(g["delisted"].mean()) if len(g) else float("nan"),
            "median_multiple": float(m.median()) if len(g) else float("nan")}


def lift_of(top, universe):
    t, u = _rates(top), _rates(universe)
    lift = t["hit"] / u["hit"] if u["hit"] and u["hit"] > 0 else float("nan")
    return {"top": t, "universe": u, "lift": lift}


def evaluate(test, score):
    """Pooled and per-year results for one model on one set of test starts."""
    top = picks(test, score)
    out = {"pooled": lift_of(top, test), "by_year": {}}
    for y, g in test.groupby(test["start"].dt.year):
        out["by_year"][int(y)] = lift_of(top[top["start"].dt.year == y], g)
    out["by_group"] = {k: lift_of(top[top["series_group"] == k], g) for k, g in test.groupby("series_group")}
    out["top"] = top
    return out


def bootstrap_lift(test, top, reps=500, seed=0):
    """A 90% interval for the pooled lift, resampling securities (each appears in many starts)."""
    sids = test["sid"].unique()
    idx = {s: i for i, s in enumerate(sids)}
    n_u = np.zeros(len(sids)); h_u = np.zeros(len(sids)); n_t = np.zeros(len(sids)); h_t = np.zeros(len(sids))
    for s, g in test.groupby("sid"):
        n_u[idx[s]], h_u[idx[s]] = len(g), (g["multiple"] >= MULTIPLE).sum()
    for s, g in top.groupby("sid"):
        n_t[idx[s]], h_t[idx[s]] = len(g), (g["multiple"] >= MULTIPLE).sum()
    rng = np.random.default_rng(seed)
    lifts = []
    for _ in range(reps):
        w = np.bincount(rng.integers(0, len(sids), len(sids)), minlength=len(sids))
        nu, nt = (w * n_u).sum(), (w * n_t).sum()
        if nu == 0 or nt == 0 or (w * h_u).sum() == 0:
            continue
        lifts.append(((w * h_t).sum() / nt) / ((w * h_u).sum() / nu))
    return (float(np.percentile(lifts, 5)), float(np.percentile(lifts, 95))) if lifts else (float("nan"),) * 2


def calibration(test, prob, bins=10):
    """Realised 3x rate by decile of predicted probability (pooled test rows)."""
    t = test.assign(_p=np.asarray(prob, dtype=float))
    t["bin"] = pd.qcut(t["_p"].rank(method="first"), bins, labels=False)
    g = t.groupby("bin").agg(predicted=("_p", "mean"), realised=("multiple", lambda m: (m >= MULTIPLE).mean()),
                             n=("multiple", "size"))
    rho = g["predicted"].rank().corr(g["realised"].rank())
    return g.reset_index(), float(rho)


def overlap(top_a, top_b):
    """Mean share of a's top K that is also in b's top K, per start."""
    shares = []
    for s, a in top_a.groupby("start"):
        b = set(top_b.loc[top_b["start"] == s, "sid"])
        shares.append(len(set(a["sid"]) & b) / len(a))
    return float(np.mean(shares)) if shares else float("nan")


def dev_pass(folds):
    """folds {year: result}; criterion 1 of the bar: lift above 1.0 in at least two of the three test years."""
    ok = [y for y, r in folds.items() if r["lift"] > 1.0]
    return len(ok) >= DEV_MIN_YEARS_ABOVE_ONE and len(folds) == len(DEV_TEST_YEARS), ok


def validation_pass(res, m1_res, dev_ok):
    """Criteria 1 to 4 of the bar. res and m1_res are `evaluate` results on the validation starts."""
    pooled = res["pooled"]
    years = {y: r["lift"] for y, r in res["by_year"].items()}
    checks = {
        "dev: lift above 1.0 in two of three years": dev_ok,
        "pooled lift at least %.1f" % VALID_MIN_POOLED_LIFT: pooled["lift"] >= VALID_MIN_POOLED_LIFT,
        "lift above 1.0 in each of 2020 and 2021": all(years.get(y, 0) > 1.0 for y in (2020, 2021)),
        "pooled lift above M1's": pooled["lift"] > m1_res["pooled"]["lift"],
        "share halved within %d points of the universe's" % int(HALVED_TOLERANCE * 100):
            pooled["top"]["halved"] <= pooled["universe"]["halved"] + HALVED_TOLERANCE,
    }
    return all(checks.values()), checks


def simplest(passed):
    """Of the models that pass, the simplest: M2, then M3, then M4."""
    for m in ("M2", "M3", "M4"):
        if m in passed:
            return m
    return None
