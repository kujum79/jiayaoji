# 推荐多样性算法实现计划

## Context

用户要求AI推荐菜品时避免短期重复，增加多样性选择器（低/中/高），并持久化最近推荐记录到 localStorage。

**现有基础设施：**
- 单餐推荐 `_generateAiRecipesForSingleDateMeal()`（L15106）——构造 sys/usr prompt → callAliyunQwenApi
- 多日推荐 `generateMultiDayRecommendation(days)`（L26657）——已有"避免与近7天历史记录重复""一周内不重复"的文字约束，但无结构化数据支撑
- `buildRecommendationPrompt()`（L27669）——组装 user prompt
- 食材推荐食谱页 `#prepIngrRecOverlay`（L4009）——已有模式选择器（食疗调理/口味偏好/营养均衡/从食知选菜），在其后加多样性选择器
- 多日推荐写入完成在 L26804-26816（updateMealPlan + renderDiary + showToast）

## 实现方案

### 步骤1：数据结构 + 读写函数

在 `_generateAiRecipesForSingleDateMeal` 函数前（约 L15100）新增：

```js
// 多样性等级
var _diversityLevel = 'medium'; // low/medium/high

// 读取最近推荐记录（7天）
function getRecentRecommendations() {
  try { return JSON.parse(lsGet('recentRecommendations')) || {}; }
  catch(e) { return {}; }
}

// 写入推荐记录
function recordRecommendation(dateKey, mealKey, dishes) {
  var rec = getRecentRecommendations();
  // dishes: [{dish, reason, ingredients}]
  var today = dateToKey(new Date());
  dishes.forEach(function(d) {
    var name = d.dish || d.name || '';
    if (!name) return;
    rec[name] = rec[name] || {};
    rec[name].dates = rec[name].dates || [];
    rec[name].dates.push({ date: dateKey, meal: mealKey });
    rec[name].dates = rec[name].dates.slice(-21); // 保留最近21条
    // 记录菜系和主食材
    var info = DISH_INGREDIENTS[name];
    if (info) {
      rec[name].category = info.category || '';
      rec[name].mainIngredient = (info.ingredients && info.ingredients[0]) || '';
    }
  });
  // 清理超过7天的记录
  var cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 7);
  var cutoffKey = dateToKey(cutoff);
  Object.keys(rec).forEach(function(name) {
    rec[name].dates = (rec[name].dates || []).filter(function(d) { return d.date >= cutoffKey; });
    if (!rec[name].dates.length) delete rec[name];
  });
  lsSet('recentRecommendations', JSON.stringify(rec));
}

// 获取多样性约束文本（注入AI prompt）
function getDiversityConstraints(dateKey) {
  var rec = getRecentRecommendations();
  if (!Object.keys(rec).length) return '';
  var level = _diversityLevel;
  var days = level === 'low' ? 0 : level === 'high' ? 7 : 3;
  if (days === 0) return '';
  
  var cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  var cutoffKey = dateToKey(cutoff);
  
  var recentDishes = [];
  var recentCategories = {}; // category → [dates]
  var recentIngredients = {};
  
  Object.keys(rec).forEach(function(name) {
    var entry = rec[name];
    var recentDates = (entry.dates || []).filter(function(d) { return d.date >= cutoffKey; });
    if (recentDates.length) {
      recentDishes.push(name);
      if (entry.category) {
        recentCategories[entry.category] = recentCategories[entry.category] || [];
        recentCategories[entry.category].push(recentDates[0].date);
      }
      if (entry.mainIngredient) {
        recentIngredients[entry.mainIngredient] = recentIngredients[entry.mainIngredient] || 0;
        recentIngredients[entry.mainIngredient] += recentDates.length;
      }
    }
  });
  
  var constraints = '';
  if (recentDishes.length) {
    constraints += '【多样性约束】避免推荐以下最近' + days + '天已推荐的菜品：' + recentDishes.slice(0, 20).join('、') + '。\n';
  }
  if (level === 'high' && Object.keys(recentCategories).length) {
    var catList = Object.keys(recentCategories).join('、');
    constraints += '避免连续2天推荐同一菜系类型（近期已出现：' + catList + '），尝试推荐新菜系。\n';
  }
  if (level !== 'low') {
    var topIngs = Object.keys(recentIngredients).sort(function(a,b){ return recentIngredients[b]-recentIngredients[a]; }).slice(0,5);
    if (topIngs.length) {
      constraints += '避免连续3天使用同一主食材（近期高频：' + topIngs.join('、') + '）。\n';
    }
  }
  return constraints;
}
```

### 步骤2：UI —— 多样性选择器

在 `#prepIngrRecModeSel`（L4016-4021）的 mode 按钮后追加：

```html
<div style="display:flex;align-items:center;gap:4px;margin-left:auto;padding-right:4px;">
  <span style="font-size:11px;color:#9CA3AF;">多样性</span>
  <button class="mode-btn" data-diversity="low" onclick="selectDiversityLevel('low', this)" style="font-size:11px;padding:3px 8px;">低</button>
  <button class="mode-btn active" data-diversity="medium" onclick="selectDiversityLevel('medium', this)" style="font-size:11px;padding:3px 8px;">中</button>
  <button class="mode-btn" data-diversity="high" onclick="selectDiversityLevel('high', this)" style="font-size:11px;padding:3px 8px;">高</button>
</div>
```

新增 `selectDiversityLevel(level, btn)` 函数：
- 设置 `_diversityLevel = level`
- 高亮当前按钮，取消同组其他按钮 active

### 步骤3：AI Prompt 注入

**单餐推荐**（`_generateAiRecipesForSingleDateMeal` L15125）：
在 usr prompt 末尾追加：
```js
var divConstraints = getDiversityConstraints(dateKey);
if (divConstraints) usr += '；\n' + divConstraints;
```

**多日推荐**（`buildRecommendationPrompt` L27709 附近）：
在 prompt 组装末尾追加：
```js
var divConstraints = getDiversityConstraints(weekDateKeys[0]);
if (divConstraints) prompt += divConstraints + '\n';
```

### 步骤4：推荐完成后写入记录

**单餐推荐**（`_generateAiRecipesForSingleDateMeal` L15138 return 前）：
```js
if (dishes.length >= 3) {
  recordRecommendation(dateKey, mealKey, dishes);
  return { dishes: dishes.slice(0, 6), source: 'ai' };
}
```

**多日推荐**（`generateMultiDayRecommendation` L26804 updateMealPlan 后、L26815 renderDiary 前）：
```js
aiResponse.days.forEach(function(day, idx) {
  var dKey = weekDateKeys[idx];
  if (!dKey) return;
  ['breakfast','lunch','dinner'].forEach(function(mt) {
    var meal = (day.meals || {})[mt];
    if (meal && (meal._normalizedDishes || meal.dishes)) {
      var dl = meal._normalizedDishes || meal.dishes;
      recordRecommendation(dKey, mt, dl.map(function(d){ return { dish: d.name, reason: d.reason || '', ingredients: d.ingredients || [] }; }));
    }
  });
});
```

## 关键文件
- `d:\jiayaoji\frontend\index.html` — 唯一改动文件

## 验证
1. 控制台 `lsGet('recentRecommendations')` → 初始为空 `{}`
2. 食材推荐食谱页 → 多样性选择器显示"低/中/高"三个按钮，默认"中"高亮
3. 执行一次 AI 推荐 → `recentRecommendations` 有数据，含菜品名/dates/category/mainIngredient
4. 再次推荐 → AI prompt 中含"避免推荐以下最近X天已推荐菜品"约束文本
5. 切换多样性等级为"低" → 约束文本为空（days=0）
6. 切换为"高" → 约束文本含"避免连续2天推荐同一菜系"和"避免连续3天使用同一主食材"
