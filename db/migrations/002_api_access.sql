-- API access: per-clinic API keys, clinic creation, and the reminder outbox.
-- Everything here keeps the application role free of superuser and RLS bypass.

-- An appointment must be addressable together with its clinic (outbox foreign key)
ALTER TABLE appointments ADD CONSTRAINT appointments_id_clinic_key UNIQUE (id, clinic_id);

-- Only a hash of each key is stored. The application role has no direct access.
CREATE TABLE clinic_api_keys (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id  uuid NOT NULL REFERENCES clinics (id),
    key_hash   text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON clinic_api_keys FROM PUBLIC;

-- Look a clinic up by key hash. SECURITY DEFINER because the caller does not yet
-- know which clinic it acts for, so RLS could never let it read the table.
CREATE FUNCTION authenticate_clinic(p_key_hash text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$ SELECT clinic_id FROM clinic_api_keys WHERE key_hash = p_key_hash $$;

-- Create a clinic and its first key. The clinic policy has no WITH CHECK that
-- allows a new row, so the function acts as the new clinic for the insert.
CREATE FUNCTION create_clinic(p_name text, p_key_hash text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
DECLARE
    new_id uuid := gen_random_uuid();
BEGIN
    PERFORM set_config('app.clinic_id', new_id::text, true);
    INSERT INTO clinics (id, name) VALUES (new_id, p_name);
    INSERT INTO clinic_api_keys (clinic_id, key_hash) VALUES (new_id, p_key_hash);
    RETURN new_id;
END
$$;

REVOKE ALL ON FUNCTION authenticate_clinic(text), create_clinic(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION authenticate_clinic(text), create_clinic(text, text) TO clinicflow_app;

-- Reminder outbox: one row per appointment. A separate dispatcher turns due rows
-- into scheduled reminders, so the API never needs AWS credentials.
CREATE TABLE reminders (
    appointment_id uuid PRIMARY KEY,
    clinic_id      uuid NOT NULL REFERENCES clinics (id),
    remind_at      timestamptz NOT NULL,
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'scheduled', 'cancelled')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (appointment_id, clinic_id) REFERENCES appointments (id, clinic_id)
);

CREATE INDEX ON reminders (status, remind_at);

ALTER TABLE reminders ENABLE ROW LEVEL SECURITY;
ALTER TABLE reminders FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON reminders
    USING (clinic_id = current_clinic_id())
    WITH CHECK (clinic_id = current_clinic_id());

GRANT SELECT, INSERT, UPDATE ON reminders TO clinicflow_app;
