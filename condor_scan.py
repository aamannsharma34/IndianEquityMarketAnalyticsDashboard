"""
Condor setup scanner — big directional move, then a tight range on drying volume.

Copyright (c) 2026 Amans2bmm. All rights reserved.
Part of the Market Analytics Dashboard. Unauthorised copying, distribution,
or derivative use without express written permission is prohibited.

WHAT THIS LOOKS FOR

A condor (or short strangle) wants the opposite of a trend: price pinned inside
a band until expiry. The setup that produces that is a stock which has already
spent its energy on a large move and is now going sideways while participation
drains away. Three things must be true together, and the third is the one that
matters most:

  1. A LARGE PRIOR MOVE. The move is what created the elevated implied vol that
     makes writing worthwhile. Without it there is no premium to collect.

  2. A TIGHT RANGE NOW. Measured on 4h and confirmed on 1h.

  3. VOLUME DRYING UP. A range on rising volume is accumulation or distribution
     — a coil, not a rest. The same range on falling volume is disinterest,
     which is what keeps price still. This filter is never relaxed.

CALIBRATED FROM REAL SETUPS, NOT FROM A SPEC

Two charts supplied as valid examples (CENTRALBK and ICICIGI, both 4h) measured
3.04% and 3.06% total range width — i.e. about +/- 1.5% from the midpoint, not
the +/- 3% originally specified. The defaults below follow the charts.

  CENTRALBK  -10.89% over 51 bars, then 3.04% range, RVol 97, Vol Buzz -3%
  ICICIGI    -17.51% then +13.04%, then 3.06% range over 26 bars / 19 days

CONSOLIDATION IS DETECTED, NOT ASSUMED

Rather than fixing a calendar window ("after the 15th"), the range is grown
backwards from the latest bar for as long as it stays tight. That is how the
boxes on those charts were actually drawn, it adapts to each stock, and it does
not break when the scan is run early in a month.

HONEST LIMITS

  - The hit probability is an empirical base rate from this stock's own history,
    not a model. It says how often price HAS stayed inside a band of this width
    over this horizon. Small samples are flagged.
  - Score weights are hand-assigned judgement. Nothing here is backtested.
  - Volume dry-up is measured with median/MAD, not mean/SD, because block trades
    spike volume the same way expiry rollover spikes open interest.
"""

import datetime
import math


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _median(vals):
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _robust_scale(values):
    """Median and MAD scaled to standard-deviation units.

    Same reasoning as the OI z-score in fno_analysis: volume distributions have
    fat tails (block deals, delivery-driven spikes), and a standard deviation
    is defenceless against them. MAD ignores tails by construction.
    """
    if not values:
        return 0.0, 0.0
    med = _median(values)
    mad = _median([abs(v - med) for v in values])
    return med, mad * 1.4826


def _sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def bollinger_bandwidth(closes, period=20, mult=2.0):
    """(upper - lower) / middle, as a percentage. The standard squeeze metric."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    if mid <= 0:
        return None
    var = sum((c - mid) ** 2 for c in window) / period
    sd = var ** 0.5
    return (2 * mult * sd) / mid * 100


def bandwidth_series(closes, period=20, mult=2.0):
    """Bandwidth at every bar where it can be computed."""
    out = []
    for i in range(period, len(closes) + 1):
        bw = bollinger_bandwidth(closes[:i], period, mult)
        out.append(bw if bw is not None else 0.0)
    return out


# --------------------------------------------------------------------------
# consolidation
# --------------------------------------------------------------------------
def detect_consolidation(candles, max_width_pct=3.2, min_bars=10, max_bars=90):
    """Grow a range backwards from the last bar while it stays tight.

    Returns the longest recent stretch whose high-low span stays within
    `max_width_pct` of its own midpoint. This is what the eye does when it
    draws a box on a chart, and unlike a fixed calendar window it adapts to
    each stock and never depends on what day of the month the scan runs.
    """
    n = len(candles)
    if n < min_bars:
        return None

    best = None
    hi = candles[-1]["high"]
    lo = candles[-1]["low"]
    for back in range(2, min(max_bars, n) + 1):
        c = candles[-back]
        hi = max(hi, c["high"])
        lo = min(lo, c["low"])
        mid = (hi + lo) / 2
        if mid <= 0:
            break
        width = (hi - lo) / mid * 100
        if width > max_width_pct:
            break                      # adding this bar broke the range
        if back >= min_bars:
            best = {"bars": back, "high": hi, "low": lo, "mid": mid, "width_pct": width}

    if not best:
        return None

    seg = candles[-best["bars"]:]
    closes = [c["close"] for c in seg]
    mid = best["mid"]
    # how far the closes actually stray from the midpoint — a range can be
    # technically tight while price ping-pongs between its edges
    devs = [abs(c - mid) / mid * 100 for c in closes]
    best["max_dev_pct"] = round(max(devs), 2)
    best["avg_dev_pct"] = round(sum(devs) / len(devs), 2)
    best["from_date"] = str(seg[0].get("date"))[:16]
    best["last_date"] = str(seg[-1].get("date"))[:16]
    last = candles[-1]["close"]
    best["price"] = last
    best["pos_in_range_pct"] = (round((last - best["low"]) / (best["high"] - best["low"]) * 100)
                                if best["high"] > best["low"] else None)
    best["dist_from_mid_pct"] = round((last - mid) / mid * 100, 2)
    best["high"] = round(best["high"], 2)
    best["low"] = round(best["low"], 2)
    best["mid"] = round(mid, 2)
    best["width_pct"] = round(best["width_pct"], 2)
    return best


def prior_move(candles, cons_bars, lookback=60):
    """The directional move that preceded the range.

    Measured from the extreme of the pre-range window to the range midpoint,
    which is what the measuring tool on a chart reports — not close-to-close,
    because the move's magnitude is what created the implied vol, and that is
    set by the extreme.
    """
    pre = candles[:-cons_bars] if cons_bars < len(candles) else []
    if len(pre) < 10:
        return None
    seg = pre[-lookback:]
    hi = max(c["high"] for c in seg)
    lo = min(c["low"] for c in seg)
    hi_i = max(range(len(seg)), key=lambda i: seg[i]["high"])
    lo_i = min(range(len(seg)), key=lambda i: seg[i]["low"])
    end = candles[-cons_bars]["close"]

    # direction is decided by which extreme came FIRST: a fall is high-then-low
    if hi_i < lo_i:
        direction, start, finish = "down", hi, lo
    else:
        direction, start, finish = "up", lo, hi
    if start <= 0:
        return None
    move = (finish - start) / start * 100
    return {
        "direction": direction,
        "move_pct": round(move, 2),
        "abs_move_pct": round(abs(move), 2),
        "from_price": round(start, 2),
        "to_price": round(finish, 2),
        "bars": len(seg),
        "from_date": str(seg[0].get("date"))[:16],
    }


# --------------------------------------------------------------------------
# volume
# --------------------------------------------------------------------------
def volume_dryup(candles, cons_bars, move_bars=None):
    """Volume during the range against volume during the move that preceded it.

    Reported three ways because they answer different questions:
      ratio      - blunt comparison of the two averages
      median_rat - same on medians, immune to a single block trade
      z          - where the range's volume sits in the move phase's own
                   MAD-scaled distribution. -2 means genuinely unusual quiet.
    """
    if cons_bars >= len(candles):
        return None
    cons = [float(c.get("volume") or 0) for c in candles[-cons_bars:]]
    pre_all = [float(c.get("volume") or 0) for c in candles[:-cons_bars]]
    if not pre_all or not cons:
        return None
    pre = pre_all[-move_bars:] if move_bars else pre_all
    pre = [v for v in pre if v > 0]
    cons = [v for v in cons if v > 0]
    if len(pre) < 5 or len(cons) < 3:
        return None

    a_pre, a_cons = sum(pre) / len(pre), sum(cons) / len(cons)
    m_pre, m_cons = _median(pre), _median(cons)
    med, mad = _robust_scale(pre)
    # Compare MEDIAN to MEDIAN. The previous version scored the consolidation's
    # MEAN against the move phase's MEDIAN, mixing two statistics — one
    # outlier-sensitive, one not — so a single huge bar in the range pushed the
    # z positive while every other measure said volume had drained.
    z = (m_cons - med) / mad if mad > 0.01 else None
    z_mean = (a_cons - med) / mad if mad > 0.01 else None

    return {
        "move_avg": int(a_pre),
        "cons_avg": int(a_cons),
        "ratio": round(a_cons / a_pre, 3) if a_pre else None,
        "contraction_pct": round((1 - a_cons / a_pre) * 100, 1) if a_pre else None,
        "median_ratio": round(m_cons / m_pre, 3) if m_pre else None,
        "median_contraction_pct": round((1 - m_cons / m_pre) * 100, 1) if m_pre else None,
        "z": round(z, 2) if z is not None else None,
        "z_mean": round(z_mean, 2) if z_mean is not None else None,
        # The figure the gate uses. The mean is reported alongside because the
        # gap between them IS the signal: a large divergence means one or two
        # outsized bars, almost always the climax bar that ended the move and
        # sits at the start of the range.
        "gate_pct": round((1 - m_cons / m_pre) * 100, 1) if m_pre else None,
        "mean_median_gap": (round(abs((1 - a_cons / a_pre) * 100 - (1 - m_cons / m_pre) * 100), 1)
                            if a_pre and m_pre else None),
        "move_bars": len(pre),
        "cons_bars": len(cons),
        # the range's own trend: still falling, or already picking back up?
        "trend": _vol_trend(cons),
    }


def _vol_trend(vols):
    """Is volume still declining through the range, or turning back up?"""
    if len(vols) < 6:
        return None
    half = len(vols) // 2
    first, second = vols[:half], vols[half:]
    a, b = sum(first) / len(first), sum(second) / len(second)
    if a <= 0:
        return None
    chg = (b - a) / a * 100
    return {"second_half_vs_first_pct": round(chg, 1),
            "state": "still drying" if chg < -10 else
                     "picking up" if chg > 15 else "flat"}


# --------------------------------------------------------------------------
# squeeze
# --------------------------------------------------------------------------
def squeeze_state(candles, period=20, lookback=100):
    """Bollinger bandwidth now, as a percentile of its own recent history.

    A percentile is used rather than the absolute thresholds in a screener
    definition because absolute bandwidth is not comparable between a 4h and a
    1h chart, nor between a quiet stock and a volatile one. The percentile is
    self-calibrating on both counts.
    """
    closes = [c["close"] for c in candles]
    if len(closes) < period + 10:
        return None
    series = bandwidth_series(closes, period)
    if len(series) < 10:
        return None
    cur = series[-1]
    hist = series[-lookback:] if len(series) > lookback else series
    pct = sum(1 for v in hist if v < cur) / len(hist) * 100

    # consecutive bars spent in the lowest quartile — duration matters, a
    # one-bar dip in bandwidth is noise
    thresh = sorted(hist)[max(0, int(len(hist) * 0.25) - 1)]
    dur = 0
    for v in reversed(series):
        if v <= thresh:
            dur += 1
        else:
            break

    return {
        "bandwidth_pct": round(cur, 2),
        "percentile": round(pct, 1),
        "in_squeeze": pct <= 25,
        "squeeze_bars": dur,
        "median_bandwidth_pct": round(_median(hist), 2),
        "vs_median": round(cur / _median(hist), 2) if _median(hist) else None,
    }


# --------------------------------------------------------------------------
# empirical hit probability
# --------------------------------------------------------------------------
def find_price_breaks(candles, mult=4.0, atr_period=14):
    """Bar indices where price moved impossibly far in one step.

    A 1:1 bonus halves the price overnight. Nobody lost anything, but the raw
    series shows a 50% collapse, and every sampling window touching that date
    records a failure. One corporate action can therefore drag a calm stock's
    base rate toward zero for reasons that have nothing to do with how it
    trades. Splits, bonuses, demergers and genuine gap events all look the
    same here, and all should be excluded from a behavioural base rate.
    """
    if len(candles) < atr_period + 5:
        return set()
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    breaks = set()
    for i in range(atr_period + 1, len(candles)):
        base = sum(trs[i - 1 - atr_period:i - 1]) / atr_period
        if base <= 0:
            continue
        step = abs(candles[i]["close"] - candles[i - 1]["close"])
        if step > mult * base:
            breaks.add(i)
    return breaks


def _in_tight_range(candles, end_idx, bars, max_width_pct):
    """Was the stock range-bound in the `bars` leading up to end_idx?"""
    if end_idx - bars < 0:
        return False
    seg = candles[end_idx - bars + 1:end_idx + 1]
    hi = max(c["high"] for c in seg)
    lo = min(c["low"] for c in seg)
    mid = (hi + lo) / 2
    if mid <= 0:
        return False
    return (hi - lo) / mid * 100 <= max_width_pct


def _vol_dried(candles, end_idx, bars, base_bars=20, min_drop=15.0):
    """Was volume lower over those bars than in the stretch before them?"""
    if end_idx - bars - base_bars < 0:
        return False
    recent = [float(c.get("volume") or 0) for c in candles[end_idx - bars + 1:end_idx + 1]]
    base = [float(c.get("volume") or 0)
            for c in candles[end_idx - bars - base_bars + 1:end_idx - bars + 1]]
    recent = [v for v in recent if v > 0]
    base = [v for v in base if v > 0]
    if len(recent) < 3 or len(base) < 5:
        return False
    mr, mb = _median(recent), _median(base)
    if mb <= 0:
        return False
    return (1 - mr / mb) * 100 >= min_drop


def stay_rate(candles, upper_pct, lower_pct, horizon, conditional=False,
              cond_bars=8, cond_width_pct=6.0, skip_breaks=True):
    """Empirical base rate: would these strikes have held?

    conditional=False  every past bar is a trial. Answers the loose question
                       "does this stock ever sit still for this long", which
                       includes trending and volatile stretches unlike today.

    conditional=True   only bars where the stock was ALSO range-bound on
                       drying volume count as trials. Answers the question
                       that matters — "when it has looked like this before,
                       what followed" — at the cost of a far smaller sample.
                       The count is returned so thin samples are visible
                       rather than hidden behind a confident percentage.

    skip_breaks        excludes windows containing a corporate action or gap.
    """
    """How often price HAS stayed inside a band of this width, historically.

    For every past bar, check whether the following `horizon` bars remained
    within +/- half_width of that bar's close. This is the same species of
    number as the reach rate on the liquidity tab: an empirical base rate from
    this instrument's own history, not a model output.

    It answers "how often does this stock sit still for this long", which is
    the question a condor actually depends on. It does NOT know about the
    current squeeze, implied vol, or any event in the calendar ahead.
    """
    n = len(candles)
    if n < horizon + 30 or upper_pct <= 0 or lower_pct <= 0:
        return None
    breaks = find_price_breaks(candles) if skip_breaks else set()
    hits = trials = skipped_break = skipped_cond = 0

    for i in range(n - horizon - 1):
        ref = candles[i]["close"]
        if ref <= 0:
            continue
        window = range(i + 1, i + 1 + horizon)
        if breaks and any(j in breaks for j in window):
            skipped_break += 1
            continue
        if conditional:
            if not _in_tight_range(candles, i, cond_bars, cond_width_pct):
                skipped_cond += 1
                continue
            if not _vol_dried(candles, i, cond_bars):
                skipped_cond += 1
                continue
        hi = max(c["high"] for c in candles[i + 1:i + 1 + horizon])
        lo = min(c["low"] for c in candles[i + 1:i + 1 + horizon])
        trials += 1
        if (hi - ref) / ref * 100 <= upper_pct and (ref - lo) / ref * 100 <= lower_pct:
            hits += 1

    min_trials = 8 if conditional else 30
    if trials < min_trials:
        return {"rate_pct": None, "samples": trials, "conditional": conditional,
                "too_thin": True, "breaks_excluded": len(breaks),
                "note": (f"only {trials} comparable periods found"
                         + (" — this stock has rarely looked like this before"
                            if conditional else " — not enough history"))}
    return {"rate_pct": round(hits / trials * 100, 1), "samples": trials,
            "conditional": conditional,
            "too_thin": trials < (20 if conditional else 60),
            "horizon_bars": horizon,
            "breaks_excluded": len(breaks),
            "skipped_for_breaks": skipped_break,
            "skipped_not_comparable": skipped_cond,
            "upper_pct": round(upper_pct, 2), "lower_pct": round(lower_pct, 2),
            "band_pct": round(upper_pct + lower_pct, 2)}


# --------------------------------------------------------------------------
# early detection — the range while it is still forming
# --------------------------------------------------------------------------
def _true_ranges(candles):
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def detect_forming(candles, recent=5, base=20):
    """Compression signals that appear BEFORE a range is long enough to qualify.

    The mature scan needs ~10 bars of range, which is roughly five sessions —
    by then implied volatility has already deflated and much of the premium
    that made the setup worth writing is gone. These five measures fire
    earlier, in the order they typically appear in real compressions:

      1. VOLUME COLLAPSE. Almost always first. Participation leaves before
         price stops moving.
      2. RANGE COMPRESSION. ATR over the recent bars against its own baseline.
      3. NARROW BARS. Count of recent bars whose true range is well under the
         baseline — the small-candle cluster.
      4. INSIDE BARS. Each contained by its predecessor. A cluster of these is
         compression that has become mechanical.
      5. BANDWIDTH FALLING. Bollinger bandwidth declining, even if it has not
         yet reached a low percentile.

    None of these is a range yet. They say a range may be starting, which is a
    weaker and earlier claim — and the point is that it is earlier.
    """
    n = len(candles)
    if n < base + recent + 2:
        return None

    trs = _true_ranges(candles)
    if len(trs) < base + recent:
        return None

    atr_recent = sum(trs[-recent:]) / recent
    atr_base = sum(trs[-(base + recent):-recent]) / base
    if atr_base <= 0:
        return None
    range_ratio = atr_recent / atr_base

    vols = [float(c.get("volume") or 0) for c in candles]
    v_recent = [v for v in vols[-recent:] if v > 0]
    v_base = [v for v in vols[-(base + recent):-recent] if v > 0]
    if len(v_recent) < 2 or len(v_base) < 5:
        return None
    a_rec = sum(v_recent) / len(v_recent)
    med, mad = _robust_scale(v_base)
    vol_z = (a_rec - med) / mad if mad > 0.01 else None
    vol_ratio = a_rec / (sum(v_base) / len(v_base))

    # narrow bars: true range under 70% of the baseline average
    look = min(8, len(trs))
    narrow = sum(1 for t in trs[-look:] if t < atr_base * 0.7)

    # inside bars: high and low both contained by the previous bar
    inside = 0
    for i in range(max(1, n - look), n):
        if candles[i]["high"] <= candles[i - 1]["high"] and candles[i]["low"] >= candles[i - 1]["low"]:
            inside += 1

    closes = [c["close"] for c in candles]
    bws = bandwidth_series(closes, 20)
    bw_slope = None
    if len(bws) >= 6 and bws[-6] > 0:
        bw_slope = (bws[-1] - bws[-6]) / bws[-6] * 100

    # how tight is the run so far, at ANY length (no minimum imposed)
    hi = lo = None
    run = 0
    for back in range(1, min(30, n) + 1):
        c = candles[-back]
        hi = c["high"] if hi is None else max(hi, c["high"])
        lo = c["low"] if lo is None else min(lo, c["low"])
        mid = (hi + lo) / 2
        if mid <= 0 or (hi - lo) / mid * 100 > 3.2:
            break
        run = back

    return {
        "range_ratio": round(range_ratio, 2),
        "vol_ratio": round(vol_ratio, 2),
        "vol_drop_pct": round((1 - vol_ratio) * 100, 1),
        "vol_z": round(vol_z, 2) if vol_z is not None else None,
        "narrow_bars": narrow,
        "inside_bars": inside,
        "bw_slope_pct": round(bw_slope, 1) if bw_slope is not None else None,
        "tight_run_bars": run,
        "bars_measured": recent,
    }


def readiness(fm, move, cons, vol, cfg=None):
    """Which of the four gates already pass, and by how much the rest miss.

    The scan otherwise reports a single reason for rejection, which tells you
    nothing about how close a stock is. This shows all four at once so a
    watchlist entry can be read as "three of four, waiting on volume" rather
    than an opaque skip.
    """
    cfg = cfg or {}
    max_w = cfg.get("max_width_pct", 3.2)
    min_m = cfg.get("min_move_pct", 10.0)
    min_c = cfg.get("min_contraction_pct", 20.0)
    min_b = cfg.get("min_cons_bars", 10)

    run = (cons or {}).get("bars") or (fm or {}).get("tight_run_bars") or 0
    width = (cons or {}).get("width_pct")
    if width is None and fm:
        width = None
    mv = (move or {}).get("abs_move_pct") or 0
    contr = (vol or {}).get("gate_pct")
    if contr is None:
        contr = (vol or {}).get("contraction_pct")
    if contr is None and fm:
        contr = fm.get("vol_drop_pct")

    checks = [
        {"gate": "Prior move", "pass": mv >= min_m,
         "value": f"{mv:.1f}%", "needs": f"≥{min_m}%",
         "gap": round(min_m - mv, 1) if mv < min_m else 0},
        {"gate": "Range length", "pass": run >= min_b,
         "value": f"{run} bars", "needs": f"≥{min_b} bars",
         "gap": max(0, min_b - run)},
        {"gate": "Range width", "pass": bool(width is not None and width <= max_w),
         "value": f"{width}%" if width is not None else "no range yet",
         "needs": f"≤{max_w}%",
         "gap": round(width - max_w, 2) if width is not None and width > max_w else 0},
        {"gate": "Volume dry-up", "pass": bool(contr is not None and contr >= min_c),
         "value": f"{contr:.1f}%" if contr is not None else "—",
         "needs": f"≥{min_c}%",
         "gap": round(min_c - contr, 1) if contr is not None and contr < min_c else 0},
    ]
    passed = sum(1 for c in checks if c["pass"])
    missing = [c["gate"] for c in checks if not c["pass"]]
    return {"checks": checks, "passed": passed, "total": len(checks),
            "missing": missing,
            "summary": (f"{passed} of {len(checks)} conditions met"
                        + (f" — waiting on {', '.join(missing).lower()}" if missing else ""))}


def score_forming(fm, move):
    """0-100 earliness score. Same honesty status as the main score: the
    weights are judgement, not measurement.

    Volume carries the most weight because it moves first and because a
    compression without it is usually a pause inside a trend rather than the
    end of one.
    """
    if not fm:
        return None
    total = 0.0
    pts = []

    z = fm.get("vol_z")
    v = 0.0
    if z is not None:
        if z <= -2.0:   v = 35.0
        elif z <= -1.5: v = 29.0
        elif z <= -1.0: v = 22.0
        elif z <= -0.5: v = 13.0
    total += v; pts.append(("Volume collapse", round(v, 1), 35))

    rr = fm["range_ratio"]
    v = 25.0 if rr <= 0.5 else 20.0 if rr <= 0.65 else 13.0 if rr <= 0.8 else 5.0 if rr <= 0.95 else 0.0
    total += v; pts.append(("Range compression", round(v, 1), 25))

    nb, ib = fm["narrow_bars"], fm["inside_bars"]
    v = min(20.0, nb * 2.5 + ib * 3.0)
    total += v; pts.append(("Narrow / inside bars", round(v, 1), 20))

    # Bandwidth EXPANDING contradicts the whole premise, so it scores negative
    # rather than merely zero. Without this a stock that is decompressing can
    # still top the list on its other signals — which it did in testing.
    s = fm.get("bw_slope_pct")
    if s is None:   v = 0.0
    elif s <= -30:  v = 10.0
    elif s <= -15:  v = 7.0
    elif s < 0:     v = 3.0
    elif s <= 10:   v = 0.0
    else:           v = -8.0        # genuinely widening: penalise
    total += v; pts.append(("Bandwidth falling", round(v, 1), 10))

    m = (move or {}).get("abs_move_pct") or 0
    v = 10.0 if m >= 15 else 7.0 if m >= 10 else 3.0 if m >= 7 else 0.0
    total += v; pts.append(("Prior move", round(v, 1), 10))

    return {"score": round(min(100.0, total)), "breakdown": pts}


# ==========================================================================
# OPTION FIT — practitioner rules that the range test alone does not cover
#
# A perfect consolidation can still be a poor condor. The structure tells you
# price is quiet; it says nothing about whether the premium is worth taking,
# whether the strikes sit far enough out, whether there is time for the trade
# to work, or whether results are due before expiry. Those are separate
# questions and they get a separate score.
#
# The rules implemented here come from experienced practice, not from theory:
#   - short strikes at 15-20 delta
#   - IV rank above ~30 before the premium is interesting
#   - 30-50 days to expiry, monthly series
#   - nothing written into an earnings date
#
# TWO HONEST SUBSTITUTIONS, both stated on the page:
#   1. IV rank is approximated by REALISED volatility rank. True IV rank needs
#      a stored history of implied vol, which this app does not keep. Realised
#      vol answers a related but different question: how much the stock has
#      actually been moving, versus how much the option market expects it to.
#   2. Delta is approximated from realised vol rather than read from a chain.
#      A 16-delta strike sits about one standard deviation out, because ~16% of
#      a normal distribution lies beyond 1 sd. Good enough to rank strike
#      placement; not a substitute for the broker's own greeks at order time.
# ==========================================================================

# z-scores for the deltas practitioners actually quote, so no inverse-normal
# routine is needed: 16 delta is ~1.0 sd out, 20 delta ~0.84, 30 delta ~0.52
_DELTA_Z = {10: 1.2816, 16: 0.9945, 20: 0.8416, 25: 0.6745, 30: 0.5244}


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def realized_vol(closes, period=20, trim_outliers=True):
    """Annualised realised volatility, in percent.

    One outsized return wrecks this measure — a 1:1 bonus halving the price
    produces a -69% log return that alone can push a 20-day vol estimate past
    150%, which then corrupts every delta and strike derived from it. Returns
    beyond 5x the median absolute return are dropped, which removes splits and
    limit moves without touching ordinary volatility.
    """
    if len(closes) < period + 1:
        return None
    rets = []
    for i in range(len(closes) - period, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 5:
        return None
    if trim_outliers and len(rets) >= 8:
        med_abs = _median([abs(r) for r in rets])
        if med_abs > 0:
            kept = [r for r in rets if abs(r) <= 5 * med_abs]
            if len(kept) >= max(5, len(rets) - 3):
                rets = kept
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


def vol_rank(closes, period=20, lookback=252):
    """Where current realised vol sits in its own past year — the analogue of
    IV rank. Above ~30 is where practitioners consider premium worth taking."""
    if len(closes) < period + lookback // 4:
        return None
    series = []
    for end in range(period + 1, len(closes) + 1):
        v = realized_vol(closes[:end], period)
        if v is not None:
            series.append(v)
    if len(series) < 30:
        return None
    hist = series[-lookback:]
    cur = series[-1]
    lo, hi = min(hist), max(hist)
    rank = (cur - lo) / (hi - lo) * 100 if hi > lo else None      # IV-rank style
    pct = sum(1 for v in hist if v < cur) / len(hist) * 100        # percentile style
    return {"current_vol_pct": round(cur, 1),
            "rank": round(rank, 1) if rank is not None else None,
            "percentile": round(pct, 1),
            "year_low": round(lo, 1), "year_high": round(hi, 1),
            "samples": len(hist)}


def strike_delta_approx(spot, strike, hv_annual_pct, days):
    """Approximate |delta| as the probability of finishing beyond the strike."""
    if not spot or not hv_annual_pct or days <= 0 or spot <= 0 or strike <= 0:
        return None
    sd = (hv_annual_pct / 100.0) * math.sqrt(days / 365.0)
    if sd <= 0:
        return None
    z = math.log(strike / spot) / sd
    p = (1 - _norm_cdf(z)) if strike > spot else _norm_cdf(z)
    return round(p * 100, 1)


def delta_strikes(spot, hv_annual_pct, days, deltas=(16, 20, 30)):
    """Strike prices sitting at the requested deltas, both sides."""
    if not spot or not hv_annual_pct or days <= 0:
        return None
    sd = (hv_annual_pct / 100.0) * math.sqrt(days / 365.0)
    out = {}
    for d in deltas:
        z = _DELTA_Z.get(d)
        if z is None:
            continue
        out[f"{d}d"] = {"call": round(spot * math.exp(z * sd), 2),
                        "put": round(spot * math.exp(-z * sd), 2)}
    out["expected_move_pct"] = round(sd * 100, 2)
    return out


def dte_fit(dte):
    """30-50 days is the practitioner window: enough time for a move to
    reverse, and monthly series carry the better liquidity. Under ~21 days
    gamma risk rises sharply — a small move late in the cycle does far more
    damage to a short strike than the same move with a month to run."""
    if dte is None:
        return None
    if 30 <= dte <= 50:  state, pts = "ideal", 100
    elif 21 <= dte < 30: state, pts = "workable, gamma rising", 65
    elif 50 < dte <= 70: state, pts = "far out, slow decay", 60
    elif 14 <= dte < 21: state, pts = "too close — gamma risk", 25
    elif dte < 14:       state, pts = "far too close", 0
    else:                state, pts = "very far out", 35
    return {"dte": dte, "state": state, "fit": pts}


def option_context(cons, spot, daily_closes, dte, results_date=None,
                   results_in_days=None):
    """Everything the structural scan cannot see: premium richness, strike
    placement in delta terms, time to expiry, and event risk."""
    hv = realized_vol(daily_closes)
    vr = vol_rank(daily_closes)
    ks = delta_strikes(spot, hv, dte) if (hv and dte) else None

    # What delta are the range edges? This is the question that matters: a
    # condor written at the edges of a very tight range can sit at 30+ delta,
    # which is far closer to the money than the 15-20 the framework calls for.
    edge_call = edge_put = None
    if hv and dte and cons:
        edge_call = strike_delta_approx(spot, cons["high"] * 1.01, hv, dte)
        edge_put = strike_delta_approx(spot, cons["low"] * 0.99, hv, dte)

    event = None
    if results_date:
        event = {"results_date": results_date, "days_until": results_in_days,
                 "before_expiry": (results_in_days is not None and dte is not None
                                   and results_in_days <= dte)}

    return {
        "hv_pct": round(hv, 1) if hv else None,
        "vol_rank": vr,
        "dte": dte_fit(dte),
        "delta_strikes": ks,
        "range_edge_delta": {"call": edge_call, "put": edge_put},
        "event": event,
        "caveats": [
            "IV rank is approximated by REALISED volatility rank — this app "
            "stores no implied-vol history. It measures how much the stock has "
            "moved, not what the option market is charging.",
            "Delta is approximated from realised volatility, not read from a "
            "live chain. Use the broker's greeks before placing the order.",
        ],
    }


def score_option_fit(oc):
    """0-100, separate from the structural score. Hand-assigned weights."""
    if not oc:
        return None
    total = 0.0
    pts = []

    # premium richness (30)
    vr = (oc.get("vol_rank") or {}).get("rank")
    if vr is None:  v = 0.0
    elif vr >= 50:  v = 30.0
    elif vr >= 30:  v = 24.0
    elif vr >= 20:  v = 14.0
    elif vr >= 10:  v = 7.0
    else:           v = 0.0
    total += v; pts.append(("Premium richness (vol rank)", round(v, 1), 30))

    # strike placement (30) — range edges should sit at 20 delta or further out
    d = oc.get("range_edge_delta") or {}
    worst = max([x for x in (d.get("call"), d.get("put")) if x is not None], default=None)
    if worst is None: v = 0.0
    elif worst <= 16: v = 30.0
    elif worst <= 20: v = 25.0
    elif worst <= 25: v = 15.0
    elif worst <= 30: v = 7.0
    else:             v = 0.0
    total += v; pts.append(("Strike placement (delta)", round(v, 1), 30))

    # time to expiry (25)
    fit = (oc.get("dte") or {}).get("fit")
    v = (fit or 0) / 100.0 * 25.0
    total += v; pts.append(("Days to expiry", round(v, 1), 25))

    # event risk (15) — results before expiry is the classic condor killer
    ev = oc.get("event")
    if ev is None:                 v = 15.0
    elif ev.get("before_expiry"):  v = 0.0
    else:                          v = 15.0
    total += v; pts.append(("No event before expiry", round(v, 1), 15))

    return {"score": round(min(100.0, total)), "breakdown": pts}


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
def add_stay_rate(result, daily_candles, horizon_days=10):
    """Attach the empirical base rate, computed on LONG daily history.

    Answers the only question the trade actually poses: starting from any past
    day, would price have stayed between these two strikes for the holding
    period? Daily bars are used because the horizon is measured in sessions and
    because a year or more of them spans quiet regimes as well as trending
    ones — which an 80-day intraday window does not.
    """
    if not result or not result.get("qualified") or not daily_candles:
        return result
    k = result["suggested_strikes"]
    up, dn = k["upper_room_pct"], k["lower_room_pct"]

    # Both are reported. The conditional rate is the relevant one; the
    # unconditional rate is the fallback when too few comparable periods exist,
    # and the gap between them is itself informative.
    loose = stay_rate(daily_candles, up, dn, horizon_days, conditional=False)
    cond = stay_rate(daily_candles, up, dn, horizon_days, conditional=True)
    for r in (loose, cond):
        if r:
            r["basis"] = "daily bars"
            r["horizon_sessions"] = horizon_days
    result["stay_rate"] = cond if (cond and cond.get("rate_pct") is not None) else loose
    result["stay_rate_conditional"] = cond
    result["stay_rate_all"] = loose
    return result


def score_setup(move, cons, vol, sq4, sq1, stay):
    """0-100 suitability score. Weights are hand-assigned judgement.

    Nothing here is backtested. The weights encode a view about what matters
    for a range holding — volume dry-up above everything else, because a range
    on rising volume is a coil rather than a rest — and that view has not been
    measured. The empirical stay rate beside it is the only figure in this
    module derived from data rather than opinion.
    """
    pts = []
    total = 0.0

    # volume dry-up — the filter that is never relaxed (35)
    c = (vol or {}).get("gate_pct")
    if c is None:
        c = (vol or {}).get("contraction_pct")
    if c is None:
        v = 0.0
    elif c >= 60: v = 35.0
    elif c >= 45: v = 29.0
    elif c >= 30: v = 22.0
    elif c >= 20: v = 14.0
    elif c >= 10: v = 7.0
    else:         v = 0.0
    if (vol or {}).get("trend", {}) and vol["trend"].get("state") == "picking up":
        v *= 0.5          # volume returning undermines the whole premise
    total += v
    pts.append(("Volume dry-up", round(v, 1), 35))

    # range tightness (20) — calibrated on the supplied examples at ~3.05%
    w = (cons or {}).get("width_pct")
    if w is None:  v = 0.0
    elif w <= 2.0: v = 20.0
    elif w <= 3.2: v = 16.0
    elif w <= 4.5: v = 10.0
    elif w <= 6.0: v = 5.0
    else:          v = 0.0
    total += v
    pts.append(("Range tightness", round(v, 1), 20))

    # squeeze on 4h, confirmed on 1h (15)
    v = 0.0
    if sq4:
        if sq4["percentile"] <= 10:   v = 10.0
        elif sq4["percentile"] <= 25: v = 7.0
        elif sq4["percentile"] <= 40: v = 3.0
        if sq4.get("squeeze_bars", 0) >= 10:
            v += 2.0
    if sq1 and sq1["percentile"] <= 25:
        v += 3.0
    total += min(15.0, v)
    pts.append(("Squeeze (4h + 1h)", round(min(15.0, v), 1), 15))

    # prior move — no move, no premium worth collecting (15)
    m = (move or {}).get("abs_move_pct")
    if m is None:  v = 0.0
    elif m >= 20:  v = 15.0
    elif m >= 15:  v = 13.0
    elif m >= 10:  v = 10.0
    elif m >= 7:   v = 5.0
    else:          v = 0.0
    total += v
    pts.append(("Prior move", round(v, 1), 15))

    # duration — a range needs to have proved itself (8)
    b = (cons or {}).get("bars") or 0
    v = 8.0 if b >= 30 else 6.0 if b >= 20 else 4.0 if b >= 14 else 2.0 if b >= 10 else 0.0
    total += v
    pts.append(("Range duration", round(v, 1), 8))

    # price sitting near the middle — a condor written around a price already
    # pressed against one edge is asymmetric from the start (7)
    d = abs((cons or {}).get("dist_from_mid_pct") or 99)
    v = 7.0 if d <= 0.5 else 5.0 if d <= 1.0 else 3.0 if d <= 1.5 else 0.0
    total += v
    pts.append(("Price near mid", round(v, 1), 7))

    return {"score": round(min(100.0, total)), "breakdown": pts}


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def analyse(c4h, c1h, cfg=None):
    """Full assessment for one symbol. Returns None when it does not qualify."""
    cfg = cfg or {}
    max_width   = cfg.get("max_width_pct", 3.2)
    min_move    = cfg.get("min_move_pct", 10.0)
    min_contract = cfg.get("min_contraction_pct", 20.0)
    min_bars    = cfg.get("min_cons_bars", 10)
    horizon     = cfg.get("horizon_bars", 20)

    if not c4h or len(c4h) < 60:
        return {"skip": "insufficient 4h history"}

    cons = detect_consolidation(c4h, max_width, min_bars)

    if not cons:
        # Not a qualified range — but it may be one FORMING. Report the
        # compression signals so an early watchlist is possible; they fire
        # days before the range is long enough to qualify.
        fm = detect_forming(c4h)
        mv = prior_move(c4h, max(1, (fm or {}).get("tight_run_bars", 1)))
        sf = score_forming(fm, mv)
        if fm and sf and sf["score"] >= 45 and mv and mv["abs_move_pct"] >= min_move * 0.7:
            return {"skip": "no tight range on 4h", "forming": True,
                    "early": fm, "early_score": sf["score"],
                    "early_breakdown": sf["breakdown"], "move": mv,
                    "waiting_on": "range",
                    "readiness": readiness(fm, mv, None, None, cfg),
                    "price": round(c4h[-1]["close"], 2)}
        return {"skip": "no tight range on 4h"}

    move = prior_move(c4h, cons["bars"])
    if not move or move["abs_move_pct"] < min_move:
        return {"skip": f"prior move {move['abs_move_pct'] if move else 0}% below {min_move}%"}

    vol = volume_dryup(c4h, cons["bars"], move_bars=move["bars"])
    if not vol or vol.get("contraction_pct") is None:
        return {"skip": "volume unavailable"}
    gate_val = vol.get("gate_pct")
    if gate_val is None:
        gate_val = vol["contraction_pct"]
    if gate_val < min_contract:
        # A range exists and the move is there — only the volume has not yet
        # drained. This is the most actionable early case of all: everything is
        # in place except the one condition that is never relaxed, so it is
        # worth watching for exactly that condition to arrive.
        fm = detect_forming(c4h)
        sf = score_forming(fm, move)
        if fm and sf and sf["score"] >= 40:
            return {"skip": f"volume contraction {gate_val}% below {min_contract}%",
                    "forming": True, "early": fm, "early_score": sf["score"],
                    "early_breakdown": sf["breakdown"], "move": move,
                    "consolidation": cons, "volume": vol,
                    "waiting_on": "volume",
                    "readiness": readiness(fm, move, cons, vol, cfg),
                    "price": round(c4h[-1]["close"], 2)}
        return {"skip": f"volume contraction {gate_val}% below {min_contract}%"}

    sq4 = squeeze_state(c4h)

    # 1h confirmation: same shape, proportionally more bars (about 4x)
    cons1 = sq1 = None
    if c1h and len(c1h) >= 80:
        cons1 = detect_consolidation(c1h, max_width, min_bars * 3, max_bars=360)
        sq1 = squeeze_state(c1h)

    fm = detect_forming(c4h)

    # The band that actually matters is the one between the short strikes, not
    # the raw range. Testing +/- half the range width asked a far harder
    # question than the trade poses and returned 0% on every stock, which is
    # no discrimination at all.
    last_px = c4h[-1]["close"]
    put_k = cons["low"] * 0.99
    call_k = cons["high"] * 1.01
    up_pct = (call_k - last_px) / last_px * 100
    dn_pct = (last_px - put_k) / last_px * 100

    # The base rate is NOT computed here. An 80-day 4h window holds ~114 bars,
    # nearly all of them inside the very move that preceded the range, so every
    # sample is drawn from a trending regime and the rate reads 0% for every
    # stock — no discrimination at all. add_stay_rate() computes it from long
    # daily history instead, and only for stocks that have already qualified,
    # so it costs one extra call on a handful of names rather than all 210.
    stay = None
    sc = score_setup(move, cons, vol, sq4, sq1, stay)

    return {
        "qualified": True,
        "move": move,
        "consolidation": cons,
        "consolidation_1h": cons1,
        "confirmed_1h": bool(cons1),
        "volume": vol,
        "squeeze_4h": sq4,
        "squeeze_1h": sq1,
        "stay_rate": stay,
        "early": fm,
        "score": sc["score"],
        "breakdown": sc["breakdown"],
        "suggested_strikes": {
            "short_call_above": round(call_k, 2),
            "short_put_below": round(put_k, 2),
            "upper_room_pct": round(up_pct, 2),
            "lower_room_pct": round(dn_pct, 2),
            "note": ("Range edges plus a 1% buffer — a starting point for strike "
                     "selection, not a recommendation. Real strike choice depends "
                     "on premium, liquidity and margin, none of which are read here."),
        },
    }
