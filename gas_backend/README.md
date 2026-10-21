# ADSremovedPP — Google Apps Script Backend

ชุดนี้ใช้แทนที่ฐานข้อมูล GitHub เดิม — ใช้สำหรับ sync, อนุมัติ และเก็บสถิติการลบแอป

---

## โครงสร้างไฟล์

| ไฟล์ | บทบาท |
|---|---|
| `Code.gs` | Backend GAS — จัดการ 3 sheet + API |
| `Index.html` | Admin Control Center (Dashboard) |
| `README.md` | คู่มือติดตั้ง |

---

## วิธีติดตั้ง

### 1. สร้าง Google Sheet

1. ไปที่ https://sheets.google.com
2. สร้าง Spreadsheet ใหม่ ตั้งชื่ออะไรก็ได้ เช่น `ADSremovedPP DB`
3. จด **Spreadsheet ID** ไว้ (ตัวเลขใน URL ระหว่าง `/d/` กับ `/edit`)

### 2. สร้าง Google Apps Script

1. ไปที่ https://script.google.com
2. กด **New Project** → ตั้งชื่อ `ADSremovedPP-Backend`
3. ลบไฟล์ `Code.gs` เดิม แล้ววางเนื้อหาจาก `Code.gs` ในโฟลเดอร์นี้
4. กด **+ Add a file** เลือก **HTML** → ตั้งชื่อ `Index` → วางเนื้อหาจาก `Index.html`
5. เปลี่ยน `ADMIN_PASSWORD` ใน `Code.gs` ให้เป็นรหัสที่ต้องการ

### 3. ผูก Script กับ Sheet

1. ในหน้า Apps Script editor → เปิด `Code.gs`
2. รันฟังก์ชัน `ensureSheets_` ครั้งหนึ่ง เพื่อสร้าง 3 sheet:
   - `Approved_DB`
   - `Pending_Reports`
   - `Deletion_Logs`
3. ระบบจะถาม permission → อนุญาต

### 4. Deploy เป็น WebApp

1. กด **Deploy** (ขวาบน) → **New deployment**
2. เลือก type: **Web app**
3. ตั้งค่า:
   - **Execute as**: `Me (your-email@gmail.com)`
   - **Who has access**: `Anyone` (แนะนำ) หรือ `Anyone with Google account`
4. กด **Deploy** → copy **Web app URL** (จะอยู่ในรูป `https://script.google.com/macros/s/AKfy.../exec`)

### 5. ทดสอบ

เปิด URL ใน browser:
```
https://script.google.com/macros/s/AKfy.../exec
```
ควรเห็นหน้า Login ของ Admin Dashboard

ทดสอบ API:
```
https://script.google.com/macros/s/AKfy.../exec?action=ping
https://script.google.com/macros/s/AKfy.../exec?action=get_approved
```

---

## การตั้งค่าในแอป Python

เปิดแอป ADSremovedPP ไปที่หน้า **ฐานข้อมูล** → **ตั้งค่า Cloud Sync**

กรอก:
- **GAS WebApp URL**: URL ที่ได้จากขั้นตอน Deploy
- **ชื่อสาขา/Reporter**: เช่น `สาขาเซ็นทรัลเวิลด์` (จะถูกส่งไปกับทุกรายงาน)

URL จะถูกเก็บไว้ที่:
```
%APPDATA%\ADSremovedPP\gas_url.txt
```

---

## Data Flow

```
┌──────────────────────┐                      ┌──────────────────────┐
│  ADSremovedPP app    │                      │  Google Apps Script   │
│  (Windows · Python)  │ ── POST {packages} ─▶│  doPost(payload)      │
│                      │                      │                        │
│  After uninstall     │                      │  ├─ ถ้าเจอใน Approved_DB│
│  ✓ background thread │                      │  │  → บันทึก Deletion_Logs │
│                      │                      │  └─ ถ้าไม่เจอ → Pending   │
│                      │                      │                        │
│  GET ?action=get_    │ ◀── JSON entries ───│  Approved_DB            │
│  approved            │                      │  (auto-filtered)        │
└──────────────────────┘                      └──────────────────────┘
                                                        │
                                                        ▼
                                              ┌──────────────────────┐
                                              │  Admin WebApp         │
                                              │  (Dashboard)          │
                                              │                        │
                                              │  • Pending queue       │
                                              │  • Approved search     │
                                              │  • Deletion stats      │
                                              │  • Add new manually    │
                                              └──────────────────────┘
```

---

## โครงสร้าง Sheet

### Approved_DB
| package | appName | category | riskLevel | source | note | addedAt | addedBy |

### Pending_Reports
| package | appName | category | riskLevel | source | deviceModel | osVersion | reporter | submittedAt | status |

### Deletion_Logs
| timestamp | package | appName | category | riskLevel | source | deviceModel | osVersion | reporter | outcome |

---

## Security Notes

- **รหัสผ่าน admin** อยู่ใน `Code.gs` — เปลี่ยนเป็นค่าที่ปลอดภัยก่อน Deploy
- ถ้าต้องการความปลอดภัยมากขึ้น ย้าย `ADMIN_PASSWORD` ไปเก็บใน **Script Properties**
  ```javascript
  var ADMIN_PASSWORD = PropertiesService.getScriptProperties().getProperty('ADMIN_PASSWORD');
  ```
  แล้วตั้งใน Project Settings → Script Properties
- GET endpoint (`get_approved`) เปิดเป็น **Anyone** เพื่อให้แอป Python เรียกได้
- POST endpoint ก็ควรเปิด Anyone (แอปจะส่ง payload อัตโนมัติ)
- ถ้ากังวลเรื่อง spam ใส่ **API Token** ใน payload แล้วตรวจใน `doPost`
