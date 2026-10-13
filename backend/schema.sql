-- Bella Vista: Supabase tables
-- Run this in the Supabase SQL editor. Safe to re-run (uses IF NOT EXISTS).
-- Columns match what backend/db.py reads and writes.

create table if not exists reservations (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  phone         text,
  date          date not null,
  time          time not null,
  party_size    int  not null,
  tables_needed int  not null,
  created_at    timestamptz default now()
);

create table if not exists callback_requests (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  phone      text not null,
  status     text default 'pending',
  created_at timestamptz default now()
);

-- Holds the admin password (stored as a salted hash once changed from the dashboard).
create table if not exists settings (
  key   text primary key,
  value text
);

-- Lock the tables down. With RLS enabled and no policies, the public (anon/publishable)
-- keys get no access at all. The backend uses the secret key, which bypasses RLS.
alter table reservations      enable row level security;
alter table callback_requests enable row level security;
alter table settings          enable row level security;
