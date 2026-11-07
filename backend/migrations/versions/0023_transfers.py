"""P-1: transfers between a household's own accounts (current -> savings, card repayments) are linked and left
out of spending. Legs may belong to different members (e.g. you pay into your partner's joint account), so linking
goes through narrow SECURITY DEFINER functions that check visibility, ownership of one leg and matching amounts.

Revision ID: 0023
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    -- "not a transfer" answers, per person
    CREATE TABLE transfer_dismissals (
        user_id    uuid NOT NULL DEFAULT app_uid() REFERENCES users(id) ON DELETE CASCADE,
        out_tx     uuid NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        in_tx      uuid NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, out_tx, in_tx)
    );
    ALTER TABLE transfer_dismissals ENABLE ROW LEVEL SECURITY;
    ALTER TABLE transfer_dismissals FORCE ROW LEVEL SECURITY;
    CREATE POLICY td_own ON transfer_dismissals FOR ALL USING (user_id = app_uid()) WITH CHECK (user_id = app_uid());
    GRANT SELECT, INSERT, DELETE ON transfer_dismissals TO ft_app;

    -- Link money out of one account with the same amount into another. Caller must see both and own at least one.
    CREATE FUNCTION link_transfer(p_out uuid, p_in uuid) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE uid uuid := app_uid(); o record; i record; g uuid;
    BEGIN
      IF uid IS NULL THEN RAISE EXCEPTION 'not signed in' USING ERRCODE = '42501'; END IF;
      SELECT t.*, a.owner_user_id AS owner INTO o FROM transactions t JOIN accounts a ON a.id = t.account_id
       WHERE t.id = p_out AND t.account_id IN (SELECT app_visible_account_ids()) FOR UPDATE OF t;
      SELECT t.*, a.owner_user_id AS owner INTO i FROM transactions t JOIN accounts a ON a.id = t.account_id
       WHERE t.id = p_in AND t.account_id IN (SELECT app_visible_account_ids()) FOR UPDATE OF t;
      IF o.id IS NULL OR i.id IS NULL THEN RAISE EXCEPTION 'not found' USING ERRCODE = 'P0002'; END IF;
      IF o.owner <> uid AND i.owner <> uid THEN RAISE EXCEPTION 'not your account' USING ERRCODE = '42501'; END IF;
      IF o.account_id = i.account_id OR o.amount >= 0 OR i.amount <> -o.amount OR o.currency <> i.currency
         OR abs(o.posted_on - i.posted_on) > 7 THEN
        RAISE EXCEPTION 'not a matching transfer' USING ERRCODE = '22023';
      END IF;
      IF o.transfer_group IS NOT NULL OR i.transfer_group IS NOT NULL THEN
        RAISE EXCEPTION 'already linked' USING ERRCODE = '23505';
      END IF;
      -- both accounts must be the same person's, or shared into a household they both belong to
      IF o.owner <> i.owner AND NOT EXISTS (
          SELECT 1 FROM household_accounts x JOIN household_accounts y ON y.household_id = x.household_id
           WHERE x.account_id = o.account_id AND y.account_id = i.account_id) THEN
        RAISE EXCEPTION 'accounts are not in the same household' USING ERRCODE = '42501';
      END IF;
      g := gen_random_uuid();
      UPDATE transactions SET transfer_group = g, needs_review = false, ai_status = 'done',
             category = CASE WHEN category_source = 'user' THEN category ELSE 'Transfers' END,
             category_source = CASE WHEN category_source = 'user' THEN category_source ELSE 'rule' END
       WHERE id IN (p_out, p_in);
      RETURN g;
    END $$;
    REVOKE ALL ON FUNCTION link_transfer(uuid, uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION link_transfer(uuid, uuid) TO ft_app;

    -- Undo a link. Any owner of a leg may unlink. Wallet moves' generated legs (source='transfer') are removed,
    -- which only their owner can do (they are always the same person's own accounts).
    CREATE FUNCTION unlink_transfer(p_group uuid) RETURNS int
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE uid uuid := app_uid(); n int;
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM transactions t JOIN accounts a ON a.id = t.account_id
                      WHERE t.transfer_group = p_group AND a.owner_user_id = uid) THEN
        RAISE EXCEPTION 'not found' USING ERRCODE = 'P0002';
      END IF;
      IF EXISTS (SELECT 1 FROM transactions t JOIN accounts a ON a.id = t.account_id
                  WHERE t.transfer_group = p_group AND a.owner_user_id <> uid AND t.source = 'transfer') THEN
        RAISE EXCEPTION 'not your account' USING ERRCODE = '42501';
      END IF;
      DELETE FROM transactions WHERE transfer_group = p_group AND source = 'transfer';
      -- legs we labelled 'Transfers' go back to uncategorised so the normal rules run again
      UPDATE transactions SET transfer_group = NULL,
             category_source = CASE WHEN category = 'Transfers' AND category_source = 'rule' THEN NULL ELSE category_source END,
             category = CASE WHEN category = 'Transfers' AND category_source = 'rule' THEN NULL ELSE category END
       WHERE transfer_group = p_group;
      GET DIAGNOSTICS n = ROW_COUNT;
      RETURN n;
    END $$;
    REVOKE ALL ON FUNCTION unlink_transfer(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION unlink_transfer(uuid) TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS unlink_transfer(uuid);
    DROP FUNCTION IF EXISTS link_transfer(uuid, uuid);
    DROP TABLE IF EXISTS transfer_dismissals;
    """)
