"""G-8 shared product names (opt-in), P-7 offline catalogue + use-by dates from a photo."""
import gzip
import io
import shutil
from datetime import date

import pytest

from app.expiry import find_dates
from tests.conftest import create_household, make_user

T = date(2026, 11, 7)


@pytest.mark.parametrize("lines,want", [
    (["BEST BEFORE 12/11/26", "L2304 12:31"], date(2026, 11, 12)),
    (["USE BY", "14 NOV"], date(2026, 11, 14)),
    (["Display until 09 Nov", "Use by 10 NOV 2026"], date(2026, 11, 10)),
    (["BEST BEFORE END MAR 2027"], date(2027, 3, 31)),
    (["BBE 03/2028"], date(2028, 3, 31)),
    (["exp 2O.12.2026"], date(2026, 12, 20)),          # O read instead of 0
])
def test_find_dates(lines, want):
    assert find_dates(lines, T)[0] == want


def test_no_date_found():
    assert find_dates(["Semi skimmed milk", "2 pints 1.136L", "Keep refrigerated"], T) == []


def test_product_names_shared_only_when_opted_in():
    a, b = make_user("a"), make_user("b")
    ha, hb = create_household(a), create_household(b)
    a.post(f"/api/households/{ha}/products", json={"name": "Yeo Valley Greek Yoghurt", "barcode": "5036589200212", "quantity": 1,
                                                  "brand": "Yeo Valley", "size_qty": 450, "size_unit": "g"})
    r = b.post(f"/api/households/{hb}/scan", json={"codes": ["5036589200212"]}).json()
    assert r["unknown"] == ["5036589200212"] and r["known_names"] == {}          # off by default
    assert b.put(f"/api/households/{ha}/share-products", json={"enabled": True}).status_code == 404
    assert a.put(f"/api/households/{ha}/share-products", json={"enabled": True}).json()["enabled"] is True
    r = b.post(f"/api/households/{hb}/scan", json={"codes": ["5036589200212"]}).json()
    assert r["known_names"]["5036589200212"]["name"] == "Yeo Valley Greek Yoghurt"
    assert set(r["known_names"]["5036589200212"]) == {"name", "brand", "size_qty", "size_unit", "source"}   # no prices, no household


def test_open_food_facts_import(tmp_path):
    from app.catalog import import_off, lookup
    tsv = ("code\tproduct_name\tbrands\tquantity\tcountries_tags\n"
           "5000168001142\tDigestive biscuits\tMcVitie's,Pladis\t400 g\ten:united-kingdom\n"
           "3017620422003\tNutella\tFerrero\t400 g\ten:france\n"
           "abc\tBad\t\t\ten:united-kingdom\n")
    p = tmp_path / "off.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(tsv)
    assert import_off(str(p)) == 1
    a = make_user("a")
    hid = create_household(a)
    r = a.post(f"/api/households/{hid}/scan", json={"codes": ["5000168001142", "3017620422003"]}).json()
    assert r["known_names"] == {"5000168001142": {"name": "Digestive biscuits", "brand": "McVitie's", "size_qty": "400.000",
                                                  "size_unit": "g", "source": "open_food_facts"}}


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_read_date_from_photo():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (900, 220), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default()
    d.text((30, 60), "BEST BEFORE 14/12/26", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    a = make_user("a")
    hid = create_household(a)
    r = a.post(f"/api/households/{hid}/read-date", files={"file": ("p.png", buf.getvalue())})
    assert r.status_code == 200 and r.json()["best"] == "2026-12-14"
    assert make_user("x").post(f"/api/households/{hid}/read-date", files={"file": ("p.png", buf.getvalue())}).status_code == 404


def test_households_cannot_overwrite_each_others_names():
    a, b, c = make_user("a"), make_user("b"), make_user("c")
    ha, hb, hc = create_household(a), create_household(b), create_household(c)
    for cl, h in ((a, ha), (b, hb)):
        cl.put(f"/api/households/{h}/share-products", json={"enabled": True})
    a.post(f"/api/households/{ha}/products", json={"name": "Heinz Baked Beans", "barcode": "5000157024671", "quantity": 1})
    b.post(f"/api/households/{hb}/products", json={"name": "Totally different", "barcode": "5000157024671", "quantity": 1})
    assert c.post(f"/api/households/{hc}/scan", json={"codes": ["5000157024671"]}).json()["known_names"]["5000157024671"]["name"] == "Heinz Baked Beans"
    a.put(f"/api/households/{ha}/share-products", json={"enabled": False})            # turning it off takes them back
    assert c.post(f"/api/households/{hc}/scan", json={"codes": ["5000157024671"]}).json()["known_names"] == {}
