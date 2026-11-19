-- 家肴记 RLS 专用执行脚本 v3（幂等）
-- 诊断结论：编辑器以 postgres（非超管、非 authenticated 成员）运行，
-- 借 PG16+ "数据库 CREATE 特权 = 对象管理权" 机制获得建策略资格
-- 用法：SQL Editor → New query → 粘贴全部 → Ctrl+Enter

grant create on database postgres to postgres;

create extension if not exists pgcrypto;

create or replace function public.is_family_member(p_family uuid)
returns boolean language sql security definer stable set search_path = public as $$
    select exists (select 1 from public.family_members m
                    where m.family_id = p_family and m.user_id = auth.uid())
        or exists (select 1 from public.families f
                    where f.id = p_family and f.owner_id = auth.uid());
$$;

create or replace function public.is_family_owner(p_family uuid)
returns boolean language sql security definer stable set search_path = public as $$
    select exists (select 1 from public.families f
                    where f.id = p_family and f.owner_id = auth.uid());
$$;

create or replace function public.shares_family_with(p_user uuid)
returns boolean language sql security definer stable set search_path = public as $$
    select exists (select 1
          from public.family_members me
          join public.family_members other on other.family_id = me.family_id
         where me.user_id = auth.uid() and other.user_id = p_user);
$$;

create or replace function public.handle_new_family()
returns trigger language plpgsql security definer set search_path = public as $$
begin
    if new.owner_id is not null then
        insert into public.family_members (family_id, user_id, role)
        values (new.id, new.owner_id, 'owner')
        on conflict (family_id, user_id) do update set role = 'owner';
    end if;
    return new;
end;
$$;

do $$
begin
    if exists (select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
                where t.tgname = 'on_family_created' and c.relname = 'families'
                  and not t.tgisinternal) then
        execute 'drop trigger on_family_created on public.families';
    end if;
    execute 'create trigger on_family_created after insert on public.families for each row execute function public.handle_new_family()';
end $$;

-- 存量家庭回填 owner 成员
insert into public.family_members (family_id, user_id, role)
select f.id, f.owner_id, 'owner'
  from public.families f
 where f.owner_id is not null
on conflict (family_id, user_id) do nothing;

-- 属主收回 + 开 RLS + 全部策略：单块动态执行
do $$
declare
    t text;
    p text;
begin
    foreach t in array array['profiles','families','family_members','family_data']
    loop
        begin
            execute 'alter table public.' || t || ' owner to postgres';
        exception when others then
            raise notice 'owner skip %: %', t, sqlerrm;
        end;
        execute 'alter table public.' || t || ' enable row level security';
    end loop;

    foreach p in array array[
        'drop policy if exists profiles_select_self_or_family on public.profiles',
        'create policy profiles_select_self_or_family on public.profiles for select using (id = auth.uid() or public.shares_family_with(id))',
        'drop policy if exists profiles_insert_self on public.profiles',
        'create policy profiles_insert_self on public.profiles for insert with check (id = auth.uid())',
        'drop policy if exists profiles_update_self on public.profiles',
        'create policy profiles_update_self on public.profiles for update using (id = auth.uid()) with check (id = auth.uid())',
        'drop policy if exists families_select_member on public.families',
        'create policy families_select_member on public.families for select using (public.is_family_member(id))',
        'drop policy if exists families_insert_owner on public.families',
        'create policy families_insert_owner on public.families for insert with check (owner_id = auth.uid())',
        'drop policy if exists families_update_owner on public.families',
        'create policy families_update_owner on public.families for update using (public.is_family_owner(id)) with check (public.is_family_owner(id))',
        'drop policy if exists families_delete_owner on public.families',
        'create policy families_delete_owner on public.families for delete using (public.is_family_owner(id))',
        'drop policy if exists family_members_select_member on public.family_members',
        'create policy family_members_select_member on public.family_members for select using (public.is_family_member(family_id))',
        'drop policy if exists family_members_insert_owner on public.family_members',
        'create policy family_members_insert_owner on public.family_members for insert with check (public.is_family_owner(family_id))',
        'drop policy if exists family_members_update_owner on public.family_members',
        'create policy family_members_update_owner on public.family_members for update using (public.is_family_owner(family_id)) with check (public.is_family_owner(family_id))',
        'drop policy if exists family_members_delete_owner_or_self on public.family_members',
        'create policy family_members_delete_owner_or_self on public.family_members for delete using (public.is_family_owner(family_id) or user_id = auth.uid())',
        'drop policy if exists family_data_select_member on public.family_data',
        'create policy family_data_select_member on public.family_data for select using (public.is_family_member(family_id))',
        'drop policy if exists family_data_insert_member on public.family_data',
        'create policy family_data_insert_member on public.family_data for insert with check (public.is_family_member(family_id))',
        'drop policy if exists family_data_update_member on public.family_data',
        'create policy family_data_update_member on public.family_data for update using (public.is_family_member(family_id)) with check (public.is_family_member(family_id))',
        'drop policy if exists family_data_delete_member on public.family_data',
        'create policy family_data_delete_member on public.family_data for delete using (public.is_family_member(family_id))'
    ]
    loop
        execute p;
    end loop;
end $$;

-- 保险丝：任何表开了 RLS 却没策略 → 抛错整体回滚
do $$
declare bad text;
begin
    select string_agg(t, ', ') into bad
      from unnest(array['profiles','families','family_members','family_data']) t
     where exists (select 1 from pg_class c
                    where c.relname = t and c.relrowsecurity
                      and c.relnamespace = 'public'::regnamespace)
       and not exists (select 1 from pg_policies p
                        where p.tablename = t and p.schemaname = 'public');
    if bad is not null then
        raise exception 'RLS enabled but no policy on %; rollback', bad;
    end if;
    raise notice 'RLS migration OK: 4 tables, 15 policies';
end $$;
