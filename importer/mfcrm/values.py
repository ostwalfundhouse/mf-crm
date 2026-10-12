"""Cleaning helpers for registrar data."""
import datetime as dt
import re


def text(v):
    """Trimmed text. Missing column -> None (keeps stored value); empty cell -> ''."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    return re.sub(r"\s+", " ", str(v)).strip()


def code(v):
    """Identifiers such as folio numbers: no spaces, no '.0' from Excel."""
    return text(v).replace(" ", "").upper()


def number(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "NA", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def date(v, dayfirst):
    """Parse a date. CAMS text dates are month/day/year, KFintech day/month/year."""
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    s = str(v).strip()
    if not s or s in ("0", "00/00/0000"):
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe(y, mo, d)
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", s)
    if m:
        a, b, y = map(int, m.groups())
        if y < 100:
            y += 2000 if y < 50 else 1900
        d, mo = (a, b) if dayfirst else (b, a)
        return _safe(y, mo, d)
    m = re.match(r"^(\d{1,2})[- ]([A-Za-z]{3})[- ](\d{2,4})", s)
    if m:
        d, mon, y = m.groups()
        y = int(y)
        if y < 100:
            y += 2000 if y < 50 else 1900
        try:
            mo = dt.datetime.strptime(mon.title(), "%b").month
        except ValueError:
            return None
        return _safe(y, mo, int(d))
    return None


def _safe(y, mo, d):
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def pan(v):
    p = code(v)
    return p if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", p) else (p or "")


def arn(v):
    s = code(v)
    if not s:
        return ""
    if re.match(r"^\d+$", s):
        return f"ARN-{s}"
    return s
