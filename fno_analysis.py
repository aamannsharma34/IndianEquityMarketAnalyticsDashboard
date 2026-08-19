"""
F&O open interest analysis: what structures are being built, and by inference
whether futures positions are being hedged with options or run naked.

WHAT OPEN INTEREST CAN AND CANNOT TELL YOU

Open interest counts contracts outstanding. Every one of those contracts has a
buyer and a seller. So OI on its own can never say who initiated, who is long,
or who is "smart". Rising OI means new positions were opened; falling OI means
positions were closed. That is the entire literal content of the number.

What makes it useful is combining OI change with PRICE change. If open interest
rises while price rises, new money is being committed on the long side, because
buyers are lifting offers to get filled. That is an inference from who is being
aggressive, not a measurement of positioning. It is a good inference, widely
used, and it is wrong often enough that it should never be the only input.

The one genuine measurement of participant type is NSE's daily participant-wise
OI file (FII / DII / Pro / Client). That is end-of-day only and is not available
through a broker API. Everything in this module is inference from aggregate data.

THE FUTURES FRAMEWORK (reliable)

    price up   + OI up    -> long buildup      fresh longs, bullish
    price down + OI up    -> short buildup     fresh shorts, bearish
    price up   + OI down  -> short covering    shorts exiting, bullish but weaker
    price down + OI down  -> long unwinding    longs exiting, bearish but weaker

Covering and unwinding are weaker signals than buildups: they represent old
positions leaving rather than new conviction arriving.

THE OPTIONS FRAMEWORK (weaker, and here is why)

    call OI up + call premium up   -> call buying    (buyers ahead on price)
    call OI up + call premium down -> call writing   (sellers ahead on price)

    A caution on wording: this says who came out ahead on PRICE, not who was
    crossing the spread. Passive buyers absorbing aggressive sellers produce
    rising OI with a rising premium and are indistinguishable here from genuine
    aggressive buying. Separating the two needs tick-level order flow, which no
    open-interest feed provides.

The weakness: option premium moves with the underlying and with implied
volatility, not just with order flow. A call can lose value on a flat day purely
from time decay while genuine buying occurs. This module therefore adjusts for
the underlying's move before reading premium direction, which removes the
largest source of false signals but not all of them.

THE PART THAT ANSWERS THE REAL QUESTION

Combining the two gives the position STRUCTURE, which is the interesting object:

    futures long buildup  + call writing  -> covered calls; bullish but capped
    futures long buildup  + put buying    -> protected long; bullish, paying for insurance
    futures short buildup + call buying   -> hedged short; bearish, capping risk
    futures short buildup + call writing  -> naked bearish; conviction, no hedge
    call writing + put writing            -> short volatility; range expected
    call buying  + put buying             -> long volatility; a move expected, direction unclear

A hedged structure and a naked one carry completely different information about
conviction, and that distinction is invisible if you look at futures or options
alone.
"""

# Copyright (c) 2026 Amans2bmm. All rights reserved.
# Part of the Market Analytics Dashboard. Unauthorised copying, distribution,
# or derivative use without express written permission is prohibited.

import datetime


def classify_futures(price_chg_pct, oi_chg_pct, min_oi_move=1.0, min_price_move=0.15):
    """Standard price/OI buildup classification for a futures contract."""
    if abs(oi_chg_pct) < min_oi_move and abs(price_chg_pct) < min_price_move:
        return {"kind": "quiet", "label": "Quiet", "bias": "neutral", "strength": 0}

    if oi_chg_pct >= min_oi_move and price_chg_pct > 0:
        k, lbl, bias = "long_buildup", "Long buildup", "bullish"
    elif oi_chg_pct >= min_oi_move and price_chg_pct < 0:
        k, lbl, bias = "short_buildup", "Short buildup", "bearish"
    elif oi_chg_pct <= -min_oi_move and price_chg_pct > 0:
        k, lbl, bias = "short_covering", "Short covering", "bullish"
    elif oi_chg_pct <= -min_oi_move and price_chg_pct < 0:
        k, lbl, bias = "long_unwinding", "Long unwinding", "bearish"
    elif price_chg_pct > 0:
        k, lbl, bias = "drift_up", "Drifting up", "neutral"
    else:
        k, lbl, bias = "drift_down", "Drifting down", "neutral"

    # buildups carry more information than covering/unwinding: new conviction
    # rather than old positions leaving
    base = 2.0 if k in ("long_buildup", "short_buildup") else 1.0
    strength = round(min(100, base * (abs(oi_chg_pct) * 1.6 + abs(price_chg_pct) * 6)))
    return {"kind": k, "label": lbl, "bias": bias, "strength": strength}


def classify_option_flow(oi_chg, premium_chg_pct, underlying_chg_pct, opt_type,
                         min_oi=200):
    """
    Was this strike written or bought?

    Premium moves with the underlying regardless of flow, so the underlying's
    expected effect is removed before reading the premium's direction. A call
    should rise when spot rises; if OI is climbing while the premium falls
    SHORT of that expectation, sellers are the aggressive side.
    """
    if abs(oi_chg) < min_oi:
        return None

    # crude delta proxy: calls gain with spot, puts lose. Not exact, but enough
    # to strip out the dominant confound.
    expected = underlying_chg_pct * (2.5 if opt_type == "CE" else -2.5)
    residual = premium_chg_pct - expected

    if oi_chg > 0:
        if residual < -2:
            return {"flow": "writing", "side": opt_type, "confidence": min(1.0, abs(residual) / 15)}
        if residual > 2:
            return {"flow": "buying", "side": opt_type, "confidence": min(1.0, abs(residual) / 15)}
        return {"flow": "unclear", "side": opt_type, "confidence": 0.2}
    else:
        # Falling OI means positions CLOSED — it can never mean new buying.
        # Which side closed matters, and the premium says which: writers buying
        # back bid the premium UP as they exit, while holders selling to close
        # offer it DOWN. Labelling both "unwinding" hides opposite meanings.
        if residual > 1.0:
            return {"flow": "writer_covering", "side": opt_type,
                    "confidence": min(1.0, abs(residual) / 12)}
        if residual < -1.0:
            return {"flow": "holder_exit", "side": opt_type,
                    "confidence": min(1.0, abs(residual) / 12)}
        return {"flow": "unwinding", "side": opt_type, "confidence": 0.3}


def aggregate_option_flow(strikes, spot, underlying_chg_pct):
    """
    Roll per-strike flow into a view for calls and for puts.
    strikes: [{strike, type, oi, oi_chg, ltp, prev_ltp}]
    """
    ce_oi = pe_oi = 0
    ce_oi_chg = pe_oi_chg = 0
    ce_written = ce_bought = pe_written = pe_bought = 0

    for s in strikes:
        oi = s.get("oi") or 0
        raw_chg = s.get("oi_chg")
        if raw_chg is None:
            # no baseline for this strike — counting it as zero would drag the
            # aggregate toward no-change and hide a real move
            if s["type"] == "CE":
                ce_oi += oi
            else:
                pe_oi += oi
            continue
        oi_chg = raw_chg
        if s["type"] == "CE":
            ce_oi += oi; ce_oi_chg += oi_chg
        else:
            pe_oi += oi; pe_oi_chg += oi_chg

        # premium change: NSE gives it directly, snapshots must derive it
        prem_chg = None
        if s.get("pct_chg") is not None:
            try:
                prem_chg = float(s["pct_chg"])
            except (TypeError, ValueError):
                prem_chg = None
        if prem_chg is None:
            ltp, prev = s.get("ltp"), s.get("prev_ltp")
            if not ltp or not prev or prev <= 0:
                continue
            prem_chg = (ltp - prev) / prev * 100
        f = classify_option_flow(oi_chg, prem_chg, underlying_chg_pct, s["type"])
        if not f:
            continue
        w = abs(oi_chg)
        # only OPENING flow counts toward the writing/buying skew; closures are
        # positions leaving and say nothing about fresh conviction
        if s["type"] == "CE":
            if f["flow"] == "writing": ce_written += w
            elif f["flow"] == "buying": ce_bought += w
        else:
            if f["flow"] == "writing": pe_written += w
            elif f["flow"] == "buying": pe_bought += w

    def dominant(written, bought):
        tot = written + bought
        if tot < 1:
            return {"flow": "none", "skew": 0.0}
        skew = (written - bought) / tot
        if skew > 0.25:
            return {"flow": "writing", "skew": round(skew, 2)}
        if skew < -0.25:
            return {"flow": "buying", "skew": round(skew, 2)}
        return {"flow": "mixed", "skew": round(skew, 2)}

    pcr_oi = round(pe_oi / ce_oi, 2) if ce_oi > 0 else None

    return {
        "ce_oi": ce_oi, "pe_oi": pe_oi,
        "ce_oi_chg": ce_oi_chg, "pe_oi_chg": pe_oi_chg,
        "pcr_oi": pcr_oi,
        "call_flow": dominant(ce_written, ce_bought),
        "put_flow": dominant(pe_written, pe_bought),
        "ce_written_vol": ce_written, "ce_bought_vol": ce_bought,
        "pe_written_vol": pe_written, "pe_bought_vol": pe_bought,
    }


def max_pain(strikes):
    """
    Strike at which option writers' total payout is smallest.

    Treat this as a gravitational hint at best. The theory that price is pushed
    toward max pain is contested and the level moves as OI shifts, so it is
    context rather than a target.
    """
    by_strike = {}
    for s in strikes:
        k = s["strike"]
        by_strike.setdefault(k, {"CE": 0, "PE": 0})
        by_strike[k][s["type"]] += (s.get("oi") or 0)
    ks = sorted(by_strike.keys())
    if len(ks) < 3:
        return None

    best, best_pain = None, None
    for expiry_price in ks:
        pain = 0
        for k, v in by_strike.items():
            if expiry_price > k:
                pain += (expiry_price - k) * v["CE"]
            if expiry_price < k:
                pain += (k - expiry_price) * v["PE"]
        if best_pain is None or pain < best_pain:
            best_pain, best = pain, expiry_price
    return best


def infer_structure(fut, opt):
    """
    Combine futures buildup with options flow into a named position structure.
    This is the layer that answers whether futures exposure is being hedged.
    """
    if not fut or not opt:
        return None
    fk = fut["kind"]
    cf = opt["call_flow"]["flow"]
    pf = opt["put_flow"]["flow"]

    S = lambda label, note, bias, conf: {
        "label": label, "note": note, "bias": bias, "confidence": conf}

    if fk == "long_buildup":
        if cf == "writing":
            return S("Covered calls", "Fresh futures longs with calls being written against them — bullish, but the upside is being capped for premium. Suggests an expectation of grind rather than breakout.", "bullish_capped", 0.7)
        if pf == "buying":
            return S("Protected long", "Fresh futures longs paying for downside puts. Bullish with conviction, but willing to pay for insurance — often a sign of event risk ahead.", "bullish", 0.7)
        if pf == "writing":
            return S("Aggressive long", "Futures longs plus put writing — the same directional view expressed twice, with no hedge. High conviction, high risk.", "bullish", 0.75)
        return S("Naked long buildup", "Fresh futures longs with no clear offsetting option structure.", "bullish", 0.5)

    if fk == "short_buildup":
        if cf == "buying":
            return S("Hedged short", "Fresh futures shorts buying calls as protection. Bearish, but the tail risk is being capped — measured rather than aggressive.", "bearish", 0.7)
        if cf == "writing":
            return S("Naked bearish", "Futures shorts plus call writing — the bearish view doubled up with no hedge. High conviction, and painful if wrong.", "bearish", 0.75)
        if pf == "buying":
            return S("Aggressive short", "Futures shorts alongside put buying. Strongly bearish positioning.", "bearish", 0.75)
        return S("Naked short buildup", "Fresh futures shorts with no clear offsetting option structure.", "bearish", 0.5)

    if fk == "short_covering":
        if cf == "buying":
            return S("Squeeze", "Shorts covering while calls are bought — the classic squeeze signature. Moves can be sharp but often fade once covering completes.", "bullish", 0.6)
        return S("Short covering", "Old shorts exiting rather than new longs arriving. Supportive, but weaker than a genuine buildup.", "bullish", 0.4)

    if fk == "long_unwinding":
        return S("Long unwinding", "Longs are leaving rather than shorts arriving. Weak, and often just position squaring rather than a directional view.", "bearish", 0.4)

    if cf == "writing" and pf == "writing":
        return S("Short volatility", "Both calls and puts being written with no futures conviction — a bet on the stock staying in a range through expiry.", "neutral", 0.6)
    if cf == "buying" and pf == "buying":
        return S("Long volatility", "Both calls and puts being bought — someone expects a large move but is not committing to direction. Often precedes results or a known event.", "neutral", 0.65)

    return S("No clear structure", "Nothing in the OI data forms a coherent position picture right now.", "neutral", 0.2)


def futures_oi_trend(candles, days=5):
    """
    Multi-day futures OI trend.

    A single day's OI change is a snapshot and can mislead badly: a stock five
    sessions into steady accumulation may post its smallest increment today and
    look quiet, while a one-day spike on an otherwise flat base looks identical
    to sustained conviction. This measures the run.

    candles: daily candles carrying 'oi' and 'close', oldest first.
    """
    rows = [c for c in candles if c.get("oi") is not None]
    if len(rows) < days + 1:
        # report the shortfall rather than vanishing — a missing line is
        # indistinguishable from a broken one
        return {"kind": "insufficient", "label": "Not enough OI history",
                "days": len(rows), "needed": days + 1,
                "oi_chg_pct": None, "px_chg_pct": None,
                "consistency": None, "conviction": 0, "daily": []}

    window = rows[-(days + 1):]
    oi = [float(c["oi"]) for c in window]
    px = [float(c["close"]) for c in window]

    total_oi_chg_pct = (oi[-1] - oi[0]) / oi[0] * 100 if oi[0] else 0
    total_px_chg_pct = (px[-1] - px[0]) / px[0] * 100 if px[0] else 0

    daily = []
    for i in range(1, len(window)):
        if oi[i - 1] <= 0 or px[i - 1] <= 0:
            continue
        daily.append({
            "oi_chg_pct": round((oi[i] - oi[i - 1]) / oi[i - 1] * 100, 2),
            "px_chg_pct": round((px[i] - px[i - 1]) / px[i - 1] * 100, 2),
        })
    if not daily:
        return None

    up_days = sum(1 for d in daily if d["oi_chg_pct"] > 0)
    # consistency: how one-directional the OI build has been
    consistency = round(max(up_days, len(daily) - up_days) / len(daily), 2)

    # classify the multi-day picture the same way as a single day
    if total_oi_chg_pct > 2 and total_px_chg_pct > 0:
        kind, label = "sustained_long", "Sustained long buildup"
    elif total_oi_chg_pct > 2 and total_px_chg_pct < 0:
        kind, label = "sustained_short", "Sustained short buildup"
    elif total_oi_chg_pct < -2 and total_px_chg_pct > 0:
        kind, label = "sustained_covering", "Sustained short covering"
    elif total_oi_chg_pct < -2 and total_px_chg_pct < 0:
        kind, label = "sustained_unwinding", "Sustained long unwinding"
    else:
        kind, label = "no_trend", "No clear multi-day trend"

    return {
        "days": len(daily),
        "kind": kind,
        "label": label,
        "oi_chg_pct": round(total_oi_chg_pct, 2),
        "px_chg_pct": round(total_px_chg_pct, 2),
        "consistency": consistency,
        # a build that is both large and one-directional is worth more than
        # a single day's spike on an otherwise flat base
        "conviction": round(min(100, abs(total_oi_chg_pct) * 4 * consistency)),
        "daily": daily,
    }


def reconcile_day_vs_trend(day_cls, trend):
    if trend and trend.get("kind") == "insufficient":
        return {"state": "insufficient",
                "note": (f"Only {trend['days']} sessions of open-interest history came back for this "
                         f"contract, and {trend['needed']} are needed for a 5-day trend. Today's "
                         f"classification above still stands — it only needs two sessions.")}
    """
    Does today agree with the run, or contradict it?

    The interesting cases are the disagreements: a multi-day short buildup that
    starts covering today is a different situation from one still adding, and
    a single strong day against a flat base is not the same as day five of
    steady accumulation.
    """
    if not trend or trend["kind"] == "no_trend":
        return {"state": "no_trend", "note": "No multi-day trend to compare today against — read today's move on its own."}

    bullish_trend = trend["kind"] in ("sustained_long", "sustained_covering")
    bullish_today = day_cls["bias"] == "bullish"

    if day_cls["kind"] == "quiet":
        return {"state": "pausing",
                "note": f"{trend['label'].lower()} over {trend['days']} sessions, but today is quiet — the build has paused rather than reversed."}

    if bullish_trend == bullish_today:
        return {"state": "confirming",
                "note": f"Today extends a {trend['label'].lower()} running {trend['days']} sessions ({trend['oi_chg_pct']:+.1f}% OI). Consistent positioning rather than a one-day spike."}

    return {"state": "diverging",
            "note": f"Today runs COUNTER to a {trend['days']}-session {trend['label'].lower()} ({trend['oi_chg_pct']:+.1f}% OI). Either the build is being unwound or today is noise — worth checking which before acting."}


def _robust_scale(values):
    """
    Median absolute deviation, scaled to be comparable with a standard deviation.

    Standard deviation is the obvious choice and the wrong one here. It assumes
    a roughly normal distribution, and open-interest data is anything but: one
    expiry rollover in the lookback window inflates the deviation enough to
    score every subsequent genuine move as routine. In testing, a single 42%
    rollover day dropped a real 4.2-sigma move to 0.07.

    MAD ignores the tails by construction. The 1.4826 factor makes it match a
    standard deviation for data that IS normal, so the scale stays familiar.
    """
    if not values:
        return 0.0, 0.0
    srt = sorted(values)
    n = len(srt)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    devs = sorted(abs(v - med) for v in values)
    mad = devs[n // 2] if n % 2 else (devs[n // 2 - 1] + devs[n // 2]) / 2
    return med, mad * 1.4826


def oi_change_zscore(candles, lookback=20):
    """
    Today's futures OI change measured against this contract's OWN distribution
    of daily OI changes, using median and MAD rather than mean and standard
    deviation.

    A 4% OI move is routine in a contract that swings 5% either way and a major
    event in one that rarely moves 1% — the raw percentage says nothing without
    the spread. But the spread itself has to be measured robustly, because
    rollover weeks plant outliers in every lookback window and a standard
    deviation is defenceless against them.
    """
    rows = [c for c in candles if c.get("oi") is not None]
    if len(rows) < lookback + 3:
        return None

    changes = []
    for i in range(1, len(rows)):
        prev, cur = float(rows[i - 1]["oi"]), float(rows[i]["oi"])
        if prev > 0:
            changes.append((cur - prev) / prev * 100)
    if len(changes) < lookback:
        return None

    today = changes[-1]
    hist = changes[-(lookback + 1):-1]
    mean, sd = _robust_scale(hist)
    if sd < 0.05:
        return None

    z = (today - mean) / sd
    if abs(z) >= 2.5:
        band = "extreme"
    elif abs(z) >= 1.5:
        band = "notable"
    elif abs(z) >= 0.75:
        band = "mild"
    else:
        band = "routine"

    return {
        "today_pct": round(today, 2),
        "mean_pct": round(mean, 2),
        "sd_pct": round(sd, 2),
        "z": round(z, 2),
        "band": band,
        "note": (f"Today's {today:+.1f}% OI move is {abs(z):.1f} robust deviations "
                 f"{'above' if z > 0 else 'below'} this contract's typical daily change "
                 f"of {mean:+.1f}% (spread {sd:.1f}%). "
                 + {"extreme": "Well outside its normal range.",
                    "notable": "Meaningfully larger than a typical day.",
                    "mild": "Somewhat above routine.",
                    "routine": "Within its ordinary day-to-day variation."}[band]),
    }


def pcr_context(pcr_today, pcr_history):
    """
    PCR judged against its own recent range rather than the folk thresholds.

    A put-call ratio of 0.8 is bearish in a stock that normally runs 1.4 and
    bullish in one that sits at 0.5. The absolute number carries almost no
    information; its position within that stock's own distribution carries most
    of it. Absolute rules of thumb (below 0.7 bearish, above 1.3 bullish) are
    applied to indices where the range is stable — much less so to single stocks.
    """
    if pcr_today is None or not pcr_history or len(pcr_history) < 5:
        return None
    vals = sorted(pcr_history)
    below = sum(1 for v in vals if v < pcr_today)
    pct = below / len(vals) * 100
    mean, sd = _robust_scale(vals)
    z = (pcr_today - mean) / sd if sd > 0.01 else 0.0

    if pct >= 80:
        state = "high"
        note = "Put OI is unusually heavy relative to calls for this stock — more downside protection or put writing than normal."
    elif pct <= 20:
        state = "low"
        note = "Call OI is unusually heavy relative to puts for this stock — more upside positioning or call writing than normal."
    else:
        state = "normal"
        note = "The put-call balance sits within this stock's usual range."

    return {"pcr": round(pcr_today, 2), "percentile": round(pct), "z": round(z, 2),
            "mean": round(mean, 2), "sessions": len(vals), "state": state, "note": note}


def option_oi_trend(history):
    """
    Multi-day direction of call and put open interest.

    history: [{"date": iso, "ce_oi": x, "pe_oi": y}, ...] oldest first.

    A single session's option OI change is as noisy as a single day's delivery.
    What matters for an expiry view is whether call and put OI have been
    building or bleeding across the run into it.
    """
    rows = [h for h in history if h.get("ce_oi") and h.get("pe_oi")]
    if len(rows) < 3:
        return None

    ce_first, ce_last = rows[0]["ce_oi"], rows[-1]["ce_oi"]
    pe_first, pe_last = rows[0]["pe_oi"], rows[-1]["pe_oi"]
    ce_chg = (ce_last - ce_first) / ce_first * 100 if ce_first else 0
    pe_chg = (pe_last - pe_first) / pe_first * 100 if pe_first else 0

    def direction(v):
        if v > 8:
            return "building"
        if v < -8:
            return "unwinding"
        return "flat"

    ce_dir, pe_dir = direction(ce_chg), direction(pe_chg)

    if ce_dir == "building" and pe_dir == "building":
        summary = "Both sides adding — open interest is growing into expiry with no directional tilt from the totals alone."
    elif ce_dir == "building" and pe_dir != "building":
        summary = "Call OI building while put OI is not — positioning is concentrating on the call side."
    elif pe_dir == "building" and ce_dir != "building":
        summary = "Put OI building while call OI is not — positioning is concentrating on the put side."
    elif ce_dir == "unwinding" and pe_dir == "unwinding":
        summary = "Both sides unwinding — positions are being closed ahead of expiry rather than added."
    else:
        summary = "No clear multi-day direction in either leg."

    return {"sessions": len(rows),
            "ce_chg_pct": round(ce_chg, 1), "pe_chg_pct": round(pe_chg, 1),
            "ce_dir": ce_dir, "pe_dir": pe_dir, "summary": summary}


def expiry_signal(fut_cls, oi_z, opt_flow, pcr_ctx, opt_trend, days_to_expiry):
    """
    Pull the strands into one reading for the expiry: bullish, bearish or mixed.

    Deliberately conservative. Each strand votes, and disagreement produces
    "mixed" rather than a forced call — the honest output when futures and
    options point different ways, which happens often and is itself information.
    """
    votes = []

    bias = fut_cls.get("bias")
    if bias in ("bullish", "bearish"):
        weight = 2 if fut_cls["kind"] in ("long_buildup", "short_buildup") else 1
        if oi_z and oi_z["band"] in ("notable", "extreme"):
            weight += 1
        votes.append((bias, weight, f"futures {fut_cls['label'].lower()}"))

    if opt_flow:
        cf, pf = opt_flow["call_flow"]["flow"], opt_flow["put_flow"]["flow"]
        if cf == "writing":
            votes.append(("bearish", 1, "calls being written"))
        elif cf == "buying":
            votes.append(("bullish", 1, "calls being bought"))
        if pf == "writing":
            votes.append(("bullish", 1, "puts being written"))
        elif pf == "buying":
            votes.append(("bearish", 1, "puts being bought"))

    if pcr_ctx and pcr_ctx["state"] == "high":
        votes.append(("bearish", 1, "put-call ratio high for this stock"))
    elif pcr_ctx and pcr_ctx["state"] == "low":
        votes.append(("bullish", 1, "put-call ratio low for this stock"))

    if opt_trend:
        if opt_trend["ce_dir"] == "building" and opt_trend["pe_dir"] != "building":
            votes.append(("bearish", 1, "call OI building over the run"))
        elif opt_trend["pe_dir"] == "building" and opt_trend["ce_dir"] != "building":
            votes.append(("bullish", 1, "put OI building over the run"))

    if not votes:
        return {"signal": "no_read", "label": "No read",
                "note": "Not enough usable positioning data to form a view."}

    bull = sum(w for b, w, _ in votes if b == "bullish")
    bear = sum(w for b, w, _ in votes if b == "bearish")
    total = bull + bear
    if total == 0:
        return {"signal": "mixed", "label": "Mixed", "note": "Signals cancel out."}

    tilt = (bull - bear) / total
    reasons = [r for _, _, r in votes]

    if tilt >= 0.5:
        sig, lbl = "bullish", "Bullish into expiry"
    elif tilt <= -0.5:
        sig, lbl = "bearish", "Bearish into expiry"
    elif abs(tilt) >= 0.2:
        sig = "lean_bullish" if tilt > 0 else "lean_bearish"
        lbl = "Leaning bullish" if tilt > 0 else "Leaning bearish"
    else:
        sig, lbl = "mixed", "Mixed"

    return {
        "signal": sig, "label": lbl,
        "bull_weight": bull, "bear_weight": bear, "tilt": round(tilt, 2),
        "reasons": reasons[:6],
        "days_to_expiry": days_to_expiry,
        "note": ("Strands disagree, which is itself information — one side is positioning "
                 "against the other rather than everyone leaning the same way."
                 if sig == "mixed" else
                 f"{bull if tilt > 0 else bear} of {total} weighted signals point the same way."),
    }
