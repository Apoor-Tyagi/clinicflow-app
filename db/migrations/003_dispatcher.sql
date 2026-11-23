-- Reminder dispatcher: a separate role that turns queued reminders into schedules.
-- It must read across clinics, so it gets its own role with narrow, read-mostly
-- grants and its own row-level security policies. It does not bypass RLS.

ALTER TABLE reminders
    ADD COLUMN scheduled_at timestamptz,
    ADD COLUMN released_at  timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'clinicflow_dispatcher') THEN
        CREATE ROLE clinicflow_dispatcher LOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO clinicflow_dispatcher;

-- Only what it needs to build a reminder: the visit time and the patient's email.
GRANT SELECT (id, clinic_id, patient_id, starts_at) ON appointments TO clinicflow_dispatcher;
GRANT SELECT (id, clinic_id, email)                ON patients     TO clinicflow_dispatcher;
GRANT SELECT ON reminders TO clinicflow_dispatcher;
GRANT UPDATE (status, scheduled_at, released_at) ON reminders TO clinicflow_dispatcher;

CREATE POLICY dispatcher_read ON appointments FOR SELECT TO clinicflow_dispatcher USING (true);
CREATE POLICY dispatcher_read ON patients     FOR SELECT TO clinicflow_dispatcher USING (true);
CREATE POLICY dispatcher_read ON reminders    FOR SELECT TO clinicflow_dispatcher USING (true);
CREATE POLICY dispatcher_update ON reminders  FOR UPDATE TO clinicflow_dispatcher
    USING (true) WITH CHECK (true);
