"""Rules-based categorisation (E05-02, E05-04). Works fully offline; the LLM only refines what this
can't place confidently. Category list is interim (gap G-13)."""
import difflib
import re
from dataclasses import dataclass
from decimal import Decimal

CATEGORIES = [
    "Groceries", "Eating out", "Transport", "Bills", "Housing", "Shopping", "Entertainment",
    "Subscriptions", "Health", "Personal care", "Travel", "Kids", "Pets", "Gifts & donations",
    "Cash", "Transfers", "Income", "Fees & charges", "Other",
]
REVIEW_BELOW = Decimal("0.70")   # confidence under this -> "needs a look"
CONFIDENT = Decimal("0.90")      # at/above this the AI pass is skipped

# alias (matched against cleaned description tokens) -> (display name, category)
MERCHANTS: dict[str, tuple[str, str]] = {
    # groceries
    "tesco": ("Tesco", "Groceries"), "sainsbury": ("Sainsbury's", "Groceries"), "sainsburys": ("Sainsbury's", "Groceries"),
    "asda": ("Asda", "Groceries"), "aldi": ("Aldi", "Groceries"), "lidl": ("Lidl", "Groceries"),
    "morrisons": ("Morrisons", "Groceries"), "waitrose": ("Waitrose", "Groceries"), "ocado": ("Ocado", "Groceries"),
    "co-op": ("Co-op", "Groceries"), "coop": ("Co-op", "Groceries"), "iceland": ("Iceland", "Groceries"),
    "costco": ("Costco", "Groceries"), "m&s": ("M&S", "Groceries"), "marks": ("M&S", "Groceries"),
    # eating out
    "costa": ("Costa", "Eating out"), "pret": ("Pret", "Eating out"), "starbucks": ("Starbucks", "Eating out"),
    "greggs": ("Greggs", "Eating out"), "mcdonalds": ("McDonald's", "Eating out"), "nandos": ("Nando's", "Eating out"),
    "deliveroo": ("Deliveroo", "Eating out"), "just eat": ("Just Eat", "Eating out"), "justeat": ("Just Eat", "Eating out"),
    "uber eats": ("Uber Eats", "Eating out"), "wagamama": ("Wagamama", "Eating out"), "kfc": ("KFC", "Eating out"),
    "dominos": ("Domino's", "Eating out"), "pizza express": ("Pizza Express", "Eating out"),
    # transport
    "tfl": ("TfL", "Transport"), "trainline": ("Trainline", "Transport"), "uber": ("Uber", "Transport"),
    "shell": ("Shell", "Transport"), "esso": ("Esso", "Transport"), "bp": ("BP", "Transport"),
    "national rail": ("National Rail", "Transport"), "ringgo": ("RingGo", "Transport"), "dvla": ("DVLA", "Transport"),
    # bills & housing
    "octopus": ("Octopus Energy", "Bills"), "british gas": ("British Gas", "Bills"), "edf": ("EDF", "Bills"),
    "eon": ("E.ON", "Bills"), "ovo": ("OVO", "Bills"), "thames water": ("Thames Water", "Bills"),
    "council tax": ("Council tax", "Bills"), "bt": ("BT", "Bills"), "virgin media": ("Virgin Media", "Bills"),
    "vodafone": ("Vodafone", "Bills"), "ee": ("EE", "Bills"), "o2": ("O2", "Bills"), "sky": ("Sky", "Bills"),
    "tv licence": ("TV Licence", "Bills"), "rent": ("Rent", "Housing"), "mortgage": ("Mortgage", "Housing"),
    # subscriptions & entertainment
    "netflix": ("Netflix", "Subscriptions"), "spotify": ("Spotify", "Subscriptions"),
    "disney": ("Disney+", "Subscriptions"), "apple.com": ("Apple", "Subscriptions"), "icloud": ("iCloud", "Subscriptions"),
    "amazon prime": ("Amazon Prime", "Subscriptions"), "youtube": ("YouTube", "Subscriptions"),
    "cinema": ("Cinema", "Entertainment"), "odeon": ("Odeon", "Entertainment"), "vue": ("Vue", "Entertainment"),
    "steam": ("Steam", "Entertainment"), "playstation": ("PlayStation", "Entertainment"),
    # shopping, health, care
    "amazon": ("Amazon", "Shopping"), "amzn": ("Amazon", "Shopping"), "argos": ("Argos", "Shopping"),
    "ikea": ("IKEA", "Shopping"), "john lewis": ("John Lewis", "Shopping"), "primark": ("Primark", "Shopping"),
    "ebay": ("eBay", "Shopping"), "boots": ("Boots", "Health"), "superdrug": ("Superdrug", "Personal care"),
    "pharmacy": ("Pharmacy", "Health"), "dentist": ("Dentist", "Health"), "gym": ("Gym", "Health"),
    "puregym": ("PureGym", "Health"), "barber": ("Barber", "Personal care"),
    "pets at home": ("Pets at Home", "Pets"), "vets": ("Vet", "Pets"),
    # travel, cash, fees
    "easyjet": ("easyJet", "Travel"), "ryanair": ("Ryanair", "Travel"), "british airways": ("British Airways", "Travel"),
    "airbnb": ("Airbnb", "Travel"), "booking.com": ("Booking.com", "Travel"), "premier inn": ("Premier Inn", "Travel"),
    "cash withdrawal": ("Cash", "Cash"), "atm": ("Cash", "Cash"), "link atm": ("Cash", "Cash"),
    "overdraft": ("Overdraft", "Fees & charges"), "interest charged": ("Interest", "Fees & charges"),
    "fee": ("Bank fee", "Fees & charges"),
}
INCOME_WORDS = ("salary", "payroll", "wages", "pension", "hmrc", "child benefit", "dividend", "interest paid")

_PREFIXES = re.compile(
    r"^(card payment to|card payment|contactless|pos|visa|debit card|dd|direct debit( to)?|so|standing order( to)?|"
    r"faster payment( to| from)?|bill payment( to)?|payment to|purchase)\b[\s:-]*", re.I)


def merchant_key(description: str) -> str:
    """Stable, human-ish key for a merchant: 'CARD PAYMENT TO TESCO STORES 2041 ON 01/11' -> 'tesco stores'."""
    s = description.lower()
    for _ in range(3):
        s = _PREFIXES.sub("", s).strip()
    s = s.split("*")[0]                                  # 'amzn mktp uk*ab12cd' -> 'amzn mktp uk'
    s = re.sub(r"\bon \d{1,2}[/\-.]\d{1,2}([/\-.]\d{2,4})?\b.*$", "", s)
    s = re.sub(r"[^a-z&.' ]+", " ", s)                  # drop digits, refs, punctuation
    s = re.sub(r"\b(ltd|plc|uk|gb|london|stores?|store|limited|www|com|co)\b", " ", s)
    toks = [t for t in s.split() if len(t) > 1 or t in ("o2",)]
    return " ".join(toks[:3])[:100] or description.lower().strip()[:100]


@dataclass
class RuleResult:
    merchant: str | None
    key: str
    category: str | None
    confidence: Decimal


def categorise(description: str, amount: Decimal) -> RuleResult:
    key = merchant_key(description)
    text_ = " " + re.sub(r"[\s*]+", " ", description.lower()) + " "
    if amount > 0 and any(w in text_ for w in INCOME_WORDS):
        return RuleResult(None, key, "Income", Decimal("0.92"))
    # 1) exact alias as a whole word/phrase in the cleaned key or the raw text
    for alias in sorted(MERCHANTS, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", key) or (
                len(alias) > 3 and re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text_)):
            name, cat = MERCHANTS[alias]
            if amount > 0 and cat not in ("Income", "Transfers"):
                return RuleResult(name, key, cat, Decimal("0.60"))  # money back from a shop: likely refund, check
            return RuleResult(name, key, cat, Decimal("0.93"))
    # 2) fuzzy: typos/truncations like 'sainsbry', 'morrison'
    single = [a for a in MERCHANTS if " " not in a and len(a) >= 4]
    best, best_ratio = None, 0.0
    for tok in key.split():
        if len(tok) < 4:
            continue
        for m in difflib.get_close_matches(tok, single, n=1, cutoff=0.8):
            r = difflib.SequenceMatcher(None, tok, m).ratio()
            if r > best_ratio:
                best, best_ratio = m, r
    if best:
        name, cat = MERCHANTS[best]
        conf = Decimal(str(round(0.55 + 0.35 * (best_ratio - 0.8) / 0.2, 3)))
        return RuleResult(name, key, cat, min(conf, Decimal("0.89")))
    return RuleResult(None, key, None, Decimal("0"))
