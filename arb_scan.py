"""Hourly Deribit BTC arbitrage scanner (public data, no API key, Python stdlib only).

Based on the tests in Alexander, Chen, Deng & Wang, "Arbitrage Opportunities and Efficiency Tests
in Crypto Derivatives" (SSRN 4495548). Deribit options are inverse (priced in BTC), so the
put-call parity used here is the fiat-free form:  C - P = 1 - K/F   (BTC per 1 BTC contract).

Each run saves:
  data/quotes/YYYY-MM-DD/HH.csv.gz  bid/ask of every option + future (for later research)
  data/arb/hits.csv                 every violation that is still positive AFTER bid/ask and taker fees
  data/arb/runs.csv                 one row per run: number of hits per type and the best edge

Checks (taker on every leg, so a hit is an edge you could have taken at the top of the book):
  parity      call/put/future with the same expiry (dated future only, no funding risk)
  box         two strikes, 4 options + future, locks a fixed BTC amount
  vertical    call (put) price must fall (rise) with strike
  butterfly   price must be convex in strike (3 adjacent strikes)
  cal_strict  same strike call: longer expiry must cost at least the shorter one (USD, F2 >= F1)
  cal_var     total implied variance (IV^2 x T) at the same strike falls with maturity
              -> not risk-free, a relative-value signal; reported separately
Top-of-book sizes are fetched for the legs of the best hits (book summary has no sizes).
"""
import csv, gzip, json, math, os, time, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

API = "https://www.deribit.com/api/v2/public/"
MON = {m: i for i, m in enumerate(["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"], 1)}
OPT_TAKER, OPT_CAP = 0.0003, 0.125      # per BTC contract, capped at 12.5% of option price
OPT_SETTLE = 0.00015                    # delivery fee (capped 12.5%)
FUT_TAKER = 0.0005                      # dated futures, share of notional
MIN_EDGE_USD = 1.0                      # per 1 BTC of contracts, after fees
MIN_DTE = 0.02                          # skip quotes < ~30 minutes to expiry
SIZE_LOOKUPS = 25

def get(method, **kw):
    url = API + method + ("?" + "&".join(f"{k}={v}" for k, v in kw.items()) if kw else "")
    for i in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read())["result"]
        except Exception as e:
            print("retry", i + 1, e); time.sleep(3 * (i + 1))
    raise SystemExit("cannot reach Deribit")

def parse_exp(code):
    return datetime(2000 + int(code[-2:]), MON[code[-5:-2]], int(code[:-5]), 8, tzinfo=timezone.utc)

def ofee(p): return min(OPT_TAKER, OPT_CAP * p) + min(OPT_SETTLE, OPT_CAP * p)

# ---------- Black-76 on USD prices (for cal_var) ----------
def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def b76(F, K, t, v, typ):
    s = v * math.sqrt(t); d1 = (math.log(F / K) + s * s / 2) / s; d2 = d1 - s
    return F * ncdf(d1) - K * ncdf(d2) if typ == "C" else K * ncdf(-d2) - F * ncdf(-d1)
def iv(price_usd, F, K, t, typ):
    intr = max(F - K, 0) if typ == "C" else max(K - F, 0)
    if price_usd <= intr + 1e-9 or t <= 0: return None
    lo, hi = 1e-3, 5.0
    if b76(F, K, t, hi, typ) < price_usd: return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if b76(F, K, t, mid, typ) < price_usd: lo = mid
        else: hi = mid
    return (lo + hi) / 2

def load(now):
    opts, futs = [], {}
    for r in get("get_book_summary_by_currency", currency="BTC", kind="future"):
        n = r["instrument_name"]
        if n.endswith("PERPETUAL"): futs["PERP"] = r; continue
        futs[parse_exp(n.split("-")[1])] = r
    for r in get("get_book_summary_by_currency", currency="BTC", kind="option"):
        try:
            _, e, k, typ = r["instrument_name"].split("-"); exp = parse_exp(e)
        except Exception:
            continue
        t = (exp - now).total_seconds() / 86400
        if t < MIN_DTE: continue
        opts.append(dict(name=r["instrument_name"], exp=exp, dte=t, K=float(k), typ=typ,
                         bid=r.get("bid_price") or 0.0, ask=r.get("ask_price") or 0.0,
                         F=r.get("underlying_price"), idx=r.get("estimated_delivery_price"), oi=r.get("open_interest")))
    return opts, futs

def scan(opts, futs, now):
    hits = []
    spot = next((o["idx"] for o in opts if o.get("idx")), None) or 0
    def add(kind, exp, edge_btc, legs, note=""):
        usd = edge_btc * spot
        if usd >= MIN_EDGE_USD:
            hits.append(dict(kind=kind, expiry=exp, edge_btc=round(edge_btc, 6), edge_usd=round(usd, 2),
                             legs=" | ".join(legs), note=note))
    by = defaultdict(dict)                      # by[exp][(K,typ)] = opt
    for o in opts: by[o["exp"]][(o["K"], o["typ"])] = o
    for exp, book in by.items():
        ex = exp.strftime("%Y-%m-%d"); fut = futs.get(exp)
        strikes = sorted({k for k, _ in book})
        # ---- parity & box: need a dated future of the same expiry ----
        if fut and fut.get("bid_price") and fut.get("ask_price"):
            Fb, Fa = fut["bid_price"], fut["ask_price"]
            pair = {K: (book.get((K, "C")), book.get((K, "P"))) for K in strikes}
            for K, (c, p) in pair.items():
                if not c or not p: continue
                ff = FUT_TAKER * K / ((Fa + Fb) / 2)
                if c["bid"] and p["ask"]:    # sell call, buy put, buy future
                    e = c["bid"] - p["ask"] - (1 - K / Fa) - ofee(c["bid"]) - ofee(p["ask"]) - ff
                    add("parity", ex, e, [f"SELL {c['name']}@{c['bid']}", f"BUY {p['name']}@{p['ask']}", f"BUY fut {fut['instrument_name']}@{Fa} notional {K:.0f} USD"])
                if c["ask"] and p["bid"]:    # buy call, sell put, sell future
                    e = (1 - K / Fb) - (c["ask"] - p["bid"]) - ofee(c["ask"]) - ofee(p["bid"]) - ff
                    add("parity", ex, e, [f"BUY {c['name']}@{c['ask']}", f"SELL {p['name']}@{p['bid']}", f"SELL fut {fut['instrument_name']}@{Fb} notional {K:.0f} USD"])
            ok = [K for K in strikes if pair[K][0] and pair[K][1]]
            for i, K1 in enumerate(ok):
                c1, p1 = pair[K1]
                for K2 in ok[i + 1:]:
                    c2, p2 = pair[K2]; d = K2 - K1; ff = FUT_TAKER * d / ((Fa + Fb) / 2)
                    if c1["ask"] and c2["bid"] and p2["ask"] and p1["bid"]:     # buy box + long future(d)
                        cost = c1["ask"] - c2["bid"] + p2["ask"] - p1["bid"]
                        e = d / Fa - cost - sum(ofee(x) for x in (c1["ask"], c2["bid"], p2["ask"], p1["bid"])) - ff
                        add("box", ex, e, [f"BUY {c1['name']}", f"SELL {c2['name']}", f"BUY {p2['name']}", f"SELL {p1['name']}", f"BUY fut notional {d:.0f} USD"], f"cost {cost:.5f} BTC")
                    if c1["bid"] and c2["ask"] and p2["bid"] and p1["ask"]:     # sell box + short future(d)
                        proc = c1["bid"] - c2["ask"] + p2["bid"] - p1["ask"]
                        e = proc - d / Fb - sum(ofee(x) for x in (c1["bid"], c2["ask"], p2["bid"], p1["ask"])) - ff
                        add("box", ex, e, [f"SELL {c1['name']}", f"BUY {c2['name']}", f"SELL {p2['name']}", f"BUY {p1['name']}", f"SELL fut notional {d:.0f} USD"], f"proceeds {proc:.5f} BTC")
        # ---- vertical & butterfly (options only, payoff >= 0 in USD so also in BTC) ----
        for typ in ("C", "P"):
            ks = [K for K in strikes if (K, typ) in book]
            for a, b in zip(ks, ks[1:]):
                lo, hi = book[(a, typ)], book[(b, typ)]
                cheap, rich = (lo, hi) if typ == "C" else (hi, lo)   # call: lower K worth more; put: higher K worth more
                if cheap["ask"] and rich["bid"]:
                    e = rich["bid"] - cheap["ask"] - ofee(rich["bid"]) - ofee(cheap["ask"])
                    add("vertical", ex, e, [f"BUY {cheap['name']}@{cheap['ask']}", f"SELL {rich['name']}@{rich['bid']}"])
            for a, b, c in zip(ks, ks[1:], ks[2:]):
                o1, o2, o3 = book[(a, typ)], book[(b, typ)], book[(c, typ)]
                if not (o1["ask"] and o3["ask"] and o2["bid"]): continue
                w1, w3 = (c - b) / (c - a), (b - a) / (c - a)
                e = o2["bid"] - w1 * o1["ask"] - w3 * o3["ask"] - ofee(o2["bid"]) - w1 * ofee(o1["ask"]) - w3 * ofee(o3["ask"])
                add("butterfly", ex, e, [f"BUY {w1:.2f} {o1['name']}", f"SELL 1 {o2['name']}", f"BUY {w3:.2f} {o3['name']}"])
    # ---- calendar ----
    exps = sorted(by)
    for e1, e2 in zip(exps, exps[1:]):
        b1, b2 = by[e1], by[e2]
        for (K, typ), o1 in b1.items():
            o2 = b2.get((K, typ))
            if not o2 or not o1["bid"] or not o2["ask"]: continue
            F1, F2 = o1["F"], o2["F"]
            if not F1 or not F2: continue
            u1, u2 = o1["bid"] * F1, o2["ask"] * F2      # USD
            f = (ofee(o1["bid"]) + ofee(o2["ask"]))
            if typ == "C" and F2 >= F1:
                e = (u1 - u2) / spot - f
                add("cal_strict", e1.strftime("%Y-%m-%d") + ">" + e2.strftime("%Y-%m-%d"), e,
                    [f"SELL {o1['name']}@{o1['bid']}", f"BUY {o2['name']}@{o2['ask']}"])
            otm = (typ == "P" and K < F1) or (typ == "C" and K >= F1)
            if otm and o1["dte"] > 0.5:
                v1 = iv(u1, F1, K, o1["dte"] / 365, typ); v2 = iv(u2, F2, K, o2["dte"] / 365, typ)
                if v1 and v2:
                    w1, w2 = v1 * v1 * o1["dte"] / 365, v2 * v2 * o2["dte"] / 365
                    if w2 < w1 * 0.97:     # far total variance at ask at least 3% below near at bid
                        hits.append(dict(kind="cal_var", expiry=e1.strftime("%Y-%m-%d") + ">" + e2.strftime("%Y-%m-%d"),
                                         edge_btc="", edge_usd="", legs=f"SELL {o1['name']}@{o1['bid']} (IV {v1*100:.1f}) | BUY {o2['name']}@{o2['ask']} (IV {v2*100:.1f})",
                                         note=f"total var near {w1:.5f} > far {w2:.5f}"))
    return hits, spot

def sizes(hits):
    hits = sorted([h for h in hits if h["edge_usd"] != ""], key=lambda h: -h["edge_usd"])
    done = {}
    for h in hits:
        names = [w for part in h["legs"].split(" | ") for w in part.split() if w.startswith("BTC-") and not w.endswith("PERPETUAL")]
        names = [n.split("@")[0] for n in names]
        mins = []
        for n in names:
            if n not in done and len(done) < SIZE_LOOKUPS:
                try:
                    ob = get("get_order_book", instrument_name=n, depth=1)
                    done[n] = (ob.get("best_bid_amount") or 0, ob.get("best_ask_amount") or 0)
                except SystemExit:
                    done[n] = None
            if done.get(n): mins.append(min(x for x in done[n] if x is not None))
        h["min_top_size_btc"] = min(mins) if mins else ""

def main():
    now = datetime.now(timezone.utc)
    opts, futs = load(now)
    d = f"data/quotes/{now:%Y-%m-%d}"; os.makedirs(d, exist_ok=True)
    with gzip.open(f"{d}/{now:%H}.csv.gz", "wt", newline="") as f:
        w = csv.writer(f); w.writerow(["time_utc", "instrument", "bid", "ask", "underlying"])
        for o in opts: w.writerow([now.strftime("%Y-%m-%d %H:%M"), o["name"], o["bid"], o["ask"], o["F"]])
        for k, r in futs.items(): w.writerow([now.strftime("%Y-%m-%d %H:%M"), r["instrument_name"], r.get("bid_price"), r.get("ask_price"), r.get("mark_price")])
    hits, spot = scan(opts, futs, now)
    sizes(hits)
    os.makedirs("data/arb", exist_ok=True)
    cols = ["time_utc", "spot", "kind", "expiry", "edge_usd", "edge_btc", "min_top_size_btc", "legs", "note"]
    new = not os.path.exists("data/arb/hits.csv")
    with open("data/arb/hits.csv", "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new: w.writeheader()
        for h in hits: w.writerow(dict(time_utc=now.strftime("%Y-%m-%d %H:%M"), spot=round(spot), **h))
    kinds = ["parity", "box", "vertical", "butterfly", "cal_strict", "cal_var"]
    cnt = {k: sum(1 for h in hits if h["kind"] == k) for k in kinds}
    best = max([h["edge_usd"] for h in hits if h["edge_usd"] != ""] or [0])
    new = not os.path.exists("data/arb/runs.csv")
    with open("data/arb/runs.csv", "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["time_utc", "spot", "n_options", "n_futures"] + kinds + ["best_edge_usd"])
        w.writerow([now.strftime("%Y-%m-%d %H:%M"), round(spot), len(opts), len(futs)] + [cnt[k] for k in kinds] + [best])
    print(f"{now:%Y-%m-%d %H:%M} UTC spot {spot:,.0f}: {len(opts)} options, {len(futs)} futures, hits {cnt}, best ${best}")

if __name__ == "__main__":
    main()
