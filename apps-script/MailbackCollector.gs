/**
 * Mailback Collector
 * Saves CAMS and KFintech mailback report files from Gmail into Google Drive
 * and records every file in the "Log" tab of this sheet.
 *
 * It only reads mail and writes to Drive. It does not delete, move, label or
 * mark any email, and it does not open the password-protected files.
 *
 * CRM: once "Set up CRM connection" has been run, every saved file is also
 * uploaded to the CRM's file inbox (Supabase Storage), where the importer
 * picks it up. The "CRM" column of the Log shows whether that worked.
 */

const CONFIG = {
  ROOT_FOLDER: 'Mailback Reports',   // created in My Drive on first run
  DAILY_LOOKBACK_DAYS: 3,            // daily run re-checks the last 3 days
  BACKFILL_DAYS: 30,                 // used by "Backfill last 30 days"
  DAILY_HOUR: 7,                     // daily run time (7 = between 7 and 8 am)
  MAX_RUNTIME_MS: 5 * 60 * 1000,     // stop before Google's 6-minute limit

  // CAMS mails do not mention the ARN, so the account is taken from the
  // address the report was sent to. Edit the labels on the right if needed.
  ACCOUNTS: {
    'address-for-arn-283370@gmail.com': 'ARN-283370',   // replace with the real addresses
    'address-for-arn-172637@gmail.com': 'ARN-172637',
    'address-for-arn-13732@gmail.com': 'ARN-13732'
  }
};

const LOG_HEADERS = ['Logged at', 'Source', 'Report', 'Account', 'Email date',
                     'Subject', 'File name', 'Drive link', 'Status', 'Message ID', 'CRM'];

/* ---------- Menu ---------- */

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Mailback')
    .addItem('Collect now', 'collectNow')
    .addItem('Backfill last ' + CONFIG.BACKFILL_DAYS + ' days', 'backfill')
    .addSeparator()
    .addItem('Turn on daily run', 'turnOnDailyRun')
    .addItem('Turn off daily run', 'turnOffDailyRun')
    .addSeparator()
    .addItem('Set up CRM connection', 'setUpCrm')
    .addItem('Send saved files to CRM', 'sendSavedFilesToCrm')
    .addToUi();
}

function collectNow() { collect_(CONFIG.DAILY_LOOKBACK_DAYS); }
function backfill()   { collect_(CONFIG.BACKFILL_DAYS); }

function turnOnDailyRun() {
  turnOffDailyRun();
  ScriptApp.newTrigger('collectNow').timeBased().everyDays(1)
    .atHour(CONFIG.DAILY_HOUR).create();
}

function turnOffDailyRun() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'collectNow') ScriptApp.deleteTrigger(t);
  });
}

/* ---------- Main ---------- */

function collect_(days) {
  const started = Date.now();
  const sheet = getLogSheet_();
  const done = getDoneIds_(sheet);
  const root = getOrCreateFolder_(DriveApp.getRootFolder(), CONFIG.ROOT_FOLDER);
  const query = 'from:(camsonline.com OR kfintech.com) newer_than:' + days + 'd';
  renameOldFolder_(root);
  // Each result is written to the Log straight away, so progress shows while it runs.
  const write = function (row) { sheet.appendRow(row); SpreadsheetApp.flush(); };
  let start = 0;
  let outOfTime = false;

  while (!outOfTime) {
    const threads = GmailApp.search(query, start, 50);
    if (threads.length === 0) break;
    start += threads.length;

    for (let i = 0; i < threads.length && !outOfTime; i++) {
      const messages = threads[i].getMessages();
      for (let j = 0; j < messages.length; j++) {
        if (Date.now() - started > CONFIG.MAX_RUNTIME_MS) { outOfTime = true; break; }
        const msg = messages[j];
        if (done[msg.getId()]) continue;

        const from = msg.getFrom().toLowerCase();
        const subject = msg.getSubject();
        try {
          if (from.indexOf('camsonline.com') > -1 && /^\s*WBR\d+\./i.test(subject)) {
            handleCams_(msg, root).forEach(write);
          } else if (from.indexOf('kfintech.com') > -1 &&
                     /Subscribed .*Report for Ref/i.test(subject)) {
            write(handleKfintech_(msg, root));
          }
        } catch (e) {
          write(logRow_('?', '?', '?', msg, '', '', 'FAILED: ' + e.message));
        }
      }
    }
  }

  if (outOfTime) {
    SpreadsheetApp.getActive().toast('Time limit reached. Run again to continue.');
  }
}

/* ---------- CAMS: zip attached to the mail ---------- */

function handleCams_(msg, root) {
  const report = msg.getSubject().match(/WBR\d+/i)[0].toUpperCase();
  const account = accountFromRecipient_(msg);
  const attachments = msg.getAttachments({ includeInlineImages: false });
  if (attachments.length === 0) {
    return [logRow_('CAMS', report, account, msg, '', '', 'NO_FILE')];
  }
  const folder = getOrCreateFolder_(
      getOrCreateFolder_(getOrCreateFolder_(root, 'CAMS'), account), report);
  return attachments.map(function (att) {
    const name = fileName_(msg, report, account, att.getName());
    const file = folder.createFile(att.copyBlob().setName(name));
    const row = logRow_('CAMS', report, account, msg, name, file.getUrl(), 'SAVED');
    row.push(sendToCrm_(file.getBlob(), name));
    return row;
  });
}

/* ---------- KFintech: file behind a "Click Here" link ---------- */

function handleKfintech_(msg, root) {
  const text = msg.getPlainBody();
  const arn = (text.match(/ARN-\d+/i) || [accountFromRecipient_(msg)])[0].toUpperCase();
  const report = (text.match(/MFSD\d+/i) || ['KFIN'])[0].toUpperCase();
  const ref = (msg.getSubject().match(/WB[A-Z]+\d+/i) || ['REF'])[0].toUpperCase();

  const link = msg.getBody().match(
      /<a[^>]+href\s*=\s*["']([^"']+)["'][^>]*>(?:\s|<[^>]+>)*Click\s*Here/i);
  if (!link) {
    // KFintech sends a mail without a link on days with nothing to report.
    const status = /No data found/i.test(text) ? 'NO_FILE' : 'FAILED: no link found';
    return logRow_('KFintech', report, arn, msg, '', '', status);
  }

  const url = link[1].replace(/&amp;/g, '&');
  const resp = UrlFetchApp.fetch(url, { followRedirects: true, muteHttpExceptions: true });
  const type = String(resp.getHeaders()['Content-Type'] || '').toLowerCase();
  if (resp.getResponseCode() !== 200 || type.indexOf('text/html') > -1) {
    return logRow_('KFintech', report, arn, msg, '', '',
        'FAILED: link gave no file (HTTP ' + resp.getResponseCode() + ')');
  }

  const disposition = String(resp.getHeaders()['Content-Disposition'] || '');
  const sent = disposition.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)/i);
  const original = sent ? decodeURIComponent(sent[1]) : ref + '.zip';
  const name = fileName_(msg, report, arn, original);

  const folder = getOrCreateFolder_(
      getOrCreateFolder_(getOrCreateFolder_(root, 'KFintech'), arn), report);
  const file = folder.createFile(resp.getBlob().setName(name));
  const row = logRow_('KFintech', report, arn, msg, name, file.getUrl(), 'SAVED');
  row.push(sendToCrm_(file.getBlob(), name));
  return row;
}

/* ---------- Helpers ---------- */

function accountFromRecipient_(msg) {
  const to = msg.getTo().toLowerCase();
  for (const email in CONFIG.ACCOUNTS) {
    if (to.indexOf(email) > -1) return CONFIG.ACCOUNTS[email];
  }
  return 'UNKNOWN';
}

function fileName_(msg, report, account, original) {
  const day = Utilities.formatDate(msg.getDate(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
  return day + '_' + account + '_' + report + '_' + original;
}

function logRow_(source, report, account, msg, name, url, status) {
  return [new Date(), source, report, account, msg.getDate(), msg.getSubject(),
          name, url, status, msg.getId()];
}

function getLogSheet_() {
  const ss = SpreadsheetApp.getActive();
  let sheet = ss.getSheetByName('Log');
  if (!sheet) sheet = ss.insertSheet('Log');
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(LOG_HEADERS);
    sheet.setFrozenRows(1);
  } else if (sheet.getRange(1, LOG_HEADERS.length).getValue() === '') {
    sheet.getRange(1, LOG_HEADERS.length).setValue(LOG_HEADERS[LOG_HEADERS.length - 1]);
  }
  return sheet;
}

// Messages already saved (or that had no file) are skipped. Failed ones are retried.
function getDoneIds_(sheet) {
  const done = {};
  const last = sheet.getLastRow();
  if (last < 2) return done;
  sheet.getRange(2, 9, last - 1, 2).getValues().forEach(function (r) {
    if (r[0] === 'SAVED' || r[0] === 'NO_FILE') done[r[1]] = true;
  });
  return done;
}

// The first runs filed this account's CAMS reports under "UTTAM"; move them to the ARN.
function renameOldFolder_(root) {
  const cams = root.getFoldersByName('CAMS');
  if (!cams.hasNext()) return;
  const camsFolder = cams.next();
  const old = camsFolder.getFoldersByName('UTTAM');
  if (old.hasNext() && !camsFolder.getFoldersByName('ARN-13732').hasNext()) {
    old.next().setName('ARN-13732');
  }
}

function getOrCreateFolder_(parent, name) {
  const found = parent.getFoldersByName(name);
  return found.hasNext() ? found.next() : parent.createFolder(name);
}

/* ---------- CRM upload ---------- */

// Asks for the Supabase project URL and service key once and stores them in
// this script's private properties (not in the sheet).
function setUpCrm() {
  const ui = SpreadsheetApp.getUi();
  const url = ui.prompt('CRM connection', 'Supabase Project URL (https://....supabase.co):',
                        ui.ButtonSet.OK_CANCEL);
  if (url.getSelectedButton() !== ui.Button.OK) return;
  const key = ui.prompt('CRM connection', 'Supabase service_role key:', ui.ButtonSet.OK_CANCEL);
  if (key.getSelectedButton() !== ui.Button.OK) return;
  PropertiesService.getScriptProperties().setProperties({
    SUPABASE_URL: url.getResponseText().trim().replace(/\/+$/, ''),
    SUPABASE_SERVICE_KEY: key.getResponseText().trim()
  });
  ui.alert('Saved. New files will be sent to the CRM. Use "Send saved files to CRM" for files saved earlier.');
}

// Uploads one file to inbox/incoming/ in Supabase Storage. Returns the text for the CRM column.
function sendToCrm_(blob, name) {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('SUPABASE_URL');
  const key = props.getProperty('SUPABASE_SERVICE_KEY');
  if (!url || !key) return '';
  const path = 'incoming/' + name.replace(/[^A-Za-z0-9._-]/g, '_');
  const resp = UrlFetchApp.fetch(url + '/storage/v1/object/inbox/' + path, {
    method: 'post',
    contentType: blob.getContentType() || 'application/octet-stream',
    payload: blob.getBytes(),
    headers: key.indexOf('eyJ') === 0
        ? { Authorization: 'Bearer ' + key, apikey: key, 'x-upsert': 'true' }
        : { apikey: key, 'x-upsert': 'true' },
    muteHttpExceptions: true
  });
  const code = resp.getResponseCode();
  return code < 300 ? 'sent' : 'error ' + code + ': ' + resp.getContentText().slice(0, 120);
}

// Sends every file in the Log that was saved but not yet sent to the CRM.
function sendSavedFilesToCrm() {
  const started = Date.now();
  const sheet = getLogSheet_();
  const last = sheet.getLastRow();
  if (last < 2) return;
  const crmCol = LOG_HEADERS.length;
  const rows = sheet.getRange(2, 1, last - 1, crmCol).getValues();
  let sent = 0;
  for (let i = 0; i < rows.length; i++) {
    if (Date.now() - started > CONFIG.MAX_RUNTIME_MS) {
      SpreadsheetApp.getActive().toast('Time limit reached after ' + sent + ' files. Run again to continue.');
      return;
    }
    const status = rows[i][8], link = rows[i][7], name = rows[i][6], crm = rows[i][crmCol - 1];
    if (status !== 'SAVED' || crm === 'sent' || !link) continue;
    const id = String(link).match(/[-\w]{25,}/);
    if (!id) continue;
    const result = sendToCrm_(DriveApp.getFileById(id[0]).getBlob(), name);
    sheet.getRange(i + 2, crmCol).setValue(result || 'not set up');
    if (result === 'sent') sent++;
  }
  SpreadsheetApp.getActive().toast(sent + ' files sent to the CRM.');
}
