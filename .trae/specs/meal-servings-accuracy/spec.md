# 逐餐逐日采购量精确计算（每餐人数独立）规范 (spec)

## 1. 问题 / 背景

当前「食材准备」页的采购量计算（`frontend/index.html` 内 `computeIngredientNeeds` 函数，L7608 起）存在一个本质缺陷：**用"统一人数 × 统一采购天数"一刀切**，忽略了每天每餐实际就餐人数可能不同。

**示例痛点**：采购周期 2 天，今天午餐 3 人、今天晚餐 2 人，明天午餐 4 人、明天晚餐 2 人。按当前逻辑会取当前就餐组人数（如 3 人）乘以 2 天得到总人数，番茄一人份 100g 就变成 600g；但按实际逐餐汇总（3+2+4+2=11 人份=1100g），差异可以超过 50%，直接导致「买多了浪费 / 买少了不够」。

用户诉求（自然语言原文）：
> "2天采购计划，今天3人明天2人——采购量按每餐实际人数累计，而不是一刀切。"
> "确认计划时指定每餐人数，系统自动汇总采购量。"

---

## 2. 用户 / 目标

**目标用户**：家肴记负责买菜/确认计划的家庭成员。

**业务目标**：
1. 采购量**按每餐实际人数累计**，彻底消灭"统一人数"带来的采购误差；
2. 用户在"食记"页**确认计划时**能**看到并修改该餐人数**（默认=当前就餐组人数）；
3. "食材准备"页能显示**逐天人数说明**（如：今天 3 人、明天 2 人），并允许微调；
4. 任何修改人数的操作（食记改/食材准备页改）**立刻触发采购量重新计算**，无需手动刷新。

---

## 3. 范围

### 3.1 In Scope（本轮必须完成）

| # | 模块 | 内容 | 代码位置（已知） |
|---|---|---|---|
| 1 | 数据模型 | `dietCalendar[date][meal].plan.servings` 字段启用与迁移：任何 plan.dishes 非空但无 servings 的，读取时兜底 = `getCurrentServings()`；写入确认戳时**允许用户改动**并持久化。 | `getMealData`(L3963), `updateMealPlan`(L4283), `confirmDiaryPlan`(L24499) |
| 2 | 采购核心算法 | `computeIngredientNeeds` 重写为**逐餐 × S_meal**：去掉末尾 `onePersonQty × S_global × D` 的二次重写；每餐从 `m.plan.servings` 取 S_meal（缺省走兜底）；`totalQty = Σ (q1 × S_meal)`；`displayNote` 改为按餐汇总的可追溯公式片段。 | `computeIngredientNeeds`(L7608-L7742) |
| 3 | 食记：确认计划弹窗 | 点击 `confirmDiaryPlan(mealKey)` 时，**不再直接打确认戳**，改为弹出一个轻量浮层：显示菜品列表 + 用餐人数 `<select>`（1~10 人，默认值 = plan.servings 或就餐组人数），用户点 [最终确认] 后才写入 servings + confirmed + 触发预扣。 | 新增 `openConfirmPlanModal() / closeConfirmPlanModal() / applyConfirmPlan()` |
| 4 | 食材准备：人数说明与编辑 | 参数卡新增一行"人数说明"：按采购周期内逐天输出格式 `今天 2/2/3（早/午/晚） · 明天 3/2/…`，右侧加 [✏️ 调整] 按钮 → 弹出逐餐人数编辑浮层；每行一个 `<select>` 1~10 人，保存后写回 dietCalendar 并立刻重渲染表格。 | `renderFridgeRecView`(L7374-L7393) + 新增模态浮层 |
| 5 | 人数变化联动采购量 | 以上两处入口（食记弹窗 / 食材准备弹窗）保存后都要：① `saveDietCalendar()` 持久化；② `renderFridgeRecView()` 或 `renderDiary()` 重渲染；③ `showToast` 人数变更提示。 | N/A |
| 6 | 兼容旧数据 | 任何读取 servings 的地方（采购计算 / 两个弹窗）都走统一工具函数 `getMealServings(date,meal)`，缺省链：`plan.servings → plan.diningMembers?.length → getCurrentServings() → 1`。 | 新增工具函数 |

### 3.2 Out of Scope（本轮不做，避免范围爆炸）

- 不动独立的"采购计划"子视图 `fridgeRecShopView`（此轮继续保留但不触及渲染入口）；
- 不修改食材的一人份拆解算法（`getOnePersonIngredients` / `getDishIngredientsWithServings` 保持不变，仅改变乘的 S_meal）；
- 不修改确认采购→生成采购记录→扣减/新增库存的入库链路（`openPrepConfirmModal` / `finalizePrepPurchase` 已有逻辑，仅替换传入 qty 的来源变量，不重构结构）；
- 不新增后端 API（纯 localStorage 前端改动，符合硬约束）。

---

## 4. 关键非功能需求

| # | 维度 | 规则 |
|---|---|---|
| NF1 | 单一文件约束 | 所有改动**只在 `frontend/index.html`**，不创建新 JS/CSS 文件，不碰 `backend/main.py`（项目硬约束）。 |
| NF2 | 向后兼容 | 老 `dietCalendar` 数据中 `plan.servings` 为空/未定义时行为与老版本一致（=就餐组人数），不得因缺字段导致 NaN 或采购量=0。 |
| NF3 | 性能 | 重渲染 ≤ 100ms（逐餐遍历最多 7 天×3 餐 = 21 餐，性能不构成问题，但必须避免 render 中重复 `JSON.parse(lsGet)`）。 |
| NF4 | UI 无原生对话框 | 本页任何交互**不得使用 `window.prompt / window.confirm`**（用户已经反馈这会在 WebView APP 中黑屏），改用 `<div>` 自定义浮层 + `<select>` / `<input type=number>`。 |
| NF5 | 可视化验收 | 390px 宽视口下：新增的「逐餐人数说明行」、「✏️ 调整」按钮、两个确认弹窗 **不得出现水平溢出**（`html.scrollWidth - html.clientWidth ≤ 2`）。 |
| NF6 | 可追溯公式 | 每样食材的 `displayNote` 不再是 `(一人份×S人×D天)`，改为 `(一人份×Σ 各餐人数)`，例：`(100g×3午×2晚×4午×2晚)` 或精简为 `(100g×11人·餐)`。 |

---

## 5. 约束 & 依赖 & 假设

### 5.1 已验证硬约束（从 project memory 继承，**任何情况不得违反**）

1. **仅改 `d:\jiayaoji\frontend\index.html` 单一文件**，不触碰 `backend/main.py`、`images/logo.png`、老的 `generateShoppingPlan/recShopPlan` 模块；
2. 数据全走 localStorage：`dietCalendar / fridgeItems / diningGroups / familyData / purchaseHistory`；价格使用 `window._recShopDefaultPrices`；
3. 后端启动命令：`py -m uvicorn backend.main:app --reload`；必须改完 `index.html` 后**重启后端进程** + 访问加 `?v=随机` 参数绕过浏览器缓存；
4. 已采购（`alreadyHave=true`）行禁止修改和删除（本轮不涉及，但仍保持）；
5. 自定义采购天数上限 = 已确认食谱计划天数，超出要 Toast 截断。

### 5.2 依赖

- **数据依赖**：`dietCalendar[dateKey][breakfast|lunch|dinner].plan.servings`（已存在写入路径 `updateMealPlan#L4306`，此前是自动注入从未被读取/修改）。
- **UI 依赖**：食记页 confirmDiaryPlan 按钮、食材准备页参数卡；两处新增自定义 DOM 浮层（复用现有 `position:fixed;inset:0;z-index:500` 的浮层写法，与 `prepDelConfirmOv` 风格一致）。

### 5.3 假设

1. 用户确认计划时未显式改人数的 = 使用当前就餐组人数（默认）；
2. 同一餐在食记页改了人数，下次打开"食材准备"会**立刻**反映（因为计算是 render 时实时从 `dietCalendar` 读取）；
3. 逐餐人数取值范围 `1 ~ 10`（家庭就餐场景，>10 人极少）。

---

## 6. 开放问题（本次先做默认决策，若用户后续反馈再调整）

| # | 问题 | 默认决策 |
|---|---|---|
| O1 | "食材准备"页调整人数时，是否要同步改食记页 `plan.servings`？ | **是**：统一写入 dietCalendar，一处改全局生效，避免数据分叉。 |
| O2 | "外出就餐/外卖"状态的餐次是否要列入采购量计算？ | **否**：沿用原逻辑——这些餐的 plan 要么没有 dishes，要么被 diningType 过滤——不产生食材需求。 |
| O3 | 人数改了后，`reservePlanIngredients`（预扣食材）是否重算？ | **否**：本轮不重构预扣/扣减机制（Out of Scope），采购量的计算口径变更才是核心目标；预扣将在确认采购最终入库时用新 qty 执行，与真实采购对齐。 |

---

## 7. 验收标准（Acceptance Criteria）

> 类型定义：`rule` = 可客观验证的二进制过/不过；`rubric` = 打分评估维度，标有阈值。

### 功能 AC（rule，13 条全必须 PASS）

| ID | 内容 | 观测方式 |
|---|---|---|
| AC1 | `getMealServings(date,meal)` 对老无 servings 数据返回 = 当前就餐组人数（非 NaN、非 0） | browser_evaluate 脚本：造一个 plan 无 servings 的假条目，断言返回 = diningGroup.members.length |
| AC2 | `computeIngredientNeeds` 对构造数据按逐餐人数累加：3人午 + 2人晚 + 4人午 + 2人晚 = 11人份；番茄一人份 100g，totalQty = 1100g | 注入假 dietCalendar（2 天 × 2 餐 × 不同人数），断言返回 `ingredientsMap['番茄'].totalQty === 1100` |
| AC3 | 旧逻辑（一人份×统一S×统一D）不再生效；即便传入 `servings` 参数，也按每餐 plan.servings 累加（servings 参数仅作 meal.servings 全缺省时的 fallback，不在循环里×） | browser_evaluate：S_global=9 但每餐 servings 都是 1，得出 totalQty 应是 一人份 × 餐次数，而不是 ×9 |
| AC4 | `displayNote` 不出现 "`×N人×M天`" 一刀切格式；改为 "`×ΣX人·餐`" 或同等语义 | browser_evaluate：任取一 result 条目，正则检查 **不得匹配** `/×\d+人×\d+天/`，**必须匹配** `/人·餐|×\d+人·餐/` |
| AC5 | 食记 confirmDiaryPlan：点击后**不立刻 Toast "计划已确认"**，而是弹出包含 `<select>` 人数控件的浮层 | browser 脚本：click() 后 DOM 中出现 confirmPlanModalOv 浮层 class="show"；Toast 不触发 |
| AC6 | 弹窗中 `<select>` 默认选中 = 当前就餐组人数或 plan.servings（若已存在）| browser_evaluate：浮层中 select.value === plan.servings 的字符串形式 |
| AC7 | 弹窗 [最终确认] 后：dietCalendar 中 plan.servings 被持久化为用户选值、plan.confirmed=true、plan.state='confirmed'，然后 Toast "已确认"并关闭浮层 | browser_evaluate 三字段检查 + Toast 文本 |
| AC8 | 食材准备页参数卡**新增**一行"人数说明"：格式含"今天 / 明天 / … 早/午/晚 各 X 人"（对应当前 cycleDays 范围内逐餐） | browser 文本扫描 `人数说明：` 关键字，随后至少出现 1 个数字 |
| AC9 | 点击 [✏️ 调整] 打开逐餐人数编辑浮层，每行 1 `<select>`，保存后 dietCalendar 中对应餐的 servings 被写入，采购表格 totalQty 重新计算 | 修改某餐人数 3→4 后，重新 compute，totalQty 按比例增长（+1/3 ≈33.33%，容忍 ±1%） |
| AC10 | 人数变化（任意入口）后，`showToast` 明确提示"X 餐人数已更新为 Y 人" | 检查 Toast 容器最后一条文本 |
| AC11 | 所有新弹窗都**不使用** window.confirm / window.prompt（代码静态检查 + 运行时行为） | Grep + 函数替换计数：`window.confirm`、`window.prompt` 在新代码路径的调用次数=0 |
| AC12 | 390px 宽度下，新增 UI（参数卡人数说明行、两个浮层）无水平溢出：`html.scrollWidth - html.clientWidth <= 2` | browser_evaluate |
| AC13 | 兼容已有用户操作链路：语音 "确认明天的午餐" 指令（`confirm_plan` case）也走同样的选人数弹窗（而不是直接打确认戳） | 手动触发 handleDiaryVoiceCommand('confirm_plan',...) 后，DOM 浮层出现 |

### 质量 AC（rubric，3 条，每项 0-2，pass 阈值 ≥ 1）

| ID | 维度 | 0 | 1（pass 阈值） | 2 | 证据源 |
|---|---|---|---|---|---|
| R1 | 数据一致性（逐餐人数 → 采购 qty → 入库 qty 三处相等） | 三处三处相差 ≥20% 或出现 NaN / 0 异常 | 一致，偏差 ≤ 5%，仅 rounding | 严格一致且有 displayNote 可追溯公式 | browser_evaluate 三连取同一条目比对 |
| R2 | UI 兼容性（390px 手机屏） | 横向溢出 / 控件被截断 | 不溢出、内容可读，但字小需放大 | 不溢出、字号 11-14px、按钮可点击（≥34px高） | browser 截图 + scrollWidth 差值 |
| R3 | 代码改动内聚性（是否只动必须改动的区域） | 改动超 1500 行或删除无关函数 | 改动 ≤ 1000 行、不删既有函数、仅新增函数 + 替换 computeIngredientNeeds 主体 | 改动 ≤ 600 行、函数边界清晰、有清晰注释说明与旧逻辑的差异点 | 文件 diff 行数统计 + 阅读 |

---

## 8. 证据输出路径（Implement & Review 时必须产出）

- AC2 / AC3：`browser_evaluate` 返回 JSON，显式打印 `totalQty === expected`；
- AC5 / AC6 / AC7：`browser_evaluate` 浮层 class='show' + select.value + 三字段断言；
- AC8 / AC9：`browser_evaluate` `innerHTML` 文本匹配；
- AC12：`document.documentElement.scrollWidth - clientWidth`；
- R1：同一食材 result.needBuyQty、`pendingBuyItems[].qty`、fridgeItems 入库后 qty 增量三值比对。
