-- Tenant model with row-level security (RLS)

CREATE TABLE clinics (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE patients (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id  uuid NOT NULL REFERENCES clinics (id),
    full_name  text NOT NULL,
    phone      text,
    email      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, clinic_id)
);

CREATE TABLE appointments (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id  uuid NOT NULL REFERENCES clinics (id),
    patient_id uuid NOT NULL,
    starts_at  timestamptz NOT NULL,
    status     text NOT NULL DEFAULT 'booked'
               CHECK (status IN ('booked', 'completed', 'no_show', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    -- an appointment can only reference a patient of the SAME clinic
    FOREIGN KEY (patient_id, clinic_id) REFERENCES patients (id, clinic_id)
);

CREATE INDEX ON patients (clinic_id);
CREATE INDEX ON appointments (clinic_id, starts_at);

-- The clinic the current transaction acts for. NULL when it was never set.
CREATE FUNCTION current_clinic_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.clinic_id', true), '')::uuid $$;

ALTER TABLE clinics      ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinics      FORCE ROW LEVEL SECURITY;
ALTER TABLE patients     ENABLE ROW LEVEL SECURITY;
ALTER TABLE patients     FORCE ROW LEVEL SECURITY;
ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON clinics
    USING (id = current_clinic_id());
CREATE POLICY tenant_isolation ON patients
    USING (clinic_id = current_clinic_id())
    WITH CHECK (clinic_id = current_clinic_id());
CREATE POLICY tenant_isolation ON appointments
    USING (clinic_id = current_clinic_id())
    WITH CHECK (clinic_id = current_clinic_id());

-- The application role: no superuser, no RLS bypass, no password here
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'clinicflow_app') THEN
        CREATE ROLE clinicflow_app LOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO clinicflow_app;
GRANT SELECT ON clinics TO clinicflow_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON patients, appointments TO clinicflow_app;
