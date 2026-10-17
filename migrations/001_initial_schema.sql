-- ─────────────────────────────────────────────────────────────────────────────
-- Assessment Platform – initial schema
-- Run in: Supabase Dashboard → SQL Editor → New query → paste → Run
-- Safe to re-run (uses IF NOT EXISTS / OR REPLACE).
-- ─────────────────────────────────────────────────────────────────────────────

-- ── users ────────────────────────────────────────────────────────────────────
create table if not exists public.users (
    id                       uuid primary key default gen_random_uuid(),
    email                    text not null unique,
    full_name                text not null,
    password_hash            text not null,
    role                     text not null default 'applicant'
                                 check (role in ('applicant', 'admin')),
    email_verified           boolean not null default false,
    is_active                boolean not null default true,
    verification_token_hash  text,
    verification_expires     timestamptz,
    reset_token_hash         text,
    reset_expires            timestamptz,
    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now()
);
create index if not exists users_verification_token_idx on public.users (verification_token_hash);
create index if not exists users_reset_token_idx        on public.users (reset_token_hash);

-- ── applications (one per applicant) ────────────────────────────────────────
create table if not exists public.applications (
    id                    uuid primary key default gen_random_uuid(),
    user_id               uuid not null unique references public.users (id) on delete cascade,
    reference_number      text not null unique,

    -- personal / contact
    full_name             text not null,
    email                 text not null,
    phone                 text default '',
    date_of_birth         date,

    -- address
    address               text not null,
    city                  text default '',
    state                 text default '',
    postal_code           text default '',
    country               text default '',

    -- education
    education             text not null,
    institution           text default '',
    field_of_study        text default '',
    graduation_year       integer,

    -- experience
    experience_level      text not null,
    current_employer      text default '',
    job_title             text default '',
    summary               text default '',

    -- resume (stored privately; only a generated file name is kept here)
    resume_filename       text,
    resume_original_name  text,

    -- payment (always verified manually by an admin)
    payment_status        text not null default 'unpaid'
                              check (payment_status in ('unpaid', 'pending', 'verified', 'rejected')),
    transaction_id        text unique,
    payment_amount        numeric(12, 2),
    payment_submitted_at  timestamptz,

    -- review
    application_status    text not null default 'draft'
                              check (application_status in
                                     ('draft', 'under_review', 'shortlisted', 'accepted', 'rejected')),
    admin_notes           text default '',
    submitted_at          timestamptz,

    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);
create index if not exists applications_payment_status_idx     on public.applications (payment_status);
create index if not exists applications_application_status_idx on public.applications (application_status);
create index if not exists applications_created_at_idx         on public.applications (created_at desc);

-- ── audit_log ────────────────────────────────────────────────────────────────
create table if not exists public.audit_log (
    id           uuid primary key default gen_random_uuid(),
    admin_id     uuid references public.users (id) on delete set null,
    action       text not null,
    target_type  text,
    target_id    text,
    details      jsonb not null default '{}'::jsonb,
    ip_address   text,
    created_at   timestamptz not null default now()
);
create index if not exists audit_log_created_at_idx on public.audit_log (created_at desc);
create index if not exists audit_log_target_idx     on public.audit_log (target_id);

-- ── keep updated_at fresh ────────────────────────────────────────────────────
create or replace function public.set_updated_at() returns trigger
language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists users_set_updated_at on public.users;
create trigger users_set_updated_at before update on public.users
    for each row execute function public.set_updated_at();

drop trigger if exists applications_set_updated_at on public.applications;
create trigger applications_set_updated_at before update on public.applications
    for each row execute function public.set_updated_at();

-- ── Row Level Security ───────────────────────────────────────────────────────
-- The backend uses the service-role key, which bypasses RLS. With RLS enabled and
-- NO policies, the public anon/authenticated API roles can read or write nothing.
alter table public.users        enable row level security;
alter table public.applications enable row level security;
alter table public.audit_log    enable row level security;

revoke all on public.users        from anon, authenticated;
revoke all on public.applications from anon, authenticated;
revoke all on public.audit_log    from anon, authenticated;
