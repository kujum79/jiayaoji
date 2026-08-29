# 任务拆解 (tasks.md)

Spec: `d:\jiayaoji\.trae\specs\shicai-prep-redesign\spec.md`
目标文件：`d:\jiayaoji\frontend\index.html`

依赖顺序：T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8

---

## Task 1: 标题与全局常量 + Tab 字典改写

**Priority**: high  
**Status**: pending  
**覆盖 AC**: AC-1  
**修改范围**:
1. `titles` 字典（~line 29138）：`fridge:'食材推荐'` → `'食材准备'`。
2. `switchFridgeView('rec')` 中（~line 5436）`topTitle.textContent = '食材推荐'` → `'食材准备'`。
3. `closeRecShopPlan()` 中（~line 7178）`topTitle.textContent = '食材推荐'` → `'食材准备'`。
4. 定义新的全局状态：
   ```js
   window._prep = {
       cycleDays: 2,          // 当前采购天数
       cycleMode: '2d',       // 2d / 3d / 7d / custom
       customDays: 2,
       servings: 1,           // 用餐人数
       selectedItems: {},     // { name: true } 勾选
       extraItems: []         // 用户手动加的食材条目
   };
   ```

**测试要求 (TR)**:
- **TR1.1 (rule)**：搜索 `titles.fridge` 取值 = `"食材准备"`；`switchFridgeView('rec')` 分支和 `closeRecShopPlan()` 函数内均写入 `"食材准备"`。
- **TR1.2 (rule)**：刷新切到食材Tab后 `document.getElementById('topTitle').textContent === '食材准备'`。

---

## Task 2: 采购参数 & 汇总计算函数（纯 JS，不触 HTML）

**Priority**: high  
**Status**: pending  
**覆盖 AC**: AC-4, AC-5, AC-6（计算部分）  
**新增函数**:
1. **`countConfirmedPlanDays(today)`** → 返回 `{ days:X, daysList:[dateKey,...] }`
   - 遍历今天起 30 天内；某一天早/午/晚任一 confirmed 则 daysList 加入该日期；
   - `days = daysList.length`。
2. **`getCurrentServings()`** → 返回人数（FR-3）。
3. **`computeIngredientNeeds(daysList, servings, daysCount)`** → 返回 `{ confirmedDays:X, ingredientsMap, totalIngredientKinds }`
   - `ingredientsMap[name] = { name, category, onePersonQty, onePersonUnit, totalQty, totalUnit, note, houseAvailableQty, houseAvailableUnit, needBuyQty, needBuyUnit, estPrice, alreadyHave }`。
   - 合并同名字，扣减 fridgeItems 可用量（按 parseQuantity 和规范化名称比较，无法量化则保守置 needBuyQty=0）。
4. **`computeNeedBuyQty(ingredientRow, itemFromFridge)`** → 子函数，完成扣减与单位回退。
5. **`estimatePrice(name, qty, unit)`** → 按 `_recShopDefaultPrices` 查表，否则默认 10 元/每单位估算。

**TR**:
- **TR2.1 (rule)**：`countConfirmedPlanDays(today)` 对今天 + 明天两餐 confirmed 场景返回 `days:2`。
- **TR2.2 (rule)**：`getCurrentServings()` 当小家 group 有 2 人时返回 2。
- **TR2.3 (rule)**：`computeIngredientNeeds` 输入已知 confirmedDishes=「番茄炒蛋（一人份番茄 100g）」servings=2,days=3 → ingredientsMap['番茄'].totalQty=600g，无库存时 needBuyQty=600g。
- **TR2.4 (rule)**：同上 + fridgeItems=[{name:'番茄', quantity:'500g', quantityValue:500, quantityUnit:'g'}] → needBuyQty=100g。
- **TR2.5 (rule)**：同上 + fridgeItems=[{name:'番茄', quantity:'适量'}] → alreadyHave=true, needBuyQty=0。

---

## Task 3: 采购参数区 HTML/CSS + 事件绑定（集成到 renderFridgeRecView）

**Priority**: high  
**Status**: pending  
**覆盖 AC**: AC-3（移除）、AC-4  
**范围**: 重写 `renderFridgeRecView()`（~line 7012-7154）。

**改造方案**:
1. 保留行 7018 `fridgeItems = JSON.parse(lsGet(...))`、7019 `fridgeNames = buildFridgeNamesDict(...)`。
2. 原有第 7024-7149 行全部替换为新结构：
   - 顶部：`现有食材（部分）：` + 紧凑标签列表（T4 渲染函数一起完成也可，若拆分先留 placeholder）。
   - **采购参数卡片**：
     - 第一行：`采购周期` 下拉（`<select id="prepCycleSel">` 含四个 option）；条件显示 `<input id="prepCustomDays" type="number">`。
     - 第一行右侧：`人数：X人`（`<span id="prepServings">`）。
   - **食谱汇总行**：
     - 左：`📋 基于食谱：已确认 <span id="prepConfirmedDays">X</span> 天计划`
     - 右：`🛒 共需采购 <span id="prepBuyCount">Y</span> 种食材`
   - **采购清单表格卡片**（T5）。
   - **按钮区**：➕ 添加食材 + 📊 查看采购记录 + 底部 ✅ 确认采购（带合计）。
3. 在 `renderFridgeRecView()` 末尾绑定事件：
   ```
   document.getElementById('prepCycleSel').onchange = onPrepCycleChange;
   document.getElementById('prepCustomDays').oninput = onPrepCustomInput;
   ```
4. 新增事件函数：
   - `onPrepCycleChange(e)`：切换 cycleMode / cycleDays；自定义显示控制；调用 `clampCustomDaysThenRender()`；
   - `clampCustomDaysThenRender()`：若 `prep.cycleDays > confirmedDays` → toast 截断；最后 `renderFridgeRecView()`。

**TR**:
- **TR3.1 (rule)**：render 后 DOM 存在 id=prepCycleSel / prepCustomDays / prepServings / prepConfirmedDays / prepBuyCount 元素。
- **TR3.2 (rule)**：prepCycleSel 默认 value=2d 时 prepCustomDays 隐藏；选 custom 时显示。
- **TR3.3 (rule)**：render 后原「◀/▶」与「采购计划」按钮 DOM 不存在（innerHTML 不包含对应字符串或 onclick）。
- **TR3.4 (rule)**：设定 confirmedDays=2，用户自定义输入 5 → 自动变为 2 且 toast 提示。

---

## Task 4: 现有食材（部分）紧凑标签渲染 + 跳食材页

**Priority**: medium  
**Status**: pending  
**覆盖 AC**: AC-2  
**添加渲染片段**：在 renderFridgeRecView() 中紧接 `现有食材（部分）：` 后：

```
const prepOverview = ['蔬菜','肉蛋奶','方便食品'];
prepOverview.forEach(cat => {
  const list = fridgeItems.filter(i=>i.category===cat);
  const parts = list.slice(0,4).map(i => {
     const q = (i.quantity||'').trim();
     return `<span class="prep-tag" onclick="switchFridgeView('category')">${escapeHtml(i.name)}${q?'×'+escapeHtml(q):''}</span>`;
  });
  html += `<span><b>${cat}</b>  ${parts.join('; ') || '<span class="prep-empty">—</span>'}</span>`;
});
```
新增 CSS：`.prep-tag` 行内紧凑样式（间距、圆角、hover 色）。

**TR**:
- **TR4.1 (rule)**：fridgeItems 含蔬菜「番茄 (quantity:3)」和「菠菜 (quantity:100g)」→ 标签为「番茄×3; 菠菜×100g」。
- **TR4.2 (rule)**：点击任意 .prep-tag → currentFridgeViewMode === 'category'。
- **TR4.3 (rule)**：fridgeItems 蔬菜为空 → 显示「—」。

---

## Task 5: 采购清单渲染 + 勾选状态 + 底部「➕ 添加食材」

**Priority**: high  
**Status**: pending  
**覆盖 AC**: AC-5, AC-6, AC-7  
**范围**: renderFridgeRecView() 中清单表格 + 新增函数 `addCustomPrepItem()` 与事件处理。

**UI 表结构**：
```html
<div class="prep-shop-card">
  <table class="prep-shop-table">
    <thead>
      <tr><th style="width:24px">☑</th><th>食材</th><th>总需求</th><th>用量说明</th><th>需购量</th><th style="width:56px;text-align:right">估价</th></tr>
    </thead>
    <tbody id="prepShopBody"><!-- 分类分组表头 + 行 --></tbody>
  </table>
  <button class="prep-add-item-btn" onclick="openAddCustomPrepItem()">➕ 添加食材</button>
</div>
```
- 分类表头行：显示分类名；
- 食材行：
  - `☑` checkbox 默认 checked；若 alreadyHave=true 则 checkbox disabled + 行色弱化；
  - 名称 = `emoji + name`；
  - 总需求 = `${totalQty || '-'} ${totalUnit || ''}`；
  - 用量说明 = `(${onePersonQty||'适量'}×S人×D天)`；
  - 需购量：alreadyHave 时显示「✅ 已有」，否则 `${needBuyQty}${needBuyUnit}`；
  - 估价：`¥${estimatePrice(...).toFixed(1)}`。
- ✅ 已有行：checkbox disabled + 灰色背景。

**「➕ 添加食材」弹窗**：
- 复用 `recShopInputOverlay`（标题改为「➕ 添加采购食材」，id=recShopInputName）或简化版新弹窗：只要名称 + 数量 + 单位。
- 确认后将新对象 push 到 `window._prep.extraItems`，然后 `renderFridgeRecView()` 重新渲染。
- 新条目被当成一条 confirmed 食材：`onePersonQty = qty, totalQty = qty*S*D`（或用户输入的数量直接当总需求），并合并入 ingredientsMap。

**TR**:
- **TR5.1 (rule)**：清单表头 6 列（☑ / 食材 / 总需求 / 用量说明 / 需购量 / 估价）依次存在。
- **TR5.2 (rule)**：needBuyQty=0 的行显示「✅ 已有」；checkbox disabled=true。
- **TR5.3 (rule)**：点「➕ 添加食材」弹窗后输入「黄瓜 / 500g」→ 清单多出「黄瓜」条目且默认勾选。
- **TR5.4 (rule)**：prepConfirmedDays 与 prepBuyCount 在参数变化、添加条目后 innerHTML 同步更新（数字变化）。

---

## Task 6: 确认采购弹窗 + 最终确认写入

**Priority**: high  
**Status**: pending  
**覆盖 AC**: AC-8, AC-9  
**新增 HTML 模态框节点**（紧接 `addFridgeModal` 附近或末尾 modal 区）：
```html
<div class="fridge-modal" id="prepConfirmModal">
  <div class="fridge-modal-content">
    <div class="fi-modal-header">
      <button class="fi-back-btn" onclick="closePrepConfirmModal()">&lt;</button>
      <span class="fi-title">📋 采购清单确认</span>
      <span style="width:48px"></span>
    </div>
    <div id="prepConfirmList" style="margin-bottom:12px;"></div>
    <div id="prepConfirmTotal" style="font-weight:700;text-align:right;margin-bottom:12px;"></div>
    <div class="fridge-form-actions">
      <button class="fridge-btn-secondary" onclick="closePrepConfirmModal()">取消</button>
      <button class="fridge-btn-primary" onclick="finalizePrepPurchase()">最终确认</button>
    </div>
  </div>
</div>
```
**新增/修改函数**:
1. `openPrepConfirmModal()`：
   - 收集所有勾选的食材（ingredientsMap 中 checked=!0 且 !alreadyHave，加上 extraItems 中勾选）；
   - 构造 `_prep.pendingBuyItems = [...]`；
   - 渲染列表到 `prepConfirmList`（每行名称 + 数量 + 单价 + 小计 + 右侧 × 删除）；
   - 合计更新到 `prepConfirmTotal`。
2. `deletePrepConfirmItem(idx)`：删除 `_prep.pendingBuyItems[idx]`，重渲染列表。
3. `closePrepConfirmModal()`：移除 class show。
4. `finalizePrepPurchase()`：
   - 按 FR-8：构造 buyItems → 写 purchaseHistory → 更新 fridgeItems（累加或新增）→ saveFridgeItems → renderFridgeRecView → toast 成功 → 关闭弹窗。
5. 页面底部按钮「✅ 确认采购」onclick = `openPrepConfirmModal()`。

**TR**:
- **TR6.1 (rule)**：点确认采购 → prepConfirmModal 显示，列表条目数 = 勾选且需购量>0 项数。
- **TR6.2 (rule)**：点某条 × 按钮后，条目数减 1，合计金额减少。
- **TR6.3 (rule)**：点最终确认后，lsGet('purchaseHistory') 新增一条记录；fridgeItems 中存在对应新增食材；toast 文本含「已确认采购」。
- **TR6.4 (rule)**：最终确认后再渲染清单，新入库的食材需购量字段显示为「✅ 已有」。

---

## Task 7: 采购记录查看模态框

**Priority**: medium  
**Status**: pending  
**覆盖 AC**: AC-10  
**新增节点**：
```html
<div class="fridge-modal" id="prepHistoryModal">
  <div class="fridge-modal-content">
    <div class="fi-modal-header">
      <button class="fi-back-btn" onclick="closePrepHistoryModal()">&lt;</button>
      <span class="fi-title">📊 当月采购记录</span>
      <span style="width:48px"></span>
    </div>
    <div id="prepHistoryBody"></div>
  </div>
</div>
```
**新增函数**:
1. `openPrepHistoryModal()`：取当月 `purchaseHistory`（今天同月份），按 timestamp 倒序，渲染卡片到 prepHistoryBody；无记录时显示提示。
2. `closePrepHistoryModal()`：hide。
3. 每张采购记录卡片格式：
   - 标题：`MM月DD日 HH:mm · X人 X天采购 · X项 · ¥YY.Y`
   - 条目列表（点击展开/默认展示）：每个 `name×count+unit · 小计¥X`。

**TR**:
- **TR7.1 (rule)**：lsGet('purchaseHistory') 为空时，弹窗显示"本月暂无采购记录"。
- **TR7.2 (rule)**：构造本月测试记录 1 条 + 上月 1 条 → 仅显示本月 1 条；条目数=1 且时间倒序。

---

## Task 8: 整体回归验证（UI+Console）

**Priority**: medium  
**Status**: pending  
**覆盖 AC**: AC-11  
**TR**:
- **TR8.1 (rule)**：GetDiagnostics(`file:///d:/jiayaoji/frontend/index.html`) 返回空数组。
- **TR8.2 (rule)**：browser 打开 http://127.0.0.1:8000/app → 切到食材 Tab → console 无 ERROR 级输出。
- **TR8.3 (rubric, 0-2, ≥1通过)**：以 390px 屏幕宽度截图，布局不溢出；参数区、清单区、按钮区层次分明；文字行对齐一致。
- **TR8.4 (rule)**：所有新增按钮（确认采购、查看记录、添加食材）都有 onclick 且不会报错。

---

## 依赖总览

| T | 依赖 |
|---|---|
| T1 | 无 |
| T2 | 无 |
| T3 | T1, T2（需要全局状态与计算函数） |
| T4 | T3（在 render 中插入片段） |
| T5 | T2, T3（计算函数 + render 片段 + prep.extraItems） |
| T6 | T2, T5（依赖 pendingBuyItems 收集 + prep.selectedItems） |
| T7 | T6（共用 purchaseHistory 新字段结构升级后展示） |
| T8 | T1-T7 全部 |
