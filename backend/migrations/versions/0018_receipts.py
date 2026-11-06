"""E04: receipts and line items (unit prices, discounts), reconciled onto bank transactions.

Revision ID: 0018
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    CREATE TABLE receipts (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        household_id   uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
        uploaded_by    uuid REFERENCES users(id) ON DELETE SET NULL,
        store          text CHECK (store IS NULL OR length(store) <= 80),
        purchased_on   date,
        total          numeric(14,2) CHECK (total IS NULL OR (total >= 0 AND total < 1e9)),
        currency       char(3) NOT NULL,
        status         text NOT NULL DEFAULT 'review' CHECK (status IN ('review', 'saved')),
        adds_up        boolean,
        read_by        text NOT NULL DEFAULT 'rules' CHECK (read_by IN ('rules', 'ai')),
        transaction_id uuid UNIQUE REFERENCES transactions(id) ON DELETE SET NULL,
        warnings       jsonb NOT NULL DEFAULT '[]'::jsonb,
        created_at     timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX receipts_household_idx ON receipts (household_id, purchased_on DESC);

    CREATE TABLE receipt_items (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        receipt_id  uuid NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
        position    int NOT NULL,
        name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
        quantity    numeric(10,3) NOT NULL DEFAULT 1 CHECK (quantity > 0 AND quantity < 100000),
        size_qty    numeric(12,3) CHECK (size_qty IS NULL OR size_qty > 0),
        size_unit   text CHECK (size_unit IS NULL OR size_unit IN ('g', 'kg', 'ml', 'l', 'item')),
        line_price  numeric(12,2) NOT NULL CHECK (abs(line_price) < 1e8),
        discount    numeric(12,2) NOT NULL DEFAULT 0 CHECK (discount <= 0 AND discount > -1e8),
        unit_price  numeric(14,4),
        unit_basis  text CHECK (unit_basis IS NULL OR unit_basis IN ('100g', '100ml', 'item')),
        category    text,
        product_id  uuid REFERENCES products(id) ON DELETE SET NULL
    );
    CREATE INDEX receipt_items_receipt_idx ON receipt_items (receipt_id, position);

    ALTER TABLE receipts ENABLE ROW LEVEL SECURITY;      ALTER TABLE receipts FORCE ROW LEVEL SECURITY;
    ALTER TABLE receipt_items ENABLE ROW LEVEL SECURITY; ALTER TABLE receipt_items FORCE ROW LEVEL SECURITY;
    CREATE POLICY receipts_rw ON receipts FOR ALL USING (household_id IN (SELECT app_my_household_ids()))
        WITH CHECK (household_id IN (SELECT app_my_household_ids())
                    AND (transaction_id IS NULL OR transaction_id IN (SELECT id FROM transactions)));
    CREATE POLICY ritems_rw ON receipt_items FOR ALL USING (receipt_id IN (SELECT id FROM receipts))
        WITH CHECK (receipt_id IN (SELECT id FROM receipts));
    GRANT SELECT, INSERT, UPDATE, DELETE ON receipts, receipt_items TO ft_app;

    -- Kept originals (opt-in per upload, PO 2026-11-06): receipt photos and bank statements.
    CREATE TABLE documents (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind            text NOT NULL CHECK (kind IN ('receipt', 'statement')),
        owner_user_id   uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        household_id    uuid REFERENCES households(id) ON DELETE CASCADE,
        account_id      uuid REFERENCES accounts(id) ON DELETE CASCADE,
        receipt_id      uuid REFERENCES receipts(id) ON DELETE CASCADE,
        import_batch_id uuid REFERENCES import_batches(id) ON DELETE SET NULL,
        filename        text NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
        content_type    text NOT NULL CHECK (content_type IN ('image/jpeg', 'image/png', 'image/webp', 'image/tiff',
                                             'application/pdf', 'text/csv', 'text/plain', 'application/octet-stream')),
        size            int NOT NULL CHECK (size > 0),
        data            bytea NOT NULL,
        created_at      timestamptz NOT NULL DEFAULT now(),
        CHECK (kind <> 'receipt' OR household_id IS NOT NULL),
        CHECK (kind <> 'statement' OR account_id IS NOT NULL)
    );
    CREATE INDEX documents_owner_idx ON documents (owner_user_id);
    CREATE INDEX documents_receipt_idx ON documents (receipt_id);
    CREATE INDEX documents_batch_idx ON documents (import_batch_id);
    ALTER TABLE documents ENABLE ROW LEVEL SECURITY; ALTER TABLE documents FORCE ROW LEVEL SECURITY;
    -- statements: only their owner (they often show address and full account numbers); receipts: the household
    CREATE POLICY docs_select ON documents FOR SELECT USING (
        (kind = 'statement' AND owner_user_id = app_uid())
        OR (kind = 'receipt' AND household_id IN (SELECT app_my_household_ids())));
    CREATE POLICY docs_insert ON documents FOR INSERT WITH CHECK (
        owner_user_id = app_uid()
        AND (kind = 'receipt' AND household_id IN (SELECT app_my_household_ids())
             OR kind = 'statement' AND account_id IN (SELECT id FROM accounts WHERE owner_user_id = app_uid())));
    CREATE POLICY docs_delete ON documents FOR DELETE USING (
        owner_user_id = app_uid() OR (kind = 'receipt' AND app_my_role(household_id) IN ('owner', 'admin')));
    GRANT SELECT, INSERT, DELETE ON documents TO ft_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents, receipt_items, receipts")
