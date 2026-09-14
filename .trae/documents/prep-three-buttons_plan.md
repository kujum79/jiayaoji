# 食材准备页面新增 3 个并排按钮 实现计划

## 需求原文（VERBATIM）
> 请在“食材准备”页面的“现有食材”版块下面，并排从左至右增加三个按钮，一个按钮是“现有食材生食谱”，基于家中已有食材生成菜谱推荐；第二个按钮是“现购食材”，点击此按钮后，弹出的页面用户可录入新买食材（名称、单价、数量、单位、费用），AI生成菜谱；第三个按钮是“周末食谱推荐”，AI生成2-3个周末完整方案，用户选择后生成采购清单；

## Repository Research（现有结构与结论）
- 代码库仅允许修改 **d:\jiayaoji\frontend\index.html**（单文件 SPA）。
- “食材准备”默认视图（`#fridgeRecView > #recContent`）由 `renderFridgeRecView()` 渲染，位置：`index.html:L7321-L7550`。
  - 顶部 "现有食材（部分）" 标签条通过 `.prep-overview` 卡片拼在 `L7496`：`<div class="prep-overview" style="margin:12px;">...${tagsHtml}</div>`，这正是用户所说的「现有食材版块」。
  - 紧接其后（L7498-L7512）是参数卡 `.prep-card`（采购周期 / 用餐人数），然后是采购清单表格 `.prep-shop-card`（L7518-L7537）。
  - 本计划将 3 个并排按钮插入在 **现有食材 overview 卡和参数卡之间**（即 `L7496` 之后的 `html += `<div class="prep-ai-btn-row">...` 一段），确保「现有食材版块下面」语义正确。
- 按钮 1（基于已有食材生成菜谱推荐）已有完整现成函数 **`generateFridgeMealPlan()`**（L11132-L11180）：读取 `localStorage.fridgeItems`，按 `DISH_INGREDIENTS` 匹配/部分匹配 3~8 道菜，使用冰箱已有的结果浮层 `#fridgeResultOverlay` 展示，可直接复用。按钮 1 的 onclick 直接调用 `generateFridgeMealPlan()` 即可。
- 按钮 2（录入新买食材 → AI 生成菜谱）：
  - 没有直接对应模态，按硬约束必须使用 `<div>` 自定义浮层（不能用 `window.prompt/confirm`）。项目已有现成浮层基例：
    - `fridge-modal` 基础样式 + `.fridge-modal-content` + `.fi-modal-header` + `.fi-row/.fi-col` 表单栅格（L1859-L1874）。
    - 具体参考「添加采购食材」模态 `#prepAddItemModal`（L3527+，`index.html:L3528-L3539`），字段 grid、保存/取消按钮模式完全可复用。
  - 录入字段严格按用户原文 5 列：**名称（文本）、单价（数字，元）、数量（数字）、单位（下拉/文本）、费用（数字只读 = 单价×数量，用户可手改）**，允许加多条（一条 + 「再加一条」），确认后把列表临时写入 `prep.newPurchasedItems[]`（或临时全局），然后调用 AI 生成菜谱：
    - 现有 AI 调用范式 `callAliyunQwenApi(systemPrompt, userMessage)`（L22167-L22226，通过 `/plan` 代理）可直接复用；返回 JSON 解析后复用 `renderFridgeMeals()` + `#fridgeResultOverlay` 展示。
  - 新买食材在 AI prompt 里合并「家中已有（fridgeItems）+ 用户刚录入新买」，避免只用新买，保证温馨家常结果更准确。
- 按钮 3（周末食谱推荐，AI 生成 2~3 个完整方案 → 用户选择 → 生成采购清单）：
  - 项目已有 AI 确认弹窗范式 `showAiRecommendationModal(recommendation, targetDate)`（L22229+），以及 `generateWeeklyAiMealRecommendation(7)` 调用模式（L27317），可直接引用。
  - 新增逻辑：
    1. 计算最近两个周末（周六+周日）的 dateKey 范围；
    2. `/plan` 请求明确要求返回 2~3 套方案数组（每套包含周六/周日每天三餐 dishes 数组或 dishesMeta），标题为「方案 A/B/C：(主题，例"团圆聚餐/轻食周末")」；
    3. 渲染卡片浮层，每套卡片底有「选本方案 → 生成采购清单」按钮；
    4. 用户点击选中后，将该套方案写入 `dietCalendar`（保持 AI 确认流程一致性），然后直接调用现有 **`generateShoppingPlan()`**（L11335+）以复用扣除冰箱已有、按分类分组、采购打勾等逻辑。
- 已有的硬规则（严禁违反）：
  - 新增交互禁止使用 `window.prompt / window.confirm`，按钮 2 表单确认、按钮 3 方案选择、空值校验全部走自定义 div 浮层 / toast（`showToast` 已存在）。
  - 所有数据走 localStorage（`fridgeItems / dietCalendar / shoppingPlans / purchaseHistory` 等），不改动 backend。
  - 移动端 `bodyDelta = scrollWidth - clientWidth = 0`（390px 窄屏不产生横滚）。

## Files and Modules（仅一个文件，分片定位）
- `d:\jiayaoji\frontend\index.html`
  - **CSS（新增样式片，不破坏现有）**：在 `/* ===== 食材准备（采购准备中心） ===== */` 区块（约 L1877-L1994）末尾追加：
    - `.prep-ai-btn-row`：390px 下 3 按钮并排，`display:flex; gap:8px; margin: 8px 12px;`，按钮等宽（`flex:1`）避免换行。
    - `.prep-ai-btn`：统一按钮视觉（圆角/边框/hover 态，颜色与 `prep-add-item-btn` 同一色系，用橙/暖棕与 Logo 对齐）。
    - `.prep-ai-btn.primary / .secondary`：三级差异化色（按钮 1 偏橙、按钮 2 偏绿、按钮 3 偏紫，便于区分，不影响 accessibility）。
    - `.prep-new-buy-list`：按钮 2 模态中多行食材输入列表容器；`.prep-new-buy-row` 单行栅格。
  - **HTML 静态结构（新增两个模态骨架）**：紧邻现有 `#prepAddItemModal`（L3527+）后追加：
    - `#prepNewBuyModal`：录入新买食材（fi 标题「现购食材录入」、加行按钮、取消/AI生成按钮）。
    - `#prepWeekendPlanOverlay`：展示 2~3 套周末方案卡片（复用 `fridge-result-overlay` 基样式或新的 `.prep-del-confirm-ov` 变体）。
  - **视图渲染（`renderFridgeRecView`，L7321-L7550）**：在 L7496 现有食材卡片之后、L7498 参数卡之前，追加 3 按钮 html 段：
    - 按钮 1 `<button class="prep-ai-btn ..." onclick="generateFridgeMealPlan();">现有食材生食谱</button>`
    - 按钮 2 `<button class="prep-ai-btn ..." onclick="openPrepNewBuyModal()">现购食材</button>`
    - 按钮 3 `<button class="prep-ai-btn ..." onclick="generateWeekendRecipes23()">周末食谱推荐</button>`
  - **JS 函数（全局挂到 window，保证内联 onclick 可解析，且不污染其它模块）**：
    - 按钮 2 相关：`openPrepNewBuyModal / closePrepNewBuyModal / addOneNewBuyRow / calcNewBuyCost(el) / submitNewBuyAndGenRecipe / genRecipesFromIngredients(newItems, useFridgeMerge=true)`。
    - 按钮 3 相关：`generateWeekendRecipes23() / renderWeekendPlans(plans) / selectWeekendPlan(idx)`。
    - 辅助：`_weekendDateRange()`（返回最近周六~周日 dateKey 数组，便于复用）。

## Implementation Steps（依赖顺序）
1. **CSS 追加**：在 `.prep-shop-cat-row:first-child td { ... }` 前（L1969 附近块内或紧邻 `.prep-btn-confirm` 之后）追加 4 类：`.prep-ai-btn-row / .prep-ai-btn / .prep-new-buy-list / .prep-new-buy-row`，保证 390px 下 3 按钮不换行、不横溢（`flex:1` + `min-width:0` + `font-size:12px` + 字少）。
2. **两个静态模态骨架写入 HTML**：
   - 模态 2（现购食材录入）：使用 `fridge-modal` 样式 + `.fi-modal-header`（返回键「<」关闭，标题「现购食材录入」，右按钮「🤖 生成菜谱」）；body 内置空容器 `#prepNewBuyList` + 底部「+ 再加一条食材」行按钮 + 取消/生成双按钮。
   - 模态 3（周末方案选择）：用 `.prep-del-confirm-ov` + 自定义 `.prep-weekend-plan-box`（更宽 max-width 410px，带标题「周末食谱推荐」+ 下方滚动容器 `#prepWeekendPlansWrap` + 底部「取消」）。
3. **renderFridgeRecView 插入按钮条**：`tagsHtml`/采购参数之间新增 `.prep-ai-btn-row` 条，三按钮 onclick 分别 `generateFridgeMealPlan()` / `openPrepNewBuyModal()` / `generateWeekendRecipes23()`。
4. **按钮 2 JS 实现**：
   - `openPrepNewBuyModal`：先清空 `#prepNewBuyList`，默认塞 1 空行（5 input：名称/单价/数量/单位/费用），`modal.classList.add('show')`；
   - 每行 5 列：单位建议带 `<datalist id="prepUnitDatalist">`（斤/个/袋/盒/把/包/克/毫升/勺 等常见值），`费用` 默认只读但可改，`单价×数量 oninput 自动更新`；
   - 「再加一条」按钮：`addOneNewBuyRow()` 往容器 append 同款结构；
   - 校验：提交前任何一行名称为空 toast 提示；
   - `submitNewBuyAndGenRecipe`：收集为 `[{name, unitPrice, quantity, unit, cost}]`，然后调用 `genRecipesFromIngredients(newItems, true)`；
   - `genRecipesFromIngredients`：合并 `fridgeItems`（家中已有）+ newItems，构造 system/user prompt → `callAliyunQwenApi` → 返回 JSON 格式 meals（3~6 道菜）+ 每道菜 have/need 食材标签 → 走 `renderFridgeMeals(meals)` + 打开 `#fridgeResultOverlay`（跟按钮 1 的展示壳一致，用户仍可「加入食计」）。
   - 同时把 newItems 写入临时 localStorage key `tempPrepNewBuy` 便于下次打开或回溯；若用户从结果浮层触发采购，通过 `_prep.extraItems` 追加到当前采购清单（或通过 `newItems.forEach( i => push to _prep.extraItems )`；如未触发则不持久，避免误把「临时输入」污染冰箱库存）。
5. **按钮 3 JS 实现**：
   - `generateWeekendRecipes23`：取 `_weekendDateRange()`（默认最近一个周六+周日共 2 天；如今天是周六/日则取当前周末），读取 familyData / 已吃历史，调用 `/plan`，要求返回 2~3 套方案 `plans: [{ title, days: { dateKey: { breakfast:[], lunch:[], dinner:[] } } }]`。
   - AI 失败兜底：使用 `DISH_INGREDIENTS` 中的家常菜（番茄牛腩、清蒸鲈鱼、蒜蓉西兰花等）拼 2 套静态方案，toast「已用家常菜兜底，稍后重试 AI」。
   - `renderWeekendPlans(plans)`：写入 `#prepWeekendPlansWrap`，每张方案卡头部 🏷️ 主题 + 摘要说明，每天三餐卡片罗列菜品名，底部按钮「选本方案 → 生成采购清单」。
   - `selectWeekendPlan(idx)`：将选中方案的 dishes 写入 `dietCalendar[dateKey][meal].plan`（走 `updateMealPlan`），关闭周末浮层，**直接调用 `generateShoppingPlan()`** 打开采购结果浮层（包含按分类分组、用量、勾选、冰箱已有扣除），与原采购链路一致。
6. **CSS/窄屏兼容兜底**：在按钮条和两个新增模态容器上统一加 `box-sizing:border-box; max-width:100%;`，模态内 `.fi-row .fi-col` 保留原有 flex 1/2/3 布局，确保 390px 下 bodyDelta=0。
7. **验证**：GetDiagnostics=0 errors；浏览器 390px 宽按按钮 1/2/3 三条手动/脚本断言。

## Dependencies and Considerations
- `/plan` 代理在离线/慢网情况下会超时 60 秒并抛错；按钮 2/3 必须 **catch 错误 + toast 提示 + 提供静态兜底**（按钮 2 用 `DISH_INGREDIENTS` 匹配出 3 道与 newItems 交集最大的菜；按钮 3 用上述家常菜 2 套静态方案），不允许 UI 空白卡死。
- 按钮 2 的「费用」用户要的是字段而不是"单价×数量"，所以要允许**只读**但**可手改**——实现为 `type=number` 且在单价/数量改变时自动填入 `单价×数量`，用户仍然能直接手改费用（当有优惠券/折扣、按袋计价时）。
- 按钮 3 写入 `dietCalendar` 必须走 `updateMealPlan`（而不是直接 lsSet），保证与 `computeIngredientNeeds / countConfirmedPlanDays` 逻辑一致，采购清单才能正确汇总。
- 不触碰 `backend/main.py`、`images/logo.png`、老的 `generateShoppingPlan / recShopPlan` 等模块；所有新增逻辑放在 `index.html` 对应分片内，最小闭包。

## Validation（实现完成后必须通过）
- **静态门禁**：`GetDiagnostics` 返回 0 errors。
- **布局门禁**（390px 窄屏）：
  - 新增按钮条与 2 个模态容器均不横溢：`document.body.scrollWidth - document.body.clientWidth === 0`（bodyDelta=0）；
  - 按钮 1/2/3 视觉为「从左至右并排、等宽、间距一致、文字不换行」。
- **功能门禁**（三条均触发 1 次）：
  - 按钮 1「现有食材生食谱」：非空 fridge 触发 `generateFridgeMealPlan()`，`#fridgeResultOverlay` 打开且卡片数 ≥ 1；空 fridge 给出正确 toast。
  - 按钮 2「现购食材」：打开浮层→填 1 行（番茄 5元 2斤 斤 10元）+ 点「再加一条」→ 填（鸡蛋 7元 1盒 盒 7元）→点「🤖 生成菜谱」→结果浮层开，至少含 1 张卡片且卡片食材标签包含「番茄/鸡蛋」；失败则兜底命中且 toast 正常。
  - 按钮 3「周末食谱推荐」：点击→浮层开，方案数 ∈ [2,3]→选第 1 方案→采购清单浮层打开（`generateShoppingPlan` 被触发），至少 1 项食材或"所有食材冰箱已有 🎉" 空态；写入 dietCalendar 的 dishes 可用 `lsGet('dietCalendar')` 断言不为空。
- **模态约束门禁**：全程无任何 `window.prompt / window.confirm` 弹窗。

## Risks 和处理
- 风险1：390px 下 3 按钮文字过长换行或撑破 → 处理：按钮文案严格用用户原 8/4/6 字（不额外加 emoji 前缀），`font-size:12px`，必要时 `letter-spacing:-0.2px`，容器 `flex:1 1 0; min-width:0`。
- 风险2：按钮 3 `/plan` 返回格式不稳定（返回非 plans 数组或数组为空）→ 处理：JSON 解析失败 + 数组长度 < 2 统一走静态家常菜兜底，toast 说明并保持后续「选方案 → 生成采购清单」链路可用。
- 风险3：按钮 2 录入新食材被误写入「冰箱库存」`fridgeItems` → 处理：仅写入临时 `tempPrepNewBuy` + `_prep.extraItems`（与当前采购计算链路打通），**不写入 `fridgeItems`**；只有用户在采购清单最终确认后，才走 `finalizePrepPurchase` 的入库逻辑。
- 风险4：新增 CSS 类名与现有 `.prep-btn-confirm / prep-add-item-btn` 冲突 → 处理：统一加前缀 `.prep-ai-btn-*`，且使用新的独立选择器块，不在原选择器上修改。
