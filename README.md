# ADSremovedPP

**Android ADB Cleaner & Repair Utility Tool** — โปรแกรมสำหรับช่างซ่อมมือถือ
สแกนแอป เทียบกับรายการต้องระวัง ถอนการติดตั้ง และเรียกคำสั่งซ่อมแซมพื้นฐานผ่าน ADB

พัฒนาด้วย **Python + PySide6** · อินเทอร์เฟซโทนเข้ม (QSS) · รองรับภาษาไทยเต็มรูปแบบ

---

## ⚠️ อ่านก่อนใช้

1. **รายการใน `local_db.json` เป็นตัวอย่าง** ยังไม่ผ่านการตรวจสอบจริง
   ต้องยืนยันกับฐานข้อมูลไวรัสสาธารณะก่อนใช้งานจริง
   การแจ้งผิดพลาดอาจทำให้ลบแอปที่ลูกค้าใช้งานจริง
2. **สำรองข้อมูลลูกค้าก่อนถอนการติดตั้งทุกครั้ง** — ข้อมูลภายในแอปจะหายถาวร
3. **โปรแกรมตรวจจับได้ไม่ 100%** เครื่องมือนี้ช่วยลดงาน ไม่ได้แทนการคิดของช่าง

---

## โครงสร้างโปรเจกต์

```
ADSremovedPP/
├── main.py                     จุดเริ่มต้นรันโปรแกรม
├── config.py                   ตัวแปรระบบ, version, URL, path resolution
├── requirements.txt
├── local_db.json               รายการต้องระวัง (fallback เมื่อไม่มีเน็ต)
├── version.json                ไฟล์ตรวจเวอร์ชันใหม่
├── core/
│   ├── __init__.py
│   ├── adb_manager.py          คำสั่ง ADB ทั้งหมด + QThread Workers
│   └── updater.py              ซิงก์ฐานข้อมูล + Auto-Update ตัวโปรแกรม
├── ui/
│   ├── __init__.py
│   ├── main_window.py          หน้าต่างหลัก + 3 Tabs
│   └── styles.py               QSS Dark Mode Theme
└── bin/
    └── (วาง adb.exe และ DLL ที่จำเป็น)
```

---

## การติดตั้ง

### 1. ติดตั้ง Python 3.10+

ดาวน์โหลดจาก <https://www.python.org/downloads/> แล้วติ๊ก **Add Python to PATH**

### 2. ติดตั้งไลบรารี

```powershell
cd "C:\...\ADSremovedPP"
pip install -r requirements.txt
```

### 3. วาง adb.exe

ดาวน์โหลด **Android SDK Platform-Tools** จาก
<https://developer.android.com/tools/releases/platform-tools>

แตกไฟล์แล้วคัดลอกโฟลเดอร์ `platform-tools` ทั้งหมด (ไม่ใช่แค่ `adb.exe`)
ไปวางใน `ADSremovedPP/bin/` โครงสร้างต้องเป็น:

```
bin/
├── adb.exe
├── AdbWinApi.dll
├── AdbWinUsbApi.dll
└── (ไฟล์อื่นๆ ที่มาพร้อมกัน)
```

> **หมายเหตุ:** โปรแกรมค้นหา adb ตามลำดับ:
> ตัวแปรแวดล้อม `ADSREMOVEDPP_ADB` → `bin/` → `PATH` → Android SDK

### 4. รันโปรแกรม

```powershell
python main.py
```

---

## การใช้งาน

### ขั้นตอนก่อนสแกนทุกครั้ง

1. เสียบสาย USB ที่มือถือ
2. ที่มือถือ: **ตั้งค่า → ตัวเลือกสำหรับนักพัฒนา → การแก้ไขข้อบกพร่อง USB** เปิดไว้
3. ที่มือถือ: เลือกโหมด **ถ่ายโอนไฟล์ (MTP)**
4. กดปุ่ม **ตระกังสีเขียว** ที่มุมขวาบนของโปรแกรม
5. รอหน้าต่างแจ้งเตือน RSA → กด **อนุญาต**

### แท็บที่ 1 — สแกนด่วน & ล้างแอดแวร์

| ปุ่ม | ทำอะไร |
|------|--------|
| เริ่มสแกน | เทียบแพ็กเกจที่ติดตั้งกับ `local_db.json` |
| รีเฟรชอุปกรณ์ | ตรวจหามือถือใหม่ |
| ค้นหา | กรองผลลัพธ์ตามชื่อหรือแพ็กเกจ |
| เลือกทั้งหมด | ติ๊กทุกแถวที่มองเห็น |
| ข้ามรายการที่เลือก | ทำเครื่องหมายว่าไม่ต้องแจ้งซ้ำ |
| ล้างรายการที่เลือก | ถอนการติดตั้งแอปที่ติ๊กไว้ |

### แท็บที่ 2 — จัดการแอป

แสดงแอปทั้งหมดที่ติดตั้งในเครื่อง พร้อมช่องค้นหา

| ปุ่ม | คำสั่ง ADB ที่ใช้ | เหมาะกับ |
|------|----------------|----------|
| ถอนการติดตั้ง | `adb uninstall <pkg>` | แอปผู้ใช้ — ลบถาวร |
| **บังคับถอนการติดตั้ง** | `adb shell pm uninstall --user 0 <pkg>` | แอประบบ — ซ่อนฝั่งผู้ใช้ ไม่ลบไฟล์ระบบ |
| ล้างข้อมูลแอป | `adb shell pm clear <pkg>` | แก้เบราว์เซอร์ค้าง / ล้างแคช |

> **ทำไมต้องมีโหมดบังคับ:** แอประบบที่ผู้ผลิตติดมากับเครื่อง ถอนแบบปกติจะได้
> `DELETE_FAILED_INTERNAL_ERROR` — วิธีเดียวที่ใช้ได้คือ `--user 0`
> ซึ่งซ่อนแอปเฉพาะผู้ใช้รายนั้น (รีเซ็ตเครื่องแล้วกลับมา)

### แท็บที่ 3 — เครื่องมือช่าง

**ทางลัดการตั้งค่า** — เปิดหน้าจอโดยตรงบนมือถือ:

| ปุ่ม | คำสั่ง |
|------|--------|
| ตัวเลือกสำหรับนักพัฒนา | `am start -a android.settings.APPLICATION_DEVELOPMENT_SETTINGS` |
| การเข้าถึง (Accessibility) | `am start -a android.settings.ACCESSIBILITY_SETTINGS` |
| ติดตั้งแอปที่ไม่รู้จัก | `am start -a android.settings.MANAGE_UNKNOWN_APP_SOURCES` |
| การใช้งานข้อมูล | `am start -a android.settings.USAGE_ACCESS_SETTINGS` |
| แอปทั้งหมด | `am start -a android.settings.MANAGE_APPLICATIONS_SETTINGS` |
| ประหยัดแบตเตอรี่ | `am start -a android.settings.BATTERY_SAVER_SETTINGS` |

**ล้างข้อมูลแอป** — `pm clear` สำหรับ Chrome / WebView / Samsung Internet / Play Services

**ควบคุมเครื่อง** — `adb reboot` · `adb reboot recovery` · `adb reboot bootloader`

---

## แหล่งข้อมูลรายการต้องระวัง

โปรแกรมดึง `local_db.json` จาก URL ที่ตั้งใน `config.py` (`BLACKLIST_URL`)
ถ้าไม่มีเน็ตจะใช้ไฟล์ในเครื่องแทนอัตโนมัติ

### ขั้นตอนตั้งค่า GitHub (ครั้งเดียว)

1. สร้าง repository บน GitHub (เช่น `ADSremovedPP`)
2. อัปโหลดไฟล์ `local_db.json` และ `version.json` เข้าไป
3. แก้ `config.py`:

```python
GITHUB_OWNER = "ชื่อผู้ใช้ของคุณ"
GITHUB_REPO  = "ADSremovedPP"
GITHUB_BRANCH = "main"
```

URL ที่ได้จะเป็น:
`https://raw.githubusercontent.com/<owner>/ADSremovedPP/main/local_db.json`

### เพิ่มรายการใหม่

แก้ไฟล์ JSON แล้ว commit — ทุกเครื่องที่ติดตั้งโปรแกรมจะได้ข้อมูลใหม่เมื่อเปิดโปรแกรม

---

## Auto-Update ตัวโปรแกรม

1. อัปโหลดไฟล์ `.exe` รุ่นใหม่ไว้ใน **Releases** ของ repository
2. แก้ `version.json`:

```json
{
  "version": "1.1.0",
  "notes": "เพิ่มฟีเจอร์สแกนลึก",
  "download_url": "https://github.com/<owner>/ADSremovedPP/releases/download/v1.1.0/ADSremovedPP.exe",
  "published_at": "2026-11-01"
}
```

เมื่อผู้ใช้กด **เครื่องมือ → ตรวจหาการอัปเดต** โปรแกรมจะ:
ดาวน์โหลดไฟล์ใหม่ → สร้าง `updater.bat` ชั่วคราว → ปิดตัวเอง → ลบไฟล์เก่า →
เปลี่ยนชื่อไฟล์ใหม่ → เปิดโปรแกรมใหม่

> **ทำไมต้องใช้ไฟล์ .bat** — ไฟล์ .exe ที่ทำด้วย PyInstaller แบบ onefile
> ถูกล็อกอยู่ระหว่างทำงาน จึง**ลบตัวเองไม่ได้** ต้องให้โปรแกรมภายนอกเป็นคนลบแทน

---

## การแพ็กเป็นไฟล์ .exe เดียว

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed ^
  --name ADSremovedPP ^
  --add-data "bin;bin" ^
  --add-data "local_db.json;." ^
  --add-data "version.json;." ^
  main.py
```

ผลลัพธ์: `dist/ADSremovedPP.exe`

### ข้อแนะนำเพิ่มเติม

```powershell
# ถ้ามีไฟล์ไอคอน .ico
pyinstaller --noconfirm --clean --onefile --windowed ^
  --name ADSremovedPP ^
  --icon "assets/app.ico" ^
  --add-data "bin;bin" ^
  --add-data "local_db.json;." ^
  --add-data "version.json;." ^
  main.py
```

> **หมายเหตุ:** PyInstaller จะรวม `bin/` ทั้งโฟลเดอร์ (รวม DLL) เข้าไปด้วย
> ถ้าไฟล์ exe ใหญ่ผิดปกติ แปลว่า `platform-tools` ถูกรวมเข้าไปแล้ว ซึ่งถูกต้อง

### ปัญหาที่พบบ่อย

| อาการ | แก้ |
|---|---|
| `No module named PySide6` | ติดตั้ง PySide6 ในสภาพแวดล้อมเดียวกับที่ใช้ PyInstaller |
| เปิดแล้วขึ้น `Failed to execute script` | ลอง `pyinstaller --onedir` แทนเพื่อดูข้อผิดพลาด |
| หา adb ไม่เจอ | ตรวจว่า `bin/adb.exe` ถูกรวมมาจริง |

---

## การพัฒนาแบบโมดูล

### `core/adb_manager.py` แยกเป็น 2 ชั้น

| คลาส | หน้าที่ | ขึ้นกับ Qt? |
|-------|--------|-----------|
| `AdbManager` | subprocess, วิเคราะห์ผลลัพธ์, จัดการ error | ไม่ |
| `AdbWorker` | ส่งงานไป thread พื้นหลัง + emit signal | ใช่ |

การแยกแบบนี้ทำให้ทดสอบ `AdbManager` ได้โดยไม่ต้องเปิดหน้าต่าง

### การเพิ่มงานใหม่

1. เพิ่มเมธอด `_task_<ชื่อ>(self, payload)` ใน `AdbWorker`
2. เพิ่ม signal สำหรับผลลัพธ์
3. เรียกจาก UI ด้วย `self.worker.request("<ชื่อ>", {...})`

ตัวอย่าง:

```python
# ใน AdbWorker
def _task_my_new_task(self, payload: dict) -> None:
    serial = self._require_device(payload.get("serial", ""))
    output = self.manager.run_shell(serial, "getprop ro.serialno")
    self.log_line.emit("OK", output)
```

---

## ปัญหาที่พบบ่อย

| อาการ | วิธีแก้ |
|---|---|
| ไม่พบมือถือ | เสียบสายใหม่, เลือกโหมด MTP, กดอนุญาต RSA บนมือถือ |
| มือถือยังไม่อนุญาต | ตั้งค่า > ตัวเลือกสำหรับนักพัฒนา > ยกเลิกการอนุญาต USB debugging แล้วเสียบใหม่ |
| ตรวจพบแต่แอประบบ | แอประบบส่วนใหญ่เป็นของแท้ ให้ใช้โหมดบังคับถอนเฉพาะเมื่อจำเป็น |
| `pm clear` ล้มเหลว | แอปกำลังทำงาน — ปิดแอปก่อน หรือรีสตาร์ทเครื่องแล้วลองใหม่ |
| ดาวน์โหลดฐานข้อมูลไม่ได้ | โปรแกรมจะใช้ไฟล์ในเครื่องแทนโดยอัตโนมัติ |
| Windows Defender ฟ้องไฟล์ .exe | โปรแกรมที่ถูกแพ็กมักถูกแจ้งเตือน — กด More info > Run anyway |

---

## License

MIT — ใช้งานและแก้ไขได้อย่างอิสระ