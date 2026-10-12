"""Checks for the holdings and parsing rules. Run: python -m tests.test_logic"""
from mfcrm import holdings as H
from mfcrm import reports as R
from mfcrm import values as V
from mfcrm.tables import Table


def t(date, su, amt, rev=False, arn="ARN-1"):
    return {"registrar": "CAMS", "folio_no": "1", "product_code": "X", "trxn_date": date,
            "signed_units": su, "amount": amt, "price": 0, "is_reversal": rev, "arn": arn}


def test_fifo():
    h = H.compute([t("2024-01-01", 100, 1000), t("2024-02-01", 50, 600),
                   t("2024-03-01", -120, 1500)])[0]
    assert h["units"] == 30 and h["cost_value"] == 360.0, h
    assert h["invested_total"] == 1600 and h["withdrawn_total"] == 1500, h


def test_reversed_purchase_removed():
    h = H.compute([t("2024-01-01", 100, 1000), t("2024-02-01", 10, 200),
                   t("2024-02-03", -10, 200, rev=True)])[0]
    assert h["units"] == 100 and h["cost_value"] == 1000.0, h


def test_full_exit():
    h = H.compute([t("2024-01-01", 100, 1000), t("2024-05-01", -100, 1300)])[0]
    assert h["units"] == 0 and h["cost_value"] == 0, h


def test_same_day_order():
    # Purchase and switch-out on one day: purchase is applied first.
    h = H.compute([t("2024-01-01", -40, 400), t("2024-01-01", 100, 1000)])[0]
    assert h["units"] == 60 and h["cost_value"] == 600.0, h


def test_dates():
    assert V.date("01/10/2024", dayfirst=True) == "2024-10-01"
    assert V.date("3/11/2022", dayfirst=False) == "2022-03-11"
    assert V.date("", True) is None
    assert V.date("31-Mar-2024", True) == "2024-03-31"


def test_cams_sign_rules():
    assert R.cams_sign_from_type("P227ES", 5) == 1
    assert R.cams_sign_from_type("SISTL30", 5) == 1
    assert R.cams_sign_from_type("SOSTPL", 5) == -1
    assert R.cams_sign_from_type("R1I", 5) == -1
    assert R.cams_sign_from_type("TICOB", 5) == 1
    assert R.cams_sign_from_type("NOM", 0) == 0
    assert R.cams_sign_from_type("XYZ", 5) is None


def test_columns_found_by_name_in_any_order():
    headers = ["Units", "Transaction Type", "Folio Number", "Nav Date", "Product Code", "Amount"]
    row = ["10.5", "RED", "123", "05/04/2025", "108FBGP", "1000"]
    table = Table("csv", headers, [dict(zip([R.V.text(h).lower().replace(" ", "") for h in headers], row))])
    assert R.detect(table) == "kfin_txn"
    tr = {"RED": {"purred": "R", "trn_mode": "N", "description": "Redemption"}}
    recs, unknown = R.kfin_transactions(table, "f.csv", tr)
    assert not unknown
    r = recs[0]
    assert r["signed_units"] == -10.5 and r["trxn_date"] == "2025-04-05", r
    assert r["dedup_key"].endswith("#1")


def test_identical_rows_kept_apart():
    headers = ["Units", "Transaction Type", "Folio Number", "Nav Date", "Product Code", "Amount"]
    keys = [h.lower().replace(" ", "") for h in headers]
    row = dict(zip(keys, ["1", "SIN", "1", "01/01/2025", "P", "100"]))
    table = Table("csv", headers, [row, dict(row)])
    recs, _ = R.kfin_transactions(table, "f.csv", {"SIN": {"purred": "P", "trn_mode": "N"}})
    assert recs[0]["dedup_key"] != recs[1]["dedup_key"]


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
    print(f"{n} checks passed")
