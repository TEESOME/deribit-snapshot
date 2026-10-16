# Deribit BTC option snapshot (รายวัน, รันบน GitHub ไม่ต้องเปิดคอม)

## ตั้งค่าครั้งเดียว (~10 นาที)
1. เข้า github.com → มุมขวาบน **+** → **New repository** → ตั้งชื่อ เช่น `deribit-snapshot` → เลือก **Private** → **Create repository**
2. ในหน้า repo ที่สร้าง กด **uploading an existing file** → ลากไฟล์ `snapshot.py` และ `README.md` ใส่ → **Commit changes**
3. สร้างไฟล์ workflow: กด **Add file → Create new file** → ช่องชื่อพิมพ์ `.github/workflows/snapshot.yml` → คัดลอกเนื้อหาจากไฟล์ `snapshot.yml` มาวาง → **Commit changes**
4. ไปแท็บ **Actions** → ถ้ามีปุ่มให้เปิดใช้ workflow ให้กดเปิด → เลือก **deribit-daily-snapshot** → **Run workflow** เพื่อทดสอบครั้งแรก
5. รอ ~1 นาที ถ้าขึ้น ✅ จะเห็นโฟลเดอร์ `data/` ใน repo

หลังจากนั้นระบบจะรันเองทุกวัน 14:17 เวลาไทย

## ข้อมูลที่ได้
- `data/summary.csv` — 1 แถวต่อ expiry ต่อวัน: spot, ATM IV, max pain, GEX รวม, gamma flip, put/call wall, OI, PCR
- `data/chain/YYYY-MM-DD.csv.gz` — ทุกสัญญาของวันนั้น (OI, mark IV, gamma, GEX)

## หมายเหตุ
- GitHub อาจเลื่อนเวลารันตามคิวได้ราว 5–30 นาที และหยุด schedule อัตโนมัติถ้า repo ไม่มีความเคลื่อนไหว 60 วัน (แต่ commit ข้อมูลทุกวันนับเป็นความเคลื่อนไหวแล้ว)
- ถ้าแท็บ Actions แสดง ❌ ให้กดเข้าไปดู log แล้วส่งข้อความ error มาให้เปรียว

---

# ตัวสแกน arbitrage รายชั่วโมง (เพิ่ม 16 ต.ค. 2026)

## ติดตั้งเพิ่ม (~5 นาที)
1. ในหน้า repo กด **Add file → Upload files** → ลากไฟล์ `arb_scan.py` และ `README.md` (ฉบับใหม่) ใส่ → **Commit changes**
2. **Add file → Create new file** → ชื่อ `.github/workflows/arb.yml` → วางเนื้อหาจากไฟล์ `arb.yml` → **Commit changes**
3. เปิดไฟล์ `.github/workflows/snapshot.yml` เดิม → กดรูปดินสอ (Edit) → ลบทั้งหมดแล้ววางเนื้อหาจาก `snapshot.yml` ฉบับใหม่ → **Commit changes** (เพิ่มกันชนเวลา push พร้อมกัน)
4. แท็บ **Actions** → **deribit-arb-scan** → **Run workflow** ทดสอบ ถ้า ✅ จะมีโฟลเดอร์ `data/arb/` และ `data/quotes/`

## ข้อมูลที่ได้
- `data/arb/runs.csv` — 1 แถวต่อชั่วโมง: จำนวนที่เจอแต่ละแบบ และ edge สูงสุด (USD ต่อขนาด 1 BTC)
- `data/arb/hits.csv` — ทุกครั้งที่เจอ: ประเภท, expiry, edge หลังหัก bid/ask และค่าธรรมเนียม taker, ขนาดที่ top of book, ขาที่ต้องเทรด
- `data/quotes/YYYY-MM-DD/HH.csv.gz` — bid/ask ทุกสัญญา เก็บไว้วิเคราะห์ย้อนหลัง

## หมายเหตุ
- ใช้นาที GitHub Actions ราว 1 นาทีต่อรอบ (~750 นาที/เดือน รวมของเดิม; repo ส่วนตัวฟรี 2,000 นาที/เดือน)
- `cal_var` เป็นสัญญาณ relative value (total variance กลับหัว) ไม่ใช่ arbitrage ไร้ความเสี่ยง
- ราคาเป็นของ Deribit; ถ้าจะเทรดจริงต้องมีบัญชี Deribit หรือเทียบราคา Bybit เอง
