/**
 * ADSremovedPP — Google Apps Script Backend
 * ----------------------------------------------------------------
 * ให้บริการ 3 sheet หลัก:
 *   • Approved_DB      — รายการแอปที่อนุมัติแล้ว แอป Python จะใช้ sheet นี้ตรวจสแกน
 *   • Pending_Reports  — รายการที่พนักงานกดลบใหม่ ๆ รอ Admin ตรวจ
 *   • Deletion_Logs    — สถิติการลบ (ทุกครั้งที่ลบจะบันทึกไว้ตรงนี้)
 *
 * ตารางทุก sheet ถูกสร้างอัตโนมัติเมื่อรันครั้งแรก (ฟังก์ชัน ensureSheets_())
 *
 * โครงสร้าง I/O
 *   GET  /exec?...action=get_approved  → JSON ของ Approved_DB
 *   GET  /exec?...action=get_pending   → JSON ของ Pending_Reports
 *   GET  /exec                         → render Index.html (Admin Dashboard)
 *   POST /exec                         → body JSON; payload จากแอป Python
 *
 * สำหรับ Admin WebApp จะเรียกฟังก์ชันผ่าน google.script.run:
 *   approveItem / rejectItem / deleteApprovedItem / addNewItemDirectly
 *   และ auth ด้วยรหัสผ่าน (ADMIN_PASSWORD) เพื่อป้องกันคนเข้าถึง URL ตรง ๆ
 */

// ============================================================================
// ค่าคงที่ — เปลี่ยน ADMIN_PASSWORD ที่นี่ หรือเก็บไว้ที่ Script Properties
// ============================================================================

var ADMIN_PASSWORD = 'ads2026';        // รหัสผ่านสำหรับเปิด Admin Dashboard
var APP_SOURCE_TAG = 'ADSremovedPP';   // tag ประจำแอป ใช้กรอง log

// หัวคอลัมน์มาตรฐานของ Approved_DB / Pending_Reports
var APPROVED_HEADERS = [
  'package',      // primary key — ชื่อ Package Name
  'appName',      // ชื่อที่แสดงให้ช่างเห็น
  'category',     // Adware / PUA / Junk / Malware / Scam
  'riskLevel',    // สูงมาก / สูง / ปานกลาง / ต่ำ
  'source',       // Sideload / Play Store / Galaxy Store / ไม่ทราบ
  'note',         // หมายเหตุเพิ่มเติม
  'addedAt',      // วันที่อนุมัติ
  'addedBy'       // ผู้อนุมัติ
];

var PENDING_HEADERS = [
  'package',
  'appName',
  'category',
  'riskLevel',
  'source',
  'deviceModel',
  'osVersion',
  'reporter',
  'submittedAt',
  'status'        // pending / approved / rejected
];

var LOG_HEADERS = [
  'timestamp',
  'package',
  'appName',
  'category',
  'riskLevel',
  'source',
  'deviceModel',
  'osVersion',
  'reporter',
  'outcome'       // approved / pending / skipped
];

// Risk level ที่อนุญาต — กันข้อมูลเพี้ยน
var ALLOWED_RISKS = ['สูงมาก', 'สูง', 'ปานกลาง', 'ต่ำ'];
var ALLOWED_CATEGORIES = ['Adware', 'PUA', 'Junk', 'Malware', 'Scam', 'อื่นๆ'];

// ============================================================================
// จุดเข้า WebApp — doGet / doPost
// ============================================================================

function doGet(e) {
  // query parameter (เผื่อ GET method)
  var params = (e && e.parameter) || {};

  // 1) ไม่มี action → render Dashboard
  if (!params.action) {
    return serveDashboard_();
  }

  // 2) JSON สำหรับแอป Python
  if (params.action === 'get_approved') {
    var rows = readApproved_();
    return jsonResponse_({
      ok: true,
      source: 'GAS',
      origin: 'remote',
      count: rows.length,
      entries: rows
    });
  }

  if (params.action === 'get_pending') {
    var pending = readPending_();
    return jsonResponse_({
      ok: true,
      count: pending.length,
      entries: pending
    });
  }

  if (params.action === 'ping') {
    return jsonResponse_({ ok: true, app: APP_SOURCE_TAG, time: new Date().toISOString() });
  }

  return jsonResponse_({ ok: false, error: 'unknown action: ' + params.action });
}

function doPost(e) {
  ensureSheets_();
  var payload;
  try {
    payload = JSON.parse((e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return jsonResponse_({ ok: false, error: 'invalid JSON', detail: String(err) });
  }

  // ---- admin write actions (from the Python app's Database page) ----------
  var action = String(payload.action || '').trim();
  if (action === 'push_approved') {
    return pushApproved_(payload);
  }
  if (action === 'delete_approved') {
    return deleteApproved_(payload);
  }

  // ---- uninstall report (from the Python app after a removal) ------------
  var pkgs = Array.isArray(payload.packages) ? payload.packages : [];
  var deviceInfo = String(payload.deviceModel || payload.device || '').trim();
  var osVersion = String(payload.osVersion || '').trim();
  var reporter = String(payload.reporter || payload.branch || '').trim();

  if (pkgs.length === 0) {
    return jsonResponse_({ ok: false, error: 'no packages supplied' });
  }

  var approved = readApprovedAsMap_();
  var session = new Date();

  var queued = 0;
  var logged = 0;
  var skipped = 0;
  var details = [];

  for (var i = 0; i < pkgs.length; i++) {
    var entry = pkgs[i] || {};
    var pkg = String(entry.package || entry.package_name || '').trim();
    if (!pkg) {
      skipped++;
      continue;
    }

    var appName = String(entry.appName || entry.app_name || pkg).trim();
    var category = normalizeCategory_(entry.category);
    var riskLevel = normalizeRisk_(entry.riskLevel);
    var source = String(entry.source || 'ไม่ทราบ').trim() || 'ไม่ทราบ';

    if (approved[pkg]) {
      // มีอยู่ใน Approved_DB แล้ว → บันทึกสถิติลง Deletion_Logs
      appendDeletionLog_({
        timestamp: Utilities.formatDate(session, 'Asia/Bangkok', 'yyyy-MM-dd HH:mm:ss'),
        package: pkg,
        appName: appName,
        category: category,
        riskLevel: riskLevel,
        source: source,
        deviceModel: deviceInfo,
        osVersion: osVersion,
        reporter: reporter,
        outcome: 'approved'
      });
      logged++;
      details.push({ package: pkg, outcome: 'logged' });
    } else {
      // ยังไม่มี → ส่งเข้า Pending_Reports ให้ Admin อนุมัติ
      var exists = readPendingHasPackage_(pkg);
      if (!exists) {
        appendPendingReport_({
          package: pkg,
          appName: appName,
          category: category,
          riskLevel: riskLevel,
          source: source,
          deviceModel: deviceInfo,
          osVersion: osVersion,
          reporter: reporter,
          submittedAt: Utilities.formatDate(session, 'Asia/Bangkok', 'yyyy-MM-dd HH:mm:ss'),
          status: 'pending'
        });
      }
      queued++;
      details.push({ package: pkg, outcome: 'queued' });
    }
  }

  return jsonResponse_({
    ok: true,
    received: pkgs.length,
    logged: logged,
    queued: queued,
    skipped: skipped,
    details: details
  });
}

// ============================================================================
// ตัวช่วยสำหรับ Admin WebApp (เรียกผ่าน google.script.run)
// ============================================================================

function approveItem(packageName) {
  ensureSheets_();
  var pkg = String(packageName || '').trim();
  if (!pkg) return { ok: false, error: 'empty package' };

  var pending = SpreadsheetApp.getActive().getSheetByName('Pending_Reports');
  var approved = SpreadsheetApp.getActive().getSheetByName('Approved_DB');
  var data = pending.getDataRange().getValues();
  var header = data[0];
  var pkIndex = header.indexOf('package');
  if (pkIndex < 0) return { ok: false, error: 'invalid pending sheet' };

  var movedRow = null;
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][pkIndex]) === pkg) {
      movedRow = data[i];
      pending.deleteRow(i + 1);
      break;
    }
  }
  if (!movedRow) return { ok: false, error: 'pending row not found' };

  // ตรวจไม่ให้ซ้ำกับ Approved
  if (findRowByPackage_(approved, pkg) > 0) {
    return { ok: true, alreadyApproved: true, package: pkg };
  }

  // เตรียมแถวใหม่สำหรับ Approved_DB
  var appName = String(movedRow[header.indexOf('appName')] || pkg);
  var category = String(movedRow[header.indexOf('category')] || 'อื่นๆ');
  var riskLevel = normalizeRisk_(String(movedRow[header.indexOf('riskLevel')] || 'ปานกลาง'));
  var source = String(movedRow[header.indexOf('source')] || 'ไม่ทราบ');
  var note = 'Auto-approved from pending queue';

  approved.appendRow([
    pkg,
    appName,
    category,
    riskLevel,
    source,
    note,
    Utilities.formatDate(new Date(), 'Asia/Bangkok', 'yyyy-MM-dd HH:mm:ss'),
    'admin (auto)'
  ]);
  return { ok: true, package: pkg, appName: appName };
}

function rejectItem(packageName) {
  ensureSheets_();
  var pkg = String(packageName || '').trim();
  if (!pkg) return { ok: false, error: 'empty package' };

  var pending = SpreadsheetApp.getActive().getSheetByName('Pending_Reports');
  var idx = findRowByPackage_(pending, pkg);
  if (idx <= 0) return { ok: false, error: 'package not in pending' };
  pending.deleteRow(idx);
  return { ok: true, package: pkg };
}

function deleteApprovedItem(packageName) {
  ensureSheets_();
  var pkg = String(packageName || '').trim();
  if (!pkg) return { ok: false, error: 'empty package' };

  var sheet = SpreadsheetApp.getActive().getSheetByName('Approved_DB');
  var idx = findRowByPackage_(sheet, pkg);
  if (idx <= 0) return { ok: false, error: 'package not approved' };
  sheet.deleteRow(idx);
  return { ok: true, package: pkg };
}

function addNewItemDirectly(itemData) {
  ensureSheets_();
  if (!itemData || !itemData.package) return { ok: false, error: 'missing package' };
  var pkg = String(itemData.package).trim();
  if (!pkg) return { ok: false, error: 'empty package' };

  var sheet = SpreadsheetApp.getActive().getSheetByName('Approved_DB');
  if (findRowByPackage_(sheet, pkg) > 0) {
    return { ok: false, error: 'package already exists' };
  }

  var appName = String(itemData.appName || pkg).trim();
  var category = normalizeCategory_(itemData.category);
  var riskLevel = normalizeRisk_(itemData.riskLevel);
  var source = String(itemData.source || 'ไม่ทราบ').trim() || 'ไม่ทราบ';
  var note = String(itemData.note || '').trim();
  var addedBy = String(itemData.addedBy || 'admin').trim() || 'admin';

  sheet.appendRow([
    pkg,
    appName,
    category,
    riskLevel,
    source,
    note,
    Utilities.formatDate(new Date(), 'Asia/Bangkok', 'yyyy-MM-dd HH:mm:ss'),
    addedBy
  ]);
  return { ok: true, package: pkg, appName: appName };
}

// ตรวจรหัสผ่าน (เรียกจาก client side ก่อนเปิด Dashboard)
function checkPassword(supplied) {
  return { ok: String(supplied || '') === ADMIN_PASSWORD };
}

// สถิติการลบ (ใช้ในแท็บ Logs ของ Dashboard)
function getDeletionStats() {
  ensureSheets_();
  var sheet = SpreadsheetApp.getActive().getSheetByName('Deletion_Logs');
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) {
    return { ok: true, total: 0, byPackage: [], byReporter: [], byDate: [] };
  }

  var header = data[0];
  var pkgIdx = header.indexOf('package');
  var appNameIdx = header.indexOf('appName');
  var reporterIdx = header.indexOf('reporter');
  var timeIdx = header.indexOf('timestamp');

  var byPackage = {};
  var byReporter = {};
  var byDate = {};

  for (var i = 1; i < data.length; i++) {
    var row = data[i];
    var pkg = String(row[pkgIdx] || '');
    var appName = String(row[appNameIdx] || '');
    var reporter = String(row[reporterIdx] || 'ไม่ระบุ');
    var date = String(row[timeIdx] || '').slice(0, 10);

    if (pkg) {
      if (!byPackage[pkg]) byPackage[pkg] = { package: pkg, appName: appName, count: 0 };
      byPackage[pkg].count++;
    }
    byReporter[reporter] = (byReporter[reporter] || 0) + 1;
    if (date) byDate[date] = (byDate[date] || 0) + 1;
  }

  var pkgList = Object.values(byPackage)
    .sort(function (a, b) { return b.count - a.count; })
    .slice(0, 20);

  var reporterList = Object.keys(byReporter).map(function (k) {
    return { reporter: k, count: byReporter[k] };
  }).sort(function (a, b) { return b.count - a.count; });

  var dateList = Object.keys(byDate).map(function (k) {
    return { date: k, count: byDate[k] };
  }).sort(function (a, b) { return a.date.localeCompare(b.date); });

  return {
    ok: true,
    total: data.length - 1,
    byPackage: pkgList,
    byReporter: reporterList,
    byDate: dateList
  };
}

// ดึง Pending list (Admin เรียกดู)
function listPending() {
  ensureSheets_();
  var sheet = SpreadsheetApp.getActive().getSheetByName('Pending_Reports');
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return { ok: true, entries: [] };
  var header = data[0];
  var out = [];
  for (var i = 1; i < data.length; i++) {
    var obj = {};
    for (var j = 0; j < header.length; j++) obj[header[j]] = String(data[i][j] || '');
    obj._row = i + 1;
    out.push(obj);
  }
  return { ok: true, entries: out };
}

// ดึง Approved list (Admin เรียกดู พร้อมช่องค้นหา)
function listApproved(query) {
  ensureSheets_();
  var sheet = SpreadsheetApp.getActive().getSheetByName('Approved_DB');
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return { ok: true, entries: [] };
  var header = data[0];
  var q = String(query || '').trim().toLowerCase();
  var out = [];
  for (var i = 1; i < data.length; i++) {
    var obj = {};
    for (var j = 0; j < header.length; j++) obj[header[j]] = String(data[i][j] || '');
    if (q) {
      var hay = (obj.package + ' ' + obj.appName).toLowerCase();
      if (hay.indexOf(q) < 0) continue;
    }
    obj._row = i + 1;
    out.push(obj);
  }
  return { ok: true, entries: out };
}

// ============================================================================
// Internal helpers
// ============================================================================

/**
 * เขียนรายการที่ช่างแก้ไขในแอป Python ลง Approved_DB ทันที
 * (ข้ามคิวรออนุมัติ เพราะคนที่แก้คือ admin อยู่แล้ว)
 */
function pushApproved_(payload) {
  var entries = Array.isArray(payload.entries) ? payload.entries : [];
  if (entries.length === 0) {
    return jsonResponse_({ ok: false, error: 'no entries supplied' });
  }

  var sheet = SpreadsheetApp.getActive().getSheetByName('Approved_DB');
  var data = sheet.getDataRange().getValues();
  var header = data[0] || [];
  var pkgIndex = header.indexOf('package');

  // index ของ package ที่มีอยู่แล้ว
  var existing = {};
  if (pkgIndex >= 0) {
    for (var i = 1; i < data.length; i++) {
      var key = String(data[i][pkgIndex] || '');
      if (key) existing[key] = i + 1;
    }
  }

  var added = 0;
  var updated = 0;
  var skipped = 0;
  var stamp = Utilities.formatDate(new Date(), 'Asia/Bangkok', 'yyyy-MM-dd HH:mm:ss');

  for (var e = 0; e < entries.length; e++) {
    var row = entries[e] || {};
    var pkg = String(row.package || '').trim();
    if (!pkg) {
      skipped++;
      continue;
    }

    var appName = String(row.appName || row.app_name || pkg).trim();
    var category = normalizeCategory_(row.category || row.type);
    var riskLevel = normalizeRisk_(row.riskLevel || row.risk);
    var source = String(row.source || 'ไม่ทราบ').trim() || 'ไม่ทราบ';
    var note = String(row.note || '').trim();

    if (existing[pkg]) {
      // อัปเดตแถวเดิม
      var rowIndex = existing[pkg];
      if (pkgIndex >= 0) sheet.getRange(rowIndex, pkgIndex + 1).setValue(pkg);
      setIfColumn_(sheet, header, rowIndex, 'appName', appName);
      setIfColumn_(sheet, header, rowIndex, 'category', category);
      setIfColumn_(sheet, header, rowIndex, 'riskLevel', riskLevel);
      setIfColumn_(sheet, header, rowIndex, 'source', source);
      setIfColumn_(sheet, header, rowIndex, 'note', note);
      setIfColumn_(sheet, header, rowIndex, 'addedAt', stamp);
      updated++;
    } else {
      sheet.appendRow([
        pkg,
        appName,
        category,
        riskLevel,
        source,
        note,
        stamp,
        'app (local edit)'
      ]);
      existing[pkg] = sheet.getLastRow();
      added++;
    }
  }

  return jsonResponse_({
    ok: true,
    added: added,
    updated: updated,
    skipped: skipped,
    total: sheet.getLastRow() - 1
  });
}

/**
 * ลบรายการออกจาก Approved_DB (เรียกจากแอป Python)
 */
function deleteApproved_(payload) {
  var pkg = String(payload.package || '').trim();
  if (!pkg) return jsonResponse_({ ok: false, error: 'empty package' });

  var sheet = SpreadsheetApp.getActive().getSheetByName('Approved_DB');
  var idx = findRowByPackage_(sheet, pkg);
  if (idx <= 0) return jsonResponse_({ ok: false, error: 'package not approved' });
  sheet.deleteRow(idx);
  return jsonResponse_({ ok: true, package: pkg });
}

function setIfColumn_(sheet, header, rowIndex, name, value) {
  var idx = header.indexOf(name);
  if (idx < 0) return;
  sheet.getRange(rowIndex, idx + 1).setValue(value);
}

function ensureSheets_() {
  var ss = SpreadsheetApp.getActive();
  if (!ss) throw new Error('ไม่พบ Spreadsheet — โปรดผูก Code.gs กับ Sheet ก่อน');

  createSheet_(ss, 'Approved_DB', APPROVED_HEADERS);
  createSheet_(ss, 'Pending_Reports', PENDING_HEADERS);
  createSheet_(ss, 'Deletion_Logs', LOG_HEADERS);

  // ตั้งค่า width ให้สวยงาม
  var approved = ss.getSheetByName('Approved_DB');
  if (approved.getLastColumn() > 0) {
    approved.setFrozenRows(1);
    approved.autoResizeColumns(1, APPROVED_HEADERS.length);
  }
}

function createSheet_(ss, name, headers) {
  var sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.appendRow(headers);
    sheet.getRange(1, 1, 1, headers.length).setFontWeight('bold').setBackground('#1f1f1f').setFontColor('#f5f5f7');
  } else if (sheet.getLastRow() === 0) {
    sheet.appendRow(headers);
  } else if (sheet.getLastColumn() < headers.length) {
    // backfill header columns
    var have = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    for (var i = have.length; i < headers.length; i++) {
      sheet.getRange(1, i + 1).setValue(headers[i]);
    }
  }
}

function readApproved_() {
  ensureSheets_();
  var sheet = SpreadsheetApp.getActive().getSheetByName('Approved_DB');
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];
  var header = data[0];
  var out = [];
  for (var i = 1; i < data.length; i++) {
    var obj = {};
    for (var j = 0; j < header.length; j++) obj[header[j]] = data[i][j];
    out.push({
      package: String(obj.package || '').trim(),
      app_name: String(obj.appName || obj.package || '').trim(),
      appName: String(obj.appName || obj.package || '').trim(),
      type: String(obj.category || '').trim(),
      category: String(obj.category || '').trim(),
      risk: String(obj.riskLevel || '').trim(),
      riskLevel: String(obj.riskLevel || '').trim(),
      source: String(obj.source || 'ไม่ทราบ').trim(),
      note: String(obj.note || '').trim(),
      addedAt: String(obj.addedAt || '').trim()
    });
  }
  return out;
}

function readApprovedAsMap_() {
  var rows = readApproved_();
  var map = {};
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    if (row.package) map[row.package] = row;
  }
  return map;
}

function readPending_() {
  ensureSheets_();
  var sheet = SpreadsheetApp.getActive().getSheetByName('Pending_Reports');
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];
  var header = data[0];
  var out = [];
  for (var i = 1; i < data.length; i++) {
    var obj = {};
    for (var j = 0; j < header.length; j++) obj[header[j]] = String(data[i][j] || '');
    out.push(obj);
  }
  return out;
}

function readPendingHasPackage_(pkg) {
  var rows = readPending_();
  for (var i = 0; i < rows.length; i++) if (rows[i].package === pkg) return true;
  return false;
}

function appendPendingReport_(obj) {
  var sheet = SpreadsheetApp.getActive().getSheetByName('Pending_Reports');
  sheet.appendRow(PENDING_HEADERS.map(function (h) { return obj[h] || ''; }));
}

function appendDeletionLog_(obj) {
  var sheet = SpreadsheetApp.getActive().getSheetByName('Deletion_Logs');
  sheet.appendRow(LOG_HEADERS.map(function (h) { return obj[h] || ''; }));
}

function findRowByPackage_(sheet, pkg) {
  var data = sheet.getDataRange().getValues();
  if (data.length === 0) return 0;
  var header = data[0];
  var idx = header.indexOf('package');
  if (idx < 0) return 0;
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][idx]) === pkg) return i + 1;
  }
  return 0;
}

function normalizeRisk_(value) {
  var v = String(value || '').trim();
  if (ALLOWED_RISKS.indexOf(v) >= 0) return v;
  // รับรูปแบบอังกฤษจาก blacklist เดิม
  var map = {
    'Critical': 'สูงมาก',
    'High': 'สูง',
    'Medium': 'ปานกลาง',
    'Low': 'ต่ำ',
    'Safe': 'ต่ำ'
  };
  return map[v] || 'ปานกลาง';
}

function normalizeCategory_(value) {
  var v = String(value || '').trim();
  if (ALLOWED_CATEGORIES.indexOf(v) >= 0) return v;
  if (!v) return 'อื่นๆ';
  return v.length > 30 ? v.slice(0, 30) : v;
}

function serveDashboard_() {
  var html = HtmlService.createTemplateFromFile('Index');
  return html.evaluate()
    .setTitle('ADSremovedPP — Admin Control Center')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL)
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function jsonResponse_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// Include helper for serving HTML partials (ถ้ามีหลายไฟล์)
function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}
