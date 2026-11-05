"""M1 foundation: users, sessions, households, memberships, invitations,
accounts, household attribution, import batches, transactions, row-level security.

Isolation model (docs/architecture.md):
  * runtime role ft_app is subject to RLS on every tenant table
  * app.user_id is set per transaction (SET LOCAL) by the API
  * pre-auth steps go through narrow SECURITY DEFINER functions owned by ft_owner

Revision ID: 0001
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = r"""
CREATE TYPE household_role AS ENUM ('owner', 'admin', 'member');
CREATE TYPE account_type   AS ENUM ('bank', 'card', 'cash');
CREATE TYPE attribution    AS ENUM ('include', 'hide');

CREATE TABLE users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email          text NOT NULL CHECK (email = lower(email) AND position('@' in email) > 1),
    display_name   text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 100),
    password_hash  text NOT NULL,
    home_currency  char(3) NOT NULL DEFAULT 'GBP',
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_uq ON users (email);

CREATE TABLE sessions (
    token_hash   text PRIMARY KEY,                 -- sha256 of the cookie token; raw token never stored
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    revoked_at   timestamptz
);
CREATE INDEX sessions_user_idx ON sessions (user_id);

CREATE TABLE households (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    created_by  uuid NOT NULL REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
    household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         household_role NOT NULL,
    joined_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (household_id, user_id)
);
CREATE INDEX memberships_user_idx ON memberships (user_id);
-- exactly one owner per household
CREATE UNIQUE INDEX memberships_one_owner ON memberships (household_id) WHERE role = 'owner';

CREATE TABLE invitations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    code_hash    text NOT NULL UNIQUE,              -- sha256 of the invite code
    email        text CHECK (email IS NULL OR email = lower(email)),  -- optional: restrict to this address
    role         household_role NOT NULL DEFAULT 'member' CHECK (role <> 'owner'),
    created_by   uuid NOT NULL REFERENCES users(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    accepted_by  uuid REFERENCES users(id),
    accepted_at  timestamptz,
    revoked_at   timestamptz
);
CREATE INDEX invitations_household_idx ON invitations (household_id);

CREATE TABLE accounts (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name           text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    type           account_type NOT NULL,
    currency       char(3) NOT NULL,
    institution    text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX accounts_owner_idx ON accounts (owner_user_id);

-- An account is shared into a household explicitly by its owner, with an attribution (§3.5, G-11).
CREATE TABLE household_accounts (
    household_id uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    account_id   uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    attribution  attribution NOT NULL,
    shared_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (household_id, account_id)
);
CREATE INDEX household_accounts_account_idx ON household_accounts (account_id);

CREATE TABLE import_batches (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id   uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    imported_by  uuid NOT NULL REFERENCES users(id),
    filename     text NOT NULL,
    format       text NOT NULL,
    rows_total   int NOT NULL DEFAULT 0,
    rows_new     int NOT NULL DEFAULT 0,
    rows_duplicate int NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX import_batches_account_idx ON import_batches (account_id);

CREATE TABLE transactions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id       uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    import_batch_id  uuid REFERENCES import_batches(id) ON DELETE CASCADE,
    posted_on        date NOT NULL,
    amount           numeric(18,4) NOT NULL,       -- negative = money out
    currency         char(3) NOT NULL,
    description      text NOT NULL,
    merchant         text,
    counterparty     text,
    category         text,
    running_balance  numeric(18,4),
    fingerprint      text NOT NULL,                -- dedupe key (architecture.md, Ledger rules)
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, fingerprint)
);
CREATE INDEX transactions_account_date_idx ON transactions (account_id, posted_on DESC);
"""

RLS = r"""
-- ---------- helpers ----------
CREATE FUNCTION app_uid() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;

-- SECURITY DEFINER (owner has BYPASSRLS) to avoid policy recursion on memberships.
CREATE FUNCTION app_my_household_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT household_id FROM memberships WHERE user_id = app_uid() $$;

CREATE FUNCTION app_my_role(h uuid) RETURNS household_role
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT role FROM memberships WHERE user_id = app_uid() AND household_id = h $$;

-- Accounts I own + accounts shared into my households whose owner is STILL a member there
-- (a removed member's accounts drop out of the household immediately; G-9 interim).
CREATE FUNCTION app_visible_account_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$
  SELECT id FROM accounts WHERE owner_user_id = app_uid()
  UNION
  SELECT ha.account_id
    FROM household_accounts ha
    JOIN accounts a     ON a.id = ha.account_id
    JOIN memberships me ON me.household_id = ha.household_id AND me.user_id = app_uid()
    JOIN memberships ow ON ow.household_id = ha.household_id AND ow.user_id = a.owner_user_id
$$;

-- ---------- RLS ----------
ALTER TABLE users              ENABLE ROW LEVEL SECURITY; ALTER TABLE users              FORCE ROW LEVEL SECURITY;
ALTER TABLE sessions           ENABLE ROW LEVEL SECURITY; ALTER TABLE sessions           FORCE ROW LEVEL SECURITY;
ALTER TABLE households         ENABLE ROW LEVEL SECURITY; ALTER TABLE households         FORCE ROW LEVEL SECURITY;
ALTER TABLE memberships        ENABLE ROW LEVEL SECURITY; ALTER TABLE memberships        FORCE ROW LEVEL SECURITY;
ALTER TABLE invitations        ENABLE ROW LEVEL SECURITY; ALTER TABLE invitations        FORCE ROW LEVEL SECURITY;
ALTER TABLE accounts           ENABLE ROW LEVEL SECURITY; ALTER TABLE accounts           FORCE ROW LEVEL SECURITY;
ALTER TABLE household_accounts ENABLE ROW LEVEL SECURITY; ALTER TABLE household_accounts FORCE ROW LEVEL SECURITY;
ALTER TABLE import_batches     ENABLE ROW LEVEL SECURITY; ALTER TABLE import_batches     FORCE ROW LEVEL SECURITY;
ALTER TABLE transactions       ENABLE ROW LEVEL SECURITY; ALTER TABLE transactions       FORCE ROW LEVEL SECURITY;

-- users: me + co-members of my households. Self-update only. Insert via register_user().
CREATE POLICY users_select ON users FOR SELECT USING (
    id = app_uid()
    OR id IN (SELECT user_id FROM memberships WHERE household_id IN (SELECT app_my_household_ids())));
CREATE POLICY users_update ON users FOR UPDATE USING (id = app_uid()) WITH CHECK (id = app_uid());

-- sessions: my own (list / revoke). Creation + resolution via definer functions.
CREATE POLICY sessions_own ON sessions FOR SELECT USING (user_id = app_uid());
CREATE POLICY sessions_revoke ON sessions FOR UPDATE USING (user_id = app_uid()) WITH CHECK (user_id = app_uid());

-- households: members see; owner renames/deletes. Creation via create_household().
CREATE POLICY households_select ON households FOR SELECT USING (id IN (SELECT app_my_household_ids()));
CREATE POLICY households_update ON households FOR UPDATE USING (app_my_role(id) = 'owner') WITH CHECK (app_my_role(id) = 'owner');
CREATE POLICY households_delete ON households FOR DELETE USING (app_my_role(id) = 'owner');

-- memberships: members see their households' rosters. Joining only via accept_invite().
-- Leave = delete own row. Owner removes any non-owner; admin removes members only. The owner row is never deletable.
CREATE POLICY memberships_select ON memberships FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
CREATE POLICY memberships_delete ON memberships FOR DELETE USING (
    role <> 'owner' AND (
        user_id = app_uid()
        OR app_my_role(household_id) = 'owner'
        OR (app_my_role(household_id) = 'admin' AND role = 'member')));
CREATE POLICY memberships_update ON memberships FOR UPDATE
    USING (app_my_role(household_id) = 'owner' AND role <> 'owner')
    WITH CHECK (app_my_role(household_id) = 'owner' AND role <> 'owner');

-- invitations: owner/admin of the household manage them.
CREATE POLICY invitations_manage ON invitations FOR ALL
    USING (app_my_role(household_id) IN ('owner', 'admin'))
    WITH CHECK (app_my_role(household_id) IN ('owner', 'admin') AND created_by = app_uid());

-- accounts: visible per app_visible_account_ids(); only the owner writes.
-- owner branch checks the row itself so INSERT ... RETURNING works (the function can't see the new row yet)
CREATE POLICY accounts_select ON accounts FOR SELECT USING (
    owner_user_id = app_uid() OR id IN (SELECT app_visible_account_ids()));
CREATE POLICY accounts_insert ON accounts FOR INSERT WITH CHECK (owner_user_id = app_uid());
CREATE POLICY accounts_update ON accounts FOR UPDATE USING (owner_user_id = app_uid()) WITH CHECK (owner_user_id = app_uid());
CREATE POLICY accounts_delete ON accounts FOR DELETE USING (owner_user_id = app_uid());

-- household_accounts: members see their households' links; the account owner (who must be a member) shares/unshares/sets attribution.
CREATE POLICY ha_select ON household_accounts FOR SELECT USING (household_id IN (SELECT app_my_household_ids()));
CREATE POLICY ha_write ON household_accounts FOR ALL
    USING (household_id IN (SELECT app_my_household_ids())
           AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()))
    WITH CHECK (household_id IN (SELECT app_my_household_ids())
           AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()));

-- import batches + transactions: readable if the account is visible; writable by the account owner.
CREATE POLICY ib_select ON import_batches FOR SELECT USING (account_id IN (SELECT app_visible_account_ids()));
CREATE POLICY ib_write ON import_batches FOR ALL
    USING (account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()))
    WITH CHECK (account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()) AND imported_by = app_uid());
CREATE POLICY tx_select ON transactions FOR SELECT USING (account_id IN (SELECT app_visible_account_ids()));
CREATE POLICY tx_write ON transactions FOR ALL
    USING (account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()))
    WITH CHECK (account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()));

-- ---------- pre-auth / privileged entry points (narrow, SECURITY DEFINER) ----------
CREATE FUNCTION auth_register_user(p_email text, p_name text, p_hash text) RETURNS uuid
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ INSERT INTO users (email, display_name, password_hash) VALUES (lower(p_email), p_name, p_hash) RETURNING id $$;

CREATE FUNCTION auth_login_lookup(p_email text) RETURNS TABLE (user_id uuid, password_hash text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ SELECT id, password_hash FROM users WHERE email = lower(p_email) $$;

CREATE FUNCTION auth_set_password_hash(p_user uuid, p_hash text) RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ UPDATE users SET password_hash = p_hash WHERE id = p_user $$;

CREATE FUNCTION auth_create_session(p_user uuid, p_token_hash text, p_expires timestamptz) RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (p_token_hash, p_user, p_expires) $$;

-- Returns the user for a live session and bumps last_seen_at. NULL if expired/revoked/unknown.
CREATE FUNCTION auth_resolve_session(p_token_hash text) RETURNS uuid
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ UPDATE sessions SET last_seen_at = now()
    WHERE token_hash = p_token_hash AND revoked_at IS NULL AND expires_at > now()
   RETURNING user_id $$;

CREATE FUNCTION auth_revoke_session(p_token_hash text) RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$ UPDATE sessions SET revoked_at = now() WHERE token_hash = p_token_hash AND revoked_at IS NULL $$;

-- Requires app.user_id; creator becomes owner.
CREATE FUNCTION create_household(p_name text) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$
DECLARE hid uuid; uid uuid := app_uid();
BEGIN
  IF uid IS NULL THEN RAISE EXCEPTION 'not authenticated' USING ERRCODE = '28000'; END IF;
  INSERT INTO households (name, created_by) VALUES (p_name, uid) RETURNING id INTO hid;
  INSERT INTO memberships (household_id, user_id, role) VALUES (hid, uid, 'owner');
  RETURN hid;
END $$;

-- Requires app.user_id. Single-use, unexpired, unrevoked; email-restricted invites must match.
CREATE FUNCTION accept_invite(p_code_hash text) RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
$$
DECLARE inv invitations%ROWTYPE; uid uuid := app_uid(); my_email text;
BEGIN
  IF uid IS NULL THEN RAISE EXCEPTION 'not authenticated' USING ERRCODE = '28000'; END IF;
  SELECT * INTO inv FROM invitations WHERE code_hash = p_code_hash FOR UPDATE;
  IF NOT FOUND OR inv.revoked_at IS NOT NULL OR inv.accepted_at IS NOT NULL OR inv.expires_at <= now() THEN
    RAISE EXCEPTION 'invalid invite' USING ERRCODE = 'P0002';
  END IF;
  SELECT email INTO my_email FROM users WHERE id = uid;
  IF inv.email IS NOT NULL AND inv.email <> my_email THEN
    RAISE EXCEPTION 'invalid invite' USING ERRCODE = 'P0002';
  END IF;
  IF EXISTS (SELECT 1 FROM memberships WHERE household_id = inv.household_id AND user_id = uid) THEN
    RAISE EXCEPTION 'already a member' USING ERRCODE = '23505';
  END IF;
  INSERT INTO memberships (household_id, user_id, role) VALUES (inv.household_id, uid, inv.role);
  UPDATE invitations SET accepted_by = uid, accepted_at = now() WHERE id = inv.id;
  RETURN inv.household_id;
END $$;

-- ---------- grants for runtime role ----------
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO ft_app;
-- column-level: the app role can never read or write password hashes directly
GRANT SELECT (id, email, display_name, home_currency, created_at) ON users TO ft_app;
GRANT UPDATE (display_name, home_currency) ON users TO ft_app;
GRANT SELECT (user_id, created_at, expires_at, last_seen_at, revoked_at) ON sessions TO ft_app;
GRANT UPDATE (revoked_at) ON sessions TO ft_app;
GRANT SELECT, UPDATE, DELETE ON households, memberships TO ft_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON invitations, accounts, household_accounts, import_batches, transactions TO ft_app;
"""


def upgrade() -> None:
    op.execute(SCHEMA)
    op.execute(RLS)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS transactions, import_batches, household_accounts, accounts,
        invitations, memberships, households, sessions, users CASCADE;
    DROP FUNCTION IF EXISTS app_uid, app_my_household_ids, app_my_role, app_visible_account_ids,
        auth_register_user, auth_login_lookup, auth_set_password_hash, auth_create_session,
        auth_resolve_session, auth_revoke_session, create_household, accept_invite CASCADE;
    DROP TYPE IF EXISTS household_role, account_type, attribution;
    """)
