# Supabase 公测前检查操作清单（两步）

> **进度（2026-11-19）**：第一步 RLS 已由 AI 助手通过 SQL 迁移完成并验收——
> 4 表 relrowsecurity 全 true，策略 15 条（families/family_data/family_members 各 4、profiles 3）。
> 迁移脚本：supabase/rls_apply.sql（幂等可重跑）。本清单第一步作废。
> **端到端验证也已通过**（登录恢复+跨设备同步实测），第二步 CSV 导入导出演练可选做。

> 给完全不懂技术也能照做的版本。全程只需点击和截图，约 15 分钟。
> 遇到任何一步和描述对不上，就在那个界面截一张图发给 AI 助手，不要继续往下点。

## 准备：登录你的 Supabase 项目

1. 浏览器打开 **https://supabase.com/dashboard** ，用当初注册的项目账号登录（如果已登录会直接进控制台）。
2. 登录后在项目列表里点进项目 **jiayaoji**（项目 ID：`tjpjcmmcaortvubtnykm`，网址会显示 `.../dashboard/project/tjpjcmmcaortvubtnykm`）。
   - 📸 **截图位置 ①**：进入项目后的主页（左上角显示项目名和绿色"Active"状态）。

---

## 第一步：确认两张表开启了行级安全（RLS）

1. 看**左侧竖排图标栏**，点那个 **表格样子的图标**，名称是 **Table Editor**（表编辑器）。
2. 中间会出现一个表列表，应该能看到 **families** 和 **family_data** 两张表（可能还有别的）。
3. 点表名 **families** 进入这张表。
4. 在表界面**顶部区域**找一个叫 **"Row Level Security"** 或显示 **RLS** 的开关/标签：
   - ✅ 开关是**绿色/打开**状态 → 合格
   - ❌ 灰色/关闭 → **点一下把它打开**（弹窗选确认即可），打开即生效
5. 对表 **family_data** 重复第 3~4 步。
   - 📸 **截图位置 ②**：families 表顶部，能同时看到表名和 RLS 开关状态。
   - 📸 **截图位置 ③**：family_data 表顶部，同上。
6. 把截图 ②③ 发给 AI 助手确认。

> 为什么重要：不开 RLS，任何拿到公开密钥的人都能读写全部家庭数据；开了之后，每个家庭只能读写自己的数据。

---

## 第二步：备份导出一次（CSV），再做导入验证

### 2a. 导出

1. 还在 **Table Editor** 里，点表 **families**。
2. 页面**右上角**找 **Download / 下载图标（↓）** 或 "Export" 按钮，选 **CSV** 格式，浏览器会下载一个 `families.csv`。
3. 同样操作导出 **family_data.csv**。
4. 顺手再点左侧图标栏的 **SQL Editor**（一个 `>_` 样子的图标），新建查询（New query），把下面这行粘进编辑区，按 **Run**（或 Ctrl+Enter）：

   ```sql
   select count(*) as 家庭数 from families;
   ```

   - 📸 **截图位置 ④**：运行结果（一个数字）。记下这个数字，导入验证时要对比。

### 2b. 导入验证（在本地临时项目里做，不碰线上数据）

> 免费套餐只有一个项目时，可以用**新建第二个项目**来做验证（免费额度允许 2 个项目）。

1. 点左上角**项目名**，弹出项目列表，选 **New project**（新建项目）。
2. 名称随便填（如 `restore-test`），数据库密码自己设一个并**记下来**，区域选 **Singapore（新加坡）**，点 **Create new project**，等 2~5 分钟建库完成。
3. 进入新项目后，左侧点 **SQL Editor**，粘贴下面内容并 Run（建一张和线上一样的表；若报错把错误截图发来）：

   ```sql
   create table families (id bigint primary key generated always as identity, created_at timestamptz default now());
   ```

4. 左侧点 **Table Editor** → 点刚建的 **families** 表 → 右上角 **Import table data**（导入表格数据）：
   - Upload 步骤选择刚才下载的 `families.csv`
   - Schema 步骤把 **id 列**类型设为 `int8`、勾 Primary Key（或直接把 CSV 里 id 列删掉再传，让它自动生成）
   - 一路 Next → **Import**
5. 导入完成后，再开 SQL Editor 跑：

   ```sql
   select count(*) as 导入行数 from families;
   ```

   - 📸 **截图位置 ⑤**：结果数字。
6. **对比截图 ④ 和 ⑤ 的数字**：一致 = 备份链路可用 ✅。
7. 验证完删掉临时项目：左上角项目名 → 新项目 → **Settings**（左侧最底）→ 页面**最底部红色区域** → **Destroy project**，输入项目名确认。

> 说明：`families` 表列结构以线上为准——如果第 3 步建表和 CSV 列对不上导致导入失败，不用硬试，把 ④ 的截图和报错发给 AI 助手，由助手生成与你线上表结构完全一致的建表语句。

---

## 完成后把这几张图发给 AI 助手归档

| 截图 | 内容 | 证明 |
|---|---|---|
| ① | 项目主页 | 登录的是正确项目 |
| ②③ | 两张表顶部 RLS 开关 | 行级安全已开启 |
| ④⑤ | 两个 count(*) 结果 | 导出→导入行数一致，恢复链路可用 |

收到截图后，助手会把结果补进《恢复演练记录-20261119.md》，V366 数据层与备份项即全部关闭，公测前检查清单清零。
