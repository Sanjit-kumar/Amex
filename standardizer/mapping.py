"""Standard Amex-style transaction schema + deterministic coercion of raw values."""
import re
from datetime import datetime

FIELDS = [
    "transaction_id", "merchant_name", "merchant_category", "transaction_date", "transaction_time",
    "amount", "currency", "card_network", "card_last4", "bank_name", "payment_method",
    "description", "reference",
]
REQUIRED = ["merchant_name", "transaction_date", "amount", "currency"]

SCHEMA_DOC = """transaction_id: string, merchant's transaction/receipt/invoice id
merchant_name: string
merchant_category: string (e.g. restaurant, hotel, retail, florist)
transaction_date: YYYY-MM-DD
transaction_time: HH:MM:SS (24h) or null
amount: number, positive for purchases, negative for refunds
currency: ISO 4217 code, e.g. USD
card_network: Visa | Mastercard | Amex | Discover | Other | null
card_last4: last 4 digits only, NEVER the full card number
bank_name: issuing bank or null
payment_method: credit | debit | cash | other | null
description: string or null
reference: auth code / order number or null"""

ALIASES = {
    "transaction_id": ["id", "transaction id", "txn id", "txn", "trans id", "transaction number", "receipt no",
                       "receipt number", "receipt", "invoice", "invoice no", "invoice number", "check no", "ticket"],
    "merchant_name": ["merchant", "merchant name", "vendor", "payee", "store", "business", "business name",
                      "shop", "restaurant", "hotel", "seller", "establishment"],
    "merchant_category": ["category", "merchant category", "mcc", "industry", "type of business", "business type"],
    "transaction_date": ["date", "transaction date", "txn date", "trans date", "purchase date", "posting date",
                         "datetime", "date time", "timestamp", "paid on"],
    "transaction_time": ["time", "transaction time", "txn time"],
    "amount": ["amount", "total", "transaction amount", "txn amount", "amt", "grand total", "charge", "price",
               "sale amount", "paid", "amount paid"],
    "currency": ["currency", "ccy", "curr", "currency code"],
    "card_network": ["card type", "card network", "network", "card brand", "brand", "scheme"],
    "card_raw": ["card", "card number", "card no", "pan", "card last4", "last4", "last 4", "last four",
                 "card ending", "account number", "card last 4"],
    "bank_name": ["bank", "bank name", "issuer", "issuing bank"],
    "payment_method": ["payment method", "payment type", "method", "tender", "tender type", "pay type"],
    "description": ["description", "memo", "notes", "details", "item", "items", "narrative"],
    "reference": ["reference", "ref", "auth", "auth code", "authorization", "authorization code", "order id",
                  "order number", "order no", "ref no"],
}


def norm_key(k) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


ALIAS_MAP = {norm_key(a): f for f, al in ALIASES.items() for a in al + [f.replace("_", " ")]}


def map_loose(record: dict) -> tuple[dict, dict, dict]:
    """Map a free-form record's keys onto the standard fields. Returns (data, confidence, source)."""
    data, conf, source = {}, {}, {}
    for k, v in record.items():
        f = ALIAS_MAP.get(norm_key(k))
        if not f or v is None or str(v).strip() == "" or f in data:
            continue
        data[f] = v
        conf[f] = 0.95
        source[f] = str(v)
    return data, conf, source


def recognises(headers) -> bool:
    mapped = {ALIAS_MAP.get(norm_key(h)) for h in headers} - {None}
    return len(mapped) >= 3 and "amount" in mapped and ("transaction_date" in mapped or "merchant_name" in mapped)


# ---------- value coercion ----------
_DATE_FORMATS_US = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%Y%m%d"]
_DATE_FORMATS_DMY = ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"]
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?")
_CCY = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY"}
_SYM_RE = re.compile("[$£€₹¥]")


def _luhn(digits: str) -> bool:
    s, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        s += d
        alt = not alt
    return s % 10 == 0


def _network_from_pan(p: str) -> str | None:
    if re.match(r"^3[47]", p): return "Amex"
    if p.startswith("4"): return "Visa"
    if re.match(r"^(5[1-5]|2[2-7])", p): return "Mastercard"
    if re.match(r"^(6011|65|64[4-9])", p): return "Discover"
    return None


def parse_time(s) -> str | None:
    m = _TIME_RE.search(str(s))
    if not m:
        return None
    h, mi, sec, ap = int(m[1]), int(m[2]), int(m[3] or 0), (m[4] or "").lower()
    if ap == "pm" and h < 12: h += 12
    if ap == "am" and h == 12: h = 0
    if h > 23 or mi > 59 or sec > 59:
        return None
    return f"{h:02d}:{mi:02d}:{sec:02d}"


def parse_date(s):
    """Return (iso_date|None, ambiguous: bool)."""
    if isinstance(s, datetime):
        return s.date().isoformat(), False
    txt = str(s).strip()
    txt = re.split(r"[T ]\d{1,2}:\d{2}", txt)[0].strip().rstrip(",")
    for fmt in _DATE_FORMATS_US:
        try:
            d = datetime.strptime(txt, fmt)
        except ValueError:
            continue
        ambiguous = fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y") and d.day <= 12 and d.month != d.day
        return d.date().isoformat(), ambiguous
    for fmt in _DATE_FORMATS_DMY:
        try:
            return datetime.strptime(txt, fmt).date().isoformat(), True
        except ValueError:
            continue
    return None, False


def parse_amount(s):
    if isinstance(s, (int, float)):
        return round(float(s), 2)
    t = str(s).strip()
    neg = (t.startswith("(") and t.endswith(")")) or t.startswith("-") or bool(re.search(r"\b(CR|refund)\b", t, re.I))
    t = re.sub(r"[^\d.,]", "", t)
    if t.count(",") and t.count(".") == 0 and re.search(r",\d{2}$", t):
        t = t.replace(",", ".")  # European decimal comma
    t = t.replace(",", "")
    try:
        v = round(float(t), 2)
    except ValueError:
        return None
    return -v if neg else v


def coerce(data: dict, conf: dict, source: dict) -> tuple[dict, dict, dict, list[dict]]:
    """Normalise raw values to the standard formats. Returns (data, conf, source, notes)."""
    d, c, s, notes = {}, dict(conf), dict(source), []
    raw = dict(data)

    if "card_raw" in raw:
        val = str(raw.pop("card_raw"))
        digits = re.sub(r"\D", "", val)
        if len(digits) >= 13:
            if not _luhn(digits):
                notes.append({"field": "card_last4", "severity": "warn", "msg": "Full card number failed Luhn check"})
            notes.append({"field": "card_last4", "severity": "info", "msg": "Full card number in source - only last 4 kept"})
            raw.setdefault("card_network", _network_from_pan(digits))
            raw["card_last4"] = digits[-4:]
        elif len(digits) >= 4:
            raw["card_last4"] = digits[-4:]
        low = val.lower()
        if not raw.get("card_network"):
            for name, net in (("amex", "Amex"), ("american express", "Amex"), ("visa", "Visa"),
                              ("mastercard", "Mastercard"), ("discover", "Discover")):
                if name in low:
                    raw["card_network"] = net
                    break
        c["card_last4"] = c.get("card_raw", 0.9)
        s["card_last4"] = s.get("card_raw", val)

    for f in FIELDS:
        v = raw.get(f)
        if v is None or str(v).strip() == "" or str(v).lower() == "null":
            continue
        if f == "transaction_date":
            iso, amb = parse_date(v)
            if iso:
                d[f] = iso
                if amb:
                    notes.append({"field": f, "severity": "warn", "msg": "Ambiguous day/month order - verify against source"})
                    c[f] = min(c.get(f, 1), 0.6)
                if "transaction_time" not in raw and _TIME_RE.search(str(v)):
                    t = parse_time(str(v))
                    if t:
                        d["transaction_time"] = t
                        c["transaction_time"] = c.get(f, 0.9)
            else:
                d[f] = str(v)  # keep as-is so a human can fix it; validator flags it
                c[f] = 0.0
        elif f == "transaction_time":
            d[f] = parse_time(v) or str(v)
        elif f == "amount":
            a = parse_amount(v)
            d[f] = a if a is not None else str(v)
            if a is None:
                c[f] = 0.0
            sym = _SYM_RE.search(str(v))
            if "currency" not in raw and sym:
                d["currency"] = _CCY[sym[0]]
                c["currency"] = 0.8
                s["currency"] = sym[0]
        elif f == "currency":
            cv = str(v).strip().upper()
            d[f] = _CCY.get(str(v).strip(), cv)
        elif f == "card_last4":
            digits = re.sub(r"\D", "", str(v))
            d[f] = digits[-4:] if digits else str(v)
        elif f == "card_network":
            low = str(v).lower()
            d[f] = ("Amex" if "amex" in low or "american" in low else "Visa" if "visa" in low
                    else "Mastercard" if "master" in low else "Discover" if "discover" in low else str(v))
        elif f == "payment_method":
            low = str(v).lower()
            d[f] = "credit" if "credit" in low else "debit" if "debit" in low else "cash" if "cash" in low else low
        else:
            d[f] = str(v).strip()
    for f in FIELDS:
        d.setdefault(f, None)
    return d, c, s, notes
