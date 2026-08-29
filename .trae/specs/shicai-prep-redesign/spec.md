# 「食材推荐」→「食材准备 / 采购中心」重构 规范 (spec)

## 1. 问题 / 背景

当前「食材推荐」Tab（`panelFridge` → `fridgeRecView` 模式）存在以下问题：
- 核心功能只是"食材提示 + 一日食谱展开 + 日期选择器跳转采购计划"，缺少以"采购准备"为核心的闭环；
- 采购流程与页面分散：日期选择器、采购计划按钮、每日早午晚菜品列表在用户一次性采购场景下冗余且割裂；
- 采购计算缺少「采购天数 × 用餐人数 × 一人份用量 = 总需求量，再对比库存算需购量」的显式公式展示；
- 没有确认采购弹窗、删除多余项、生成采购记录、入库、查看历史等全流程。

用户需求：把「食材推荐」页面改造成"**食材准备**"（即「采购准备中心」），作为食材 Tab 的默认视图，一站式完成：查看家中库存 → 设置采购参数 → 自动计算食材需求量 → 勾选确认 → 确认采购弹窗 → 生成采购记录 → 自动入库 → 查看历史。

## 2. 用户 / 目标

**目标用户**：家肴记普通使用者（负责家里买菜的人）。

**业务目标**：
- 基于"已确认的 N 天食谱计划"，用最短操作流程生成一份可执行的采购清单；
- 让用户直观看到：哪些已有、要买多少、预估总价；
- 提供确认后自动登记采购记录、同步库存的闭环。

## 3. 范围

### 3.1 In Scope（本页 + 相关持久化）
- 默认视图 `fridgeRecView` 重写：标题、文案、布局、控件全部替换；
- 顶部标题 `topTitle` 在默认 rec 模式下改为"食材准备"；
- `titles.fridge` 字典从"食材推荐"改为"食材准备"；
- 新增页面字段：采购周期下拉（2/3/7/自定义）、自定义天数输入、用餐人数（只读）；
- 新增基于食谱汇总计算逻辑：一人份用量 × 用餐人数 × 采购天数 = 总需求量；
- 已有食材对比：从 `fridgeItems` 扣减，得到"需购量"；
- 采购食材清单 UI：每行「☐ + 名称 + 总需求 + 用量说明 + 需购量 + 预估价格」；
- 底部「➕ 添加食材」按钮；
- 确认采购弹窗（清单、可删除条目、最终确认）；
- 采购记录历史查看页（当月，按时间倒序）；
- `purchaseHistory` 数据结构升级（增加用餐人数、采购天数、计划日期范围、每项明细字段）。

### 3.2 Out of Scope（本次不做）
- 不改动编辑/添加食材模态框（已在上一轮改造完成）；
- 不改动食材管理视图（`fridgeHomeView`）和分类管理视图（`fridgeCategoryView`）；
- 不改动独立的「采购计划」子视图 `fridgeRecShopView`（暂保留，后续可删，此轮不触达渲染入口）；
- 不改动 `generateShoppingPlan()` 函数（它服务于食材管理视图里的"📋 生成采购计划"）；
- 不做价格接口/真实比价，预估价格用本地默认单价表；
- 不做多人协作/多端同步。

## 4. 非功能要求
- 仅改前端 `d:\jiayaoji\frontend\index.html`；
- 所有数据从 localStorage 读取，使用既有 `lsGet/lsSet`、`getDietCalendar()`、`getDishIngredientsWithServings()` 等函数；
- 无控制台错误；
- 移动端友好（最大宽度 430px，在 App 容器内嵌）；
- 改动点使用 GetDiagnostics 无语法错误。

## 5. 功能需求 (FR)

### FR-1 标题与现有食材区
- 页面顶部标题由"食材推荐"改为"**食材准备**"。
- "现有食材提示（部分）："文案改为"**现有食材（部分）：**"。
- 右侧用紧凑标签形式，按分类展示：`蔬菜  番茄×数量; 肉蛋奶  牛奶×数量；方便食品  牛肉饼×数量…`。
- 标签显示"名称×数量"格式：数量使用 `item.quantity`，若数量缺失则只显示名称。
- 点击任一标签，切换到底部 Tab 的食材页（即 `switchFridgeView('category')`，当前推荐页→家中食材分类视图）。

### FR-2 移除原冗余元素
- 移除原页面中「日期选择器」（前后箭头 + 星期 + 回今天/回明天按钮）。
- 移除原页面中「采购计划」按钮（旧的 `openRecShoppingPlan()` 入口）。
- 移除原页面中"早餐、午餐、晚餐食谱计划"整块展示（`plannedMeals` / `rec-day-group` 段）。
- 不再使用 `recSelectedDate` 相关变量渲染该页面（保留变量定义，以免其他地方用到）。

### FR-3 新增采购参数区
参数区为一行/两行 flex 卡片，包含：

- **采购周期下拉**：选项为 `2天 / 3天 / 7天 / 自定义`，默认 `2天`。
- **自定义天数输入**：仅在选择"自定义"时显示一个数字输入框；取值 1 到 N（N = 已确认食谱计划的天数，见 FR-6）。
- **采购天数校验**：当自定义值 > 已确认天数，截断到已确认天数并 toast 提示。
- **用餐人数（只读）**：从 `getDiaryDiningGroup()` 就餐组自动读取成员数 `members.length`；若就餐组不可用，回退到 `diningGroups.find(g=>g.name==='小家')?.members.length || 1`。右侧显示 `人数：X人`。

### FR-4 已确认食谱天数量化
- 定义"已确认 X 天计划"：从今天（`new Date()`）起遍历，连续或合计最多 30 天内，查找 `dietCalendar[dateKey][meal].plan` 中 `(state==='confirmed' || confirmed===true)` 且含 `dishes.length>0` 的「天数」。
  - 统计规则：某**天**内早/午/晚任一餐有 confirmed 计划，即算该"天"为已确认的一天（按日期计，不是按餐次数）。
- 页面显示文案：`基于食谱：已确认 X 天计划`，右侧并列显示 `共需采购：Y 种食材`，Y 为当前计算后"需购量 > 0"的食材种数。

### FR-5 采购食材清单渲染
清单为一个卡片列表，每一条目包含以下列：

| 列 | 内容 | 备注 |
|---|---|---|
| ☑ | 勾选框 | 默认勾选；可取消 |
| 食材名称 | 蔬菜类图标 + 名称 | `guessFoodCategory` 或 meta 里的分类取图标 emoji |
| 总需求量 | `一人份用量 × 用餐人数 × 采购天数` | 显示为"数值 单位"（例：600g、12 个、适量 3份 等） |
| 用量说明 | 简短文字，用于展示计算来源 | 例：`(100g×2人×3天)` 或 `(适量×2人×3天)` |
| 需购量 | max(0, 总需求 - 家中已有可用量) | 为 0 时显示"✅ 已有"（不可勾选、置灰） |
| 预估价格 | `ceil(需购量折算为标准单位) × 默认单价` | 例：¥12.5；默认单价取自 `_recShopDefaultPrices` 或估算表 |

- 清单按 `分类` 分组（蔬菜 → 肉蛋奶 → 主粮 → 水果 → 其他）。
- 清单底部显示「➕ 添加食材」按钮：点击打开添加采购项输入弹窗（复用 `recShopInputOverlay` 或新建简易版，输入"名称/分类/数量/单价"，加入到清单中勾选状态）。

### FR-6 采购计算规则
1. 确定「采购天数 D」：
   - 默认 2；由 FR-3 的下拉 / 自定义值决定；
   - 上限：不超过「已确认食谱计划的天数 X」（FR-4 得出的 X）；若用户选值 > X，则取 X 并 toast。
2. 确定「用餐人数 S」：FR-3 自动取值。
3. 确定「采购日期范围」：从今天起往后 D 天（含今天）。
4. 遍历 D 天内每餐 confirmed 计划菜品：
   - `dietCalendar[dayKey][breakfast/lunch/dinner].plan`，`(state==='confirmed' || confirmed===true)`，`dishes.length>0`；
   - 取菜品 `ingredients`（优先 `dishMeta[菜名].ingredients`，否则 `getDishIngredientsWithServings(菜名, 1人份)` 拿一人份）。
5. 汇总：
   - `一人份用量 q_person`：meta 中 quantity 或默认一人份数量，单位 unit；
   - `总需求 = q_person × S × D`，合并单位一致食材相加；
   - `已有可用量`：`fridgeItems` 中同名（或规范化 `normalizeIngredientName` 后相同）的食材，以 `quantityValue/quantityUnit` 或 quantity 字符串对比单位换算（如无精确数量，则视为"已有=适量"，**保守扣减：若已有 > 0，则按已有可覆盖 1 份/天计**，对于无 quantity 数值的食材，若家里有则需购量置为 0 并显示"✅ 已有"）。
6. 最终需购量 = max(0, 总需求 - 已有用量折算后数值)。
7. 无精确数量/单位的食材，保守策略：家中存在同名食材 → 需购量 = 0（已有）；否则需购量 = 1。

### FR-7 确认采购流程
1. 底部固定按钮：「✅ 确认采购」，右侧显示合计 `共 X 项 · ¥YY.Y`。
2. 点击后弹出「采购清单确认」模态框：
   - 顶部标题：「📋 采购清单确认」；
   - 列表列出所有用户勾选的条目（需购量>0 且 勾选=true）：名称 + 数量 + 单价 + 小计；
   - 每行右侧有 × 删除按钮，可删除不需要的采购项；
   - 底部显示「合计：X 项 · ¥YY.Y」；
   - 按钮：
     - 「取消」关闭弹窗；
     - 「最终确认」执行 FR-8。

### FR-8 最终确认 → 生成采购记录 → 更新库存
1. 从弹窗中仍存活的条目生成 `buyItems[]`：
   ```
   { name, category, count（需购量数值或1）, unit（单位）, unitPrice, subtotal }
   ```
2. 写入 `purchaseHistory[]`：
   ```
   { id, date, timestamp, days, servings, dateRange:{from,to},
     items:[{name,category,count,unit,unitPrice,subtotal}], totalAmount, itemCount }
   ```
3. 更新 `fridgeItems` 库存：
   - 对每个 buyItem，若 `fridgeItems` 中已存在同名 + 同单位 的条目：累加 `quantityValue`，更新 `quantity = quantityValue + unit`，更新 `purchaseDate=今天`；
   - 否则新增条目（category、emoji、storage 用默认值，`purchaseDate=今天`，`expiryDate=今天+7天`，quantity = count+unit，quantityValue=count, quantityUnit=unit）；
4. `lsSet('fridgeItems')` 与 `lsSet('purchaseHistory')` 保存；
5. 刷新页面显示 + toast：`✅ 已确认采购，X 项已入库 · ¥YY.Y`；
6. 关闭确认弹窗。

### FR-9 采购记录查看
1. 页面底部（确认采购按钮上方）或参数区下方，添加按钮：「📊 查看采购记录」。
2. 点击弹出模态框：
   - 标题：「📊 当月采购记录」；
   - 显示当月（与今天同月）所有 `purchaseHistory`，按 `timestamp` 倒序；
   - 每条记录卡片：日期 `MM月DD日 HH:mm` + 人数/天数信息 + `X 项 · ¥YY.Y`；
   - 展开详情时列出采购条目（名称×数量+小计）；
3. 无记录时提示：「本月暂无采购记录，确认采购后将自动登记」。

## 6. 约束 / 依赖 / 假设

- **约束**：
  - 所有采购天数必须 ≤ 已确认计划的天数（FR-4 / FR-6.1）；
  - 仅使用 `frontend/index.html` 文件中的 CSS + HTML + JS；
  - 不破坏 `fridgeRecShopView` 及 `generateShoppingPlan()` 已有功能（它们从食材管理视图入口）。
- **依赖**：
  - `getDietCalendar()` / `setDietCalendar()`；
  - `getDishIngredients()` / `getDishIngredientsWithServings(dish, 1)`；
  - `getDiaryDiningGroup()` 或 fallback 的 `diningGroups`；
  - `fridgeItems` 数据结构与解析函数；
  - `normalizeIngredientName()`、`parseQuantity()`、`guessFoodEmoji()`、`guessFoodCategory()`；
  - `showToast()`；
  - 已有的模态框展示模式 `fridge-modal` 与 overlay `fridge-confirm-overlay` 的样式家族。
- **假设**：
  - 若菜名无 meta 也无 `DISH_INGREDIENTS` 映射，则按 `getDishIngredients()` 推断食材，一人份按 100g/单位计；
  - 默认单价表使用 `window._recShopDefaultPrices`（已存在的全局），缺失时按 10 元/单位估算；
  - 自定义采购天数用户输入整数即可，不做非整数天数支持。

## 7. 开放问题（待确认后可调整，Acceptance 先按 FR 执行）

1. **自定义天数的截断提示方式**：当前设 toast；是否改为输入框 max 属性直接限制？**默认：toast + max 属性双保险**。
2. **"添加食材"按钮实现方式**：复用 `recShopInputOverlay`（采购计划·输入弹窗）或新建"添加采购项弹窗"？**默认：复用 recShopInputOverlay，名称和单位输入可加入当前清单。**
3. **FR-5 "家中已有可用量"扣减规则**：对于 `quantity` 是"适量"、"半颗"等无法量化文本，是否保守认为库存够（需购量 0）？**默认：是（保守策略）。**

## 8. 验收标准 (AC)

> 类型：`rule` = 可二值验证；`rubric` = 评价指标（0-2，≥1 通过）。

### AC-1 标题与文案
- **rule**：进入食材 Tab 默认视图，`topTitle.textContent === '食材准备'`，且 `titles.fridge === '食材准备'`；页面显示"现有食材（部分）："字样，不再出现"现有食材提示（部分）："字符串。
- **rule**：切换到食材 Tab 时，`topTitle.textContent` 首次渲染即"食材准备"（非"食材推荐"闪现）。

### AC-2 现有食材紧凑标签
- **rule**：现有食材（部分）区显示「蔬菜 / 肉蛋奶 / 方便食品」三类内容，每类后以分号/顿号分隔，每项格式为 `[食材名]×[数量]`（若数量缺失则仅显示名称）。
- **rule**：点击任一个食材标签后，`currentFridgeViewMode === 'category'`（即页面切换到"家中食材"分类视图）。

### AC-3 旧元素移除
- **rule**：渲染后的 `recContent` DOM 中不存在 `shiftRecSelectedDate`、`resetRecSelectedDate`、`openRecShoppingPlan`、`采购计划`、`◀`、`▶` 字符（日期选择器 + 旧采购按钮）。
- **rule**：渲染后的 DOM 中不存在早餐/午餐/晚餐餐次标签 `.rec-meal-label` 与 菜品卡片 `#dishCard_*`。

### AC-4 采购参数区
- **rule**：页面存在「采购周期」下拉，四个选项"2天、3天、7天、自定义"，默认选中"2天"。
- **rule**：选择"自定义"时出现数字输入框；选择其他项时输入框隐藏。
- **rule**：页面显示「人数：X人」，X 等于当前就餐组 `members.length`（fallback 小家/1），字段为只读或禁用样式。
- **rule**：当自定义天数 > 已确认计划天数时，toast 提示「自定义天数不能超过已确认的 X 天，已自动调整为 X」，并把值截断为 X。

### AC-5 食谱确认天数与采购种数
- **rule**：页面显示「基于食谱：已确认 X 天计划」，X 是从今天起 30 天内有 confirmed 计划的**日期去重数**。
- **rule**：右侧显示「共需采购：Y 种食材」，Y 等于当前清单中需购量 > 0 的食材种数，参数变化后重新渲染时 Y 会变化。

### AC-6 采购清单渲染与计算
- **rule**：清单每行至少包含"勾选框 + 食材名称 + 总需求量 + 用量说明 + 需购量 + 预估价格"6 个可视元素，顺序匹配。
- **rule**：当需购量 = 0 时，该行显示「✅ 已有」，对应勾选框为禁用/不显示，整行弱化样式。
- **rule**：清单按分类分组（至少蔬菜、肉蛋奶分开）。
- **rule**：采购计算：对于 confirmed 菜"番茄炒蛋"（一人份番茄100g），人数=2，天数=3，总需求番茄=600g；若家中无番茄，需购量应显示 600g。
- **rule**：同上例，家中已有 500g 番茄，则需购量=100g；家中已有 800g，则需购量=0 并显示 ✅ 已有。
- **rule**：参数区「采购天数」或「用餐人数」变更时，清单重新计算且总需求、需购量、预估价格同步更新。

### AC-7 添加食材
- **rule**：底部「➕ 添加食材」按钮可点击并弹出输入框（名称必填，数量/单位可选），确认后新条目追加到清单且默认勾选。

### AC-8 确认采购弹窗
- **rule**：点击「✅ 确认采购」→ 弹出模态框标题「📋 采购清单确认」，内容为所有勾选过的条目（非已有），每条显示名称、数量、单价、小计；右侧有 × 删除按钮，点击后条目移除，合计项数和金额实时更新。
- **rule**：点「取消」关闭弹窗，不产生任何写入。

### AC-9 最终确认写入
- **rule**：点「最终确认」后：
  1. `purchaseHistory` 新增一条记录（时间戳 = 今天，含 items、days、servings、dateRange、totalAmount、itemCount）；
  2. `fridgeItems` 中新增或累加对应食材（新食材按缺省结构入库：emoji、category、storage=冷藏、purchaseDate=今天、expiryDate=今天+7天）；
  3. toast 显示成功信息「✅ 已确认采购...」；
  4. 清单中的条目"需购量"会被重新计算（因为库存刚增加，刚入库的条目应变为 ✅ 已有）。

### AC-10 采购记录查看
- **rule**：点击「📊 查看采购记录」弹窗打开，标题「📊 当月采购记录」，按时间倒序显示当月 `purchaseHistory`。
- **rule**：无记录时显示"本月暂无采购记录"提示。
- **rubric（0-2, ≥1通过）**：采购记录卡片信息完整性（日期时间、项数、总金额齐全；展开可看到条目明细）。

### AC-11 质量
- **rule**：`GetDiagnostics` 返回空数组（index.html 零语法错误）。
- **rule**：浏览器页面打开后 `renderFridgeRecView()` 首次渲染 console 无 ERROR 级输出。
- **rubric（0-2, ≥1通过）**：布局合理性（移动端 390px 宽度不溢出、参数区/清单区/按钮区分层清晰、文字对齐）。
