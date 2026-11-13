-- ============================================================
-- 家肴记 · Supabase 建表与 RLS 策略（可重复执行）
-- 项目：https://tjpjcmmcaortvubtnykm.supabase.co
-- 使用：Supabase Dashboard → SQL Editor → 全选粘贴执行
-- 说明：
--   1) families 表线上已存在，脚本用 if not exists / add column if not exists 做增量补齐
--   2) 家庭归属关系落到 family_members，RLS 全部基于「是否该家庭成员」
--   3) family_data 以 (family_id, key) 存原 localStorage 的业务键值，前端做写穿同步
-- ============================================================

create extension if not exists pgcrypto;

-- ---------------- profiles：用户资料（1:1 auth.users） ----------------
create table if not exists public.profiles (
    id           uuid primary key references auth.users(id) on delete cascade,
    email        text,
    display_name text,
    phone        text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

-- ---------------- families：家庭（已存在则只补列） ----------------
create table if not exists public.families (
    id           uuid primary key default gen_random_uuid(),
    family_name  text not null,
    family_code  text not null,
    owner_id     uuid references auth.users(id) on delete set null,
    owner_email  text,
    created_at   timestamptz not null default now()
);
alter table public.families add column if not exists family_name text;
alter table public.families add column if not exists family_code text;
alter table public.families add column if not exists owner_id uuid;
alter table public.families add column if not exists owner_email text;
alter table public.families add column if not exists created_at timestamptz default now();

-- 家庭码唯一（老数据若有重复会跳过，不影响后续执行）
do $$
begin
    create unique index if not exists families_family_code_key on public.families (family_code);
exception when others then
    raise notice 'families.family_code 存在重复值，未创建唯一索引：%', sqlerrm;
end $$;

-- ---------------- family_members：家庭成员（决定 RLS 可见性） ----------------
create table if not exists public.family_members (
    family_id  uuid not null references public.families(id) on delete cascade,
    user_id    uuid not null references auth.users(id) on delete cascade,
    role       text not null default 'member' check (role in ('owner', 'admin', 'member')),
    relation   text,                 -- 家庭内身份：爸爸/妈妈/儿子…
    joined_at  timestamptz not null default now(),
    primary key (family_id, user_id)
);
create index if not exists family_members_user_idx on public.family_members (user_id);

-- 存量家庭回填：创建者自动成为 owner 成员（否则老账号读不到自己的数据）
insert into public.family_members (family_id, user_id, role)
select f.id, f.owner_id, 'owner'
  from public.families f
 where f.owner_id is not null
on conflict (family_id, user_id) do nothing;

-- ---------------- family_data：家庭业务数据（原 localStorage 备份键） ----------------
create table if not exists public.family_data (
    family_id  uuid not null references public.families(id) on delete cascade,
    key        text not null,
    value      text,
    updated_by uuid default auth.uid(),
    updated_at timestamptz not null default now(),
    primary key (family_id, key)
);
create index if not exists family_data_family_idx on public.family_data (family_id);

-- ============================================================
-- 辅助函数（security definer 避免 RLS 递归；只暴露布尔结果）
-- ============================================================
create or replace function public.is_family_member(p_family uuid)
returns boolean
language sql
security definer
stable
set search_path = public
as $$
    select exists (
        select 1 from public.family_members m
         where m.family_id = p_family and m.user_id = auth.uid()
    ) or exists (
        select 1 from public.families f
         where f.id = p_family and f.owner_id = auth.uid()
    );
$$;

create or replace function public.is_family_owner(p_family uuid)
returns boolean
language sql
security definer
stable
set search_path = public
as $$
    select exists (
        select 1 from public.families f
         where f.id = p_family and f.owner_id = auth.uid()
    );
$$;

-- 家人之间可互相看到 profile 基础信息
create or replace function public.shares_family_with(p_user uuid)
returns boolean
language sql
security definer
stable
set search_path = public
as $$
    select exists (
        select 1
          from public.family_members me
          join public.family_members other on other.family_id = me.family_id
         where me.user_id = auth.uid() and other.user_id = p_user
    );
$$;

-- ============================================================
-- 触发器
-- ============================================================
-- 1) 新用户注册 → 自动建 profile
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id, email, phone, display_name)
    values (
        new.id,
        new.email,
        new.phone,
        coalesce(new.raw_user_meta_data->>'display_name', split_part(coalesce(new.email, ''), '@', 1))
    )
    on conflict (id) do update set email = excluded.email, phone = excluded.phone;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- 2) 新建家庭 → 创建者自动进入 family_members(owner)
create or replace function public.handle_new_family()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if new.owner_id is not null then
        insert into public.family_members (family_id, user_id, role)
        values (new.id, new.owner_id, 'owner')
        on conflict (family_id, user_id) do update set role = 'owner';
    end if;
    return new;
end;
$$;

drop trigger if exists on_family_created on public.families;
create trigger on_family_created
    after insert on public.families
    for each row execute function public.handle_new_family();

-- 3) updated_at 自动维护
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_family_data_touch on public.family_data;
create trigger trg_family_data_touch
    before update on public.family_data
    for each row execute function public.touch_updated_at();

drop trigger if exists trg_profiles_touch on public.profiles;
create trigger trg_profiles_touch
    before update on public.profiles
    for each row execute function public.touch_updated_at();

-- ============================================================
-- RPC：家人用「家庭码」加入家庭（绕过 families 的 select 限制）
-- ============================================================
create or replace function public.join_family_by_code(p_code text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    f record;
begin
    if auth.uid() is null then
        raise exception '请先登录';
    end if;

    select id, family_name, family_code
      into f
      from public.families
     where upper(family_code) = upper(trim(p_code))
     limit 1;

    if f.id is null then
        raise exception '家庭码不存在';
    end if;

    insert into public.family_members (family_id, user_id, role)
    values (f.id, auth.uid(), 'member')
    on conflict (family_id, user_id) do nothing;

    return jsonb_build_object(
        'family_id', f.id,
        'family_name', f.family_name,
        'family_code', f.family_code
    );
end;
$$;

revoke all on function public.join_family_by_code(text) from public;
grant execute on function public.join_family_by_code(text) to authenticated;

-- ============================================================
-- 开启 RLS + 策略
-- ============================================================
alter table public.profiles       enable row level security;
alter table public.families       enable row level security;
alter table public.family_members enable row level security;
alter table public.family_data    enable row level security;

-- ---- profiles ----
drop policy if exists profiles_select_self_or_family on public.profiles;
create policy profiles_select_self_or_family on public.profiles
    for select to authenticated
    using (id = auth.uid() or public.shares_family_with(id));

drop policy if exists profiles_insert_self on public.profiles;
create policy profiles_insert_self on public.profiles
    for insert to authenticated
    with check (id = auth.uid());

drop policy if exists profiles_update_self on public.profiles;
create policy profiles_update_self on public.profiles
    for update to authenticated
    using (id = auth.uid())
    with check (id = auth.uid());

-- ---- families ----
drop policy if exists families_select_member on public.families;
create policy families_select_member on public.families
    for select to authenticated
    using (public.is_family_member(id));

drop policy if exists families_insert_owner on public.families;
create policy families_insert_owner on public.families
    for insert to authenticated
    with check (owner_id = auth.uid());

drop policy if exists families_update_owner on public.families;
create policy families_update_owner on public.families
    for update to authenticated
    using (public.is_family_owner(id))
    with check (public.is_family_owner(id));

drop policy if exists families_delete_owner on public.families;
create policy families_delete_owner on public.families
    for delete to authenticated
    using (public.is_family_owner(id));

-- ---- family_members ----
drop policy if exists family_members_select_member on public.family_members;
create policy family_members_select_member on public.family_members
    for select to authenticated
    using (public.is_family_member(family_id));

-- 只能由家庭 owner 增删（本人入组走上面的 join_family_by_code RPC）
drop policy if exists family_members_insert_owner on public.family_members;
create policy family_members_insert_owner on public.family_members
    for insert to authenticated
    with check (public.is_family_owner(family_id));

drop policy if exists family_members_update_owner on public.family_members;
create policy family_members_update_owner on public.family_members
    for update to authenticated
    using (public.is_family_owner(family_id))
    with check (public.is_family_owner(family_id));

drop policy if exists family_members_delete_owner_or_self on public.family_members;
create policy family_members_delete_owner_or_self on public.family_members
    for delete to authenticated
    using (public.is_family_owner(family_id) or user_id = auth.uid());

-- ---- family_data ----
drop policy if exists family_data_select_member on public.family_data;
create policy family_data_select_member on public.family_data
    for select to authenticated
    using (public.is_family_member(family_id));

drop policy if exists family_data_insert_member on public.family_data;
create policy family_data_insert_member on public.family_data
    for insert to authenticated
    with check (public.is_family_member(family_id));

drop policy if exists family_data_update_member on public.family_data;
create policy family_data_update_member on public.family_data
    for update to authenticated
    using (public.is_family_member(family_id))
    with check (public.is_family_member(family_id));

drop policy if exists family_data_delete_member on public.family_data;
create policy family_data_delete_member on public.family_data
    for delete to authenticated
    using (public.is_family_member(family_id));

-- ============================================================
-- 验收 SQL（执行后应返回 4 张表 + 各自的 policy 数）
-- ============================================================
-- select relname, relrowsecurity from pg_class
--  where relname in ('profiles','families','family_members','family_data');
-- select schemaname, tablename, policyname, cmd
--   from pg_policies
--  where schemaname = 'public'
--    and tablename in ('profiles','families','family_members','family_data')
--  order by tablename, cmd;
