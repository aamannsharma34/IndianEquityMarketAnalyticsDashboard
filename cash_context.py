"""
Cash-market context for F&O positioning.

THE PROBLEM THIS SOLVES

Futures open interest alone cannot distinguish between three completely
different situations that produce identical data:

  1. Genuine bearish speculation  - someone expects the stock to fall
  2. Cash-and-carry arbitrage     - someone bought the stock in cash and sold
                                    futures to capture the basis. Market
                                    neutral. No directional view at all.
  3. Hedging an existing holding  - an institution protecting a cash position
                                    it has no intention of selling

All three show up as "short buildup". Reading that as bearish is wrong two
times out of three, and cash-and-carry is mechanical and common, so the error
is not rare.

WHAT SEPARATES THEM

BASIS (futures price minus spot). Arbitrage only pays when futures are rich to
spot. A short buildup on an expensive basis is likely arbitrage; the same
buildup with the basis flat or negative is more likely real selling, because
nobody arbitrages at a loss.

DELIVERY PERCENTAGE. The share of traded volume that actually settled into
demat accounts rather than being squared off intraday.

  WHAT IT CORRECTLY EXCLUDES: intraday trading. Positions opened and closed the
  same day net to zero and never reach settlement, so day-trading volume does
  not inflate this number.

  WHAT IT DOES NOT EXCLUDE, and this matters:
    - MTF (margin-funded) purchases. These take delivery into the broker's
      account against a loan, so they are indistinguishable here from an
      outright cash buyer, despite being leveraged and often short-horizon.
    - BTST positions, which settle on day one and are sold on day two.
    - The cash leg of cash-and-carry arbitrage. An arbitrageur buying stock to
      short futures against it takes full delivery.

  That last one deserves attention: high delivery alongside a futures short
  buildup may be arbitrage rather than accumulation. The verdict logic below
  checks for exactly that combination, but it means delivery on its own was
  never a clean accumulation signal — only a filter that removes the noisiest
  category of activity.

CASH VOLUME. Whether the cash market is participating at all, or whether the
whole story is happening in derivatives.

WHAT THIS STILL CANNOT DO

None of this identifies participants. Delivery percentage does not say who took
delivery. The basis does not say who is arbitraging. This narrows the
interpretation of aggregate data considerably, which is worth a great deal, but
it is not attribution.
"""

# Copyright (c) 2026 Amans2bmm. All rights reserved.
# Part of the Market Analytics Dashboard. Unauthorised copying, distribution,
# or derivative use without express written permission is prohibited.


def annualised_basis(spot, future, days_to_expiry):
    """
    Basis expressed as an annual rate, which is the only way to judge whether
    it is rich. A 0.4% basis is enormous with three days to expiry and
    unremarkable with forty.
    """
    if not spot or not future or spot <= 0 or days_to_expiry <= 0:
        return None
    raw_pct = (future - spot) / spot * 100
    return {
        "basis": round(future - spot, 2),
        "basis_pct": round(raw_pct, 3),
        "annualised_pct": round(raw_pct * 365.0 / days_to_expiry, 2),
        "days": days_to_expiry,
    }


def classify_basis(ann_pct, risk_free=6.5):
    """
    Is the basis rich enough to make cash-and-carry worth doing?

    Arbitrage needs to beat the cost of funding plus transaction costs. Around
    the risk-free rate is fair value; meaningfully above it is where arbitrage
    desks engage, and that is where futures short OI stops being a directional
    signal.
    """
    if ann_pct is None:
        return {"state": "unknown", "note": "Basis unavailable."}
    if ann_pct > risk_free + 4:
        return {"state": "rich",
                "note": f"Basis annualises to {ann_pct:.1f}%, well above funding cost. Cash-and-carry arbitrage is profitable here, so futures short buildup may be arbitrage rather than a bearish view."}
    if ann_pct > risk_free:
        return {"state": "fair_rich",
                "note": f"Basis annualises to {ann_pct:.1f}%, modestly above funding. Some arbitrage activity is plausible."}
    if ann_pct < -2:
        return {"state": "negative",
                "note": f"Basis is negative ({ann_pct:.1f}% annualised) — futures trading below spot. Genuine selling pressure or hard-to-borrow stock; arbitrage is not the explanation."}
    return {"state": "normal",
            "note": f"Basis annualises to {ann_pct:.1f}%, around fair value. Little arbitrage incentive, so futures positioning is more likely directional."}


def classify_delivery(deliv_pct, avg_deliv_pct=None, std_deliv_pct=None, streak=None):
    """
    Delivery judged against the stock's own distribution, not a fixed multiple.

    A ratio threshold is the wrong instrument. 1.35x its average is roughly one
    standard deviation for a stock whose delivery sits in a tight band, and
    barely half a deviation for one that swings between 20% and 80%. Published
    practice normalises instead — a reading beyond two standard deviations is
    treated as significant whatever the absolute level. That is what this does
    when a standard deviation is available, falling back to the ratio only when
    there is too little history to compute one.

    Also reported: consecutive sessions above average. Practitioners consistently
    warn that a single high day can be a block deal, a promoter pledge or an
    index rebalance, and that a run of three to five sessions is the pattern
    worth attention.
    """
    if deliv_pct is None:
        return {"state": "unknown", "note": "Delivery data unavailable."}

    # NOTE: the caller supplies mean and standard deviation for delivery. That
    # is more defensible here than for open interest — delivery percentage is
    # bounded 0-100 and cannot produce a rollover-sized outlier — but the same
    # caution applies: a block deal can plant one extreme day in the window.
    if avg_deliv_pct and avg_deliv_pct > 0 and std_deliv_pct and std_deliv_pct > 0.5:
        z = (deliv_pct - avg_deliv_pct) / std_deliv_pct
        ratio = deliv_pct / avg_deliv_pct
        streak_txt = (f" It has now run above its average for {streak} consecutive sessions."
                      if streak and streak >= 3 else "")
        base = {"ratio": round(ratio, 2), "z": round(z, 2), "streak": streak}
        if z >= 2.0:
            return {**base, "state": "high",
                    "note": f"Delivery at {deliv_pct:.1f}% is {z:.1f} standard deviations above this stock's own mean of {avg_deliv_pct:.1f}% — significant by any normalised measure.{streak_txt}"}
        if z >= 1.0:
            return {**base, "state": "mildly_high",
                    "note": f"Delivery at {deliv_pct:.1f}% is {z:.1f} standard deviations above this stock's mean of {avg_deliv_pct:.1f}% — raised, but short of the two-deviation mark usually treated as significant.{streak_txt}"}
        if z <= -1.0:
            return {**base, "state": "low",
                    "note": f"Delivery at {deliv_pct:.1f}% is {abs(z):.1f} deviations below this stock's mean — most volume was squared off intraday."}
        return {**base, "state": "normal",
                "note": f"Delivery at {deliv_pct:.1f}% sits within one deviation of this stock's mean of {avg_deliv_pct:.1f}%.{streak_txt}"}

    # not enough history for a deviation — fall back to a ratio, and say so
    if avg_deliv_pct and avg_deliv_pct > 0:
        ratio = deliv_pct / avg_deliv_pct
        note_tail = " (ratio only — too few sessions to compute a deviation)"
        if ratio >= 1.35:
            return {"state": "high", "ratio": round(ratio, 2),
                    "note": f"Delivery at {deliv_pct:.1f}% is {ratio:.2f}x this stock's average.{note_tail}"}
        if ratio >= 1.15:
            return {"state": "mildly_high", "ratio": round(ratio, 2),
                    "note": f"Delivery at {deliv_pct:.1f}% is {ratio:.2f}x this stock's average.{note_tail}"}
        if ratio <= 0.7:
            return {"state": "low", "ratio": round(ratio, 2),
                    "note": f"Delivery at {deliv_pct:.1f}% is {ratio:.2f}x this stock's average.{note_tail}"}
        return {"state": "normal", "ratio": round(ratio, 2),
                "note": f"Delivery at {deliv_pct:.1f}% is close to this stock's average.{note_tail}"}

    # no baseline at all: absolute thresholds, which published sources treat as
    # a rough guide only, since sector norms differ widely
    if deliv_pct >= 65:
        return {"state": "high", "note": f"Delivery at {deliv_pct:.1f}% — high in absolute terms, though no baseline for this stock was available to compare against."}
    if deliv_pct <= 25:
        return {"state": "low", "note": f"Delivery at {deliv_pct:.1f}% — low in absolute terms, no stock-specific baseline available."}
    return {"state": "normal", "note": f"Delivery at {deliv_pct:.1f}%, no stock-specific baseline available."}


def true_positioning(fut_class, basis_cls, deliv_cls, opt_flow=None):
    """
    Combine futures, basis and cash delivery into a reading that survives the
    arbitrage problem.

    The critical case: futures short buildup on a rich basis with strong
    delivery is almost certainly cash-and-carry, and reading it as bearish is
    the single most common misinterpretation of stock futures OI.
    """
    fk = fut_class["kind"]
    b = basis_cls["state"]
    d = deliv_cls["state"]

    R = lambda verdict, conf, note: {"verdict": verdict, "confidence": conf, "note": note}

    # ---- the arbitrage trap ----
    if fk == "short_buildup":
        if b == "rich" and d in ("high", "normal"):
            return R("Likely arbitrage, not bearish", 0.75,
                     "Futures shorts are building while the basis is rich enough to make cash-and-carry profitable, and cash delivery is holding up — the signature of arbitrageurs buying stock and selling futures against it. This is market-neutral flow. Reading it as a bearish signal would be a mistake.")
        if b == "negative":
            # Negative basis has a benign explanation that must be ruled out first:
            # futures holders do not receive dividends, so futures legitimately
            # trade below spot ahead of an ex-date. Strong cash delivery alongside
            # it points that way rather than at selling pressure.
            if d == "mildly_high":
                return R("Bearish, but cash is absorbing", 0.5,
                         "Futures shorts are building on a negative basis, which rules out arbitrage — nobody buys cash and shorts futures at a loss. But delivery is running modestly above this stock's own norm, so someone is taking real stock while others add shorts. The bearish reading stands, with less conviction than it would carry on flat delivery.")
            if d == "high":
                return R("Conflicting — check for a dividend", 0.35,
                         "Futures shorts are building on a negative basis, which usually reads as selling pressure — but cash delivery is unusually strong, which is accumulation behaviour, and the two do not fit together. The most common benign explanation is an upcoming ex-dividend date: futures holders do not receive the dividend, so futures trade below spot ahead of it, with no bearish content at all. Check the corporate actions calendar before treating this as bearish.")
            return R("Genuinely bearish", 0.7,
                     "Futures shorts building with the basis below spot. Nobody arbitrages at a negative carry, so this is real selling pressure or a hard-to-borrow situation. Worth confirming no ex-dividend date falls before expiry, which would explain the negative basis innocently.")
        if d == "high":
            return R("Shorts against real accumulation", 0.45,
                     "Futures shorts are building while cash delivery runs unusually strong. Stock is being taken up in the cash market at the same time as shorts are added — often a hedged or arbitrage-adjacent structure rather than a directional bearish view.")
        if d == "mildly_high":
            return R("Bearish, cash mildly supportive", 0.45,
                     "Futures shorts building while delivery runs a little above this stock's norm. Real stock is being taken up alongside the shorting, which softens the bearish read without reversing it.")
        if d == "low":
            return R("Speculative short", 0.65,
                     "Futures shorts building while cash delivery is weak — positioning is happening in derivatives with little real stock changing hands. Directional and speculative rather than hedged.")
        return R("Bearish, arbitrage not excluded", 0.5,
                 "Futures shorts building with an unremarkable basis. Probably directional, but some hedging or arbitrage cannot be ruled out from this data.")

    # ---- long side ----
    if fk == "long_buildup":
        if d == "high":
            return R("Genuine accumulation", 0.75,
                     "Futures longs building alongside unusually strong cash delivery. Real stock is being taken up rather than positions merely being traded — the more durable version of a bullish signal.")
        if d == "mildly_high":
            return R("Bullish, cash confirming", 0.65,
                     "Futures longs building with delivery modestly above this stock's norm. Some real accumulation behind the positioning, though not the decisive surge that would push confidence higher.")
        if d == "low":
            return R("Speculative long", 0.6,
                     "Futures longs building while cash delivery is weak. The move is being expressed in derivatives with little underlying accumulation, which tends to unwind faster.")
        if b == "rich":
            return R("Bullish, some carry demand", 0.55,
                     "Futures longs with a rich basis. Genuinely bullish, though part of the futures bid may be leveraged carry rather than conviction.")
        return R("Bullish", 0.6, "Futures longs building with nothing in the cash or basis data contradicting it.")

    if fk == "short_covering":
        if d == "high":
            return R("Covering into real demand", 0.65,
                     "Shorts exiting while cash delivery runs strong — buyers are absorbing stock, not just shorts squaring up. More sustainable than a pure squeeze.")
        return R("Short covering", 0.45,
                 "Old shorts leaving rather than new longs arriving. Supportive but self-limiting; it ends when covering does.")

    if fk == "long_unwinding":
        return R("Long unwinding", 0.45,
                 "Longs are exiting. Weak, and often just position squaring rather than a directional view.")

    return R("No clear positioning", 0.2,
             "Nothing in the futures, basis or delivery data forms a coherent picture.")


def index_positioning(fut_class, basis_cls, opt_flow=None):
    """
    Positioning verdict for an INDEX rather than a stock.

    Two things differ and both matter:

    NO DELIVERY DATA. An index is not deliverable, so the cash-market leg that
    separates accumulation from churn simply does not exist. Confidence is
    therefore lower across the board than the equivalent stock verdict, and
    saying so is more useful than quietly reusing the stock thresholds.

    ARBITRAGE IS HARDER BUT REAL. Index arbitrage means trading the full
    constituent basket against the future, which needs scale and execution
    infrastructure. It happens, but it is the preserve of a handful of desks
    rather than the broad, mechanical activity seen in single stocks. A rich
    basis on an index is therefore weaker evidence of arbitrage than the same
    basis on a stock.

    OFFSETTING THIS: index options are far more liquid than stock options, so
    the option-flow half of the analysis is considerably more reliable here.
    """
    fk = fut_class["kind"]
    b = basis_cls["state"]

    R = lambda verdict, conf, note: {"verdict": verdict, "confidence": conf, "note": note}

    if fk == "short_buildup":
        if b == "rich":
            return R("Bearish, index arbitrage possible", 0.5,
                     "Futures shorts building on a rich basis. Index arbitrage — selling futures against the constituent basket — would produce the same pattern, though it takes scale and far fewer desks do it than in single stocks. Directional selling is the more likely reading, but not the only one.")
        if b == "negative":
            return R("Genuinely bearish", 0.7,
                     "Futures shorts building with the basis below spot. Nobody arbitrages at negative carry, so this is directional selling. On an index a negative basis often also signals hedging demand outrunning the natural futures bid.")
        return R("Bearish", 0.6,
                 "Futures shorts building with an unremarkable basis. Directional positioning, though without delivery data the read is softer than the stock equivalent.")

    if fk == "long_buildup":
        if b == "rich":
            return R("Bullish, leveraged carry", 0.55,
                     "Futures longs building with a rich basis — buyers paying up for leveraged exposure rather than owning the basket. Genuinely bullish, but the leverage means it unwinds faster if the move stalls.")
        return R("Bullish", 0.6,
                 "Futures longs building. Without delivery data there is no way to confirm whether real money is behind it, so treat this as positioning rather than accumulation.")

    if fk == "short_covering":
        return R("Short covering", 0.45,
                 "Shorts exiting rather than new longs arriving. Supportive but self-limiting — it stops when the covering does.")

    if fk == "long_unwinding":
        return R("Long unwinding", 0.45,
                 "Longs are exiting. On an index this is often hedge removal or expiry positioning rather than a directional view.")

    return R("No clear positioning", 0.2,
             "Nothing in the futures or basis data forms a coherent picture.")
