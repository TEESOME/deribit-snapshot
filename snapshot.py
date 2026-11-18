"""Snapshot รายวันของ BTC option chain บน Deribit (ข้อมูลสาธารณะ ไม่ต้องใช้ API key)
บันทึก:
  data/chain/YYYY-MM-DD.csv.gz   — ทุกสัญญา: expiry, strike, type, OI, mark IV, mark price, volume, gamma, GEX
  data/summary.csv               — 1 แถวต่อ expiry ต่อวัน: spot, ATM IV, max pain, GEX รวม, gamma flip,
                                   put wall / call wall (strike ที่ GEX ลบ/บวก มากที่สุด), OI รวม, PCR
ใช้แค่ Python มาตรฐาน (ไม่ต้อง pip install)
GEX = OI(BTC) × gamma × spot² × 1%  (USD ต่อการขยับ 1%); call = +, put = − (สมมติ dealer short put / long call แบบเดียวกับ dashboard ทั่วไป)
"""
import csv, gzip, json, math, os, time, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
MON = {m: i for i, m in enumerate(["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"], 1)}

def get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read())["result"]
        except Exception as e:
            print("retry", i + 1, e); time.sleep(5 * (i + 1))
    raise SystemExit("cannot reach Deribit")

def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def npdf(x): return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)
def gamma(S, K, iv, t):
    if iv <= 0 or t <= 0: return 0.0
    s = iv * math.sqrt(t); d1 = (math.log(S / K) + s * s / 2) / s
    return npdf(d1) / (S * s)

now = datetime.now(timezone.utc)
rows = get(URL)
chain = []
for r in rows:
    try:
        _, e, k, typ = r["instrument_name"].split("-")
        exp = datetime(2000 + int(e[-2:]), MON[e[-5:-2]], int(e[:-5]), 8, tzinfo=timezone.utc)
    except Exception:
        continue
    S = r.get("underlying_price") or r.get("estimated_delivery_price")
    iv = (r.get("mark_iv") or 0) / 100
    t = max((exp - now).total_seconds() / 86400 / 365, 1e-6)
    K = float(k); oi = r.get("open_interest") or 0
    g = gamma(S, K, iv, t)
    gex = oi * g * S * S * 0.01 * (1 if typ == "C" else -1)
    chain.append(dict(time_utc=now.strftime("%Y-%m-%d %H:%M"), expiry=exp.strftime("%Y-%m-%d"), dte=round(t * 365, 3),
                      strike=K, type=typ, open_interest=oi, mark_iv=r.get("mark_iv"), mark_price=r.get("mark_price"),
                      underlying=S, volume=r.get("volume"), gamma=g, gex_usd=gex))

os.makedirs("data/chain", exist_ok=True)
with gzip.open(f"data/chain/{now:%Y-%m-%d}.csv.gz", "wt", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(chain[0])); w.writeheader(); w.writerows(chain)

# ---------- summary per expiry ----------
by_exp = defaultdict(list)
for c in chain: by_exp[c["expiry"]].append(c)
spot = sum(c["underlying"] for c in chain) / len(chain)
out = []
for exp, cs in sorted(by_exp.items()):
    S = cs[0]["underlying"]; t = cs[0]["dte"] / 365
    strikes = sorted({c["strike"] for c in cs})
    # max pain
    def pain(X): return sum(c["open_interest"] * (max(X - c["strike"], 0) if c["type"] == "C" else max(c["strike"] - X, 0)) for c in cs)
    mp = min(strikes, key=pain)
    # ATM IV
    atm = min(cs, key=lambda c: abs(c["strike"] - S)); atm_iv = atm["mark_iv"]
    # GEX by strike, walls
    gk = defaultdict(float)
    for c in cs: gk[c["strike"]] += c["gex_usd"]
    put_wall = min(gk, key=gk.get); call_wall = max(gk, key=gk.get)
    # gamma flip: re-price total GEX at hypothetical spot levels
    def tot(X): return sum(c["open_interest"] * gamma(X, c["strike"], (c["mark_iv"] or 0) / 100, max(t, 1e-6)) * X * X * 0.01 * (1 if c["type"] == "C" else -1) for c in cs)
    grid = [S * (0.80 + 0.005 * i) for i in range(81)]
    vals = [tot(x) for x in grid]; flip = ""
    for a, b, va, vb in zip(grid, grid[1:], vals, vals[1:]):
        if va == 0 or va * vb < 0: flip = round(a + (b - a) * (-va) / (vb - va)) if vb != va else round(a); break
    oi_c = sum(c["open_interest"] for c in cs if c["type"] == "C"); oi_p = sum(c["open_interest"] for c in cs if c["type"] == "P")
    out.append(dict(date=now.strftime("%Y-%m-%d"), time_utc=now.strftime("%H:%M"), expiry=exp, dte=cs[0]["dte"], spot=round(S, 1),
                    atm_iv=atm_iv, max_pain=mp, gex_total_usd=round(sum(gk.values())), gamma_flip=flip,
                    put_wall=put_wall, put_wall_gex=round(gk[put_wall]), call_wall=call_wall, call_wall_gex=round(gk[call_wall]),
                    oi_calls=round(oi_c, 1), oi_puts=round(oi_p, 1), pcr=round(oi_p / oi_c, 3) if oi_c else ""))

new = not os.path.exists("data/summary.csv")
with open("data/summary.csv", "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]))
    if new: w.writeheader()
    w.writerows(out)
print(f"{now:%Y-%m-%d %H:%M} UTC: {len(chain)} instruments, {len(out)} expiries, spot {spot:,.0f}")

# ---------- weekly signal: SMA200 + strike 2SD / 2.5SD / 3SD ของ weekly ถัดไป (เพิ่ม 19 พ.ย. 2026) ----------
# บันทึก:
#   data/weekly_signal.csv          — 1 แถวต่อ expiry (weekly ศุกร์ 2 ตัวถัดไป) ต่อวัน
#   data/weekly_signal_latest.json  — ค่าล่าสุดสำหรับให้ scheduled task อ่าน
# กฎ (protocol ข้อ 2): ราคา ≥ SMA200 → ขาย put ≥ 2.5SD, ราคา < SMA200 → ≥ 3SD
# 1SD = spot × ATM IV ของ expiry × √(วันที่เหลือถึง 08:00 UTC ของวันหมดอายุ / 365); put ปัดลงเป็น strike ที่มีจริง
C_USD = 11000          # เงินกองทุน C
CAP_X = 4              # เพดาน 4×C
NEAR_PCT = 3.0         # เตือน "ใกล้ตัด" ถ้าห่าง SMA200 ไม่ถึง 3%

def weekly_signal(chain, now):
    from datetime import timedelta
    # 1) ราคาปิดรายวันย้อนหลัง (BTC-PERPETUAL บน Deribit, แท่ง 00:00 UTC) → SMA200 ของแท่งที่ปิดแล้ว
    end_ms = int(now.timestamp() * 1000); start_ms = end_ms - 320 * 86400 * 1000
    tv = get("https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
             f"?instrument_name=BTC-PERPETUAL&resolution=1D&start_timestamp={start_ms}&end_timestamp={end_ms}")
    today0 = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000)
    closes = [c for t, c in zip(tv["ticks"], tv["close"]) if t < today0]      # ตัดแท่งของวันนี้ที่ยังไม่ปิด
    if len(closes) < 200:
        print("weekly_signal: daily closes", len(closes), "< 200"); return
    sma200 = sum(closes[-200:]) / 200
    sma200_20d_ago = sum(closes[-220:-20]) / 200 if len(closes) >= 220 else None
    spot = get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd")["index_price"]
    dist = (spot / sma200 - 1) * 100
    mode = "2.5SD" if spot >= sma200 else "3SD"
    k_rule = 2.5 if mode == "2.5SD" else 3.0

    # 2) สถานะเทียบรอบก่อน (ตัดลง / ตัดขึ้น / ใกล้ตัด)
    prev_mode = ""
    if os.path.exists("data/weekly_signal_latest.json"):
        try:
            with open("data/weekly_signal_latest.json") as f: prev_mode = json.load(f).get("mode", "")
        except Exception: pass
    if prev_mode and prev_mode != mode:
        alert = "CROSS_DOWN" if mode == "3SD" else "CROSS_UP"
    elif abs(dist) < NEAR_PCT:
        alert = "NEAR_SMA200"
    else:
        alert = ""

    # 3) weekly ศุกร์ 2 ตัวถัดไป (เหลือ > 1 วัน) → ATM IV, SD, strike ที่มีจริง
    by_exp = defaultdict(list)
    for c in chain: by_exp[c["expiry"]].append(c)
    fridays = sorted(e for e in by_exp
                     if datetime.strptime(e, "%Y-%m-%d").weekday() == 4
                     and datetime.strptime(e, "%Y-%m-%d").replace(hour=8, tzinfo=timezone.utc) - now > timedelta(days=1))[:2]
    rows = []
    for exp in fridays:
        cs = by_exp[exp]
        t_exp = datetime.strptime(exp, "%Y-%m-%d").replace(hour=8, tzinfo=timezone.utc)
        days = (t_exp - now).total_seconds() / 86400
        fwd = cs[0]["underlying"]
        k_atm = min({c["strike"] for c in cs}, key=lambda k: abs(k - fwd))
        ivs = [c["mark_iv"] for c in cs if c["strike"] == k_atm and c["mark_iv"]]
        atm_iv = sum(ivs) / len(ivs)
        sd = spot * atm_iv / 100 * math.sqrt(days / 365)
        puts = {c["strike"]: c for c in cs if c["type"] == "P"}
        pk = sorted(puts)
        def put_at(level):            # strike ที่มีจริงสูงสุดที่ ≤ level
            ks = [k for k in pk if k <= level]
            return ks[-1] if ks else None
        r = dict(date=now.strftime("%Y-%m-%d"), time_utc=now.strftime("%H:%M"), expiry=exp, days=round(days, 2),
                 spot=round(spot, 1), sma200=round(sma200, 1), dist_pct=round(dist, 2),
                 sma200_slope_20d=round(sma200 - sma200_20d_ago, 1) if sma200_20d_ago else "",
                 mode=mode, alert=alert, atm_iv=round(atm_iv, 2), sd1=round(sd, 1))
        for lab, k in (("2sd", 2.0), ("2_5sd", 2.5), ("3sd", 3.0)):
            K = put_at(spot - k * sd)
            r[f"put_{lab}"] = int(K) if K else ""
            mp = puts[K]["mark_price"] if K else None            # BTC ต่อ 1 สัญญา
            r[f"put_{lab}_mark_usd"] = round(mp * spot, 1) if mp is not None else ""
        K_rule = put_at(spot - k_rule * sd)
        r["rule_strike"] = int(K_rule) if K_rule else ""
        r["rule_max_btc_4xC"] = round(CAP_X * C_USD / K_rule, 3) if K_rule else ""   # ก่อนหัก position ที่ถืออยู่
        rows.append(r)
    if not rows:
        print("weekly_signal: no weekly expiry found"); return

    new = not os.path.exists("data/weekly_signal.csv")
    with open("data/weekly_signal.csv", "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        if new: w.writeheader()
        w.writerows(rows)
    with open("data/weekly_signal_latest.json", "w") as f:
        json.dump(dict(updated_utc=now.strftime("%Y-%m-%d %H:%M"), spot=round(spot, 1), sma200=round(sma200, 1),
                       dist_pct=round(dist, 2), mode=mode, prev_mode=prev_mode, alert=alert, expiries=rows),
                  f, ensure_ascii=False, indent=1)
    print(f"weekly_signal: spot {spot:,.0f} vs SMA200 {sma200:,.0f} ({dist:+.1f}%) → {mode} {alert}; "
          + "; ".join(f"{r['expiry']} rule strike {r['rule_strike']}" for r in rows))

try:
    weekly_signal(chain, now)
except BaseException as e:          # ห้ามทำให้ snapshot หลักล้ม
    print("weekly_signal failed:", repr(e))

# ---------- long vol signal: VRP / DVOL percentile + strangle ±0.5SD ~60 วัน (เพิ่ม 19 พ.ย. 2026) ----------
# บันทึก:
#   data/longvol_signal.csv     — 1 แถวต่อวัน
#   data/longvol_latest.json    — ค่าล่าสุดสำหรับ scheduled task
# สัญญาณ (protocol กฎ long vol): VRP < 0.8 หรือ DVOL อยู่ใน 20% ต่ำสุดของ 365 วัน
# VRP = DVOL ÷ RV คาดการณ์ (Yang-Zhang เฉลี่ยหน้าต่าง 5/20/60 วัน จากแท่งรายวันที่ปิดแล้ว) — นิยามเดียวกับ backtest
LV_VRP = 0.8
LV_PCT = 0.20
LV_DAYS = 60
LV_BUDGET = (0.01 * C_USD, 0.02 * C_USD)   # งบ premium 1% และ 2% ของ C

def _yz(o, h, l, c, n):
    if len(c) < n + 1: return None
    lo = [math.log(x) for x in o[-n:]]; lh = [math.log(x) for x in h[-n:]]
    ll = [math.log(x) for x in l[-n:]]; lc = [math.log(x) for x in c[-n:]]
    lcp = [math.log(x) for x in c[-n - 1:-1]]
    def var(xs):
        m = sum(xs) / len(xs); return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    ov = var([a - b for a, b in zip(lo, lcp)]); cv = var([a - b for a, b in zip(lc, lo)])
    rs = sum((hh - cc) * (hh - oo) + (lll - cc) * (lll - oo) for hh, lll, cc, oo in zip(lh, ll, lc, lo)) / n
    return math.sqrt((ov + k * cv + (1 - k) * rs) * 365)

def longvol_signal(chain, now):
    end_ms = int(now.timestamp() * 1000); start_ms = end_ms - 400 * 86400 * 1000
    today0 = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000)
    # 1) RV จากแท่งรายวัน BTC-PERPETUAL ที่ปิดแล้ว
    tv = get("https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
             f"?instrument_name=BTC-PERPETUAL&resolution=1D&start_timestamp={start_ms}&end_timestamp={end_ms}")
    bars = [(t, o, h, l, c) for t, o, h, l, c in zip(tv["ticks"], tv["open"], tv["high"], tv["low"], tv["close"]) if t < today0]
    o = [b[1] for b in bars]; h = [b[2] for b in bars]; l = [b[3] for b in bars]; c = [b[4] for b in bars]
    rvs = [_yz(o, h, l, c, n) for n in (5, 20, 60)]
    if None in rvs:
        print("longvol_signal: not enough daily bars"); return
    rv = sum(rvs) / 3
    # 2) DVOL รายวัน 365 วัน → ค่าวันนี้ (open ของแท่งวันนี้) และ percentile
    dv = get("https://www.deribit.com/api/v2/public/get_volatility_index_data"
             f"?currency=BTC&resolution=1D&start_timestamp={start_ms}&end_timestamp={end_ms}")["data"]
    dv = sorted(dv, key=lambda r: r[0])
    hist = [r[1] for r in dv if r[0] < today0][-364:]
    today = [r[1] for r in dv if r[0] >= today0]
    dvol = today[0] if today else dv[-1][4]
    pct = sum(1 for x in hist if x < dvol) / len(hist) if hist else None
    pct_lvl = sorted(hist + [dvol])[int(0.2 * len(hist))] if hist else None    # ระดับ DVOL ที่ percentile 20%
    vrp = dvol / 100 / rv
    sig_vrp = vrp < LV_VRP; sig_pct = pct is not None and pct < LV_PCT
    signal = "ON" if (sig_vrp or sig_pct) else ""
    # 3) strangle ±0.5SD ของ expiry วันศุกร์ตัวแรกที่เหลือ ≥ 60 วัน
    spot = get("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd")["index_price"]
    by_exp = defaultdict(list)
    for x in chain: by_exp[x["expiry"]].append(x)
    cands = sorted(e for e in by_exp if datetime.strptime(e, "%Y-%m-%d").weekday() == 4
                   and (datetime.strptime(e, "%Y-%m-%d").replace(hour=8, tzinfo=timezone.utc) - now).days >= LV_DAYS)
    row = dict(date=now.strftime("%Y-%m-%d"), time_utc=now.strftime("%H:%M"), spot=round(spot, 1),
               dvol=round(dvol, 2), dvol_pct_1y=round(pct, 3) if pct is not None else "", dvol_pct20_level=round(pct_lvl, 1) if pct_lvl else "",
               rv_forecast=round(rv * 100, 2), vrp=round(vrp, 3), sig_vrp=int(sig_vrp), sig_dvol_pct=int(sig_pct), signal=signal,
               expiry="", days="", atm_iv="", sd1="", call_strike="", call_mark_usd="", put_strike="", put_mark_usd="",
               strangle_usd_per_btc="", breakeven_low="", breakeven_high="", size_btc_1pctC="", size_btc_2pctC="")
    if cands:
        exp = cands[0]; cs = by_exp[exp]
        t_exp = datetime.strptime(exp, "%Y-%m-%d").replace(hour=8, tzinfo=timezone.utc)
        days = (t_exp - now).total_seconds() / 86400
        fwd = cs[0]["underlying"]
        k_atm = min({x["strike"] for x in cs}, key=lambda k: abs(k - fwd))
        ivs = [x["mark_iv"] for x in cs if x["strike"] == k_atm and x["mark_iv"]]
        iv = sum(ivs) / len(ivs); sd = spot * iv / 100 * math.sqrt(days / 365)
        calls = {x["strike"]: x for x in cs if x["type"] == "C"}; puts = {x["strike"]: x for x in cs if x["type"] == "P"}
        kc = min([k for k in calls if k >= spot + 0.5 * sd], default=None)
        kp = max([k for k in puts if k <= spot - 0.5 * sd], default=None)
        if kc and kp:
            cm = calls[kc]["mark_price"] * spot; pm = puts[kp]["mark_price"] * spot; tot = cm + pm
            row.update(expiry=exp, days=round(days, 1), atm_iv=round(iv, 2), sd1=round(sd, 1),
                       call_strike=int(kc), call_mark_usd=round(cm, 1), put_strike=int(kp), put_mark_usd=round(pm, 1),
                       strangle_usd_per_btc=round(tot, 1), breakeven_low=round(kp - tot), breakeven_high=round(kc + tot),
                       size_btc_1pctC=round(LV_BUDGET[0] / tot, 4), size_btc_2pctC=round(LV_BUDGET[1] / tot, 4))
    prev = ""
    if os.path.exists("data/longvol_latest.json"):
        try:
            with open("data/longvol_latest.json") as f: prev = json.load(f).get("signal", "")
        except Exception: pass
    row["new_signal"] = int(bool(signal) and not prev)
    new = not os.path.exists("data/longvol_signal.csv")
    with open("data/longvol_signal.csv", "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new: w.writeheader()
        w.writerow(row)
    with open("data/longvol_latest.json", "w") as f:
        json.dump(dict(updated_utc=now.strftime("%Y-%m-%d %H:%M"), **row), f, ensure_ascii=False, indent=1)
    print(f"longvol_signal: DVOL {dvol:.1f} (pct {pct:.0%}) RV {rv*100:.1f} VRP {vrp:.2f} → {signal or 'no signal'}; "
          f"{row['expiry']} C{row['call_strike']} / P{row['put_strike']} ≈ {row['strangle_usd_per_btc']} USD/BTC")

try:
    longvol_signal(chain, now)
except BaseException as e:          # ห้ามทำให้ snapshot หลักล้ม
    print("longvol_signal failed:", repr(e))
