create extension if not exists pgcrypto;

create table if not exists public.users (
    id uuid primary key default gen_random_uuid(),
    telegram_id bigint unique not null,
    username text,
    first_name text,
    last_name text,
    is_banned boolean not null default false,
    created_at timestamptz not null default now(),
    last_activity timestamptz not null default now()
);

create index if not exists users_telegram_id_idx on public.users(telegram_id);

create table if not exists public.demo_users (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    signals_received integer not null default 0,
    active boolean not null default true,
    started_at timestamptz not null default now(),
    completed_at timestamptz
);

create unique index if not exists demo_active_user_idx
on public.demo_users(user_id)
where active = true;

create table if not exists public.subscriptions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    plan_code text not null,
    payment_method text not null,
    starts_at timestamptz not null,
    expires_at timestamptz not null,
    status text not null default 'active',
    invite_link text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists subscriptions_user_idx on public.subscriptions(user_id);
create index if not exists subscriptions_expiry_idx on public.subscriptions(expires_at);

create table if not exists public.payments (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    plan_code text not null,
    payment_method text not null,
    amount numeric not null,
    currency text not null,
    status text not null default 'pending',
    telegram_charge_id text,
    cryptobot_invoice_id bigint,
    payload text,
    created_at timestamptz not null default now(),
    paid_at timestamptz
);

create unique index if not exists payments_telegram_charge_unique
on public.payments(telegram_charge_id)
where telegram_charge_id is not null;

create unique index if not exists payments_crypto_invoice_unique
on public.payments(cryptobot_invoice_id)
where cryptobot_invoice_id is not null;

create table if not exists public.support_threads (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    status text not null default 'open',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.support_messages (
    id uuid primary key default gen_random_uuid(),
    thread_id uuid not null references public.support_threads(id) on delete cascade,
    user_id uuid not null references public.users(id) on delete cascade,
    direction text not null,
    message_type text,
    text text,
    telegram_message_id bigint,
    created_at timestamptz not null default now()
);

create table if not exists public.broadcasts (
    id uuid primary key default gen_random_uuid(),
    admin_username text,
    text text,
    sent integer not null default 0,
    failed integer not null default 0,
    created_at timestamptz not null default now()
);

create table if not exists public.promo_codes (
    id uuid primary key default gen_random_uuid(),
    code text unique not null,
    discount_percent integer not null default 0,
    max_uses integer,
    used_count integer not null default 0,
    expires_at timestamptz,
    active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.user_promos (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.users(id) on delete cascade,
    promo_id uuid not null references public.promo_codes(id) on delete cascade,
    used_at timestamptz not null default now(),
    unique(user_id, promo_id)
);

-- The bot uses the Supabase service-role key server-side.
-- RLS can be enabled later if client-side access is introduced.
