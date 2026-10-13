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
