"""
Weekly 200 EMA interaction scanner.

Finds stocks currently interacting with their 200-week EMA and classifies HOW:
holding it as support, being rejected by it as resistance, breaking through it,
or merely approaching.

THREE THINGS TO UNDERSTAND BEFORE TRUSTING THIS

1. The current week is incomplete. A stock that has pierced the EMA and recovered
   on Wednesday can close below it on Friday. Mid-week readings are provisional
   and are flagged as such. Only Friday's close settles the week.

2. A weekly 200 EMA needs roughly 200 weeks of history. Anything listed in the
   last four years cannot have a meaningful one, and is excluded rather than
   given a half-warmed-up value that looks authoritative but is not.

3. Touching a moving average is not a signal by itself. Thousands of stocks touch
   a long EMA in any given week. What matters is the context this adds: whether
   price approached from above or below, whether the close held, how deep the
   wick pierced, and whether volume expanded. Those are reported separately so
   you can judge rather than take a label at face value.
"""

import datetime


def resample_weekly(candles):
    """Daily candles -> weekly OHLCV, keyed by ISO week so partial weeks stay intact."""
    weeks = {}
    order = []
    for c in candles:
        d = c["date"]
        d = d.date() if hasattr(d, "date") else d
        key = d.isocalendar()[:2]          # (iso_year, iso_week)
        if key not in weeks:
            weeks[key] = {"date": d, "open": c["open"], "high": c["high"],
                          "low": c["low"], "close": c["close"],
                          "volume": float(c["volume"]), "days": 1}
            order.append(key)
        else:
            w = weeks[key]
            w["high"] = max(w["high"], c["high"])
            w["low"] = min(w["low"], c["low"])
            w["close"] = c["close"]
            w["volume"] += float(c["volume"])
            w["days"] += 1
    return [weeks[k] for k in order]


def ema_series(values, span):
    """
    EMA seeded with a simple average of the first `span` values — this is what
    TradingView, MetaTrader and standard charting platforms do. Seeding from a
    single first value instead leaves a residual error of roughly half a percent
    even after 300 bars, which is enough to misclassify a stock sitting close to
    the level. Returns a list aligned to the tail of `values`.
    """
    if len(values) < span:
        return []
    k = 2.0 / (span + 1)
    out = [sum(values[:span]) / span]
    for v in values[span:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def classify(weekly, span=200, touch_band=1.5, lookback=6):
    """
    Classify how price is interacting with its weekly 200 EMA right now.

    touch_band: percent either side of the EMA that counts as 'at' the level.
                A pure high/low crossing is too strict — price often turns a
                fraction short of the exact value, and that is the same event.
    """
    # need the full seed window plus enough bars after it for the EMA to settle
    if len(weekly) < span + 20:
        return None

    closes = [w["close"] for w in weekly]
    ema_tail = ema_series(closes, span)
    if len(ema_tail) < 12:
        return None
    # ema_tail[0] corresponds to weekly[span-1]; pad the front so indices line up
    ema = [None] * (span - 1) + ema_tail
    if len(ema) != len(weekly):
        ema = ema[:len(weekly)] if len(ema) > len(weekly) else ema + [ema[-1]] * (len(weekly) - len(ema))

    cur, prev = weekly[-1], weekly[-2]
    e_cur, e_prev = ema[-1], ema[-2]
    if e_cur is None or e_prev is None:
        return None

    close = cur["close"]
    dist_pct = (close - e_cur) / e_cur * 100

    # did the EMA fall inside this week's range?
    pierced = cur["low"] <= e_cur <= cur["high"]
    near = abs(dist_pct) <= touch_band
    interacting = pierced or near

    # where was price in the weeks leading in? use closes, not wicks
    prior = weekly[-(lookback + 1):-1]
    prior_ema = ema[-(lookback + 1):-1]
    pairs = [(w, e) for w, e in zip(prior, prior_ema) if e is not None]
    if len(pairs) < 3:
        return None
    prior, prior_ema = [p[0] for p in pairs], [p[1] for p in pairs]
    above_count = sum(1 for w, e in zip(prior, prior_ema) if w["close"] > e)
    below_count = len(prior) - above_count
    was_above = above_count >= len(prior) - 1      # allow one exception
    was_below = below_count >= len(prior) - 1

    # wick geometry — how hard was the level defended?
    rng = max(cur["high"] - cur["low"], 1e-9)
    lower_wick_pct = (min(cur["open"], close) - cur["low"]) / rng * 100
    upper_wick_pct = (cur["high"] - max(cur["open"], close)) / rng * 100
    pierce_depth = ((e_cur - cur["low"]) / e_cur * 100) if cur["low"] < e_cur else 0.0
    overshoot = ((cur["high"] - e_cur) / e_cur * 100) if cur["high"] > e_cur else 0.0

    # volume against the 20-week norm
    vols = [w["volume"] for w in weekly[-21:-1]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    vol_ratio = round(cur["volume"] / avg_vol, 2) if avg_vol > 0 else None

    prev_dist = (prev["close"] - e_prev) / e_prev * 100

    kind, label, bias = None, None, "neutral"

    if was_above and close >= e_cur and pierced:
        kind, label, bias = "support_hold", "Held as support", "bullish"
    elif was_above and close < e_cur:
        kind, label, bias = "breakdown", "Broke down through", "bearish"
    elif was_below and close <= e_cur and pierced:
        kind, label, bias = "resistance_reject", "Rejected as resistance", "bearish"
    elif was_below and close > e_cur:
        kind, label, bias = "breakout", "Broke out above", "bullish"
    elif near and close >= e_cur:
        kind, label, bias = "testing_above", "Testing from above", "neutral"
    elif near and close < e_cur:
        kind, label, bias = "testing_below", "Testing from below", "neutral"
    elif interacting:
        kind, label, bias = "at_level", "At the level", "neutral"

    if kind is None:
        return None

    # crossings in the recent window signal a level being fought over
    crossings = 0
    for i in range(len(weekly) - lookback, len(weekly)):
        if i < 1 or ema[i] is None or ema[i - 1] is None:
            continue
        if (closes[i] > ema[i]) != (closes[i - 1] > ema[i - 1]):
            crossings += 1

    return {
        "kind": kind,
        "label": label,
        "bias": bias,
        "close": round(close, 2),
        "ema": round(e_cur, 2),
        "dist_pct": round(dist_pct, 2),
        "prev_dist_pct": round(prev_dist, 2),
        "pierced": bool(pierced),
        "pierce_depth_pct": round(pierce_depth, 2),
        "overshoot_pct": round(overshoot, 2),
        "lower_wick_pct": round(lower_wick_pct, 1),
        "upper_wick_pct": round(upper_wick_pct, 1),
        "vol_ratio": vol_ratio,
        "week_days": cur["days"],
        "weeks_of_history": len(weekly),
        # How much of the EMA is still the seed rather than real price action.
        # Under ~2% the value matches a full-history chart; above ~10% it does not.
        "seed_influence_pct": round(((1 - 2.0 / (span + 1)) ** max(0, len(weekly) - span)) * 100, 1),
        "ema_slope_pct": round((e_cur - ema[-14]) / ema[-14] * 100, 2)
                          if len(ema) > 14 and ema[-14] else None,
        "crossings_recent": crossings,
        "week_change_pct": round((close - cur["open"]) / cur["open"] * 100, 2),
    }


def score(row):
    """
    Rank by how much confirmation the interaction carries — not by how
    attractive the setup looks. Volume and wick geometry are evidence;
    a bare touch is not.
    """
    s = 0
    k = row["kind"]

    if k in ("support_hold", "resistance_reject"):
        s += 35                                    # level actually defended
    elif k in ("breakout", "breakdown"):
        s += 30                                    # level actually given up
    elif k in ("testing_above", "testing_below"):
        s += 18
    else:
        s += 10

    v = row.get("vol_ratio")
    if v:
        if v >= 2.0:   s += 25
        elif v >= 1.5: s += 16
        elif v >= 1.0: s += 8
        elif v < 0.6:  s -= 8                      # nobody showed up; weak evidence

    # a deep pierce that still closed the right side is a real rejection
    if k == "support_hold" and row["pierce_depth_pct"] >= 1.5:
        s += 12
    if k == "resistance_reject" and row["overshoot_pct"] >= 1.5:
        s += 12
    if k == "support_hold" and row["lower_wick_pct"] >= 40:
        s += 10
    if k == "resistance_reject" and row["upper_wick_pct"] >= 40:
        s += 10

    # a level crossed repeatedly lately is not being respected
    if row.get("crossings_recent", 0) >= 3:
        s -= 12

    slope = row.get("ema_slope_pct")
    if slope is not None:
        if k in ("support_hold", "breakout") and slope > 0:
            s += 8
        if k in ("resistance_reject", "breakdown") and slope < 0:
            s += 8

    return max(0, min(100, s))
