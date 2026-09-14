# xiangou-function-enhance 实现任务清单

## Task 1: 清理现购弹框底部「取消 / 生成菜谱推荐」冗余按钮 + 改副标题 + 保留头部🤖生成
- **Priority**: high
- **Depends**: None
- **Files touched**: `d:\jiayaoji\frontend\index.html`
- **Scope**: DOM L3687-L3705 的 `prepNewBuyModal`
  - 删除底部 `.fridge-form-actions` 容器（取消 + 🤖 生成菜谱推荐两按钮）。
  - 改副标题行 L3695：旧「结合家中已有 + 新买食材」→ 新「📋 双模式：买前先查常见搭配；买后录清单→AI自动给搭配建议；点右上角🤖生成具体菜谱（**仅基于新买食材**）」。
- **Test Requirements**:
  - **rule** 打开 `prepNewBuyModal` 后 DOM 查询 `.fridge-form-actions button => length=0` 或 `textContent` 不包含 `取消|生成菜谱推荐`。
  - **rule** 顶部标题后副标题含 `双模式`、`仅基于新买食材` 字样。
- **Status**: pending
- **Completion Evidence**: _(待填写)_

---

## Task 2: 现购弹框增加双 Tab 外壳（买前查询 / 买后录入）+ 默认 Tab=买前
- **Priority**: high
- **Depends**: Task 1
- **Scope**: DOM `prepNewBuyModal` 内 `fi-modal-header` 下方，副标题之上插入 Tab 栏；将现有 `prep-new-buy-list` 容器 + `prep-nb-addmore` 按钮整体包进「买后录入 Tab-content」容器。
  - 新插入结构（类名：`prep-tabs` / `prep-tab-item` / `.is-active`；配套 CSS 12 条样式，放在 `<style>` 末尾或紧邻 `prep-nb-*` CSS 家族）：
    - Tab 栏：`🔍 买前查询` / `🛒 买后录入`，onclick=`switchPrepBuyTab('query'|'entry')`；各自对应容器 id=`prepBuyQueryTab` / `prepBuyEntryTab`。
    - 「买前查询」容器：搜索行 `#prepBuyQueryInput` + 按钮 `#prepBuyQueryGo` + 「清空」；结果 `#prepBuyQueryResult`；下方操作按钮 `#prepBuyQueryAdd`「➕ 加入买后录入」/「清空结果」。
    - 「买后录入」容器：保留旧 `prepNewBuyList` + `prep-nb-addmore` + 新插入 AI 自动评价区 `#prepBuyAutoEval`。
  - JS 新增 `switchPrepBuyTab(tab)` 切换：切换 `.is-active`、显示/隐藏两容器；`openPrepNewBuyModal()` 尾部默认切 `tab='query'`。
- **Test Requirements**:
  - **rule** 弹框打开后默认 Tab 激活项文字含「买前查询」；点击 Tab 两栏切换 DOM display 正确。
  - **rule** 买后录入 Tab 下仍存在 `#prepNewBuyList`（渲染 1 空行）与 「➕ 再加一条食材」按钮。
- **Status**: pending
- **Completion Evidence**: _(待填写)_

---

## Task 3: 买前查询 - AI 查询搭配 + 3.5s 本地规则兜底
- **Priority**: high
- **Depends**: Task 2
- **Scope**: 新增 `runPrepBuyQuery(ing)` + `_pairingsRule(ing)` 两个 JS 函数：
  1. `runPrepBuyQuery(ing)`：trim 非空校验 → 结果区先写 `AI 查询中…` → `Promise.race([callAliyunQwenApi(sys,usr), 3.5s 定时器→null])` → AI 成功（含 pairs/worthBuying/reason/tips）按 FR-1 渲染；失败走 `_pairingsRule`。
  2. `_pairingsRule(ing)`：按名称 `if/else if` 分 10 大类（牛/猪/鸡/鸭/鱼/虾/豆腐/番茄/鸡蛋/叶菜/土豆/根茎/米饭面…）输出 pairs 3-6 + worthBuying=推荐 + tips 2 条。
  3. 交互：`#prepBuyQueryGo.onclick` / 输入框 enter → `runPrepBuyQuery`；`#prepBuyQueryAdd.onclick`：名字非空 → `addOneNewBuyRow({name})` + `switchPrepBuyTab('entry')` + toast；"清空结果/清空"按钮 → 清除输入和结果。
- **Test Requirements**:
  - **rule** 输入「牛肉」→ 结果 3s 内显示 3+ 搭配项；断网 fallback 下也至少 3 项、worthBuying 非空、tips 2 条。
  - **rule** 点「➕ 加入买后录入」买后 Tab 新增 1 行，名称=牛肉；其他字段为空。
- **Status**: pending
- **Completion Evidence**: _(待填写)_

---

## Task 4: 买后录入 - 多行录入保留 & 新增"名称变更或费用变更"的 AI 自动评价（500ms 防抖 + 规则兜底）
- **Priority**: high
- **Depends**: Task 2
- **Scope**:
  1. `addOneNewBuyRow(seed)`：现有渲染里给「名称」input 和「费用」input 各挂 `debounceCollectNewBuyEval()`。
  2. 新增 `debounceCollectNewBuyEval()`：500ms 防抖，读取所有行；若 ≥1 行名称非空 → 调 `_aiEvalRuleNewBuy(items, [])` 生成规则卡 3 条 → 写入 `#prepBuyAutoEval`。
  3. 异步再并行调 `_aiEvalAsyncNewBuy(items, [])`（9s 超时）替换 tips 内容；头部补"总体搭配"自动一行（按名字分类统计：蔬菜/肉蛋奶/主粮/水产/水果数量，"缺绿叶"、"缺水产"、"缺主食"启发式生成）。
  4. 「🤖 生成菜谱」按钮 submit 时（`submitNewBuyAndGenRecipe`）保存前再做一次评价（写入 `tempPrepNewBuy` 后、`closePrepNewBuyModal` 前），保证 AI 建议已被用户看到。
- **Test Requirements**:
  - **rule** 输入「番茄」后 blur 名称字段 → ≤1.5s 内 `#prepBuyAutoEval` 出现，标题含「AI 搭配评价」。
  - **rule** 录入 4 行（1 蔬菜+1 肉+1 水产+1 主食）时，"总体搭配"合理字（如"荤素搭配均衡"）；只录入 3 条牛肉类时提示"蛋白质过多，建议加一份叶菜"。
- **Status**: pending
- **Completion Evidence**: _(待填写)_

---

## Task 5: `genRecipesFromIngredients(newBuy, mergeFridge=false)` 适配"只基于新买食材"
- **Priority**: high
- **Depends**: Task 1, Task 4
- **Scope**: 原函数 [L8938-L9012] 修改 3 处：
  1. L8957-L8959 `if (mergeFridge)` 读 fridge；else `fridgeList=[];existingNames=[]`。
  2. L8945 标题：`titleEl.textContent = mergeFridge ? '📋 推荐菜谱（基于家中已有 + 新买食材）' : '📋 推荐菜谱（基于新买食材 · '+meals.length+'道）'`；在 meals 确定后再补一次正确标题。
  3. L8989-L8990 sys/usr prompt 改为：mergeFridge=false 时 sys 明确"本次只基于新买食材推荐菜谱，不要使用家中已有库存；优先把新买食材用完"；usr 里写家中已有食材=(空)，仅带新买食材清单文本。
  4. fallbackDishes：`allIngs` 改为 `mergeFridge ? existingNames.concat(newBuyNames) : newBuyNames.slice()`；不再读 existingNames。
- **Test Requirements**:
  - **rule** spy：`genRecipesFromIngredients(items, false)` 调用时，传给 `callAliyunQwenApi` 的 usr 字符串里包含 `家中已有食材：(空)`，且不包含 `家中已有食材：鸡蛋`（即使 `fridgeItems=[鸡蛋]` 在库）。
  - **rule** fallbackDishes 只拿 newBuyNames 命中；家中库存中的菜不会进入 scored。
  - **rule** `titleEl.textContent` 最终包含「基于新买食材」字样，不含「+ 家中已有」。
- **Status**: pending
- **Completion Evidence**: _(待填写)_

---

## Task 6: 生成菜谱结果页的 AI 评价卡 + 加入食记 source=shopping 不变
- **Priority**: medium
- **Depends**: Task 5
- **Scope**:
  1. 现有 `meals = arr` 分支里把 `renderFridgeMeals(meals, 'shopping')` 的 source 保持为 shopping（L9013 不改）。
  2. 在 `newBuy && !mergeFridge` 分支也追加 AI 建议卡：与 L9018-L9045 相同，只是 title 的 scene='new-buy-pure'，标题改成「🧑‍🍳 AI 小贴士 · 新买食材搭配&保存（纯新买版）」。
  3. AC-5 的写入校验：`addToPlanFromFridge` 在 source='shopping' 时 dishMeta.source==='shopping'；如果用户切换页面到食记页，会出现在 2 buckets 里的 preferred/ai 之外的 shopping bucket（验证 2 buckets 渲染正常时存在 🛒 食材推荐食谱组或「🛒 新买食材推荐」bucket，实际以 `uiBucketOfSource` 为准）。
- **Test Requirements**:
  - **rule** 菜谱卡点击「📋 加入食记」1 次后，`lsGet('dietCalendar')[todayKey]['lunch'].plan.dishMeta[dish].source === 'shopping'`。
  - **rule** 连点 2 次不重复写入；toast 提示「已在今日午餐计划中」。
- **Status**: pending
- **Completion Evidence**: _(待填写)_

---

## Task 7: 门禁：语法 / 诊断 / 横向滚动
- **Priority**: high
- **Depends**: Task 1-6 all done
- **Scope**:
  1. `GetDiagnostics(index.html) === []`。
  2. `node` 读全 `<script>` 块：`new Function(allScriptsText)` 不抛 SyntaxError。
  3. 浏览器 420px 视口：`body.scrollWidth - body.clientWidth === 0`（无横向滚动条）。
  4. 全流程（打开食材 Tab → 查牛肉→加入清单→切买后录番茄 2 斤×4→点🤖生成菜谱→卡中加菜入食记）无 console ERROR。
- **Test Requirements**:
  - **rule** GetDiagnostics 返回空数组。
  - **rule** node `new Function(...).call({})` 无 SyntaxError。
  - **rubric 0-2 ≥1** 移动端布局（420px 宽度）合理性。
- **Status**: pending
- **Completion Evidence**: _(待填写)_
