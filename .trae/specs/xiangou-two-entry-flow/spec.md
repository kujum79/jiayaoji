# 「现购食材」双入口改造 规范

## 1. 问题 / 目标
用户在「现购食材」流程生成菜谱后，只提供「📋 加入食记计划」一种入口，实际想"今晚就吃"时还得跳去食记页、再打开记录实际、再填一遍菜品 —— 多 2-3 步。

目标：在现购食材生成的菜谱卡片（`fridgeResultContent .fridge-recipe-card`）**增加两个并排按钮**：
1. 「🍳 今晚就吃」→ 直接打开 **食记·添加记录**弹框，菜品已自动进入 `recordFormDishes`，餐次=晚餐，用餐类型=在家做饭，自动带一条 `source:'现购食材'` 标记；用户可加评价后点「保存」，保存后扣减 `fridgeItems` 库存。
2. 「📋 加入食记计划」→ 继续复用原有 `addToPlanFromFridge`（仅计划，confirmed=false，source=shopping，库存零改动，预扣走「确认计划」链路）。

## 2. 用户 / 目标
- 用户：负责买菜 / 做饭 / 写食记的家庭成员。
- 业务目标：让“买完 → 今晚就吃”这条高频链路一键直达记录实际，减少切换和重复录入；且不破坏「食记 → 逐道勾选 → 确认计划 → 预扣库存」的另一条链路。

## 3. 范围
### 3.1 In Scope
- 菜谱推荐结果页 `fridgeResultOverlay` 的卡片，`renderFridgeMeals` 按钮区改成「🍳 今晚就吃 / 📋 加入食记计划」两按钮并排。
- 新增 `jumpToDinnerTonight(mealDish, source)`：
  - 若当前时间 < 17:00 → 默认晚餐；≥17:00 → 晚餐（与 "今晚"语义一致）。
  - 先写 pending「实际记录草稿」到 mealRecords 的 staging 字段（或用 recordFormDishes 全局数组 + 一个带来源的对象）；
  - 调用 `openAddRecordModal()` 后立即：
    - 切餐次=`晚餐`；
    - 把 `mealDish` 追加到 `recordFormDishes`；
    - 写每个 dish.userComment 初始为 `来源：现购食材推荐（菜谱名/时间戳）`；
    - 若存在 `dishMeta.source`（今日餐段 plan 已有此菜），则去重；
  - 自动把 `currentRecordFormDining = '在家做饭'`；
  - 加一条 banner 在实际记录 modal 顶部 `recordAiFeedbackArea` 显示：`已从「现购食材·今晚就吃」带入菜品：X，记得保存前补充评价 🎉`。
- 保存记录 `submitAddRecord()`：
  - 构造 `record` 时，**新增字段 `record.source='shopping'`**（与其他入口区分：shopping=现购食材 tonight）；另外在 `record.dishes[i].sourceMeta = {source:'shopping', broughtAt:timestamp}` 写逐道元信息。
  - 保存后触发 `deductIngredientsForDish()`（现有在家做饭会自动扣，不需改逻辑，但要**兜底**：如果菜品在 fridgeItems 中找不到（因为是新买未入库），就把 `shoppingPlans` 里对应"已购但未入库"项自动入冰箱再扣）。
- 结果页顶部增加一行说明：`🍳 今晚就吃 = 直达食记·记录实际；📋 加入食记计划 = 只写计划，后续在食记页逐道勾选→确认`。

### 3.2 Out of Scope
- 不改「🥕 食材推荐食谱」3 天 3 餐 overlay 的按钮（那是多选计划确认页，不是本 scope）。
- 不改 weekend / existing / ai 其他入口的按钮（本 scope 只改 source=shopping 时渲染结果页的按钮）。
- 不做批量「整餐今晚就吃」（当前只做单卡按钮，符合用户“选择菜谱后”语义）。
- 不把 dishMeta 字段里 source 枚举改名（继续保留 shopping/preferred/ai/existing/weekend）。

## 4. 非功能要求
- 仍 **只改动 `d:\jiayaoji\frontend\index.html`**，零后端路由/文件新增。
- 全部读写用 `lsGet / lsSet` 做家庭码隔离。
- `GetDiagnostics(index.html) === 0`，430px 视口 `body.scrollWidth−clientWidth === 0`。
- 对不存在的函数/变量做短路兜底，不得出现 ReferenceError 打断整页。

## 5. 功能需求 (FR)

### FR-1 两个按钮并排显示
- `renderFridgeMeals(meals, defaultSource)`：
  - 按钮区改成两按钮：`🍳 今晚就吃`（主色，绿色或番茄红，与 shopping 主色一致）、`📋 加入食记计划`（次色灰边），按钮宽度按 50%/50% 分配 + 间距 6px。
  - 点击「今晚就吃」调用 `jumpToDinnerTonight({dish, ingredients, category, source})`。
  - 点击「加入食记计划」继续调用 `addToPlanFromFridge(dish, 'lunch', 'shopping')`（本入口默认为午餐，沿用旧行为；若需要改成晚餐，可通过参数覆盖）。
  - 结果页顶部标题下方插入一行说明文字（只在 `defaultSource === 'shopping'` 时出现）：`「🍳 今晚就吃」 → 直达「食记·记录实际」填好菜品和在家做饭；「📋 加入食记计划」 → 先入今日食记计划，后续勾确认`。

### FR-2 今晚就吃 跳转正确
- `jumpToDinnerTonight(meal, source)`：
  1. 参数校验，name 非空。
  2. 计算餐次：`'晚餐'`（无论现在时间，与"今晚"强绑定）；日期为今天（`dateToKey(new Date())`）。
  3. 打开 modal 用 `openAddRecordModal()`。
  4. 覆盖餐次按钮 active 到「晚餐」；设置 `currentRecordFormMeal = '晚餐'`。
  5. 用餐类型：`currentRecordFormDining = '在家做饭'`；在家/外出 toggle 更新 active。
  6. 日期：`recordFormDate` = 今天。
  7. 菜品：**不重复**：若 `recordFormDishes.map(n=>n.name)` 已包含同名则跳过，toast "已在记录表单里，去改评价吧"；否则 `addDishToForm(name)`。
  8. 给新加入的 dish 追加 `sourceMeta: { source: 'shopping', addedBy: 'tonight_button', at: Date.now() }`，并默认 `userComment = '🛒 来源：现购食材推荐（今晚就吃）'`。
  9. banner 写到 `recordAiFeedbackArea`：`div.record-source-banner` 绿底提示。
  10. 关闭 `fridgeResultOverlay`（可选，toast：「已打开记录实际，请补评价后保存」）。
  11. 如果当前没在 diary tab → `switchTab('diary')`。

### FR-3 加入食记计划 跳转正确
- `addToPlanFromFridge` 无任何功能变化；source 继续写 shopping / confirmed=false；点击仍 toast "✅ 已加入今日午餐/晚餐，请去食记页勾选确认"。
- 本 scope 不新增 toast 内容，但若按钮文本由「加入食计」→「加入食记计划」，需替换文案。

### FR-4 记录来源标记正确（source: shopping）
- `submitAddRecord()`：
  - `record` 对象新增顶层字段 `source: (recordFormDishes.some(d => d.sourceMeta && d.sourceMeta.source === 'shopping') ? 'shopping' : '')`。
  - 逐道 dish：保留 `sourceMeta`（若有）。
  - 写入 mealRecords 后，同时写一份到 `updateMealActual(dateKey, mealType, {dishes:record.dishes, diningType:currentRecordFormDining, comment:record.overallComment, source:record.source, savedAt:Date.now()})` 同步更新日历视图（保证食记页已吃盘/统计一致）。

### FR-5 库存自动扣减正常
- 现有 `currentRecordFormDining === '在家做饭'` 时会对每道菜 `deductIngredientsForDish()`。新增一层兜底：
  - 如果 `deductIngredientsForDish(name)` 返回空数组（fridge 找不到该菜食材），则：
    - 从 `lsGet('shoppingPlans')` 中取 `purchased=true` 但 `purchasedAt 在最近 24h 内` 的项，先调用现有入库 `addFridgeItem(...)` 临时入库，再扣一次；
    - 或直接调用 `deductIngredientsForDish` 的"购物清单→冰箱先入库再扣"辅助函数（若已存在）；
  - 最终 toast 仍要告知扣减情况（包含"x 种从现购清单入库后再扣"）。

## 6. 约束 / 依赖
- `renderFridgeMeals`（L13216）、`addToPlanFromFridge`（L13243）、`openAddRecordModal`（L18501）、`addDishToForm`（L18617）、`submitAddRecord`（L18658）、`deductIngredientsForDish`（L18548）、`updateMealActual`（L4778）、`saveFridgeItems`（L6246）、`callAliyunQwenApi`（AI）。
- source 枚举验证 regexp：shopping / existing / weekend / ai / preferred。

## 7. 开放问题（默认方案，用户可调）
1. **今晚就吃 默认餐次**：本规范=晚餐；若用户想改成「按当前时段自动（<11:00 早餐；<15:00 午餐；其余晚餐）」，后续可扩展。当前先锁死「今晚→晚餐」。
2. **加入食记计划 默认餐次**：本规范=午餐（保持旧行为）。是否改晚餐？默认不改。

## 8. 验收标准 (AC)
### AC-0 双按钮显示
- **rule**：任一 `shopping` 来源的菜谱结果卡片，按钮区渲染 2 颗按钮文本分别包含「🍳 今晚就吃」「📋 加入食记计划」，布局并排无换行。
- **rule**：shopping 来源结果页顶部有说明 banner 文本含「今晚就吃 → 直达食记」「加入食记计划 → 先入计划」。

### AC-1 今晚就吃跳转正确
- **rule**：点「🍳 今晚就吃」→ `addRecordModal.classList.contains('show') === true`；
  - `currentRecordFormMeal === '晚餐'`；
  - `currentRecordFormDining === '在家做饭'`；
  - `recordFormDate` 等于今天（`dateToKey(new Date())`）；
  - `recordFormDishes.some(d => d.name === dishName)` 为 true；
  - `tab=diary` 或 `#panelDiary`（diary panel）active。

### AC-2 加入食记计划跳转正确
- **rule**：点「📋 加入食记计划」→ `getMealData(today, 'lunch').plan.dishes` 出现菜品；
  - `dishMeta[dish].source === 'shopping'` 且 `confirmed === false`；
  - toast 文案含「已将加入今日午餐」和「食记」。

### AC-3 来源标记正确
- **rule**：跳转进入实际记录 → 保存后，`mealRecords[last].source === 'shopping'`；
  - 对应 dish 的 `sourceMeta.source === 'shopping'`；
  - `updateMealActual` 写入的 `dietCalendar[todayKey].晚餐.actual.source === 'shopping'`。

### AC-4 库存扣减正确
- **rule**：冰箱 `fridgeItems` 已有番茄 3 / 鸡蛋 6 → 保存"番茄炒蛋"在家做饭后 → `lsGet('fridgeItems')` 里番茄 & 鸡蛋数量均有减少。
- **rule**：冰箱无库存但 shoppingPlans 有近 24h 已购「番茄 2 斤 / 鸡蛋 10 个」 → 保存后 fridgeItems 先自动入库 + 再扣减，toast 含"入库后再扣"字样。
