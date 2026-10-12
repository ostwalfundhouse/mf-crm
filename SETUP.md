# MF CRM setup

The CRM has four parts:

| Part | Where it runs | What it does |
|---|---|---|
| Mailback Collector (Apps Script) | Your Google account | Saves CAMS and KFintech mailback files from Gmail to Drive every morning and sends a copy to the CRM's file inbox |
| File inbox | Supabase Storage, bucket `inbox` | Holds files waiting to be imported; files uploaded from the website also land here |
| Importer (`importer/`) | GitHub Actions, every two hours | Unlocks each file, reads it by column name, adds new transactions, updates client details, recalculates holdings |
| Website (`docs/`) | GitHub Pages: https://ostwalfundhouse.github.io/mf-crm/ | Sign-in for the three users; clients, holdings, ARN map, missing details, uploads |

Database tables and rules are in `supabase/schema.sql`; reference data (KFintech transaction types and fund house codes) is in `supabase/seed.sql`.

## 1. Database

1. In Supabase, open **SQL Editor → New query**, paste the whole of `supabase/schema.sql`, and run it.
2. Do the same with `supabase/seed.sql`.
3. Add the three users who may sign in (replace the addresses):
   ```sql
   insert into app_users (email, name) values
     ('first@example.com', 'Name 1'),
     ('second@example.com', 'Name 2'),
     ('third@example.com', 'Name 3')
   on conflict (email) do nothing;
   ```

## 2. Sign-in

1. **Authentication → Sign In / Providers → Email**: keep Email enabled and turn **off** "Allow new users to sign up".
2. **Authentication → Users → Add user → Send invitation** for each of the three addresses.
3. **Authentication → URL Configuration**: set **Site URL** to the website address, and add the same address under Redirect URLs.

Only addresses in `app_users` can read any data, even if someone else gets an account.

## 3. Importer (GitHub)

In the GitHub repository: **Settings → Secrets and variables → Actions → New repository secret**, add:

| Name | Value |
|---|---|
| `SUPABASE_URL` | Project URL, e.g. `https://abcd.supabase.co` |
| `SUPABASE_SERVICE_KEY` | The `service_role` key or `sb_secret_...` secret key (Project Settings → API Keys). Never put this in the website or in chat. |
| `MAILBACK_ZIP_PASSWORD` | The password for the CAMS and KFintech zip files |

The workflow `.github/workflows/import.yml` then runs every two hours. To run it at once: **Actions → Import mailback files → Run workflow**.

## 4. Website

`docs/config.js` holds the Project URL and the publishable key (both are safe to publish). In GitHub: **Settings → Pages → Build and deployment → Deploy from a branch → main, /docs → Save.**

## 5. Mailback Collector

1. Replace the Apps Script code in the "Mailback Collector" sheet with `apps-script/MailbackCollector.gs` and save. The copy in this repository has placeholder email addresses in `ACCOUNTS`; put the real ones back before saving.
2. Reload the sheet, then **Mailback → Set up CRM connection** and enter the Project URL and the `service_role` key. They are stored in the script's private properties, not in the sheet.
3. **Mailback → Send saved files to CRM** sends the files saved so far.

## 6. First load of full history

On the website's **Upload files** page, upload each ARN's full downloads:

- CAMS: the investor static details file (WBR9) and the transaction file (WBR10), as .xls/.xlsx or as the zip.
- KFintech: the investor master (MFSD211) and the transaction report (MFSD201), as the **original CSV** or zip. Do not upload copies saved as Google Sheets: Sheets swaps day and month in KFintech dates.

Then run the workflow (or wait up to two hours). The **Upload files** page shows each file's result, and the `reconciliation` view in Supabase compares units worked out from transactions with the units the registrar reports.

## How the importer treats data

- Columns are found by name, so reordered columns still import.
- Client details are matched on registrar + folio + scheme; a newer report date replaces older details.
- CAMS transactions are matched on the CAMS transaction number; KFintech transactions (which have no number in the standard format) on folio + scheme + type + date + units + amount. Identical rows within one file are numbered so genuine repeats are kept.
- Units held = sum of signed units. CAMS gives the sign (`trxn_sign`, or worked out from the type code in the daily WBR2 feed). KFintech's sign comes from the "TRTypes & Flags" table (`importer/mfcrm/data/kfin_tr_types.csv`). One row of that table was corrected: STRIR (STRIP In Rejection) is treated as removing units.
- Cost value is first-in-first-out on purchase amounts; reversed purchases are removed rather than redeemed.
- Value = units × latest NAV in the `navs` table (filled from KFintech MFSD203 when available), otherwise the NAV implied by the registrar's own reported value.

## Testing locally

```
cd importer
python -m tests.test_logic
LOCAL_PSQL_DB=mfcrm MAILBACK_ZIP_PASSWORD=... python -m mfcrm.run --path /folder/with/files
```
`supabase/local_test_stub.sql` must be loaded before `schema.sql` on a plain PostgreSQL.
