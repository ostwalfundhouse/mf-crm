"""Import registrar report files into the CRM database.

Production (GitHub Action):
    python -m mfcrm.run --inbox
        Reads every file under incoming/ in the Supabase Storage bucket "inbox",
        imports it, and moves it to processed/ or failed/.

Local files:
    python -m mfcrm.run --path some/file.zip --path some/folder

Environment:
    SUPABASE_URL, SUPABASE_SERVICE_KEY   for Supabase
    LOCAL_PSQL_DB                        use a local PostgreSQL instead (tests)
    MAILBACK_ZIP_PASSWORD                password for the registrar zip files
"""
import argparse
import csv
import datetime as dt
import os
import sys

from . import holdings as H
from . import reports as R
from .db import PsqlDB, SupabaseDB
from .tables import read_tables
from .zipread import WrongPassword, is_zip, unzip

BATCH = 500
DATA = os.path.join(os.path.dirname(__file__), "data")


def load_tr_types():
    with open(os.path.join(DATA, "kfin_tr_types.csv"), newline="") as f:
        return {r["code"]: r for r in csv.DictReader(f)}


def connect():
    if os.environ.get("LOCAL_PSQL_DB"):
        return PsqlDB(os.environ["LOCAL_PSQL_DB"])
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY (or LOCAL_PSQL_DB for tests).")
    return SupabaseDB(url, key)


def _batches(rows):
    for i in range(0, len(rows), BATCH):
        yield rows[i:i + BATCH]


def _add(total, res):
    for k in ("inserted", "updated", "skipped"):
        total[k] = total.get(k, 0) + int(res.get(k, 0))


class Importer:
    def __init__(self, db, password):
        self.db = db
        self.password = password
        self.tr_types = load_tr_types()
        self.touched = set()          # folio keys whose holdings need recalculating

    def import_bytes(self, name, data):
        """Import one file. Returns a summary dict for the log."""
        if is_zip(data) and not name.lower().endswith((".xlsx", ".xlsm")):
            inner = unzip(data, self.password)
        else:
            inner = [(name, data)]

        summary = {"report_type": [], "rows_read": 0, "inserted": 0, "updated": 0,
                   "skipped": 0, "warnings": []}
        for inner_name, content in inner:
            for table in read_tables(inner_name, content):
                kind = R.detect(table)
                if not kind:
                    continue
                summary["report_type"].append(kind)
                summary["rows_read"] += len(table.rows)
                totals = {}
                if kind == "cams_master":
                    self._folios(R.cams_folios(table, name), totals)
                elif kind == "kfin_master":
                    self._folios(R.kfin_folios(table, name), totals)
                elif kind == "kfin_aum":
                    folios, navs = R.kfin_aum(table, name)
                    self._folios(folios, totals)
                    for b in _batches(R.jsonable(navs)):
                        self.db.rpc("ingest_navs", rows=b)
                elif kind == "cams_txn":
                    txns, unknown = R.cams_transactions(table, name)
                    self._transactions(txns, totals)
                    if unknown:
                        summary["warnings"].append("CAMS types without a sign: " +
                                                   ", ".join(f"{k} ({v})" for k, v in unknown.items()))
                elif kind == "kfin_txn":
                    txns, unknown = R.kfin_transactions(table, name, self.tr_types)
                    self._transactions(txns, totals)
                    if unknown:
                        summary["warnings"].append("KFintech types not in TRTypes: " +
                                                   ", ".join(f"{k} ({v})" for k, v in unknown.items()))
                for k in ("inserted", "updated", "skipped"):
                    summary[k] += totals.get(k, 0)
        return summary

    def _folios(self, rows, totals):
        for b in _batches(R.jsonable(rows)):
            _add(totals, self.db.rpc("ingest_folios", rows=b))

    def _transactions(self, rows, totals):
        for r in rows:
            self.touched.add(f"{r['registrar']}|{r['folio_no']}|{r['product_code']}")
        for b in _batches(R.jsonable(rows)):
            _add(totals, self.db.rpc("ingest_transactions", rows=b))

    def recompute_holdings(self):
        keys = sorted(self.touched)
        n = 0
        for i in range(0, len(keys), 200):
            chunk = keys[i:i + 200]
            txns = self.db.rpc("transactions_for_keys", keys=chunk)
            rows = H.compute(txns)
            for b in _batches(R.jsonable(rows)):
                n += int(self.db.rpc("upsert_holdings", rows=b).get("upserted", 0))
        self.touched.clear()
        return n


def process(importer, name, data):
    db = importer.db
    log_id = db.log_start(name)
    try:
        s = importer.import_bytes(name, data)
        status = "done" if s["report_type"] else "unrecognised"
        msg = "; ".join(s["warnings"]) or None
        db.log_finish(log_id, report_type=",".join(sorted(set(s["report_type"]))) or None,
                      rows_read=s["rows_read"], inserted=s["inserted"], updated=s["updated"],
                      skipped=s["skipped"], status=status, message=msg)
        print(f"{status:12} {name}: read {s['rows_read']}, new {s['inserted']}, "
              f"updated {s['updated']}, skipped {s['skipped']}" + (f" | {msg}" if msg else ""))
        return status
    except WrongPassword:
        db.log_finish(log_id, status="failed", message="Wrong zip password")
        print(f"failed       {name}: wrong zip password")
        return "failed"
    except Exception as exc:              # keep going with the next file
        db.log_finish(log_id, status="failed", message=str(exc)[:1000])
        print(f"failed       {name}: {exc}")
        return "failed"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--inbox", action="store_true", help="import files from Supabase Storage")
    p.add_argument("--path", action="append", default=[], help="file or folder to import")
    p.add_argument("--recompute-all", action="store_true",
                   help="recalculate holdings for every folio")
    args = p.parse_args(argv)

    password = os.environ.get("MAILBACK_ZIP_PASSWORD", "")
    db = connect()
    imp = Importer(db, password)

    if args.inbox:
        stamp = dt.date.today().isoformat()
        for path in db.list_files("incoming/"):
            name = path.split("/", 1)[1]
            status = process(imp, name, db.download(path))
            folder = "processed" if status == "done" else "failed"
            db.move(path, f"{folder}/{stamp}/{name}")

    for target in args.path:
        files = []
        if os.path.isdir(target):
            for root, _dirs, names in os.walk(target):
                files += [os.path.join(root, n) for n in sorted(names)]
        else:
            files = [target]
        for f in files:
            with open(f, "rb") as fh:
                process(imp, os.path.basename(f), fh.read())

    if args.recompute_all:
        for row in db.select("transactions", "select=registrar,folio_no,product_code"):
            imp.touched.add(f"{row['registrar']}|{row['folio_no']}|{row['product_code']}")

    if imp.touched:
        n = imp.recompute_holdings()
        print(f"holdings recalculated for {n} folio-schemes")


if __name__ == "__main__":
    main()
