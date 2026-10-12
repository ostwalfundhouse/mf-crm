"""Units held and cost value per folio and scheme, from transactions.

Units are the plain sum of signed units, which is what the registrar reports.
Cost value uses first-in-first-out: redemptions and switch-outs use up the
oldest purchases first, as on a CAMS consolidated statement. A reversed
purchase removes the matching purchase rather than the oldest one.
"""
from collections import defaultdict

EPS = 1e-6


def _f(v):
    return float(v) if v not in (None, "") else 0.0


def compute(transactions):
    """transactions: dicts with registrar, folio_no, product_code, trxn_date,
    signed_units, amount, price, is_reversal, arn. Returns holdings rows."""
    groups = defaultdict(list)
    for t in transactions:
        groups[(t["registrar"], t["folio_no"], t["product_code"])].append(t)

    out = []
    for (registrar, folio, product), txns in groups.items():
        def order(t):
            su = _f(t.get("signed_units"))
            rev = bool(t.get("is_reversal"))
            # Same day: purchases first, then outflows, then reversals.
            return (str(t.get("trxn_date") or ""), 2 if rev else (0 if su > 0 else 1),
                    t.get("id") or 0)

        txns = sorted(txns, key=order)
        lots = []                      # [units, cost]
        units_total = 0.0
        invested = withdrawn = 0.0
        first_date = last_date = None
        arn_latest = None

        for t in txns:
            su = _f(t.get("signed_units"))
            if abs(su) < EPS:
                continue
            amt = abs(_f(t.get("amount")))
            price = _f(t.get("price"))
            rev = bool(t.get("is_reversal"))
            units_total += su
            d = t.get("trxn_date")
            if d:
                first_date = first_date or d
                last_date = d
            if t.get("arn"):
                arn_latest = t["arn"]

            if su > 0 and not rev:
                cost = amt if amt > 0 else su * price
                lots.append([su, cost])
                invested += cost
            elif su < 0 and rev:
                # A purchase was reversed: remove that purchase.
                need = -su
                match = next((l for l in reversed(lots) if abs(l[0] - need) < 1e-3), None)
                if match is not None:
                    invested -= match[1]
                    lots.remove(match)
                else:
                    invested -= _take(lots, need, newest_first=True)
            elif su < 0:
                withdrawn += amt
                _take(lots, -su, newest_first=False)
            else:
                # A redemption was reversed: put the units back at their value.
                cost = amt if amt > 0 else su * price
                lots.append([su, cost])
                withdrawn -= amt

        lot_units = sum(l[0] for l in lots)
        lot_cost = sum(l[1] for l in lots)
        units = round(units_total, 4)
        if abs(units) < 1e-3:
            units, cost_value = 0.0, 0.0
        elif lot_units > EPS:
            cost_value = lot_cost * (units / lot_units) if abs(lot_units - units) > 1e-3 else lot_cost
        else:
            cost_value = 0.0
        out.append({
            "registrar": registrar, "folio_no": folio, "product_code": product,
            "units": units, "cost_value": round(max(cost_value, 0.0), 2),
            "invested_total": round(invested, 2), "withdrawn_total": round(withdrawn, 2),
            "first_txn_date": first_date, "last_txn_date": last_date,
            "arn_latest": arn_latest,
        })
    return out


def _take(lots, units, newest_first):
    """Remove units from lots; return the cost removed."""
    removed = 0.0
    order = range(len(lots) - 1, -1, -1) if newest_first else range(len(lots))
    for i in list(order):
        if units <= EPS:
            break
        lot = lots[i]
        use = min(lot[0], units)
        part = lot[1] * (use / lot[0]) if lot[0] > EPS else 0.0
        lot[0] -= use
        lot[1] -= part
        removed += part
        units -= use
    lots[:] = [l for l in lots if l[0] > EPS]
    return removed
