-- MF CRM database schema for Supabase (PostgreSQL).
-- Run once in the Supabase SQL editor. Safe to run again: everything is
-- created only if missing, and functions and views are replaced.
--
-- Who can see what:
--   * Only email addresses listed in app_users can read anything.
--   * Data is written only by the importer, which uses the service_role key.

------------------------------------------------------------------------------
-- Login allow-list
------------------------------------------------------------------------------
create table if not exists app_users (
  email text primary key,
  name  text
);

-- auth.jwt() exists on Supabase. The local test setup creates a stub.
create or replace function is_app_user() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from app_users
    where lower(email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  );
$$;

------------------------------------------------------------------------------
-- Reference data
------------------------------------------------------------------------------
create table if not exists arns (
  arn  text primary key,
  name text
);

-- KFintech transaction types: purred P adds units, R removes units.
create table if not exists tr_types (
  code        text primary key,
  description text,
  trn_mode    text,   -- N normal, R rejection/reversal
  tr_flag     text,
  purred      text    -- P, R or D
);

create table if not exists amc_codes (
  registrar text not null,
  amc_code  text not null,
  amc_name  text,
  primary key (registrar, amc_code)
);

------------------------------------------------------------------------------
-- Folios: one row per registrar + folio + scheme, latest details win
------------------------------------------------------------------------------
create table if not exists folios (
  registrar        text not null check (registrar in ('CAMS', 'KFIN')),
  folio_no         text not null,
  product_code     text not null,
  amc_code         text,
  scheme_name      text,
  pan              text,
  inv_name         text,
  joint1_name      text,
  joint2_name      text,
  joint1_pan       text,
  joint2_pan       text,
  guardian_name    text,
  guardian_pan     text,
  holding_mode     text,
  tax_status       text,
  dob              date,
  mobile           text,
  email            text,
  phone_res        text,
  phone_off        text,
  address1         text,
  address2         text,
  address3         text,
  city             text,
  pincode          text,
  state            text,
  country          text,
  occupation       text,
  bank_name        text,
  bank_branch      text,
  bank_ac_type     text,
  bank_ac_no       text,
  bank_ifsc        text,
  bank_city        text,
  nominee1_name    text,
  nominee1_relation text,
  nominee1_pct     numeric,
  nominee2_name    text,
  nominee2_relation text,
  nominee2_pct     numeric,
  nominee3_name    text,
  nominee3_relation text,
  nominee3_pct     numeric,
  arn              text,
  sub_broker       text,
  units_reported   numeric,
  value_reported   numeric,
  report_date      date,
  folio_date       date,
  source_file      text,
  raw              jsonb,
  updated_at       timestamptz not null default now(),
  primary key (registrar, folio_no, product_code)
);
create index if not exists folios_pan_idx on folios (pan);
create index if not exists folios_name_idx on folios (lower(inv_name));
create index if not exists folios_mobile_idx on folios (mobile);

------------------------------------------------------------------------------
-- Transactions: append-only, duplicates skipped by dedup_key
------------------------------------------------------------------------------
create table if not exists transactions (
  id           bigserial primary key,
  dedup_key    text not null unique,
  registrar    text not null check (registrar in ('CAMS', 'KFIN')),
  amc_code     text,
  folio_no     text not null,
  product_code text not null,
  scheme_name  text,
  inv_name     text,
  pan          text,
  trxn_type    text,
  trxn_desc    text,
  trxn_no      text,
  trxn_mode    text,
  trxn_date    date,
  post_date    date,
  units        numeric,          -- as supplied by the registrar
  amount       numeric,
  price        numeric,
  sign         smallint,         -- +1 adds units, -1 removes, 0 no effect
  signed_units numeric,          -- units with the sign applied
  is_reversal  boolean not null default false,
  arn          text,
  sub_broker   text,
  euin         text,
  report_date  date,
  source_file  text,
  raw          jsonb,
  created_at   timestamptz not null default now()
);
create index if not exists txn_folio_idx
  on transactions (registrar, folio_no, product_code, trxn_date);
create index if not exists txn_pan_idx on transactions (pan);
create index if not exists txn_date_idx on transactions (trxn_date);

------------------------------------------------------------------------------
-- Holdings: recalculated by the importer from transactions (FIFO cost)
------------------------------------------------------------------------------
create table if not exists holdings (
  registrar       text not null,
  folio_no        text not null,
  product_code    text not null,
  units           numeric not null,
  cost_value      numeric not null,
  invested_total  numeric,
  withdrawn_total numeric,
  first_txn_date  date,
  last_txn_date   date,
  arn_latest      text,
  computed_at     timestamptz not null default now(),
  primary key (registrar, folio_no, product_code)
);

------------------------------------------------------------------------------
-- NAVs by registrar product code
------------------------------------------------------------------------------
create table if not exists navs (
  registrar    text not null,
  product_code text not null,
  nav_date     date not null,
  nav          numeric not null,
  isin         text,
  source       text,
  primary key (registrar, product_code, nav_date)
);

------------------------------------------------------------------------------
-- CRM features: families and notes
------------------------------------------------------------------------------
create table if not exists families (
  id         bigserial primary key,
  name       text not null,
  created_at timestamptz not null default now()
);

create table if not exists family_members (
  pan       text primary key,
  family_id bigint not null references families (id) on delete cascade
);

create table if not exists client_notes (
  id         bigserial primary key,
  pan        text not null,
  note       text not null,
  created_by text,
  created_at timestamptz not null default now()
);

------------------------------------------------------------------------------
-- Import log
------------------------------------------------------------------------------
create table if not exists import_log (
  id          bigserial primary key,
  file_name   text not null,
  report_type text,
  rows_read   integer,
  inserted    integer,
  updated     integer,
  skipped     integer,
  status      text not null,
  message     text,
  started_at  timestamptz not null default now(),
  finished_at timestamptz
);

------------------------------------------------------------------------------
-- Views
------------------------------------------------------------------------------
create or replace view latest_navs as
select distinct on (registrar, product_code)
       registrar, product_code, nav_date, nav
from navs
order by registrar, product_code, nav_date desc;

-- Holding value: latest NAV if we have one, otherwise the NAV implied by the
-- registrar's own reported value.
create or replace view holding_values as
select h.registrar, h.folio_no, h.product_code,
       f.amc_code, f.scheme_name, f.pan, f.inv_name, coalesce(f.arn, h.arn_latest) as arn,
       h.units, h.cost_value, h.invested_total, h.withdrawn_total,
       h.first_txn_date, h.last_txn_date,
       f.units_reported, f.value_reported, f.report_date,
       coalesce(n.nav,
                case when f.units_reported > 0 then f.value_reported / f.units_reported end) as nav,
       coalesce(n.nav_date, f.report_date) as nav_date,
       round(h.units * coalesce(n.nav,
                case when f.units_reported > 0 then f.value_reported / f.units_reported end), 2)
         as market_value
from holdings h
left join folios f using (registrar, folio_no, product_code)
left join latest_navs n using (registrar, product_code);

-- One row per client (PAN). Contact details come from the most recently
-- reported folio.
create or replace view clients as
with ranked as (
  select f.*, row_number() over (partition by pan order by report_date desc nulls last,
                                  updated_at desc) as rn
  from folios f
  where pan is not null and pan <> ''
),
money as (
  select pan, sum(market_value) as market_value, sum(cost_value) as cost_value,
         sum(cost_value) filter (where market_value is not null) as valued_cost,
         count(*) filter (where units > 0.001) as active_holdings,
         count(*) filter (where units > 0.001 and market_value is null) as unvalued_holdings
  from holding_values group by pan
)
select r.pan, r.inv_name, r.dob, r.mobile, r.email, r.city, r.pincode, r.state,
       r.occupation, r.tax_status,
       (select array_agg(distinct f2.arn) from folios f2 where f2.pan = r.pan) as arns,
       (select count(*) from folios f2 where f2.pan = r.pan) as folio_rows,
       coalesce(m.active_holdings, 0) as active_holdings,
       coalesce(m.market_value, 0) as market_value,
       coalesce(m.cost_value, 0) as cost_value,
       fm.family_id,
       coalesce(m.valued_cost, 0) as valued_cost,
       coalesce(m.unvalued_holdings, 0) as unvalued_holdings
from ranked r
left join money m using (pan)
left join family_members fm using (pan)
where r.rn = 1;

-- Transactions with scheme, client and PAN filled in from the folio when the
-- registrar file left them out (CAMS full history has no scheme or PAN column).
create or replace view transactions_full as
select t.id, t.registrar, t.folio_no, t.product_code, t.trxn_date, t.post_date,
       t.trxn_type, t.trxn_desc, t.trxn_no, t.units, t.amount, t.price, t.sign,
       t.signed_units, t.is_reversal, t.arn,
       coalesce(nullif(t.scheme_name, ''), f.scheme_name) as scheme_name,
       coalesce(nullif(t.inv_name, ''), f.inv_name) as inv_name,
       coalesce(nullif(t.pan, ''), f.pan) as pan
from transactions t
left join folios f using (registrar, folio_no, product_code);

-- Which ARN each folio and scheme was transacted under, by year.
create or replace view arn_history as
select registrar, folio_no, product_code,
       extract(year from trxn_date)::int as year,
       arn,
       count(*) as transactions,
       min(trxn_date) as first_date,
       max(trxn_date) as last_date
from transactions
where trxn_date is not null
group by registrar, folio_no, product_code, extract(year from trxn_date), arn;

-- Folios with details missing, for follow-up.
create or replace view missing_details as
select f.registrar, f.folio_no, f.product_code, f.scheme_name, f.pan, f.inv_name, f.arn,
       (coalesce(f.nominee1_name, '') = '') as no_nominee,
       (coalesce(f.email, '') = '')         as no_email,
       (coalesce(f.mobile, '') = '')        as no_mobile,
       (f.dob is null)                      as no_dob,
       (coalesce(f.pan, '') = '')           as no_pan
from folios f
join holdings h using (registrar, folio_no, product_code)
where h.units > 0.001
  and (coalesce(f.nominee1_name, '') = '' or coalesce(f.email, '') = ''
       or coalesce(f.mobile, '') = '' or f.dob is null or coalesce(f.pan, '') = '');

------------------------------------------------------------------------------
-- Importer functions (called with the service_role key)
------------------------------------------------------------------------------

-- Insert or update folios. A row only replaces an existing one if its report
-- date is the same or newer. A column the file did not have arrives as null
-- and keeps the stored value.
create or replace function ingest_folios(rows jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  n_ins int := 0;
  n_upd int := 0;
  n_all int := jsonb_array_length(rows);
begin
  with incoming as (
    select * from jsonb_populate_recordset(null::folios, rows)
  ), up as (
    insert into folios as f (
      registrar, folio_no, product_code, amc_code, scheme_name, pan, inv_name,
      joint1_name, joint2_name, joint1_pan, joint2_pan, guardian_name, guardian_pan,
      holding_mode, tax_status, dob, mobile, email, phone_res, phone_off,
      address1, address2, address3, city, pincode, state, country, occupation,
      bank_name, bank_branch, bank_ac_type, bank_ac_no, bank_ifsc, bank_city,
      nominee1_name, nominee1_relation, nominee1_pct,
      nominee2_name, nominee2_relation, nominee2_pct,
      nominee3_name, nominee3_relation, nominee3_pct,
      arn, sub_broker, units_reported, value_reported, report_date, folio_date,
      source_file, raw, updated_at)
    select
      registrar, folio_no, product_code, amc_code, scheme_name, pan, inv_name,
      joint1_name, joint2_name, joint1_pan, joint2_pan, guardian_name, guardian_pan,
      holding_mode, tax_status, dob, mobile, email, phone_res, phone_off,
      address1, address2, address3, city, pincode, state, country, occupation,
      bank_name, bank_branch, bank_ac_type, bank_ac_no, bank_ifsc, bank_city,
      nominee1_name, nominee1_relation, nominee1_pct,
      nominee2_name, nominee2_relation, nominee2_pct,
      nominee3_name, nominee3_relation, nominee3_pct,
      arn, sub_broker, units_reported, value_reported, report_date, folio_date,
      source_file, raw, now()
    from incoming
    on conflict (registrar, folio_no, product_code) do update set
      amc_code          = coalesce(excluded.amc_code, f.amc_code),
      scheme_name       = coalesce(excluded.scheme_name, f.scheme_name),
      pan               = coalesce(excluded.pan, f.pan),
      inv_name          = coalesce(excluded.inv_name, f.inv_name),
      joint1_name       = coalesce(excluded.joint1_name, f.joint1_name),
      joint2_name       = coalesce(excluded.joint2_name, f.joint2_name),
      joint1_pan        = coalesce(excluded.joint1_pan, f.joint1_pan),
      joint2_pan        = coalesce(excluded.joint2_pan, f.joint2_pan),
      guardian_name     = coalesce(excluded.guardian_name, f.guardian_name),
      guardian_pan      = coalesce(excluded.guardian_pan, f.guardian_pan),
      holding_mode      = coalesce(excluded.holding_mode, f.holding_mode),
      tax_status        = coalesce(excluded.tax_status, f.tax_status),
      dob               = coalesce(excluded.dob, f.dob),
      mobile            = coalesce(excluded.mobile, f.mobile),
      email             = coalesce(excluded.email, f.email),
      phone_res         = coalesce(excluded.phone_res, f.phone_res),
      phone_off         = coalesce(excluded.phone_off, f.phone_off),
      address1          = coalesce(excluded.address1, f.address1),
      address2          = coalesce(excluded.address2, f.address2),
      address3          = coalesce(excluded.address3, f.address3),
      city              = coalesce(excluded.city, f.city),
      pincode           = coalesce(excluded.pincode, f.pincode),
      state             = coalesce(excluded.state, f.state),
      country           = coalesce(excluded.country, f.country),
      occupation        = coalesce(excluded.occupation, f.occupation),
      bank_name         = coalesce(excluded.bank_name, f.bank_name),
      bank_branch       = coalesce(excluded.bank_branch, f.bank_branch),
      bank_ac_type      = coalesce(excluded.bank_ac_type, f.bank_ac_type),
      bank_ac_no        = coalesce(excluded.bank_ac_no, f.bank_ac_no),
      bank_ifsc         = coalesce(excluded.bank_ifsc, f.bank_ifsc),
      bank_city         = coalesce(excluded.bank_city, f.bank_city),
      nominee1_name     = coalesce(excluded.nominee1_name, f.nominee1_name),
      nominee1_relation = coalesce(excluded.nominee1_relation, f.nominee1_relation),
      nominee1_pct      = coalesce(excluded.nominee1_pct, f.nominee1_pct),
      nominee2_name     = coalesce(excluded.nominee2_name, f.nominee2_name),
      nominee2_relation = coalesce(excluded.nominee2_relation, f.nominee2_relation),
      nominee2_pct      = coalesce(excluded.nominee2_pct, f.nominee2_pct),
      nominee3_name     = coalesce(excluded.nominee3_name, f.nominee3_name),
      nominee3_relation = coalesce(excluded.nominee3_relation, f.nominee3_relation),
      nominee3_pct      = coalesce(excluded.nominee3_pct, f.nominee3_pct),
      arn               = coalesce(excluded.arn, f.arn),
      sub_broker        = coalesce(excluded.sub_broker, f.sub_broker),
      units_reported    = coalesce(excluded.units_reported, f.units_reported),
      value_reported    = coalesce(excluded.value_reported, f.value_reported),
      report_date       = coalesce(excluded.report_date, f.report_date),
      folio_date        = coalesce(excluded.folio_date, f.folio_date),
      source_file       = excluded.source_file,
      raw               = coalesce(f.raw, '{}'::jsonb) || coalesce(excluded.raw, '{}'::jsonb),
      updated_at        = now()
    where excluded.report_date is null
       or f.report_date is null
       or excluded.report_date >= f.report_date
    returning (xmax = 0) as inserted
  )
  select count(*) filter (where inserted), count(*) filter (where not inserted)
    into n_ins, n_upd
  from up;

  return jsonb_build_object('inserted', n_ins, 'updated', n_upd,
                            'skipped', n_all - n_ins - n_upd);
end;
$$;

-- Insert transactions; rows whose dedup_key already exists are skipped.
create or replace function ingest_transactions(rows jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  n_ins int := 0;
  n_all int := jsonb_array_length(rows);
begin
  with ins as (
    insert into transactions (
      dedup_key, registrar, amc_code, folio_no, product_code, scheme_name, inv_name,
      pan, trxn_type, trxn_desc, trxn_no, trxn_mode, trxn_date, post_date, units,
      amount, price, sign, signed_units, is_reversal, arn, sub_broker, euin,
      report_date, source_file, raw)
    select
      dedup_key, registrar, amc_code, folio_no, product_code, scheme_name, inv_name,
      pan, trxn_type, trxn_desc, trxn_no, trxn_mode, trxn_date, post_date, units,
      amount, price, sign, signed_units, coalesce(is_reversal, false), arn, sub_broker,
      euin, report_date, source_file, raw
    from jsonb_populate_recordset(null::transactions, rows)
    on conflict (dedup_key) do nothing
    returning 1
  )
  select count(*) into n_ins from ins;

  return jsonb_build_object('inserted', n_ins, 'updated', 0, 'skipped', n_all - n_ins);
end;
$$;

-- Transactions for a set of folio keys ("REGISTRAR|folio|product"), oldest first,
-- so the importer can recalculate holdings for just those folios.
create or replace function transactions_for_keys(keys text[]) returns setof transactions
language sql stable security definer set search_path = public as $$
  select t.* from transactions t
  where (t.registrar || '|' || t.folio_no || '|' || t.product_code) = any (keys)
  order by t.registrar, t.folio_no, t.product_code, t.trxn_date, t.post_date nulls last, t.id;
$$;

create or replace function upsert_holdings(rows jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  insert into holdings as h (registrar, folio_no, product_code, units, cost_value,
      invested_total, withdrawn_total, first_txn_date, last_txn_date, arn_latest,
      computed_at)
  select registrar, folio_no, product_code, units, cost_value, invested_total,
         withdrawn_total, first_txn_date, last_txn_date, arn_latest, now()
  from jsonb_populate_recordset(null::holdings, rows)
  on conflict (registrar, folio_no, product_code) do update set
    units = excluded.units, cost_value = excluded.cost_value,
    invested_total = excluded.invested_total, withdrawn_total = excluded.withdrawn_total,
    first_txn_date = excluded.first_txn_date, last_txn_date = excluded.last_txn_date,
    arn_latest = excluded.arn_latest, computed_at = now();
  get diagnostics n = row_count;
  return jsonb_build_object('upserted', n);
end;
$$;

create or replace function ingest_navs(rows jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  insert into navs as v (registrar, product_code, nav_date, nav, isin, source)
  select registrar, product_code, nav_date, nav, isin, source
  from jsonb_populate_recordset(null::navs, rows)
  where nav is not null and nav_date is not null
  on conflict (registrar, product_code, nav_date) do update set
    nav = excluded.nav, isin = coalesce(excluded.isin, v.isin), source = excluded.source;
  get diagnostics n = row_count;
  return jsonb_build_object('upserted', n);
end;
$$;

------------------------------------------------------------------------------
-- Security: row level security and grants
------------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['app_users', 'arns', 'tr_types', 'amc_codes', 'folios',
                           'transactions', 'holdings', 'navs', 'families',
                           'family_members', 'client_notes', 'import_log'] loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists app_read on %I', t);
    execute format('create policy app_read on %I for select using (is_app_user())', t);
  end loop;

  -- The three users can manage families and notes themselves.
  foreach t in array array['families', 'family_members', 'client_notes'] loop
    execute format('drop policy if exists app_write on %I', t);
    execute format('create policy app_write on %I for all using (is_app_user()) '
                   'with check (is_app_user())', t);
  end loop;
end $$;

-- Views run with the permissions of the person asking, so row level security
-- on the tables still applies.
alter view latest_navs      set (security_invoker = true);
alter view holding_values   set (security_invoker = true);
alter view clients          set (security_invoker = true);
alter view arn_history      set (security_invoker = true);
alter view missing_details  set (security_invoker = true);

-- Only the importer may call the write functions.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke execute on function ingest_folios(jsonb)          from public, anon, authenticated;
    revoke execute on function ingest_transactions(jsonb)    from public, anon, authenticated;
    revoke execute on function upsert_holdings(jsonb)        from public, anon, authenticated;
    revoke execute on function ingest_navs(jsonb)            from public, anon, authenticated;
    revoke execute on function transactions_for_keys(text[]) from public, anon, authenticated;
    grant execute on function ingest_folios(jsonb)          to service_role;
    grant execute on function ingest_transactions(jsonb)    to service_role;
    grant execute on function upsert_holdings(jsonb)        to service_role;
    grant execute on function ingest_navs(jsonb)            to service_role;
    grant execute on function transactions_for_keys(text[]) to service_role;
  end if;
end $$;

------------------------------------------------------------------------------
-- File inbox (Supabase Storage): signed-in users may upload report files
------------------------------------------------------------------------------
do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'storage') then
    insert into storage.buckets (id, name, public)
    values ('inbox', 'inbox', false)
    on conflict (id) do nothing;

    drop policy if exists inbox_upload on storage.objects;
    create policy inbox_upload on storage.objects for insert to authenticated
      with check (bucket_id = 'inbox' and public.is_app_user());

    drop policy if exists inbox_read on storage.objects;
    create policy inbox_read on storage.objects for select to authenticated
      using (bucket_id = 'inbox' and public.is_app_user());
  end if;
end $$;

------------------------------------------------------------------------------
-- Seed data
------------------------------------------------------------------------------
insert into arns (arn, name) values
  ('ARN-283370', 'Ribhav Ostwal'),
  ('ARN-172637', 'Gaurav Ostwal'),
  ('ARN-13732',  null)
on conflict (arn) do nothing;

------------------------------------------------------------------------------
-- Reconciliation: units from transactions vs units the registrar reports
------------------------------------------------------------------------------
create or replace view reconciliation as
select coalesce(h.registrar, f.registrar) as registrar,
       coalesce(h.folio_no, f.folio_no) as folio_no,
       coalesce(h.product_code, f.product_code) as product_code,
       f.scheme_name, f.inv_name, f.pan,
       h.units as units_from_transactions,
       f.units_reported,
       round(coalesce(h.units, 0) - coalesce(f.units_reported, 0), 3) as difference,
       f.report_date, h.last_txn_date
from holdings h
full join folios f using (registrar, folio_no, product_code)
where f.units_reported is not null;
alter view reconciliation set (security_invoker = true);
alter view transactions_full set (security_invoker = true);
