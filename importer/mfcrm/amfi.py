"""Daily NAVs from AMFI (https://portal.amfiindia.com/spages/NAVAll.txt).

The registrars' product codes (for example "108FBGP" or "L72S") do not appear
in AMFI's file, so each scheme is matched to AMFI's list by name: same fund
house, same plan (Regular / Direct), same option (Growth / IDCW), and the
closest scheme name. Each match is stored in `scheme_map` with a score; a
match marked 'manual' there is never overwritten. The NAV for every matched
product code then goes into `navs`.

Run:  python -m mfcrm.amfi
"""
import os
import re
import sys

import requests

from .db import PsqlDB, SupabaseDB

AMFI_URLS = ["https://portal.amfiindia.com/spages/NAVAll.txt",
             "https://www.amfiindia.com/spages/NAVAll.txt"]
MIN_SCORE = 0.75

STOP = {"fund", "plan", "option", "the", "scheme", "mutual", "mf", "an", "open", "ended",
        "of", "and", "regular", "direct", "growth", "gr", "idcw", "dividend", "div",
        "payout", "reinvestment", "reinvest", "income", "distribution", "cum", "capital",
        "withdrawal", "formerly", "known", "as", "erstwhile", "reg", "dir", "g", "option",
        "options", "sub", "plans", "na", "unit", "units"}

# Fund house names as they start scheme names, with words AMFI uses in its headers.
HOUSE_ALIASES = {
    "aditya": "aditya birla", "abakkus": "abakkus", "axis": "axis", "bajaj": "bajaj finserv",
    "bandhan": "bandhan", "bank": "bank of india", "baroda": "baroda bnp", "boi": "bank of india",
    "canara": "canara robeco", "dsp": "dsp", "edelweiss": "edelweiss", "franklin": "franklin",
    "groww": "groww", "hdfc": "hdfc", "helios": "helios", "hsbc": "hsbc", "icici": "icici",
    "invesco": "invesco", "iti": "iti", "jm": "jm financial", "kotak": "kotak", "lic": "lic",
    "mahindra": "mahindra", "mirae": "mirae", "motilal": "motilal", "navi": "navi",
    "nippon": "nippon", "nj": "nj", "old": "old bridge", "parag": "ppfas", "ppfas": "ppfas",
    "pgim": "pgim", "quant": "quant", "quantum": "quantum", "samco": "samco", "sbi": "sbi",
    "shriram": "shriram", "sundaram": "sundaram", "tata": "tata", "taurus": "taurus",
    "trust": "trust", "union": "union", "uti": "uti", "whiteoak": "whiteoak", "white": "whiteoak",
    "zerodha": "zerodha", "360": "360 one", "unifi": "unifi", "capitalmind": "capitalmind",
    "angel": "angel one", "the": "wealth company", "wealth": "wealth company", "altiva": "edelweiss",
}


def norm(text):
    t = (text or "").lower().replace("&", " and ")
    t = re.sub(r"off[\s-]*shore", "offshore", t)
    t = re.sub(r"mid[\s-]*cap", "midcap", t)
    t = re.sub(r"\bfund of funds?\b", "fof", t)
    t = re.sub(r"\bquaterly\b", "quarterly", t)
    t = re.sub(r"\bhalf yearly\b", "halfyearly", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def plan_of(name):
    return "direct" if re.search(r"\bdirect\b|\bdir\b", norm(name)) else "regular"


def option_of(name, divopt=None):
    n = norm(name)
    if divopt:
        d = divopt.strip().upper()
        if d.startswith("G"):
            return "growth"
        if d and d[0] in "DIRP":
            return "idcw"
    if re.search(r"\bidcw\b|\bdividend\b|\bdiv\b|\bincome distribution\b|\bbonus\b", n):
        return "idcw" if "bonus" not in n else "bonus"
    if re.search(r"\bgrowth\b|\bgr\b|\bg\b|\bcumulative\b", n):
        return "growth"
    return "growth"   # schemes with no option named are normally growth


def tokens(name):
    n = name or ""
    n = re.sub(r"(?i)fund(regular|direct)", r"fund \1", n)             # "FundRegular"
    n = re.sub(r"(?i)\(?\s*(formerly|erstwhile)\b.*$", "", n)           # old names
    n = re.sub(r"(?i)\([^)]*80\s*c[^)]*\)", " ", n)                       # "(ELSS U/S 80C of IT Act)"
    return {w for w in norm(n).split() if w not in STOP and not re.fullmatch(r"\d+", w)}


def house_of(name):
    first = norm(name).split(" ")[0] if name else ""
    return HOUSE_ALIASES.get(first, first)


def parse_navall(text):
    """Return a list of AMFI schemes with code, isin, name, plan, option, nav, date, fund house.
    Columns are read by header name; newer files have separate Plan and Option columns."""
    out, house, cols = [], "", None
    months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ";" not in line:
            if "mutual fund" in line.lower() or line.lower().endswith(" mf"):
                house = norm(line)
            continue
        parts = [p.strip() for p in line.split(";")]
        if parts[0].lower() == "scheme code":
            cols = {re.sub(r"[^a-z]", "", h.lower()): i for i, h in enumerate(parts)}
            continue
        if not parts[0].isdigit():
            continue
        c = cols or {"schemecode": 0, "isindivpayoutisingrowth": 1, "isindivreinvestment": 2,
                     "schemename": 3, "netassetvalue": 4, "date": 5}
        get = lambda k: parts[c[k]] if k in c and c[k] < len(parts) else ""
        try:
            navf = float(get("netassetvalue"))
        except ValueError:
            continue
        m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", get("date"))
        if not m:
            continue
        iso = f"{m.group(3)}-{months.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}"
        name = get("schemename")
        isin1, isin2 = get("isindivpayoutisingrowth"), get("isindivreinvestment")
        plan_col, opt_col = get("plan"), get("option")
        if not plan_col and re.search(r"\bdirect\b|\bregular\b", norm(opt_col)):
            plan_col = opt_col                      # e.g. Option "Direct Growth"
        named_plan = re.search(r"\bdirect\b|\bregular\b", norm(name))
        plan = ("direct" if "direct" in plan_col.lower() else "regular") if plan_col else plan_of(name)
        reinvest = isin2 not in ("", "-")
        explicit = bool(opt_col) or bool(re.search(r"\bgrowth\b|\bidcw\b|\bdividend\b|\bbonus\b", norm(name)))
        if opt_col:
            o = opt_col.lower()
            option = "growth" if "growth" in o or "cumulative" in o else "bonus" if "bonus" in o else "idcw"
        elif explicit:
            option = option_of(name)
        else:
            # No option given: a scheme with a reinvestment ISIN pays IDCW; otherwise growth.
            option = "idcw" if reinvest else "growth"
        freq = {w for w in norm(opt_col).split()
                if w in ("monthly", "quarterly", "weekly", "daily", "annual", "halfyearly",
                         "fortnightly", "periodic", "flexi")}
        out.append({"code": parts[c.get("schemecode", 0)], "isin": isin1 if isin1 not in ("", "-") else isin2,
                    "name": name, "nav": navf, "date": iso, "house": house,
                    "plan": plan, "option": option, "explicit": explicit, "tokens": tokens(name) | freq,
                    "plan_unknown": not plan_col and not named_plan})
    return resolve_plans(drop_stale(out))


def drop_stale(schemes, days=60):
    """Leave out schemes whose last NAV is far older than the file (wound-up plans)."""
    import datetime as dt
    if not schemes:
        return schemes
    newest = max(dt.date.fromisoformat(s["date"]) for s in schemes)
    return [s for s in schemes if (newest - dt.date.fromisoformat(s["date"])).days <= days]


def resolve_plans(schemes):
    """Many AMFI rows leave Plan blank and the name does not say Regular or Direct.
    For such a scheme with exactly two versions of the same option, the one with the
    lower NAV is Regular (its expenses are higher) and the other Direct. A single
    version is taken as is; three or more stay undecided and are not matched."""
    groups = {}
    for s in schemes:
        if s["plan_unknown"]:
            groups.setdefault((s["house"], norm(s["name"]), s["option"]), []).append(s)
    for g in groups.values():
        if len(g) == 2:
            lo, hi = sorted(g, key=lambda x: x["nav"])
            lo["plan"], hi["plan"] = "regular", "direct"
        elif len(g) > 2:
            for s in g:
                s["plan"] = "unknown"
    return schemes


def ranked(product, schemes, hint=None):
    """AMFI schemes of the same fund house, plan and option, best name match first.
    Score = shared words / all words in either name (1.0 = same words).
    `hint` is a name to match instead (a scheme's new name); plan and option still
    come from our own scheme name."""
    name, divopt = product["scheme_name"] or "", product.get("divopt")
    want_plan, want_opt = plan_of(name), option_of(name, divopt)
    want = tokens(hint or name)
    house = house_of(name)
    if not want:
        return []
    out = []
    for s in schemes:
        if s["plan"] != want_plan or s["option"] != want_opt:
            continue
        if house and not (re.search(rf"\b{re.escape(house)}\b", s["house"])
                          or re.search(rf"\b{re.escape(house)}\b", norm(s["name"]))):
            continue
        inter = len(want & s["tokens"])
        if inter:
            out.append((inter / len(want | s["tokens"]), s))
    # Equal names: prefer the scheme whose option AMFI states outright.
    out.sort(key=lambda x: (-x[0], not x[1].get("explicit", True)))
    return out


def best_match(product, schemes):
    r = ranked(product, schemes)
    if not r:
        return None, 0.0, []
    top_score, top = r[0]
    cands = [f"{x['name']} [{x['code']}] {sc:.2f}" for sc, x in r[:3]]
    tied = [x for sc, x in r if sc == top_score and x.get("explicit", True) == top.get("explicit", True)]
    if len({round(x["nav"], 4) for x in tied}) > 1:
        return None, top_score, cands     # equal fits with different NAVs: leave for review
    # (equal fits with the same NAV, such as IDCW payout and reinvestment, are safe to use)
    return top, top_score, cands


AMFI_URLS += ["https://www.amfiindia.com/spages/NAVOpen.txt",
              "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=&todt="]


def download():
    notes = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
               "Accept": "text/plain,*/*"}
    for url in AMFI_URLS:
        try:
            r = requests.get(url, timeout=120, headers=headers)
            text = r.text
            n = len(parse_navall(text)) if r.status_code == 200 else 0
            notes.append(f"{url} -> HTTP {r.status_code}, {len(text)} chars, {n} schemes, "
                         f"starts {text[:120]!r}")
            if n > 1000:
                print(notes[-1])
                return text
        except requests.RequestException as exc:
            notes.append(f"{url} -> {exc}")
    raise RuntimeError("Could not download AMFI NAVs:\n" + "\n".join(notes))


def run(db, text=None):
    schemes = parse_navall(text or download())
    by_code = {s["code"]: s for s in schemes}
    products = db.select("nav_products", "select=*")
    manual = {(m["registrar"], m["product_code"]): m for m in db.select("scheme_map", "select=*")
              if m.get("method") == "manual"}
    maps, navs, unmatched = [], [], 0
    for p in products:
        key = (p["registrar"], p["product_code"])
        cands = []
        if key in manual:
            m = manual[key]
            s = by_code.get(m.get("amfi_code") or "")
            if s is None and m.get("amfi_name"):
                # A manual match given by the scheme's new name: the AMFI scheme with
                # those words, in our plan and option.
                r = ranked(p, schemes, hint=m["amfi_name"])
                s = r[0][1] if r and r[0][0] >= MIN_SCORE else None
            score, method = 1.0, "manual"
            if s is None:
                unmatched += 1
                print(f"manual match not found: {p['scheme_name']} -> {m.get('amfi_name')}")
                continue
            hint = m.get("amfi_name") if not m.get("amfi_code") else None
            maps.append({"registrar": p["registrar"], "product_code": p["product_code"],
                         "scheme_name": p["scheme_name"], "amfi_code": None if hint else s["code"],
                         "amfi_name": hint or s["name"], "isin": s["isin"], "score": 1.0,
                         "method": "manual", "candidates": f"{s['name']} [{s['code']}]"})
            navs.append({"registrar": p["registrar"], "product_code": p["product_code"],
                         "nav_date": s["date"], "nav": s["nav"], "isin": s["isin"], "source": "AMFI"})
            continue
        else:
            s, score, cands = best_match(p, schemes)
            method = "auto"
            if s is None or score < MIN_SCORE:
                unmatched += 1
                maps.append({"registrar": p["registrar"], "product_code": p["product_code"],
                             "scheme_name": p["scheme_name"], "amfi_code": None, "amfi_name": None,
                             "isin": None, "score": round(score, 3), "method": "unmatched",
                             "candidates": "; ".join(cands)})
                continue
        maps.append({"registrar": p["registrar"], "product_code": p["product_code"],
                     "scheme_name": p["scheme_name"], "amfi_code": s["code"], "amfi_name": s["name"],
                     "isin": s["isin"], "score": round(score, 3), "method": method,
                     "candidates": "; ".join(cands)})
        navs.append({"registrar": p["registrar"], "product_code": p["product_code"],
                     "nav_date": s["date"], "nav": s["nav"], "isin": s["isin"], "source": "AMFI"})
    for i in range(0, len(maps), 500):
        db.rpc("upsert_scheme_map", rows=maps[i:i + 500])
    for i in range(0, len(navs), 500):
        db.rpc("ingest_navs", rows=navs[i:i + 500])
    print(f"AMFI: {len(schemes)} schemes in file; {len(products)} of our schemes; "
          f"{len(navs)} NAVs saved; {unmatched} not matched")


def main():
    if os.environ.get("LOCAL_PSQL_DB"):
        db = PsqlDB(os.environ["LOCAL_PSQL_DB"])
    else:
        url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY")
        db = SupabaseDB(url, key)
    text = None
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
            text = f.read()
    run(db, text)


if __name__ == "__main__":
    main()
