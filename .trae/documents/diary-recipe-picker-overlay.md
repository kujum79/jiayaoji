# 食记页面食谱选择浮层 + 计划列表扁平化改造

## Context
用户希望在「三餐计划、记录」页面增加一个 ☰ 按钮，点击后打开浮层显示当天「现有食材食谱、现购食材食谱、食材推荐食谱」三类推荐，勾选后写入当天对应餐次计划。同时简化食记计划列表：去掉来源分组标题（📋 食材推荐食谱/🤖 AI推荐食谱）、手动添加输入框、三组并排提示文字、每道菜的 × 删除按钮。三类食谱按顺序显示（现有/现购默认勾选，AI 紧随其后）。

## 修改文件
- `d:\jiayaoji\frontend\index.html`（单文件应用，所有 DOM/CSS/JS 都在此）

## 实现步骤

### 1. 食记日期导航栏增加 ☰ 按钮
在 `diary-date-nav`（L3461）中 `diaryPrev` 按钮之前插入：
```html
<button class="diary-nav-btn" onclick="openDiaryRecipePicker()" title="选择食谱">☰</button>
```

### 2. 新增食谱选择浮层 DOM
在 `panelDiary` 结束后（L3490 后）插入新浮层 `diaryRecipePickerOverlay`，复用 `.fridge-result-overlay` 样式。结构：
- 顶部：日期显示 + 早餐/午餐/晚餐三个按钮选择器
- 中部：动态渲染三个来源分区（现有食材食谱、现购食材食谱、食材推荐食谱）
- 底部：取消按钮 + 确认加入计划按钮

### 3. 提取可复用逻辑函数
- `_computeFridgeMealMatches(fridgeItems)` — 从 `generateFridgeMealPlan`（L14643）提取冰箱食材匹配逻辑，返回 `{meals, matched, partial}`
- `_computeShoppingMealMatches(shoppingItems)` — 新增，匹配已购采购计划项
- `_generateAiRecipesForSingleDateMeal(dateKey, mealKey)` — 新增，为单日单餐生成 AI 推荐食谱（3.5s 超时，失败走 `_staticFallbackIngrPrefRecipes` 兜底）

### 4. 浮层控制器函数
- `openDiaryRecipePicker()` — 打开浮层，显示当前日期，默认选早餐
- `closeDiaryRecipePicker()` — 关闭浮层
- `selectDiaryRecipePickerMeal(meal, btn)` — 切换餐次，重新渲染分区
- `renderDiaryRecipePickerSections()` — async 渲染三分区：现有食材/现购食材同步计算，AI 异步生成
- `onDiaryRecipePickerToggle(source, dish, checked)` — 记录勾选状态
- `confirmDiaryRecipePicker()` — 将勾选的菜写入 `currentDiaryDate` 对应餐次计划，现有/现购 `source='existing'/'shopping', confirmed=true`，AI `source='ai', confirmed=false`
- `_writeDiaryRecipePickerToPlan(dateKey, meal, buckets)` — 合并写入 dietCalendar（参考 `writeIngrRecBucketsToDiary` L11533 的合并逻辑）

所有函数用 `function name() {}` 声明，自动挂载到 window，onclick 可用。

### 5. 改造 `renderDiary()` 计划区渲染（L28294-L28382）
替换原来的 buckets/groupHtmls 机制为扁平列表：
- 收集所有菜品（跳过 `source='manual'`），按 source 优先级排序：existing/shopping/weekend → 0，preferred → 1，ai → 2
- 每道菜只渲染 checkbox + 菜名（无 × 删除按钮）
- 单个分组标题：`📋 ${meal.name}计划` + 已勾计数
- 保留 `✅ 确认计划` 按钮
- 去掉：`📋 食材推荐食谱`/`🤖 AI推荐食谱` 分组标题、`✏️ 手动添加` 输入框、`💡 三组并排比较` 提示、`×` 删除按钮

### 6. 保留的函数和数据兼容
- `diaryToggleDishConfirmed`、`confirmDiaryMealPlan`、`getDishMetaSafe`、`_backfillPlanDishMeta` 不变
- `removeDishFromMealPlan` 保留（其他地方可能调用）
- `dishMeta` 数据结构不变：`{source, confirmed, addedAt}`

## 验证
1. 食记页左上角显示 ☰ 按钮
2. 点击 ☰ 打开浮层，显示当前日期 + 早餐/午餐/晚餐选择
3. 三分区显示推荐食谱，现有/现购默认勾选
4. 确认后写入计划，食记列表扁平显示，现有/现购勾选状态保留，AI 未勾选在后
5. 无分组标题、无手动添加、无提示文字、无 × 删除按钮
6. `✅ 确认计划` 按钮仍可用
7. 食材准备页的 `generateFridgeMealPlan` 等原有功能不受影响
