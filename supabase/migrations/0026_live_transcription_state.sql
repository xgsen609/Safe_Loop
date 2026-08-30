-- Keep short-lived Gemini Live handoff state shared across Cloud Run instances.
-- Raw bearer tickets and session identifiers are never stored; only SHA-256 hashes.

create table public.live_transcription_tickets (
  token_hash text primary key check (token_hash ~ '^[0-9a-f]{64}$'),
  actor_id uuid not null references public.profiles(id) on delete cascade,
  hint_locale text not null check (hint_locale in ('zh-CN', 'cmn-Hans-CN', 'en-SG')),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  check (expires_at > created_at)
);

create index live_transcription_tickets_expires_at
  on public.live_transcription_tickets (expires_at);

create table public.live_transcription_sessions (
  session_hash text primary key check (session_hash ~ '^[0-9a-f]{64}$'),
  actor_id uuid not null references public.profiles(id) on delete cascade,
  hint_locale text not null check (hint_locale in ('zh-CN', 'cmn-Hans-CN', 'en-SG')),
  text_raw text not null check (btrim(text_raw) <> ''),
  detected_locale text not null,
  duration_ms integer not null check (duration_ms >= 0),
  provider_ref text not null,
  latency_ms integer not null check (latency_ms >= 0),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  check (expires_at > created_at)
);

create index live_transcription_sessions_expires_at
  on public.live_transcription_sessions (expires_at);

alter table public.live_transcription_tickets enable row level security;
alter table public.live_transcription_sessions enable row level security;
revoke all privileges on public.live_transcription_tickets from public, anon, authenticated;
revoke all privileges on public.live_transcription_sessions from public, anon, authenticated;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant all privileges on public.live_transcription_tickets to service_role';
    execute 'grant all privileges on public.live_transcription_sessions to service_role';
  end if;
end $$;
