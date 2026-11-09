"""What kind of thing a receipt line is (P-16), for splitting a supermarket payment by what was bought.
Food stays Groceries; the usual non-food aisles get their own category. Built-in categories only."""
import re

_KINDS = [
    ("Pets", r"\b(cat|dog|kitten|puppy)\s*(food|treats?|litter|biscuits)|\bpet\b|whiskas|felix|pedigree|bakers|dreamies|cat litter"),
    ("Kids", r"\bnapp(y|ies)\b|baby wipes|pampers|huggies|\bformula\b|calpol|baby food"),
    ("Health", r"paracetamol|ibuprofen|aspirin|plasters?\b|vitamins?|antihistamine|cold\s*&?\s*flu|lemsip|strepsils|gaviscon|rennie|first aid|nurofen"),
    ("Personal care", r"shampoo|conditioner|tooth\s*paste|toothbrush|mouthwash|deodorant|anti.?perspirant|shower gel|body wash|\bsoap\b|"
                      r"hand wash|razor|shaving|tampons?|sanitary|\bpads\b|moisturi[sz]er|sun\s*cream|cotton (wool|buds)|lip balm|hair (dye|gel|spray)|make.?up"),
    ("Shopping", r"toilet (roll|tissue)|kitchen roll|tissues|bin (bags|liners)|washing.?up|dishwasher|laundry|detergent|fabric conditioner|"
                 r"bleach|disinfectant|surface (spray|cleaner)|cleaner\b|sponges?|cloths?\b|foil\b|cling ?film|baking paper|freezer bags|"
                 r"batter(y|ies)|light ?bulb|candles?|air freshener|matches|charcoal|plant\b|flowers|card\b|gift ?wrap|socks|t-?shirt|clothing"),
    ("Entertainment", r"magazine|newspaper|\bdvd\b|lottery|scratchcard"),
    ("Fees & charges", r"carrier bag|bag charge|\bbags? for life\b"),
]
_COMPILED = [(c, re.compile(p, re.I)) for c, p in _KINDS]


def item_category(name: str, default: str = "Groceries") -> str:
    for cat, rx in _COMPILED:
        if rx.search(name or ""):
            return cat
    return default
