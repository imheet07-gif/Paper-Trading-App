import importlib.util
import os
import sys
import unittest

HAVE_PANDAS = importlib.util.find_spec("pandas") is not None and importlib.util.find_spec("numpy") is not None
HAVE_SKLEARN = importlib.util.find_spec("sklearn") is not None

from _load import ROOT  # noqa: E402

if HAVE_PANDAS:
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import numpy as np  # noqa: E402
    import pandas as pd  # noqa: E402
    import features as feat  # noqa: E402
    import mbrun  # noqa: E402
    import models  # noqa: E402
    import panel  # noqa: E402

N = 700


def made_up():
    """A made-up panel of 700 weekdays from 2014-01-01 (offline). UP rises 0.2% a day and UP2 0.1%; both liquid
    (Rs 10 crore a day), EQ. FALL falls 0.1% a day. THIN trades Rs 1 lakh a day (under the floor). SPARSE has a
    close one day in three. SMEC is SME (Rs 50 lakh a day). NEW lists at session 400. ETF is an INF fund unit.
    BADCA has a failed corporate action check."""
    days = pd.bdate_range("2014-01-01", periods=N)
    i = np.arange(N)
    spec = {"UP": ("INE001A01018", 100 * 1.002 ** i, 1e8, 0.0), "UP2": ("INE002A01018", 100 * 1.001 ** i, 1e8, 0.0),
            "FALL": ("INE003A01018", 100 * 0.999 ** i, 1e8, 0.0), "THIN": ("INE004A01018", 100 * 1.001 ** i, 1e5, 0.0),
            "SPARSE": ("INE005A01018", np.where(i % 3 == 0, 100.0, np.nan), 1e8, 0.0),
            "SMEC": ("INE006A01018", 100 * 1.0015 ** i, 5e6, 3.0),
            "NEW": ("INE007A01018", np.where(i >= 400, 50 * 1.001 ** (i - 400), np.nan), 1e8, 0.0),
            "ETF": ("INF008A01018", 100 * 1.002 ** i, 1e8, 0.0), "BADCA": ("INE009A01018", 100 * 1.002 ** i, 1e8, 0.0)}
    close = pd.DataFrame({v[0]: v[1] for v in spec.values()}, index=days)
    value = pd.DataFrame({v[0]: np.where(np.isnan(v[1]), np.nan, v[2]) for v in spec.values()}, index=days)
    code = pd.DataFrame({v[0]: np.where(np.isnan(v[1]), np.nan, v[3]) for v in spec.values()}, index=days)
    issue = pd.DataFrame(False, index=days, columns=close.columns)
    issue["INE009A01018"] = True
    frames = {"raw_close": close.copy(), "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
              "volume": close * 0 + 1000, "value": value, "deliv": close * 0 + 0.4, "series_code": code}
    return panel.Panel(frames, {v[0]: k for k, v in spec.items()}, code.eq(0.0), issue)


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Features(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = made_up()
        cls.start = panel.month_ends(cls.p.dates)[-2]
        cls.df = feat.build(cls.p, cls.start, cls.start, with_labels=False)
        cls.by = cls.df.set_index("sid")

    def test_universe_rules(self):
        # in: UP, UP2, FALL, SMEC (Rs 50 lakh a day clears the floor) and NEW (about 270 sessions by this start).
        # out: THIN (value floor), SPARSE (a close one day in three), ETF (INF, not a company share), BADCA (failed check)
        self.assertEqual(set(self.df["sid"]), {"INE001A01018", "INE002A01018", "INE003A01018", "INE006A01018",
                                                "INE007A01018"})

    def test_floor_is_a_mean_of_60_sessions(self):
        p = made_up()
        p.value["INE006A01018"] = 2e6  # Rs 20 lakh a day: under the floor
        df = feat.build(p, self.start, self.start, with_labels=False)
        self.assertNotIn("INE006A01018", set(df["sid"]))

    def test_sme_flag_and_group(self):
        self.assertEqual(self.by.loc["INE006A01018", "is_sme"], 1.0)
        self.assertEqual(self.by.loc["INE006A01018", "series_group"], "SME")
        self.assertEqual(self.by.loc["INE001A01018", "is_sme"], 0.0)
        self.assertEqual(self.by.loc["INE001A01018", "series_group"], "main board EQ")

    def test_new_listing_needs_a_year_of_history(self):
        me = panel.month_ends(self.p.dates)
        early = [d for d in me if 400 + 200 <= self.p.dates.get_loc(d) < 400 + 251][-1]  # under 252 closes so far
        df = feat.build(self.p, early, early, with_labels=False)
        self.assertNotIn("INE007A01018", set(df["sid"]))
        self.assertIn("INE007A01018", set(self.df["sid"]))  # in at the later start
        t = self.p.dates.get_loc(self.start)
        raw = feat._chunk(self.p, ["INE007A01018"], [t], self.p.close.notna().idxmax(), self.p.close * 0)
        self.assertEqual(raw["pre_store"].iloc[0], 0.0)
        self.assertAlmostEqual(raw["age_months"].iloc[0], (self.start - self.p.dates[400]).days / 30.4375, places=6)

    def test_ranks_follow_the_raw_ordering(self):
        r = self.by
        self.assertEqual(r.loc["INE001A01018", "ret_12m"], 1.0)  # the fastest riser ranks top
        self.assertEqual(r.loc["INE003A01018", "ret_12m"], r["ret_12m"].min())
        self.assertGreater(r.loc["INE001A01018", "mom_12_1"], r.loc["INE002A01018", "mom_12_1"])
        for c in feat.RANKED:
            self.assertTrue(r[c].between(0, 1).all(), c)

    def test_flags_and_age(self):
        r = self.by
        self.assertEqual(r.loc["INE001A01018", "pre_store"], 1.0)  # first close is the store's first session
        self.assertEqual(r.loc["INE007A01018", "pre_store"], 0.0)
        self.assertEqual(r.loc["INE001A01018", "age_months"], r["age_months"].max())  # the oldest rank top (tied)
        self.assertGreater(r.loc["INE001A01018", "age_months"], r.loc["INE007A01018", "age_months"])
        self.assertEqual(r.loc["INE007A01018", "age_months"], r["age_months"].min())
        t = self.p.dates.get_loc(self.start)
        raw = feat._chunk(self.p, ["INE001A01018"], [t], self.p.close.notna().idxmax(), self.p.close * 0)
        self.assertAlmostEqual(raw["age_months"].iloc[0], min(120.0, (self.start - self.p.dates[0]).days / 30.4375), places=6)
        self.assertTrue((r["be_share_252"] == r["be_share_252"].min()).all())  # nobody traded in BE

    def test_raw_values_match_a_hand_computation(self):
        t = self.p.dates.get_loc(self.start)
        raw = feat._chunk(self.p, ["INE001A01018"], [t], self.p.close.notna().idxmax(), self.p.close * 0)
        c = self.p.close["INE001A01018"]
        self.assertAlmostEqual(raw["ret_1m"].iloc[0], c.iloc[t] / c.iloc[t - 21] - 1, places=9)
        self.assertAlmostEqual(raw["mom_12_1"].iloc[0], c.iloc[t - 21] / c.iloc[t - 252] - 1, places=9)
        self.assertAlmostEqual(raw["prox_52w_high"].iloc[0], 1.0, places=9)  # rising every day
        self.assertAlmostEqual(raw["ma200_ratio"].iloc[0], c.iloc[t] / c.iloc[t - 199:t + 1].mean(), places=9)
        self.assertAlmostEqual(raw["vol_1y"].iloc[0], 0.0, places=9)  # a constant daily return
        self.assertAlmostEqual(raw["delivery_60"].iloc[0], 0.4, places=9)
        self.assertAlmostEqual(raw["log_value_60"].iloc[0], np.log(1e8), places=9)
        self.assertAlmostEqual(raw["max_drawdown_1y"].iloc[0], 0.0, places=9)

    def test_the_registered_feature_list(self):
        self.assertEqual(len(feat.FEATURES), 23)  # the table in FINDINGS.md lists 23 (its text said 22)
        self.assertEqual(set(feat.FLAGS), {"pre_store", "is_sme"})
        self.assertEqual(feat.FLOOR, 2.5e6)
        self.assertEqual((feat.MIN_TRADED, feat.MIN_HISTORY), (40, 252))


def synthetic(n_starts=60, per=40, seed=1):
    """A training table: month-end starts from 2013-01, `per` stocks each; mom_12_1 decides the label."""
    rng = np.random.default_rng(seed)
    starts = pd.date_range("2013-01-31", periods=n_starts, freq="ME")
    rows = []
    for s in starts:
        for k in range(per):
            x = rng.random()
            row = {"start": s, "sid": "S%03d" % k, "mq": x, "series_group": "main board EQ",
                   "multiple": 4.0 if x > 0.9 else (0.4 if x < 0.2 else 1.2), "delisted": False}
            for f in feat.FEATURES:
                row[f] = rng.random()
            row["mom_12_1"] = x
            row["prox_52w_high"] = x
            rows.append(row)
    return pd.DataFrame(rows)


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Models(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = synthetic()

    def test_purge_cutoffs_match_the_registration(self):
        self.assertEqual(models.purge_cutoff("2017-01-31"), pd.Timestamp("2014-01-31"))
        self.assertEqual(models.purge_cutoff("2018-01-31"), pd.Timestamp("2015-01-31"))
        self.assertEqual(models.purge_cutoff("2019-01-31"), pd.Timestamp("2016-01-31"))

    def test_dev_folds_never_train_on_a_window_that_reaches_the_test(self):
        df = synthetic(n_starts=84)  # 2013-01 to 2019-12
        folds = models.dev_folds(df)
        self.assertEqual([y for y, _, _ in folds], [2017, 2018, 2019])
        for y, tr, te in folds:
            first_test = df.loc[te, "start"].min()
            last_train = df.loc[tr, "start"].max()
            self.assertLessEqual(last_train + pd.DateOffset(months=36), first_test)
            self.assertFalse((tr & te).any())

    def test_validation_fold_is_the_registered_one(self):
        df = synthetic(n_starts=108)  # 2013-01 to 2021-12
        tr, te = models.validation_fold(df)
        self.assertEqual((df.loc[tr, "start"].min(), df.loc[tr, "start"].max()),
                         (pd.Timestamp("2013-01-31"), pd.Timestamp("2016-12-31")))
        self.assertEqual((df.loc[te, "start"].min(), df.loc[te, "start"].max()),
                         (pd.Timestamp("2020-01-31"), pd.Timestamp("2021-12-31")))

    def test_top_k_is_five_percent_rounded_up(self):
        self.assertEqual(models.top_k(969), 49)
        self.assertEqual(models.top_k(10), 1)
        top = models.picks(self.df, self.df["mom_12_1"])
        self.assertEqual(len(top), self.df["start"].nunique() * 2)  # ceil(5% of 40)

    def test_lift_when_the_score_is_the_label_driver(self):
        r = models.evaluate(self.df, self.df["mom_12_1"].values)
        self.assertGreater(r["pooled"]["top"]["hit"], 0.85)  # the top 5% are nearly all above 0.9
        self.assertLess(r["pooled"]["universe"]["hit"], 0.2)
        self.assertGreater(r["pooled"]["lift"], 4)
        flat = models.evaluate(self.df, np.zeros(len(self.df)))
        self.assertAlmostEqual(flat["pooled"]["lift"], flat["pooled"]["top"]["hit"] / flat["pooled"]["universe"]["hit"])

    def test_m1_and_m2(self):
        test = self.df.head(40)
        self.assertEqual(list(models.fit_predict("M1", test, test)), list(test["mq"]))
        a, b = test.iloc[[0]].copy(), test.iloc[[0]].copy()
        a["vol_1y"], b["vol_1y"] = 0.1, 0.9
        self.assertGreater(models.m2_score(a).iloc[0], models.m2_score(b).iloc[0])  # more volatile scores lower
        c, d = test.iloc[[0]].copy(), test.iloc[[0]].copy()
        c["mom_12_1"], d["mom_12_1"] = 0.9, 0.1
        self.assertGreater(models.m2_score(c).iloc[0], models.m2_score(d).iloc[0])  # more momentum scores higher
        self.assertEqual(len(models.m2_score(test)), 40)

    @unittest.skipUnless(HAVE_SKLEARN, "scikit-learn is needed for M3 and M4")
    def test_m3_and_m4_learn_the_driver(self):
        train, test = self.df[self.df["start"] < "2016-01-01"], self.df[self.df["start"] >= "2016-01-01"]
        for m in ("M3", "M4"):
            score = models.fit_predict(m, train, test)
            r = models.evaluate(test, score)
            self.assertGreater(r["pooled"]["lift"], 3, m)
            self.assertTrue(((score >= 0) & (score <= 1)).all())

    def test_hyperparameters_are_the_registered_ones(self):
        self.assertEqual(models.BOOSTING, {"max_depth": 3, "learning_rate": 0.05, "max_iter": 200,
                                           "min_samples_leaf": 500, "l2_regularization": 1.0,
                                           "early_stopping": False, "random_state": 0})
        self.assertEqual(models.LOGISTIC, {"C": 1.0, "solver": "lbfgs", "max_iter": 1000})
        self.assertEqual(models.M2_TERMS, (("mom_12_1", 1), ("prox_52w_high", 1), ("ma200_ratio", 1),
                                           ("delivery_60", 1), ("vol_1y", -1), ("log_value_60", -1)))

    def test_a_single_class_training_set_is_refused(self):
        train = self.df.assign(multiple=1.0)
        with self.assertRaises(ValueError):
            models.fit_predict("M3", train, self.df)

    def test_calibration_and_bootstrap(self):
        score = self.df["mom_12_1"].values
        cal, rho = models.calibration(self.df, score)
        self.assertEqual(len(cal), 10)
        self.assertEqual(cal["realised"].idxmax(), 9)  # the top decile of the score is the one that triples
        self.assertGreater(rho, 0.3)
        top = models.picks(self.df, score)
        lo, hi = models.bootstrap_lift(self.df, top, reps=100)
        self.assertLessEqual(lo, hi)

    def test_dev_bar(self):
        def mk(lift):
            return {"lift": lift}
        self.assertTrue(models.dev_pass({2017: mk(1.2), 2018: mk(0.9), 2019: mk(1.5)})[0])
        self.assertFalse(models.dev_pass({2017: mk(1.2), 2018: mk(0.9), 2019: mk(0.8)})[0])
        self.assertFalse(models.dev_pass({2017: mk(1.2), 2018: mk(1.3)})[0])  # a fold is missing

    def test_validation_bar(self):
        def res(lift, y20, y21, halved_top=0.2, halved_uni=0.2):
            return {"pooled": {"lift": lift, "top": {"halved": halved_top}, "universe": {"halved": halved_uni}},
                    "by_year": {2020: {"lift": y20}, 2021: {"lift": y21}}}
        m1 = res(1.3, 1.2, 1.4)
        self.assertTrue(models.validation_pass(res(1.6, 1.5, 1.7), m1, True)[0])
        self.assertFalse(models.validation_pass(res(1.4, 1.5, 1.3), m1, True)[0])  # pooled lift under 1.5
        self.assertFalse(models.validation_pass(res(1.6, 0.9, 2.3), m1, True)[0])  # a year at or below 1.0
        self.assertFalse(models.validation_pass(res(1.6, 1.5, 1.7), res(1.7, 1.5, 1.9), True)[0])  # M1 does better
        self.assertFalse(models.validation_pass(res(1.6, 1.5, 1.7, halved_top=0.3), m1, True)[0])  # halves more
        self.assertFalse(models.validation_pass(res(1.6, 1.5, 1.7), m1, False)[0])  # failed dev

    def test_the_simplest_passing_model_is_kept(self):
        self.assertEqual(models.simplest({"M4", "M3"}), "M3")
        self.assertEqual(models.simplest({"M2", "M4"}), "M2")
        self.assertIsNone(models.simplest(set()))


@unittest.skipUnless(HAVE_PANDAS, "numpy and pandas are needed for the research layer")
class Gate(unittest.TestCase):
    def test_validation_needs_a_dev_pass_and_only_one_look(self):
        dev_ok = {"stage": "dev", "model": "M2", "dev_pass": True}
        self.assertIn("has not passed", mbrun.gate(["M3"], [dev_ok]))
        self.assertIn("has not passed", mbrun.gate(["M2"], [{"stage": "dev", "model": "M2", "dev_pass": False}]))
        self.assertIsNone(mbrun.gate(["M2"], [dev_ok]))
        self.assertIsNone(mbrun.gate(["M1"], []))  # the reference needs no pass
        self.assertIn("already had", mbrun.gate(["M2"], [dev_ok, {"stage": "validation", "model": "M2"}]))

    def test_the_runner_reads_no_price_past_the_stage_limit(self):
        self.assertEqual(mbrun.DEV_END, "2022-12-31")
        self.assertEqual(mbrun.VALID_END, "2024-12-31")


if __name__ == "__main__":
    unittest.main()
