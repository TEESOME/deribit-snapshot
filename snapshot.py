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
