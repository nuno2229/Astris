from tests.conftest import create_household, invite_and_join, make_user


def test_create_and_list():
    a = make_user("a")
    hid = create_household(a, "Flat")
    hs = a.get("/api/me/households").json()
    assert hs == [{"id": hid, "name": "Flat", "role": "owner"}]


def test_invite_flow_single_use():
    a, b, c = make_user("a"), make_user("b"), make_user("c")
    hid = create_household(a)
    code = a.post(f"/api/households/{hid}/invites", json={}).json()["code"]
    assert b.post("/api/invites/accept", json={"code": code}).status_code == 200
    assert c.post("/api/invites/accept", json={"code": code}).status_code == 400  # already used
    members = a.get(f"/api/households/{hid}").json()["members"]
    assert {m["role"] for m in members} == {"owner", "member"}


def test_email_restricted_invite():
    a, b, c = make_user("a"), make_user("b"), make_user("c")
    hid = create_household(a)
    code = a.post(f"/api/households/{hid}/invites", json={"email": b.me["email"]}).json()["code"]
    assert c.post("/api/invites/accept", json={"code": code}).status_code == 400
    assert b.post("/api/invites/accept", json={"code": code}).status_code == 200


def test_revoked_invite_cannot_be_used():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    inv = a.post(f"/api/households/{hid}/invites", json={}).json()
    assert a.delete(f"/api/households/{hid}/invites/{inv['id']}").status_code == 204
    assert b.post("/api/invites/accept", json={"code": inv["code"]}).status_code == 400


def test_expired_invite(owner_engine):
    from sqlalchemy import text
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    inv = a.post(f"/api/households/{hid}/invites", json={}).json()
    with owner_engine.begin() as c:
        c.execute(text("UPDATE invitations SET expires_at = now() - interval '1 second' WHERE id = :i"), {"i": inv["id"]})
    assert b.post("/api/invites/accept", json={"code": inv["code"]}).status_code == 400


def test_roles():
    owner, admin, member, newbie = make_user("o"), make_user("ad"), make_user("m"), make_user("n")
    hid = create_household(owner)
    invite_and_join(owner, admin, hid, role="admin")
    invite_and_join(owner, member, hid)
    # member can't invite or see invites
    assert member.post(f"/api/households/{hid}/invites", json={}).status_code == 403
    assert member.get(f"/api/households/{hid}/invites").status_code == 403
    # admin can invite members but not admins
    assert admin.post(f"/api/households/{hid}/invites", json={"role": "admin"}).status_code == 403
    assert admin.post(f"/api/households/{hid}/invites", json={}).status_code == 201
    # only owner renames
    assert admin.patch(f"/api/households/{hid}", json={"name": "X"}).status_code == 403
    assert owner.patch(f"/api/households/{hid}", json={"name": "X"}).status_code == 200
    # nobody removes the owner; admin can't remove admin; member can't remove anyone
    assert admin.delete(f"/api/households/{hid}/members/{owner.me['id']}").status_code == 403
    assert member.delete(f"/api/households/{hid}/members/{admin.me['id']}").status_code == 403
    assert owner.delete(f"/api/households/{hid}/members/{owner.me['id']}").status_code == 403


def test_revocation_is_immediate():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    assert b.get(f"/api/households/{hid}").status_code == 200
    assert a.delete(f"/api/households/{hid}/members/{b.me['id']}").status_code == 204
    assert b.get(f"/api/households/{hid}").status_code == 404
    assert b.get("/api/me/households").json() == []


def test_member_can_leave():
    a, b = make_user("a"), make_user("b")
    hid = create_household(a)
    invite_and_join(a, b, hid)
    assert b.delete(f"/api/households/{hid}/members/{b.me['id']}").status_code == 204
    assert b.get(f"/api/households/{hid}").status_code == 404
