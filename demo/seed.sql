CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE meta.tenant (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE meta.plan_catalog (
    code text PRIMARY KEY,
    monthly_price numeric NOT NULL
);

DO $$
DECLARE
    tenant_ids text[] := ARRAY[
        'a1c3e5f7-1111-4aaa-8bbb-000000000001',
        'a1c3e5f7-2222-4aaa-8bbb-000000000002',
        'a1c3e5f7-3333-4aaa-8bbb-000000000003',
        'a1c3e5f7-4444-4aaa-8bbb-000000000004',
        'a1c3e5f7-5555-4aaa-8bbb-000000000005'
    ];
    tenant_id text;
BEGIN
    FOREACH tenant_id IN ARRAY tenant_ids LOOP
        EXECUTE format('CREATE SCHEMA %I', tenant_id);
        EXECUTE format($fmt$
            CREATE TABLE %I.invoice (
                id uuid PRIMARY KEY,
                status varchar(32),
                amount numeric,
                created_at timestamptz NOT NULL DEFAULT now()
            )$fmt$, tenant_id);
        EXECUTE format($fmt$
            CREATE TABLE %I.subscription (
                id uuid PRIMARY KEY,
                plan varchar(32),
                seats integer
            )$fmt$, tenant_id);
        EXECUTE format('INSERT INTO meta.tenant (id, name) VALUES (%L, %L)',
                       tenant_id, 'tenant ' || left(tenant_id, 8));
    END LOOP;
END $$;

ALTER TABLE "a1c3e5f7-4444-4aaa-8bbb-000000000004".invoice DROP COLUMN status;
ALTER TABLE "a1c3e5f7-4444-4aaa-8bbb-000000000004".invoice ADD COLUMN status varchar(32);

ALTER TABLE "a1c3e5f7-5555-4aaa-8bbb-000000000005".invoice DROP COLUMN amount;
ALTER TABLE "a1c3e5f7-5555-4aaa-8bbb-000000000005".subscription
    ALTER COLUMN seats TYPE bigint;

CREATE SCHEMA "a1c3e5f7-6666-4aaa-8bbb-000000000006";

COMMENT ON TABLE meta.tenant IS 'One row per customer, the registry every tenant schema maps to.';
COMMENT ON COLUMN meta.tenant.name IS 'Display name, not unique and not an identifier.';

DO $$
DECLARE
    first_tenant text := 'a1c3e5f7-1111-4aaa-8bbb-000000000001';
BEGIN
    EXECUTE format('COMMENT ON TABLE %I.invoice IS %L', first_tenant,
                   'One row per issued invoice for this tenant.');
    EXECUTE format('COMMENT ON COLUMN %I.invoice.amount IS %L', first_tenant,
                   'Gross total, tax included.');
END $$;

-- A view, so the bundle has something it deliberately does not document and the
-- index has to say so. Real tenant schemas usually carry these.
CREATE VIEW meta.active_tenant AS
    SELECT id, name FROM meta.tenant WHERE created_at > now() - interval '90 days';
