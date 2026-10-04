"""Deterministic validation. No LLM involved: these decide what a human must look at."""
import re
from datetime import date

from mapping import REQUIRED

LOW_CONF = 0.7


def validate(data: dict, conf: dict, notes: list[dict] | None = None, siblings: list[dict] | None = None) -> list[dict]:
    issues = list(notes or [])

    def add(field, sev, msg):
        issues.append({"field": field, "severity": sev, "msg": msg})

    for f in REQUIRED:
        if data.get(f) in (None, ""):
            add(f, "error", "Required field is missing")

    d = data.get("transaction_date")
    if d:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)):
            add("transaction_date", "error", "Date is not a recognised format (expected YYYY-MM-DD)")
        elif str(d) > date.today().isoformat():
            add("transaction_date", "warn", "Date is in the future")
        elif str(d) < "2000-01-01":
            add("transaction_date", "warn", "Date is before 2000")

    a = data.get("amount")
    if a not in (None, ""):
        if not isinstance(a, (int, float)):
            add("amount", "error", "Amount is not a number")
        elif a == 0:
            add("amount", "warn", "Amount is zero")
        elif abs(a) > 100000:
            add("amount", "warn", "Unusually large amount")

    cur = data.get("currency")
    if cur and not re.fullmatch(r"[A-Z]{3}", str(cur)):
        add("currency", "error", "Currency must be a 3-letter ISO code")

    l4 = data.get("card_last4")
    if not l4:
        add("card_last4", "warn", "Card last 4 digits missing")
    elif not re.fullmatch(r"\d{4}", str(l4)):
        add("card_last4", "error", "Card last 4 must be exactly 4 digits")

    t = data.get("transaction_time")
    if t and not re.fullmatch(r"\d{2}:\d{2}:\d{2}", str(t)):
        add("transaction_time", "warn", "Time is not HH:MM:SS")

    for f, c in (conf or {}).items():
        if data.get(f) not in (None, "") and c < LOW_CONF and not any(i["field"] == f for i in issues):
            add(f, "warn", f"Low extraction confidence ({c:.2f})")

    key = (data.get("merchant_name"), data.get("transaction_date"), data.get("amount"), data.get("card_last4"))
    if all(k not in (None, "") for k in key):
        if any((s.get("merchant_name"), s.get("transaction_date"), s.get("amount"), s.get("card_last4")) == key for s in (siblings or [])):
            add("transaction_id", "warn", "Possible duplicate of another record in this file")
    return issues
