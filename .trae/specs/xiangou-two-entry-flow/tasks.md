# xiangou-two-entry-flow 任务清单

## Task 1: renderFridgeMeals 双按钮 + shopping 专属顶部说明 banner
- **Priority**: high
- **Depends**: None
- **文件**: `d:\jiayaoji\frontend\index.html`
- **范围**:
  1. `renderFridgeMeals` 旧按钮 `📋 加入食计` → 改为 2 按钮并排（L13233-13235 段）：
     - 「🍳 今晚就吃」`onclick="jumpToDinnerTonight('${escapeAttr(meal.dish)}', ${escapeAttr(JSON.stringify(meal))}, '${escapeAttr(src)}')"`
     - 「📋 加入食记计划」`onclick="addToPlanFromFridge('${escapeAttr(meal.dish)}', 'lunch', '${escapeAttr(src)}')"`
  2. 在 `container.innerHTML = html` 前，若 `src === 'shopping'`：在 html 顶部插入说明 banner（div 用 ai-suggestion-box 家族样式），包含两句话：
     - 「🍳 今晚就吃 → 一键直达『食记·记录实际』：菜品填好/餐次=晚餐/在家做饭，补个评价保存就自动扣库存。」
     - 「📋 加入食记计划 → 今天午餐先入计划（未确认、不扣库存），稍后到『食记』勾选→确认计划。」
- **Test Requirements (TR)**:
  - **rule**：`genRecipesFromIngredients(items, false)` 生成 3 道菜后，每张卡 2 个按钮可见，文本=今晚就吃/加入食记计划；顶部 banner 含「今晚就吃 →」和「加入食记计划 →」。
- **Status**: pending

## Task 2: 新增 jumpToDinnerTonight(dishName, mealObjJson, source) 做「今晚就吃」跳转/填菜/切餐次/切 Tab/切在家做饭/写 banner
- **Priority**: high
- **Depends**: Task 1
- **范围**:
  1. 在 `renderFridgeMeals` 附近或紧跟其后新增全局函数 `jumpToDinnerTonight(dishName, mealObj, source)`：
     - 参数 dishName=字符串；mealObj 可能是 JSON string 或对象（try parse）；source=shopping/existing/…
     - 校验空名 → toast 返回；
     - 日期=今天 `dateToKey(new Date())`；
     - 调 `switchTab('diary')` → 等 panel 出现；
     - 调用 `openAddRecordModal()`；
     - 在 DOMContentLoaded after 条件里同步餐次按钮 active / dining 按钮 active：
       - `currentRecordFormMeal = '晚餐'`；触发对应 `#recordMealTypes [data-meal=晚餐]` 点击或手动 classList 加 active；
       - `currentRecordFormDining = '在家做饭'`；对应 `#recordDiningToggle [data-dining="在家做饭"]` 加 active；`recordDiningExtra` display=none；
       - `recordFormDate.value = 今天`；
     - 去重：`recordFormDishes.some(d=>d.name.trim()===name.trim())` → toast "菜品已在记录表单，请补评价再保存"；
     - 否则 `addDishToForm(name)` 后：
       - 找到新 dish（`recordFormDishes.findLast?` 或 `.slice(-1)[0]`）→ `d.sourceMeta = {source:(source==='shopping'?'shopping':source||''), addedBy:'tonight_button', at:Date.now()}`；
       - `d.userComment = '🛒 来源：现购食材推荐（今晚就吃）'`；
       - 再 `renderFormDishList()`；
     - 顶部 banner：`recordAiFeedbackArea` 首节点插入 `div.record-source-banner`（背景 #ECFDF5 / 边框 #A7F3D0），文字包含「从现购食材·今晚就吃 带入：$dishName；已设为今天晚餐·在家做饭；补完评价按 保存 就自动扣库存 ✅」。
     - 关闭 `fridgeResultOverlay`：`closeFridgeResult()`；toast：「已跳转到记录实际，菜品填好，去加个评价吧～」。
- **TR**:
  - **rule**：点「今晚就吃」后，`recordFormDishes` 含菜名，`recordFormDate=今天`，`currentRecordFormMeal=晚餐`，`currentRecordFormDining=在家做饭`；tab=diary。
- **Status**: pending

## Task 3: 加入食记计划（addToPlanFromFridge）回归 + 文案/按钮改名
- **Priority**: low
- **Depends**: Task 1
- **范围**:
  - 不改动 addToPlanFromFridge 行为；只把按钮文案改成「📋 加入食记计划」（上一轮按钮之前写的是「📋 加入食计」错别字也一并修）。
  - 保持 toast 含「请去食记页面勾选确认」原文，不另改逻辑。
- **TR**:
  - **rule**：对同一菜连点 2 次加入食记计划，第 2 次 toast 提示已在计划中并跳去 diary tab。
- **Status**: pending

## Task 4: 保存实际记录：追加顶层 source=shopping 逐道 sourceMeta；同步到 updateMealActual；库存扣减兜底（现购先入库再扣）
- **Priority**: high
- **Depends**: Task 2
- **范围**:
  1. 修改 `submitAddRecord()`（L18658-18713）：
     - 在构造 record 时：新增 `source: (recordFormDishes.some(d => d && d.sourceMeta && d.sourceMeta.source === 'shopping') ? 'shopping' : '')`；
     - `record.dishes.map(d=>...)` 返回对象中保留 `sourceMeta: d.sourceMeta || undefined`。
     - 保存到 `mealRecords` 后，立即写一份到日历 `updateMealActual(date, (mealTypeMap[currentRecordFormMeal]||'dinner'), {dishes:record.dishes, diningType:currentRecordFormDining, comment:overallComment, source:record.source, savedAt:record.createdAt, id:record.id})`。
     - 原 `deductIngredientsForDish` 循环追加兜底：
       ```
       const deducted = deductIngredientsForDish(dish.name);
       if (deducted.length === 0 && (record.source === 'shopping')) {
         const filled = fillFridgeFromShoppingPlansForDishThenDeduct(dish.name); // 新辅助
         deductedItems = deductedItems.concat(filled);
       } else deductedItems = deductedItems.concat(deducted);
       ```
     - toast 里区分：`"已保存，扣减"+deductedItems.length+"种食材"` + `(filled>0 ? `（含从现购清单入库 ${filled} 种后再扣）` : '')`。
  2. 新增辅助函数：
     - `fillFridgeFromShoppingPlansForDishThenDeduct(dishName)`：
       - 读 `shoppingPlans = JSON.parse(lsGet('shoppingPlans')||'[]')||[]`；
       - 取 24h 内 `purchased === true` 项 `purchasedAt > Date.now()-86400000`；
       - 若菜 ingredients（`DISH_INGREDIENTS[dish].ingredients`）与 shoppingPlans 项名匹配 → 对命中项调用 `addFridgeItem`（或直接 push 到 fridgeItems）标记 `broughtBy='tonight_autobank'`，saveFridgeItems；
       - 再 `deductIngredientsForDish(dishName)` 返回命中的扣减列表；
- **TR**:
  - **rule**：保存后 `mealRecords[last].source === 'shopping'`；`getMealData(today, '晚餐').actual.source === 'shopping'`。
  - **rule**：冰箱有足量库存时，保存后冰箱对应食材数量减少；无库存但近 24h shoppingPlans 已购项存在时，先入库再扣减，toast 含「入库 xx 种后再扣」。
- **Status**: pending

## Task 5: 420px 布局与双按钮不换行样式 + banner CSS（必要时追加 10 行）
- **Priority**: medium
- **Depends**: Task 1, Task 2
- **范围**:
  - `.fridge-recipe-actions`：`display:flex; gap:6px; flex-wrap:nowrap;`；
  - 两按钮各自 `flex:1 1 0; min-width:0;`，文字不截断允许换行。
  - `.record-source-banner`：复用 `.ai-suggestion-box` 外观或直接写 4 行内联 style 一致样式。
- **TR**:
  - **rule**：`body.scrollWidth−clientWidth === 0` （420px 视口）。
- **Status**: pending

## Task 6: 门禁（GetDiagnostics / Syntax / 端到端全流程）
- **Priority**: high
- **Depends**: Task 1-5 all completed
- **范围**:
  1. `GetDiagnostics(index.html) === []`。
  2. node `new Function(allScriptsText).call({})` 不抛 SyntaxError。
  3. 端到端：
     - 切食材准备 Tab → 点现购食材 → Tab=买后录入 → 填番茄 2 斤/鸡蛋 10 个 → 点 🤖 生成菜谱 → 首卡 今晚就吃 → 校验 AC-1 → 再第二卡 加入食记计划 → 校验 AC-2 → 回记录实际表单 → 保存 → 校验 AC-3/AC-4；
     - 全程 console 无新增 ERROR（允许旧 logo_aborted / autoCheck 失败老错）。
- **TR**:
  - **rule**：诊断通过 & 语法无误 & 全流程 5 步均通过上面验收规则。
- **Status**: pending
