import json
import re
import threading
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

BASE = ("https://exam.sanand.workers.dev/questionData?email=24f3004942%40ds.study.iitm.ac.in"
        "&quizSign=LJQ%2BnRzEyCty60okLULLHMQ6lDt8V1vgnaiUaARPvC8KxCyJCd%2F0j%2Fz8Zh8P2TKLYet%2BvnRxO2gHi1ZQuiNOvp7jUzDBJms9DUvOngipuO%2Brc40aRA3uBisGy%2FTNfOVCViITJZVKf8%2F4%2FTIFgqQ9orQSXBu5wp4OU%2Ft8sDdT43B2AmwM09CxrnRXeRD9uCZwNbrjlav4KI5jxWbNrP2YXfn%2FmfkvyLflc%2BOAN%2Fevj8DV3VFYxrqbdZHmE%2BVWzMtpwGjKN6EzEcjaD9kMPhhpqPXQh1R%2Fy%2Bbrgqqak2Sm8RZO4wd%2BuIgbrRqpExb35iu0uGBX8rUYkdACNAy%2F8oQvIw%3D%3D"
        "&questionId=q-ledger-agent-server")
IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_CACHE = {}
_LOCK = threading.Lock()


def _get(path):
    req = urllib.request.Request(f"{BASE}&path={path}", headers={"User-Agent": "curl/8.5.0", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.read().decode()


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load():
    with _LOCK:
        if "rows" in _CACHE:
            return _CACHE
        rates = json.loads(_get("%2Frates"))["usd_per_unit"]
        raw = [json.loads(l) for l in _get("%2Fexport").splitlines() if l.strip()]
        latest = {}
        for r in raw:
            u = parse_ts(r["updated_at"])
            if r["id"] not in latest or u > latest[r["id"]][0]:
                latest[r["id"]] = (u, r)
        rows = []
        for _, r in latest.values():
            d = parse_ts(r["created_at"]).astimezone(IST)
            rows.append({**r, "date": d.date(), "usd": float(r["amount"]) * float(rates[r["currency"]])})
        _CACHE.update(rows=rows, rates=rates,
                      regions=sorted({r["region"] for r in rows}),
                      products=sorted({r["product"] for r in rows}, key=len, reverse=True),
                      customers={r["customer"] for r in rows})
        return _CACHE


MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
for k, v in list(MONTHS.items()):
    MONTHS[k[:3]] = v
MONTHS["sept"] = 9


def month_end(y, m):
    return (datetime(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).date()


def parse_dates(q):
    """Return (start, end) inclusive dates or None."""
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", q)]
    year = years[0] if years else 2026

    # explicit ISO dates
    iso = re.findall(r"\b(20\d\d)-(\d\d)-(\d\d)\b", q)
    if len(iso) >= 2:
        a = datetime(*map(int, iso[0])).date(); b = datetime(*map(int, iso[1])).date()
        return min(a, b), max(a, b)
    if len(iso) == 1:
        a = datetime(*map(int, iso[0])).date()
        return a, a

    m = re.search(r"\bq([1-4])\b|\b(first|second|third|fourth)\s+quarter\b", q)
    if m:
        qn = int(m.group(1)) if m.group(1) else ["first", "second", "third", "fourth"].index(m.group(2)) + 1
        return datetime(year, 3 * qn - 2, 1).date(), month_end(year, 3 * qn)
    if re.search(r"\b(first half|h1)\b", q):
        return datetime(year, 1, 1).date(), month_end(year, 6)
    if re.search(r"\b(second half|h2)\b", q):
        return datetime(year, 7, 1).date(), month_end(year, 12)

    found = []
    for w in re.findall(r"[a-z]+", q):
        if w in MONTHS:
            found.append(MONTHS[w])
    # numeric month like 2026-03 or 03/2026
    for y, mo in re.findall(r"\b(20\d\d)[-/](\d{1,2})\b(?!-\d)", q):
        found.append(int(mo)); year = int(y)
    for mo, y in re.findall(r"\b(\d{1,2})/(20\d\d)\b", q):
        found.append(int(mo)); year = int(y)
    found = [f for f in found if 1 <= f <= 12]
    if found:
        lo, hi = min(found), max(found)
        if re.search(r"\b(between|from|through|to|until|thru)\b", q) or len(set(found)) == 1:
            return datetime(year, lo, 1).date(), month_end(year, hi)
        return ("set", year, set(found))
    if years:
        return datetime(year, 1, 1).date(), datetime(year, 12, 31).date()
    return None


def in_range(d, rng):
    if rng is None:
        return True
    if rng[0] == "set":
        return d.year == rng[1] and d.month in rng[2]
    return rng[0] <= d <= rng[1]


def answer(question):
    c = load()
    q = question.lower().strip()

    region = next((r for r in c["regions"] if re.search(rf"\b{r.lower()}\b", q)), None)
    product = next((p for p in c["products"] if p.lower() in q or p.lower().replace(" ", "") in q.replace(" ", "")), None)
    cust_m = re.search(r"\b(c\d{3,})\b", q)
    customer = cust_m.group(1).upper() if cust_m else None
    cur_m = re.search(r"\b(paid|priced|billed|charged|denominated|in)\s+(eur|euros?|inr|rupees?|usd|dollars?)\b", q)
    currency_filter = None
    if cur_m and not re.search(r"\b(in|to)\s+(usd|dollars?)\b\s*(\?|$|from|for|of|in|during)", q):
        t = cur_m.group(2)
        currency_filter = "EUR" if t.startswith("eur") else "INR" if t.startswith(("inr", "rupee")) else "USD"
        if currency_filter == "USD" and cur_m.group(1) == "in":
            currency_filter = None
    m = re.search(r"\b(eur|inr)\s+(orders|sales|transactions)\b|\b(orders|sales|transactions)\s+in\s+(eur|inr)\b", q)
    if m:
        currency_filter = (m.group(1) or m.group(4)).upper()
    rng = parse_dates(q)

    # which statuses
    if re.search(r"\brefund", q):
        statuses = {"refunded"}
    elif re.search(r"\bvoid", q):
        statuses = {"void"}
    elif re.search(r"\b(all orders|any status|regardless of status|including (refund|void))", q):
        statuses = {"paid", "refunded", "void"}
    elif re.search(r"\b(how many|number of|count)\b.*\borders?\b", q) and not re.search(r"\b(paid|revenue|sales|sold|completed|successful)\b", q):
        statuses = {"paid", "refunded", "void"}
    else:
        statuses = {"paid"}

    rows = [r for r in c["rows"]
            if r["status"] in statuses
            and (region is None or r["region"] == region)
            and (product is None or r["product"] == product)
            and (customer is None or r["customer"] == customer)
            and (currency_filter is None or r["currency"] == currency_filter)
            and in_range(r["date"], rng)]

    def money(x):
        return round(x + 1e-9, 2)

    # group-by / ranking questions
    rank = re.search(r"\b(which|what|who|top|best|most|highest|largest|biggest|leading|least|lowest|fewest|smallest|worst)\b", q)
    group = None
    if re.search(r"\bwhich (product|item)|\bwhat (product|item)|\b(top|best|most|least)[- ]selling\b|\bproduct (had|has|with|generated|brought|earned|made|sold)", q):
        group = "product"
    elif re.search(r"\b(which|what|who|top) (customer|client|buyer)|\bcustomer (had|has|with|spent|generated|placed|bought)", q):
        group = "customer"
    elif re.search(r"\b(which|what) region|\bregion (had|has|with|generated|earned)", q):
        group = "region"
    lowest = bool(re.search(r"\b(least|lowest|fewest|smallest|worst|minimum|min)\b", q))

    wants_units = bool(re.search(r"\b(units|quantity|qty|pieces|items sold)\b", q))
    wants_count = bool(re.search(r"\b(how many|number of|count of|count)\b", q)) and not wants_units
    wants_avg = bool(re.search(r"\b(average|avg|mean)\b", q))
    wants_customers = bool(re.search(r"\b(unique|distinct|different)?\s*customers?\b", q)) and wants_count and group is None

    def metric(rs):
        if wants_units:
            return sum(r["qty"] for r in rs)
        if wants_count:
            return len(rs)
        if wants_avg:
            return sum(r["usd"] for r in rs) / len(rs) if rs else 0.0
        return sum(r["usd"] for r in rs)

    if group and rank:
        buckets = defaultdict(list)
        for r in rows:
            buckets[r[group]].append(r)
        if not buckets:
            return None
        scored = {k: metric(v) for k, v in buckets.items()}
        best = (min if lowest else max)(scored, key=lambda k: (scored[k], k if lowest else ""))
        return best

    if wants_customers:
        return len({r["customer"] for r in rows})
    if wants_units:
        return sum(r["qty"] for r in rows)
    if wants_count:
        return len(rows)
    if wants_avg:
        return money(sum(r["usd"] for r in rows) / len(rows)) if rows else 0
    return money(sum(r["usd"] for r in rows))


@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/")
@app.post("/ask")
@app.post("/answer")
async def ask(request: Request):
    try:
        body = await request.json()
        question = str(body.get("question", ""))
    except Exception:
        return {"answer": None}
    try:
        return {"answer": answer(question)}
    except Exception as e:
        return {"answer": None, "error": str(e)}
