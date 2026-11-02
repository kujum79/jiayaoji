# 家肴记 Day 7 · 上线前 P0 整改清单（V343 基線）

- 判定标准：同时满足「**影响真实家庭数据正确性**、**用户可感知且不可解释**、**上线首周会被反复投诉**」三条之一，且修复成本可控 → P0
- 结论：**上线前必须清零 6 项（P0-1 ~ P0-6）**；其余 11 项 D7 缺陷归入 P1/P2 首个迭代
- 前置说明：本次全部结论来自 `V343-20261101` 本地实测（四家真实账号 + 真实 localStorage + 真实点击），修复后按文末「回归清单」逐项验收

---

## 一、P0 清单（必须修）

### P0-1 库存账：0 库存仍展示 + 数量显示反向（D7-02 + D7-05）

| 项 | 内容 |
|---|---|
| **现象** | ①「现有食材（部分）」列出菠菜×0把、豆腐×0块、鸡蛋×0个、牛肉×0g；②记录一餐后牛奶 8盒→9盒、鸡蛋 12个→19个 |
| **影响家庭** | ①家1、家3、家4；②家1（其余家庭同结构，风险等同） |
| **用户后果** | 「冰箱明明有菜却说没有 / 越吃越多」→ 对整个食材模块失去信任，进而放弃采购与做菜 |
| **根因** | ① [renderFridgeRecView](file:///d:/jiayaoji/frontend/index.html#L19474-L19507) 仅按 `category` 过滤，未排除 `usedUp` / `quantityValue<=0`；② 录入时 `quantity`（"8盒"）与 `quantityValue`（10）不同源，[deductIngredientsForDish](file:///d:/jiayaoji/frontend/index.html#L44544-L44604) 扣减后按 `quantityValue` 重算 `quantity` |
| **修法** | ① 概览过滤 `usedUp !== true && status 非 used_up`（`quantityValue` 是原始入库量、不是剩余量，不能作判断依据），并新增「已用完（N）」可折叠区；② 统一以 `quantityValue + quantityUnit` 为唯一真值，`quantity` 仅作展示缓存，读取时 `formatQuantity(quantityValue, quantityUnit)` 重算；对历史脏数据（如 `8盒/10`）做一次性归一 |
| **验收标准** | 四家食材概览无任何 `×0` 条目；连续记录两餐后同一食材显示数量单调递减；`usedUp` 条目不出现在「现有食材」；家1 牛奶由 10 盒扣至 8 盒 |
| **回归范围** | 四家食材页 + 采购清单 + 食材推荐 + 记录扣减链路 |

> **P0-1 状态（2026-11-01，V344-20261101）**：①「现有食材」概览已排除 `usedUp`/`status=used_up` 条目，「现有食材食谱」默认食材池同步排除（四家实测无 ×0 标签，推荐不再引用 0 库存食材）——**已修**；②显示串与数值字段不同源导致的「扣减后反增」已通过启动对齐迁移修复（家1 牛奶 10盒→9盒实测不再反增）——**已修**。

### P0-2 家庭隔离迁移补丁（D7-01 + D7-03 + D7-06，一个补丁三处收口）

> 三项同源：V336 把 `mealRecords` 等键纳入家庭前缀白名单，但**历史数据没有随迁**，同时遗留了未规范化的历史结构。

| 子项 | 现象 | 影响家庭 | 证据 |
|---|---|---|---|
| **P0-2a** | 对象型 `plan.dishes` 使 `cleanDishName` 抛 `title.trim is not a function`，当餐清理中断 | 家3（159 处）、家4（173 处） | 每次加载 console 必现；[cleanDishName](file:///d:/jiayaoji/frontend/index.html#L53393-L53412) 无类型兜底 |
| **P0-2b** | `<家庭码>_mealRecords` 全为 `[]`，历史 11 条仍躺在全局 `mealRecords`（7180 字节），复盘统计归零 | 四家 | [getMealLocationStats](file:///d:/jiayaoji/frontend/index.html#L35519-L35548) 只读 `mealRecords` |
| **P0-2c** | `<家庭码>_familyHomeAddress` 缺失，天气条降级为「请填写地址」 | 家2、家4 | 家1「武汉…」、家3「上海…」存在 |
| **修法** | ① `cleanDishName` 入口先做类型归一（`typeof title === 'object' ? title.name : title`），`plan.dishes` 统一 map 为字符串；② 一次性迁移：按记录内 `familyId` / 家庭码归属把全局 `mealRecords` 拆分写入各 `<fid>_mealRecords`，无归属的批量归档保留不删；③ 启动期把遗留的全局 `familyHomeAddress` 随迁到当前家庭（**必须在 `claimGlobalDataForFamily()` 之前**，该函数每启动会删除全局副本）；云端备份与成员 `currentCity` 均无地址时不伪造，由【我家】补填引导兜底 | | |
| **关键原则** | **只增不删**：全局键保留 1 个版本周期作为回滚点；迁移幂等（重复执行不产生重复记录） | | |
| **验收标准** | ① 家3/家4 加载 console 0 条 TypeError；② 四家复盘统计条数与 `dietCalendar.actual` 对应；③ 家2/家4 食记顶部正常显示天气；④ 迁移脚本执行两次结果一致 | | |
| **回归范围** | 四家食记（今日/历史/复盘）、天气条、食材推荐、AI 三餐生成 |

> **P0-2a 状态（2026-11-01，已修 V344-20261101）**：`dishNameOf` 类型归一 + `plan.dishes` 一次性迁移 + 记录弹窗兜底三处落地；四家实测 console 0 条 TypeError/`title.trim`，家3/家4 对象菜品全部转为字符串（75→0 / 83→0），菜品条数与 `dishMeta` 条数前后一致，`actual.dishes` 与地点/分餐字段无变化。
>
> **P0-2b 状态（2026-11-01，已修 V344-20261101）**：新增 `migrateGlobalMealRecords()` 一次性按「日期 + 餐次 + 菜品名重合」判定归属，把全局 `mealRecords` 回填到各 `<家庭码>_mealRecords`（迁移 5 条、无归属归档 6 条，幂等，全局键保留作回滚）；`getMealLocationStats()` 同时并入 `dietCalendar.actual` 并按日期+餐次去重，四家「就餐地点分析」由 0 恢复为 1 / 3 / 22 / 27 人次，与食记条数对应。
>
> **P0-2c 已修（2026-11-01，V344-20261101）**：新增启动期 `migrateLegacyGlobalAddress()`，登录态下把遗留的全局 `familyHomeAddress` 回写到缺失该键的当前家庭（只补空缺、不覆盖），并前移到 `claimGlobalDataForFamily()` 之前——后者每启动按「移动」语义删除全局副本，放其后则读不到历史值（首版实测四家均未触发，已修正）。模拟验证：家2 / 家4 均自动回写并打印迁移日志，二次登录不被篡改值覆盖；家1 / 家3 住址不变。真实数据侧家2/家4 的地址在设备、服务端备份、成员 `currentCity` 中均已无来源（V336 已清理），属不可恢复，已按 Day1 预置值回填测试数据；四家食记天气条恢复真实天气（家1 中雨 / 家2 小雨 / 家3 小雨 / 家4 多云），0 console 错误。

### P0-3 份量账：婴儿计入人数 + 菜量口径倒挂（D7-08 + D7-10）

| 项 | 内容 |
|---|---|
| **现象** | ① 家3「用餐 4 人」含 0 岁婴儿 → 采购多算约 1 人份；② 家1（3 人）燕麦 40g（人均 13g）、家2（4 人）燕麦 40g（人均 10g）；家3（4 人）吐司 320g（人均 80g）、家4（6 人）480g（人均 80g） |
| **影响家庭** | ①家3；②家1、家2（付费家庭，最伤） |
| **用户后果** | 「买了吃不完 / 做了不够吃」；付费用户体验反而低于免费用户 |
| **根因** | ① 就餐人数未按年龄段折算（0-6m 系数应为 0）；② AI 方案 `ingredients` 未按人数放大，通用方案已放大 → 两套写入路径不一致 |
| **修法** | ① 统一 `getDiaryServings()` 返回加权人数（成人 1、1-3 岁 0.5、4-6 岁 0.7、0-6m 0），界面显示「成人 3 + 婴儿 1（不计入）」；② AI 方案统一 `×servings` 放大，或在 `dishMeta` 增加 `servings` 并在 UI 明示「本菜 N 人份」；③ 采购清单与食谱共用同一份 servings 计算结果 |
| **验收标准** | ① 家3 采购区显示「成人 3 + 婴儿 1（不计入）」且总量较现状下降约 25%；② 家1/家2 燕麦主料达到「≥40g/人」量级（70+ 老人按 0.8 折算后可略低）；③ 家3/家4 同菜按「每人份基准一致 + 按人群折算」定价，折算后克数比 ≈ 5.5/3（不再机械追求 1.5 倍） |
| **回归范围** | 四家今日三餐、3 天规划、采购清单、食材推荐 |

> **P0-3 状态（2026-11-02，已修 V345-20261102，四家实测）**：
> - **D7-08 婴儿/分龄折算接入采购**：`computeIngredientNeeds` 从「原始人数」改为「折算份数」（`q1 × eq[staple|protein|other]`），`_memberServingWeight` 拆分 1-3 岁 0.5 / 4-6 岁 0.7；食材页人数改为「用餐 3 人 + 婴儿 1 人（不计入）」。实测家3 文案与 `ctx{count:3,infantCount:1}` 一致；同批已确认菜品旧/新总量比：家1 **1.00**、家2 **0.90**（两位 70+ ×0.8）、家3 **0.75**（-25%，达标）、家4 **0.87~0.92**（儿童 0.7/0.5、外婆 1.3/1.2）。
> - **D7-10 菜量口径统一为「每人份」**：5 条写入路径（周计划 AI 应用 / 日推荐应用 / 周 AI 确认 / 语音加菜 / 推荐预览）统一存 `getDishIngredientsWithServings(name,1)` + `perPersonIngredients:true`；启动迁移对旧「总量」数据按「库内每人份 × 当餐人数」精确匹配还原（`plan.servings` 缺失时用就餐组成员数兜底），无法判定则保持原值不强改；周计划 prompt 增加「quantity 必须每人份」硬性规则，预览弹窗标注「（每人份）」。
> - **实测**：家3 三明治 `吐司320→80g / 鸡蛋4→1 / 生菜120→30 / 番茄200→50`，牛奶麦片 `燕麦片200→50 / 牛奶1000→250`；家4 三明治 `480→80g`，牛奶麦片 `300→50 / 1500→250`；采购侧折算后 家3 吐司 **240g**、家4 **440g**（旧口径实际为 1280g / 2880g 的重复放大已消除）；家1 燕麦粥保留 AI 每人份 40g（采购 120g，人均 40g）；四家 0 console / pageerror。
> - **遗留说明**：老日期中「不在库内」的 AI 总量数据无法自动判别（同形同量校验不通过时保持原值），需等该菜被重新生成或人工改写；`getDiaryServings()` 仍返回实际人数（用于外食/省钱展示），采购侧一律用折算份数。

### P0-4 安全凭据：已泄漏的阿里云 API Key 仍未轮换（D5-02 残留）

| 项 | 内容 |
|---|---|
| **现象** | V331 已从前端移除硬编码 `ALIYUN_API_KEY`，但该 Key 曾在公开前端明文存在，视为已泄漏；Day5 结论为「用户选择暂不轮换」 |
| **影响** | 面向公众上线后，任何拿到旧 HTML 的人可直接消耗额度、绕过计费与风控 |
| **修法** | 阿里云控制台吊销旧 Key → 新建 Key 仅存后端环境变量 → 后端 `/plan` 等代理统一鉴权；上线前用旧 Key 发一次请求确认返回 401 |
| **验收标准** | 旧 Key 调用失败；前端产物中全局搜索无 `sk-` 明文；`/plan`、`/recommend-dishes`、`/chef-image`、`/ai/*` 全部经后端 |
| **回归范围** | 线上 AI 全链路（三餐生成、点菜、餐厅推荐、菜单识别、专家评估） |

> **P0-4 状态（2026-11-02，V345 复核 + 代码加固）**：
> - **旧 Key 实测失效**：从 git 历史取出 V331 前前端明文的 DashScope Key，直接请求 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` → **HTTP 401 Unauthorized**，即该 Key 已在阿里云侧失效/删除，验收①「旧 Key 调用失败」达标。
> - **前端产物无明文**：`frontend/` 全目录搜索 `sk-…` / `aliyuncs.com` / `dashscope` → **0 命中**；`/plan`、`/recommend-dishes`、`/recommend-restaurants`、`/recognize_ingredients`、`/ai/vision`、`/parse-menu-image|url` 全部经 `API_BASE` 走后端，验收②③达标。
> - **修掉一处未走后端**：前端 AI 配图原先请求 `/chef-image`，但后端只定义 `/api/chef-image`（线上必然 404 后降级到 pollinations）→ 已改为 `/api/chef-image`。实测该路由返回 JSON（无 Key 时为 502 JSON 错误，而非 404/HTML）；`/plan` 422 JSON、`/recommend-dishes` 200 JSON、`/ai/usage` 405（GET 路由收到 POST）均证明路由在后端。
> - **新增泄漏 Key 黑名单守卫**：`backend/model_config.py` 只存旧 Key 的 sha256（不落明文），命中即视为「未配置」并打印安全日志、自动降级下一服务商；`backend/main.py` 的 `ALIYUN_API_KEY` 同样过滤。实测：把旧 Key 注入 `DASHSCOPE_API_KEY` → `is_leaked_api_key=True`、`dashscope_key_empty=True`；随机新 Key → `False`（正常放行）。
> - **生产服务器复核（2026-11-02，47.97.178.39 真实执行）**：扫描 `backend/.env`、根 `.env`、`/etc/environment`、`/root/.bashrc|.profile`、`/etc/default|sysconfig/jiayaoji`、`/etc/systemd/system/*jiayaoji*` → 仅 2 个 `sk-` 令牌（指纹 `8314124c`、`6d60ec57`，均为 Agnes/SiliconFlow 正常 Key），**旧泄漏 Key 0 命中**；`systemctl cat jiayaoji` 只有 `EnvironmentFile=/var/www/jiayaoji/backend/.env`、无内联 `Environment=`；线上 `https://jiayaoji.cn/app` `sk-` 命中 **0**。结论：无需清理动作，生产侧无旧 Key 残留。
> - **生产 Key 文件已解耦（2026-11-02，47.97.178.39 真实执行完成）**：主单元 `systemctl show -p FragmentPath` = `/etc/systemd/system/jiayaoji.service`（本身无 `EnvironmentFile`），真实来源是 drop-in `jiayaoji.service.d/override.conf`。已做：① 备份到 `/root/jiayaoji-backup-20261102085110/`（`backend.env`、`unit-*.txt`、`override.conf.orig`）；② Key 复制为 `/etc/jiayaoji/api-keys.env`（600，与 `backend/.env` sha256 一致 `77ed591b…`）；③ `override.conf` 改为 `EnvironmentFile=-/etc/jiayaoji/api-keys.env`、删除临时 `30-envfile.conf`；④ 移走仓库副本 `backend/.env`。验证：`systemctl show -p EnvironmentFiles` **只剩** `/etc/jiayaoji/api-keys.env (ignore errors=yes)`；重启后 `active`、进程环境 `AGNES_API_KEY` 计数 1、`/health` `{"ping":"pong"}`、`https://jiayaoji.cn/app` 200、`POST /recommend-dishes` 200、`POST /plan` 422；`backend/.env` 已不存在。**注意**：systemd 对 `EnvironmentFile=` 不支持「空值清空」，靠 drop-in 追加无法覆盖旧值（曾出现两处并存），覆写必须直接改 `override.conf`（已记入操作步骤）。
> - **后续轮换方式**：需启用百炼时在控制台新建 Key，只写 `/etc/jiayaoji/api-keys.env`（或 `deploy/rotate-api-key.sh --set` 写入的 `20-api-keys.conf`），禁止进仓库。仓库根 `.env`、`backend/.env` 均未被 git 跟踪（`git ls-files` 0 命中；`.env` 已在 `.gitignore`）。
> - **运维脚本**：[deploy/rotate-api-key.sh](file:///d:/jiayaoji/deploy/rotate-api-key.sh) 提供 `--check`（只读扫描 + 线上 `sk-` 断言）、`--remove`（清空泄漏 Key 并自动备份）、`--set` / `--set-file`（新 Key 写入 `/etc/systemd/system/jiayaoji.service.d/20-api-keys.conf`，600 权限、不进仓库）、`--verify`（不重启只验证）。脚本只输出 sha256 指纹，不打印 Key 原文。
> - **残留（P2，不阻塞）**：食材知识、菜谱配图仍有 keyless 的 pollinations 直连（`text.pollinations.ai` / `image.pollinations.ai`），无密钥泄漏风险，但严格绕过后端代理，建议随 P0-5 发布门禁一并收敛。

### P0-5 部署基线：nginx 非 `/api` 路由代理必须入库并纳入发布校验（D6-08 防回退）

| 项 | 内容 |
|---|---|
| **现象** | D6-08 曾导致线上所有 AI 路由 405 / 落静态页；修复只存在于服务器，**不在仓库**，换环境/重装即丢失 |
| **修法** | 把 `nginx.conf` 片段纳入仓库部署基线（`deploy/nginx/jiayaoji.conf`），发布脚本加一步「路由冒烟」；CI/部署后自动 `POST /plan`（空参）断言非 405 |
| **验收标准** | 新环境一键部署后 `/plan`、`/recommend-dishes`、`/recommend-restaurants`、`/parse-menu-image`、`/parse-menu-url`、`/recognize_ingredients`、`/chef-image`、`/ai/usage`、`/ai/budget`、`/ai/vision` 均返回 JSON 而非 HTML / 405 |
| **回归范围** | 生产部署流程 |

> **P0-5 关联风险（2026-11-02 实际发生）**：本地仓库有**两个远端**——`origin`=GitHub（`github.com/kujum79/jiayaoji`）、`gitee`=Gitee（`gitee.com/jiayaoji/jiayaoji`），**生产 systemd 所在服务器拉的是 Gitee**。V344/V345 只推到 GitHub，导致生产在 2026-11-02 仍停留在 `6f9d746`（V343），`git pull` 一直 `Already up to date`。补齐推送后（`6f9d746..8b3751a`）生产才更新到 V345。**发布约定需固化为：每次「提交推送」必须同时 `git push origin main` 与 `git push gitee main`，否则线上不更新**（建议发布脚本用一个 `push-all` 命令或把 Gitee 设为唯一部署源、GitHub 仅作备份镜像）。
> 生产发布实测（2026-11-02）：`git pull` → `8b3751a`、`sudo systemctl restart jiayaoji` → `active`、`/health` `{"ping":"pong"}`、`https://jiayaoji.cn/app` 返回 `V345-20261102`、`app=200`；nginx `root=/var/www/jiayaoji/frontend`（静态页直接读仓库目录，前端刷新即生效），`proxy_pass http://127.0.0.1:8000`（API 走后端）。首次启动按预期执行 V344/V345 迁移（就餐记录回填、地址补全、菜品名归一、dishMeta 每人份归一）。

### P0-6 付费承诺一致性：「快速记录 4 星」未落库（D7-04）

| 项 | 内容 |
|---|---|
| **现象** | 「⚡ 快速记录（4 星）」整餐 4 星、逐菜 `rating=0`，明细显示「未评分」 |
| **影响家庭** | 四家（家1 已实测） |
| **为什么算 P0** | 它不是「少个功能」，而是**用户已执行的评分动作没有落库**——直接影响口味画像与后续推荐，属于「承诺了没做到」 |
| **修法（二选一，成本都低）** | 方案A：快速记录时把整餐星级继承写入每道菜（`dishes[i].rating = overall`），可后改；方案B：按钮文案改为「快速记录（不打分）」，整餐星级不写入 `overall` |
| **验收标准** | 快速记录后明细中逐菜星级与整餐一致（方案A），或明细不出现「4 星」而显示「快速记录（未评分）」（方案B）；`rating` 数据可被偏好画像读取 |
| **回归范围** | 食记记录弹窗、明细、外食详情、口味画像 |

> **P0-6 状态（2026-11-01，已修 V344-20261101，采用方案A）**：快速记录时把整餐星级写入每道菜。家2 今日午餐实测：`actual.dishes` 四道菜 `rating` 全部由 `undefined` → 4，`mealRecords` 末条同口径，toast「已快速记录午餐 ✅」；测试数据已还原。历史快速记录不做回填（避免伪造用户评分）。

---

## 二、上线前必须跑通的冒烟清单（四家 × 9 项）

| # | 检查项 | 判定 |
|---|---|---|
| 1 | 四家登录 → 落到首页 | 无白屏、无版本回退 |
| 2 | 食记今日四卡 | 早/午/晚/加餐齐全，状态标签正确 |
| 3 | 快速记录一餐 → 明细 | 逐菜评分与整餐一致（P0-6） |
| 4 | 食材概览 | 无 `×0` 条目（P0-1） |
| 5 | 记录前后库存 | 数量只减不增（P0-1） |
| 6 | 采购清单「用餐 N 人」 | 婴儿不计入（P0-3） |
| 7 | 食养堂卡片 | 四家均有正文，订阅态正确 |
| 8 | 我的 → 积分中心 | 可打开，分数与流水一致 |
| 9 | Console | 四家 0 条 `title.trim` / `Uncaught TypeError`（P0-2） |
| 10 | 天气条 | 四家均显示天气（P0-2c） |
| 11 | 复盘统计 | 条数 = `dietCalendar.actual` 条数（P0-2b） |
| 12 | 线上 AI 路由 | `/plan` 返回 JSON，非 405 / HTML（P0-5） |

---

## 三、P1 清单（上线后第一个迭代，按顺序）

| # | 事项 | 关联 | 影响 |
|---|---|---|---|
| 1 | 未订阅家庭也做画像差异化（禁口过滤 + 人数 + 一句推荐理由） | D7-07 / D2-01 | 付费转化第一闸 |
| 2 | 高龄/慢病禁用油煎腌制主菜，或强制给软烂低盐替代 | D7-09 / D2-02 | 安全与口碑 |
| 3 | 菜谱补 `steps` + `heat` + `cookTime` | D2-09 | 从「菜单」变「菜谱」 |
| 4 | 健康声称重写：「慢病适宜」限定病种、「长高推荐」按年龄、「禁止白米饭」改建议式 | D7-11 / L1-L3 | 合规 |
| 5 | 血虚/补铁类体质：每餐至少 1 道富铁菜 | D7-09 | 个体化可信度 |
| 6 | 器具/分锅提示（整鱼 >750g、蔬菜 >800g） | 厨师报告 | 多人家庭可用性 |
| 7 | 未订阅锁屏露出 1 个系列名 + 1 篇标题 | PM U4 | 转化 |
| 8 | 食养内容按画像附禁忌注（糖尿病/胃肠/婴幼儿） | 柿子一课 | 内容安全 |
| 9 | 积分兑换差额提示「还差 N 分」 | PM U6 | 减少挫败 |
| 10 | 食记卡片价格区折叠、采购页首屏给结论 | PM U1/U2 | 首屏清爽 |
| 11 | `reason` 字段语义归一（理由 ≠ 时长） | 厨师报告 | 数据一致性 |

> **P1-1 进度（2026-11-02，V345-20261102）**：D7-07 画像差异化已落地——内置快速方案接入本地画像引擎（儿童分龄 / 外籍 / 体力劳动 / 家乡菜系 / 节气 / 喜欢食材），每餐替换 1–2 道非主食。四家实测跨家庭相同餐次 ≤1/9（家3/家4 由 6/6 相同 → 0/9），地区化（家1 湖北热干面 / 家2 贵州辣子鸡 / 家3 意大利面 / 家4 东北锅包肉）生效，同餐流质 ≤1、道数不变、0 报错。剩余项：禁口硬过滤、卡片一句个性化推荐理由（婴儿不计人数已随 P0-3 修复）。

---

## 四、建议的发布节奏

1. **第一批（上线阻塞）**：P0-1 库存账 → P0-2 迁移补丁 → P0-6 评分口径。三项都在前端 / 迁移层，改动集中、风险可控，建议合并为一个版本（建议 `V344`）。
2. **第二批（上线前 24 小时）**：P0-3 份量账（后端 prompt + servings 计算）、P0-4 Key 轮换、P0-5 部署基线入库。
3. **上线**：按「冒烟清单」12 项逐条验收，Console 与复盘统计为硬门槛。
4. **首迭代**：P1-1 ~ P1-5，优先把「个性化」与「可照做」补上，再谈增长。

---

## 五、一句话总结

**功能可以上线，账本不能带着错上线。** 这 6 项 P0 里有 4 项（库存、份量、迁移、评分）本质都是同一个问题——**同一个业务事实存在两套存储口径**（`quantity` vs `quantityValue`、`dietCalendar` vs `mealRecords`、`servings` 两套写入、整餐 vs 逐菜评分）。因此除了逐项修，更建议在修复时顺手统一「单一真值来源」，否则下一次改版还会再犯同一类错。
