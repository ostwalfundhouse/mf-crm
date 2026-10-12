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
MIN_SCORE = 0.6

STOP = {"fund", "plan", "option", "the", "scheme", "mutual", "mf", "an", "open", "ended",
        "of", "and", "regular", "direct", "growth", "gr", "idcw", "dividend", "div",
        "payout", "reinvestment", "reinvest", "income", "distribution", "cum", "capital",
        "withdrawal", "formerly", "known", "as", "erstwhile", "reg", "dir", "g", "option",
        "options", "series", "sub", "plans", "na", "unit", "units"}

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
    return {w for w in norm(name).split() if w not in STOP and not re.fullmatch(r"\d+", w)}


def house_of(name):
    first = norm(name).split(" ")[0] if name else ""
    return HOUSE_ALIASES.get(first, first)


def parse_navall(text):
    """Return a list of AMFI schemes with code, isins, name, nav, date and fund house."""
    out, house = [], ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ";" not in line:
            if "mutual fund" in line.lower() or line.lower().endswith(" mf"):
                house = norm(line)
            continue
        parts = line.split(";")
        if len(parts) < 6 or not parts[0].strip().isdigit():
            continue
        code, isin1, isin2, name, nav, day = [p.strip() for p in parts[:6]]
        try:
            navf = float(nav)
        except ValueError:
            continue
        m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", day)
        if not m:
            continue
        months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
        iso = f"{m.group(3)}-{months.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}"
        out.append({"code": code, "isin": isin1 if isin1 not in ("", "-") else isin2,
                    "name": name, "nav": navf, "date": iso, "house": house,
                    "plan": plan_of(name), "option": option_of(name), "tokens": tokens(name)})
    return out


def best_match(product, schemes):
    name, divopt = product["scheme_name"] or "", product.get("divopt")
    want_plan, want_opt = plan_of(name), option_of(name, divopt)
    want_tokens = tokens(name)
    house = house_of(name)
    if not want_tokens:
        return None, 0.0
    scored = []
    for s in schemes:
        if s["plan"] != want_plan or s["option"] != want_opt:
            continue
        if house and not (re.search(rf"\b{re.escape(house)}\b", s["house"])
                          or re.search(rf"\b{re.escape(house)}\b", norm(s["name"]))):
            continue
        inter = len(want_tokens & s["tokens"])
        if not inter:
            continue
        # Share of the product's words found in AMFI's name, lightly penalising extra words.
        score = inter / len(want_tokens) - 0.05 * len(s["tokens"] - want_tokens)
        scored.append((score, s))
    if not scored:
        return None, 0.0
    scored.sort(key=lambda x: -x[0])
    top_score, top = scored[0]
    if len(scored) > 1 and scored[1][0] == top_score and scored[1][1]["nav"] != top["nav"]:
        return None, top_score            # tie between different schemes: leave for review
    return top, top_score


def download():
    last = None
    for url in AMFI_URLS:
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0 mf-crm"})
            if r.status_code == 200 and ";" in r.text[:5000]:
                return r.text
            last = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            last = str(exc)
    raise RuntimeError(f"Could not download AMFI NAVs: {last}")


def run(db, text=None):
    schemes = parse_navall(text or download())
    by_code = {s["code"]: s for s in schemes}
    products = db.select("nav_products", "select=*")
    manual = {(m["registrar"], m["product_code"]): m for m in db.select("scheme_map", "select=*")
              if m.get("method") == "manual"}
    maps, navs, unmatched = [], [], 0
    for p in products:
        key = (p["registrar"], p["product_code"])
        if key in manual:
            s = by_code.get(manual[key].get("amfi_code") or "")
            score, method = 1.0, "manual"
        else:
            s, score = best_match(p, schemes)
            method = "auto"
            if s is None or score < MIN_SCORE:
                unmatched += 1
                maps.append({"registrar": p["registrar"], "product_code": p["product_code"],
                             "scheme_name": p["scheme_name"], "amfi_code": None, "amfi_name": None,
                             "isin": None, "score": round(score, 3), "method": "unmatched"})
                continue
        if s is None:
            continue
        maps.append({"registrar": p["registrar"], "product_code": p["product_code"],
                     "scheme_name": p["scheme_name"], "amfi_code": s["code"], "amfi_name": s["name"],
                     "isin": s["isin"], "score": round(score, 3), "method": method})
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
