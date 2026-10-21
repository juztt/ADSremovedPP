<#
.SYNOPSIS
    ผูกโปรเจกต์ ADSremovedPP เข้ากับ GitHub repository ที่เพิ่งสร้าง

.DESCRIPTION
    สคริปต์นี้จะ:
      1. ตั้งค่า git identity (ถามถ้ายังไม่ได้ตั้ง)
      2. แก้ config.py ให้ชี้ไปที่ repo ของคุณ
      3. commit ครั้งแรก
      4. เชื่อม remote แล้ว push ขึ้น GitHub

.EXAMPLE
    .\setup_repo.ps1
    .\setup_repo.ps1 -SkipPush
#>

[CmdletBinding()]
param(
    [string]$Owner,
    [string]$Repo = "ADSremovedPP",
    [string]$Remote,
    [switch]$SkipPush,
    [switch]$SkipCommit
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$git = Join-Path ${env:ProgramFiles} "Git\cmd\git.exe"
if (-not (Test-Path $git)) {
    $git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
}
if (-not $git) {
    throw "ไม่พบ git.exe — ติดตั้ง Git for Windows จาก https://git-scm.com/download/win"
}

function Write-Step { param([string]$m) Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "  [OK] $m" -ForegroundColor Green }

# ---------------------------------------------------------------- identity --
Write-Step "1/5 ตรวจสอบ git identity"

$userName = (& $git config user.name 2>$null)
$userEmail = (& $git config user.email 2>$null)

if (-not $userName) {
    $userName = Read-Host "  ชื่อที่จะแสดงบน commit (เช่น Your Name)"
    & $git config user.name $userName
}
if (-not $userEmail) {
    Write-Host "  หมายเหตุ: ใช้อีเมลเดียวกับบัญชี GitHub เพื่อให้ commit ผูกกับโปรไฟล์"
    $userEmail = Read-Host "  อีเมล GitHub"
    & $git config user.email $userEmail
}
Write-Ok "user.name  = $((& $git config user.name))"
Write-Ok "user.email = $((& $git config user.email))"

# ------------------------------------------------------------------ owner --
Write-Step "2/5 ตั้งค่า GitHub owner"

if (-not $Owner) {
    $Owner = Read-Host "  GitHub username ของคุณ (ไม่ต้องใส่ https://github.com/)"
}
if (-not $Owner) { throw "ต้องระบุ GitHub username" }

# ------------------------------------------------------------- remote url --
if (-not $Remote) {
    $Remote = "https://github.com/$Owner/$Repo.git"
}
Write-Ok "remote = $Remote"

# ------------------------------------------------------------------ config --
Write-Step "3/5 แก้ config.py ให้ชี้ไปที่ repo"

$configPath = Join-Path $root "config.py"
$content = Get-Content $configPath -Raw -Encoding UTF8
$content = $content -replace 'GITHUB_OWNER = "[^"]*"', "GITHUB_OWNER = `"$Owner`""
$content = $content -replace 'GITHUB_REPO = "[^"]*"',  "GITHUB_REPO = `"$Repo`""
[System.IO.File]::WriteAllText($configPath, $content, (New-Object System.Text.UTF8Encoding($false)))
Write-Ok "GITHUB_OWNER = `"$Owner`""
Write-Ok "GITHUB_REPO  = `"$Repo`""
Write-Ok "BLACKLIST_URL = https://raw.githubusercontent.com/$Owner/$Repo/main/local_db.json"

# ----------------------------------------------------------------- commit --
if (-not $SkipCommit) {
    Write-Step "4/5 commit ครั้งแรก"
    & $git add -A
    & $git commit -m "ADSremovedPP v1.0.0 — Android ADB Cleaner & Repair Utility" 2>&1 | ForEach-Object { Write-Host "  $_" }
    Write-Ok "commit สำเร็จ: $((& $git log --oneline -1))"
} else {
    Write-Step "4/5 ข้ามการ commit (ตามที่ระบุ -SkipCommit)"
}

# ----------------------------------------------------------------- remote --
Write-Step "5/5 เชื่อม remote + push"

$existing = (& $git remote 2>$null)
if ($existing -contains "origin") {
    Write-Host "  remote 'origin' มีอยู่แล้ว — กำลังอัปเดต URL"
    & $git remote set-url origin $Remote
} else {
    & $git remote add origin $Remote
}
Write-Ok "remote 'origin' = $((& $git remote get-url origin))"

if ($SkipPush) {
    Write-Host "`nข้ามการ push (ตามที่ระบุ -SkipPush)" -ForegroundColor Yellow
    Write-Host "รันเองด้วยคำสั่ง:  git push -u origin main`n"
    exit 0
}

Write-Host "  ระบบอาจขอให้ยืนยันตัวตน (เปิดหน้าต่างเบราว์เซอร์)"
Write-Host "  ถ้าใช้ Personal Access Token ให้ใส่เป็นรหัสผ่านแทน`n"

& $git push -u origin main 2>&1 | ForEach-Object { Write-Host "  $_" }

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n==================================================" -ForegroundColor Green
    Write-Host "  push สำเร็จ!" -ForegroundColor Green
    Write-Host "==================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  ขั้นตอนต่อไปบน GitHub:"
    Write-Host "  1. เปิด https://github.com/$Owner/$Repo"
    Write-Host "  2. ไปที่ Settings > Pages > Source: main / (root) > Save"
    Write-Host "     (ไม่บังคับ — ทำให้ raw URL เสถียรขึ้น)"
    Write-Host "  3. ทดสอบว่า DB ดึงได้:"
    Write-Host "     https://raw.githubusercontent.com/$Owner/$Repo/main/local_db.json"
    Write-Host ""
} else {
    Write-Host "`n  push ไม่สำเร็จ — ลองตรวจสอบ:" -ForegroundColor Yellow
    Write-Host "  · สร้าง repo บน GitHub ให้เรียบร้อยก่อน (ชื่อ $Repo)"
    Write-Host "  · repository ต้องไม่มี README/LICENSE เริ่มต้น (เลือกว่างไว้)"
    Write-Host "  · ตรวจว่า URL ถูก: $($Remote -replace 'https://','https://github.com/')"
}