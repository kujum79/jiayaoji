# 食材准备三场景食谱 → 食记流转（按来源分组+逐道勾选+confirmed才被采购）Implementation Plan

## 一、Repository Research（现状与关键约束）
### 1.1 现有数据存储（唯一真实来源 = localStorage `dietCalendar`）
- `getDietCalendar()` / `saveDietCalendar()`：`index.html:L4224-L4232`。
- 结构：`dietCalendar[dateKey YYYYMMDD][mealType breakfast/lunch/dinner] = { plan: {...}, actual: {...} }`。
  - `plan` 当前字段：`dishes`（字符串数组，兼容老数据；可能混杂 `{name}` 对象但极少）、`note`、`dishMeta:{ [dishName]:{servings?,comment?,aiFeedback?,userComment?} }`（在 confirmAiRecommendation 与其它地方已用，但**尚未包含 source / confirmed 标记**）、`state`（`pending / confirmed / completed`，`getMealPlanState` 在 L4239-L4245 定义）、`confirmed`（遗留布尔字段，`planConfirmed` 判定里把 `plan.confirmed || state==='confirmed'` 都算"已确认"）。
- 约束：用户原文写的 `mealPlans` 是语义名，**不新建独立 localStorage key**（避免数据双写，采购页 generateShoppingPlan 不会读到 mealPlans）。用户语义下的"每道菜 mealPlans"直接落在 `plan.dishMeta[dishName]` 上，形状为 `{ source: 'existing' | 'shopping' | 'weekend' | 'ai', confirmed: boolean, addedAt: ms }`。

### 1.2 三场景写入入口（统一改写为 `dishMeta[].source + confirmed=false` 新契约）
- 场景 1「现有食材生食谱」→ `addToPlanFromFridge(dish, mealType||'lunch')`：L12060-L12115，**默认今天午餐**，原直接 push 到 dishes 数组 + 预扣食材 + `updateMealPlan(planData dishes 数组)`。
- 场景 2「现购食材」→ 仍然走 `renderFridgeMeals` 的 `📋 加入食计` 按钮，即调用点也是 `addToPlanFromFridge(dish, default 'lunch')`（同一个函数，只是 recommend 的食材来源不同，我们在调用时传 `source='shopping'` 即可）。
- 场景 2 的 render 调用：`genRecipesFromIngredients`（L8760 左右）里在 `callAliyunQwenApi / fallback` 之后同样调用 `renderFridgeMeals`，**但当前按钮 onclick 不区分来源**——因此需要把 `renderFridgeMeals` 的按钮改成能传入 source：`<button onclick="addToPlanFromFridge('xxx','lunch','shopping')">`，并且在 `genRecipesFromIngredients` 渲染前把"本次来源"存到一个临时变量/函数参数（最简单方式：`renderFridgeMeals(meals, defaultSource)`，默认 `existing`）。
- 场景 3「周末食谱推荐」→ `selectWeekendPlan(idx)`：L9016-L9055，直接 `updateMealPlan(dateKey, mealType, {dishes:[...], note: title})`，不经过 addToPlanFromFridge；它原本覆盖式写入整个 plan.dishes；**这里必须为每道菜打 `source='weekend'`**。
- 场景 4「AI推荐食谱计划」（用户表格里第四组 "ai"）：包含 `confirmAiRecommendation()`（L23215 附近，推荐详情浮层的「✅采用并填入食记」）以及其它 AI 入口（例如 `addToPlanFromKnowledge()`、`confirmAddToPlan()` L22079-L22169）。这些入口目前只是写入 dishes 数组；**我们要统一在 `updateMealPlan + dishMeta` 里补上 `source='ai'`**，并通过调用方显式传参兜底。

### 1.3 采购读取链路（现在是"只要 plan.dishes 非空就读取"，不能通过状态，必须按需求改成只吃 confirmed）
- `countConfirmedPlanDays(today)`：L8196-L8213，现在判定口径是 `m.plan.dishes.length>0` 就算一天（**没有 filter confirmed**）。
- `computeIngredientNeeds(confirmedDays, servings, cycleDays)`：L8317-L8370，遍历 `confirmedDays -> mealData -> m.plan.dishes.forEach(dsh => { lookup dishMeta })` 只是为了拿价格/分量，**没有过滤 dishes 本身**。
- `generateShoppingPlan()` L12186-L12215：遍历今天起 cycleDays 的每个餐次，直接 `mealData.plan.dishes.forEach(dishName => 累加需求)`。
- 这三处是「只有 confirmed 状态的计划才会被采购计划读取」的核心落地位置。注意用户的 confirmed 定义在**逐道级别**（不是整餐 state），所以：
  - `countConfirmedPlanDays`：改"确认口径"为 —— `meal` 中**至少有 1 道 `dishMeta[name].confirmed=true`** 才算 confirmed 日/餐（原状态兼容保留但降级）。
  - `computeIngredientNeeds / generateShoppingPlan`：`plan.dishes` 遍历时 `if ( (dishMeta[name]||{}).confirmed === true ) 才计入/累加`；**整餐 `state==='confirmed'` 要回退到全餐 dishes 都当 confirmed（兼容旧单人模式自动确认）**，否则老用户的数据突然不会被采购读取就变成大回归——这里的兼容是高风险点，必须用"旧 confirmed=true 的整餐"兜底所有 dishes。

### 1.4 UI「食记」渲染：原 `renderDiary()` L25141-L25329
- 三餐卡 plan 区现在是「一行 dish-tag-clickable 链接用「、」拼文本」，没有分组显示、没有勾选框。
- 需求要：在 **"食谱计划区域"** 按 **4 组顺序** 来源分组；每组内每道菜前 ☑ 复选框；底部/餐卡尾部「确认计划」按钮。
- 现状：每个 mealCard 已有「diary-meal-actions」按钮区（记录实际、外出、AI推荐等按钮），可以直接加个「📋 确认计划」新按钮进去，不影响老按钮。
- 难点：`plan.dishes` 是按「加入顺序」的字符串数组，而不是按 source 分组。需要在渲染时先把 `dishes` 按 `dishMeta[name].source`（默认 `'ai'` 兜底）分到 4 组 buckets，空组也要显示一个空态小提示（例如「本组暂无」），否则用户看不到四格的秩序感。
- 难点 2：renderDiary 的 plan 文本现在还用于 `actualSet / planSet` 对比生成 AI 建议（L25286-L25296），要保留这一对比逻辑，但 `hasPlan` 判定不能改——只要存在 dish 就算"有计划"。
- 难点 3：dish-tag-clickable 原是一个 span 点击直接 showDishDetail，现在每个 dish 前面多了 checkbox，这里要把 dish 元素改成「flex 行：checkbox + 标签 + 删除（可选）」三部分；showDishDetail 仍然绑定在菜名上，不要绑到 checkbox。

---

## 二、Files and Modules（单文件分片精确范围）
仅 `d:\jiayaoji\frontend\index.html`。

分片列表：
- `L2076-L2113` 附近：新增 `.diary-source-group` / `.diary-source-title` / `.diary-dish-row` / `.diary-dish-cb` / `.diary-empty-source` / `.diary-confirm-plan-btn` 的一组 CSS；不要覆盖已有的 `diary-meal-card / dish-tag-clickable`。
- `L4224-L4250`：`getMealPlanState` 注释加说明；加一个工具函数 `getDishMetaSafe(plan, dishName)`，返回合并默认值 `{source:'ai', confirmed:false, addedAt:0}`。
- `L4558-L4620`：`updateMealPlan` 增加**「如果 planData.dishes 中包含同名且 dishMeta 已写 confirmed/source 的保留原值」**的兼容补丁；不对已有 dishMeta 做覆盖（否则每次 `addToPlanFromFridge` 追加一道菜，会把别道菜的 confirmed 洗掉）。
- `L8196-L8215`：`countConfirmedPlanDays` 判定口径改成"逐道 confirmed 或旧整餐 state==='confirmed' 才算"。
- `L8317-L8370`：`computeIngredientNeeds` 的 `m.plan.dishes.forEach` 加逐道 confirmed 过滤 + 旧整餐 state 兼容兜底。
- `L12186-L12230`：`generateShoppingPlan` 的 dish 循环同理过滤。
- `L12033-L12057`：`renderFridgeMeals(meals, defaultSource)` 增加第二参数，按钮 onclick 改成 `addToPlanFromFridge('${dish}','lunch','${defaultSource||'existing'}')`；如果 `defaultSource==='shopping'` 场景 2 生效。
- `L12060-L12115`：`addToPlanFromFridge(dish, mealType, source)` 加第三参数 source（默认 `'existing'`），写到 `planData.dishMeta[dishName] = {...oldMeta, source, confirmed:false, addedAt:Date.now()}`；因为用户要"默认今天午餐 + 回到食记最终确认"，所以**取消原单人模式的自动 confirmed（逐道层面）**；整餐 `state` 在单人模式下从 auto='confirmed' 改回默认 `pending`，只有用户在食记里按「确认计划」才会升为 confirmed（否则逐道默认 false，采购读取不到）。⚠️ 注意不要误伤外出就餐/外卖等其他场景的 auto-confirmed（那两个本来走 actual）。
- `L9016-L9055`：`selectWeekendPlan(idx)` 写入 `updateMealPlan(dateKey, mealType, planData)` 时构造 `planData.dishMeta = { [dishName]:{source:'weekend', confirmed:false, addedAt:Date.now()} }`；同时把整餐 `state` 强制置 `'pending'`（与上述一致，等待用户在食记勾选确认）。
- `L23215-L23260 附近` 与 `L22079-L22169 附近`：`confirmAiRecommendation / confirmAddToPlan / addToPlanFromKnowledge` 等「AI 来源填入食记」的调用点，显式把 `planData.dishMeta[菜名].source='ai'`，逐道 `confirmed=false`，整餐 `state='pending'` 统一口径。
- `L25141-L25329`：`renderDiary()` 的 plan 区改造（核心 UI）——
  1. 对每个 meal，在 `hasPlan` 分支里，不再用字符串 `、` 拼接，改为「4 个 source group + 逐道勾选 + 组内空态提示」的卡片内容结构（在原 `.diary-section-plan` 里呈现）。
  2. source 顺序严格按用户原文：1⃣️ `existing` 现有食材生成食谱 2⃣️ `shopping` 现购食材 3⃣️ `weekend` 周末食谱推荐 4⃣️ `ai` AI推荐食谱计划。
  3. 每组 header 显示「来源图标 + 来源中文名称 + 本组已勾 x/总数」。
  4. 每道菜一行 flex：`<input type="checkbox" class="diary-dish-cb" ${meta.confirmed?'checked':''} data-dish="..." data-meal="...">` + dish 标签（保留 `showDishDetail` 点击）；右侧可以加 `×` 删除（复用原有 remove 行为或直接调 `removeDishFromPlan(dateKey, mealType, dish)`）。
  5. 每组空态 `（本组暂无）`。
  6. 在该 meal card 的 `.diary-meal-actions` 末尾追加一个按钮 `<button class="diary-confirm-plan-btn" onclick="confirmDiaryMealPlan('${meal.key}')">✅ 确认计划</button>`；整餐已全部 confirmed 或 state==='confirmed' 时按钮 badge 显示"已确认"且 disabled。
- 新增 JS 工具（放在 `updateMealPlan / addToPlanFromFridge` 邻近区）：`confirmDiaryMealPlan(mealType, dateKey?)` — 读当前 dateKey 的 meal plan，扫描 `inputs.diary-dish-cb`，把 plan.dishMeta[name].confirmed 同步为 input.checked；再整体设置 `plan.state = (整餐有 confirmed dish 或全部勾选后 state==='confirmed')`；ls 落盘 + `reservePlanIngredients`（整餐 confirm 时再预扣食材，而不是在 addToPlanFromFridge 时提前扣——否则取消勾选无法释放库存）。这里对原「addToPlanFromFridge 立即预扣食材」要做修改：**改成「预扣食材仅在 confirmDiaryMealPlan 确认时执行，取消确认退回」**。否则用户勾选/取消勾选反复会造成 fridgeItems.reserved 错计。这是最关键的"状态单一驱动源"（对应 Experience 100034920 的结论：不能 add 时就改状态/库存，避免两个驱动源互相打架）。
- 兼容老数据迁移：在 `getMealData` 或 `renderDiary` 开始处，做一次性回填：对 `plan.dishes` 中无 dishMeta 的菜，补 `dishMeta[name] = {source: (plan.note && plan.note.includes('周末')?'weekend':'ai'), confirmed: (plan.state==='confirmed'||plan.confirmed===true), addedAt:0}`。这样老数据天然不会"突然掉到未勾选导致采购没菜"，也符合"只有 confirmed 才被采购读取"的新契约。

## 三、Implementation Steps（依赖顺序，避免"先改采购再改写入"导致漏采）
1. **数据层契约先落地（不要先改 UI）**：
   1.1 加 `getDishMetaSafe`；给 `updateMealPlan` 增加 dishMeta 保留逻辑（追加菜不覆盖已确认的同胞菜 confirmed/source）。
   1.2 老数据迁移函数 `_backfillPlanDishMeta(dateKey?, mealType?)`，默认 renderDiary 时对当前 dateKey 执行一次，避免全量扫库阻塞；对 state==='confirmed' 的老数据把每道菜 confirmed=true，source 按是否包含周末/知识库/AI文案判定，其余 'ai'。
   1.3 三场景 + AI 四个写入入口统一打 `source`，并默认逐道 `confirmed=false`；整餐 state='pending'（除非用户后续在 UI 确认）；对 `selectWeekendPlan`、`addToPlanFromFridge`、`confirmAiRecommendation`、`confirmAddToPlan` 四处显式写 `dishMeta[].source`。
2. **采购读取层改读"逐道 confirmed"**：
   2.1 `countConfirmedPlanDays` 餐判定口径：整餐 state==='confirmed'（兼容老） 或 该餐至少一道 dishMeta.confirmed=true（新契约）；不改变循环顺序与返回结构（返回 dayList/daysObj 不变）。
   2.2 `computeIngredientNeeds` 的 `m.plan.dishes.forEach` 过滤：只有 `(dishMeta[d]||{}).confirmed === true` 或 **整餐 state==='confirmed'（兼容旧单人模式）** 才计数。这一步必须**兜底**，否则老用户刚升级后，所有以前 confirmed 的老计划会全从采购单消失 → 严重回归。
   2.3 `generateShoppingPlan` 的 dish 遍历同上过滤。
3. **库存预扣的驱动源统一**：
   3.1 把 `addToPlanFromFridge` 原立即执行的"冰箱 reserved++"那一段删除（或通过条件保护只在老 confirmAiRecommendation 特殊路径不执行），保留食材不足提示触发"加入 shoppingPlans"的 confirm 弹窗——但弹窗条件从"加入时立刻扣 → 不足"改成"确认计划时扣 → 不足"；为了最小改法，可以把这段预扣逻辑直接抽成一个独立 `reservePlanIngredients(dateKey, mealType, reservedSetOfDishNames?)` 并在 `confirmDiaryMealPlan` 成功后调用。
4. **UI renderDiary 按来源分组 + 逐道勾选 + 确认按钮**：
   4.1 追加一组 CSS 类（不破坏原 dish-tag-clickable）。
   4.2 `renderDiary` plan 区：`meal.forEach` 内部 `hasPlan` 分支重写 dish UI 渲染；保留菜品排序规则（主食第 1 / 汤羹第 2）并在 source group 内部排序（组间不动，保持 1-2-3-4 顺序）。
   4.3 checkbox change 事件即时写回 `dishMeta[].confirmed` 并 `saveDietCalendar`（避免用户勾完不点确认就离开，勾选状态丢失）。
   4.4 `diary-meal-actions` 追加「✅ 确认计划」按钮；已确认（全部已勾或 meal state==='confirmed'）时显示 disabled + 「已确认」badge；否则点击后 `confirmDiaryMealPlan(meal.key)` → toast 成功 → renderDiary 刷新。
5. **新增模态 confirmDiaryMealPlan(mealType, dateKey)**：无需自定义浮层——直接 sync 逻辑 + toast；若当前餐所有组都是「本组暂无」，toast 提示先加菜。

## 四、Dependencies and Considerations
- 采购链路的"老 state==='confirmed' 兼容兜底"是最高级风险。严格规则：
  - **只要 `plan.state==='confirmed' || plan.confirmed===true`，就当 plan.dishes 全部逐道 confirmed=true**（对老数据 / 旧单人模式自动确认的结果保持 100% 无感知）。
  - 新增写入一律"逐道默认 false，整餐 state 一律 pending"。
- `renderDiary` 与 `computeIngredientNeeds` 中 dishes 处理的**规范化**必须保持一致：`typeof d==='string'?d:(d.name||'')`，否则老数据中 plan.dishes 混存对象时，dishMeta[name] 找不着 → 采购读不到也显示没勾。
- 取消"addToPlanFromFridge 立即预扣食材"会让 add 时的「已预扣 N 种食材」的 toast 消失，需要**换一种更温和的文案**：`✅ 已将 XXX 加入今日午餐计划（请到食记页面勾选并确认计划）`，不再提"已预扣 N 种食材"。否则语义前后不一致。
- 用户需求里"添加到食记按钮 → 分配到对应日期餐次（默认今天午餐）"的统一流转：
  - 场景 1/2 均用 `addToPlanFromFridge(dish, 'lunch', source)` 精准写入今天午餐；
  - 场景 3 按方案卡片中的真实 dateKey 与真实 mealType（早/中/晚）写入；
  - 场景 4 AI 推荐（`confirmAiRecommendation / confirmAddToPlan`）沿用它们当前默认选的 targetDate / mealType，但统一 dishMeta.source='ai'。
- 三场景按钮 1/2/3 后 UI 最好有一个「已加入今天午餐计划」→ 自动切换到食记 tab 的弱引导（toast 中附一个文案即可，不用硬跳转），保证用户能自然地"加完 → 去食记勾选确认"闭环。
- 关于 `confirmed` 语义：用户要求"逐道勾选框 + 确认计划后状态变为 confirmed（单道菜 level）+ 整餐 state 也会升 confirmed"。这里我们采用**双写**：`dishMeta[name].confirmed=true`（驱动采购），且当该餐**存在至少 1 道 confirmed** 时把 `plan.state='confirmed'`（这样整餐 badge 与老 renderDiary 的 statusBadge 一致）。取消勾选把所有 confirmed 置 false 时，`plan.state='pending'`，兼容旧的 statusBadge 显示。

## 五、Validation（必须全部通过才能答复）
- 静态门禁：`GetDiagnostics = 0 errors`；未引入任何 `window.prompt/confirm`。
- 390px 布局门禁：`bodyDelta = scrollWidth - clientWidth = 0`（四个来源分组的行不横溢）。
- 数据写入门禁（三场景 + AI）：
  - [ ] 点击「现有食材生食谱」任意菜「加入食计」→ 今天午餐 plan.dishes 包含该菜，`dishMeta[name].source==='existing'` 且 `confirmed=false`。
  - [ ] 点击「现购食材」结果浮层任意菜「加入食计」→ source==='shopping'，confirmed=false。
  - [ ] 选择「周末食谱推荐」任一方案 → 对应两天三餐所有写入菜，source==='weekend'，confirmed=false。
  - [ ] 走 AI 推荐浮层「✅采用并填入食记」→ 该日对应餐 dishMeta.source==='ai'，confirmed=false。
- 分组 UI 门禁：
  - [ ] 食记 tab 打开后三餐卡 plan 区均按 1.existing/2.shopping/3.weekend/4.ai 顺序显示；空组也显示"本组暂无"（四组分段确实可见）；
  - [ ] 每道菜前 checkbox 可独立勾选/取消；切换 tab 再回到食记，勾选状态保持未变（写回 LS 的 dishMeta.confirmed 生效）；
  - [ ] 同一 meal 的「✅确认计划」按钮点击后，该餐 state==='confirmed'，已勾选的菜 dishMeta.confirmed=true；再次打开页面未改变。
- 采购读取门禁：
  - [ ] `generateShoppingPlan` 在未勾选任何菜时，采购结果清单不包含任何从"未勾菜"来的食材（可用一个对照 dish 如「番茄炒蛋」未勾但已加入，断言番茄鸡蛋在清单里不存在）。
  - [ ] 对一个老数据的 plan.state==='confirmed'（未加 dishMeta）进行采购生成，所有原 dish 都正常被计入（兼容兜底验证）。
  - [ ] 勾选 2/3 道菜后，`countConfirmedPlanDays` 计数+1，采购清单只包含勾中的菜（逐道级别粒度验证）。

## 六、Risks 与处理
- **风险1（最严重）：老单人模式自动 confirmed 的数据突然从采购消失** → 处理：2.2/2.3 过滤必须带 `if (plan.state==='confirmed' || plan.confirmed) 则该餐所有 dishes 一律当 confirmed；只有 `plan.state==='pending' 且无 plan.confirmed` 的"新写入数据"才强制按逐道 dishMeta.confirmed 判断。
- **风险2：addToPlanFromFridge 预扣食材与取消勾选的库存矛盾** → 处理：3.1 删除 add 时的即时预扣，改到 `confirmDiaryMealPlan` 统一 `reservePlanIngredients(dateKey, mealType)` 执行；再次取消确认/取消勾选时调用一个 `unreservePlanIngredients`，对同一 dateKey+mealType 的所有 reserved 归零重算（避免累计错误）。
- **风险3：renderDiary 重写 plan 区后，L25286 planSet/actualSet 的 AI 建议对比失效** → 处理：新 planSet 仍然是所有 dish 的 dishName 并集（不按 source 过滤），所以在新分组循环里用一个 Set 去加所有 dish，用于 actual 对比；跟原文口径一致。
- **风险4：周末方案写入时覆盖老 dishMeta 的 confirmed/source** → 处理：写入前做 `Object.assign(existingMeta, {source:'weekend'}, confirmed ? {} : {confirmed:false})` 形式 merge，若用户此前已在同一餐把某菜勾成 confirmed，不被周末方案覆盖回 false（更符合用户预期的"已确认不被覆盖"）。
- **风险5：4 组空态+逐道 checkbox 在 390px 下导致横溢** → 处理：每行 flex wrap + `min-width:0`；checkbox width:16px、标签 ellipsis、来源标题 icon+中文 短名（现有/现购/周末/AI）不超 20 字；用真实脚本测 bodyDelta=0 后才算通过。
