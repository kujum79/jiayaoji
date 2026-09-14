# 「食材准备 / 现购食材」买前查询 + 买后录入双模式 规范

## 1. 问题 / 背景
当前「食材准备」页的 2 号入口「现购食材」弹框 `prepNewBuyModal` 存在两个核心问题：
1) **买前/买后只一个录入视图**：缺少"买前查搭配"轻量模式，用户在超市里决定要不要买、搭什么买更合理时没工具；
2) **录入后 AI 仍会合并家中已有食材**，并且底部有两颗多余按钮（「取消」+「🤖 生成菜谱推荐」，与顶部右侧「🤖 生成菜谱」完全重复）。

本轮升级：把「现购食材」弹框升级为**双 Tab 模式**（买前查询 / 买后录入），并让 AI 推荐菜谱**只基于"新买食材"**（不再合并家中已有）、底部两颗冗余按钮删除，买后录入自动挂 AI 烹饪/搭配建议、菜谱可逐道「📋 加入食记」。

## 2. 用户 / 目标
- **目标用户**：家肴记普通使用者（负责买/录的人）。
- **业务目标**：
  - 在超市/菜市场买东西前：输入食材名，即时拿到「常见搭配建议 + 是否值得买」的辅助决策。
  - 回到家录入后：AI 自动评价搭配合理度 + 生成 3-6 道菜谱 + 逐道加入今日食记。
- **产品目标**：保留单文件、无后端、localStorage 持久化的约束，不破坏 食记→计划→确认 的链路。

## 3. 范围
### 3.1 In Scope
- 「现购食材」弹框 `prepNewBuyModal` 重写为双 Tab（「买前查询」、「买后录入」）。
- 买前查询：单食材搜索框 + AI 搭配建议 + 3.5s 本地规则兜底。
- 买后录入：保留多行录入（名称 / 数量 / 单位 / 单价 / 总价，总价按单价×数量自动同步），+ 删行，清空最后行保留；AI 录入后自动展示搭配与评价。
- 弹框**头部**保留 `< 返回 / 标题 / 🤖 生成菜谱`，**底部两颗冗余按钮**删除：`取消` 与 `🤖 生成菜谱推荐`。
- 生成菜谱：`genRecipesFromIngredients(newBuy, mergeFridge=false)`，**仅基于新买食材**（不再读 `fridgeItems`）；结果复用 `fridgeResultOverlay` + `renderFridgeMeals`，source=`shopping`。
- 加入食记：复用以有的 `addToPlanFromFridge(dish, 'lunch'|'dinner', 'shopping')`，写入 `dietCalendar[todayKey][mealType].plan`，`source=shopping, confirmed=false`。

### 3.2 Out of Scope
- 不改造「现有食材生食谱」和「🥕 食材推荐食谱」两条入口。
- 不改动 `fridgeItems` 入库逻辑（采购记录 / 入库走原采购确认流程，不与本录入联动自动入库）。
- 不做真实比价接口；价格仍手填或由 `estimatePrepPrice` 估算。
- 不做多端同步 / 多人协作。

## 4. 非功能要求
- **仅改动 `d:\jiayaoji\frontend\index.html`**（内联 CSS + 原生 JS，单文件 SPA）。
- 数据全部使用 `lsGet / lsSet`（家庭码前缀隔离）。
- `GetDiagnostics(index.html) === 0`。
- 移动端 430px 宽度：`body.scrollWidth - body.clientWidth === 0`（无横向滚动）。
- 与现有 AI 调用保持一致：`Promise.race([callAliyunQwenApi(sys, usr), 3.5s fallback])`。

## 5. 功能需求 (FR)

### FR-0 弹框底栏清理
- 删除 `prepNewBuyModal` 里底部「取消」「🤖 生成菜谱推荐」两颗按钮 DOM。
- 保留头部 `<` 返回按钮（`closePrepNewBuyModal`）和右上角「🤖 生成菜谱」按钮（`submitNewBuyAndGenRecipe`）。
- 头部文案与按钮不变。
- 弹框副标题（提示语）改成："📋 双模式：买前先查常见搭配，买后录清单→AI自动给搭配建议 + 点右上角🤖生成具体菜谱"。

### FR-1 买前查询 Tab（轻量）
- 在 `prepNewBuyModal` 内部增加 Tab 栏：「🔍 买前查询」「🛒 买后录入」，默认选中「买前查询」。
- 查询 Tab 结构：
  1. 搜索框（食材名称，placeholder=牛肉 / 番茄），右侧按钮「🤖 查询搭配」；也支持回车触发。
  2. 查询结果区 `#prepBuyQueryResult`：
     - 首行「X 的常见搭配（最多 6 种，顿号分隔）：土豆、番茄、洋葱…」
     - 次行「是否值得买：| 推荐理由（一句，20-30 字）」
     - 2-3 条 💡 烹饪/保存小贴士（复用 `_aiEvalRuleNewBuy` 规则生成或 AI 版覆盖）。
  3. 结果区下方提供两个快捷按钮：
     - 「➕ 加入买后录入清单」：把当前输入的食材名作为新的录入行加入买后 Tab（名=输入，其它空）；自动切到买后录入 Tab 并 toast。
     - 「清空」：清空搜索框与结果。
- 触发逻辑：
  - AI prompt sys = "你是家肴记买菜小助手。严格输出 JSON：{pairs:[\"土豆\",…], worthBuying:\"推荐/可选/不推荐\", reason:\"20-30 字\", tips:[\"s1\",\"s2\"]}，pairs 3-6 项；reason 简明；tips 2 条烹饪或保存。字段严格一致。"
  - usr = "用户考虑是否买【X】。请问 X 常见搭配（中国家常）、是否值得买 + 简短理由 + 2 条烹饪/保存小贴士"
  - 兜底：`_pairingsRule(ing)`：牛肉→番茄/土豆/洋葱；猪肉→青椒/蒜苗/土豆；鱼→豆腐/姜葱/萝卜；虾→西兰花/芦笋/韭菜；鸡蛋→番茄/韭菜/黄瓜；番茄→鸡蛋/牛肉/土豆；etc.，worthBuying 一律=推荐。

### FR-2 买后录入 Tab（核心）
- 复用原多行录入容器 `#prepNewBuyList`：名称 * / 单价(元) / 数量 / 单位 / 费用(元) + 删行按钮，最后行清空不删除。
- 弹框打开时默认一条空行 + 恢复 `tempPrepNewBuy`（与现逻辑一致）。
- 「+ 再加一条食材」按钮保留，位置在录入区底部。
- **AI 评价触发时机（两种）**：
  a) 任一行「名称」blur 或 「费用」input 变更 500ms 防抖后，若已有 ≥1 行名称非空，在录入区下方追加 AI 搭配评价卡（`#prepBuyAutoEval`）。
  b) 用户点击右上角「🤖 生成菜谱」提交录入后，在 `fridgeResultOverlay` 的菜谱上方再追加一份 AI 评价卡。
- AI 搭配评价卡结构：
  - 标题「🧑‍🍳 AI 搭配评价 · 新买食材」
  - 评价行：「总体搭配：合理 / 稍单一 / 偏肉多 等」+「一句建议，例如"还差 1 份绿叶蔬菜，加一斤菠菜营养更均衡"」。
  - 2-3 条 💡 小贴士（烹饪→保存→替代）。
  - 兜底：复用 `_aiEvalRuleNewBuy(newBuy, [])` 规则版；AI 返回 null 时仍有规则可见。

### FR-3 生成菜谱（仅基于新买食材）
- `submitNewBuyAndGenRecipe()` 里收集 items 写入 `tempPrepNewBuy` 与 `_prep.newPurchasedItems` 后，`closePrepNewBuyModal()` + 调用 `genRecipesFromIngredients(items, false)`。
- `genRecipesFromIngredients(newBuy, mergeFridge)` 适配：
  - 当 `mergeFridge=false` 时，**忽略** `lsGet('fridgeItems')`（`fridgeList=[];existingNames=[]`）。
  - prompt 里文案明确："本次只基于新买食材推荐菜谱，不使用家中已有库存。优先把新买食材用完。"
  - 结果标题：`📋 推荐菜谱（基于新买食材 · ${meals.length}道）`。
  - fallbackDishes：只用 `newBuyNames` 集匹配 `DISH_INGREDIENTS`，不再把 `existingNames` 并进去；不够时再用 generic 补齐。
- 菜谱卡片「📋 加入食记」默认落到今天午餐。若同名菜已在今日同一餐，toast 提示并跳食记页（复用既有 `addToPlanFromFridge` 行为）。

### FR-4 加入食记（逐道选择菜谱）
- 用户在 `fridgeResultOverlay` 里任意菜谱卡上点「📋 加入食记」即执行 `addToPlanFromFridge(meal.dish, 'lunch', 'shopping')`。
- 若用户想加晚餐/明天，手动跳食记页改。
- 不新增"多选一键加入食记"批量能力；本轮保持现有的"单道加入 → 到食记页批量勾选确认"链路不动。

### FR-5 与「🥕 食材推荐食谱」入口关系说明
- 食材推荐食谱（今/明/后 × 三餐 逐道勾选）走 `prepIngrRecOverlay` / `generateIngredientPrefRecipes`，本轮不改。
- 两个入口的 source、写入食记字段必须仍然区分：`shopping`（本弹框）与 `preferred`（食材推荐食谱），防止 2 buckets 混桶。

## 6. 约束 / 依赖 / 假设
- **约束**：只改单文件 `frontend/index.html`；写全部用 `lsSet` 读 `lsGet`；不增后端路由。
- **依赖**：
  - `callAliyunQwenApi(sysPrompt, usrPrompt) → Promise<Object>`；
  - `DISH_INGREDIENTS`、`estimatePrepPrice(name, qty, unit)`；
  - `renderFridgeMeals`、`addToPlanFromFridge`、`updateMealPlan`、`getMealData`、`_aiEvalRuleNewBuy`、`_aiEvalAsyncNewBuy`；
  - `showToast`、`escapeHtml`、`escapeAttr`、`fridgeResultOverlay` DOM。
- **假设**：
  - AI JSON 返回可能失败或超时，任何 AI 结果都必须有 3.5s 本地 fallback 保证 UI 不空等。
  - 买前查询"加入买后录入"时若食材重名，允许重复行（用户可能买两批不同规格）。
  - 加入食记默认今天午餐；如需改餐次由用户在食记页手动调整。

## 7. 开放问题（当前默认方案，可用户调整）
1. **加入食记默认餐次**：本规范默认今天午餐。是否改成"弹一下餐次选择小浮层再决定"？默认：不新增，今日午餐。
2. **录入自动入库**：是否在"点🤖生成菜谱"或"加菜入食记"时自动入冰箱库存？默认：不自动入（库存走采购确认流程）。

## 8. 验收标准 (AC)
> 类型：`rule` 二值通过；`rubric` 0-2 分，≥1 通过。

### AC-0 冗余按钮清理
- **rule**：打开 `prepNewBuyModal` 后，DOM 中 `#prepNewBuyModal .fridge-form-actions` 不存在（或内部没有 `取消` / `生成菜谱推荐` 文本按钮）。
- **rule**：右上角「🤖 生成菜谱」仍在，onclick === submitNewBuyAndGenRecipe。

### AC-1 双 Tab 存在
- **rule**：`prepNewBuyModal` 弹框内可见 Tab 栏两个文本：「买前查询」「买后录入」，默认买前查询高亮。
- **rule**：点击两个 Tab 能切换各自内容区显示/隐藏，互不覆盖。

### AC-2 买前查询功能
- **rule**：输入食材「牛肉」→ 回车或点「查询搭配」→ 3s 内结果区出现 "牛肉的常见搭配：…"（至少 3 项顿号分隔）+ "是否值得买：推荐/可选/不推荐" 字样 + 2 条小贴士。
- **rule**：离线（网络断开）时仍有规则兜底结果，不会空窗或抛错。
- **rule**：点「➕ 加入买后录入清单」 → Tab 切到买后录入、并新增 1 行名称="牛肉"的录入行。

### AC-3 买后录入与 AI 自动评价
- **rule**：录入 1 行名称='番茄'、数量=2、单位=斤、单价=4 → 费用自动=8.00；手改费用不会被覆盖。
- **rule**：录完 blur 名称后 1s 内录入区下方出现 `#prepBuyAutoEval`，标题含"AI 搭配评价"字样。
- **rule**：录完点击右上角「🤖 生成菜谱」→ `fridgeResultOverlay` 打开，标题包含「基于新买食材」字样。

### AC-4 生成菜谱**只**用新买食材
- **rule**：`fridgeItems`（家中冰箱）有『鸡蛋』，但新买食材只录入『番茄 2 斤』，生成 3~6 道菜里：
  ① fallback 命中必须只和番茄交集优先；
  ② prompt 传给 AI 的 usr 里一定不包含 "家中已有食材" 列表（或显式写家中已有=(空)）。可通过 `console` hook 或直接 `spy callAliyunQwenApi` 验证。
- **rule**：菜谱卡片里每道菜 have = 新买食材交集，need = 其它食材；✅ / 🛒 图标颜色区分正确。

### AC-5 加入食记正常
- **rule**：从菜谱卡点「📋 加入食记」→ `lsGet('dietCalendar')[todayKey]['lunch']['plan'].dishes` 里新增该菜名，且 `dishMeta[name].source === 'shopping'` 且 `confirmed === false`。
- **rule**：对同一菜再点一次时，toast 文案提示"已在今日午餐计划中"，不重复写。

### AC-6 质量
- **rule**：`GetDiagnostics(index.html) === []`。
- **rule**：打开 tab=fridge → 切「现购食材」 → 查牛肉 → 录番茄×2斤×4 → 生成菜谱 → 加菜入食记，全程无 console ERROR（WARN/INFO 允许）。
- **rubric（0-2, ≥1通过）**：移动端 420px 布局合理性（Tab 不换行、输入框不横向溢出、录入区每行 6 列在 420px 下可读性可接受）。
