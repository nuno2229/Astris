from decimal import Decimal

from tests.conftest import create_account, create_household, invite_and_join, make_user, upload

UK_CSV = b"""Account statement,,,,
Generated 05/11/2026,,,,
Date,Description,Paid out,Paid in,Balance
01/11/2026,TESCO STORES 2041,12.50,,987.50
01/11/2026,COSTA COFFEE,3.20,,984.30
01/11/2026,COSTA COFFEE,3.20,,981.10
03/11/2026,ACME LTD SALARY,,"2,000.00","2,981.10"
garbage,,,,
"""

OFX = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX><SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20261105<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>GBP<BANKACCTFROM><BANKID>000000<ACCTID>12345678<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20261101<DTEND>20261105
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261102<TRNAMT>-45.10<FITID>A1<NAME>ALDI<MEMO>Groceries</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261104<TRNAMT>10.00<FITID>A2<NAME>REFUND</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>964.90<DTASOF>20261105</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""


def test_uk_csv_with_preamble_and_dedupe():
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "statement.csv", UK_CSV)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["rows_total"], body["rows_new"], body["rows_duplicate"]) == (4, 4, 0)
    assert any("Skipped 1" in w for w in body["warnings"])
    txs = a.get(f"/api/accounts/{acc}/transactions").json()
    # newest first; same-day rows in reverse statement order (Tesco was the first line on 01/11)
    assert [Decimal(t["amount"]) for t in txs] == [Decimal("2000"), Decimal("-3.2"), Decimal("-3.2"), Decimal("-12.5")]
    assert txs[0]["posted_on"] == "2026-11-03"
    assert Decimal(a.get(f"/api/accounts/{acc}").json()["balance"]) == Decimal("2981.10")
    # re-import: nothing new; the two identical coffees are still two rows
    again = upload(a, acc, "statement.csv", UK_CSV).json()
    assert (again["rows_new"], again["rows_duplicate"]) == (0, 4)


def test_ofx_import_uses_fitid_and_balance():
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "x.ofx", OFX)
    assert r.status_code == 201, r.text
    assert r.json()["rows_new"] == 2
    txs = a.get(f"/api/accounts/{acc}/transactions").json()
    assert txs[1]["description"] == "ALDI — Groceries" and Decimal(txs[1]["amount"]) == Decimal("-45.10")
    assert Decimal(a.get(f"/api/accounts/{acc}").json()["balance"]) == Decimal("964.90")
    assert upload(a, acc, "x.qfx", OFX).json()["rows_new"] == 0


def test_bad_file_is_422():
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "x.csv", b"foo,bar\n1,2\n")
    assert r.status_code == 422 and "Headers seen" in r.json()["detail"]
    assert upload(a, acc, "x.pdf", b"%PDF-1.4 ...").status_code == 422


def test_undo_import():
    a = make_user("a")
    acc = create_account(a)
    bid = upload(a, acc, "s.csv", UK_CSV).json()["batch_id"]
    assert a.delete(f"/api/imports/{bid}").status_code == 204
    assert a.get(f"/api/accounts/{acc}/transactions").json() == []


def test_summary_respects_attribution():
    """'hide' accounts are excluded from household totals but still visible (§3.5)."""
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    inc, hid_acc = create_account(a, "Joint"), create_account(b, "Bob private")
    a.put(f"/api/households/{hid}/accounts/{inc}", json={"attribution": "include"})
    b.put(f"/api/households/{hid}/accounts/{hid_acc}", json={"attribution": "hide"})
    upload(a, inc, "a.csv", b"Date,Description,Amount\n2026-11-01,Shop,-10.00\n2026-11-02,Pay,100\n")
    upload(b, hid_acc, "b.csv", b"Date,Description,Amount\n2026-11-01,Gadget,-99.99\n")
    s = a.get(f"/api/households/{hid}/summary?month=2026-11").json()
    assert len(s["included"]) == 1 and s["included"][0]["currency"] == "GBP"
    assert Decimal(s["included"][0]["spent"]) == 10 and Decimal(s["included"][0]["received"]) == 100
    assert Decimal(s["excluded"][0]["spent"]) == Decimal("99.99")
    assert (s["accounts_included"], s["accounts_excluded"]) == (1, 1)
    assert len(a.get(f"/api/households/{hid}/transactions").json()) == 3  # access not stripped
    assert [(d["day"], Decimal(d["spent"])) for d in s["daily"]] == [("2026-11-01", Decimal(10))]  # hidden excluded


def test_attribution_requires_explicit_choice():
    a = make_user("a")
    hid = create_household(a)
    acc = create_account(a)
    assert a.put(f"/api/households/{hid}/accounts/{acc}", json={}).status_code == 422


import pytest  # noqa: E402


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-inf", "1E+400", "1e5", "123456789012345.00", "1.23456"])
def test_rejects_non_numeric_or_huge_amounts(amount):
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "x.csv", f"Date,Description,Amount\n2026-11-01,Shop,{amount}\n".encode())
    assert r.status_code == 422, (amount, r.text)
    assert a.get(f"/api/accounts/{acc}/transactions").json() == []
