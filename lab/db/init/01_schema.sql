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

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
