"""
Crude ↔ natural gas ratio analysis.

WHAT IS ACTUALLY TRUE ABOUT THIS PAIR

They are neither reliably inverse nor reliably direct. Both are energy, so a
broad demand shock moves them together; beyond that they decouple, because:

  - Crude is global and transportable. Gas is regional and expensive to move,
    so Henry Hub, European TTF and Asian LNG can diverge for months.
  - Gas demand is strongly seasonal (heating, then cooling). Crude is far less so.
  - Gas is one of the most volatile traded commodities — often 2-3x crude's vol.
  - Since shale, US gas supply responds to its own dynamics, not oil's.

THE RATIO

Oil-to-gas is a long-standing spread trade. Energy equivalence is ~6:1 by heat
content, but the price ratio has ranged from roughly 5 to over 60. It does
mean-revert, but slowly and with brutal excursions on the way. This module
measures the reversion rather than assuming it, and reports the worst-case
drawdown alongside the average, because the average is what tempts you in and
the drawdown is what takes you out.
"""

import math
import datetime


def to_returns(series):
    dates = sorted(series.keys())
    out = {}
    for i in range(1, len(dates)):
        p, c = series[dates[i - 1]], series[dates[i]]
        if p and c and p != 0:
            out[dates[i]] = (c - p) / p * 100
    return out


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def build_ratio(crude, gas):
    """Oil-to-gas price ratio on shared dates."""
    common = sorted(set(crude.keys()) & set(gas.keys()))
    ratio = {}
    for d in common:
        if gas[d] and gas[d] > 0:
            ratio[d] = crude[d] / gas[d]
    return ratio


def zscore_series(ratio, window=252):
    """
    Ratio expressed as standard deviations from its own trailing mean.
    A trailing window matters: using the full-sample mean would leak future
    information and make historical extremes look far more tradeable than they were.
    """
    dates = sorted(ratio.keys())
    out = []
    for i in range(window, len(dates)):
        hist = [ratio[dates[j]] for j in range(i - window, i)]
        m = sum(hist) / len(hist)
        sd = math.sqrt(sum((v - m) ** 2 for v in hist) / len(hist))
        if sd > 0:
            out.append({
                "date": dates[i],
                "ratio": round(ratio[dates[i]], 2),
                "mean": round(m, 2),
                "z": round((ratio[dates[i]] - m) / sd, 2),
            })
    return out


def correlation_profile(crude, gas):
    """Return correlation overall, by year, and rolling — is it even stable?"""
    cr, gr = to_returns(crude), to_returns(gas)
    common = sorted(set(cr.keys()) & set(gr.keys()))
    xs = [cr[d] for d in common]
    ys = [gr[d] for d in common]

    overall = pearson(xs, ys)

    yearly = {}
    for d, x, y in zip(common, xs, ys):
        yearly.setdefault(d[:4], {"x": [], "y": []})
        yearly[d[:4]]["x"].append(x)
        yearly[d[:4]]["y"].append(y)

    rolling = []
    W = 126
    for i in range(W, len(xs), 5):
        r = pearson(xs[i - W:i], ys[i - W:i])
        if r is not None:
            rolling.append({"date": common[i - 1], "corr": round(r, 3)})

    # volatility comparison — the practical reason gas positions blow up
    def ann_vol(vals):
        m = sum(vals) / len(vals)
        return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) * math.sqrt(252)

    return {
        "overall_corr": round(overall, 4) if overall is not None else None,
        "n": len(xs),
        "yearly": [
            {"year": y, "corr": round(pearson(v["x"], v["y"]), 3) if pearson(v["x"], v["y"]) is not None else None,
             "n": len(v["x"])}
            for y, v in sorted(yearly.items()) if len(v["x"]) > 30
        ],
        "rolling": rolling,
        "crude_ann_vol_pct": round(ann_vol(xs), 1),
        "gas_ann_vol_pct": round(ann_vol(ys), 1),
        "vol_ratio": round(ann_vol(ys) / ann_vol(xs), 2) if ann_vol(xs) else None,
    }


def seasonality(gas):
    """Natural gas by calendar month — the dominant non-crude driver."""
    monthly = {}
    dates = sorted(gas.keys())
    for i in range(1, len(dates)):
        d = dates[i]
        p, c = gas[dates[i - 1]], gas[d]
        if p and p != 0:
            monthly.setdefault(d[5:7], []).append((c - p) / p * 100)
    names = {"01":"Jan","02":"Feb","03":"Mar","04":"Apr","05":"May","06":"Jun",
             "07":"Jul","08":"Aug","09":"Sep","10":"Oct","11":"Nov","12":"Dec"}
    out = []
    for m in sorted(monthly.keys()):
        vals = monthly[m]
        if len(vals) < 20:
            continue
        avg = sum(vals) / len(vals)
        out.append({
            "month": names[m],
            "avg_daily_pct": round(avg, 3),
            "avg_month_pct": round(avg * 21, 2),
            "up_share": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
            "n": len(vals),
        })
    return out


def reversion_backtest(zseries, ratio, entry_z=2.0, horizons=(20, 60, 120)):
    """
    The question that actually matters: when the ratio hit an extreme, did it
    revert — and what did you endure first?

    Reports average and median outcome, hit rate, AND worst-case adverse
    excursion. Mean reversion strategies die from the excursion, not the mean,
    so a backtest that hides it is worse than useless.
    """
    dates = [z["date"] for z in zseries]
    zmap = {z["date"]: z["z"] for z in zseries}
    rmap = {z["date"]: z["ratio"] for z in zseries}

    results = {}
    for side, cond in (("high", lambda z: z >= entry_z), ("low", lambda z: z <= -entry_z)):
        for h in horizons:
            trades = []
            i = 0
            while i < len(dates) - h:
                d = dates[i]
                if cond(zmap[d]):
                    entry = rmap[d]
                    path = [rmap[dates[j]] for j in range(i, min(i + h + 1, len(dates)))]
                    exit_v = path[-1]
                    # ratio falling is profit when short the ratio (high entry)
                    pnl = (entry - exit_v) / entry * 100 if side == "high" else (exit_v - entry) / entry * 100
                    if side == "high":
                        worst = max(path)
                        mae = (worst - entry) / entry * 100
                    else:
                        worst = min(path)
                        mae = (entry - worst) / entry * 100
                    trades.append({"pnl": pnl, "mae": mae})
                    i += h          # non-overlapping, so trades aren't double counted
                else:
                    i += 1
            if len(trades) >= 3:
                pnls = sorted(t["pnl"] for t in trades)
                maes = [t["mae"] for t in trades]
                results[f"{side}_{h}d"] = {
                    "trades": len(trades),
                    "avg_pnl_pct": round(sum(pnls) / len(pnls), 2),
                    "median_pnl_pct": round(pnls[len(pnls) // 2], 2),
                    "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
                    "worst_trade_pct": round(pnls[0], 2),
                    "best_trade_pct": round(pnls[-1], 2),
                    "avg_max_adverse_pct": round(sum(maes) / len(maes), 2),
                    "worst_max_adverse_pct": round(max(maes), 2),
                }
    return results


def _thin_keep_last(z, target=700):
    """Thin for chart rendering, but never drop the most recent bar —
    losing it makes the chart look stale and hides the current reading."""
    if not z:
        return []
    step = max(1, len(z) // target)
    out = [{"date": p["date"], "ratio": p["ratio"], "z": p["z"], "mean": p["mean"]}
           for p in z[::step]]
    last = z[-1]
    if not out or out[-1]["date"] != last["date"]:
        out.append({"date": last["date"], "ratio": last["ratio"],
                    "z": last["z"], "mean": last["mean"]})
    return out


def analyse(crude, gas, entry_z=2.0, z_window=252, intraday=False):
    ratio = build_ratio(crude, gas)
    min_needed = z_window + 60
    if len(ratio) < min_needed:
        return {"error": f"not enough overlapping bars ({len(ratio)}, need {min_needed})"}

    z = zscore_series(ratio, window=z_window)
    dates = sorted(ratio.keys())
    cur_ratio = ratio[dates[-1]]
    cur_z = z[-1]["z"] if z else None

    all_vals = sorted(ratio.values())
    pct_rank = sum(1 for v in all_vals if v < cur_ratio) / len(all_vals) * 100

    return {
        "current": {
            "ratio": round(cur_ratio, 2),
            "z": cur_z,
            "percentile": round(pct_rank, 1),
            "crude": round(crude[dates[-1]], 2),
            "gas": round(gas[dates[-1]], 3),
            "date": dates[-1],
            "mean": z[-1]["mean"] if z else None,
        },
        "history": _thin_keep_last(z),
        "correlation": correlation_profile(crude, gas),
        "seasonality": [] if intraday else seasonality(gas),
        "reversion": reversion_backtest(z, ratio, entry_z=entry_z,
                                        horizons=(20, 60, 120) if not intraday else (12, 36, 72)),
        "entry_z": entry_z,
        "range": {"min": round(min(all_vals), 2), "max": round(max(all_vals), 2),
                  "median": round(all_vals[len(all_vals) // 2], 2)},
        "bars": len(ratio),
        "intraday": intraday,
        "span": {"from": z[0]["date"] if z else None,
                 "to": z[-1]["date"] if z else None,
                 "plotted": None},
    }
