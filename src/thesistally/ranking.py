"""Walk-forward ranker evaluation using sealed detection snapshots and resolved labels."""

import json
import math
from collections import defaultdict
from contextlib import closing
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import evaluation, market
from .journal import STRATEGIES, journal, metrics
from .signals import rules

BASE = "rs126-v1"
FEATURES = [
    "rs_percentile",
    "atr_fraction",
    "stop_fraction",
    "reward_risk",
    "log_turnover",
    "log_volume",
    "vix",
    "breadth",
    "regime",
    "pullback",
    "momentum",
    "vcp",
]


def values(strategy, entry, stop, target, f, regime):
    return [
        f.get("rs_percentile"),
        (f["atr"] / entry if f.get("atr") else None),
        (entry - stop) / entry,
        (target - entry) / (entry - stop),
        math.log1p(f["median_turnover"]) if f.get("median_turnover") else None,
        math.log1p(f["volume"]) if f.get("volume") else None,
        regime.get("vix"),
        regime.get("breadth"),
        int(regime["enabled"]),
        *[int(strategy == s) for s in STRATEGIES],
    ]


@lru_cache(maxsize=4)
def model(path, sha):
    import lightgbm as lgb

    raw = Path(path).read_bytes()
    if market.sha(raw) != sha:
        raise market.MarketError("Ranking model checksum mismatch.")
    return lgb.Booster(model_str=raw.decode())


def order(candidates, regime, cfg):
    if "results_strategy" in cfg:
        return sorted(candidates, key=lambda c: (-c.features["sue"], c.isin))
    if "range_bouncers" in cfg:
        return sorted(candidates, key=lambda c: (c.features["position60"], c.isin))
    if "dip" in cfg:
        return sorted(candidates, key=lambda c: (c.features["steady_rank"], c.isin))
    manifest = cfg.get("ranking_model")
    if not manifest or not candidates:
        return sorted(
            candidates,
            key=lambda c: (-(c.features.get("rs_percentile") or 0), c.symbol, c.strategy),
        )
    predictor = model(manifest["path"], manifest["sha256"])
    matrix = np.asarray(
        [values(c.strategy, c.entry, c.stop, c.target, c.features, regime) for c in candidates],
        dtype=float,
    )
    for c, p in zip(candidates, predictor.predict(matrix, num_threads=1)):
        c.features["probability_2r"] = float(p)
        c.features["ranking_score"] = float(p) * (c.target - c.entry) / (c.entry - c.stop)
        c.features["ranking_model_version"] = manifest["sha256"]
    return sorted(candidates, key=lambda c: (-c.features["ranking_score"], c.symbol, c.strategy))


def regime_analysis(rows, validation):
    groups = defaultdict(list)
    for r in rows:
        state = json.loads(r["regime"])
        for label, bucket in (
            ("gate", "pass" if state["enabled"] else "fail"),
            ("vix", "<15" if state["vix"] < 15 else "15–22" if state["vix"] <= 22 else ">22"),
            (
                "breadth",
                "<40%"
                if state["breadth"] < 0.4
                else "40–60%"
                if state["breadth"] <= 0.6
                else ">60%",
            ),
        ):
            groups[
                (
                    r["strategy"],
                    "portfolio" if r["allocation"] == "portfolio" else "shadow",
                    label,
                    bucket,
                )
            ].append(r)
    result = []
    for (strategy, book, group, bucket), items in sorted(groups.items()):
        stats = evaluation.trade_stats(items, validation)
        enough = stats["closed"] >= validation["min_per_window"]
        lo, hi = stats["mean_r_ci"]
        review = "Retain the current gate"
        if enough and hi is not None and hi < 0:
            review = "Test excluding this bucket in a new walk-forward revision"
        elif enough and lo is not None and lo > 0:
            review = "Test this favorable bucket in a new walk-forward revision"
        result.append(
            {
                "strategy": strategy,
                "book": book,
                "group": group,
                "bucket": bucket,
                **stats,
                "research_recommendation": review,
            }
        )
    return result


def study(root, path=None):
    import lightgbm as lgb

    from .provenance import snapshot

    cfg = rules()
    with closing(journal(root / "journal.sqlite")) as db:
        if path is None:
            ref = db.execute(
                "SELECT revision,experiment FROM holdout_uses WHERE status='used' ORDER BY end DESC LIMIT 1"
            ).fetchone()
            if not ref:
                raise market.MarketError("Complete the walk-forward benchmark first.")
            path = root / "backtests" / f"{ref[0]}-{ref[1]}.json"
        baseline = json.loads(Path(path).read_text())
        if baseline["rules"] != cfg["revision"] or baseline["parameters"]["mode"] != "walk_forward":
            raise market.MarketError(
                "Use a formal walk-forward report for the current rule configuration."
            )
        windows = [f for f in baseline["folds"] if f["period"] != "holdout"]
        archive = market.sessions(root)
        results = []
        history = []
        new_rows = []
        matched = []
        folder = root / "rankers"
        folder.mkdir(parents=True, exist_ok=True)
        proof = snapshot(root)
        training_spec = {
            "features": FEATURES,
            "num_leaves": 7,
            "trees": 45,
            "seed": 716,
            "gap_sessions": cfg["exits"]["max_days"],
            "library": lgb.__version__,
        }
        dest = folder / (
            market.sha(
                json.dumps([baseline["provenance"], proof, training_spec], sort_keys=True).encode()
            )[:20]
            + ".json"
        )
        if dest.exists():
            return json.loads(dest.read_text())
        for w in windows:
            start = date.fromisoformat(w["start"])
            end = date.fromisoformat(w["end"])
            prior = [d for d in archive if d < start]
            cutoff = str(prior[-1 - cfg["exits"]["max_days"]])
            train = [
                r for r in history if w["training"][0] <= r["day"] and r["closed_on"] <= cutoff
            ]
            real = [
                dict(r)
                for r in db.execute(
                    """SELECT c.*,e.day AS closed_on FROM calls c JOIN events e ON e.call_id=c.id AND e.kind='closed' WHERE c.run=? ORDER BY c.id""",
                    (w["run"],),
                )
            ]
            history.extend(real)
            outcome = {
                "period": w["period"],
                "train_cutoff": cutoff,
                "training_rows": len(train),
                "rs_baseline": w["portfolio"],
            }
            labels = [int(r["mfe"] >= 2) for r in train]
            if not w["complete"] or len(train) < 100 or len(set(labels)) < 2:
                outcome["status"] = (
                    "insufficient_resolved_training_data"
                    if w["complete"]
                    else "incomplete_baseline"
                )
                results.append(outcome)
                continue
            x = np.asarray(
                [
                    values(
                        r["strategy"],
                        r["entry"],
                        r["stop"],
                        r["planned_target"] if r["planned_target"] is not None else r["target"],
                        json.loads(r["features"]),
                        json.loads(r["regime"]),
                    )
                    for r in train
                ],
                dtype=float,
            )
            learner = lgb.LGBMClassifier(
                n_estimators=45,
                num_leaves=7,
                min_child_samples=20,
                random_state=716,
                deterministic=True,
                force_col_wise=True,
                verbosity=-1,
                n_jobs=1,
            )
            learner.fit(x, labels)
            raw = learner.booster_.model_to_string().encode()
            digest = market.sha(raw)
            file = folder / (digest + ".txt")
            with market.staging(folder) as tmp:
                (tmp / "model").write_bytes(raw)
                (tmp / "model").replace(file)
            manifest = {"path": str(file.resolve()), "sha256": digest, "trained_through": cutoff}
            run = "rank-" + dest.stem + "-" + w["period"]
            ranked = {**cfg, "ranking_model": manifest}
            gaps = evaluation.replay_window(
                db, root, [d for d in archive if start <= d <= end], [(run, ranked)]
            )
            alt = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM calls WHERE run=? AND allocation='portfolio'", (run,)
                )
            ]
            outcome.update(
                model=manifest,
                ranked=metrics(alt),
                status="incomplete" if gaps else "complete",
                valuation_gaps=gaps,
            )
            if not gaps:
                matched.extend(r for r in real if r["allocation"] == "portfolio")
                new_rows.extend(alt)
            results.append(outcome)
        rs = metrics(matched)
        new = metrics(new_rows)
        delta = new["expectancy_r"] - rs["expectancy_r"] if rs["closed"] and new["closed"] else None
        enough = (
            sum(r["status"] == "complete" for r in results) >= 2
            and min(rs["closed"], new["closed"]) >= cfg["validation"]["min_closed"]
        )
        report = {
            "research": "LightGBM probability of reaching 2R before exit",
            "provenance": {**baseline["provenance"], **proof, "model_version": lgb.__version__},
            "benchmark_report": str(path),
            "training": training_spec,
            "windows": results,
            "comparable_rs": rs,
            "comparable_model": new,
            "mean_r_delta": delta,
            "eligible_to_switch": bool(enough and delta is not None and delta > 0),
            "active_model": BASE,
            "regime_analysis": regime_analysis(history, cfg["validation"]),
            "holdout_used": False,
        }
        if report["eligible_to_switch"]:
            chosen = next(w["model"] for w in reversed(results) if w["status"] == "complete")
            report["active_model"] = chosen["sha256"]
            with market.staging(folder) as tmp:
                (tmp / "selected").write_text(
                    json.dumps(
                        {
                            "model": chosen,
                            "revision": cfg["revision"],
                            "evaluated_through": windows[-1]["end"],
                            "eligible_to_switch": True,
                            "evidence": str(dest),
                            "mean_r_delta": delta,
                        }
                    )
                )
                (tmp / "selected").replace(folder / "active.json")
        with market.staging(folder) as tmp:
            (tmp / "result").write_text(json.dumps(report, allow_nan=False, indent=2))
            (tmp / "result").replace(dest)
        return report
