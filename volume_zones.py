"""
Volume-weighted support and resistance zones.

Copyright (c) 2026 Amans2bmm. All rights reserved.
Part of the Market Analytics Dashboard. Unauthorised copying, distribution,
or derivative use without express written permission is prohibited.

APPROACH

Levels are built from confirmed swing pivots and ranked by the volume that
transacted at them, rather than by how many times a line has been touched. The
design follows six principles, each of which changes the output materially:

  1. VOLUME-WEIGHTED. A level's strength comes from the volume behind it. Two
     pivots at the same price are not equal if one formed on five times the
     turnover of the other.

  2. ACCUMULATING. Every re-test adds its volume to the zone, and nearby pivots
     merge into it rather than creating a competing level. A zone defended four
     times therefore reads stronger than one touched once, without any explicit
     touch-count rule doing the work.

  3. ZONES, NOT LINES. Width is set by ATR, so a zone on a volatile instrument
     is proportionally wider than one on a quiet instrument. Price rarely turns
     at an exact number, and a fixed-width band would be too tight on one stock
     and meaninglessly wide on another. The centre is a volume-weighted average
     of everything merged into it, so the zone drifts toward where volume
     actually sits as more is added.

  4. NON-REPAINTING. A pivot is only used once there are enough bars after it to
     confirm it. Levels never move retrospectively, which is what makes them
     usable — a level that reappears in hindsight cannot be traded.

  5. ROLE FLIPS. Support that price closes decisively below becomes resistance,
     and vice versa. The level does not vanish; the market's relationship to it
     inverts.

  6. WICKS STRENGTHEN, CLOSES INVALIDATE. A wick through a zone that reverses is
     evidence the level held under pressure and adds to its strength. Only a
     decisive CLOSE beyond it counts as a break. This distinction is the whole
     difference between a level being tested and a level failing.
"""


def _atr(candles, period=14):
    """Average true range, used to normalise zone width across instruments."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    # Wilder smoothing
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _rolling_atr(candles, period=14):
    """ATR at each bar, so a zone's width reflects volatility when it formed."""
    out = [None] * len(candles)
    if len(candles) < period + 1:
        return out
    trs = [0.0]
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[1:period + 1]) / period
    out[period] = atr
    for i in range(period + 1, len(candles)):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def find_confirmed_pivots(candles, left=3, right=3):
    """
    Swing pivots with `right` bars of confirmation after them.

    The confirmation requirement is what makes this non-repainting: a pivot is
    only emitted once enough later bars exist to prove it was a turning point.
    Nothing appears retrospectively.
    """
    pivots = []
    n = len(candles)
    for i in range(left, n - right):
        window = range(i - left, i + right + 1)
        hi, lo = candles[i]["high"], candles[i]["low"]
        if all(candles[j]["high"] <= hi for j in window if j != i):
            pivots.append({"index": i, "price": hi, "kind": "high",
                           "volume": float(candles[i].get("volume") or 0),
                           "date": candles[i].get("date")})
        if all(candles[j]["low"] >= lo for j in window if j != i):
            pivots.append({"index": i, "price": lo, "kind": "low",
                           "volume": float(candles[i].get("volume") or 0),
                           "date": candles[i].get("date")})
    return sorted(pivots, key=lambda p: p["index"])


def build_volume_zones(candles, left=3, right=3, atr_period=14,
                       width_atr=0.55, break_atr=0.35, merge_atr=0.75):
    """
    Build, merge, test and invalidate volume-weighted zones over the series.

    Walks forward in time so every zone's state at the end reflects only
    information available as the bars arrived — no hindsight.

    width_atr : half-width of a zone, in ATR at formation
    break_atr : how far beyond the edge a CLOSE must sit to count as decisive
    merge_atr : distance within which a new pivot merges into an existing zone
    """
    n = len(candles)
    if n < atr_period + left + right + 5:
        return []

    atr_series = _rolling_atr(candles, atr_period)
    pivots = find_confirmed_pivots(candles, left, right)
    by_confirm = {}
    for p in pivots:
        # available only once `right` further bars exist
        by_confirm.setdefault(p["index"] + right, []).append(p)

    zones = []
    avg_vol = (sum(float(c.get("volume") or 0) for c in candles) / n) or 1.0

    for i in range(n):
        atr = atr_series[i] or _atr(candles[:i + 1], atr_period)
        if not atr or atr <= 0:
            continue
        bar = candles[i]
        close, high, low = bar["close"], bar["high"], bar["low"]
        vol = float(bar.get("volume") or 0)

        # ---- 1. admit newly confirmed pivots, merging where they overlap ----
        for p in by_confirm.get(i, []):
            merged = False
            for z in zones:
                if z["dead"]:
                    continue
                if abs(p["price"] - z["center"]) <= merge_atr * atr:
                    # accumulate: volume-weighted centre drifts toward the
                    # price where volume actually sits
                    tot = z["volume"] + p["volume"]
                    if tot > 0:
                        z["center"] = (z["center"] * z["volume"]
                                       + p["price"] * p["volume"]) / tot
                    z["volume"] = tot
                    z["pivots"] += 1
                    z["last_index"] = i
                    z["half"] = max(z["half"], width_atr * atr)
                    merged = True
                    break
            if not merged:
                zones.append({
                    "center": p["price"],
                    "half": width_atr * atr,
                    "volume": p["volume"],
                    "pivots": 1,
                    "origin": p["kind"],
                    "role": "resistance" if p["kind"] == "high" else "support",
                    "created_index": i,
                    "created_date": p.get("date"),
                    "last_index": i,
                    "touches": 0,
                    "wick_rejections": 0,
                    "flips": 0,
                    "dead": False,
                    "atr_at_birth": atr,
                })

        # ---- 2. interaction with live zones ----
        for z in zones:
            if z["dead"] or i <= z["created_index"]:
                continue
            top, bot = z["center"] + z["half"], z["center"] - z["half"]

            touched = (low <= top and high >= bot)
            if not touched:
                continue

            closed_above = close > top + break_atr * atr
            closed_below = close < bot - break_atr * atr

            if closed_above or closed_below:
                # A decisive close is a break. The level does not disappear —
                # its role inverts. A second break after flipping means the
                # market has stopped respecting it and the zone is retired.
                new_role = "support" if closed_above else "resistance"
                if z["role"] != new_role:
                    z["flips"] += 1
                    z["role"] = new_role
                    z["volume"] += vol * 0.5      # the break itself carries weight
                    z["last_index"] = i
                    if z["flips"] >= 2:
                        z["dead"] = True
                        z["died_index"] = i
                else:
                    z["dead"] = True
                    z["died_index"] = i
            else:
                # Held. A wick through that closed back inside is the strongest
                # form of defence and is weighted accordingly.
                pierced = (low < bot) or (high > top)
                z["touches"] += 1
                z["volume"] += vol * (1.0 if pierced else 0.6)
                if pierced:
                    z["wick_rejections"] += 1
                z["last_index"] = i

    # ---- 3. score and present ----
    live = [z for z in zones if not z["dead"]]
    if not live:
        return []

    last_close = candles[-1]["close"]
    max_vol = max(z["volume"] for z in live) or 1.0
    final_atr = atr_series[-1] or _atr(candles, atr_period) or 1.0

    out = []
    for z in live:
        top, bot = z["center"] + z["half"], z["center"] - z["half"]
        recency = max(0.0, 1.0 - (len(candles) - 1 - z["last_index"]) / max(len(candles), 1))
        vol_share = z["volume"] / max_vol

        # Strength: volume dominates, defences and wick rejections add, and a
        # zone untouched for a long stretch decays rather than counting forever.
        strength = (vol_share * 55
                    + min(z["touches"], 6) * 4
                    + min(z["wick_rejections"], 4) * 5
                    + min(z["pivots"] - 1, 4) * 3
                    + recency * 12)
        if z["flips"]:
            strength += 6          # a level that flipped and still holds is real

        dist_pct = (z["center"] - last_close) / last_close * 100
        out.append({
            "center": round(z["center"], 2),
            "low": round(bot, 2),
            "high": round(top, 2),
            "width_pct": round((top - bot) / z["center"] * 100, 2),
            "role": z["role"],
            "origin": z["origin"],
            "volume": int(z["volume"]),
            "vol_x_avg": round(z["volume"] / avg_vol, 2),
            "vol_share_pct": round(vol_share * 100, 1),
            "pivots": z["pivots"],
            "touches": z["touches"],
            "wick_rejections": z["wick_rejections"],
            "flips": z["flips"],
            "bars_since_test": len(candles) - 1 - z["last_index"],
            "created_date": str(z.get("created_date"))[:16] if z.get("created_date") else None,
            "dist_pct": round(dist_pct, 2),
            # A zone containing the current price is neither above nor below it.
            # Sorting by centre alone put a zone price was sitting inside on the
            # wrong side of the price line, which reads as a bug even though the
            # role label was correct.
            "position": ("inside" if bot <= last_close <= top
                         else ("above" if z["center"] > last_close else "below")),
            "price_in_zone": bool(bot <= last_close <= top),
            "pct_into_zone": (round((last_close - bot) / (top - bot) * 100)
                              if bot <= last_close <= top and top > bot else None),
            # how far a close must travel to flip the role
            "flip_level": round(top + 0.35 * final_atr, 2) if z["role"] == "resistance"
                          else round(bot - 0.35 * final_atr, 2),
            "strength": round(min(100, strength)),
            "atr_width": round(z["half"] * 2 / final_atr, 2),
        })

    out.sort(key=lambda z: -z["strength"])
    return out


def summarise(zones, last_close):
    """Nearest meaningful zone either side, plus what the set looks like overall."""
    if not zones:
        return None
    above = sorted([z for z in zones if z["position"] == "above"],
                   key=lambda z: z["dist_pct"])
    below = sorted([z for z in zones if z["position"] == "below"],
                   key=lambda z: -z["dist_pct"])
    inside = [z for z in zones if z["position"] == "inside"]

    def pick(side):
        if not side:
            return None
        # nearest zone carrying real weight, else simply the nearest
        strong = [z for z in side if z["strength"] >= 45]
        return (strong or side)[0]

    return {
        "inside": sorted(inside, key=lambda z: -z["strength"])[:2],
        "nearest_above": pick(above),
        "nearest_below": pick(below),
        "count": len(zones),
        "strong_count": sum(1 for z in zones if z["strength"] >= 60),
        "flipped_count": sum(1 for z in zones if z["flips"]),
        "avg_touches": round(sum(z["touches"] for z in zones) / len(zones), 1),
    }


# ===========================================================================
# FLOW ENRICHMENT — added 16 Aug 2026
#
# Candle-computable pieces of the volumetric-liquidity approach: sweep-and-
# reclaim confirmation, a CVD *proxy* divergence, anchored-VWAP band
# confluence, and volume-node (POC/HVN) confluence.
#
# Everything in this section is ADDITIVE. build_volume_zones() and
# summarise() above are untouched; zone geometry, breaks, flips and the
# strength score are exactly what they were. Enrichment annotates the
# finished zones and returns a chart-level context block.
#
# HONESTY NOTES — do not soften these when presenting:
#   - cvd_proxy is intrabar close-location pressure, NOT tick-level CVD.
#     Passive absorption and aggressive selling are indistinguishable in
#     candles. The field is named _proxy for that reason.
#   - Historical order-book depth ("liquidity bands") does not exist on any
#     public Indian feed. Live depth is on the Execution tab; the historical
#     version is not obtainable and is not imitated here.
#   - flow_score weights and the edge bonus cap are hand-assigned judgement,
#     not backtested — same status as every confidence number in this project.
#   - Sweep/divergence checks replay recent bars against each zone's FINAL
#     centre and width. Centres drift slowly (volume-weighted), so over the
#     short replay window this is a close approximation, not a perfect
#     bar-by-bar reconstruction. Stated so it is never over-claimed.
# ===========================================================================


def _cvd_proxy(candles):
    """Cumulative intrabar pressure: vol x (2*(C-L)/(H-L) - 1).

    Close near the high => buyers absorbed what was offered; near the low =>
    sellers did. A proxy for cumulative volume delta, with the limitation
    stated in the section header.
    """
    out = []
    acc = 0.0
    for c in candles:
        h, l, cl = c["high"], c["low"], c["close"]
        v = float(c.get("volume") or 0)
        rng = h - l
        if rng > 0:
            acc += v * (2.0 * (cl - l) / rng - 1.0)
        out.append(acc)
    return out


def _anchored_vwap(candles, lookback=120):
    """VWAP anchored at the highest-volume confirmed pivot in the recent
    window (the 'significant swing point' anchor), with volume-weighted
    standard deviation bands. Falls back to the window start if no pivot
    qualifies. Returns None when volume is absent (indices)."""
    n = len(candles)
    start = max(0, n - lookback)
    window = candles[start:]

    anchor = start
    pivots = find_confirmed_pivots(candles, 3, 3)
    # anchor must leave at least 20 bars of accumulation, or the VWAP and its
    # bands are too thin to say anything
    recent = [p for p in pivots if start <= p["index"] <= n - 20]
    if recent:
        anchor = max(recent, key=lambda p: p["volume"])["index"]

    tot_v = 0.0
    acc_pv = 0.0
    acc_pv2 = 0.0
    for c in candles[anchor:]:
        v = float(c.get("volume") or 0)
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        tot_v += v
        acc_pv += tp * v
        acc_pv2 += tp * tp * v
    if tot_v <= 0:
        return None

    vwap = acc_pv / tot_v
    var = max(0.0, acc_pv2 / tot_v - vwap * vwap)
    sd = var ** 0.5
    last = candles[-1]["close"]
    return {
        "anchor_index": anchor,
        "anchor_date": str(candles[anchor].get("date"))[:16],
        "anchored_bars": n - anchor,
        "vwap": round(vwap, 2),
        "sd": round(sd, 2),
        "price_z": round((last - vwap) / sd, 2) if sd > 0 else None,
        "bands": {f"{s:+d}sd": round(vwap + s * sd, 2) for s in (-2, -1, 1, 2)} | {"vwap": round(vwap, 2)} if sd > 0 else None,
    }


def _volume_profile(candles, lookback=250, bins=50, hvn_frac=0.70):
    """Volume-at-price from candles: each bar's volume spread uniformly
    across its high-low range. Returns POC and merged HVN price ranges.
    An approximation of a tick-built profile — resolution is the bar range."""
    window = candles[-min(lookback, len(candles)):]
    lo = min(c["low"] for c in window)
    hi = max(c["high"] for c in window)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    vol_at = [0.0] * bins
    for c in window:
        v = float(c.get("volume") or 0)
        if v <= 0:
            continue
        b0 = int((c["low"] - lo) / step)
        b1 = int((c["high"] - lo) / step)
        b0 = max(0, min(bins - 1, b0))
        b1 = max(0, min(bins - 1, b1))
        share = v / (b1 - b0 + 1)
        for b in range(b0, b1 + 1):
            vol_at[b] += share
    peak = max(vol_at)
    if peak <= 0:
        return None
    poc_bin = vol_at.index(peak)
    poc = lo + (poc_bin + 0.5) * step

    hvn_bins = [i for i, v in enumerate(vol_at) if v >= hvn_frac * peak]
    hvns = []
    for b in hvn_bins:
        b_lo, b_hi = lo + b * step, lo + (b + 1) * step
        if hvns and b_lo <= hvns[-1][1] + step * 0.01:
            hvns[-1][1] = b_hi
        else:
            hvns.append([b_lo, b_hi])

    return {
        "poc": round(poc, 2),
        "hvns": [{"low": round(a, 2), "high": round(b, 2)} for a, b in hvns],
        "profile_lookback": len(window),
    }


def _sweep_reclaims(z_bot, z_top, role, candles, reclaim_within=3, window=60):
    """Multi-bar sweep-and-reclaim events against a zone's final geometry.

    A sweep: a CLOSE beyond the tested edge (any amount — including the soft
    band under the decisive-break threshold). A reclaim: a close back on the
    defended side within `reclaim_within` bars. This is the multi-bar cousin
    of the single-bar wick rejection already scored in strength; the two do
    not double count because a wick rejection never closes outside.
    """
    n = len(candles)
    start = max(1, n - window)
    events = 0
    last_bars_ago = None
    edges = []
    if role in ("support", "inside"):
        edges.append(("below", z_bot))
    if role in ("resistance", "inside"):
        edges.append(("above", z_top))

    for side, edge in edges:
        i = start
        while i < n:
            c = candles[i]["close"]
            swept = c < edge if side == "below" else c > edge
            if swept:
                reclaimed_at = None
                for j in range(i + 1, min(i + 1 + reclaim_within, n)):
                    cj = candles[j]["close"]
                    back = cj >= edge if side == "below" else cj <= edge
                    if back:
                        reclaimed_at = j
                        break
                if reclaimed_at is not None:
                    events += 1
                    last_bars_ago = n - 1 - reclaimed_at
                    i = reclaimed_at + 1
                    continue
            i += 1
    return events, last_bars_ago


def _absorption_divergence(z_bot, z_top, role, candles, cvd, window=60):
    """Compare the two most recent zone interactions: price extreme deeper
    into the zone while the CVD proxy refuses to confirm => absorption
    (proxy). Bullish at support, bearish at resistance. Returns None when
    there are fewer than two interactions to compare."""
    n = len(candles)
    start = max(0, n - window)
    touches = []
    for i in range(start, n):
        c = candles[i]
        if role == "resistance":
            if c["high"] >= z_bot and c["high"] <= z_top + (z_top - z_bot):
                touches.append(i)
        else:  # support
            if c["low"] <= z_top and c["low"] >= z_bot - (z_top - z_bot):
                touches.append(i)
    # collapse consecutive bars into single interactions, keep the extreme bar
    groups = []
    for i in touches:
        if groups and i - groups[-1][-1] <= 2:
            groups[-1].append(i)
        else:
            groups.append([i])
    if len(groups) < 2:
        return None

    def extreme(g):
        if role == "resistance":
            return max(g, key=lambda i: candles[i]["high"])
        return min(g, key=lambda i: candles[i]["low"])

    a, b = extreme(groups[-2]), extreme(groups[-1])
    if role == "resistance":
        price_pushed = candles[b]["high"] > candles[a]["high"]
        flow_refused = cvd[b] < cvd[a]
    else:
        price_pushed = candles[b]["low"] < candles[a]["low"]
        flow_refused = cvd[b] > cvd[a]
    return bool(price_pushed and flow_refused)


def enrich_zones_with_flow(zones, candles, atr_period=14):
    """Annotate finished zones with flow-confirmation criteria and return a
    chart-level context block. Mutates zone dicts by ADDING keys only —
    strength, prospect and every pre-existing field are left exactly as
    produced upstream.

    Adds per zone:
      sweep_reclaims, last_reclaim_bars_ago,
      absorption_divergence (candle proxy — may be None),
      vwap_confluence (which band overlaps, or None),
      hvn_confluence, poc_in_zone,
      flow_score (0-100, hand-assigned weights),
      edge (strength + flow bonus, bonus capped at +18, capped 100)
    """
    if not zones or not candles:
        return None

    cvd = _cvd_proxy(candles)
    has_volume = any(float(c.get("volume") or 0) > 0 for c in candles[-30:])
    vwap = _anchored_vwap(candles) if has_volume else None
    profile = _volume_profile(candles) if has_volume else None

    for z in zones:
        bot, top, role = z["low"], z["high"], z["role"]
        if z["position"] == "inside":
            role = "inside"

        events, last_ago = _sweep_reclaims(bot, top, role, candles)
        z["sweep_reclaims"] = events
        z["last_reclaim_bars_ago"] = last_ago

        div = _absorption_divergence(bot, top, z["role"], candles, cvd) if has_volume else None
        z["absorption_divergence"] = div

        band = None
        if vwap and vwap.get("bands"):
            for name, level in vwap["bands"].items():
                if bot <= level <= top:
                    band = name
                    break
        z["vwap_confluence"] = band

        hvn_hit = False
        poc_in = False
        if profile:
            poc_in = bot <= profile["poc"] <= top
            for h in profile["hvns"]:
                if h["low"] <= top and h["high"] >= bot:
                    hvn_hit = True
                    break
        z["hvn_confluence"] = bool(hvn_hit)
        z["poc_in_zone"] = bool(poc_in)

        # hand-assigned weights — judgement, not backtest
        fs = (min(events, 2) * 25
              + (25 if div else 0)
              + (15 if band else 0)
              + (15 if hvn_hit else 0)
              + (10 if poc_in else 0))
        z["flow_score"] = min(100, fs)
        z["edge"] = min(100, z["strength"] + round(z["flow_score"] * 0.18))

    ctx = {
        "vwap": vwap,
        "profile": profile,
        "cvd_proxy_slope_20": (round(cvd[-1] - cvd[-21], 0)
                               if len(cvd) > 21 else None),
        "limitations": [
            "absorption_divergence uses a candle-based CVD proxy — passive "
            "absorption and aggressive selling are indistinguishable without "
            "tick data",
            "historical order-book depth is not available on any public "
            "Indian feed; live depth remains on the Execution tab only",
            "flow_score weights and the edge bonus are hand-assigned, not "
            "backtested",
            "sweep/divergence replay uses each zone's final geometry over a "
            "short recent window, not a bar-by-bar historical reconstruction",
        ],
    }
    if vwap and vwap.get("price_z") is not None and abs(vwap["price_z"]) > 2:
        ctx["vwap_note"] = (f"price is {vwap['price_z']:+.2f} sd from the "
                            "anchored VWAP — beyond the 2-sd band. Whether the "
                            "move is initiated or exhausted cannot be measured "
                            "from candles.")
    return ctx
