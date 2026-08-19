"""
Synthetic index level from put-call parity, and futures basis.

WHY THIS MATTERS AFTER CAS (live since 3 August 2026)

Under the Closing Auction Session, continuous matching in F&O-eligible cash
stocks stops at 3:15pm. The auction collects orders until a random cutoff
between 3:28 and 3:30, matches around 3:35, and equity derivatives now trade
on until 3:40.

The consequence: between 3:15 and 3:35 the Nifty and Bank Nifty values on
screen are effectively FROZEN, because index levels are computed from traded
constituent prices and no continuous trades are happening. Options, however,
keep trading throughout — and they are actively pricing where the auction
will print.

Put-call parity extracts that view:

    Synthetic forward = Call - Put + Strike

For a European index option with no dividend leakage over the period, this is
the market's implied forward level of the index. Comparing it to the stale
spot print tells you what options think the close will be, which is precisely
the information that gets lost when you read premiums against a frozen index.

CAVEATS THAT MATTER
  - Uses last traded prices unless bid/ask are available. A stale LTP on an
    illiquid strike corrupts the synthetic, so strikes are quality-filtered.
  - The synthetic is a FORWARD, not spot. It legitimately differs from spot by
    cost of carry; on expiry day they converge.
  - Wide bid-ask on far strikes makes the synthetic unreliable. Only strikes
    close to the money are used, and each one's spread is reported.
"""

import datetime


def _mid(q):
    """Prefer the bid/ask midpoint over last price — LTP can be minutes stale."""
    depth = (q or {}).get("depth") or {}
    buy = depth.get("buy") or []
    sell = depth.get("sell") or []
    bid = buy[0]["price"] if buy and buy[0].get("price") else None
    ask = sell[0]["price"] if sell and sell[0].get("price") else None
    ltp = q.get("last_price")
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2, bid, ask, round((ask - bid) / ((ask + bid) / 2) * 100, 2)
    return ltp, bid, ask, None


def build_synthetic(spot, calls, puts, max_strikes=9):
    """
    calls/puts: {strike: quote}. Returns per-strike synthetic levels plus a
    consensus figure weighted toward the tightest, most liquid strikes.
    """
    strikes = sorted(set(calls.keys()) & set(puts.keys()))
    if not strikes:
        return None

    # keep the strikes nearest spot — parity degrades badly on wings
    strikes.sort(key=lambda k: abs(k - spot))
    strikes = sorted(strikes[:max_strikes])

    rows = []
    for k in strikes:
        cq, pq = calls[k], puts[k]
        c_mid, c_bid, c_ask, c_spread = _mid(cq)
        p_mid, p_bid, p_ask, p_spread = _mid(pq)
        if not c_mid or not p_mid or c_mid <= 0 or p_mid <= 0:
            continue

        synth = c_mid - p_mid + k
        c_oi = cq.get("oi") or 0
        p_oi = pq.get("oi") or 0
        c_vol = (cq.get("volume") or cq.get("volume_traded") or 0)
        p_vol = (pq.get("volume") or pq.get("volume_traded") or 0)

        # confidence: tight spreads and real volume on BOTH legs
        spread_pen = 0.0
        for sp in (c_spread, p_spread):
            if sp is None:
                spread_pen += 1.5
            else:
                spread_pen += min(sp, 12.0)
        liquidity = min(c_vol, p_vol)
        conf = max(0.05, 1.0 / (1.0 + spread_pen / 4.0)) * (1.0 if liquidity > 0 else 0.35)

        rows.append({
            "strike": k,
            "call": round(c_mid, 2), "put": round(p_mid, 2),
            "call_bid": c_bid, "call_ask": c_ask,
            "put_bid": p_bid, "put_ask": p_ask,
            "call_spread_pct": c_spread, "put_spread_pct": p_spread,
            "synthetic": round(synth, 2),
            "vs_spot": round(synth - spot, 2),
            "vs_spot_pct": round((synth - spot) / spot * 100, 3),
            "call_oi": c_oi, "put_oi": p_oi,
            "call_vol": c_vol, "put_vol": p_vol,
            "atm_distance": round(abs(k - spot), 2),
            "confidence": round(conf, 3),
        })

    if not rows:
        return None

    # consensus: confidence-weighted, extra weight to strikes nearest the money
    tot_w = 0.0
    acc = 0.0
    for r in rows:
        prox = 1.0 / (1.0 + r["atm_distance"] / max(spot * 0.01, 1))
        w = r["confidence"] * prox
        acc += r["synthetic"] * w
        tot_w += w
    consensus = acc / tot_w if tot_w else None

    atm = min(rows, key=lambda r: r["atm_distance"])
    spreads = [r["synthetic"] for r in rows]
    dispersion = round(max(spreads) - min(spreads), 2)

    return {
        "rows": rows,
        "consensus": round(consensus, 2) if consensus else None,
        "consensus_vs_spot": round(consensus - spot, 2) if consensus else None,
        "consensus_vs_spot_pct": round((consensus - spot) / spot * 100, 3) if consensus else None,
        "atm_strike": atm["strike"],
        "atm_synthetic": atm["synthetic"],
        # wide dispersion across strikes means parity is not holding cleanly,
        # usually stale quotes — the consensus should be trusted less
        "dispersion": dispersion,
        "reliable": bool(dispersion < spot * 0.004),
        "strikes_used": len(rows),
    }


def cas_phase(now=None):
    """Where we are in the trading day relative to the CAS windows."""
    now = now or datetime.datetime.now()
    t = now.time()
    def at(h, m):
        return datetime.time(h, m)

    if t < at(9, 15):
        return {"phase": "pre_open", "label": "Before open",
                "note": "Market has not opened. Figures below are from the previous session."}
    if t < at(15, 15):
        return {"phase": "continuous", "label": "Continuous trading",
                "note": "Normal session. Spot updates live, so the synthetic mostly reflects cost of carry rather than any auction expectation."}
    if t < at(15, 30):
        return {"phase": "cas_collection", "label": "CAS order collection",
                "note": "Continuous matching in F&O cash stocks has stopped, so the index on screen is FROZEN. Options are still trading — the synthetic below is the live view of where the close will print. The auction book shuts at a random moment between 3:28 and 3:30."}
    if t < at(15, 36):
        return {"phase": "cas_matching", "label": "CAS matching",
                "note": "The auction is matching. The official close is being determined now; the index shown is still the pre-auction value."}
    if t < at(15, 41):
        return {"phase": "post_auction", "label": "Derivatives open, cash closed",
                "note": "The auction print is out and derivatives trade until 3:40 — this is the window for hedging any basis surprise from the close."}
    return {"phase": "closed", "label": "Market closed",
            "note": "Session over. The synthetic reflects the last traded option prices."}
