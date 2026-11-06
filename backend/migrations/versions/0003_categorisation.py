"""E05: categorisation — confidence, source, review flag, AI queue state, learned merchant rules.

Revision ID: 0003
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TYPE category_source AS ENUM ('rule', 'llm', 'user', 'user_rule');

    ALTER TABLE transactions
        ADD COLUMN category_source     category_source,
        ADD COLUMN category_confidence numeric(4,3) CHECK (category_confidence BETWEEN 0 AND 1),
        ADD COLUMN needs_review        boolean NOT NULL DEFAULT false,
        ADD COLUMN merchant_key        text,
        -- background AI pass: pending -> done | skipped (no LLM configured) | failed
        ADD COLUMN ai_status           text NOT NULL DEFAULT 'pending'
                   CHECK (ai_status IN ('pending', 'done', 'skipped', 'failed'));
    CREATE INDEX transactions_ai_pending_idx ON transactions (account_id) WHERE ai_status = 'pending';
    CREATE INDEX transactions_review_idx ON transactions (account_id) WHERE needs_review;

    -- What a user taught the app ("always put TESCO in Groceries"). Personal: applies to their own accounts.
    CREATE TABLE merchant_rules (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        merchant_key text NOT NULL CHECK (length(merchant_key) BETWEEN 1 AND 100),
        merchant     text CHECK (merchant IS NULL OR length(merchant) <= 100),
        category     text NOT NULL CHECK (length(category) <= 50),
        created_at   timestamptz NOT NULL DEFAULT now(),
        updated_at   timestamptz NOT NULL DEFAULT now(),
        UNIQUE (user_id, merchant_key)
    );
    ALTER TABLE merchant_rules ENABLE ROW LEVEL SECURITY;
    ALTER TABLE merchant_rules FORCE ROW LEVEL SECURITY;
    CREATE POLICY mr_own ON merchant_rules FOR ALL USING (user_id = app_uid()) WITH CHECK (user_id = app_uid());
    GRANT SELECT, INSERT, UPDATE, DELETE ON merchant_rules TO ft_app;

    -- Background worker discovery: which users have AI work queued. Returns ids only; the worker then
    -- binds app.user_id to each owner, so every read/write still goes through RLS as that user.
    CREATE FUNCTION ai_pending_owners(p_limit int) RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT DISTINCT a.owner_user_id FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE t.ai_status = 'pending' LIMIT least(p_limit, 100) $$;

    -- Household owner turned AI on: re-queue rows of accounts shared into that household (status only).
    CREATE FUNCTION ai_requeue_household(p_household uuid) RETURNS int
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE n int;
    BEGIN
      IF app_my_role(p_household) IS DISTINCT FROM 'owner' THEN
        RAISE EXCEPTION 'not allowed' USING ERRCODE = '42501';
      END IF;
      UPDATE transactions t SET ai_status = 'pending'
        FROM household_accounts ha
       WHERE ha.household_id = p_household AND ha.account_id = t.account_id
         AND t.ai_status IN ('skipped', 'failed')
         AND (t.category_source IS DISTINCT FROM 'user' AND t.category_source IS DISTINCT FROM 'user_rule');
      GET DIAGNOSTICS n = ROW_COUNT;
      RETURN n;
    END $$;

    REVOKE ALL ON FUNCTION ai_pending_owners(int), ai_requeue_household(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION ai_pending_owners(int), ai_requeue_household(uuid) TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS ai_pending_owners(int), ai_requeue_household(uuid);
    DROP TABLE IF EXISTS merchant_rules;
    ALTER TABLE transactions DROP COLUMN IF EXISTS category_source, DROP COLUMN IF EXISTS category_confidence,
        DROP COLUMN IF EXISTS needs_review, DROP COLUMN IF EXISTS merchant_key, DROP COLUMN IF EXISTS ai_status;
    DROP TYPE IF EXISTS category_source;
    """)
