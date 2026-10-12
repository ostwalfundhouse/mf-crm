"""Recognise each registrar report by its column names and turn rows into
records for the database.

Columns are always looked up by name, so a reordered file still imports.
A column that is missing from a file becomes None, which tells the database
to keep what it already has.
"""
import json
from collections import Counter

from . import values as V

MISSING = object()


def _get(row, cols, *names):
    for n in names:
        if n in cols:
            return row.get(n)
    return MISSING


def _t(row, cols, *names):
    v = _get(row, cols, *names)
    return None if v is MISSING else V.text(v)


def _n(row, cols, *names):
    v = _get(row, cols, *names)
    return None if v is MISSING else V.number(v)


def _d(row, cols, dayfirst, *names):
    v = _get(row, cols, *names)
    return None if v is MISSING else V.date(v, dayfirst)


def _raw(table, row):
    out = {}
    for orig, key in zip(table.original_headers, table.headers):
        v = row.get(key)
        if v is None or v == "":
            continue
        out[orig or key] = V.text(v) if not isinstance(v, (int, float)) else v
    return out


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect(table):
    c = table.columns
    if {"foliochk", "closbal"} <= c or {"foliochk", "panno", "invname"} <= c:
        return "cams_master"
    if {"trxnno", "trxntype"} <= c and ({"foliochk", "foliono"} & c):
        return "cams_txn"
    if {"balance", "aum"} <= c and ({"folionumber", "folio"} & c):
        return "kfin_aum"
    if {"pannumber", "nominee"} <= c and ({"folio", "folionumber"} & c):
        return "kfin_master"
    if {"transactiontype", "navdate", "units"} <= c:
        return "kfin_txn"
    return None


# ---------------------------------------------------------------------------
# Folios (investor master)
# ---------------------------------------------------------------------------

def cams_folios(table, source):
    c = table.columns
    out = []
    for r in table.rows:
        folio = V.code(_get(r, c, "foliochk", "foliono") or "")
        product = V.code(_get(r, c, "product", "prodcode") or "")
        if not product and {"amccode", "schcode"} <= c:
            product = V.code(r.get("amccode")) + V.code(r.get("schcode"))
        if not folio or not product:
            continue
        out.append({
            "registrar": "CAMS",
            "folio_no": folio,
            "product_code": product,
            "amc_code": _t(r, c, "amccode"),
            "scheme_name": _t(r, c, "schname", "scheme"),
            "pan": V.pan(r.get("panno")) if "panno" in c else (V.pan(r.get("pan")) if "pan" in c else None),
            "inv_name": _t(r, c, "invname"),
            "joint1_name": _t(r, c, "jntname1"),
            "joint2_name": _t(r, c, "jntname2"),
            "joint1_pan": _t(r, c, "joint1pan"),
            "joint2_pan": _t(r, c, "joint2pan"),
            "guardian_name": _t(r, c, "guardname"),
            "guardian_pan": _t(r, c, "guardpan"),
            "holding_mode": _t(r, c, "holdingnature"),
            "tax_status": _t(r, c, "taxstatus"),
            "dob": _d(r, c, False, "invdob"),
            "mobile": _t(r, c, "mobileno"),
            "email": _t(r, c, "email"),
            "phone_res": _t(r, c, "phoneres"),
            "phone_off": _t(r, c, "phoneoff"),
            "address1": _t(r, c, "address1"),
            "address2": _t(r, c, "address2"),
            "address3": _t(r, c, "address3"),
            "city": _t(r, c, "city"),
            "pincode": _t(r, c, "pincode"),
            "state": _t(r, c, "state"),
            "country": _t(r, c, "country"),
            "occupation": _t(r, c, "occupation"),
            "bank_name": _t(r, c, "bankname"),
            "bank_branch": _t(r, c, "branch"),
            "bank_ac_type": _t(r, c, "actype"),
            "bank_ac_no": _t(r, c, "acno"),
            "bank_ifsc": _t(r, c, "ifsccode"),
            "bank_city": _t(r, c, "bcity"),
            "nominee1_name": _t(r, c, "nomname"),
            "nominee1_relation": _t(r, c, "relation"),
            "nominee1_pct": _n(r, c, "nompercentage"),
            "nominee2_name": _t(r, c, "nom2name"),
            "nominee2_relation": _t(r, c, "nom2relation"),
            "nominee2_pct": _n(r, c, "nom2percentage"),
            "nominee3_name": _t(r, c, "nom3name"),
            "nominee3_relation": _t(r, c, "nom3relation"),
            "nominee3_pct": _n(r, c, "nom3percentage"),
            "arn": V.arn(_get(r, c, "brokercode", "brokcode")) if ({"brokercode", "brokcode"} & c) else None,
            "sub_broker": _t(r, c, "subbroker"),
            "units_reported": _n(r, c, "closbal"),
            "value_reported": _n(r, c, "rupeebal"),
            "report_date": _d(r, c, False, "repdate"),
            "folio_date": _d(r, c, False, "foliodate"),
            "source_file": source,
            "raw": _raw(table, r),
        })
    return out


def kfin_folios(table, source):
    c = table.columns
    out = []
    for r in table.rows:
        folio = V.code(_get(r, c, "folio", "folionumber") or "")
        product = V.code(_get(r, c, "productcode") or "")
        if not folio or not product:
            continue
        out.append({
            "registrar": "KFIN",
            "folio_no": folio,
            "product_code": product,
            "amc_code": _t(r, c, "fund"),
            "scheme_name": _t(r, c, "funddescription"),
            "pan": V.pan(r.get("pannumber")) if "pannumber" in c else None,
            "inv_name": _t(r, c, "investorname"),
            "joint1_name": _t(r, c, "jointname1"),
            "joint2_name": _t(r, c, "jointname2"),
            "holding_mode": _t(r, c, "modeofholdingdescription"),
            "tax_status": _t(r, c, "taxstatus"),
            "dob": _d(r, c, True, "dateofbirth"),
            "mobile": _t(r, c, "mobilenumber"),
            "email": _t(r, c, "email"),
            "phone_res": _t(r, c, "phoneresidence"),
            "phone_off": _t(r, c, "phoneoffice"),
            "address1": _t(r, c, "address1"),
            "address2": _t(r, c, "address2"),
            "address3": _t(r, c, "address3"),
            "city": _t(r, c, "city"),
            "pincode": _t(r, c, "pincode"),
            "state": _t(r, c, "state"),
            "country": _t(r, c, "country"),
            "occupation": _t(r, c, "occupationdescription"),
            "bank_name": _t(r, c, "bankname"),
            "bank_branch": _t(r, c, "branch"),
            "bank_ac_type": _t(r, c, "accounttype"),
            "bank_ac_no": _t(r, c, "bankaccno"),
            "bank_city": _t(r, c, "bankcity"),
            "nominee1_name": _t(r, c, "nominee"),
            "nominee2_name": _t(r, c, "nominee2"),
            "nominee3_name": _t(r, c, "nominee3"),
            "arn": V.arn(r.get("brokercode")) if "brokercode" in c else None,
            "report_date": _d(r, c, True, "reportdate"),
            "source_file": source,
            "raw": _raw(table, r),
        })
    return out


def kfin_aum(table, source):
    """MFSD 203 Client Wise AUM: units and value per folio, plus NAV."""
    c = table.columns
    folios, navs = [], []
    for r in table.rows:
        folio = V.code(_get(r, c, "folionumber", "folio") or "")
        product = V.code(_get(r, c, "productcode") or "")
        if not folio or not product:
            continue
        rep = _d(r, c, True, "reportdate", "todate")
        folios.append({
            "registrar": "KFIN", "folio_no": folio, "product_code": product,
            "amc_code": _t(r, c, "fund"),
            "scheme_name": _t(r, c, "funddescription"),
            "pan": V.pan(r.get("pan")) if "pan" in c else None,
            "inv_name": _t(r, c, "investorname"),
            "email": _t(r, c, "email"),
            "mobile": _t(r, c, "mobileno"),
            "arn": V.arn(r.get("agentcode")) if "agentcode" in c else None,
            "units_reported": _n(r, c, "balance"),
            "value_reported": _n(r, c, "aum"),
            "report_date": rep,
            "source_file": source,
        })
        nav = _n(r, c, "nav")
        if nav and rep:
            navs.append({"registrar": "KFIN", "product_code": product, "nav_date": rep,
                         "nav": nav, "isin": _t(r, c, "schemeisin"), "source": source})
    return folios, navs


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def cams_sign_from_type(trxn_type, units):
    """Used when a CAMS file has no trxn_sign column (the daily WBR2 feed).
    Rules checked against the trxn_sign column of the full ARN-172637 history."""
    if not units:
        return 0
    t = (trxn_type or "").upper()
    if t.startswith(("SI", "TI")):
        return 1
    if t.startswith(("SO", "TO")):
        return -1
    if t.startswith("P"):
        return 1
    if t.startswith("R"):
        return -1
    return None   # unknown: reported as a warning


def _with_occurrence(records):
    """Identical rows inside one file are kept apart by numbering them."""
    seen = Counter()
    for rec in records:
        base = rec["dedup_key"]
        seen[base] += 1
        rec["dedup_key"] = f"{base}#{seen[base]}"
    return records


def cams_transactions(table, source):
    c = table.columns
    out, unknown = [], Counter()
    for r in table.rows:
        folio = V.code(_get(r, c, "foliochk", "foliono") or "")
        amc = V.code(r.get("amccode")) if "amccode" in c else ""
        if "prodcode" in c and V.code(r.get("prodcode")):
            product = V.code(r.get("prodcode"))
        else:
            product = amc + V.code(r.get("schcode")) if "schcode" in c else ""
        if not folio or not product:
            continue
        ttype = V.code(r.get("trxntype"))
        tno = V.code(r.get("trxnno"))
        mode = V.code(r.get("trxnmode")) if "trxnmode" in c else ""
        units = V.number(r.get("units"))
        amount = V.number(r.get("amount"))
        if "trxnsign" in c:
            s = V.code(r.get("trxnsign"))
            sign = 1 if s == "+" else -1 if s == "-" else 0
        else:
            sign = cams_sign_from_type(ttype, units)
            if sign is None:
                unknown[ttype] += 1
                sign = 0
        tdate = _d(r, c, False, "traddate")
        if tno:
            key = f"C|{folio}|{product}|{tno}|{mode}|{ttype}"
        else:
            key = f"C|{folio}|{product}|{ttype}|{tdate}|{units}|{amount}"
        out.append({
            "dedup_key": key,
            "registrar": "CAMS",
            "amc_code": amc or None,
            "folio_no": folio,
            "product_code": product,
            "scheme_name": _t(r, c, "scheme"),
            "inv_name": _t(r, c, "invname"),
            "pan": V.pan(r.get("pan")) if "pan" in c else None,
            "trxn_type": ttype,
            "trxn_desc": _t(r, c, "trxntypeflag", "trxnnature"),
            "trxn_no": tno or None,
            "trxn_mode": mode or None,
            "trxn_date": tdate,
            "post_date": _d(r, c, False, "postdate"),
            "units": units,
            "amount": amount,
            "price": V.number(r.get("purprice")) if "purprice" in c else None,
            "sign": sign,
            "signed_units": (sign * units) if (units is not None) else None,
            "is_reversal": mode in ("R", "D"),
            "arn": V.arn(_get(r, c, "brokcode", "brokercode")) if ({"brokcode", "brokercode"} & c) else None,
            "sub_broker": _t(r, c, "subbrok"),
            "euin": _t(r, c, "euin"),
            "report_date": _d(r, c, False, "repdate"),
            "source_file": source,
        })
    return _with_occurrence(out), unknown


def kfin_transactions(table, source, tr_types):
    """tr_types: {code: {"purred": "P"/"R"/"D", "trn_mode": "N"/"R", "description": ...}}"""
    c = table.columns
    out, unknown = [], Counter()
    for r in table.rows:
        folio = V.code(_get(r, c, "folionumber", "folio") or "")
        product = V.code(_get(r, c, "productcode") or "")
        if not folio or not product:
            continue
        ttype = V.code(r.get("transactiontype"))
        units = V.number(r.get("units"))
        amount = V.number(r.get("amount"))
        info = tr_types.get(ttype)
        if info is None:
            unknown[ttype] += 1
            sign, reversal, desc = 0, False, None
        else:
            purred = (info.get("purred") or "").upper()
            sign = 1 if purred == "P" else -1 if purred == "R" else 0
            reversal = (info.get("trn_mode") or "").upper() == "R"
            desc = info.get("description")
        tdate = _d(r, c, True, "navdate")
        u_key = f"{abs(units):.4f}" if units is not None else ""
        a_key = f"{abs(amount):.2f}" if amount is not None else ""
        key = f"K|{folio}|{product}|{ttype}|{tdate}|{u_key}|{a_key}"
        out.append({
            "dedup_key": key,
            "registrar": "KFIN",
            "amc_code": _t(r, c, "fund"),
            "folio_no": folio,
            "product_code": product,
            "scheme_name": _t(r, c, "funddescription"),
            "inv_name": _t(r, c, "investorname"),
            "pan": V.pan(r.get("pan1")) if "pan1" in c else None,
            "trxn_type": ttype,
            "trxn_desc": _t(r, c, "transactiondescription") or desc,
            "trxn_no": _t(r, c, "transactionnumber") or None,
            "trxn_mode": _t(r, c, "transactionmode") or None,
            "trxn_date": tdate,
            "post_date": _d(r, c, True, "processdate"),
            "units": units,
            "amount": amount,
            "price": _n(r, c, "price", "nav"),
            "sign": sign,
            "signed_units": (sign * abs(units)) if units is not None else None,
            "is_reversal": reversal,
            "arn": V.arn(r.get("agentcode")) if "agentcode" in c else None,
            "sub_broker": _t(r, c, "subbrokercode"),
            "euin": _t(r, c, "euin"),
            "report_date": _d(r, c, True, "reportdate"),
            "source_file": source,
        })
    return _with_occurrence(out), unknown


def jsonable(records):
    """Round-trip through JSON so dates and numbers are plain values."""
    return json.loads(json.dumps(records, default=str))
