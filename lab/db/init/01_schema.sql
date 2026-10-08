CREATE TABLE clinics (
    id          int PRIMARY KEY,
    name        text NOT NULL,
    suburb      text NOT NULL
);

CREATE TABLE patients (
    id               int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clinic_id        int NOT NULL REFERENCES clinics(id),
    full_name        text NOT NULL,
    dob              date NOT NULL,
    medicare_number  text NOT NULL,
    ihi              text NOT NULL,
    phone            text NOT NULL,
    email            text NOT NULL
);

CREATE TABLE appointments (
    id          int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clinic_id   int NOT NULL REFERENCES clinics(id),
    patient_id  int NOT NULL REFERENCES patients(id),
    starts_at   timestamptz NOT NULL,
    reason      text NOT NULL,
    status      text NOT NULL DEFAULT 'booked'
);

CREATE TABLE clinical_notes (
    id          int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clinic_id   int NOT NULL REFERENCES clinics(id),
    patient_id  int NOT NULL REFERENCES patients(id),
    created_at  timestamptz NOT NULL,
    note        text NOT NULL
);

CREATE SCHEMA lokra;
CREATE TABLE lokra.agent_scopes (
    role_name  text NOT NULL,
    clinic_id  int  NOT NULL,
    PRIMARY KEY (role_name, clinic_id)
);

-- session_user, not current_user: inside SECURITY DEFINER current_user is the owner.
CREATE FUNCTION lokra.agent_clinic_ids() RETURNS SETOF int
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = lokra, pg_temp
AS $$ SELECT clinic_id FROM lokra.agent_scopes WHERE role_name = session_user $$;
REVOKE ALL ON FUNCTION lokra.agent_clinic_ids() FROM PUBLIC;

ALTER TABLE clinics        ENABLE ROW LEVEL SECURITY;
ALTER TABLE patients       ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments   ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical_notes ENABLE ROW LEVEL SECURITY;

CREATE POLICY clinic_scope ON clinics        USING (id        IN (SELECT lokra.agent_clinic_ids()));
CREATE POLICY clinic_scope ON patients       USING (clinic_id IN (SELECT lokra.agent_clinic_ids()));
CREATE POLICY clinic_scope ON appointments   USING (clinic_id IN (SELECT lokra.agent_clinic_ids()));
CREATE POLICY clinic_scope ON clinical_notes USING (clinic_id IN (SELECT lokra.agent_clinic_ids()));

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
