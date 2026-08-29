# 逐餐逐日采购量精确计算 任务拆解 (tasks)

> 对应规范：`spec.md`；所有任务**仅改 `d:\jiayaoji\frontend\index.html`**，不创建新文件、不碰 backend。
> 优先级定义：high = 阻塞 AC 通过必须做；medium = 保证体验但不阻塞 core AC；low = 打磨。
> 每项 TR 类型：rule（二态）/ rubric（打分，见 pass 阈值）

---

## Task 1：统一工具函数 `getMealServings(date, meal)` + 旧数据兜底

**目标**：所有"取一餐人数"的地方统一走此函数，保证向后兼容（AC1、NF2）。

**改动位置**：`frontend/index.html`，在 `getCurrentServings()` 之后（L7488 附近）新增；`getMealData` 如有必要可内部调用它。

**改动步骤**：
1. 新增函数 `getMealServings(dateKey, mealKey)`：
   ```js
   读取 plan.servings → Number；有效（1-99）→ return；
   否则读取 plan.diningMembers?.length；有效 → return；
   否则 return getCurrentServings() || 1；
   ```
2. 在头部加 JSDoc 说明："唯一可信入口；永远返回 ≥1 的整数"。
3. 可选：在 `saveDietCalendar` / `updateMealPlan` 路径不主动补全（避免大规模写入老数据），仅在"读"时兜底，符合"最小侵入"。

**依赖**：无（最先做，其它任务都依赖它）。

**本地测试要求（TR）**：

| TR | 类型 | 条件 | 证据 |
|---|---|---|---|
| T1.1 | rule | 造一个 plan.servings=undefined、diningMembers 缺失的假条目 → 返回值 = getCurrentServings() 且 ≥1 | browser_evaluate JSON `{got,expected,pass:got===expected}` |
| T1.2 | rule | 造一个 plan.servings=3 → 返回 3 | browser_evaluate |
| T1.3 | rule | 造一个 plan.servings=0 或 null 但 diningMembers.length=2 → 返回 2 | browser_evaluate |

**AC 覆盖**：AC1、NF2

---

## Task 2：重写 `computeIngredientNeeds` 核心算法（逐餐 × S_meal）

**目标**：把"一刀切 S×D"改为"逐餐 Σ q1 × S_meal"，彻底修复采购量误差（AC2、AC3、AC4、NF6）。

**改动位置**：`frontend/index.html` L7606 ~ L7742。

**改动要点**：
1. **删除**原 L7612-7613 的 `D = daysToConsume.length || 1` / `S = Number(servings || prep.servings) || 1` 这两个全局变量，改为在**每餐循环内**读 `S_meal = getMealServings(key, meal)`。
2. **删除**原 L7676-7677 的 "totalQty = onePersonQty × S × D" 重写逻辑（这是旧一刀切 bug 的核心来源）。
3. 循环主体保留按天→按餐→按菜→按食材累加，但把：
   - 旧 `map[norm].totalQty += q1 * S` → 新 `map[norm].totalQty += q1 * S_meal`
   - 同时维护 `mealServingsSum[norm] += S_meal`（每个人食材维度的累计人·餐份数，用于 displayNote）
4. `displayNote`：旧 `(${displayOne}×${S}人×${D}天)` → 新 `(${displayOne}×${mealServingsSum[norm]}人·餐)`。
5. 参数 `servings`（从 `renderFridgeRecView` 传入）不再作为全局 S 乘入循环，仅作为 `getMealServings` 的最终 fallback（当 `prep.servings` 也可用，当所有兜底失败时）。
6. 顶部返回值字段仍保留：`{ confirmedDays, ingredientsMap, totalIngredientKinds, servings, days }`，其中 `servings` 改为 `"逐餐"` 字符串或显示 -1，避免误导；`days = daysToConsume.length` 不变。

**依赖**：Task 1（`getMealServings` 存在）。

**TR**：

| TR | 类型 | 条件 | 证据 |
|---|---|---|---|
| T2.1 | rule | 构造假 dietCalendar：今天午 servings=3，晚=2；明天午=4，晚=2；每餐 dishes=[番茄炒蛋]，一人份番茄 100g → ingredientsMap['番茄'].totalQty === (3+2+4+2)*100 = 1100 | browser_evaluate |
| T2.2 | rule | 假 dietCalendar：每餐 servings 都是 1；传入 servings=9（旧逻辑会×9×2天=18 倍）→ totalQty 仍 = 一人份 × 餐次数（=4），不会放大 | browser_evaluate：实际/期望值比值区间 [0.99,1.01] |
| T2.3 | rule | displayNote 的 HTML 字符串 **不匹配** `/×\d+人×\d+天/`，**匹配** `/×\d+人·餐/` | browser_evaluate 正则结果 |

**AC 覆盖**：AC2、AC3、AC4、NF6、R1（基础）

---

## Task 3：食记页「确认计划」新增人数选择浮层（confirmDiaryPlan 入口）

**目标**：点击 [确认计划] 按钮后不再直接打确认戳，先弹人数选择浮层（AC5/6/7/13）。

**改动位置**：
- 替换 `confirmDiaryPlan()` (L24499) 主体；
- 新增 DOM 浮层 HTML（与 `prepDelConfirmOv` 同风格，在 L3372 附近的 `<body>` 末尾浮层区新增一个兄弟节点）；
- 新增 3 个全局函数：`openConfirmPlanModal() / closeConfirmPlanModal() / applyConfirmPlan()`；
- 同步替换 voice case `confirm_plan` (L10539)：原来直接打戳 → 改为先调 `openConfirmPlanModal(dateKey, meal)`。

**UI 结构（390px 友好）**：
```
┌─ 遮罩 position:fixed inset 0 ──────────────────────┐
│ ┌ 卡片 max-width:320px; margin:auto; border-radius 12 ┐ │
│ │ 📋 确认食谱计划                                   │ │
│ │ 🍱 今天午餐（固定 mealLabel）                     │ │
│ │ 计划：番茄炒蛋、米饭（ dishes 一行省略 ）          │ │
│ │ 用餐人数：[<select> 1..10] 人 ← 默认值=getMealServings│ │
│ │                  [取消]   [✅ 最终确认]           │ │
│ └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

**保存后行为**：`plan.servings = N`；`plan.confirmed=true`；`plan.state='confirmed'`；`plan.confirmTime=now`；`updateMealPlan()`；`reservePlanIngredients()`；`renderDiary()`；Toast ✅ 文案含人数。

**依赖**：Task 1（`getMealServings` 用于默认值）。

**TR**：

| TR | 类型 | 条件 | 证据 |
|---|---|---|---|
| T3.1 | rule | 点 confirmDiaryPlan() → `#confirmPlanModalOv.classList.contains('show') === true`，5s 内无 Toast 出现 | browser_evaluate + toast 容器文本计数 |
| T3.2 | rule | 浮层中 `<select id="cpServingsSel">` 的 defaultValue 字符串化后等于 `getMealServings(dateKey,mealKey)` | browser_evaluate |
| T3.3 | rule | 选 select=2 → 点 [最终确认]：dietCalendar[date][meal].plan 三字段（servings:2 / confirmed:true / state:'confirmed'）都成立，Toast 文案含 "2人" | browser_evaluate 三字段+ToastLast |
| T3.4 | rule | 取消按钮：浮层关闭，plan 不变，无 Toast | browser_evaluate |
| T3.5 | rule | 语音指令 `confirm_plan` 路径也开浮层（非直接打戳） | 触发语音 case，断言浮层 show=true |

**AC 覆盖**：AC5、AC6、AC7、AC13、NF4

---

## Task 4：食材准备参数卡 + 逐餐人数说明 + ✏️ 调整弹窗

**目标**：食材准备页一眼能看见"今天几人、明天几人"，并能逐餐改人数（AC8、AC9、AC10）。

**改动位置**：
- `renderFridgeRecView()` 参数卡区 (L7374-L7393)，在 `<div class="prep-param-row">` 之下新增 `<div class="prep-servings-row">`；
- 新增一个自定义 DOM 浮层 `prepServingsModalOv`（复用 prepDelConfirmOv 风格，body 末尾浮层区加一个）；
- 新增 3 函数：`openPrepServingsModal()` / `closePrepServingsModal()` / `applyPrepServings()`；
- 辅助函数 `buildCycleServingsSummary(daysList)`：在 390px 下以紧凑格式拼接逐天逐餐人数标签。

**"人数说明行" UI 设计（390px）**：
```
采购周期：[2天 ▼]              用餐人数：逐餐（今天3人 明天2人…）

人数说明：今天 早2·午3·晚2  ·  明天 早2·午2·晚2   [✏️ 调整]
```
- 逐餐格式：`<day> <早>·<午>·<晚>`（按天分隔，多日之间 `·` 或空格）；
- 若某餐未确认，显示 "—"，避免用户误以为有计划没确认；
- [✏️ 调整] 按钮右对齐，橙底圆角小按钮，高 ≥34px。

**"调整"浮层 UI 设计**：
```
┌ 调整用餐人数（2天/6餐） ───────────┐
│ 今天 🍚 早餐： [select] 人        │
│ 今天 🥗 午餐： [select] 人        │
│ 今天 🍱 晚餐： [select] 人        │
│ 明天 🍚 早餐： [select] 人        │
│ …（共 N×3 行；无计划行灰掉不可选） │
│              [取消]   [✅ 保存]   │
└──────────────────────────────────┘
```
- 保存后：逐餐写回 `dietCalendar[date][meal].plan.servings`；无 plan 的餐跳过或写入也可（但用户看不到效果）；
- 保存后**自动** `renderFridgeRecView()` 重算采购清单，Toast "X 餐人数已更新，采购量已重算"。

**依赖**：Task 1、Task 2。

**TR**：

| TR | 类型 | 条件 | 证据 |
|---|---|---|---|
| T4.1 | rule | 参数卡 DOM 中 `包含 "人数说明"` 关键字，且其后文本至少有 1 个数字（显示了人数） | browser `innerText` 检查 |
| T4.2 | rule | 点 [✏️ 调整] → `#prepServingsModalOv` show=true，至少有 `<select>` 个数等于当前 cycleDays × 3 | browser_evaluate |
| T4.3 | rule | 把某餐 select 从 3→4，保存后：`dietCalendar 对应 servings === 4`，番茄 totalQty 增加约 33.33%（上下 1%），Toast 出现含 "已更新" | browser_evaluate qtyBefore/qtyAfter ratio |
| T4.4 | rule | 390px 宽度 scrollWidth-clientWidth ≤ 2（参数卡 + 两个浮层） | browser_evaluate |
| T4.5 | rule | "人数说明"中未确认的餐显示 "—"，不出现 0 或空白 | browser DOM 文本检查 |

**AC 覆盖**：AC8、AC9、AC10、AC12、NF5

---

## Task 5：静态/运行时双检 — 原生对话框清零 + 语法校验 + 390px 全量截图

**目标**：质量门禁（AC11、AC12、R2、R3）。

**步骤**：
1. `GetDiagnostics(frontend/index.html)` → 0 errors；
2. `Grep "window\.confirm|window\.prompt" frontend/index.html` → 在 Task 3/4 新增路径中出现数 = 0；若有历史调用在其他无关页面也保留即可（不算 fail，但要标出来）；
3. 重启后端 uvicorn → 打开 `http://127.0.0.1:8000/app?v=svcs`（随机 v）→ 跑 browser_use 390px：
   - ① 首屏首页正常、切食记Tab、点 [确认计划] → 弹窗出现；
   - ② 切食材 Tab → 参数卡有 "人数说明"+[调整] → 点调整 → 弹窗无溢出 → 改人数 → 保存 → 总数变化；
   - ③ 3 处（食记确认弹窗 / 食材准备调整弹窗 / 食材准备主参数卡）都截图并记录 scrollWidth 差；
4. 统计代码改动行数（`git diff -- frontend/index.html | wc -l`，如果没有 git 则粗略：新增函数数+被替换 computeIngredientNeeds 行数估计），不超过 1000 行（R3 阈值 1）。

**TR**：

| TR | 类型 | 条件 | 证据 |
|---|---|---|---|
| T5.1 | rule | GetDiagnostics errors = 0 | 工具输出 |
| T5.2 | rule | Task3/4 新增代码的原生对话框调用数 = 0 | Grep 输出 |
| T5.3 | rule | 390px 三处 UI scrollWidth - clientWidth ≤ 2 | browser_evaluate 数值 |
| T5.4 | rubric 0/1/2（阈值 1） | R2 UI 兼容性：截图视觉 + 溢出差 | 浏览器截图 + 数值 |
| T5.5 | rubric 0/1/2（阈值 1） | R3 代码内聚：改动行数 ≤1000 行；不删既有函数 | diff 行数 |

**AC 覆盖**：AC11、AC12、R2、R3

---

## Task 6（可选，优先级 medium，做完 1~5 有预算才做）：参数卡人数显示升级 & 数量 tooltip

**目标**：体验加分，不阻塞 AC。例如：鼠标悬停/点击 "X人·餐" 的 displayNote，弹窗展开"今天午×3、今 晚×2、明天午×4…"明细。

**依赖**：Task 2/4。

**TR**（可选）：
| TR | 类型 | 条件 | 证据 |
|---|---|---|---|
| T6.1 | rubric 0/1/2（阈值 1） | 明细展开 + 数值与 Σ 相等 | browser 交互截图 |

---

## 完成顺序（依赖链）

```
Task 1 ──→ Task 2 ──→ Task 5（静态部分）
   │
   ├──────→ Task 3 ──→┐
   │                  │
   └──────→ Task 4 ──→┴───→ Task 5（运行时部分）───→ Review
                    (Task 6 有空做)
```

---

## AC 覆盖矩阵（每个 AC 至少有一条 TR 直接验证）

| AC | 验证 TR |
|---|---|
| AC1 老数据兜底非 0 非 NaN | T1.1/1.2/1.3 |
| AC2 Σ 人份正确（番茄 1100g） | T2.1 |
| AC3 旧 S×D 逻辑失效 | T2.2 |
| AC4 displayNote 改为 人·餐 格式 | T2.3 |
| AC5 食记确认开浮层不秒确认 | T3.1 |
| AC6 弹窗默认值 = 当前 servings | T3.2 |
| AC7 保存后三字段持久化 + Toast | T3.3 |
| AC8 食材准备人数说明可见 | T4.1 |
| AC9 调整弹窗保存 → qty 比例增长 | T4.3 |
| AC10 人数改后 Toast 提示 | T4.3 Toast 检查 |
| AC11 不使用原生对话框 | T5.2 |
| AC12 390px 不溢出 | T4.4 / T5.3 |
| AC13 语音 confirm_plan 也开浮层 | T3.5 |
| R1 三处 qty 一致性（≥1分） | Task2 之后 Review 单独比对 |
| R2 390px UI（≥1分） | T5.4 |
| R3 内聚性（≥1分） | T5.5 |
