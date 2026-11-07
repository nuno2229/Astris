"""P-13: delete my account and data.

The user row stays as an anonymous tombstone ("Former member", unusable login) so household history and the
tamper-evident audit chain stay intact; everything personal is deleted: accounts with all their transactions and
statement files, documents, rules, notification channels, personal budgets and lists, goal contributions, sessions.
Households where they were the only member are deleted; in shared households ownership passes to an admin (or the
longest-standing member).

Revision ID: 0030
"""
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE users ADD COLUMN deleted_at timestamptz;

    CREATE FUNCTION delete_my_account() RETURNS TABLE (household_id uuid, new_owner uuid)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$
    DECLARE uid uuid := app_uid(); h uuid; heir uuid;
    BEGIN
      IF uid IS NULL THEN RAISE EXCEPTION 'not signed in' USING ERRCODE = '42501'; END IF;
      IF (SELECT is_server_admin FROM users WHERE id = uid)
         AND NOT EXISTS (SELECT 1 FROM users WHERE is_server_admin AND id <> uid AND deleted_at IS NULL)
         AND EXISTS (SELECT 1 FROM users WHERE id <> uid AND deleted_at IS NULL) THEN
        RAISE EXCEPTION 'last server admin' USING ERRCODE = '42501';
      END IF;
      FOR h IN SELECT m.household_id FROM memberships m WHERE m.user_id = uid AND m.role = 'owner' LOOP
        SELECT m.user_id INTO heir FROM memberships m WHERE m.household_id = h AND m.user_id <> uid
         ORDER BY (m.role = 'admin') DESC, m.joined_at LIMIT 1;
        IF heir IS NULL THEN
          DELETE FROM households WHERE id = h;
        ELSE
          UPDATE memberships SET role = 'member' WHERE memberships.household_id = h AND user_id = uid;
          UPDATE memberships SET role = 'owner' WHERE memberships.household_id = h AND user_id = heir;
          household_id := h; new_owner := heir; RETURN NEXT;
        END IF;
      END LOOP;
      DELETE FROM memberships WHERE user_id = uid;
      DELETE FROM former_members WHERE user_id = uid;
      DELETE FROM accounts WHERE owner_user_id = uid;            -- transactions, imports, shares cascade
      DELETE FROM documents WHERE owner_user_id = uid;
      DELETE FROM goal_contributions WHERE user_id = uid;
      DELETE FROM goal_rules WHERE user_id = uid;
      DELETE FROM budgets WHERE owner_user_id = uid;
      DELETE FROM shopping_lists WHERE owner_user_id = uid;
      DELETE FROM favourites WHERE user_id = uid;
      DELETE FROM merchant_rules WHERE user_id = uid;
      DELETE FROM notification_channels WHERE user_id = uid;
      DELETE FROM notification_prefs WHERE user_id = uid;
      DELETE FROM notification_log WHERE user_id = uid;
      DELETE FROM import_previews WHERE user_id = uid;
      DELETE FROM import_runs WHERE user_id = uid;
      DELETE FROM import_profiles WHERE user_id = uid;
      DELETE FROM copilot_actions WHERE user_id = uid;
      DELETE FROM transfer_dismissals WHERE user_id = uid;
      DELETE FROM sessions WHERE user_id = uid;
      UPDATE invitations SET revoked_at = now() WHERE created_by = uid AND accepted_at IS NULL AND revoked_at IS NULL;
      UPDATE users SET email = 'deleted-' || id || '@invalid', display_name = 'Former member', password_hash = '!',
                       is_server_admin = false, deleted_at = now()
       WHERE id = uid;
    END $$;
    REVOKE ALL ON FUNCTION delete_my_account() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION delete_my_account() TO ft_app;
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS delete_my_account();
    ALTER TABLE users DROP COLUMN IF EXISTS deleted_at;
    """)
