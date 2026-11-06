"""E10: append-only, hash-chained audit log.

The runtime role can only INSERT and SELECT (RLS). A trigger owned by the schema owner fills the chain:
each entry's hash covers its content and the previous entry's hash in the same chain (per account,
household or user). Editing or deleting a past entry breaks verification.

Revision ID: 0011
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE audit_log (
        id            bigserial PRIMARY KEY,
        at            timestamptz NOT NULL DEFAULT now(),
        actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        household_id  uuid REFERENCES households(id) ON DELETE SET NULL,
        account_id    uuid REFERENCES accounts(id) ON DELETE SET NULL,
        action        text NOT NULL CHECK (length(action) <= 60),
        entity        text NOT NULL CHECK (length(entity) <= 40),
        entity_id     uuid,
        summary       text NOT NULL CHECK (length(summary) <= 300),
        reason        text CHECK (reason IS NULL OR length(reason) <= 300),
        before        jsonb,
        after         jsonb,
        reverses      bigint REFERENCES audit_log(id),
        chain         text NOT NULL DEFAULT '',
        prev_hash     text NOT NULL DEFAULT '',
        hash          text NOT NULL DEFAULT ''
    );
    CREATE INDEX audit_chain_idx ON audit_log (chain, id);
    CREATE INDEX audit_account_idx ON audit_log (account_id, id DESC);
    CREATE INDEX audit_household_idx ON audit_log (household_id, id DESC);
    CREATE UNIQUE INDEX audit_reverses_once ON audit_log (reverses) WHERE reverses IS NOT NULL;

    CREATE FUNCTION audit_entry_hash(r audit_log) RETURNS text
    LANGUAGE sql IMMUTABLE AS
    $$ SELECT encode(sha256(convert_to(concat_ws('|', r.prev_hash, r.id::text, r.chain,
              to_char(r.at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'), coalesce(r.actor_user_id::text, ''),
              r.action, r.entity, coalesce(r.entity_id::text, ''), r.summary, coalesce(r.reason, ''),
              coalesce(r.before::text, ''), coalesce(r.after::text, ''), coalesce(r.reverses::text, '')), 'UTF8')), 'hex') $$;

    CREATE FUNCTION audit_chain() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE prev text;
    BEGIN
      NEW.at := now();
      NEW.chain := CASE WHEN NEW.account_id IS NOT NULL THEN 'account:' || NEW.account_id
                        WHEN NEW.household_id IS NOT NULL THEN 'household:' || NEW.household_id
                        ELSE 'user:' || NEW.actor_user_id END;
      PERFORM pg_advisory_xact_lock(hashtextextended(NEW.chain, 0));
      SELECT hash INTO prev FROM audit_log WHERE chain = NEW.chain ORDER BY id DESC LIMIT 1;
      NEW.prev_hash := coalesce(prev, 'genesis');
      NEW.hash := audit_entry_hash(NEW);
      RETURN NEW;
    END $$;
    CREATE TRIGGER audit_chain_trg BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_chain();

    -- Recompute a chain the caller is allowed to see. Returns the first broken entry (NULL = intact).
    CREATE FUNCTION audit_verify(p_chain text) RETURNS TABLE (entries int, head text, broken_id bigint)
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE r audit_log; expected_prev text := 'genesis'; n int := 0; last text := NULL; ok_to_see boolean;
    BEGIN
      ok_to_see := CASE
        WHEN p_chain LIKE 'account:%' THEN EXISTS (SELECT 1 FROM accounts WHERE id = substr(p_chain, 9)::uuid AND owner_user_id = app_uid())
        WHEN p_chain LIKE 'household:%' THEN app_my_role(substr(p_chain, 11)::uuid) IN ('owner', 'admin')
        WHEN p_chain LIKE 'user:%' THEN substr(p_chain, 6)::uuid = app_uid()
        ELSE false END;
      IF NOT coalesce(ok_to_see, false) THEN RAISE EXCEPTION 'not allowed' USING ERRCODE = '42501'; END IF;
      FOR r IN SELECT * FROM audit_log WHERE chain = p_chain ORDER BY id LOOP
        n := n + 1;
        IF r.prev_hash <> expected_prev OR r.hash <> audit_entry_hash(r) THEN
          entries := n; head := last; broken_id := r.id; RETURN NEXT; RETURN;
        END IF;
        expected_prev := r.hash; last := r.hash;
      END LOOP;
      entries := n; head := last; broken_id := NULL; RETURN NEXT;
    END $$;

    ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
    ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
    CREATE POLICY audit_select ON audit_log FOR SELECT USING (
        actor_user_id = app_uid()
        OR (account_id IS NOT NULL AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid()))
        OR (household_id IS NOT NULL AND app_my_role(household_id) IN ('owner', 'admin')));
    CREATE POLICY audit_insert ON audit_log FOR INSERT WITH CHECK (actor_user_id = app_uid());
    -- append-only for the app: no UPDATE / DELETE grants at all
    GRANT SELECT, INSERT ON audit_log TO ft_app;
    GRANT USAGE ON SEQUENCE audit_log_id_seq TO ft_app;
    REVOKE ALL ON FUNCTION audit_verify(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION audit_verify(text) TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS audit_log CASCADE;
    DROP FUNCTION IF EXISTS audit_chain(), audit_verify(text), audit_entry_hash(audit_log);
    """)
