"""
Market Analytics Dashboard
Copyright (c) 2026 Amans2bmm. All rights reserved.

Original work: sector breadth, liquidity mapping, weekly EMA scanning,
sector rotation, and F&O positioning analysis built on Kite Connect.

Unauthorised copying, distribution, or derivative use of this file or any
part of this project, in whole or in part, is prohibited without the express
written permission of the copyright holder.
"""

"""
================================================================================
  MARKET ANALYTICS SUITE
  Copyright (c) 2026 Amans2bmm. All rights reserved.

  Original work: research, design and implementation by Amans2bmm.
  Unauthorised copying, redistribution or removal of attribution is prohibited.

  Copyright subsists in this work from the moment of its creation. Attribution
  is asserted here, in every source file, and in the LICENSE accompanying this
  software.
================================================================================
"""

"""
Sector Breadth Tracker — Zerodha Kite Connect
-----------------------------------------------
Run locally, open http://127.0.0.1:5000 in Chrome.

Setup:
  1. pip install -r requirements.txt
  2. credentials.txt in this folder:
         KITE_API_KEY=your_key_here
         KITE_API_SECRET=your_secret_here
  3. Kite app Redirect URL must be exactly: http://127.0.0.1:5000/callback
  4. python app.py

Requires the PAID Kite Connect plan for historical_data().

DEFINITIONS USED (change here if your definition differs):
  HVQ = today's volume is the highest of the last HVQ_SESSIONS sessions (quarter)
  HVY = today's volume is the highest of the last HVY_SESSIONS sessions (year)
"""

import os
import io
import re
import json
import time
import threading
import datetime
from flask import Flask, redirect, request, render_template, jsonify
from kiteconnect import KiteConnect
import pandas as pd
import requests

HVQ_SESSIONS = 63    # ~1 quarter of trading days
HVY_SESSIONS = 252   # ~1 year of trading days
OHL_TOLERANCE = 0.001  # 0.1% — treat open within this of high/low as equal (exact ties are rare)


def load_credentials(path="credentials.txt"):
    creds = {}
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"'{path}' not found. Create it with:\n"
            "KITE_API_KEY=your_key_here\nKITE_API_SECRET=your_secret_here"
        )
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    if "KITE_API_KEY" not in creds or "KITE_API_SECRET" not in creds:
        raise ValueError("credentials.txt must contain KITE_API_KEY and KITE_API_SECRET")
    return creds["KITE_API_KEY"], creds["KITE_API_SECRET"]


API_KEY, API_SECRET = load_credentials()
REDIRECT_PORT = 5000
TOKEN_FILE = "access_token.txt"

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)

SECTOR_INDICES = {
    # ---- Broad market indices ----
    "nifty-50":         {"display": "Nifty 50",          "match": "NIFTY 50",         "nse": "NIFTY 50",
                         "csv": ["ind_nifty50list.csv"], "group": "broad"},
    # Bank Nifty sits with the broad indices: it is the second most-traded index
    # in India and overlaps almost entirely with Pvt Bank + PSU Bank, so counting
    # it as a sector inflated banking's share of turnover.
    "nifty-bank":       {"display": "Nifty Bank",        "match": "NIFTY BANK",       "nse": "NIFTY BANK",
                         "csv": ["ind_niftybanklist.csv"], "group": "broad"},
    "nifty-midcap50":   {"display": "Nifty Midcap 50",   "match": "NIFTY MIDCAP 50",  "nse": "NIFTY MIDCAP 50",
                         "csv": ["ind_niftymidcap50list.csv"], "group": "broad"},
    "nifty-smallcap50": {"display": "Nifty Smallcap 50", "match": "NIFTY SMLCAP 50",  "nse": "NIFTY SMALLCAP 50",
                         "csv": ["ind_niftysmallcap50list.csv"], "group": "broad"},

    # ---- Sector indices ----
    "nifty-auto":       {"display": "Nifty Auto",       "match": "NIFTY AUTO",      "nse": "NIFTY AUTO",              "csv": ["ind_niftyautolist.csv"]},
    "nifty-fmcg":       {"display": "Nifty FMCG",       "match": "NIFTY FMCG",      "nse": "NIFTY FMCG",              "csv": ["ind_niftyfmcglist.csv"]},
    "nifty-it":         {"display": "Nifty IT",         "match": "NIFTY IT",        "nse": "NIFTY IT",                "csv": ["ind_niftyitlist.csv"]},
    "nifty-metal":      {"display": "Nifty Metal",      "match": "NIFTY METAL",     "nse": "NIFTY METAL",             "csv": ["ind_niftymetallist.csv"]},
    "nifty-pharma":     {"display": "Nifty Pharma",     "match": "NIFTY PHARMA",    "nse": "NIFTY PHARMA",            "csv": ["ind_niftypharmalist.csv"]},
    "nifty-realty":     {"display": "Nifty Realty",     "match": "NIFTY REALTY",    "nse": "NIFTY REALTY",            "csv": ["ind_niftyrealtylist.csv"]},
    "nifty-energy":     {"display": "Nifty Energy",     "match": "NIFTY ENERGY",    "nse": "NIFTY ENERGY",            "csv": ["ind_niftyenergylist.csv"]},
    "nifty-psubank":    {"display": "Nifty PSU Bank",   "match": "NIFTY PSU BANK",  "nse": "NIFTY PSU BANK",          "csv": ["ind_niftypsubanklist.csv"]},
    # Nifty Pvt Bank: published CSV filename couldn't be resolved, so the list is
    # pinned here. VERIFY against NSE/Kite after each index rebalance.
    "nifty-pvtbank":    {"display": "Nifty Pvt Bank",   "match": "NIFTY PVT BANK",  "nse": "NIFTY PRIVATE BANK",
                         "csv": ["ind_niftyprivatebanklist.csv", "ind_niftypvtbanklist.csv"],
                         "hardcoded": [
                             "HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK", "INDUSINDBK",
                             "IDFCFIRSTB", "FEDERALBNK", "BANDHANBNK", "RBLBANK", "CITYUNIONBNK",
                         ]},
    "nifty-media":      {"display": "Nifty Media",      "match": "NIFTY MEDIA",     "nse": "NIFTY MEDIA",             "csv": ["ind_niftymedialist.csv"]},
    # Nifty Chemicals: niftyindices.com filename couldn't be resolved, so the list is
    # pinned here. VERIFY against NSE/Kite after each index rebalance and edit as needed.
    "nifty-chemicals":  {"display": "Nifty Chemicals",  "match": "NIFTY CHEMICALS", "nse": "NIFTY CHEMICALS",
                         "csv": ["ind_niftychemicalslist.csv", "ind_niftychemicals_list.csv"],
                         "hardcoded": [
                             "PIDILITIND", "SRF", "SOLARINDS", "LINDEINDIA", "PIIND",
                             "UPL", "AARTIIND", "DEEPAKNTR", "TATACHEM", "ATUL",
                             "NAVINFLUOR", "FLUOROCHEM", "COROMANDEL", "CHAMBLFERT", "EIDPARRY",
                         ]},
    "nifty-healthcare": {"display": "Nifty Healthcare", "match": "NIFTY HEALTHCARE","nse": "NIFTY HEALTHCARE INDEX",  "csv": ["ind_niftyhealthcarelist.csv"]},
    "nifty-oilgas":     {"display": "Nifty Oil & Gas",  "match": "NIFTY OIL",       "nse": "NIFTY OIL & GAS",         "csv": ["ind_niftyoilgaslist.csv"]},
    "nifty-consdur":    {"display": "Nifty Consumer Durables", "match": "NIFTY CONSR DURBL", "nse": "NIFTY CONSUMER DURABLES", "csv": ["ind_niftyconsumerdurableslist.csv", "ind_niftyconsrdurableslist.csv"]},
}

_nse_instruments = None
_equity_token_map = None
_constituent_cache = {}   # sector_key -> (date, [symbols])
_scan_cache = {}          # sector_key -> (timestamp, payload)
SCAN_CACHE_SECONDS = 300  # re-use a scan for 5 minutes instead of re-fetching


def nse_instruments():
    global _nse_instruments
    if _nse_instruments is None:
        _nse_instruments = kite.instruments("NSE")
    return _nse_instruments


def get_index_token(match):
    """Resolve an index instrument token. Exact name match wins over substring,
    so 'NIFTY 50' doesn't accidentally resolve to 'NIFTY 500'."""
    indices = [i for i in nse_instruments() if i.get("segment") == "INDICES"]
    for inst in indices:
        if inst.get("name", "").strip().upper() == match:
            return inst["instrument_token"]
    # fall back to the shortest substring match (closest to the intended index)
    hits = [i for i in indices if match in i.get("name", "").upper()]
    if hits:
        hits.sort(key=lambda i: len(i.get("name", "")))
        return hits[0]["instrument_token"]
    return None


def equity_token_map():
    global _equity_token_map
    if _equity_token_map is None:
        _equity_token_map = {
            i["tradingsymbol"]: i["instrument_token"]
            for i in nse_instruments()
            if i.get("instrument_type") == "EQ"
        }
    return _equity_token_map


# ---------------------------------------------------------------------------
# MCX energy contracts — added 16 Aug 2026
#
# WHY MCX RATHER THAN A CFD FEED
# The volume-zone method is volume-weighted (vol_share is 55 of 100 strength
# points). CFD platforms publish TICK volume — a count of quote updates from
# one broker — not contracts traded. Feeding that in would produce numbers
# that compute but mean something else entirely. MCX is a centralised
# exchange, so its volume is real contracts, and it arrives through the same
# Kite path the rest of this app already trusts.
#
# WHAT THIS IS NOT
# Kite's instrument dump lists only CURRENTLY LISTED contracts — expired ones
# carry no token, so a long continuous back-history cannot be assembled from
# it. This uses the FRONT-MONTH CONTRACT'S OWN HISTORY, unstitched. The
# upside: zero rollover artifacts, unlike a spliced continuous series (gas
# has large seasonal spreads between months, so splices plant phantom
# levels). The cost: depth is limited to that contract's listed life, and
# zones RESET at each roll. Both are stated on the page.
# ---------------------------------------------------------------------------
_mcx_instruments = None

MCX_PREFIX = "MCX-"

MCX_ENERGY = {
    "NATURALGAS": {"label": "Natural Gas", "unit": "MMBtu"},
    "CRUDEOIL":   {"label": "Crude Oil",   "unit": "bbl"},
}


def mcx_instruments():
    global _mcx_instruments
    if _mcx_instruments is None:
        _mcx_instruments = kite.instruments("MCX")
    return _mcx_instruments


def mcx_front_month(name, min_days_to_expiry=3):
    """Nearest non-expired futures contract for an MCX underlying.

    Rolls `min_days_to_expiry` days early: in the last days of a contract
    liquidity migrates to the next month, so the front month stops being the
    place where price is actually discovered.
    """
    today = datetime.date.today()
    cands = []
    for i in mcx_instruments():
        if (i.get("name") or "").upper() != name.upper():
            continue
        if i.get("instrument_type") != "FUT":
            continue
        exp = i.get("expiry")
        if not exp:
            continue
        exp = exp.date() if hasattr(exp, "date") else exp
        if exp < today:
            continue
        cands.append((exp, i))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    for exp, inst in cands:
        if (exp - today).days >= min_days_to_expiry:
            return inst
    return cands[-1][1]        # all near expiry — take the furthest available


def resolve_market_token(symbol):
    """(token, meta) for an NSE equity, an NSE index, or MCX-<NAME> futures.

    Keeps every existing NSE lookup exactly as it was; the MCX branch is
    additive and only fires on the explicit MCX- prefix.
    """
    symbol = (symbol or "").upper().strip()
    if symbol.startswith(MCX_PREFIX):
        name = symbol[len(MCX_PREFIX):]
        inst = mcx_front_month(name)
        if not inst:
            return None, None
        exp = inst.get("expiry")
        exp = exp.date() if hasattr(exp, "date") else exp
        return inst["instrument_token"], {
            "exchange": "MCX",
            "underlying": name,
            "label": (MCX_ENERGY.get(name) or {}).get("label", name),
            "tradingsymbol": inst.get("tradingsymbol"),
            "expiry": str(exp)[:10] if exp else None,
            "days_to_expiry": (exp - datetime.date.today()).days if exp else None,
            "lot_size": inst.get("lot_size"),
            "contract_only": True,
        }
    tok = equity_token_map().get(symbol)
    if tok:
        return tok, {"exchange": "NSE", "underlying": symbol}
    tok = get_index_token(symbol)
    if tok:
        return tok, {"exchange": "NSE", "underlying": symbol, "is_index": True}
    return None, None


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def _from_niftyindices(cfg):
    """Try niftyindices.com constituent CSVs. Returns (symbols, note) or raises."""
    errs = []
    for slug in cfg["csv"]:
        url = f"https://niftyindices.com/IndexConstituent/{slug}"
        try:
            r = requests.get(url, headers=BROWSER_HEADERS, timeout=12)
            if r.status_code != 200:
                errs.append(f"{slug}: HTTP {r.status_code}")
                continue
            head = r.text[:200].lstrip().lower()
            if head.startswith("<!doctype") or head.startswith("<html"):
                errs.append(f"{slug}: got a web page, not a CSV (wrong filename)")
                continue
            df = pd.read_csv(io.StringIO(r.text))
            col = next((c for c in df.columns if c.strip().lower() == "symbol"), None)
            if col is None:
                errs.append(f"{slug}: no Symbol column (columns: {list(df.columns)[:4]})")
                continue
            syms = df[col].dropna().astype(str).str.strip().tolist()
            if syms:
                return syms, f"niftyindices.com/{slug}"
            errs.append(f"{slug}: empty")
        except Exception as e:
            errs.append(f"{slug}: {e}")
    raise RuntimeError("; ".join(errs) if errs else "no candidates")


def _from_nse_api(cfg):
    """Fallback: NSE's own equity-stockIndices endpoint. Needs a primed session cookie."""
    index_name = cfg.get("nse")
    if not index_name:
        raise RuntimeError("no NSE index name configured")
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    # prime cookies — NSE rejects bare API calls
    s.get("https://www.nseindia.com", timeout=12)
    time.sleep(0.6)
    r = s.get("https://www.nseindia.com/api/equity-stockIndices",
              params={"index": index_name},
              headers={"Referer": "https://www.nseindia.com/market-data/live-equity-market"},
              timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"NSE API HTTP {r.status_code} for '{index_name}'")
    data = r.json()
    syms = [d["symbol"].strip() for d in data.get("data", [])
            if d.get("symbol") and d["symbol"].strip().upper() != index_name.upper()]
    if not syms:
        raise RuntimeError(f"NSE API returned no rows for '{index_name}'")
    return syms, f"nseindia.com ({index_name})"


def fetch_constituents(sector_key):
    """Constituent symbols for a sector, cached for the day. Tries niftyindices, then NSE API."""
    today = datetime.date.today().isoformat()
    cached = _constituent_cache.get(sector_key)
    if cached and cached[0] == today:
        return cached[1]

    cfg = SECTOR_INDICES[sector_key]

    # A pinned list always wins — used for indices whose published CSV can't be reached.
    if cfg.get("hardcoded"):
        syms = list(cfg["hardcoded"])
        _constituent_cache[sector_key] = (today, syms)
        print(f"[constituents] {sector_key}: {len(syms)} symbols from hardcoded list")
        return syms

    problems = []

    for source in (_from_niftyindices, _from_nse_api):
        try:
            syms, note = source(cfg)
            _constituent_cache[sector_key] = (today, syms)
            print(f"[constituents] {sector_key}: {len(syms)} symbols via {note}")
            return syms
        except Exception as e:
            problems.append(f"{source.__name__.replace('_from_', '')} -> {e}")

    raise RuntimeError(" | ".join(problems))


# ---------------- Auth ----------------

def get_saved_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            d, t = f.read().strip().split("|", 1)
            if d == datetime.date.today().isoformat():
                return t
    return None


def save_token(token):
    with open(TOKEN_FILE, "w") as f:
        f.write(f"{datetime.date.today().isoformat()}|{token}")


def require_auth():
    t = get_saved_token()
    if t:
        kite.set_access_token(t)
    return t


@app.route("/")
def home():
    if not require_auth():
        return redirect("/login")
    return render_template("index.html")


@app.route("/stocks/<sector_key>")
def stocks_page(sector_key):
    if sector_key not in SECTOR_INDICES:
        return "Unknown sector", 404
    if not require_auth():
        return redirect("/login")
    return render_template("stocks.html",
                           sector_key=sector_key,
                           sector_name=SECTOR_INDICES[sector_key]["display"])


@app.route("/login")
def login():
    return redirect(kite.login_url())


@app.route("/callback")
def callback():
    rt = request.args.get("request_token")
    if not rt:
        return "Login failed or cancelled. <a href='/login'>Try again</a>", 400
    data = kite.generate_session(rt, api_secret=API_SECRET)
    save_token(data["access_token"])
    return redirect("/")


# ---------------- Sector breadth (index vs EMA) ----------------

ALLOWED_SPANS = [10, 21, 50, 100, 200]


@app.route("/api/breadth")
def api_breadth():
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    span = request.args.get("span", default=200, type=int)
    if span not in ALLOWED_SPANS:
        span = 200

    to_date = datetime.date.today()
    # generous calendar lookback — ~250 trading days per 365 calendar days
    from_date = to_date - datetime.timedelta(days=max(int(span * 1.7), 400))

    MIN_SESSIONS = 30  # below this an EMA is meaningless regardless of span

    results = []
    for key, cfg in SECTOR_INDICES.items():
        try:
            tok = get_index_token(cfg["match"])
            if not tok:
                results.append({"key": key, "name": cfg["display"], "group": cfg.get("group", "sector"),
                                "error": "index not found on Kite"})
                continue
            candles = kite.historical_data(tok, from_date, to_date, "day")
            if len(candles) < MIN_SESSIONS:
                results.append({"key": key, "name": cfg["display"], "group": cfg.get("group", "sector"),
                                "error": f"only {len(candles)} sessions of history"})
                continue
            closes = pd.Series([c["close"] for c in candles])
            ema = closes.ewm(span=span, adjust=False).mean().iloc[-1]
            ltp = closes.iloc[-1]
            results.append({
                "key": key, "name": cfg["display"],
                "group": cfg.get("group", "sector"),
                "ltp": round(float(ltp), 2),
                "ema": round(float(ema), 2),
                "pct_diff": round(float((ltp - ema) / ema * 100), 2),
                "above": bool(ltp >= ema),
                "sessions": len(candles),
                "partial": bool(len(candles) < span),
            })
        except Exception as e:
            results.append({"key": key, "name": cfg["display"], "group": cfg.get("group", "sector"),
                            "error": str(e)})

    return jsonify({
        "as_of": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "span": span,
        "sectors": results,
    })


# ---------------- Sector turnover (where the money is today) ----------------

@app.route("/api/sector-volume")
def api_sector_volume():
    """Today's traded value per sector = sum(volume x price) across its constituents."""
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        return _sector_volume()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


def _sector_volume():
    sector_symbols = {}
    all_symbols = set()
    constituent_errors = []
    # Only sector indices — broad indices overlap them and would double-count turnover
    keys = [k for k, c in SECTOR_INDICES.items() if c.get("group") != "broad"]
    for key in keys:
        try:
            syms = fetch_constituents(key)
            sector_symbols[key] = syms
            all_symbols.update(syms)
        except Exception as e:
            sector_symbols[key] = []
            constituent_errors.append(f"{key}: {e}")

    tmap = equity_token_map()
    valid = sorted(s for s in all_symbols if s in tmap)

    if not valid:
        return jsonify({
            "error": "No constituent symbols resolved. "
                     "niftyindices.com may be unreachable from this machine. "
                     + (" | ".join(constituent_errors[:3]) if constituent_errors else "")
        }), 502

    quotes = {}
    quote_errors = []
    BATCH = 200
    for i in range(0, len(valid), BATCH):
        chunk = [f"NSE:{s}" for s in valid[i:i + BATCH]]
        try:
            quotes.update(kite.quote(chunk))
        except Exception as e:
            quote_errors.append(str(e))
        time.sleep(0.4)

    if not quotes:
        return jsonify({"error": "Kite quote() returned nothing. "
                                 + (quote_errors[0] if quote_errors else "")}), 502

    # Live volume reads zero before the open and on holidays, which produces an
    # empty panel rather than the previous session's picture. Fall back to the
    # last published bhavcopy so the page still shows something meaningful.
    live_cr = {}
    for sym in valid:
        q = quotes.get(f"NSE:{sym}")
        if not q:
            continue
        vol = q.get("volume") or q.get("volume_traded") or 0
        price = q.get("last_price") or 0
        if vol and price:
            live_cr[sym] = float(vol) * float(price) / 1e7

    source, fb_day = "live", None
    fallback = {}
    if len(live_cr) < max(50, len(valid) * 0.2):
        fallback, fb_day = _bhavcopy_turnover()
        source = f"last session ({fb_day})" if fallback else "unavailable"

    def turnover_of(sym):
        cr = live_cr.get(sym)
        if cr is None:
            cr = fallback.get(sym.upper())
        return (cr or 0.0) * 1e7

    rows = []
    for key in keys:
        cfg = SECTOR_INDICES[key]
        syms = sector_symbols.get(key, [])
        total = sum(turnover_of(s) for s in syms)
        rows.append({
            "key": key,
            "name": cfg["display"],
            "turnover": total,
            "turnover_cr": round(total / 1e7, 1),
            "stocks": len(syms),
        })

    grand = sum(r["turnover"] for r in rows) or 1
    for r in rows:
        r["share"] = round(r["turnover"] / grand * 100, 1)
    rows.sort(key=lambda r: r["turnover"], reverse=True)

    now = datetime.datetime.now()
    return jsonify({
        "as_of": now.strftime("%d %b %Y, %I:%M %p"),
        "sectors": rows,
        "warnings": constituent_errors,
        "source": source,
        "data_date": fb_day,
        "is_live": source == "live",
        "market_open": bool(now.weekday() < 5
                            and datetime.time(9, 15) <= now.time() <= datetime.time(15, 40)),
    })


# ---------------- Stock-level quality scan ----------------

def analyse_stock(symbol, candles):
    closes = pd.Series([c["close"] for c in candles])
    vols   = pd.Series([c["volume"] for c in candles])
    last   = candles[-1]

    o, h, l, c = last["open"], last["high"], last["low"], last["close"]
    today_vol = float(last["volume"])

    ema200 = closes.ewm(span=200, adjust=False).mean().iloc[-1] if len(closes) >= 200 else None
    sma20  = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else None

    prior_q = vols.iloc[-(HVQ_SESSIONS + 1):-1]
    prior_y = vols.iloc[-(HVY_SESSIONS + 1):-1]
    hvq = bool(len(prior_q) >= 20 and today_vol >= prior_q.max())
    hvy = bool(len(prior_y) >= 100 and today_vol >= prior_y.max())

    avg20_vol = float(vols.iloc[-21:-1].mean()) if len(vols) >= 21 else None
    vol_ratio = round(today_vol / avg20_vol, 2) if avg20_vol else None

    open_eq_low  = bool(h > l and abs(o - l) / max(l, 1e-9) <= OHL_TOLERANCE)
    open_eq_high = bool(h > l and abs(o - h) / max(h, 1e-9) <= OHL_TOLERANCE)

    above200 = bool(ema200 is not None and c >= ema200)
    above20  = bool(sma20 is not None and c >= sma20)

    # ---- quality score: volume weighted heaviest ----
    score = 0
    if hvy:
        score += 50
    elif hvq:
        score += 35
    elif vol_ratio:
        score += min(20, max(0, (vol_ratio - 1) * 20))  # up to +20 for volume expansion

    if open_eq_low:
        score += 15   # opened at low, closed higher — buyers in control
    if open_eq_high:
        score -= 15   # opened at high — sellers in control

    if above200:
        score += 20
    if above20:
        score += 15

    return {
        "symbol": symbol,
        "ltp": round(float(c), 2),
        "chg_pct": round(float((c - candles[-2]["close"]) / candles[-2]["close"] * 100), 2) if len(candles) >= 2 else None,
        "volume": int(today_vol),
        "vol_ratio": vol_ratio,
        "hvq": hvq,
        "hvy": hvy,
        "open_eq_low": open_eq_low,
        "open_eq_high": open_eq_high,
        "ema200": round(float(ema200), 2) if ema200 is not None else None,
        "above200": above200,
        "sma20": round(float(sma20), 2) if sma20 is not None else None,
        "above20": above20,
        "score": round(max(0, min(100, score))),
    }


@app.route("/api/stocks/<sector_key>")
def api_stocks(sector_key):
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    if sector_key not in SECTOR_INDICES:
        return jsonify({"error": "unknown sector"}), 404

    # Serve a recent scan instantly unless the user forced a refresh
    force = request.args.get("force") == "1"
    cached = _scan_cache.get(sector_key)
    if cached and not force and (time.time() - cached[0]) < SCAN_CACHE_SECONDS:
        payload = dict(cached[1])
        payload["cached"] = True
        payload["cache_age_sec"] = int(time.time() - cached[0])
        return jsonify(payload)

    try:
        symbols = fetch_constituents(sector_key)
    except Exception as e:
        return jsonify({"error": f"Could not fetch constituent list: {e}"}), 502

    tmap = equity_token_map()
    fno = fno_symbols()
    to_date = datetime.date.today()
    from_date = to_date - datetime.timedelta(days=500)  # enough for 200 EMA + 252-session volume window

    stocks = []
    for sym in symbols:
        try:
            tok = tmap.get(sym)
            if not tok:
                stocks.append({"symbol": sym, "error": "not found on Kite",
                               "fno": bool(sym.upper() in fno)})
                continue
            candles = kite.historical_data(tok, from_date, to_date, "day")
            if len(candles) < 25:
                stocks.append({"symbol": sym, "error": "insufficient history",
                               "fno": bool(sym.upper() in fno)})
                continue
            row = analyse_stock(sym, candles)
            row["fno"] = bool(sym.upper() in fno)
            stocks.append(row)
            time.sleep(0.35)  # stay under Kite's historical-data rate limit
        except Exception as e:
            stocks.append({"symbol": sym, "error": str(e)})

    valid = [s for s in stocks if "error" not in s]
    summary = {
        "total": len(valid),
        "hvq": sum(1 for s in valid if s["hvq"]),
        "hvy": sum(1 for s in valid if s["hvy"]),
        "above200": sum(1 for s in valid if s["above200"]),
        "above20": sum(1 for s in valid if s["above20"]),
        "open_eq_low": sum(1 for s in valid if s["open_eq_low"]),
        "open_eq_high": sum(1 for s in valid if s["open_eq_high"]),
        "fno": sum(1 for s in valid if s.get("fno")),
    }

    stocks.sort(key=lambda s: s.get("score", -1), reverse=True)

    payload = {
        "sector": SECTOR_INDICES[sector_key]["display"],
        "as_of": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "summary": summary,
        "stocks": stocks,
        "cached": False,
    }
    _scan_cache[sector_key] = (time.time(), payload)
    return jsonify(payload)


def find_fair_value_gaps(candles, max_age=120, min_gap_pct=0.15):
    """
    Fair Value Gap (imbalance): a 3-candle sequence where price moved so fast
    that a band of prices never traded on both sides.

      Bullish FVG (left behind in an UP move): low[i] > high[i-2]
      Bearish FVG (left behind in a DOWN move): high[i] < low[i-2]

    A gap is 'unfilled' until later price action trades back through it.
    We track how much has been eaten into and report what remains.
    """
    gaps = []
    n = len(candles)
    start = max(2, n - max_age)

    for i in range(start, n):
        c0, c2 = candles[i - 2], candles[i]

        # bullish gap — created on the way up, sits BELOW current price
        if c2["low"] > c0["high"]:
            lo, hi, side = c0["high"], c2["low"], "bullish"
        # bearish gap — created on the way down, sits ABOVE current price
        elif c2["high"] < c0["low"]:
            lo, hi, side = c2["high"], c0["low"], "bearish"
        else:
            continue

        mid = (lo + hi) / 2
        size_pct = (hi - lo) / mid * 100
        if size_pct < min_gap_pct:
            continue  # too thin to be meaningful

        # how far later candles pushed back into the gap
        remaining_lo, remaining_hi = lo, hi
        filled = False
        for j in range(i + 1, n):
            cj = candles[j]
            if side == "bullish":
                # price coming back down eats the gap from the top
                if cj["low"] < remaining_hi:
                    remaining_hi = max(cj["low"], remaining_lo)
            else:
                # price coming back up eats the gap from the bottom
                if cj["high"] > remaining_lo:
                    remaining_lo = min(cj["high"], remaining_hi)
            if remaining_hi - remaining_lo <= 0:
                filled = True
                break

        if filled:
            continue

        remaining_pct = (remaining_hi - remaining_lo) / mid * 100
        if remaining_pct < min_gap_pct * 0.5:
            continue

        gaps.append({
            "type": "FVG",
            "side": side,
            "low": round(remaining_lo, 2),
            "high": round(remaining_hi, 2),
            "original_low": round(lo, 2),
            "original_high": round(hi, 2),
            "size_pct": round(remaining_pct, 2),
            "age_sessions": n - 1 - i,
            "date": str(candles[i]["date"])[:16],
            "partially_filled": bool(remaining_lo != lo or remaining_hi != hi),
        })
    return gaps


def find_swing_liquidity(candles, lookback=3, max_age=250, eq_tolerance=0.0015):
    """
    Untested swing highs = buy-side liquidity (stops of shorts rest above).
    Untested swing lows  = sell-side liquidity (stops of longs rest below).
    Two swings at nearly the same level ('equal highs/lows') are a stronger pool.
    """
    n = len(candles)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    start = max(lookback, n - max_age)

    swing_highs, swing_lows = [], []
    for i in range(start, n - lookback):
        window = range(i - lookback, i + lookback + 1)
        if all(highs[i] >= highs[j] for j in window) and highs[i] > highs[i - 1]:
            swing_highs.append((i, highs[i]))
        if all(lows[i] <= lows[j] for j in window) and lows[i] < lows[i - 1]:
            swing_lows.append((i, lows[i]))

    def untested(swings, kind):
        out = []
        for idx, level in swings:
            later = candles[idx + 1:]
            if kind == "high":
                if any(c["high"] > level for c in later):
                    continue  # already swept
            else:
                if any(c["low"] < level for c in later):
                    continue
            out.append({
                "type": "SWING",
                "side": "buy-side" if kind == "high" else "sell-side",
                "level": round(level, 2),
                "age_sessions": n - 1 - idx,
                "date": str(candles[idx]["date"])[:10],
                "equal_count": 1,
            })
        return out

    pools = untested(swing_highs, "high") + untested(swing_lows, "low")

    # merge near-identical levels on the same side into one stronger pool
    merged = []
    for p in sorted(pools, key=lambda x: (x["side"], x["level"])):
        if merged and merged[-1]["side"] == p["side"] and \
           abs(merged[-1]["level"] - p["level"]) / p["level"] <= eq_tolerance:
            merged[-1]["equal_count"] += 1
            merged[-1]["age_sessions"] = max(merged[-1]["age_sessions"], p["age_sessions"])
        else:
            merged.append(p)
    return merged


def volume_profile_nodes(candles, bins=40, lookback=180):
    """
    Bin the price range and distribute each session's volume across the bins its
    range covers. Low-volume bins = prices the market passed through and never
    accepted; price tends to move through them quickly on a revisit.
    """
    data = candles[-lookback:]
    if len(data) < 20:
        return []

    lo = min(c["low"] for c in data)
    hi = max(c["high"] for c in data)
    if hi <= lo:
        return []

    width = (hi - lo) / bins
    buckets = [0.0] * bins

    for c in data:
        c_lo, c_hi, vol = c["low"], c["high"], float(c["volume"])
        first = max(0, int((c_lo - lo) / width))
        last = min(bins - 1, int((c_hi - lo) / width))
        spread = max(1, last - first + 1)
        for b in range(first, last + 1):
            buckets[b] += vol / spread   # spread volume evenly across the candle's range

    total = sum(buckets) or 1
    avg = total / bins

    nodes = []
    for b, v in enumerate(buckets):
        share = v / avg
        if share < 0.45:               # thin zone
            nodes.append({
                "type": "LVN",
                "low": round(lo + b * width, 2),
                "high": round(lo + (b + 1) * width, 2),
                "rel_volume": round(share, 2),
            })

    # merge adjacent thin bins into single zones
    merged = []
    for nd in nodes:
        if merged and abs(merged[-1]["high"] - nd["low"]) < width * 0.01:
            merged[-1]["high"] = nd["high"]
            merged[-1]["rel_volume"] = round(min(merged[-1]["rel_volume"], nd["rel_volume"]), 2)
        else:
            merged.append(dict(nd))
    return merged


def find_high_volume_nodes(candles, top_pct=0.06, lookback=250,
                           min_sep_pct=0.25, max_width_pct=3.0):
    """
    High Volume Nodes: the price ranges of the heaviest-volume candles.
    Heavy volume means large positions changed hands there; those participants
    tend to defend the level, so price often reacts on a revisit.

    Merging is deliberately tight and width-capped: a 15%-wide "zone" spanning
    current price is not actionable, so candidates that would exceed
    max_width_pct are kept as separate narrower zones instead of one blob.
    """
    data = candles[-lookback:]
    if len(data) < 30:
        return []

    vols = sorted((float(c["volume"]) for c in data), reverse=True)
    cutoff = vols[max(0, int(len(vols) * top_pct) - 1)]
    avg_vol = sum(float(x["volume"]) for x in data) / len(data)

    raw = []
    for i, c in enumerate(data):
        if float(c["volume"]) < cutoff:
            continue
        lo, hi = c["low"], c["high"]
        mid = (lo + hi) / 2
        revisits = sum(1 for later in data[i + 1:] if later["low"] <= hi and later["high"] >= lo)
        raw.append({
            "type": "HVN",
            "low": lo, "high": hi, "mid": mid,
            "volume": int(c["volume"]),
            "vol_rank": round(float(c["volume"]) / avg_vol, 1),
            "age_sessions": len(data) - 1 - i,
            "date": str(c["date"])[:16],
            "revisits": revisits,
            "stacked": 1,
        })

    raw.sort(key=lambda z: z["mid"])
    merged = []
    for z in raw:
        if merged:
            m = merged[-1]
            gap_pct = (z["low"] - m["high"]) / max(z["mid"], 1e-9) * 100
            new_low, new_high = min(m["low"], z["low"]), max(m["high"], z["high"])
            new_width = (new_high - new_low) / max((new_high + new_low) / 2, 1e-9) * 100
            # only merge when they're genuinely adjacent AND the result stays narrow
            if gap_pct <= min_sep_pct and new_width <= max_width_pct:
                m["low"], m["high"] = new_low, new_high
                m["mid"] = (new_low + new_high) / 2
                m["volume"] += z["volume"]
                m["vol_rank"] = max(m["vol_rank"], z["vol_rank"])
                m["stacked"] += 1
                m["age_sessions"] = min(m["age_sessions"], z["age_sessions"])
                m["revisits"] = max(m["revisits"], z["revisits"])
                continue
        merged.append(dict(z))

    # drop anything still too wide to trade against
    out = []
    for m in merged:
        width = (m["high"] - m["low"]) / max(m["mid"], 1e-9) * 100
        if width > max_width_pct:
            continue
        m["low"] = round(m["low"], 2)
        m["high"] = round(m["high"], 2)
        m["mid"] = round(m["mid"], 2)
        m["width_pct"] = round(width, 2)
        out.append(m)

    # keep the heaviest zones, not every qualifying candle
    out.sort(key=lambda z: -(z["vol_rank"] * (1 + 0.15 * (z["stacked"] - 1))))
    return out[:8]


def count_touches(candles, level, tolerance_pct=0.25, lookback=250):
    """
    How many separate times price came to a level and turned away from it.
    A level tested four times is a far more crowded pool than one tested once.
    Consecutive bars at the level count as a single touch, not many.
    """
    data = candles[-lookback:]
    tol = level * tolerance_pct / 100
    touches, in_touch = 0, False
    for c in data:
        near = (c["low"] - tol) <= level <= (c["high"] + tol)
        if near and not in_touch:
            touches += 1
            in_touch = True
        elif not near:
            in_touch = False
    return touches


def excursion_probability(candles, target_pct, direction, horizon=20):
    """
    Empirical base rate, measured on this stock's own history:
    across every past window of `horizon` bars, how often did price travel
    at least `target_pct` in `direction` from where it closed?

    This is NOT a prediction. It's the historical frequency of a move of this
    size over this horizon — driven mostly by distance and volatility.
    """
    n = len(candles)
    if n < horizon + 30:
        return None

    hits = total = 0
    for i in range(n - horizon - 1):
        base = candles[i]["close"]
        window = candles[i + 1:i + 1 + horizon]
        if direction == "above":
            excursion = (max(c["high"] for c in window) - base) / base * 100
        else:
            excursion = (base - min(c["low"] for c in window)) / base * 100
        total += 1
        if excursion >= abs(target_pct):
            hits += 1
    return round(hits / total * 100) if total else None


def score_zone(z, candles):
    """
    Strength = how many independent things point at this price.
    Deliberately kept transparent: each factor is listed so you can see
    what earned the score rather than trusting a black box.
    """
    factors = []
    score = 0

    if z["type"] == "FVG":
        score += 20
        factors.append(f"unfilled gap ({z['size_pct']}% wide)")
        if z["size_pct"] >= 1.0:
            score += 10; factors.append("large imbalance")
        if not z.get("partially_filled"):
            score += 8; factors.append("untouched")

    if z["type"] == "SWING":
        score += 18
        eq = z.get("equal_count", 1)
        if eq > 1:
            score += 12 * min(eq - 1, 3)
            factors.append(f"{eq} equal levels — stacked stops")
        else:
            factors.append("untested swing")
        touches = z.get("touches", 0)
        if touches >= 3:
            score += 10; factors.append(f"tested {touches}x")

    if z["type"] == "HVN":
        score += 22
        factors.append(f"heavy volume node ({z['vol_rank']}x avg)")
        if z.get("stacked", 1) > 1:
            score += 8; factors.append(f"{z['stacked']} high-volume candles stacked")
        if z.get("revisits", 0) >= 3:
            score += 8; factors.append(f"revisited {z['revisits']}x")

    if z["type"] == "LVN":
        score += 10
        factors.append("thin zone — price travels fast through")

    if z["type"] == "VA":
        score += 16
        factors.append("volume profile level")

    # recency: fresh structure matters more than stale structure
    age = z.get("age_sessions")
    if age is not None:
        if age <= 20:   score += 10; factors.append("recent")
        elif age <= 60: score += 5
        elif age > 150: score -= 5;  factors.append("stale")

    return max(0, score), factors


def merge_confluence(zones, tolerance_pct=0.5, max_width_pct=4.0):
    """
    When zones of different types land on the same price, that's confluence —
    the strongest signal available here. Merge them, combine their factors,
    but refuse merges that would produce a band too wide to act on.
    """
    if not zones:
        return []
    zones = sorted(zones, key=lambda z: z["mid"])
    out = [dict(zones[0])]
    out[0]["types"] = [out[0]["type"]]
    for z in zones[1:]:
        prev = out[-1]
        close_enough = abs(z["mid"] - prev["mid"]) / max(prev["mid"], 1e-9) * 100 <= tolerance_pct
        new_low = min(prev.get("low", prev["mid"]), z.get("low", z["mid"]))
        new_high = max(prev.get("high", prev["mid"]), z.get("high", z["mid"]))
        new_width = (new_high - new_low) / max((new_high + new_low) / 2, 1e-9) * 100
        if close_enough and new_width <= max_width_pct:
            prev["low"], prev["high"] = new_low, new_high
            prev["mid"] = (new_low + new_high) / 2
            prev["strength"] = prev["strength"] + z["strength"] * 0.7
            prev["factors"] = prev["factors"] + z["factors"]
            prev["types"] = sorted(set(prev["types"] + [z["type"]]))
        else:
            z = dict(z); z["types"] = [z["type"]]
            out.append(z)

    for z in out:
        z["confluence"] = len(set(z["types"]))
        # collapse repeated factors from merged zones, keeping the strongest
        # count where the same factor appears with different numbers
        seen, deduped = {}, []
        for f in z["factors"]:
            key = re.sub(r"\d+", "#", f)          # "tested 7x" and "tested 8x" share a key
            if key in seen:
                # keep whichever mentions the larger number
                nums_new = [int(n) for n in re.findall(r"\d+", f)]
                nums_old = [int(n) for n in re.findall(r"\d+", deduped[seen[key]])]
                if nums_new and nums_old and max(nums_new) > max(nums_old):
                    deduped[seen[key]] = f
                continue
            seen[key] = len(deduped)
            deduped.append(f)
        z["factors"] = deduped

        if z["confluence"] > 1:
            z["strength"] += 15 * (z["confluence"] - 1)
            z["factors"].insert(0, f"confluence of {z['confluence']} zone types")
    return out


def assemble_zones(symbol, candles, gaps, pools, lvns, hvns=None, horizon=20):
    ltp = candles[-1]["close"]
    hvns = hvns or []

    def dist(level):
        return round((level - ltp) / ltp * 100, 2)

    raw = []
    for g in gaps:
        raw.append({**g, "mid": (g["low"] + g["high"]) / 2})
    for p in pools:
        lvl = p["level"]
        raw.append({**p, "low": lvl, "high": lvl, "mid": lvl,
                    "touches": count_touches(candles, lvl)})
    for nd in lvns:
        raw.append({**nd, "mid": (nd["low"] + nd["high"]) / 2, "side": "thin"})
    for h in hvns:
        raw.append({**h, "side": "volume"})

    for z in raw:
        z["strength"], z["factors"] = score_zone(z, candles)

    merged = merge_confluence(raw)

    for z in merged:
        z["mid"] = round(z["mid"], 2)
        z["low"] = round(z.get("low", z["mid"]), 2)
        z["high"] = round(z.get("high", z["mid"]), 2)
        z["width_pct"] = round((z["high"] - z["low"]) / max(z["mid"], 1e-9) * 100, 2)
        z["dist_pct"] = dist(z["mid"])
        z["direction"] = "above" if z["mid"] > ltp else "below"
        # a band containing current price isn't a target — price is already in it
        z["straddles"] = bool(z["low"] <= ltp <= z["high"] and z["width_pct"] > 0.05)
        z["strength"] = round(min(100, z["strength"]))
        if z["straddles"]:
            z["factors"].insert(0, "price already inside this zone")
        z["base_rate"] = excursion_probability(candles, z["dist_pct"], z["direction"], horizon)
        z["rank"] = round((z["base_rate"] or 0) / 100 * z["strength"], 1)
        if z["straddles"]:
            z["rank"] *= 0.4   # de-prioritise; it's context, not a target
        z["factors"] = z["factors"][:5]

    above = sorted([z for z in merged if z["direction"] == "above"], key=lambda z: -z["rank"])
    below = sorted([z for z in merged if z["direction"] == "below"], key=lambda z: -z["rank"])

    return {
        "symbol": symbol,
        "ltp": round(float(ltp), 2),
        "horizon": horizon,
        "zones_above": above[:10],
        "zones_below": below[:10],
        "top_above": above[0] if above else None,
        "top_below": below[0] if below else None,
        "counts": {
            "fvg_up": sum(1 for g in gaps if g["side"] == "bullish"),
            "fvg_down": sum(1 for g in gaps if g["side"] == "bearish"),
            "buyside_pools": sum(1 for p in pools if p["side"] == "buy-side"),
            "sellside_pools": sum(1 for p in pools if p["side"] == "sell-side"),
            "hvn_zones": len(hvns),
            "thin_zones": len(lvns),
        },
    }


def execution_liquidity(candles, lookback=60):
    """
    How institutions actually measure liquidity: not 'where are the stops',
    but 'how much can I trade before I move the price against myself'.
    """
    data = candles[-lookback:]
    if len(data) < 20:
        return None

    closes = [c["close"] for c in data]
    highs  = [c["high"] for c in data]
    lows   = [c["low"] for c in data]
    vols   = [float(c["volume"]) for c in data]

    # --- Average daily traded value (the basic capacity number) ---
    turnovers = [v * c for v, c in zip(vols, closes)]
    adv_value = sum(turnovers) / len(turnovers)

    # --- Amihud illiquidity: |return| per rupee traded ---
    # Scaled to "basis points of price move per ₹1 crore traded"
    impacts = []
    for i in range(1, len(data)):
        if turnovers[i] <= 0:
            continue
        ret = abs(closes[i] - closes[i - 1]) / closes[i - 1]
        impacts.append(ret / (turnovers[i] / 1e7))
    amihud_bps_per_cr = (sum(impacts) / len(impacts) * 10000) if impacts else None

    # --- Corwin-Schultz spread estimator from daily high/low ranges ---
    # Recovers an implied bid-ask spread without needing tick data.
    import math
    spreads = []
    for i in range(1, len(data)):
        h1, l1 = highs[i - 1], lows[i - 1]
        h2, l2 = highs[i], lows[i]
        if min(l1, l2) <= 0:
            continue
        beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
        hi2, lo2 = max(h1, h2), min(l1, l2)
        gamma = math.log(hi2 / lo2) ** 2
        denom = 3 - 2 * math.sqrt(2)
        alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / denom - math.sqrt(gamma / denom)
        s = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
        if s > 0:
            spreads.append(s)
    spread_pct = (sum(spreads) / len(spreads) * 100) if spreads else None

    # --- Capacity: rupee size tradeable at a given participation rate ---
    # 10% of ADV is a common single-day ceiling before impact becomes material.
    capacity_10pct = adv_value * 0.10

    # --- Realised volatility, for context on impact ---
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    mean_r = sum(rets) / len(rets)
    vol_daily = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5
    vol_annual_pct = vol_daily * (252 ** 0.5) * 100

    # --- Liquidity tier, judged on traded value ---
    cr = adv_value / 1e7
    if cr >= 500:   tier, tier_note = "Very high", "institutional size trades easily"
    elif cr >= 100: tier, tier_note = "High", "comfortable for most position sizes"
    elif cr >= 25:  tier, tier_note = "Moderate", "scale in and out, avoid market orders"
    elif cr >= 5:   tier, tier_note = "Thin", "use limits, expect slippage"
    else:           tier, tier_note = "Illiquid", "hard to exit in size"

    return {
        "adv_cr": round(adv_value / 1e7, 1),
        "capacity_10pct_cr": round(capacity_10pct / 1e7, 2),
        "amihud": round(amihud_bps_per_cr, 2) if amihud_bps_per_cr is not None else None,
        "spread_pct": round(spread_pct, 3) if spread_pct is not None else None,
        "vol_annual_pct": round(vol_annual_pct, 1),
        "tier": tier,
        "tier_note": tier_note,
        "lookback": len(data),
    }


def value_area(candles, lookback=120, bins=50, va_pct=0.70):
    """
    Point of Control (price with the most volume) and the Value Area — the band
    containing 70% of traded volume. This is what execution algos anchor to.
    """
    data = candles[-lookback:]
    if len(data) < 20:
        return None

    lo = min(c["low"] for c in data)
    hi = max(c["high"] for c in data)
    if hi <= lo:
        return None

    width = (hi - lo) / bins
    buckets = [0.0] * bins
    for c in data:
        first = max(0, int((c["low"] - lo) / width))
        last = min(bins - 1, int((c["high"] - lo) / width))
        spread = max(1, last - first + 1)
        for b in range(first, last + 1):
            buckets[b] += float(c["volume"]) / spread

    total = sum(buckets)
    if total <= 0:
        return None

    poc_idx = buckets.index(max(buckets))
    # expand outward from the POC until 70% of volume is captured
    included = {poc_idx}
    acc = buckets[poc_idx]
    lo_i = hi_i = poc_idx
    while acc < total * va_pct and (lo_i > 0 or hi_i < bins - 1):
        below = buckets[lo_i - 1] if lo_i > 0 else -1
        above = buckets[hi_i + 1] if hi_i < bins - 1 else -1
        if above >= below:
            hi_i += 1; acc += buckets[hi_i]; included.add(hi_i)
        else:
            lo_i -= 1; acc += buckets[lo_i]; included.add(lo_i)

    ltp = candles[-1]["close"]
    vah = lo + (hi_i + 1) * width
    val = lo + lo_i * width
    poc = lo + (poc_idx + 0.5) * width

    if ltp > vah:    position = "above value"
    elif ltp < val:  position = "below value"
    else:            position = "inside value"

    return {
        "poc": round(poc, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "position": position,
        "poc_dist_pct": round((poc - ltp) / ltp * 100, 2),
        "lookback": len(data),
    }


def order_book_depth(symbol):
    """
    The only genuinely real liquidity measure available: actual resting orders.
    Kite exposes 5 levels of live market depth. This is a snapshot, not history.
    """
    try:
        q = kite.quote([f"NSE:{symbol}"]).get(f"NSE:{symbol}")
        if not q:
            return None
        depth = q.get("depth") or {}
        buy, sell = depth.get("buy", []), depth.get("sell", [])
        if not buy or not sell:
            return None

        best_bid = buy[0]["price"]
        best_ask = sell[0]["price"]
        mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
        spread_pct = ((best_ask - best_bid) / mid * 100) if mid else None

        bid_qty = sum(l.get("quantity", 0) for l in buy)
        ask_qty = sum(l.get("quantity", 0) for l in sell)
        total = bid_qty + ask_qty

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_pct": round(spread_pct, 3) if spread_pct is not None else None,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            # >0.5 means more resting bids than offers within the visible 5 levels
            "imbalance": round(bid_qty / total, 2) if total else None,
            "bid_value_lakh": round(sum(l["price"] * l["quantity"] for l in buy) / 1e5, 1),
            "ask_value_lakh": round(sum(l["price"] * l["quantity"] for l in sell) / 1e5, 1),
        }
    except Exception:
        return None


# ---------------- Timeframes ----------------
# NSE trades 09:15–15:30 = 375 minutes. Timeframes that divide 375 evenly
# (25, 75) give whole candles with no stub at the close — Kite doesn't serve
# these natively, so we build them by grouping smaller candles from the open.
TIMEFRAMES = {
    # NSE trades 09:15-15:30 = 375 minutes. 125 and 75 divide it evenly into 3
    # and 5 bars, so no stub candle at the close. 4-hour does not divide evenly
    # (the session is 6.25h), so each day yields one full 4h bar plus a shorter
    # closing bar — the same way any platform handles it.
    # Kite intraday history limits: 60minute ~400 days, 5/15minute ~100 days.
    "day":   {"label": "Daily",       "kite": "day",       "group": 1,  "days": 400},
    "4h":    {"label": "4 hour",      "kite": "60minute",  "group": 4,  "days": 380},
    "125m":  {"label": "125 minute",  "kite": "5minute",   "group": 25, "days": 95},
    "75m":   {"label": "75 minute",   "kite": "15minute",  "group": 5,  "days": 95},
    "60m":   {"label": "60 minute",   "kite": "60minute",  "group": 1,  "days": 380},
}



def resample_candles(candles, group):
    """
    Combine every `group` consecutive candles into one, restarting the count at
    each session open so buckets stay aligned to 09:15 and never straddle days.
    """
    if group <= 1:
        return candles

    out = []
    current_day = None
    bucket = []

    def flush():
        if not bucket:
            return
        out.append({
            "date":   bucket[0]["date"],
            "open":   bucket[0]["open"],
            "high":   max(c["high"] for c in bucket),
            "low":    min(c["low"] for c in bucket),
            "close":  bucket[-1]["close"],
            "volume": sum(c["volume"] for c in bucket),
        })

    for c in candles:
        d = c["date"]
        day = d.date() if hasattr(d, "date") else d
        if day != current_day:      # new session — start a fresh bucket
            flush()
            bucket = []
            current_day = day
        bucket.append(c)
        if len(bucket) == group:
            flush()
            bucket = []
    flush()   # keep the trailing partial candle (the live one)
    return out


def get_candles(token, timeframe):
    """Fetch and, if needed, resample candles for the requested timeframe."""
    cfg = TIMEFRAMES.get(timeframe, TIMEFRAMES["day"])
    to_date = datetime.date.today()
    from_date = to_date - datetime.timedelta(days=cfg["days"])
    raw = kite.historical_data(token, from_date, to_date, cfg["kite"])
    return resample_candles(raw, cfg["group"])


@app.route("/api/liquidity/<symbol>")
def api_liquidity(symbol):
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    symbol = symbol.upper().strip()
    timeframe = request.args.get("tf", "day")
    if timeframe not in TIMEFRAMES:
        timeframe = "day"

    tok, market = resolve_market_token(symbol)
    if not tok:
        return jsonify({"error": f"{symbol} not found on Kite"}), 404

    try:
        candles = get_candles(tok, timeframe)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    if len(candles) < 30:
        return jsonify({"error": f"insufficient history on {TIMEFRAMES[timeframe]['label']}"}), 400

    # Gaps and swings scale with timeframe: what counts as a meaningful
    # imbalance on a daily chart is enormous on a 25-minute chart.
    # A gap that is meaningful on a daily chart is enormous intraday, so the
    # minimum gap size scales down as the timeframe gets finer.
    if timeframe == "day":
        min_gap, swing_lb, gap_age, swing_age, vp_lb = 0.15, 3, 120, 250, 180
    elif timeframe == "4h":
        min_gap, swing_lb, gap_age, swing_age, vp_lb = 0.10, 3, 150, 250, 200
    elif timeframe in ("125m", "75m"):
        min_gap, swing_lb, gap_age, swing_age, vp_lb = 0.07, 3, 180, 280, 220
    else:  # 60m
        min_gap, swing_lb, gap_age, swing_age, vp_lb = 0.05, 4, 200, 300, 250

    gaps  = find_fair_value_gaps(candles, max_age=gap_age, min_gap_pct=min_gap)
    pools = find_swing_liquidity(candles, lookback=swing_lb, max_age=swing_age)
    lvns  = volume_profile_nodes(candles, lookback=vp_lb)
    hvns  = find_high_volume_nodes(candles, lookback=vp_lb)

    horizon = 20 if timeframe == "day" else 40
    result = assemble_zones(symbol, candles, gaps, pools, lvns, hvns, horizon=horizon)
    result["timeframe"] = timeframe
    result["timeframe_label"] = TIMEFRAMES[timeframe]["label"]
    result["bars"] = len(candles)
    result["value_area"] = value_area(candles, lookback=vp_lb)

    # Execution metrics are a property of the stock, not the chart — always daily
    try:
        daily = get_candles(tok, "day")
        result["execution"] = execution_liquidity(daily)
    except Exception:
        result["execution"] = None

    result["book"] = order_book_depth(symbol)
    result["as_of"] = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
    return jsonify(result)


@app.route("/api/volume-zones/<symbol>")
def api_volume_zones(symbol):
    """Volume-weighted S&R zones for a symbol on the requested timeframe."""
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        import volume_zones as vz

        symbol = symbol.upper().strip()
        timeframe = request.args.get("tf", "day")
        if timeframe not in TIMEFRAMES:
            timeframe = "day"

        tok, market = resolve_market_token(symbol)
        if not tok:
            return jsonify({"error": f"{symbol} not found on Kite"}), 404

        try:
            candles = get_candles(tok, timeframe)
        except Exception as e:
            return jsonify({"error": str(e)}), 502

        if len(candles) < 60:
            return jsonify({"error": f"only {len(candles)} bars on "
                                     f"{TIMEFRAMES[timeframe]['label']} — not enough to build zones"}), 400

        # Pivot confirmation widens on faster timeframes: intraday noise throws
        # up far more local turns, and a 3-bar pivot there is not a swing.
        if timeframe == "day":
            left = right = 3
        elif timeframe in ("4h", "125m"):
            left = right = 4
        else:
            left = right = 5

        zones = vz.build_volume_zones(candles, left=left, right=right)
        last_close = candles[-1]["close"]

        # Flow enrichment (added 16 Aug 2026): sweep-and-reclaim, CVD-proxy
        # divergence, anchored-VWAP and POC/HVN confluence. Annotates the
        # finished zones only — build_volume_zones output is untouched, and
        # `strength` stays exactly as before. New per-zone fields: flow_score
        # and edge (strength + flow bonus, capped). Chart-level context in
        # `flow_context`, including the limitations list — render it.
        flow_context = vz.enrich_zones_with_flow(zones, candles)

        # Strength says how much structure sits at a level. It says nothing about
        # whether price will get there — that is governed by distance and this
        # instrument's own volatility, so it needs the empirical base rate used
        # on the structural tab. A 95-strength zone 25% away is a far weaker
        # prospect than a 40-strength zone 2% away, and the score alone hides that.
        horizon = 20 if timeframe == "day" else 40
        for z in zones:
            if z["position"] == "inside":
                z["reach_rate"] = 100
                z["reach_note"] = "price is already inside this zone"
            else:
                z["reach_rate"] = excursion_probability(
                    candles, z["dist_pct"], z["position"], horizon)
                z["reach_note"] = None
            # ranking that respects both: a strong level you cannot reach is
            # not actionable, and a reachable level with nothing at it is noise
            z["prospect"] = (round((z["reach_rate"] or 0) / 100 * z["strength"])
                             if z["reach_rate"] is not None else None)

        return jsonify({
            "symbol": symbol,
            "timeframe": timeframe,
            "timeframe_label": TIMEFRAMES[timeframe]["label"],
            "bars": len(candles),
            "pivot_confirm": right,
            "last_close": round(float(last_close), 2),
            "zones": zones,
            "summary": vz.summarise(zones, last_close),
            "flow_context": flow_context,
            "market": market,
            "horizon": horizon,
            "as_of": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/liquidity/<symbol>")
def liquidity_page(symbol):
    if not require_auth():
        return redirect("/login")
    return render_template("liquidity.html", symbol=symbol.upper())


FLOW_CACHE_FILE = "flow_history.json"


def _load_flow_cache():
    try:
        with open(FLOW_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_flow_cache(cache):
    try:
        with open(FLOW_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


def build_sector_turnover_history(days=90, force=False):
    """
    Daily turnover (volume x close) per sector, summed across constituents.

    NSE indices carry no traded volume of their own, so this must be built from
    constituent data — roughly 200 historical calls. Cached to disk per day:
    the first run of a session is slow, everything after is instant.
    """
    cache = _load_flow_cache()
    today = datetime.date.today().isoformat()
    if not force and cache.get("date") == today and cache.get("series"):
        return cache["series"], True

    tmap = equity_token_map()
    to_date = datetime.date.today()
    from_date = to_date - datetime.timedelta(days=int(days * 1.6) + 30)

    series = {}   # sector_key -> { "YYYY-MM-DD": turnover }
    for key, cfg in SECTOR_INDICES.items():
        if cfg.get("group") == "broad":
            continue   # broad indices overlap sectors; they'd double-count
        try:
            symbols = fetch_constituents(key)
        except Exception:
            continue

        daily = {}
        for sym in symbols:
            tok = tmap.get(sym)
            if not tok:
                continue
            try:
                candles = kite.historical_data(tok, from_date, to_date, "day")
            except Exception:
                continue
            for c in candles:
                d = c["date"]
                d = (d.date() if hasattr(d, "date") else d).isoformat()
                daily[d] = daily.get(d, 0.0) + float(c["volume"]) * float(c["close"])
            time.sleep(0.32)   # respect Kite's historical rate limit
        if daily:
            series[key] = daily

    _save_flow_cache({"date": today, "series": series})
    return series, False


def sector_flow_metrics(series, baseline=20):
    """
    For each sector: today's turnover against its own recent baseline.
    Comparing a sector to itself is the whole point — absolute rupee turnover
    just ranks sectors by size, which never changes.
    """
    out = []
    for key, daily in series.items():
        dates = sorted(daily.keys())
        if len(dates) < baseline + 5:
            continue
        vals = [daily[d] for d in dates]
        today_val = vals[-1]
        base = sum(vals[-(baseline + 1):-1]) / baseline
        if base <= 0:
            continue

        rel = today_val / base
        prev_rel = vals[-2] / (sum(vals[-(baseline + 2):-2]) / baseline) if len(vals) > baseline + 2 else None

        # 3-day slope of relative turnover — is attention building or fading?
        recent_rel = []
        for i in range(3):
            idx = len(vals) - 1 - i
            if idx - baseline < 0:
                break
            b = sum(vals[idx - baseline:idx]) / baseline
            if b > 0:
                recent_rel.append(vals[idx] / b)
        trend = None
        if len(recent_rel) == 3:
            trend = round(recent_rel[0] - recent_rel[2], 2)

        out.append({
            "key": key,
            "name": SECTOR_INDICES[key]["display"],
            "turnover_cr": round(today_val / 1e7, 1),
            "baseline_cr": round(base / 1e7, 1),
            "rel": round(rel, 2),
            "prev_rel": round(prev_rel, 2) if prev_rel else None,
            "trend": trend,
            "sessions": len(dates),
        })

    out.sort(key=lambda r: -r["rel"])
    return out


def backtest_persistence(series, baseline=20, top_n=3, min_days=40):
    """
    Does abnormal sector turnover actually persist to the next session?

    Measures: when a sector was in the top-N by relative turnover on day t,
    how often was it still top-N on day t+1? Compared against the base rate
    you'd get by picking at random. The gap between them is the whole edge —
    if there isn't one, this tool has nothing to offer and should say so.
    """
    keys = list(series.keys())
    if not keys:
        return None

    all_dates = sorted(set().union(*[set(series[k].keys()) for k in keys]))
    if len(all_dates) < baseline + min_days:
        return None

    rel_by_date = {}
    for d_i in range(baseline, len(all_dates)):
        d = all_dates[d_i]
        row = {}
        for k in keys:
            daily = series[k]
            window = [daily.get(all_dates[j]) for j in range(d_i - baseline, d_i)]
            window = [v for v in window if v is not None]
            cur = daily.get(d)
            if cur is None or len(window) < baseline * 0.7:
                continue
            base = sum(window) / len(window)
            if base > 0:
                row[k] = cur / base
        if len(row) >= 5:
            rel_by_date[d] = row

    dates = sorted(rel_by_date.keys())
    if len(dates) < min_days:
        return None

    hits = trials = 0
    rand_hits = rand_trials = 0
    for i in range(len(dates) - 1):
        today_row = rel_by_date[dates[i]]
        next_row = rel_by_date[dates[i + 1]]
        if len(today_row) < top_n + 2 or len(next_row) < top_n + 2:
            continue
        top_today = {k for k, _ in sorted(today_row.items(), key=lambda x: -x[1])[:top_n]}
        top_next = {k for k, _ in sorted(next_row.items(), key=lambda x: -x[1])[:top_n]}
        for k in top_today:
            trials += 1
            if k in top_next:
                hits += 1
        # base rate: chance a randomly chosen sector lands in tomorrow's top-N
        rand_trials += len(next_row)
        rand_hits += top_n

    if trials < 20:
        return None

    hit_rate = hits / trials * 100
    base_rate = rand_hits / rand_trials * 100 if rand_trials else 0

    return {
        "hit_rate": round(hit_rate, 1),
        "base_rate": round(base_rate, 1),
        "edge": round(hit_rate - base_rate, 1),
        "samples": trials,
        "days_tested": len(dates),
        "top_n": top_n,
    }


def india_vix_context():
    """India VIX level and its recent change — rising volatility usually means
    money concentrates into fewer, larger, more liquid names."""
    try:
        tok = get_index_token("INDIA VIX")
        if not tok:
            return None
        to_date = datetime.date.today()
        candles = kite.historical_data(tok, to_date - datetime.timedelta(days=60), to_date, "day")
        if len(candles) < 10:
            return None
        closes = [c["close"] for c in candles]
        cur = closes[-1]
        avg20 = sum(closes[-21:-1]) / 20 if len(closes) > 21 else sum(closes[:-1]) / max(1, len(closes) - 1)
        return {
            "level": round(float(cur), 2),
            "chg_pct": round((cur - closes[-2]) / closes[-2] * 100, 2),
            "vs_avg20": round((cur - avg20) / avg20 * 100, 1),
            "regime": "elevated" if cur > avg20 * 1.1 else ("calm" if cur < avg20 * 0.9 else "normal"),
        }
    except Exception:
        return None


@app.route("/flow")
def flow_page():
    if not require_auth():
        return redirect("/login")
    return render_template("flow.html")


_flow_build = {"running": False, "done": 0, "total": 0, "sector": None,
               "started": None, "error": None}
_flow_lock = threading.Lock()


def _do_flow_build(days=90):
    """Background worker: pulls per-stock history and aggregates sector turnover."""
    try:
        tmap = equity_token_map()
        to_date = datetime.date.today()
        from_date = to_date - datetime.timedelta(days=int(days * 1.6) + 30)

        plan = []
        for key, cfg in SECTOR_INDICES.items():
            if cfg.get("group") == "broad":
                continue
            try:
                plan.append((key, fetch_constituents(key)))
            except Exception:
                continue

        total = sum(len(syms) for _, syms in plan)
        with _flow_lock:
            _flow_build["total"] = total
            _flow_build["done"] = 0

        series = {}
        for key, symbols in plan:
            with _flow_lock:
                _flow_build["sector"] = SECTOR_INDICES[key]["display"]
            daily = {}
            for sym in symbols:
                tok = tmap.get(sym)
                if tok:
                    try:
                        for c in kite.historical_data(tok, from_date, to_date, "day"):
                            d = c["date"]
                            d = (d.date() if hasattr(d, "date") else d).isoformat()
                            daily[d] = daily.get(d, 0.0) + float(c["volume"]) * float(c["close"])
                    except Exception:
                        pass
                    time.sleep(0.32)     # Kite historical rate limit
                with _flow_lock:
                    _flow_build["done"] += 1
            if daily:
                series[key] = daily

        _save_flow_cache({"date": datetime.date.today().isoformat(), "series": series})
        with _flow_lock:
            _flow_build["error"] = None
        print(f"[flow] build complete: {len(series)} sectors, {total} stocks")
    except Exception as e:
        import traceback; traceback.print_exc()
        with _flow_lock:
            _flow_build["error"] = f"{type(e).__name__}: {e}"
    finally:
        with _flow_lock:
            _flow_build["running"] = False
            _flow_build["sector"] = None


@app.route("/api/flow")
def api_flow():
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    force = request.args.get("force") == "1"
    cache = _load_flow_cache()
    today = datetime.date.today().isoformat()
    have_fresh = cache.get("date") == today and cache.get("series")

    with _flow_lock:
        running = _flow_build["running"]
        snap = dict(_flow_build)

    # a build is already in flight: report progress, never block the request
    if running:
        pct = round(snap["done"] / snap["total"] * 100) if snap["total"] else 0
        elapsed = int(time.time() - (snap["started"] or time.time()))
        eta = None
        if snap["done"] > 5 and snap["total"]:
            eta = int((elapsed / snap["done"]) * (snap["total"] - snap["done"]))
        return jsonify({"building": True, "pct": pct, "done": snap["done"],
                        "total": snap["total"], "sector": snap["sector"],
                        "elapsed_sec": elapsed, "eta_sec": eta})

    if force or not have_fresh:
        with _flow_lock:
            if not _flow_build["running"]:
                _flow_build.update({"running": True, "done": 0, "total": 0,
                                    "sector": "starting", "started": time.time(),
                                    "error": None})
                threading.Thread(target=_do_flow_build, daemon=True).start()
        return jsonify({"building": True, "pct": 0, "done": 0, "total": 0,
                        "sector": "starting", "elapsed_sec": 0, "eta_sec": None})

    if snap.get("error"):
        return jsonify({"error": snap["error"]}), 500

    try:
        series = cache["series"]
        return jsonify({
            "as_of": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
            "cached": True,
            "sectors": sector_flow_metrics(series),
            "backtest": backtest_persistence(series),
            "vix": india_vix_context(),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


def _rel_matrix(series, baseline):
    """Relative-turnover matrix: {date: {sector: turnover / own baseline}}."""
    keys = list(series.keys())
    if not keys:
        return {}, []
    all_dates = sorted(set().union(*[set(series[k].keys()) for k in keys]))
    rel = {}
    for i in range(baseline, len(all_dates)):
        d = all_dates[i]
        row = {}
        for k in keys:
            daily = series[k]
            window = [daily.get(all_dates[j]) for j in range(i - baseline, i)]
            window = [v for v in window if v is not None]
            cur = daily.get(d)
            if cur is None or len(window) < baseline * 0.7:
                continue
            b = sum(window) / len(window)
            if b > 0:
                row[k] = cur / b
        if len(row) >= 5:
            rel[d] = row
    return rel, sorted(rel.keys())


def _test_config(rel, dates, top_n, horizon, require_trend=False, trend_min=0.15):
    """
    One configuration: if a sector is top_n today, is it top_n at any point in
    the next `horizon` sessions? Optionally only take signals where relative
    turnover was also rising, so we can see whether the trend filter earns its place.
    """
    hits = trials = 0
    rand_hits = rand_trials = 0
    for i in range(len(dates) - horizon):
        today = rel[dates[i]]
        if len(today) < top_n + 2:
            continue
        ranked = sorted(today.items(), key=lambda x: -x[1])
        top_today = [k for k, _ in ranked[:top_n]]

        future_top = set()
        for h in range(1, horizon + 1):
            fut = rel[dates[i + h]]
            if len(fut) < top_n + 2:
                continue
            future_top |= {k for k, _ in sorted(fut.items(), key=lambda x: -x[1])[:top_n]}
        if not future_top:
            continue

        for k in top_today:
            if require_trend:
                # only take it if relative turnover rose vs the prior session
                if i == 0:
                    continue
                prev = rel[dates[i - 1]].get(k)
                if prev is None or (today[k] - prev) < trend_min:
                    continue
            trials += 1
            if k in future_top:
                hits += 1

        rand_trials += len(today)
        rand_hits += len(future_top)

    if trials < 15:
        return None
    return {
        "hit_rate": round(hits / trials * 100, 1),
        "base_rate": round(rand_hits / rand_trials * 100, 1) if rand_trials else 0,
        "edge": round(hits / trials * 100 - (rand_hits / rand_trials * 100 if rand_trials else 0), 1),
        "samples": trials,
    }


def calibrate_flow(series):
    """
    Sweep the parameters instead of guessing them, and validate out-of-sample.

    In-sample results always flatter — with enough combinations tried, something
    will look good by luck. So the winner from the first half of the data is
    re-tested on the untouched second half. Only that second number means anything.
    """
    results = []
    for baseline in (10, 20, 60):
        rel, dates = _rel_matrix(series, baseline)
        if len(dates) < 60:
            continue
        for top_n in (1, 2, 3, 5):
            for horizon in (1, 2, 3, 5):
                r = _test_config(rel, dates, top_n, horizon)
                if r:
                    results.append({"baseline": baseline, "top_n": top_n,
                                    "horizon": horizon, "trend_filter": False, **r})
                rt = _test_config(rel, dates, top_n, horizon, require_trend=True)
                if rt:
                    results.append({"baseline": baseline, "top_n": top_n,
                                    "horizon": horizon, "trend_filter": True, **rt})

    if not results:
        return None
    results.sort(key=lambda r: -r["edge"])

    # --- walk-forward check on the best in-sample config ---
    best = results[0]
    rel, dates = _rel_matrix(series, best["baseline"])
    split = len(dates) // 2
    oos = None
    if split > 30:
        train_dates, test_dates = dates[:split], dates[split:]
        in_s = _test_config({d: rel[d] for d in train_dates}, train_dates,
                            best["top_n"], best["horizon"], best["trend_filter"])
        out_s = _test_config({d: rel[d] for d in test_dates}, test_dates,
                             best["top_n"], best["horizon"], best["trend_filter"])
        if in_s and out_s:
            oos = {"in_sample": in_s, "out_of_sample": out_s,
                   "decay": round(in_s["edge"] - out_s["edge"], 1)}

    # --- does the trend filter actually help? paired comparison ---
    trend_verdict = None
    paired = []
    for r in results:
        if r["trend_filter"]:
            match = next((x for x in results
                          if not x["trend_filter"] and x["baseline"] == r["baseline"]
                          and x["top_n"] == r["top_n"] and x["horizon"] == r["horizon"]), None)
            if match:
                paired.append(r["edge"] - match["edge"])
    if paired:
        avg = sum(paired) / len(paired)
        trend_verdict = {
            "avg_delta": round(avg, 1),
            "helps": bool(avg > 2),
            "configs_compared": len(paired),
        }

    return {
        "top_configs": results[:10],
        "worst_config": results[-1],
        "walk_forward": oos,
        "trend_filter_verdict": trend_verdict,
        "configs_tested": len(results),
    }


@app.route("/api/flow-calibrate")
def api_flow_calibrate():
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        series, _ = build_sector_turnover_history()
        if not series:
            return jsonify({"error": "No turnover history — load the flow page first."}), 502
        cal = calibrate_flow(series)
        if not cal:
            return jsonify({"error": "Not enough history to calibrate. Needs ~60+ sessions cached."}), 400
        return jsonify({"as_of": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"), **cal})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


EVENTS_CACHE_FILE = "events_cache.json"


def fetch_nse_events():
    """
    Upcoming corporate events from NSE's public event calendar.
    Kite carries no event data, so this comes straight from the exchange.
    NSE rejects bare requests, so cookies get primed first. Cached for the day.
    """
    today = datetime.date.today().isoformat()
    try:
        with open(EVENTS_CACHE_FILE) as f:
            cached = json.load(f)
            if cached.get("date") == today and cached.get("events") is not None:
                return cached["events"], True
    except Exception:
        pass

    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=12)
        time.sleep(0.7)
        r = s.get("https://www.nseindia.com/api/event-calendar",
                  headers={"Referer": "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"},
                  timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"NSE event calendar HTTP {r.status_code}")
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"could not reach NSE event calendar ({e})")

    events = []
    for row in data if isinstance(data, list) else []:
        sym = (row.get("symbol") or "").strip().upper()
        purpose = (row.get("purpose") or "").strip()
        raw_date = (row.get("date") or "").strip()
        if not sym or not raw_date:
            continue
        iso = None
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                iso = datetime.datetime.strptime(raw_date, fmt).date().isoformat()
                break
            except ValueError:
                continue
        if not iso:
            continue
        events.append({
            "symbol": sym,
            "date": iso,
            "purpose": purpose,
            # results/earnings are what actually move sector volume
            "is_results": bool(re.search(r"result|financial", purpose, re.I)),
        })

    try:
        with open(EVENTS_CACHE_FILE, "w") as f:
            json.dump({"date": today, "events": events}, f)
    except Exception:
        pass
    return events, False


def events_by_sector(days_ahead=10):
    """Group upcoming events under the sectors whose constituents they belong to."""
    events, cached = fetch_nse_events()

    sym_to_sectors = {}
    for key, cfg in SECTOR_INDICES.items():
        try:
            for sym in fetch_constituents(key):
                sym_to_sectors.setdefault(sym.upper(), []).append(key)
        except Exception:
            continue

    today = datetime.date.today()
    horizon = today + datetime.timedelta(days=days_ahead)

    by_sector = {}
    unmapped = []
    for ev in events:
        try:
            d = datetime.date.fromisoformat(ev["date"])
        except ValueError:
            continue
        if not (today <= d <= horizon):
            continue
        sectors = sym_to_sectors.get(ev["symbol"])
        if not sectors:
            # A result in a stock outside the tracked indices is still a real
            # catalyst. Dropping it silently made the panel look empty while
            # announcements were in fact scheduled.
            if ev["is_results"]:
                unmapped.append(ev)
            continue
        for key in sectors:
            by_sector.setdefault(key, []).append(ev)

    out = []
    for key, evs in by_sector.items():
        cfg = SECTOR_INDICES[key]
        results = [e for e in evs if e["is_results"]]
        # nearest upcoming results date drives the volume expectation
        dates = sorted({e["date"] for e in results})
        out.append({
            "key": key,
            "name": cfg["display"],
            "group": cfg.get("group", "sector"),
            "results_count": len(results),
            "total_events": len(evs),
            "next_date": dates[0] if dates else None,
            "events": sorted(results, key=lambda e: e["date"])[:25],
        })

    out.sort(key=lambda r: (r["next_date"] or "9999", -r["results_count"]))

    # Distinguish "nothing scheduled" from "fetch returned nothing" — they look
    # identical on screen but mean completely different things.
    all_results = [e for e in events if e["is_results"]]
    future_results = [e for e in all_results if e["date"] >= today.isoformat()]
    next_dates = sorted({e["date"] for e in future_results})

    return {
        "sectors": out,
        "cached": cached,
        "unmapped_results": len(unmapped),
        "horizon_days": days_ahead,
        "other_results": sorted(
            [{"symbol": e["symbol"], "date": e["date"], "purpose": e["purpose"][:80]}
             for e in unmapped],
            key=lambda e: e["date"])[:40],
        "diagnostics": {
            "in_window_unmapped": len(unmapped),
            "events_fetched": len(events),
            "results_events": len(all_results),
            "results_upcoming": len(future_results),
            "next_results_date": next_dates[0] if next_dates else None,
            "days_until_next": ((datetime.date.fromisoformat(next_dates[0]) - today).days
                                if next_dates else None),
        },
    }


@app.route("/api/events")
def api_events():
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        days = request.args.get("days", default=10, type=int)
        data = events_by_sector(days_ahead=max(1, min(days, 45)))
        data["as_of"] = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
        return jsonify(data)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502


ENERGY_CACHE = "energy_cache.json"


# yfinance interval limits: 5m data only goes back ~60 days, 1h ~730 days.
# 75/125-minute bars come from NSE's 375-minute session; crude and gas trade
# ~23h on CME, so these divisions are arbitrary here — included because asked for.
ENERGY_TIMEFRAMES = {
    "1d":    {"label": "Daily",      "yf_interval": "1d", "group": 1,  "period_days": 2920, "z_window": 252},
    "4h":    {"label": "4 hour",     "yf_interval": "1h", "group": 4,  "period_days": 720,  "z_window": 252},
    "125m":  {"label": "125 minute", "yf_interval": "5m", "group": 25, "period_days": 58,   "z_window": 200},
    "75m":   {"label": "75 minute",  "yf_interval": "5m", "group": 15, "period_days": 58,   "z_window": 250},
}


def _resample_bars(rows, group):
    """rows = [(timestamp, close)] sorted. Group consecutive bars, never across days."""
    if group <= 1:
        return {t.isoformat() if hasattr(t, "isoformat") else str(t): c for t, c in rows}
    out = {}
    bucket = []
    cur_day = None
    for t, c in rows:
        day = t.date() if hasattr(t, "date") else None
        if day != cur_day:
            if bucket:
                out[bucket[0][0].isoformat()] = bucket[-1][1]
            bucket = []
            cur_day = day
        bucket.append((t, c))
        if len(bucket) == group:
            out[bucket[0][0].isoformat()] = bucket[-1][1]
            bucket = []
    if bucket:
        out[bucket[0][0].isoformat()] = bucket[-1][1]
    return out


def fetch_energy_series(timeframe="1d"):
    """Crude and natural gas closes at the requested timeframe."""
    cfg = ENERGY_TIMEFRAMES.get(timeframe, ENERGY_TIMEFRAMES["1d"])
    today = datetime.date.today().isoformat()
    # cache each timeframe separately so switching between them doesn't refetch
    store = {}
    try:
        with open(ENERGY_CACHE) as f:
            store = json.load(f)
        if store.get("date") == today:
            hit = (store.get("tf") or {}).get(timeframe)
            if hit and hit.get("crude") and hit.get("gas"):
                return hit["crude"], hit["gas"], True
        else:
            store = {}
    except Exception:
        store = {}

    def from_yf(ticker, label):
        import yfinance as yf
        df = yf.download(ticker,
                         start=datetime.date.today() - datetime.timedelta(days=cfg["period_days"]),
                         end=datetime.date.today() + datetime.timedelta(days=1),
                         interval=cfg["yf_interval"], progress=False, auto_adjust=False)
        if df is None or df.empty:
            raise RuntimeError(f"nothing for {ticker} at {cfg['yf_interval']}")
        rows = []
        for idx, row in df.iterrows():
            v = row["Close"]
            try:
                v = float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
            except Exception:
                continue
            if v == v and v > 0:
                rows.append((idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx, v))
        if len(rows) < 120:
            raise RuntimeError(f"only {len(rows)} bars for {ticker}")
        out = _resample_bars(rows, cfg["group"])
        print(f"[energy] {label} {cfg['label']}: {len(rows)} raw -> {len(out)} bars ({ticker})")
        return out

    crude = from_yf("CL=F", "crude")
    gas = from_yf("NG=F", "natural gas")

    try:
        store.setdefault("date", today)
        store.setdefault("tf", {})[timeframe] = {"crude": crude, "gas": gas}
        with open(ENERGY_CACHE, "w") as f:
            json.dump(store, f)
    except Exception:
        pass
    return crude, gas, False


@app.route("/api/probe-mcx-contracts")
def api_probe_mcx_contracts():
    """DIAGNOSTIC — how much history each LISTED MCX contract actually holds.

    Read-only; changes no behaviour anywhere. It answers the one question that
    decides whether a stitched continuous series is needed at all: if each
    contract already carries enough bars on its own, per-contract zones are
    deeper than a splice would be and carry no rollover artifacts.

    /api/probe-mcx-contracts?name=NATURALGAS&tf=day
    """
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    name = (request.args.get("name") or "NATURALGAS").upper().strip()
    timeframe = request.args.get("tf", "day")
    if timeframe not in TIMEFRAMES:
        timeframe = "day"

    today = datetime.date.today()
    listed = []
    for i in mcx_instruments():
        if (i.get("name") or "").upper() != name or i.get("instrument_type") != "FUT":
            continue
        exp = i.get("expiry")
        exp = exp.date() if hasattr(exp, "date") else exp
        listed.append((exp, i))
    listed.sort(key=lambda x: (x[0] is None, x[0]))

    front = mcx_front_month(name)
    front_tok = front["instrument_token"] if front else None

    out = []
    for exp, inst in listed:
        row = {
            "tradingsymbol": inst.get("tradingsymbol"),
            "expiry": str(exp)[:10] if exp else None,
            "days_to_expiry": (exp - today).days if exp else None,
            "lot_size": inst.get("lot_size"),
            "is_front_month": inst["instrument_token"] == front_tok,
        }
        try:
            candles = get_candles(inst["instrument_token"], timeframe)
            row["bars"] = len(candles)
            if candles:
                row["first_bar"] = str(candles[0].get("date"))[:16]
                row["last_bar"] = str(candles[-1].get("date"))[:16]
                vols = [float(c.get("volume") or 0) for c in candles]
                nonzero = [v for v in vols if v > 0]
                row["bars_with_volume"] = len(nonzero)
                row["avg_volume"] = int(sum(nonzero) / len(nonzero)) if nonzero else 0
                # last 20 bars vs whole life — is this contract liquid NOW?
                recent = [v for v in vols[-20:] if v > 0]
                row["avg_volume_last20"] = int(sum(recent) / len(recent)) if recent else 0
                # zone-builder feasibility, using the route's own floor
                row["enough_for_zones"] = len(candles) >= 60
                row["enough_for_deep_zones"] = len(candles) >= 250
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        out.append(row)

    usable = [r for r in out if r.get("bars")]
    verdict = None
    if usable:
        front_row = next((r for r in usable if r.get("is_front_month")), None)
        deep = front_row and front_row.get("enough_for_deep_zones")
        ok = front_row and front_row.get("enough_for_zones")
        verdict = ("front month already has deep history — per-contract zones are "
                   "sufficient, stitching would add repaint risk for no gain" if deep else
                   "front month clears the 60-bar floor but is shallow — stitching or "
                   "intraday timeframes worth considering" if ok else
                   "front month is below the 60-bar floor on this timeframe — zones "
                   "cannot be built from it alone")
    return jsonify({
        "underlying": name,
        "timeframe": TIMEFRAMES[timeframe]["label"],
        "requested_days": TIMEFRAMES[timeframe]["days"],
        "listed_contracts": len(out),
        "contracts": out,
        "verdict": verdict,
        "note": ("Kite lists only CURRENTLY LISTED contracts. Expired ones carry no "
                 "token, so no back-history can be fetched for them — an archive can "
                 "only accumulate forward from today."),
    })


@app.route("/api/mcx-energy-quote")
def api_mcx_energy_quote():
    """Live front-month MCX energy quotes — real exchange volume, unlike a
    CFD feed. Powers the clickable price cards on the energy page."""
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    out = {}
    for key in ("NATURALGAS", "CRUDEOIL"):
        try:
            inst = mcx_front_month(key)
            if not inst:
                out[key] = {"error": f"no listed {key} contract found on MCX"}
                continue
            full = f"MCX:{inst['tradingsymbol']}"
            q = (kite.quote([full]) or {}).get(full) or {}
            ohlc = q.get("ohlc") or {}
            ltp = q.get("last_price")
            prev = ohlc.get("close")
            exp = inst.get("expiry")
            exp = exp.date() if hasattr(exp, "date") else exp
            out[key] = {
                "symbol": MCX_PREFIX + key,
                "label": MCX_ENERGY[key]["label"],
                "unit": MCX_ENERGY[key]["unit"],
                "tradingsymbol": inst.get("tradingsymbol"),
                "expiry": str(exp)[:10] if exp else None,
                "days_to_expiry": (exp - datetime.date.today()).days if exp else None,
                "lot_size": inst.get("lot_size"),
                "ltp": ltp,
                "prev_close": prev,
                "chg_pct": (round((ltp - prev) / prev * 100, 2)
                            if ltp and prev else None),
                "open": ohlc.get("open"), "high": ohlc.get("high"), "low": ohlc.get("low"),
                "volume": q.get("volume"),
                "oi": q.get("oi"),
            }
        except Exception as e:
            out[key] = {"error": f"{type(e).__name__}: {e}"}
    return jsonify({"quotes": out, "as_of": datetime.datetime.now().strftime("%d %b %Y %H:%M")})


@app.route("/api/probe-symbols")
def api_probe_symbols():
    """Test which Stooq tickers actually return data from this machine."""
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    candidates = request.args.get("syms")
    if candidates:
        syms = [s.strip() for s in candidates.split(",") if s.strip()]
    else:
        syms = ["cl.c", "cl.f", "clf.f", "wtic", "cb.c", "brent",
                "ng.c", "ng.f", "ngf.f",
                "dx.f", "dx.c", "usdinr", "^dji"]

    start = (datetime.date.today() - datetime.timedelta(days=400)).strftime("%Y%m%d")
    end = datetime.date.today().strftime("%Y%m%d")
    out = []
    for sym in syms:
        try:
            r = requests.get(f"https://stooq.com/q/d/l/?s={sym}&d1={start}&d2={end}&i=d",
                             headers=BROWSER_HEADERS, timeout=15)
            body = r.text.strip()
            rows = len(body.split("\n")) - 1 if "Date" in body[:200] else 0
            sample = body.split("\n")[1] if rows > 0 else body[:80]
            out.append({"symbol": sym, "status": r.status_code, "rows": rows,
                        "ok": rows > 100, "sample": sample})
        except Exception as e:
            out.append({"symbol": sym, "error": str(e), "ok": False})
        time.sleep(0.25)
    return jsonify({"results": out,
                    "working": [r["symbol"] for r in out if r.get("ok")]})


@app.route("/energy")
def energy_page():
    if not require_auth():
        return redirect("/login")
    return render_template("energy.html")


@app.route("/api/energy")
def api_energy():
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        import energy_analysis as ea
        entry_z = request.args.get("z", default=2.0, type=float)
        entry_z = max(1.0, min(entry_z, 3.0))

        timeframe = request.args.get("tf", "1d")
        if timeframe not in ENERGY_TIMEFRAMES:
            timeframe = "1d"
        cfg = ENERGY_TIMEFRAMES[timeframe]

        try:
            crude, gas, cached = fetch_energy_series(timeframe)
        except Exception as e:
            return jsonify({"error": f"Could not fetch {cfg['label']} data: {e}"}), 502

        result = ea.analyse(crude, gas, entry_z=entry_z, z_window=cfg["z_window"],
                            intraday=(timeframe != "1d"))
        result["timeframe"] = timeframe
        result["timeframe_label"] = cfg["label"]
        result["z_window"] = cfg["z_window"]
        if "error" in result:
            return jsonify(result), 400
        result["cached"] = cached
        result["as_of"] = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


WEMA_CACHE = "wema_scan.json"
_wema_build = {"running": False, "done": 0, "total": 0, "stage": None,
               "started": None, "error": None, "min_cr": None}
_wema_lock = threading.Lock()


def _avg_turnover(sessions=15):
    """
    Average daily turnover per stock over recent sessions.

    Liquidity is a property of the stock; one session's turnover is a sample of
    it. Filtering the universe on a single day makes membership arbitrary — the
    same stock passes on a busy Thursday and fails on a quiet Monday — and it
    drops names whose normal liquidity is fine but whose reference day happened
    to be slow.

    Reuses the delivery-history cache, which already holds the bhavcopy rows.
    """
    hist = {}
    try:
        with open(DELIV_HIST) as f:
            hist = json.load(f).get("days") or {}
    except Exception:
        hist = {}

    if len(hist) < 3:
        # nothing cached yet — fall back to the most recent single session
        single, day = _bhavcopy_turnover()
        return {k: v for k, v in single.items()}, (f"single session {day}" if day else "unavailable")

    # the delivery cache stores delivery %, not turnover, so pull turnover
    # from the same bhavcopy files
    totals, counts = {}, {}
    for iso in sorted(hist)[-sessions:]:
        ds = datetime.date.fromisoformat(iso).strftime("%d%m%Y")
        got = None
        for url in (f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv",
                    f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv"):
            try:
                sess = _nse_session()
                r = sess.get(url, timeout=25)
                if r.status_code != 200 or "SYMBOL" not in r.text[:400].upper():
                    continue
                lines = r.text.strip().split("\n")
                hdr = [h.strip().upper() for h in lines[0].split(",")]
                def col(*names):
                    for n in names:
                        if n in hdr:
                            return hdr.index(n)
                    return None
                i_sym, i_ser, i_val = col("SYMBOL"), col("SERIES"), col("TURNOVER_LACS", "TURNOVER")
                if i_sym is None or i_val is None:
                    continue
                got = {}
                for line in lines[1:]:
                    p_ = [x.strip() for x in line.split(",")]
                    if len(p_) <= max(i_sym, i_val):
                        continue
                    if i_ser is not None and p_[i_ser].upper() not in ("EQ", "BE"):
                        continue
                    try:
                        got[p_[i_sym].upper()] = float(p_[i_val]) / 100.0
                    except ValueError:
                        continue
                if len(got) > 200:
                    break
            except Exception:
                continue
            finally:
                time.sleep(0.25)
        if got:
            for sym, cr in got.items():
                totals[sym] = totals.get(sym, 0.0) + cr
                counts[sym] = counts.get(sym, 0) + 1

    if not totals:
        single, day = _bhavcopy_turnover()
        return single, (f"single session {day}" if day else "unavailable")

    avg = {sym: round(totals[sym] / counts[sym], 2) for sym in totals if counts[sym] >= 3}
    print(f"[universe] average turnover from {max(counts.values(), default=0)} sessions, {len(avg)} stocks")
    return avg, f"{max(counts.values(), default=0)}-session average"


def _bhavcopy_turnover():
    """
    Turnover per stock from the most recent published bhavcopy.

    Needed because the live-quote route reports zero volume before the market
    opens and on holidays, which silently empties the universe: every stock
    fails a turnover filter when every stock has traded nothing yet.
    """
    out = {}
    for back in range(0, 8):
        d = datetime.date.today() - datetime.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        ds = d.strftime("%d%m%Y")
        for url in (f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv",
                    f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv"):
            try:
                sess = _nse_session()
                r = sess.get(url, timeout=25)
                if r.status_code != 200 or "SYMBOL" not in r.text[:400].upper():
                    continue
                lines = r.text.strip().split("\n")
                hdr = [h.strip().upper() for h in lines[0].split(",")]
                def col(*names):
                    for n in names:
                        if n in hdr:
                            return hdr.index(n)
                    return None
                i_sym, i_ser = col("SYMBOL"), col("SERIES")
                i_val = col("TURNOVER_LACS", "TURNOVER")
                i_vol = col("TTL_TRD_QNTY", "TOTAL_TRADED_QUANTITY")
                i_cls = col("CLOSE_PRICE", "CLOSE")
                if i_sym is None:
                    continue
                for line in lines[1:]:
                    p_ = [x.strip() for x in line.split(",")]
                    if len(p_) <= i_sym:
                        continue
                    if i_ser is not None and p_[i_ser].upper() not in ("EQ", "BE"):
                        continue
                    try:
                        if i_val is not None:
                            cr = float(p_[i_val]) / 100.0        # lakhs -> crore
                        elif i_vol is not None and i_cls is not None:
                            cr = float(p_[i_vol]) * float(p_[i_cls]) / 1e7
                        else:
                            continue
                    except ValueError:
                        continue
                    out[p_[i_sym].upper()] = round(cr, 2)
                if len(out) > 200:
                    print(f"[universe] turnover fallback from bhavcopy {d.isoformat()}: {len(out)} stocks")
                    return out, d.isoformat()
            except Exception:
                continue
            finally:
                time.sleep(0.3)
    return {}, None


def _tradeable_universe(min_turnover_cr=5.0):
    exchange = "NSE"   # NSE only: BSE is mostly dual-listed duplicates or illiquid
    """
    All EQ instruments on an exchange, filtered to those with real turnover today.

    Pre-filtering matters: bulk quotes cost 4-5 calls for 2000 stocks, while
    historical data costs one call each. Cutting the illiquid tail first turns
    an 11-minute scan into a 3-minute one, and removes names where a 200-week
    EMA touch is meaningless noise anyway.
    """
    instruments = kite.instruments(exchange)
    eq = [i for i in instruments if i.get("instrument_type") == "EQ" and i.get("tradingsymbol")]

    # skip non-common-stock instruments that clutter the list
    skip = ("-RE", "-PP", "-BE", "-BZ", "-SM", "-ST", "-IT", "ETF", "GOLDBEES",
            "LIQUIDBEES", "-NP", "-N1", "-N2", "-N3", "-Y1")
    eq = [i for i in eq if not any(i["tradingsymbol"].upper().endswith(s) or s in i["tradingsymbol"].upper()
                                   for s in skip)]

    symbols = [i["tradingsymbol"] for i in eq]
    tok = {i["tradingsymbol"]: i["instrument_token"] for i in eq}

    quotes = {}
    BATCH = 400
    for i in range(0, len(symbols), BATCH):
        chunk = [f"{exchange}:{s}" for s in symbols[i:i + BATCH]]
        try:
            quotes.update(kite.quote(chunk))
        except Exception:
            pass
        time.sleep(0.35)

    live_turnover = {}
    for s in symbols:
        q = quotes.get(f"{exchange}:{s}")
        if not q:
            continue
        vol = q.get("volume") or q.get("volume_traded") or 0
        price = q.get("last_price") or 0
        if vol and price:
            live_turnover[s] = float(vol) * float(price) / 1e7

    # Before the open, and on holidays, every stock reports zero volume. Falling
    # through with that produces an empty universe and a scan that reports
    # "0 stocks" with no explanation — so fall back to the last bhavcopy.
    # TWO ROUTES IN.
    #
    # Route 1 — normal liquidity: average turnover clears the floor. This is the
    # stock's own property and the right default gate.
    #
    # Route 2 — volume expansion: a stock whose average sits below the floor but
    # which is currently trading a large multiple of its own norm. Excluding
    # these was backwards: a name running four times its usual volume into a
    # major level is more interesting than a quiet blue chip drifting past one.
    # The expanded turnover must still clear the floor in absolute terms, so a
    # thin stock at five times nothing stays out.
    EXPANSION_X = 3.0
    avg_cr, source = _avg_turnover()
    _tradeable_universe.last_source = source

    # latest session's turnover, for the expansion test
    latest_cr = dict(live_turnover)
    if len(latest_cr) < max(50, len(symbols) * 0.2):
        fb, _fbday = _bhavcopy_turnover()
        for k, v in fb.items():
            latest_cr.setdefault(k, v)

    liquid = []
    for s in symbols:
        avg = avg_cr.get(s.upper())
        today = latest_cr.get(s) or latest_cr.get(s.upper())
        cr = avg if avg is not None else today
        if cr is None:
            continue

        via = None
        if avg is not None and avg >= min_turnover_cr:
            via = "liquidity"
        elif (today and avg and avg > 0
              and today >= avg * EXPANSION_X
              and today >= min_turnover_cr):
            via = "expansion"
        elif avg is None and today and today >= min_turnover_cr:
            via = "liquidity"          # newly listed, no average yet
        if via is None:
            continue
        q = quotes.get(f"{exchange}:{s}") or {}
        price = q.get("last_price") or (q.get("ohlc") or {}).get("close") or 0
        liquid.append({"symbol": s, "token": tok[s], "exchange": exchange,
                       "turnover_cr": round(cr, 1),
                       "turnover_today_cr": round(today, 1) if today else None,
                       "turnover_x": round(today / avg, 1) if (today and avg and avg > 0) else None,
                       "admitted_via": via,
                       "price": round(float(price), 2)})
    liquid.sort(key=lambda r: -r["turnover_cr"])
    n_exp = sum(1 for r in liquid if r["admitted_via"] == "expansion")
    print(f"[universe] {len(liquid)} stocks above Rs{min_turnover_cr}Cr "
          f"({n_exp} admitted on volume expansion) (source: {source})")
    return liquid


def _fetch_long_daily(token, years=11, chunk_days=1900):
    """
    Kite caps a single daily request near 2000 days, so walk backwards in chunks.

    This matters more than it looks. A 200-period EMA seeded on an SMA still
    carries (1 - 2/201)^n of that seed after n further bars: with only 85
    settling bars the seed is still 43% of the value, which is why short
    history produced numbers well off TradingView.

    11 years is the working compromise. It needs two chained requests rather
    than three, and leaves roughly 370 settling bars — enough to bring the seed
    influence to about 2.5%, close enough that values track a full-history chart.
    Three requests across 800 stocks is ~2,400 sequential calls, which runs into
    sustained rate limiting and fails silently.
    """
    to_date = datetime.date.today()
    collected = {}
    cursor_end = to_date
    chunks = int(years * 365.25 / chunk_days) + 1
    for _ in range(chunks):
        cursor_start = cursor_end - datetime.timedelta(days=chunk_days)
        try:
            batch = kite.historical_data(token, cursor_start, cursor_end, "day")
        except Exception:
            break
        if not batch:
            break                      # listing start reached
        for c in batch:
            d = c["date"]
            d = d.date() if hasattr(d, "date") else d
            collected[d] = c
        cursor_end = cursor_start - datetime.timedelta(days=1)
        time.sleep(0.32)
        if len(batch) < 50:
            break                      # thin chunk means we ran past the listing
    return [collected[d] for d in sorted(collected.keys())]


def _do_wema_scan(min_turnover_cr=50.0, touch_band=1.5):
    exchange = "NSE"
    import weekly_ema_scan as ws
    try:
        with _wema_lock:
            _wema_build["stage"] = "finding liquid stocks"

        universe = _tradeable_universe(min_turnover_cr)
        with _wema_lock:
            _wema_build["total"] = len(universe)
            _wema_build["done"] = 0
            _wema_build["stage"] = f"scanning {len(universe)} stocks"

        fno = fno_symbols()          # was dropped by an earlier edit; the loop
                                     # references it and every hit died on a
                                     # NameError swallowed by the except below
        hits = []
        skipped_short = 0
        errors = []
        no_candles = 0
        classified_none = 0
        candle_counts = []
        for row in universe:
            try:
                candles = _fetch_long_daily(row["token"])
                candle_counts.append(len(candles))
                if not candles:
                    no_candles += 1
                if len(candles) > 1100:            # need ~220+ weekly bars
                    weekly = ws.resample_weekly(candles)
                    r = ws.classify(weekly, touch_band=touch_band)
                    if r:
                        r["symbol"] = row["symbol"]
                        r["exchange"] = row["exchange"]
                        r["turnover_cr"] = row["turnover_cr"]
                        r["turnover_today_cr"] = row.get("turnover_today_cr")
                        r["turnover_x"] = row.get("turnover_x")
                        r["admitted_via"] = row.get("admitted_via")
                        r["fno"] = bool(row["symbol"].upper() in fno)
                        r["score"] = ws.score(r)
                        hits.append(r)
                    elif len(weekly) < 220:
                        skipped_short += 1
                    else:
                        classified_none += 1     # enough history, simply not at the level
                else:
                    skipped_short += 1
            except Exception as e:
                # A silent except here has now hidden three separate bugs: a
                # rate-limited scan, an undefined variable, and a wrong endpoint
                # all looked identical to "the market has nothing at this level".
                # Programming errors are re-raised immediately; only data and
                # network faults are tolerated and counted.
                if isinstance(e, (NameError, AttributeError, TypeError, KeyError, IndexError)):
                    raise
                if len(errors) < 8:
                    errors.append(f"{row['symbol']}: {type(e).__name__}: {str(e)[:120]}")
            with _wema_lock:
                _wema_build["done"] += 1

        med_candles = sorted(candle_counts)[len(candle_counts) // 2] if candle_counts else 0
        print(f"[wema] diagnostics: {len(hits)} hits | {classified_none} had history but no interaction "
              f"| {skipped_short} short history | {no_candles} returned nothing | {len(errors)} errored "
              f"| median candles/stock {med_candles}")
        if errors:
            print(f"[wema] first error: {errors[0]}")

        hits.sort(key=lambda r: -r["score"])
        payload = {
            "date": datetime.date.today().isoformat(),
            "exchange": exchange,
            "min_turnover_cr": min_turnover_cr,
            "scanned": len(universe),
            "skipped_short_history": skipped_short,
            "hits": hits,
            "generated_at": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
            "weekday": datetime.date.today().weekday(),
        }
        with open(WEMA_CACHE, "w") as f:
            json.dump(payload, f)
        print(f"[wema] scan complete: {len(hits)} interactions from {len(universe)} stocks")
        with _wema_lock:
            _wema_build["error"] = None
    except Exception as e:
        import traceback; traceback.print_exc()
        with _wema_lock:
            _wema_build["error"] = f"{type(e).__name__}: {e}"
    finally:
        with _wema_lock:
            _wema_build["running"] = False
            _wema_build["stage"] = None


@app.route("/wema")
def wema_page():
    if not require_auth():
        return redirect("/login")
    return render_template("wema.html")


@app.route("/api/wema")
def api_wema():
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    force = request.args.get("force") == "1"
    exchange = "NSE"
    # Floor is configurable, defaulting to Rs50Cr. Lower values admit genuinely
    # interesting mid-caps whose normal turnover sits below it — a stock averaging
    # Rs20Cr can still be a real level test, especially when volume is expanding.
    # The cost is a much larger universe and a longer scan.
    min_turnover = max(10.0, request.args.get("min_cr", default=50.0, type=float))

    cached = {}
    try:
        with open(WEMA_CACHE) as f:
            cached = json.load(f)
    except Exception:
        pass
    same_day = cached.get("date") == datetime.date.today().isoformat()
    params_match = abs(cached.get("min_turnover_cr", -1) - min_turnover) < 0.01

    with _wema_lock:
        running = _wema_build["running"]
        snap = dict(_wema_build)

    if running:
        pct = round(snap["done"] / snap["total"] * 100) if snap["total"] else 0
        elapsed = int(time.time() - (snap["started"] or time.time()))
        eta = int((elapsed / snap["done"]) * (snap["total"] - snap["done"])) if snap["done"] > 10 else None
        return jsonify({"building": True, "pct": pct, "done": snap["done"],
                        "total": snap["total"], "stage": snap["stage"],
                        "elapsed_sec": elapsed, "eta_sec": eta,
                        "running_min_cr": snap.get("min_cr")})

    # A scan starts ONLY on an explicit request, or when nothing is cached at
    # all. Previously any mismatch between the cached floor and the requested
    # one counted as stale and silently launched a fresh scan — so a page
    # refresh, which resets the dropdown to its default, discarded a completed
    # result and started over. The user asks for a rescan; polling never does.
    if force or not cached:
        with _wema_lock:
            if not _wema_build["running"]:
                _wema_build.update({"running": True, "done": 0, "total": 0,
                                    "stage": "starting", "started": time.time(),
                                    "error": None, "min_cr": min_turnover})
                threading.Thread(target=_do_wema_scan,
                                 args=(min_turnover,), daemon=True).start()
        return jsonify({"building": True, "pct": 0, "done": 0, "total": 0,
                        "stage": "starting", "elapsed_sec": 0, "eta_sec": None,
                        "running_min_cr": min_turnover})

    if not params_match or not same_day:
        # serve what we have and let the page offer a rescan. Re-running takes
        # minutes, so it happens on request rather than on a page load.
        out = dict(cached)
        out["params_differ"] = not params_match
        out["stale_day"] = not same_day
        out["requested_min_cr"] = min_turnover
        return jsonify(out)

    if snap.get("error"):
        return jsonify({"error": snap["error"]}), 500

    return jsonify(cached)


_nfo_cache = {"date": None, "instruments": None}


def _nfo_instruments():
    today = datetime.date.today().isoformat()
    if _nfo_cache["date"] == today and _nfo_cache["instruments"]:
        return _nfo_cache["instruments"]
    inst = kite.instruments("NFO")
    _nfo_cache["date"] = today
    _nfo_cache["instruments"] = inst
    return inst


_fno_symbols_cache = {"date": None, "symbols": None}


def fno_symbols():
    """
    Underlyings that have stock derivatives, taken from the NFO instrument dump.
    Futures are the reliable marker: every F&O-eligible stock has a future,
    while some option series can be missing for a given expiry.
    """
    today = datetime.date.today().isoformat()
    if _fno_symbols_cache["date"] == today and _fno_symbols_cache["symbols"] is not None:
        return _fno_symbols_cache["symbols"]
    try:
        syms = {i["name"].upper() for i in _nfo_instruments()
                if i.get("segment") == "NFO-FUT" and i.get("name")}
    except Exception:
        syms = set()
    _fno_symbols_cache["date"] = today
    _fno_symbols_cache["symbols"] = syms
    return syms


def _chain_for(name, spot, strike_span=10):
    """Nearest-expiry option chain around spot, plus the front-month future."""
    inst = _nfo_instruments()
    today = datetime.date.today()

    def exp_of(i):
        e = i.get("expiry")
        if not e:
            return None
        return e if isinstance(e, datetime.date) else None

    opts = [i for i in inst if i.get("name") == name and i.get("segment") == "NFO-OPT"]
    futs = [i for i in inst if i.get("name") == name and i.get("segment") == "NFO-FUT"]

    future_expiries = sorted({exp_of(i) for i in opts if exp_of(i) and exp_of(i) >= today})
    if not future_expiries:
        return None
    expiry = future_expiries[0]

    near = [i for i in opts if exp_of(i) == expiry]
    strikes = sorted({float(i["strike"]) for i in near if i.get("strike")})
    if not strikes:
        return None
    strikes.sort(key=lambda k: abs(k - spot))
    keep = set(strikes[:strike_span * 2 + 1])

    ce = {float(i["strike"]): i for i in near
          if i.get("instrument_type") == "CE" and float(i["strike"]) in keep}
    pe = {float(i["strike"]): i for i in near
          if i.get("instrument_type") == "PE" and float(i["strike"]) in keep}

    fut_exps = sorted({exp_of(i) for i in futs if exp_of(i) and exp_of(i) >= today})
    fut = None
    if fut_exps:
        fut = next((i for i in futs if exp_of(i) == fut_exps[0]), None)

    return {"expiry": expiry, "ce": ce, "pe": pe, "future": fut}


NSE_CAS_ENDPOINT_FILE = "nse_cas_endpoint.json"

# NSE does not document these. The CAS market-watch page is at
# /market-data/closing-auction-session, and NSE's pages are all backed by
# /api/... routes, so these are the plausible shapes. The probe below finds
# which one actually responds from your machine rather than guessing.
CAS_ENDPOINT_CANDIDATES = [
    "/api/market-data-closing-auction?key=ALL",
    "/api/market-data-closing-auction",
    "/api/closing-auction-session?key=ALL",
    "/api/closing-auction-session",
    "/api/market-data-cas?key=ALL",
    "/api/market-data-cas",
    "/api/closing-auction",
    "/api/cas-market-watch",
    "/api/market-data-pre-open?key=CAS",
]


def _nse_session(prime_for=None):
    """
    NSE rejects bare requests; cookies must be primed by visiting a real page
    first. Which page matters — the option chain API only issues usable cookies
    after the option-chain page has been loaded in the same session.
    """
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    s.get("https://www.nseindia.com", timeout=12)
    time.sleep(0.7)
    page = {
        "option_chain": "https://www.nseindia.com/option-chain",
        "cas": "https://www.nseindia.com/market-data/closing-auction-session",
    }.get(prime_for, "https://www.nseindia.com/market-data/closing-auction-session")
    try:
        s.get(page, timeout=12)
    except Exception:
        pass
    time.sleep(0.5)
    return s


# Bumped whenever the scan output gains fields. A cache written by an older
# build lacks them, and silently serving it makes new features look broken —
# so a mismatch forces a rebuild instead.
OI_SCHEMA = 19
OI_CACHE = "fno_oi_scan.json"
OI_SNAP = "fno_oi_snapshots.json"
_oi_build = {"running": False, "done": 0, "total": 0, "stage": None,
             "started": None, "error": None}
_oi_lock = threading.Lock()


def _load_snapshots():
    try:
        with open(OI_SNAP) as f:
            d = json.load(f)
            return d if isinstance(d.get("by_date"), dict) else {"by_date": {}}
    except Exception:
        return {"by_date": {}}


def _pick_baseline(snaps):
    """
    Choose what today's option OI is compared against.

    A previous DAY's snapshot is strongly preferred, because futures OI change
    is day-over-day (it comes from historical candles). Comparing a 30-minute
    options delta against a full-day futures delta would mix time windows inside
    the same inference and quietly corrupt it.

    Falls back to an earlier snapshot from today only if no prior day exists,
    and labels it so the mismatch is visible rather than hidden.
    """
    by_date = snaps.get("by_date", {})
    today = datetime.date.today().isoformat()
    prior_days = sorted([d for d in by_date if d < today], reverse=True)
    if prior_days:
        d = prior_days[0]
        return by_date[d].get("data", {}), "previous_day", d
    todays = by_date.get(today)
    if todays and todays.get("data"):
        return todays["data"], "intraday", todays.get("stamp", today)
    return {}, "none", None


def _snapshot_quality(snaps, stamp):
    """
    A baseline taken mid-session is not close-to-close, so comparing it against
    futures OI (which IS close-to-close) mixes windows. Flag that rather than
    presenting the result as a clean day-over-day delta.
    """
    by_date = snaps.get("by_date", {})
    today = datetime.date.today().isoformat()
    prior = sorted([d for d in by_date if d < today], reverse=True)
    if not prior:
        return {"clean": False, "taken_at": None, "note": "no prior snapshot"}
    taken = by_date[prior[0]].get("stamp")
    if not taken:
        return {"clean": False, "taken_at": None, "note": "snapshot time unknown"}
    try:
        hh, mm = [int(x) for x in taken.split(":")]
    except Exception:
        return {"clean": False, "taken_at": taken, "note": "snapshot time unreadable"}
    minutes = hh * 60 + mm
    # anything from 3:25pm onward is close enough to settlement
    clean = minutes >= 15 * 60 + 25
    return {
        "clean": clean,
        "taken_at": taken,
        "note": (f"baseline captured at {taken}, near the close — comparable with futures OI"
                 if clean else
                 f"baseline captured at {taken}, mid-session. Option OI change measures from then to now, "
                 f"while futures OI change is close-to-close, so the two cover different windows.")
    }


def _oi_retention(hist, strike_hist=None):
    """How much of the option OI open N sessions ago is STILL open.

    Answers "who is still in the position" at the only resolution the stored
    data supports honestly. Two hard limits, both stated on the page:

      - It is NET, not gross. If 9% closed and 4% opened, retention reads 95%.
        It is therefore a FLOOR on turnover, never the count of who left.
      - OI cannot be split into holders and writers. Every contract has a buyer
        and a seller in equal number, always. Retention describes contracts
        outstanding, not participants on either side.

    `hist` is the per-stock aggregate list (date, ce_oi, pe_oi) already
    assembled for the PCR range.
    """
    if not hist or len(hist) < 2:
        return None
    rows = [h for h in hist if h.get("ce_oi") and h.get("pe_oi")]
    if len(rows) < 2:
        return None
    cur = rows[-1]
    out = []
    # Plausible bands for a NET change in total open interest. Anything beyond
    # these is a coverage artifact, not positioning: the stored totals are sums
    # over whatever strikes were captured, and NSE returns partial chains, the
    # fallback path covers only strikes near the money, and the strike window
    # shifts as spot moves. Deliberately generous — event days and expiry weeks
    # produce genuinely large moves, and a false "not comparable" costs less
    # than a confident 405%.
    BANDS = {1: (60.0, 160.0), 3: (40.0, 250.0), 5: (30.0, 350.0)}

    for back in (1, 3, 5):
        if len(rows) <= back:
            continue
        base = rows[-(back + 1)]
        ce = (cur["ce_oi"] / base["ce_oi"] * 100.0) if base["ce_oi"] else None
        pe = (cur["pe_oi"] / base["pe_oi"] * 100.0) if base["pe_oi"] else None

        reason = None
        # (a) direct test, when both days recorded their strike count: if the
        #     number of strikes summed moved much, the totals are not the same
        #     measurement and no ratio between them means anything
        n_now, n_then = cur.get("n"), base.get("n")
        if n_now and n_then:
            if abs(n_now - n_then) / max(n_then, 1) > 0.10:
                reason = f"strike coverage changed ({n_then} to {n_now} strikes)"
        # (b) source flip: an NSE full chain and a near-the-money Kite window
        #     are different sets by construction
        if not reason and cur.get("src") and base.get("src") and cur["src"] != base["src"]:
            reason = f"data source changed ({base['src']} to {cur['src']})"
        # (c) fallback heuristic for older rows with no coverage recorded
        if not reason:
            lo, hi = BANDS[back]
            outside = [v for v in (ce, pe) if v is not None and not (lo <= v <= hi)]
            if outside:
                reason = "change too large to be positioning — likely partial chain data"
            elif (ce is not None and pe is not None
                  and abs(ce - 100) > 40 and abs(pe - 100) > 40
                  and (ce - 100) * (pe - 100) > 0):
                # calls AND puts both moving hugely the same way is the chain
                # itself changing size, not two independent position changes
                reason = "calls and puts moved together — chain coverage differs"

        out.append({
            "sessions": back,
            "from_date": base["date"],
            "reliable": reason is None,
            "unreliable_reason": reason,
            "call_still_open_pct": (round(ce, 1) if ce is not None and not reason else None),
            "put_still_open_pct": (round(pe, 1) if pe is not None and not reason else None),
            # >100 means MORE is open now than then: net new positions, not
            # survivors. Worth naming, since "110% still open" reads oddly.
            "call_direction": (("added" if ce > 102 else "reduced" if ce < 98 else "flat")
                               if ce is not None and not reason else None),
            "put_direction": (("added" if pe > 102 else "reduced" if pe < 98 else "flat")
                              if pe is not None and not reason else None),
        })
    if not out:
        return None
    return {
        "windows": out,
        "sessions_stored": len(rows),
        "note": ("Net change in contracts outstanding, not a count of people. "
                 "Closures and new positions offset, so this is a floor on "
                 "turnover. Open interest cannot be split into holders and "
                 "writers — every contract has both, in equal number."),
    }


def _strikes_around_money(strikes, spot, span=9):
    """
    Keep both legs of the strikes nearest the money, so the chain can be
    rendered paired. Selecting by OI change magnitude instead drops whichever
    side moved less — usually the puts — and hides half the picture.
    """
    usable = [x for x in strikes if x.get("oi_chg") is not None]
    if not usable:
        return []
    levels = sorted({x["strike"] for x in usable}, key=lambda k: abs(k - spot))[:span]
    keep = set(levels)
    return sorted([x for x in usable if x["strike"] in keep],
                  key=lambda x: (x["strike"], x["type"]))


def _do_oi_scan(top_n=None, strikes_each_side=14, use_nse=True, nse_limit=None, only=None):
    """
    Build the F&O positioning picture.

    Futures OI change comes from historical data with oi=True — that is a real
    day-over-day number and is reliable from the first run. Option OI change
    needs a prior snapshot, so it is only available from the second run onward;
    day one still yields OI distribution, PCR and max pain, which do not need a
    delta.
    """
    import fno_analysis as fa
    import cash_context as cc
    try:
        with _oi_lock:
            _oi_build["stage"] = "fetching cash delivery data"
        deliv, deliv_day = fetch_delivery_data()
        deliv_avg, deliv_days = fetch_delivery_baseline()

        with _oi_lock:
            _oi_build["stage"] = "listing F&O underlyings"

        inst = _nfo_instruments()
        today = datetime.date.today()

        futs = [i for i in inst if i.get("segment") == "NFO-FUT" and i.get("expiry")]
        by_name = {}
        next_month = {}
        for i in futs:
            e = i["expiry"]
            e = e if isinstance(e, datetime.date) else None
            if not e or e < today:
                continue
            cur = by_name.get(i["name"])
            if cur is None or e < cur["expiry"]:
                # previous "current" becomes the next-month contract
                if cur is not None:
                    next_month[i["name"]] = cur
                by_name[i["name"]] = {"expiry": e, "inst": i}
            else:
                nxt = next_month.get(i["name"])
                if nxt is None or e < nxt["expiry"]:
                    next_month[i["name"]] = {"expiry": e, "inst": i}

        # Bank Nifty is included: it settles monthly, so its expiry cycle lines
        # up with the stock series and the comparison holds. Nifty and the rest
        # are excluded because their weekly expiries make "nearest expiry" mean
        # something different and the numbers stop being comparable.
        INDEX_NAMES = {"BANKNIFTY": "NIFTY BANK"}
        emap = equity_token_map()
        # Every listed F&O underlying is scanned. This used to be sliced with
        # [:top_n] on an UNRANKED dict, so ~20-30 names were dropped according
        # to nothing but Kite's dump ordering — VBL among them. A cap here is
        # only honest if the ranking is meaningful, and there was none.
        names = [n for n in by_name
                 if n not in ("NIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")
                 and (n in emap or n in INDEX_NAMES)]
        names.sort()
        if top_n:
            names = names[:top_n]
        if only:
            # single-stock refresh: same pipeline, universe of one. Nothing
            # below needs to know — it all flows from `names`.
            want = only.upper().strip()
            names = [n for n in names if n == want]
            if not names:
                raise ValueError(f"{want} is not a current F&O underlying")
        # keep the index at the front so it is scanned before any throttling
        names.sort(key=lambda n: (n not in INDEX_NAMES, n))

        with _oi_lock:
            _oi_build["total"] = len(names)
            _oi_build["done"] = 0
            _oi_build["stage"] = f"reading futures OI for {len(names)} stocks"

        # ---- futures: real OI change from historical ----
        fut_rows = {}
        fut_hist = {}
        fut_errors = []
        frm = today - datetime.timedelta(days=40)   # ~28 sessions, ample for a 5-day trend
        for n in names:
            f = by_name[n]["inst"]
            try:
                c = kite.historical_data(f["instrument_token"], frm, today, "day", oi=True)
                if len(c) >= 2:
                    fut_hist[n] = c
                    cur, prev = c[-1], c[-2]
                    oi_now = float(cur.get("oi") or 0)
                    oi_prev = float(prev.get("oi") or 0)
                    px_now, px_prev = float(cur["close"]), float(prev["close"])
                    if oi_prev > 0 and px_prev > 0:
                        fut_rows[n] = {
                            "symbol": f["tradingsymbol"],
                            "price": round(px_now, 2),
                            "price_chg_pct": round((px_now - px_prev) / px_prev * 100, 2),
                            "oi": int(oi_now),
                            "oi_chg": int(oi_now - oi_prev),
                            "oi_chg_pct": round((oi_now - oi_prev) / oi_prev * 100, 2),
                        }
            except Exception as e:
                # a bug in this block used to fail silently for every symbol and
                # look identical to "no data" — surface the first few instead
                if len(fut_errors) < 5:
                    fut_errors.append(f"{n}: {type(e).__name__}: {e}")
            time.sleep(0.32)
            with _oi_lock:
                _oi_build["done"] += 1

        if fut_errors:
            print(f"[oi] futures errors ({len(fut_errors)} shown): {fut_errors[0]}")

        # ---- next-month futures OI, to separate rollover from unwinding ----
        with _oi_lock:
            _oi_build["stage"] = "reading next-month futures OI"
        next_oi = {}
        nsyms, nmap = [], {}
        for n in fut_rows:
            nm = next_month.get(n)
            if nm:
                key = f"NFO:{nm['inst']['tradingsymbol']}"
                nsyms.append(key)
                nmap[key] = n
        for i in range(0, len(nsyms), 400):
            try:
                q = kite.quote(nsyms[i:i + 400])
                for k, v in q.items():
                    if k in nmap:
                        next_oi[nmap[k]] = {"oi": float(v.get("oi") or 0),
                                            "price": v.get("last_price"),
                                            "expiry": next_month[nmap[k]]["expiry"].isoformat()}
            except Exception:
                pass
            time.sleep(0.3)

        # ---- cash spot prices, for the basis ----
        with _oi_lock:
            _oi_build["stage"] = "reading cash prices"
        spot_px = {}
        for iname, imatch in INDEX_NAMES.items():
            if iname in fut_rows:
                try:
                    itok = get_index_token(imatch)
                    if itok:
                        iq = kite.quote([itok]).get(str(itok)) or {}
                        if iq.get("last_price"):
                            spot_px[iname] = iq["last_price"]
                except Exception:
                    pass
        want = [n for n in fut_rows if n in emap]
        for i in range(0, len(want), 400):
            chunk = [f"NSE:{n}" for n in want[i:i + 400]]
            try:
                q = kite.quote(chunk)
                for k, v in q.items():
                    spot_px[k.split(":", 1)[1]] = v.get("last_price")
            except Exception:
                pass
            time.sleep(0.3)

        # ---- options: NSE first (it supplies OI change directly) ----
        nse_chains, nse_fail = {}, []
        nse_attempted = set()
        if use_nse:
            with _oi_lock:
                _oi_build["stage"] = "trying NSE option chains (gives same-day OI change)"
            # Ranked by futures OI so the biggest names are tried FIRST under
            # throttling — but no longer truncated. Anything NSE refuses falls
            # back to snapshot comparison rather than vanishing from the page.
            ranked = sorted(fut_rows.keys(), key=lambda n: -fut_rows[n]["oi"])
            if nse_limit:
                ranked = ranked[:nse_limit]
            # the index must be in the batch regardless of where OI ranks it
            for iname in INDEX_NAMES:
                if iname in fut_rows and iname not in ranked:
                    ranked.insert(0, iname)
            want_exp = {n: by_name[n]["expiry"] for n in ranked if n in by_name}
            nse_chains, nse_fail = _nse_chain_batch(ranked, expiries=want_exp)
            nse_attempted = set(ranked)
            print(f"[oi] NSE chains: {len(nse_chains)} ok, {len(nse_fail)} failed")

        # ---- options: Kite bulk quotes for whatever NSE did not cover ----
        with _oi_lock:
            _oi_build["stage"] = "reading option chains"

        opts = [i for i in inst if i.get("segment") == "NFO-OPT" and i.get("expiry")]
        chain_syms, chain_meta = [], {}
        for n in list(fut_rows.keys()):
            if n in nse_chains:
                continue          # already have exchange-computed deltas
            exp = by_name[n]["expiry"]
            spot = emap.get(n)
            px = fut_rows[n]["price"]
            near = [i for i in opts if i["name"] == n
                    and (i["expiry"] if isinstance(i["expiry"], datetime.date) else None) == exp]
            if not near:
                continue
            ks = sorted({float(i["strike"]) for i in near if i.get("strike")})
            if not ks:
                continue
            # Max pain and PCR both need a wide chain: open interest often piles
            # up well away from spot, and a narrow window simply cannot see it.
            # Indices carry far denser strikes, so they need a wider span again.
            span = strikes_each_side * (2 if n in INDEX_NAMES else 1)
            ks.sort(key=lambda k: abs(k - px))
            keep = set(ks[:span * 2 + 1])
            for i in near:
                if float(i.get("strike") or 0) in keep and i.get("instrument_type") in ("CE", "PE"):
                    key = f"NFO:{i['tradingsymbol']}"
                    chain_syms.append(key)
                    chain_meta[key] = {"name": n, "strike": float(i["strike"]),
                                       "type": i["instrument_type"]}

        quotes = {}
        quote_errors = []
        BATCH = 150          # Kite tolerates large batches poorly once the
                             # instrument list runs to thousands; smaller and
                             # slower beats a silent total failure
        for i in range(0, len(chain_syms), BATCH):
            try:
                quotes.update(kite.quote(chain_syms[i:i + BATCH]))
            except Exception as e:
                if len(quote_errors) < 5:
                    quote_errors.append(str(e)[:160])
            time.sleep(0.4)
        print(f"[oi] option quotes: {len(quotes)} of {len(chain_syms)} requested"
              + (f" | {len(quote_errors)} batch errors: {quote_errors[0]}" if quote_errors else ""))

        snaps = _load_snapshots()
        prev_snap, delta_basis, basis_stamp = _pick_baseline(snaps)
        snap_q = _snapshot_quality(snaps, basis_stamp)
        new_snap = {}

        per_stock = {}
        for key, meta in chain_meta.items():
            q = quotes.get(key)
            if not q:
                continue
            oi = float(q.get("oi") or 0)
            ltp = q.get("last_price")
            new_snap[key] = {"oi": oi, "ltp": ltp}
            p = prev_snap.get(key)
            # A strike absent from the baseline has NO measurable change. Falling
            # back to the current value would report zero, which is silently
            # wrong: the strike window shifts as spot moves, so on any decent
            # move most strikes are new and the aggregate collapses toward zero.
            has_base = bool(p and p.get("oi") is not None)
            per_stock.setdefault(meta["name"], []).append({
                "strike": meta["strike"], "type": meta["type"],
                "oi": oi,
                "oi_chg": (oi - float(p["oi"])) if has_base else None,
                "has_baseline": has_base,
                "ltp": ltp, "prev_ltp": (p or {}).get("ltp"),
                "volume": q.get("volume") or q.get("volume_traded") or 0,
            })

        # NSE deltas are the exchange's own day-over-day figure, so they align
        # with futures OI change without needing any stored snapshot
        # UNIT MISMATCH: NSE publishes option OI in CONTRACTS (lots) while Kite
        # reports it in UNITS (shares). Left unconverted, an NSE-sourced card
        # shows option OI in thousands next to a futures OI in lakhs, and no
        # two cards are comparable. Multiply NSE figures by the lot size.
        lot_by_name = {}
        for i in inst:
            if i.get("segment") == "NFO-FUT" and i.get("name") and i.get("lot_size"):
                lot_by_name.setdefault(i["name"], int(i["lot_size"]))

        nse_sourced = set()
        for n, ch in nse_chains.items():
            if not ch.get("strikes"):
                continue
            lot = lot_by_name.get(n, 1)
            if lot > 1:
                for st in ch["strikes"]:
                    if st.get("oi") is not None:
                        st["oi"] = st["oi"] * lot
                    if st.get("oi_chg") is not None:
                        st["oi_chg"] = st["oi_chg"] * lot
                    if st.get("volume"):
                        st["volume"] = st["volume"] * lot
            per_stock[n] = ch["strikes"]
            nse_sourced.add(n)
            # ARCHIVE (added 18 Aug 2026): NSE-sourced strikes were never
            # snapshotted, because same-day deltas came from the exchange and
            # the Kite quote path was skipped. That left the per-strike store
            # holding only NSE FAILURES — 2-3 stocks a day — so no multi-day
            # strike history could ever accumulate. These rows are already
            # multiplied by lot size above, so they are in SHARES, the same
            # unit Kite reports and the same unit prev_snap holds. Mixing units
            # would plant 75x jumps whenever a stock flipped source.
            for st in ch["strikes"]:
                stk, typ, o = st.get("strike"), st.get("type"), st.get("oi")
                if stk is None or typ not in ("CE", "PE") or o is None:
                    continue
                new_snap[f"ARCH:{n}:{int(float(stk))}:{typ}"] = {
                    "oi": float(o), "ltp": st.get("ltp"), "src": "nse",
                }

        have_prev = bool(prev_snap)
        snapshot_aligned = (delta_basis == "previous_day") and snap_q["clean"]

        # option aggregates from previous sessions, for PCR range and OI trend
        opt_hist = {}
        for iso in sorted(snaps.get("by_date", {})):
            for sym, a in (snaps["by_date"][iso].get("agg") or {}).items():
                opt_hist.setdefault(sym, []).append({
                    "date": iso, "ce_oi": a.get("ce_oi"),
                    "pe_oi": a.get("pe_oi"), "pcr": a.get("pcr")})

        # per-strike archive coverage, so the page can say when the strike-level
        # version will be usable instead of silently showing nothing
        arch_dates = sorted(d for d, v in snaps.get("by_date", {}).items()
                            if any(k.startswith("ARCH:") for k in (v.get("data") or {})))

        results = []
        for n, strikes in per_stock.items():
            f = fut_rows.get(n)
            if not f:
                continue
            fut_cls = fa.classify_futures(f["price_chg_pct"], f["oi_chg_pct"])
            # A daily OI move beyond ~40% is almost never genuine positioning.
            # It usually means expiry rollover, a contract with very little OI,
            # or a partial candle during the session. Better flagged than
            # silently classified as high-conviction.
            f["suspect"] = bool(abs(f["oi_chg_pct"]) > 40 or f["oi"] < 5000)
            f["suspect_reason"] = (
                "OI moved more than 40% in a day — likely expiry rollover or a partial session candle"
                if abs(f["oi_chg_pct"]) > 40 else
                ("very low absolute OI, so percentage changes exaggerate" if f["oi"] < 5000 else None))
            trend = fa.futures_oi_trend(fut_hist.get(n, []), days=5)
            recon = fa.reconcile_day_vs_trend(fut_cls, trend)

            # ---- rollover: is falling OI a position closing, or moving on? ----
            nx = next_oi.get(n)
            dte_cur = (by_name[n]["expiry"] - today).days
            roll = None
            if nx and nx["oi"] > 0:
                combined = f["oi"] + nx["oi"]
                roll = {
                    "next_expiry": nx["expiry"],
                    "next_oi": int(nx["oi"]),
                    "combined_oi": int(combined),
                    "next_share_pct": round(nx["oi"] / combined * 100, 1),
                    # near expiry a rising next-month share is ordinary rollover,
                    # not conviction leaving the stock
                    "in_roll_window": bool(dte_cur <= 7),
                }
                if f["oi_chg"] < 0 and nx["oi"] > abs(f["oi_chg"]) * 0.5 and dte_cur <= 10:
                    roll["likely_rollover"] = True
                    roll["note"] = (f"Current-month OI fell while {roll['next_share_pct']}% of total open interest "
                                    f"already sits in the {nx['expiry']} contract. With {dte_cur} days to expiry this "
                                    f"is most likely rollover rather than positions being closed.")
                else:
                    roll["likely_rollover"] = False

            # ---- cash-market context: separates arbitrage from direction ----
            sp = spot_px.get(n)
            dte = (by_name[n]["expiry"] - today).days
            basis = cc.annualised_basis(sp, f["price"], dte) if sp else None
            basis_cls = cc.classify_basis(basis["annualised_pct"] if basis else None)
            is_index = n in INDEX_NAMES
            if is_index:
                # an index is not deliverable, so the cash leg does not exist
                dp = None
                deliv_cls = {"state": "unknown", "note": "Indices are not deliverable — no delivery data exists."}
                verdict = cc.index_positioning(fut_cls, basis_cls)
            else:
                drec = deliv.get(n.upper()) or {}
                dp = drec.get("deliv_pct")
                # judge against this stock's own norm, not a blanket threshold
                base = deliv_avg.get(n.upper()) or {}
                deliv_cls = cc.classify_delivery(dp, base.get("mean"), base.get("sd"),
                                                 base.get("streak"))
                verdict = cc.true_positioning(fut_cls, basis_cls, deliv_cls)
            agg = fa.aggregate_option_flow(strikes, f["price"], f["price_chg_pct"])

            # classify each strike individually so the writing/buying call is
            # inspectable rather than only available as an aggregate verdict
            for st in strikes:
                st["flow"] = None
                if st.get("oi_chg") is None:
                    continue

                # Premium change comes from one of two places. NSE supplies it
                # directly as pChange; the snapshot path has to derive it from a
                # stored previous LTP. Only the second was being used, so every
                # NSE-sourced strike silently went unclassified.
                prem_chg = None
                if st.get("pct_chg") is not None:
                    try:
                        prem_chg = float(st["pct_chg"])
                    except (TypeError, ValueError):
                        prem_chg = None
                if prem_chg is None:
                    prev = st.get("prev_ltp")
                    if st.get("ltp") and prev and prev > 0:
                        prem_chg = (st["ltp"] - prev) / prev * 100
                if prem_chg is None:
                    continue

                st["prem_chg_pct"] = round(prem_chg, 1)
                # OI change as a percentage of the prior level — this is the
                # column option chains normally show, and the one to compare
                # against any external chain
                prior_oi = (st.get("oi") or 0) - st["oi_chg"]
                st["oi_chg_pct"] = round(st["oi_chg"] / prior_oi * 100, 1) if prior_oi > 0 else None
                fl = fa.classify_option_flow(st["oi_chg"], prem_chg,
                                             f["price_chg_pct"], st["type"])
                st["flow"] = fl["flow"] if fl else None
            mp = fa.max_pain(strikes)

            # normalise the raw numbers against this contract's own history
            oi_z = fa.oi_change_zscore(fut_hist.get(n, []))
            hist_rows = opt_hist.get(n, [])
            pcr_hist = [h["pcr"] for h in hist_rows if h.get("pcr")]
            pcr_ctx = fa.pcr_context(agg.get("pcr_oi"), pcr_hist)
            opt_trend = fa.option_oi_trend(hist_rows)
            retention = _oi_retention(hist_rows)

            from_nse = n in nse_sourced

            # Materiality: option OI moves that are tiny against the position
            # already outstanding carry no information. Inferring "call buying"
            # from 1,100 contracts against lakhs of open interest is reading
            # noise, so the structure is withheld below a floor.
            total_opt_oi = (agg["ce_oi"] or 0) + (agg["pe_oi"] or 0)
            moved = abs(agg["ce_oi_chg"] or 0) + abs(agg["pe_oi_chg"] or 0)

            # Coverage: what share of strikes actually had a baseline to compare
            # against. Low coverage means the aggregate is built from a partial
            # chain and understates the real move.
            strikes_all = per_stock.get(n, [])
            with_base = sum(1 for x in strikes_all if x.get("has_baseline") or x.get("oi_chg") is not None)
            coverage = round(with_base / len(strikes_all), 2) if strikes_all else 0

            material = (total_opt_oi > 0 and (moved / total_opt_oi) >= 0.02
                        and moved >= 5000 and coverage >= 0.6)

            can_infer = (from_nse or snapshot_aligned) and material
            structure = fa.infer_structure(fut_cls, agg) if can_infer else None

            results.append({
                "name": n,
                "future": f,
                "futures_class": fut_cls,
                "trend": trend,
                "reconcile": recon,
                "is_index": is_index,
                "display_name": INDEX_NAMES.get(n, n),
                "expiry": by_name[n]["expiry"].isoformat(),
                "days_to_expiry": dte_cur,
                "rollover": roll,
                "spot": round(sp, 2) if sp else None,
                "basis": basis,
                "basis_class": basis_cls,
                "delivery_pct": dp,
                "delivery_avg": (deliv_avg.get(n.upper()) or {}).get("mean"),
                "delivery_sd": (deliv_avg.get(n.upper()) or {}).get("sd"),
                "delivery_z": deliv_cls.get("z"),
                "delivery_streak": (deliv_avg.get(n.upper()) or {}).get("streak"),
                "delivery_class": deliv_cls,
                "verdict": verdict,
                "options": agg,
                "retention": retention,
                "max_pain": mp,
                "max_pain_vs_price": round((mp - f["price"]) / f["price"] * 100, 2) if mp else None,
                "structure": structure,
                "oi_zscore": oi_z,
                "pcr_context": pcr_ctx,
                "option_trend": opt_trend,
                "expiry_signal": fa.expiry_signal(
                    fut_cls, oi_z, agg if can_infer else None,
                    pcr_ctx, opt_trend, dte_cur),
                # An option chain is read PAIRED by strike, calls against puts.
                # Sorting everything by OI change and truncating buried the puts
                # whenever calls moved more, which is most of the time.
                "strikes": _strikes_around_money(strikes, f["price"], span=9),
                "notional_oi_cr": round(f["oi"] * f["price"] / 1e7, 1),
                "oi_source": "nse" if from_nse else ("snapshot" if snapshot_aligned else "none"),
                "nse_attempted": bool(n in nse_attempted),
                "lot_size": lot_by_name.get(n),
                "oi_units": "shares",
                "opt_material": bool(material),
                "opt_move_pct": round(moved / total_opt_oi * 100, 2) if total_opt_oi else None,
                "opt_coverage": coverage,
            })

        def rank(r):
            if r.get("is_index"):
                return -1e9          # index pinned to the top
            base = r["futures_class"]["strength"] * (1 + abs(r["future"]["oi_chg_pct"]) / 20)
            t = r.get("trend")
            if t and t["kind"] != "no_trend":
                base *= 1 + (t["conviction"] / 100) * 0.6   # reward sustained builds
            if r.get("reconcile", {}).get("state") == "diverging":
                base *= 1.25                                 # divergences are worth surfacing
            if r["future"].get("suspect"):
                base *= 0.25                                 # push data artifacts down
            return -base
        results.sort(key=rank)

        today_key = today.isoformat()
        # keep per-stock option aggregates alongside the raw strikes: PCR and
        # OI totals only mean something against their own recent range, and a
        # single day cannot supply that
        agg_today = {}
        for r in results:
            o = r.get("options") or {}
            if o.get("ce_oi") and o.get("pe_oi"):
                agg_today[r["name"]] = {"ce_oi": o["ce_oi"], "pe_oi": o["pe_oi"],
                                        "pcr": o.get("pcr_oi"),
                                        # strike count: the totals above are sums
                                        # over whatever strikes were captured, and
                                        # that set is NOT stable day to day. Without
                                        # this, a coverage change is indistinguishable
                                        # from a positioning change.
                                        "n": len(r.get("strikes") or []),
                                        "src": "nse" if r.get("from_nse") else "kite"}
        if only:
            # A single-stock run holds one stock's strikes and aggregates.
            # Writing it wholesale would DESTROY today's rows for the other 208
            # and break every retention and PCR-range calculation that depends
            # on them. Merge into today's entry instead.
            today_entry = snaps.setdefault("by_date", {}).setdefault(
                today_key, {"data": {}, "agg": {}, "stamp": None})
            today_entry.setdefault("data", {}).update(new_snap)
            today_entry.setdefault("agg", {}).update(agg_today)
            # stamp is left as the FULL scan's — it describes the baseline for
            # the other stocks, and _snapshot_quality reads it for all of them
            if not today_entry.get("stamp"):
                today_entry["stamp"] = datetime.datetime.now().strftime("%H:%M")
        else:
            snaps.setdefault("by_date", {})[today_key] = {
                "data": new_snap,
                "agg": agg_today,
                "stamp": datetime.datetime.now().strftime("%H:%M"),
            }
        # keep a week; older snapshots are of no use and the file gets large
        for d in sorted(snaps["by_date"].keys())[:-7]:
            snaps["by_date"].pop(d, None)
        try:
            with open(OI_SNAP, "w") as fjs:
                json.dump(snaps, fjs)
        except Exception:
            pass

        payload = {
            "schema": OI_SCHEMA,
            "captured_at": datetime.datetime.now().strftime("%H:%M:%S"),
            "date": today.isoformat(),
            "generated_at": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
            "count": len(results),
            "have_option_deltas": bool(nse_sourced) or have_prev,
            "delta_basis": delta_basis,
            "delta_basis_stamp": basis_stamp,
            "structure_available": bool(nse_sourced) or snapshot_aligned,
            "nse_count": len(nse_sourced),
            "nse_failures": len(nse_fail),
            "nse_sample_errors": nse_fail[:3],
            "nse_attempted": len(nse_attempted),
            "nse_limit": nse_limit,          # None = no cap, every underlying attempted
            "universe_capped": bool(top_n),   # False = full F&O list, nothing dropped
            "strike_archive": {
                "ready": len(arch_dates) >= 5,
                "days_needed": max(0, 5 - len(arch_dates)),
                "days_stored": len(arch_dates),
                "dates": arch_dates,
                "archived_today": sum(1 for k in new_snap if k.startswith("ARCH:")),
                "note": ("Per-strike history accumulates forward from 18 Aug 2026 only. "
                         "It cannot be backfilled: before this, strikes were stored just "
                         "for stocks NSE refused. Strike-level retention needs ~5 sessions."),
            },
            "pipeline": {
                "universe": len(names),
                "futures_ok": len(fut_rows),
                "futures_errors": fut_errors[:3],
                "option_instruments_requested": len(chain_syms),
                "option_quotes_received": len(quotes),
                "quote_batch_errors": quote_errors[:3],
                "stocks_with_chains": len(per_stock),
                "results_built": len(results),
            },
            "snapshot_quality": snap_q,
            "delivery_date": deliv_day,
            "delivery_baseline_days": deliv_days,
            "delivery_count": len(deliv),
            "stocks": results,
        }
        if only:
            # splice the refreshed stock into the existing cache, keeping the
            # full scan's metadata; only the one card changes
            try:
                with open(OI_CACHE) as fjs:
                    base = json.load(fjs)
            except Exception:
                base = None
            if base and isinstance(base.get("stocks"), list):
                fresh = {r["name"]: r for r in results}
                merged, replaced = [], 0
                for old in base["stocks"]:
                    if old.get("name") in fresh:
                        merged.append(fresh.pop(old["name"]))
                        replaced += 1
                    else:
                        merged.append(old)
                merged.extend(fresh.values())          # newly appearing name
                base["stocks"] = merged
                base["count"] = len(merged)
                base["partial_refresh"] = {
                    "symbol": only.upper(),
                    "at": datetime.datetime.now().strftime("%H:%M:%S"),
                    "replaced": replaced,
                }
                payload = base
        with open(OI_CACHE, "w") as fjs:
            json.dump(payload, fjs)
        print(f"[oi] scan complete: {len(results)} stocks | NSE deltas={len(nse_sourced)} | snapshot basis={delta_basis}")
        with _oi_lock:
            _oi_build["error"] = None
    except Exception as e:
        import traceback; traceback.print_exc()
        with _oi_lock:
            _oi_build["error"] = f"{type(e).__name__}: {e}"
    finally:
        with _oi_lock:
            _oi_build["running"] = False
            _oi_build["stage"] = None


NSE_INDEX_SYMBOLS = {"BANKNIFTY", "NIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def _find_rows(obj, depth=0):
    """
    Locate the strike array anywhere in an NSE payload.

    The v3 migration moved things around and is undocumented, so rather than
    assume a path this walks the structure looking for the shape that matters:
    a list of dicts carrying strikePrice with a CE or PE leg.
    """
    if depth > 4:
        return None
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and "strikePrice" in obj[0] \
           and ("CE" in obj[0] or "PE" in obj[0]):
            return obj
        return None
    if isinstance(obj, dict):
        for key in ("data", "records", "filtered", "optionChain", "chain", "result"):
            if key in obj:
                found = _find_rows(obj[key], depth + 1)
                if found:
                    return found
        for v in obj.values():
            found = _find_rows(v, depth + 1)
            if found:
                return found
    return None


def _find_expiries(obj, depth=0):
    """Locate the expiry list, wherever NSE has put it in this version."""
    if depth > 4 or not isinstance(obj, (dict, list)):
        return []
    if isinstance(obj, dict):
        for key in ("expiryDates", "expiryDatesList", "expiries", "expiryDate"):
            v = obj.get(key)
            if isinstance(v, list) and v and isinstance(v[0], str):
                return v
        for v in obj.values():
            found = _find_expiries(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj[:8]:
            found = _find_expiries(v, depth + 1)
            if found:
                return found
    return []


def _parse_chain_payload(data, want_expiry=None):
    """Pull strikes out of an NSE option-chain payload, whatever version it is."""
    rows = _find_rows(data) or []
    expiries = _find_expiries(data)
    rec = data.get("records") if isinstance(data, dict) else None
    underlying = None
    for src in (rec if isinstance(rec, dict) else {}, data if isinstance(data, dict) else {}):
        underlying = underlying or src.get("underlyingValue")
    if not rows:
        return None, expiries, underlying

    near = want_expiry or (expiries[0] if expiries else None)
    strikes = []
    for row in rows:
        if near and row.get("expiryDate") and row.get("expiryDate") != near:
            continue
        k = row.get("strikePrice")
        if k is None:
            continue
        for t in ("CE", "PE"):
            leg = row.get(t)
            if not leg:
                continue
            strikes.append({
                "strike": float(k),
                "type": t,
                "oi": float(leg.get("openInterest") or 0),
                "oi_chg": float(leg.get("changeinOpenInterest") or 0),
                "ltp": leg.get("lastPrice"),
                "prev_ltp": None,
                "pct_chg": leg.get("pChange"),
                "iv": leg.get("impliedVolatility"),
                "volume": leg.get("totalTradedVolume") or 0,
            })
    return strikes, expiries, underlying


def fetch_nse_option_chain(symbol, session=None, want_expiry=None):
    """
    Option chain from NSE, which supplies changeinOpenInterest directly — the
    exchange's own day-over-day delta, so no stored snapshot is needed.

    NSE moved to an /option-chain-v3 endpoint and retired the older
    option-chain-indices and option-chain-equities paths, which now return 404
    behind an Akamai error page. Several URL shapes are tried because the
    migration is undocumented and the older paths still work intermittently.

    v3 also wants an explicit expiry. When it is omitted the response often
    carries expiryDates but no rows, so the first expiry is read and the call
    repeated.
    """
    s = session or _nse_session(prime_for="option_chain")
    is_index = symbol.upper() in NSE_INDEX_SYMBOLS
    ctype = "Indices" if is_index else "Equity"
    ref = "https://www.nseindia.com/option-chain"

    attempts = [
        f"https://www.nseindia.com/api/option-chain-v3?type={ctype}&symbol={symbol}",
        f"https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}",
        (f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}" if is_index
         else f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"),
    ]

    errors = []

    # v3 usually needs an explicit expiry, and contract-info is what lists them.
    known_expiries = []
    try:
        ci = s.get(f"https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}",
                   headers={"Referer": ref}, timeout=15)
        if ci.status_code == 200 and "json" in (ci.headers.get("content-type") or "").lower():
            known_expiries = _find_expiries(ci.json())
    except Exception:
        pass
    if known_expiries:
        # match the futures expiry where possible: taking the first entry blindly
        # can pull a different month and the OI would not be comparable
        pick = known_expiries[0]
        if want_expiry:
            for e in known_expiries:
                try:
                    if datetime.datetime.strptime(e, "%d-%b-%Y").date() == want_expiry:
                        pick = e
                        break
                except ValueError:
                    continue
        attempts.insert(0, f"https://www.nseindia.com/api/option-chain-v3?type={ctype}"
                           f"&symbol={symbol}&expiry={pick}")
    time.sleep(0.3)

    for url in attempts:
        try:
            r = s.get(url, headers={"Referer": ref}, timeout=15)
            if r.status_code != 200:
                errors.append(f"{url.split('?')[0].rsplit('/', 1)[-1]}: HTTP {r.status_code}")
                continue
            if "json" not in (r.headers.get("content-type") or "").lower():
                errors.append(f"{url.split('?')[0].rsplit('/', 1)[-1]}: not JSON")
                continue
            data = r.json()
            strikes, expiries, underlying = _parse_chain_payload(data)

            # v3 commonly returns the expiry list but no rows until asked for one
            if not strikes and expiries and "option-chain-v3" in url:
                time.sleep(0.4)
                r2 = s.get(url + f"&expiry={expiries[0]}", headers={"Referer": ref}, timeout=15)
                if r2.status_code == 200 and "json" in (r2.headers.get("content-type") or "").lower():
                    strikes, expiries2, underlying2 = _parse_chain_payload(r2.json(), expiries[0])
                    underlying = underlying or underlying2
                    expiries = expiries or expiries2

            if strikes:
                return {"expiry": expiries[0] if expiries else None,
                        "underlying": underlying, "strikes": strikes,
                        "source": url.split("?")[0].rsplit("/", 1)[-1]}
            errors.append(f"{url.split('?')[0].rsplit('/', 1)[-1]}: empty")
        except Exception as e:
            errors.append(f"{url.split('?')[0].rsplit('/', 1)[-1]}: {type(e).__name__}")
        time.sleep(0.3)

    raise RuntimeError("; ".join(errors) or "no endpoint responded")


def _nse_chain_batch(names, pace=0.75, max_failures=25, expiries=None):
    """
    Pull chains for many symbols, stopping early once NSE starts refusing.
    Partial results are more useful than none, so failures are collected
    rather than raised.
    """
    out, failures = {}, []
    try:
        s = _nse_session(prime_for="option_chain")
    except Exception as e:
        return {}, [f"session: {e}"]

    consecutive = 0
    for n in names:
        try:
            out[n] = fetch_nse_option_chain(n, s, want_expiry=(expiries or {}).get(n))
            consecutive = 0
        except Exception as e:
            failures.append(f"{n}: {e}")
            consecutive += 1
            if consecutive >= 5:
                # NSE has almost certainly started blocking; re-prime once
                try:
                    s = _nse_session(prime_for="option_chain")
                    consecutive = 0
                except Exception:
                    break
        if len(failures) >= max_failures and len(out) == 0:
            break
        time.sleep(pace)
    return out, failures


@app.route("/api/probe-option-chain")
def api_probe_option_chain():
    """Check whether NSE's option chain endpoint works from this machine."""
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    sym = request.args.get("symbol", "RELIANCE").upper()

    # raw view: walk every candidate URL and report what each returned
    if request.args.get("raw") == "1":
        is_idx = sym in NSE_INDEX_SYMBOLS
        ctype = "Indices" if is_idx else "Equity"
        urls = [
            f"https://www.nseindia.com/api/option-chain-v3?type={ctype}&symbol={sym}",
            f"https://www.nseindia.com/api/option-chain-contract-info?symbol={sym}",
            (f"https://www.nseindia.com/api/option-chain-indices?symbol={sym}" if is_idx
             else f"https://www.nseindia.com/api/option-chain-equities?symbol={sym}"),
        ]
        sess = _nse_session(prime_for="option_chain")
        out = {"symbol": sym, "type": ctype, "cookies_held": len(sess.cookies), "attempts": []}

        # contract-info carries the expiry list; v3 returns an empty object
        # without it, so resolve the expiry and put the chained URL first
        try:
            ci = sess.get(f"https://www.nseindia.com/api/option-chain-contract-info?symbol={sym}",
                          headers={"Referer": "https://www.nseindia.com/option-chain"}, timeout=15)
            if ci.status_code == 200:
                exps = _find_expiries(ci.json())
                out["resolved_expiries"] = exps[:5]
                if exps:
                    urls.insert(0, f"https://www.nseindia.com/api/option-chain-v3?type={ctype}"
                                   f"&symbol={sym}&expiry={exps[0]}")
        except Exception as e:
            out["contract_info_error"] = str(e)
        time.sleep(0.3)
        for u in urls:
            row = {"url": u}
            try:
                r = sess.get(u, headers={"Referer": "https://www.nseindia.com/option-chain"}, timeout=15)
                row["status"] = r.status_code
                row["content_type"] = r.headers.get("content-type")
                row["body_length"] = len(r.text)
                if "json" in (r.headers.get("content-type") or "").lower():
                    j = r.json()
                    row["top_level_keys"] = list(j.keys()) if isinstance(j, dict) else str(type(j))
                    recs = j.get("records") if isinstance(j, dict) else None
                    src = recs if isinstance(recs, dict) else (j if isinstance(j, dict) else {})
                    row["data_rows"] = len(src.get("data") or [])
                    row["expiryDates"] = (src.get("expiryDates") or [])[:5]
                    row["underlyingValue"] = src.get("underlyingValue")
                    d0 = (src.get("data") or [{}])[0]
                    if isinstance(d0, dict):
                        row["first_row_keys"] = list(d0.keys())
                else:
                    row["body_snippet"] = r.text[:180]
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"
            out["attempts"].append(row)
            time.sleep(0.4)
        working = [a["url"] for a in out["attempts"] if a.get("data_rows")]
        out["working"] = working
        out["verdict"] = ("Found a working endpoint." if working else
                          "No endpoint returned rows. If v3 shows expiryDates but zero data_rows, "
                          "it needs an explicit &expiry= value — the fetcher already retries that way.")
        return jsonify(out)

    try:
        chain = fetch_nse_option_chain(sym)
        is_idx = sym in NSE_INDEX_SYMBOLS
        ce = [x for x in chain["strikes"] if x["type"] == "CE"]
        pe = [x for x in chain["strikes"] if x["type"] == "PE"]
        return jsonify({
            "ok": True, "symbol": sym,
            "endpoint": "option-chain-indices" if is_idx else "option-chain-equities",
            "expiry": chain["expiry"],
            "underlying": chain["underlying"],
            "ce_strikes": len(ce), "pe_strikes": len(pe),
            "total_ce_oi_chg": sum(x["oi_chg"] for x in ce),
            "total_pe_oi_chg": sum(x["oi_chg"] for x in pe),
            "sample": sorted(chain["strikes"], key=lambda x: -abs(x["oi_chg"]))[:6],
            "note": "NSE is reachable and supplying OI change directly. The F&O scan can use it for same-day analysis.",
        })
    except Exception as e:
        return jsonify({"ok": False, "symbol": sym, "error": str(e),
                        "note": "NSE refused or is unreachable. The scan will fall back to snapshot comparison, "
                                "which needs a prior day's data."}), 502


DELIV_CACHE = "delivery_data.json"


def fetch_delivery_data(days_back=1):
    """
    Delivery percentage per stock from NSE's full bhavcopy.

    This is the file that carries DELIV_PER — the share of traded volume that
    actually settled into demat rather than being squared off intraday. It is
    published after the session, so intraday runs get the previous day's file.
    """
    cached = {}
    try:
        with open(DELIV_CACHE) as f:
            cached = json.load(f)
    except Exception:
        pass

    want = (datetime.date.today() - datetime.timedelta(days=days_back - 1))
    # walk back over weekends and holidays until a file exists
    for back in range(0, 7):
        d = want - datetime.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        key = d.isoformat()
        if cached.get("date") == key and cached.get("data"):
            return cached["data"], key

        ds = d.strftime("%d%m%Y")
        urls = [
            f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv",
            f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv",
        ]
        for url in urls:
            try:
                s = _nse_session()
                r = s.get(url, timeout=25)
                if r.status_code != 200 or "SYMBOL" not in r.text[:400].upper():
                    continue
                out = {}
                lines = r.text.strip().split("\n")
                header = [h.strip().upper() for h in lines[0].split(",")]
                def col(*names):
                    for n in names:
                        if n in header:
                            return header.index(n)
                    return None
                i_sym = col("SYMBOL")
                i_ser = col("SERIES")
                i_dp = col("DELIV_PER", "DELIVERY_PERCENTAGE")
                i_dq = col("DELIV_QTY")
                i_vol = col("TTL_TRD_QNTY", "TOTAL_TRADED_QUANTITY")
                if i_sym is None or i_dp is None:
                    continue
                for line in lines[1:]:
                    p = [x.strip() for x in line.split(",")]
                    if len(p) <= max(filter(None, [i_sym, i_ser, i_dp])):
                        continue
                    if i_ser is not None and p[i_ser].upper() not in ("EQ", "BE"):
                        continue
                    try:
                        dp = float(p[i_dp])
                    except ValueError:
                        continue
                    rec = {"deliv_pct": dp}
                    if i_dq is not None and i_vol is not None:
                        try:
                            rec["deliv_qty"] = float(p[i_dq])
                            rec["volume"] = float(p[i_vol])
                        except ValueError:
                            pass
                    out[p[i_sym].upper()] = rec
                if len(out) > 200:
                    try:
                        with open(DELIV_CACHE, "w") as f:
                            json.dump({"date": key, "data": out}, f)
                    except Exception:
                        pass
                    print(f"[delivery] {len(out)} stocks from bhavcopy {key}")
                    return out, key
            except Exception:
                continue
    return {}, None


DELIV_HIST = "delivery_history.json"


def fetch_delivery_baseline(sessions=15):
    """
    Per-stock average delivery percentage over recent sessions.

    A single day's figure is close to meaningless on its own: some counters
    routinely settle 20% of volume into demat and others 70%, so an absolute
    threshold flags the habitually-high names every day and never flags a
    genuine surge in a low-delivery stock. What matters is the deviation from
    each stock's own norm, which needs a baseline.

    Cached — the bhavcopy for a past session never changes.
    """
    cache = {}
    try:
        with open(DELIV_HIST) as f:
            cache = json.load(f)
    except Exception:
        cache = {}

    days = cache.get("days") or {}
    today = datetime.date.today()
    wanted, d = [], today - datetime.timedelta(days=1)
    while len(wanted) < sessions and (today - d).days < sessions * 2 + 12:
        if d.weekday() < 5:
            wanted.append(d.isoformat())
        d -= datetime.timedelta(days=1)

    missing = [x for x in wanted if x not in days]
    for iso in missing[:sessions]:
        dt = datetime.date.fromisoformat(iso)
        ds = dt.strftime("%d%m%Y")
        got = None
        for url in (f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv",
                    f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv"):
            try:
                sess = _nse_session()
                r = sess.get(url, timeout=25)
                if r.status_code != 200 or "SYMBOL" not in r.text[:400].upper():
                    continue
                lines = r.text.strip().split("\n")
                hdr = [h.strip().upper() for h in lines[0].split(",")]
                def col(*names):
                    for n in names:
                        if n in hdr:
                            return hdr.index(n)
                    return None
                i_sym, i_ser, i_dp = col("SYMBOL"), col("SERIES"), col("DELIV_PER")
                if i_sym is None or i_dp is None:
                    continue
                row = {}
                for line in lines[1:]:
                    pcs = [x.strip() for x in line.split(",")]
                    if len(pcs) <= max(i_sym, i_dp):
                        continue
                    if i_ser is not None and pcs[i_ser].upper() not in ("EQ", "BE"):
                        continue
                    try:
                        row[pcs[i_sym].upper()] = float(pcs[i_dp])
                    except ValueError:
                        continue
                if len(row) > 200:
                    got = row
                    break
            except Exception:
                continue
            finally:
                time.sleep(0.4)
        if got:
            days[iso] = got
            print(f"[delivery] baseline day {iso}: {len(got)} stocks")

    # keep only the recent window
    for k in sorted(days)[:-sessions - 3]:
        days.pop(k, None)
    try:
        with open(DELIV_HIST, "w") as f:
            json.dump({"days": days}, f)
    except Exception:
        pass

    series = {}
    for iso in sorted(days):
        for sym, v in days[iso].items():
            series.setdefault(sym, []).append(v)

    out = {}
    for sym, vals in series.items():
        if len(vals) < 5:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        sd = var ** 0.5
        # consecutive most-recent sessions above the mean — published practice
        # weights a 3-5 day run far above any single spike
        streak = 0
        for v in reversed(vals):
            if v > mean:
                streak += 1
            else:
                break
        out[sym] = {"mean": round(mean, 2), "sd": round(sd, 2),
                    "n": len(vals), "streak": streak}
    return out, len(days)


@app.route("/api/probe-delivery")
def api_probe_delivery():
    """Check whether NSE's bhavcopy (delivery data) is reachable."""
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        data, day = fetch_delivery_data()
        if not data:
            return jsonify({"ok": False,
                            "note": "Could not fetch the bhavcopy. NSE may be blocking this machine, "
                                    "or no file exists yet for a recent session. Delivery context will "
                                    "be skipped and positioning read from futures and basis only."}), 502
        sample = {k: data[k] for k in list(data)[:8]}
        return jsonify({"ok": True, "date": day, "stocks": len(data), "sample": sample,
                        "note": "Delivery data available. Cash-market context is active in the F&O scan."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/oi")
def oi_page():
    if not require_auth():
        return redirect("/login")
    return render_template("oi.html")


@app.route("/api/oi-refresh/<symbol>")
def api_oi_refresh_one(symbol):
    """Re-run the scan for ONE underlying and splice it into the cache.

    Runs SYNCHRONOUSLY — a single stock takes a few seconds, so there is no
    need for the background-thread and polling machinery the full scan uses,
    and the caller gets the refreshed card straight back.

    It shares the global _oi_lock with the full scan: the two must never run
    together, because both write fno_oi_scan.json and the snapshot file.
    """
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    sym = (symbol or "").upper().strip()
    if not sym:
        return jsonify({"error": "no symbol given"}), 400

    with _oi_lock:
        if _oi_build["running"]:
            return jsonify({"error": "a full scan is running — wait for it to finish",
                            "busy": True}), 409
        _oi_build.update({"running": True, "done": 0, "total": 1,
                          "stage": f"refreshing {sym}", "started": time.time(),
                          "error": None})
    try:
        _do_oi_scan(only=sym)
    finally:
        with _oi_lock:
            err = _oi_build.get("error")
            _oi_build["running"] = False
            _oi_build["stage"] = None

    if err:
        return jsonify({"error": err, "symbol": sym}), 500

    try:
        with open(OI_CACHE) as f:
            cached = json.load(f)
    except Exception as e:
        return jsonify({"error": f"cache unreadable after refresh: {e}"}), 500

    row = next((r for r in cached.get("stocks", []) if r.get("name") == sym), None)
    if not row:
        return jsonify({"error": f"{sym} produced no result — it may have no "
                                 f"current-month futures contract, or the data "
                                 f"call failed. Check the server log.",
                        "symbol": sym}), 404
    return jsonify({
        "symbol": sym,
        "stock": row,
        "refreshed_at": datetime.datetime.now().strftime("%H:%M:%S"),
        # carried so the card can render trend/structure notes identically
        "have_option_deltas": cached.get("have_option_deltas"),
        "structure_available": cached.get("structure_available"),
        "delta_basis": cached.get("delta_basis"),
        "snapshot_quality": cached.get("snapshot_quality"),
    })


@app.route("/api/oi")
def api_oi():
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    force = request.args.get("force") == "1"
    cached = {}
    try:
        with open(OI_CACHE) as f:
            cached = json.load(f)
    except Exception:
        pass

    with _oi_lock:
        running = _oi_build["running"]
        snap = dict(_oi_build)

    if running:
        pct = round(snap["done"] / snap["total"] * 100) if snap["total"] else 0
        elapsed = int(time.time() - (snap["started"] or time.time()))
        eta = int((elapsed / snap["done"]) * (snap["total"] - snap["done"])) if snap["done"] > 10 else None
        return jsonify({"building": True, "pct": pct, "done": snap["done"],
                        "total": snap["total"], "stage": snap["stage"],
                        "elapsed_sec": elapsed, "eta_sec": eta})

    stale_schema = cached.get("schema") != OI_SCHEMA
    if force or not cached or stale_schema:
        with _oi_lock:
            if not _oi_build["running"]:
                _oi_build.update({"running": True, "done": 0, "total": 0,
                                  "stage": "starting", "started": time.time(), "error": None})
                threading.Thread(target=_do_oi_scan, daemon=True).start()
        return jsonify({"building": True, "pct": 0, "done": 0, "total": 0,
                        "stage": ("cached results are from an older build — rebuilding"
                                  if stale_schema and cached else "starting"),
                        "elapsed_sec": 0, "eta_sec": None})

    if snap.get("error"):
        return jsonify({"error": snap["error"]}), 500
    return jsonify(cached)



FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#E8B84B"/>
      <stop offset="100%" stop-color="#9B7BFF"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="14" fill="#0A0C0E"/>
  <rect x="10" y="34" width="7" height="18" rx="2" fill="url(#g)" opacity=".55"/>
  <rect x="21" y="24" width="7" height="28" rx="2" fill="url(#g)" opacity=".75"/>
  <rect x="32" y="14" width="7" height="38" rx="2" fill="url(#g)"/>
  <rect x="43" y="28" width="7" height="24" rx="2" fill="url(#g)" opacity=".65"/>
  <circle cx="46.5" cy="19" r="4.5" fill="#33D17A"/>
</svg>"""


@app.route("/favicon.ico")
@app.route("/favicon.svg")
def favicon():
    """Small candlestick mark so browser tabs are not a blank page icon."""
    from flask import Response
    return Response(FAVICON_SVG, mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.route("/api/version")
def api_version():
    """
    Report what is actually running, so a missing feature can be traced to the
    file that did not get replaced instead of being guessed at.
    """
    import os as _os
    checks = {}

    # backend markers: a feature name -> a string that only exists in the new code
    src_markers = {
        "rollover_detection": "likely_rollover",
        "next_month_oi": "next_month",
        "per_strike_flow": "prem_chg_pct",
        "nse_premium_change": "pct_chg",
        "baseline_coverage": "has_baseline",
        "snapshot_quality": "_snapshot_quality",
        "delivery_data": "fetch_delivery_data",
        "cash_context": "true_positioning",
    }
    try:
        with open(__file__, encoding="utf-8") as f:
            src = f.read()
        checks["app_py"] = {k: (v in src) for k, v in src_markers.items()}
    except Exception as e:
        checks["app_py"] = {"error": str(e)}

    # template markers
    tpl_markers = {
        "templates/oi.html": {
            "rollover_line": "rolloverLine",
            "expiry_shown": "class=\"expy\"",
            "per_strike_table": "strikeTable",
            "verdict_block": "verdictLine",
            "coverage_note": "opt_coverage",
        },
        "templates/flow.html": {"event_diagnostics": "diagnostics"},
        "templates/index.html": {"nav_bar": "topnav", "synthetic_cells": "loadDerived"},
    }
    for path, markers in tpl_markers.items():
        try:
            with open(path, encoding="utf-8") as f:
                t = f.read()
            checks[path] = {k: (v in t) for k, v in markers.items()}
        except Exception as e:
            checks[path] = {"error": str(e)}

    modules = {}
    for m in ("fno_analysis", "cash_context", "synthetic_index",
              "weekly_ema_scan", "energy_analysis"):
        modules[m + ".py"] = _os.path.exists(m + ".py")

    caches = {}
    for c in ("fno_oi_scan.json", "fno_oi_snapshots.json", "events_cache.json",
              "delivery_data.json", "flow_history.json", "wema_scan.json"):
        caches[c] = _os.path.exists(c)

    stale = [k for k, v in checks.items()
             if isinstance(v, dict) and not v.get("error") and not all(v.values())]

    return jsonify({
        "oi_schema": OI_SCHEMA,
        "expected_oi_schema": 19,
        "feature_checks": checks,
        "modules_present": modules,
        "cache_files": caches,
        "files_needing_replacement": stale,
        "verdict": ("Everything is current." if not stale and OI_SCHEMA == 19 else
                    "Some files are out of date — see files_needing_replacement. "
                    "Replace those, then restart the server and hard-refresh the browser."),
    })


@app.route("/api/probe-universe")
def api_probe_universe():
    """
    Walk the same steps the F&O scan uses to build its universe, reporting the
    count at each stage. The first stage that reads zero is the failure point.
    """
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        out = {}
        inst = _nfo_instruments()
        out["nfo_instruments_total"] = len(inst)

        segs = {}
        for i in inst:
            segs[i.get("segment")] = segs.get(i.get("segment"), 0) + 1
        out["segments"] = segs

        futs = [i for i in inst if i.get("segment") == "NFO-FUT" and i.get("expiry")]
        out["futures_with_expiry"] = len(futs)
        if futs:
            sample = futs[0]
            out["sample_future"] = {
                "tradingsymbol": sample.get("tradingsymbol"),
                "name": sample.get("name"),
                "expiry": str(sample.get("expiry")),
                "expiry_type": type(sample.get("expiry")).__name__,
                "segment": sample.get("segment"),
            }

        today = datetime.date.today()
        by_name = {}
        bad_expiry = 0
        past_expiry = 0
        for i in futs:
            e = i["expiry"]
            e = e if isinstance(e, datetime.date) else None
            if not e:
                bad_expiry += 1
                continue
            if e < today:
                past_expiry += 1
                continue
            cur = by_name.get(i["name"])
            if cur is None or e < cur["expiry"]:
                by_name[i["name"]] = {"expiry": e, "inst": i}
        out["expiry_not_a_date"] = bad_expiry
        out["expiry_in_past"] = past_expiry
        out["underlyings_with_live_future"] = len(by_name)
        out["sample_underlyings"] = sorted(by_name)[:12]
        out["banknifty_present"] = "BANKNIFTY" in by_name

        emap = equity_token_map()
        out["equity_token_map_size"] = len(emap)
        out["sample_equity_symbols"] = sorted(emap)[:8]

        matched = [n for n in by_name if n in emap]
        out["underlyings_matching_equity_map"] = len(matched)
        out["unmatched_sample"] = sorted(set(by_name) - set(emap))[:15]

        INDEX_NAMES = {"BANKNIFTY": "NIFTY BANK"}
        names = [n for n in by_name
                 if n not in ("NIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")
                 and (n in emap or n in INDEX_NAMES)]
        out["final_universe"] = len(names)
        out["final_sample"] = sorted(names)[:12]

        stage = None
        for k in ("nfo_instruments_total", "futures_with_expiry",
                  "underlyings_with_live_future", "equity_token_map_size",
                  "underlyings_matching_equity_map", "final_universe"):
            if not out.get(k):
                stage = k
                break
        out["failure_stage"] = stage
        out["verdict"] = ("Universe builds correctly — the problem is downstream."
                          if stage is None else f"Pipeline empties at: {stage}")
        return jsonify(out)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/probe-fno")
def api_probe_fno():
    """Confirm F&O detection is working and that this build of app.py is live."""
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        syms = fno_symbols()
        checks = ["POONAWALLA", "NATCOPHARM", "HINDCOPPER", "DELHIVERY",
                  "RELIANCE", "AFFLE", "WELCORP", "REDINGTON"]
        return jsonify({
            "build": "fno-enabled",
            "total_fno_underlyings": len(syms),
            "sample": sorted(syms)[:25],
            "checks": {c: (c in syms) for c in checks},
            "note": ("If total is 0, kite.instruments('NFO') failed — check the "
                     "Command Prompt for an error." if not syms else
                     "Detection is working. If badges still do not show, the running "
                     "server is an older app.py — restart it."),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/probe-nse-cas")
def api_probe_nse_cas():
    """
    Try each candidate CAS endpoint and report what comes back.

    Run this DURING the auction window (3:15-3:35) — outside it the endpoints
    may exist but return nothing, which looks the same as a wrong URL.
    """
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401

    extra = request.args.get("try")
    candidates = ([extra] if extra else []) + CAS_ENDPOINT_CANDIDATES

    try:
        s = _nse_session()
    except Exception as e:
        return jsonify({"error": f"Could not reach nseindia.com: {e}"}), 502

    results = []
    for path in candidates:
        url = "https://www.nseindia.com" + path
        row = {"path": path}
        try:
            r = s.get(url, headers={"Referer": "https://www.nseindia.com/market-data/closing-auction-session"},
                      timeout=15)
            row["status"] = r.status_code
            ctype = r.headers.get("content-type", "")
            row["content_type"] = ctype
            if r.status_code == 200 and "json" in ctype.lower():
                try:
                    j = r.json()
                    row["ok"] = True
                    row["top_level_keys"] = list(j.keys())[:12] if isinstance(j, dict) else f"list[{len(j)}]"
                    # show one sample record so the field names are visible
                    sample = None
                    if isinstance(j, dict):
                        for k, v in j.items():
                            if isinstance(v, list) and v:
                                sample = v[0]
                                row["array_key"] = k
                                row["rows"] = len(v)
                                break
                    elif isinstance(j, list) and j:
                        sample = j[0]
                        row["rows"] = len(j)
                    if isinstance(sample, dict):
                        row["sample_fields"] = list(sample.keys())[:25]
                        row["sample"] = {k: sample[k] for k in list(sample.keys())[:10]}
                except Exception as e:
                    row["ok"] = False
                    row["note"] = f"200 but not parseable JSON: {e}"
            else:
                row["ok"] = False
                row["note"] = "not JSON" if r.status_code == 200 else "non-200"
        except Exception as e:
            row["ok"] = False
            row["error"] = str(e)
        results.append(row)
        time.sleep(0.5)

    working = [r["path"] for r in results if r.get("ok")]
    if working:
        try:
            with open(NSE_CAS_ENDPOINT_FILE, "w") as f:
                json.dump({"endpoint": working[0], "found_at": datetime.datetime.now().isoformat()}, f)
        except Exception:
            pass

    now = datetime.datetime.now().time()
    in_window = datetime.time(15, 15) <= now <= datetime.time(15, 40)
    return jsonify({
        "results": results,
        "working": working,
        "saved_endpoint": working[0] if working else None,
        "in_cas_window": in_window,
        "advice": ("Good — this was run inside the CAS window, so an empty result means the URL is wrong."
                   if in_window else
                   "Run this again between 3:15 and 3:35pm. Outside the auction these endpoints can return "
                   "nothing even when the URL is correct, which is indistinguishable from a bad URL."),
    })


def fetch_cas_indicative():
    """Indicative CAS data using whichever endpoint the probe discovered."""
    endpoint = None
    try:
        with open(NSE_CAS_ENDPOINT_FILE) as f:
            endpoint = json.load(f).get("endpoint")
    except Exception:
        pass
    if not endpoint:
        return {"available": False,
                "reason": "No working NSE endpoint saved yet. Visit /api/probe-nse-cas during the 3:15-3:35 window to discover it."}

    try:
        s = _nse_session()
        r = s.get("https://www.nseindia.com" + endpoint,
                  headers={"Referer": "https://www.nseindia.com/market-data/closing-auction-session"},
                  timeout=15)
        if r.status_code != 200:
            return {"available": False, "reason": f"NSE returned HTTP {r.status_code}"}
        data = r.json()
    except Exception as e:
        return {"available": False, "reason": f"fetch failed: {e}"}

    rows = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list) and v:
                rows = v
                break
    elif isinstance(data, list):
        rows = data

    # field names are unknown until the probe reveals them, so match loosely
    def pick(d, *names):
        for n in names:
            for key in d:
                if key.lower().replace("_", "") == n.lower().replace("_", ""):
                    return d[key]
        return None

    out = []
    for d in rows if isinstance(rows, list) else []:
        if not isinstance(d, dict):
            continue
        sym = pick(d, "symbol", "sym", "tradingsymbol")
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "indicative": pick(d, "iep", "indicativePrice", "equilibriumPrice", "finalPrice", "lastPrice"),
            "qty": pick(d, "iepQty", "totalTradedVolume", "quantity", "finalQuantity"),
            "reference": pick(d, "referencePrice", "prevClose", "previousClose"),
            "buy_qty": pick(d, "totalBuyQuantity", "buyQuantity"),
            "sell_qty": pick(d, "totalSellQuantity", "sellQuantity"),
        })

    return {"available": bool(out), "endpoint": endpoint, "count": len(out),
            "stocks": out[:250],
            "reason": None if out else "Endpoint responded but held no usable rows (likely outside the auction window)."}


@app.route("/api/cas-indicative")
def api_cas_indicative():
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        import synthetic_index as si
        data = fetch_cas_indicative()
        data["phase"] = si.cas_phase()
        data["as_of"] = datetime.datetime.now().strftime("%d %b %Y, %I:%M:%S %p")
        return jsonify(data)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/synthetic")
def synthetic_page():
    if not require_auth():
        return redirect("/login")
    return render_template("synthetic.html")


@app.route("/api/synthetic")
def api_synthetic():
    if not require_auth():
        return jsonify({"error": "not_logged_in"}), 401
    try:
        import synthetic_index as si

        out = {"phase": si.cas_phase(),
               "as_of": datetime.datetime.now().strftime("%d %b %Y, %I:%M:%S %p"),
               "indices": []}

        targets = [("NIFTY", "NIFTY 50"), ("BANKNIFTY", "NIFTY BANK")]
        for fo_name, index_match in targets:
            entry = {"name": fo_name, "display": index_match}
            try:
                tok = get_index_token(index_match)
                if not tok:
                    entry["error"] = f"{index_match} not found on Kite"
                    out["indices"].append(entry)
                    continue
                spot_q = kite.quote([tok]).get(str(tok)) or {}
                spot = spot_q.get("last_price")
                if not spot:
                    entry["error"] = "no spot quote"
                    out["indices"].append(entry)
                    continue
                entry["spot"] = round(float(spot), 2)
                ohlc = spot_q.get("ohlc") or {}
                entry["prev_close"] = ohlc.get("close")
                if entry["prev_close"]:
                    entry["spot_chg_pct"] = round((spot - entry["prev_close"]) / entry["prev_close"] * 100, 2)

                chain = _chain_for(fo_name, float(spot))
                if not chain:
                    entry["error"] = "no option chain found"
                    out["indices"].append(entry)
                    continue
                entry["expiry"] = chain["expiry"].isoformat()
                entry["days_to_expiry"] = (chain["expiry"] - datetime.date.today()).days

                symbols = [f"NFO:{i['tradingsymbol']}" for i in list(chain["ce"].values()) + list(chain["pe"].values())]
                if chain["future"]:
                    symbols.append(f"NFO:{chain['future']['tradingsymbol']}")
                quotes = {}
                for i in range(0, len(symbols), 200):
                    try:
                        quotes.update(kite.quote(symbols[i:i + 200]))
                    except Exception:
                        pass
                    time.sleep(0.25)

                calls = {k: quotes.get(f"NFO:{v['tradingsymbol']}") for k, v in chain["ce"].items()}
                puts = {k: quotes.get(f"NFO:{v['tradingsymbol']}") for k, v in chain["pe"].items()}
                calls = {k: v for k, v in calls.items() if v}
                puts = {k: v for k, v in puts.items() if v}

                syn = si.build_synthetic(float(spot), calls, puts)
                entry["synthetic"] = syn

                if chain["future"]:
                    fq = quotes.get(f"NFO:{chain['future']['tradingsymbol']}")
                    if fq and fq.get("last_price"):
                        fp = float(fq["last_price"])
                        entry["future"] = {
                            "symbol": chain["future"]["tradingsymbol"],
                            "price": round(fp, 2),
                            "basis": round(fp - float(spot), 2),
                            "basis_pct": round((fp - float(spot)) / float(spot) * 100, 3),
                            "oi": fq.get("oi"),
                            "volume": fq.get("volume") or fq.get("volume_traded"),
                        }
                        if syn and syn.get("consensus"):
                            entry["future"]["vs_synthetic"] = round(fp - syn["consensus"], 2)
            except Exception as e:
                entry["error"] = str(e)
            out["indices"].append(entry)

        return jsonify(out)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500





# ---------------------------------------------------------------------------
#  Attribution — injected server-side so it lives in one place, not eight.
#  This makes casual removal inconvenient, not impossible: any local app whose
#  source sits on disk can be edited. Real protection is legal — see LICENSE.
# ---------------------------------------------------------------------------

_FOOT_CSS = """<style>
.a2b-foot{max-width:1240px;margin:64px auto 0;padding:0 2px;}
.a2b-rule{height:1px;background:linear-gradient(90deg,transparent,#242A30 20%,#333C44 50%,#242A30 80%,transparent);}
.a2b-body{display:flex;align-items:center;justify-content:space-between;gap:18px;
  padding:18px 2px 10px;flex-wrap:wrap;}
.a2b-brand{display:flex;align-items:center;gap:11px;}
.a2b-glyph{width:26px;height:26px;border-radius:7px;display:flex;align-items:center;
  justify-content:center;font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:10px;
  letter-spacing:.02em;color:#0A0C0E;background:linear-gradient(135deg,#E8B84B,#9B7BFF);
  box-shadow:0 2px 10px rgba(155,123,255,.16);}
.a2b-word{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:13px;
  letter-spacing:.16em;text-transform:uppercase;color:#AEB6BD;}
.a2b-meta{font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:#4E575F;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.a2b-meta i{width:2px;height:2px;border-radius:50%;background:#333C44;font-style:normal;}
@media(max-width:620px){.a2b-body{justify-content:center;text-align:center;}}
</style>"""

_FOOT_HTML = """<footer class="a2b-foot">
<div class="a2b-rule"></div>
<div class="a2b-body">
<div class="a2b-brand"><span class="a2b-glyph">A2B</span><span class="a2b-word">Amans2bmm</span></div>
<div class="a2b-meta"><span>&copy; 2026</span><i></i><span>All rights reserved</span><i></i><span>Built on Kite Connect</span></div>
</div>
</footer>"""


@app.after_request
def _attach_attribution(response):
    """Append the attribution block to every HTML page served."""
    try:
        if "text/html" not in (response.headers.get("Content-Type") or "") \
           or response.direct_passthrough:
            return response
        body = response.get_data(as_text=True)
        if "a2b-foot" in body or "</body>" not in body:
            return response
        response.set_data(body.replace("</body>", _FOOT_CSS + _FOOT_HTML + "</body>"))
        response.headers["X-Author"] = "Amans2bmm"
        response.headers["X-Copyright"] = "(c) 2026 Amans2bmm - All rights reserved"
    except Exception:
        pass
    return response


if __name__ == "__main__":
    # threaded: a long build never blocks other pages
    # use_reloader off: saving a file mid-build won't kill the background job
    app.run(port=REDIRECT_PORT, debug=True, threaded=True, use_reloader=False)
