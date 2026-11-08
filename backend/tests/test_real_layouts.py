"""Layouts from the PO's real banks (2026-11-08), rebuilt with made-up data: AMEX UK CSV export and Chase UK PDF text."""
from decimal import Decimal

from app.importers.csv_import import parse_csv
from app.importers.text_statement import parse_statement_lines
from tests.conftest import create_account, create_household, make_user

AMEX = ('Date,Description,Amount,Extended Details,Appears On Your Statement As,Address,Town/City,Postcode,Country,Reference,Category\n'
        '09/09/2026,PIZZA PLACE,-5.00,Offer credit,PIZZA PLACE,"1 HIGH ST\nFLOOR 2",LONDON,SW1A 1AA,UNITED KINGDOM,\'AT000000000000000000001\',Entertainment-Restaurants\n'
        '09/09/2026,PIZZA PLACE               LONDON,23.66,,PIZZA PLACE               LONDON,"1 HIGH ST\nFLOOR 2",LONDON,SW1A 1AA,UNITED KINGDOM,\'AT000000000000000000002\',Entertainment-Restaurants\n'
        '05/09/2026,ZORBLAX BISTRO,37.00,,ZORBLAX BISTRO,2 LOW RD,LONDON,N1 1AA,UNITED KINGDOM,\'AT000000000000000000003\',Entertainment-Restaurants\n'
        '05/09/2026,ZORBLAX BISTRO,37.00,,ZORBLAX BISTRO,2 LOW RD,LONDON,N1 1AA,UNITED KINGDOM,\'AT000000000000000000004\',Entertainment-Restaurants\n'
        '20/08/2026,PAYMENT RECEIVED - THANK YOU,-320.65,,,,,,,\'AT000000000000000000005\',\n').encode()


def test_amex_csv_signs_quotes_and_categories():
    r = parse_csv(AMEX)
    assert r.format == "csv-amex" and r.negated and r.warnings == []
    amounts = [t.amount for t in r.transactions]
    assert amounts == [Decimal("5.00"), Decimal("-23.66"), Decimal("-37.00"), Decimal("-37.00"), Decimal("320.65")]
    assert r.transactions[2].bank_category == "Entertainment-Restaurants"
    assert parse_csv(AMEX, negate=False).transactions[1].amount == Decimal("23.66")      # the wizard can override


def test_amex_import_categorises_from_hint_and_keeps_twin_charges():
    a = make_user("a")
    hid = create_household(a)
    acc = create_account(a, "Amex", type_="card")
    a.put(f"/api/households/{hid}/accounts/{acc}", json={"attribution": "include"})
    r = a.post(f"/api/accounts/{acc}/imports", files={"file": ("amex.csv", AMEX)})
    assert r.status_code == 201 and r.json()["rows_new"] == 5          # two identical £37 charges both kept
    t = [x for x in a.get(f"/api/accounts/{acc}/transactions").json() if x["description"] == "ZORBLAX BISTRO"]
    assert len(t) == 2 and t[0]["category"] == "Eating out" and not t[0]["needs_review"]


CHASE = ["Account summary", "Opening balance Money in Money out Closing balance",
         "01 Nov 2025 Opening balance £1,002.58",
         "03 Nov 2025 Octopus Energy -£137.28 £865.30",
         "10 Nov 2025 From Jane Doe +£50.00 £915.30",
         "20 Nov 2025 Acme Ltd Salary +£2,631.06 £3,546.36",
         "24 Nov 2025 To Bills pot -£1,000.00 £2,546.36",
         "30 Nov 2025 Closing balance £2,546.36"]


def test_chase_closing_balance_line_is_not_a_transaction():
    r = parse_statement_lines(CHASE, "pdf")
    assert [t.amount for t in r.transactions] == [Decimal("-137.28"), Decimal("50.00"), Decimal("2631.06"), Decimal("-1000.00")]
    assert r.transactions[-1].running_balance == Decimal("2546.36")
    assert not any("direction" in w for w in r.warnings)
