-- Mi Bank de Apuestas · base de datos
-- Pégalo en Supabase → SQL Editor → New query → Run.
-- Se puede ejecutar más de una vez sin romper nada.

create table if not exists public.bets (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  match       text not null check (char_length(btrim(match)) between 1 and 120),
  stake       numeric(12,2) not null check (stake > 0),
  odds        numeric(8,3)  not null check (odds > 1),
  bet_date    date not null default current_date,
  result      text not null default 'pendiente'
              check (result in ('pendiente', 'ganada', 'perdida', 'nula')),
  created_at  timestamptz not null default now()
);

create index if not exists bets_user_date_idx
  on public.bets (user_id, bet_date, created_at);

create table if not exists public.settings (
  user_id      uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  initial_bank numeric(12,2) not null default 1000 check (initial_bank > 0)
);

-- Seguridad: cada usuario solo ve y toca sus propias filas.
alter table public.bets     enable row level security;
alter table public.settings enable row level security;

drop policy if exists "bets: solo el dueño" on public.bets;
create policy "bets: solo el dueño" on public.bets
  for all to authenticated
  using      ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists "settings: solo el dueño" on public.settings;
create policy "settings: solo el dueño" on public.settings
  for all to authenticated
  using      ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

-- Permisos: sin sesión iniciada no se puede leer ni escribir nada.
revoke all on public.bets     from anon;
revoke all on public.settings from anon;
grant select, insert, update, delete on public.bets     to authenticated;
grant select, insert, update, delete on public.settings to authenticated;
grant all on public.bets     to service_role;  -- lo usa el informe mensual
grant all on public.settings to service_role;
