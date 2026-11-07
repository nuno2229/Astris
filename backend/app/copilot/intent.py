"""Understanding a request (E15). Rules first-class (offline, no model); the local LLM, when the household has one,
only fills the same small structured Intent. Statement text is never sent to the model here."""
import json
import re
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field

from app.categorise.rules import CATEGORIES

TOOLS = ["spending", "balance", "low_stock", "expiring", "stock_lookup", "bills", "budgets", "goals", "by_member",
         "top_shops", "set_budget", "pantry_add", "pantry_use", "goal_contribute", "list_add", "list_show", "help"]
PERIODS = ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_year"]

SYNONYMS = {
    "food shop": "Groceries", "food shopping": "Groceries", "supermarket": "Groceries", "grocery": "Groceries",
    "takeaway": "Eating out", "takeaways": "Eating out", "restaurant": "Eating out", "restaurants": "Eating out",
    "coffee": "Eating out", "eating": "Eating out", "petrol": "Transport", "fuel": "Transport", "train": "Transport",
    "trains": "Transport", "bus": "Transport", "taxi": "Transport", "rent": "Housing", "mortgage": "Housing",
    "energy": "Bills", "electric": "Bills", "electricity": "Bills", "gas": "Bills", "water": "Bills", "phone": "Bills",
    "internet": "Bills", "broadband": "Bills", "streaming": "Subscriptions", "clothes": "Shopping", "holiday": "Travel",
    "holidays": "Travel", "pets": "Pets", "kids": "Kids", "children": "Kids", "gifts": "Gifts & donations",
    "salary": "Income", "pay": "Income", "wages": "Income", "fees": "Fees & charges",
}


@dataclass
class Intent:
    tool: str = "help"
    category: str | None = None
    merchant: str | None = None
    period: str = "this_month"
    account: str | None = None
    product: str | None = None
    quantity: float | None = None
    amount: float | None = None
    goal: str | None = None
    member: str | None = None
    budget_period: str = "monthly"
    source: str = "rules"
    extra: dict = field(default_factory=dict)


# the household's own categories (G-13) for the request being handled; set by the copilot router
EXTRA_CATEGORIES: ContextVar[tuple[str, ...]] = ContextVar("extra_categories", default=())


def all_categories() -> list[str]:
    return CATEGORIES + [c for c in EXTRA_CATEGORIES.get() if c not in CATEGORIES]


def _category(t: str) -> str | None:
    for c in sorted(all_categories(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(c.lower())}\b", t):
            return c
    for k, v in sorted(SYNONYMS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(k)}\b", t):
            return v
    return None


def _period(t: str) -> str:
    for p in PERIODS:
        if p.replace("_", " ") in t:
            return p
    if "this year" in t or "so far this year" in t:
        return "this_year"
    if "week" in t:
        return "this_week"
    return "this_month"


def _amount(t: str) -> float | None:
    m = re.search(r"[£€$]\s?(\d{1,7}(?:[.,]\d{1,2})?)", t) or re.search(r"\b(\d{1,7}(?:\.\d{1,2})?)\s?(?:pounds|quid|gbp|euros?)\b", t)
    return float(m[1].replace(",", ".")) if m else None


def _qty_and_name(s: str) -> tuple[float, str]:
    s = s.strip(" .!?")
    m = re.match(r"^(?:(\d+(?:\.\d+)?)|an?|one|two|three|four|five|six)\s+(.+)$", s)
    words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    if m:
        first = s.split()[0]
        q = float(m[1]) if m[1] else words.get(first, 1)
        name = m[2]
    else:
        q, name = 1.0, s
    name = re.sub(r"^(of\s+)?(the|some|more)\s+", "", name)
    name = re.sub(r"\s+(cartons?|bottles?|packs?|boxes?|tins?|cans?|bags?|loaves|loaf|jars?)\s+of\s+", " ", " " + name).strip()
    name = re.sub(r"^(cartons?|bottles?|packs?|boxes?|tins?|cans?|bags?|jars?)\s+of\s+", "", name)
    return q, name.strip()


def rules(text: str) -> Intent:
    t = " " + re.sub(r"\s+", " ", text.lower()).strip() + " "
    it = Intent(period=_period(t), category=_category(t), amount=_amount(t))
    # ---- actions ----
    m = re.search(r"\b(?:add|put|we need|need)\s+(.+?)\s+(?:to|on)\s+(?:the\s+|my\s+|our\s+)?(?:shopping\s+)?list\b", t) \
        or re.search(r"\b(?:we need|we're out of|we are out of|need to buy)\s+(.+?)\s*$", t)
    if m:
        it.tool = "list_add"
        it.quantity, it.product = _qty_and_name(m[1])
        return it
    if re.search(r"\b(shopping list|what'?s on the list|the list)\b", t):
        it.tool = "list_show"
        return it
    if re.search(r"\b(set|make|create|change|put)\b.*\bbudget\b", t) and it.amount:
        it.tool = "set_budget"
        it.budget_period = "weekly" if "week" in t else "daily" if re.search(r"\b(day|daily)\b", t) else "monthly"
        return it
    m = re.search(r"\b(?:put|add|save|saved|move|moved)\s+[£€$]?\s?\d[\d.,]*\s*(?:pounds|quid)?\s+(?:towards|to|into|in|for)\s+(?:the\s+|our\s+|my\s+)?(.+?)(?:\s+goal|\s+pot|\s+fund)?\s*$", t)
    if m and it.amount:
        it.tool, it.goal = "goal_contribute", m[1].strip(" .!?")
        return it
    m = re.search(r"\b(?:add|added|bought|got|put|stock up on)\s+(.+?)\s+(?:to|in|into)\s+(?:the\s+)?(?:pantry|stock|fridge|freezer|cupboard)", t) \
        or re.search(r"\b(?:i|we)\s+(?:bought|got)\s+(.+?)\s*$", t)
    if m:
        it.tool = "pantry_add"
        it.quantity, it.product = _qty_and_name(m[1])
        return it
    m = re.search(r"\b(?:used|use up|finished|ate|drank|opened|used up)\s+(.+?)\s*$", t)
    if m and not re.search(r"\b(how|what|when)\b", t):
        it.tool = "pantry_use"
        it.quantity, it.product = _qty_and_name(m[1])
        return it
    # ---- questions ----
    if re.search(r"\b(balance|balances|how much (?:money )?(?:do|have) (?:i|we) (?:have|got))\b", t):
        it.tool = "balance"
    elif re.search(r"\b(expir|use by|use-by|go(?:es|ing)? off|out of date)", t):
        it.tool = "expiring"
    elif re.search(r"\b(running low|run(?:ning)? out|low on|need to buy|what'?s low|low stock)\b", t):
        it.tool = "low_stock"
    elif m := re.search(r"\b(?:do|have)\s+(?:we|i)\s+(?:have|got)\s+(?:any\s+)?(.+?)\s*\??\s*$", t):
        it.tool, it.product = "stock_lookup", m[1].strip(" ?")
    elif re.search(r"\b(bills?|due|renew\w*|subscriptions?|direct debits?|standing orders?)\b", t):
        it.tool = "bills"
    elif re.search(r"\bbudgets?\b", t):
        it.tool = "budgets"
    elif re.search(r"\b(goals?|saving for|savings)\b", t):
        it.tool = "goals"
    elif re.search(r"\bwho\s+(?:spent|paid|spends|pays)\b|\bby (?:person|member)\b", t):
        it.tool = "by_member"
    elif re.search(r"\b(top|biggest|most)\b.*\b(shops?|stores?|merchants?|places)\b|\bwhere (?:did|does) (?:the |our |my )?money go\b", t):
        it.tool = "top_shops"
    elif re.search(r"\b(spend|spent|spending|cost|paid|pay|how much)\b", t) or it.category:
        it.tool = "spending"
        if not it.category:
            m = re.search(r"\b(?:at|on|in)\s+([a-z0-9&' .-]{2,40}?)\s+(?:this|last|today|yesterday|so far|in|$)", t + " ")
            if m and m[1].strip() not in ("the", "a", "total", "all"):
                it.merchant = m[1].strip()
    return it


def llm_schema() -> dict:
    return {"type": "object", "properties": {
        "tool": {"type": "string", "enum": TOOLS},
        "category": {"type": ["string", "null"], "enum": all_categories() + [None]},
        "merchant": {"type": ["string", "null"], "maxLength": 60},
        "period": {"type": "string", "enum": PERIODS},
        "account": {"type": ["string", "null"], "maxLength": 60},
        "product": {"type": ["string", "null"], "maxLength": 80},
        "quantity": {"type": ["number", "null"], "minimum": 0, "maximum": 1000},
        "amount": {"type": ["number", "null"], "minimum": 0, "maximum": 10000000},
        "goal": {"type": ["string", "null"], "maxLength": 80},
        "member": {"type": ["string", "null"], "maxLength": 60},
        "budget_period": {"type": "string", "enum": ["daily", "weekly", "monthly"]}},
        "required": ["tool", "period"]}


SYSTEM = (
    "You map a household finance app user's request to ONE tool call. Reply only with JSON matching the schema.\n"
    "Tools: spending (how much was spent; category/merchant/member/period), balance (account balances), low_stock, "
    "expiring (pantry use-by), stock_lookup (do we have <product>), bills (upcoming bills & subscription renewals), "
    "budgets, goals, by_member (who spent what), top_shops, set_budget (category + amount + budget_period), "
    "pantry_add (product + quantity), pantry_use (product + quantity), goal_contribute (goal + amount), "
    "list_add (add product + quantity to the shopping list), list_show (what's on the shopping list), help.\nNever invent numbers that the user didn't say. If unsure, use help."
)


def from_llm(text: str, base_url: str, model: str | None, allow_internet: bool) -> Intent | None:
    """Ask the household's local model for an Intent. Any failure or invalid output -> None (rules are used)."""
    from app.netcheck import is_local_url, origin_only, pinned_request
    base = origin_only(base_url or "")
    if not base or (not allow_internet and not is_local_url(base)):
        return None
    body = {"model": model or "default", "temperature": 0, "max_tokens": 200,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text[:500]}],
            "response_format": {"type": "json_object", "schema": llm_schema()}}
    try:
        code, raw = pinned_request(base + "/v1/chat/completions", "POST", json.dumps(body).encode(), timeout=20,
                                   max_bytes=64 * 1024, headers={"Content-Type": "application/json"})
        if code != 200:
            return None
        d = json.loads(json.loads(raw)["choices"][0]["message"]["content"])
    except Exception:
        return None
    if d.get("tool") not in TOOLS:
        return None
    it = Intent(source="ai")
    for k in ("category", "merchant", "account", "product", "goal", "member"):
        v = d.get(k)
        setattr(it, k, v.strip()[:80] if isinstance(v, str) and v.strip() else None)
    if it.category not in all_categories():
        it.category = None
    it.tool = d["tool"]
    it.period = d.get("period") if d.get("period") in PERIODS else "this_month"
    it.budget_period = d.get("budget_period") if d.get("budget_period") in ("daily", "weekly", "monthly") else "monthly"
    for k in ("quantity", "amount"):
        v = d.get(k)
        setattr(it, k, float(v) if isinstance(v, (int, float)) and 0 <= v <= 1e7 else None)
    return it


def as_dict(it: Intent) -> dict:
    return asdict(it)
