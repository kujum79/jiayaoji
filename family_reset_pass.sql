-- =====================================================================
-- 家庭登录/密码相关 SQL 修复（Supabase Dashboard → SQL Editor → 粘贴执行）
-- =====================================================================

-- ---------------------------------------------------------------------
-- 修复一：get_family_email RPC 返回完整字段
-- 旧版疑似只返回部分列（缺 family_name/family_code），导致：
--   ① 登录后首页右上角显示邮箱前缀而非家庭名称
--   ② fam.family_code 为空 → 每次登录都生成新家庭码并回写，家人手中的旧码失效
-- 本版返回 id / family_name / family_code / owner_id / owner_email 完整列
--（LIMIT 2 保留前端"存在多个同名家庭"的重名检测逻辑）
-- ---------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.get_family_email(family_name_input TEXT)
RETURNS TABLE (id UUID, family_name TEXT, family_code TEXT, owner_id UUID, owner_email TEXT)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT f.id, f.family_name, f.family_code, f.owner_id, f.owner_email
    FROM public.families f
    WHERE f.family_name = family_name_input
    LIMIT 2;
$$;

-- ---------------------------------------------------------------------
-- 修复二：家庭密码重置函数（v2）：凭家庭码直接重置登录密码
--
-- 关键改动：同步更新 Supabase Auth 的 auth.users.encrypted_password，
--           新密码对家庭登录（家庭名称+密码 → Auth 校验）立即生效。
--           旧版只更新 families.pass_hash，Auth 登录不读该字段，
--           会导致"重置成功但新密码登录不上"。
--
-- 验证方法：SELECT public.family_reset_pass('你的家庭码', 'test123456');
--           返回 OK 后用 家庭名称 + test123456 登录一次确认可进，
--           再用同样方式把密码改回真实密码。
--
-- 安全说明：本函数凭家庭码即可改密码（家庭码本身是分享给家人的凭据），
--           请勿将家庭码泄露给家庭以外的人。
-- =====================================================================

CREATE OR REPLACE FUNCTION public.family_reset_pass(p_code TEXT, p_pass TEXT)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions
AS $$
DECLARE
    v_owner_email TEXT;
    v_user_id     UUID;
BEGIN
    -- 参数校验（与前端规则一致：新密码至少 6 位）
    p_code := upper(trim(coalesce(p_code, '')));
    IF p_pass IS NULL OR length(p_pass) < 6 THEN
        RETURN 'PASS_TOO_SHORT';
    END IF;

    -- 家庭码格式：8 位字母数字，无 I/O/U/0/1
    IF p_code !~ '^[A-HJ-NP-Z2-9]{8}$' THEN
        RETURN 'CODE_NOT_FOUND';
    END IF;

    -- 1) 凭家庭码查绑定的 owner 邮箱
    SELECT owner_email INTO v_owner_email
    FROM public.families
    WHERE family_code = p_code
    LIMIT 1;

    IF v_owner_email IS NULL OR btrim(v_owner_email) = '' THEN
        RETURN 'CODE_NOT_FOUND';
    END IF;

    -- 2) 定位对应的 Auth 用户（同一邮箱取最早注册的一个）
    SELECT id INTO v_user_id
    FROM auth.users
    WHERE lower(email) = lower(btrim(v_owner_email))
    ORDER BY created_at ASC
    LIMIT 1;

    IF v_user_id IS NULL THEN
        RETURN 'USER_NOT_FOUND';
    END IF;

    -- 3) 直接更新 Auth 密码（bcrypt，与 Auth 登录校验格式一致），立即生效
    UPDATE auth.users
    SET encrypted_password = crypt(p_pass, gen_salt('bf'))
    WHERE id = v_user_id;

    RETURN 'OK';
END;
$$;
