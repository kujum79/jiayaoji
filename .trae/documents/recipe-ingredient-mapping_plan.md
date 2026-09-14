# 食材→食谱→用量映射表功能实现计划

## Context

用户要求实现"食材→食谱→用量"映射表，包含5个模块：预置50道菜、AI兜底生成、采购联动、localStorage持久化。

**关键发现：代码库已有大量基础设施，无需重复造轮子：**
- `DISH_INGREDIENTS`（L6769-6817）已有37道菜，每道含 `perServing: [{name, quantity, unit}]`（每人份用量）
- `getDishIngredientsWithServings(dishName, servings)`（L6819-6839）已按人数倍乘
- `computeIngredientNeeds()`（L9203-9337）已完整实现采购联动：读已确认计划→提取食材→按人数缩放→扣减冰箱库存→生成采购清单
- `callAliyunQwenApi()`（L26939）AI接口已有
- `getDishIngredients()`（L8411）已有本地推断兜底（无AI调用）

**缺口：**
1. DISH_INGREDIENTS 仅37道，需补到50道（+13道）
2. 未知菜谱无AI实时生成兜底（仅有本地推断）
3. AI生成的食材清单未缓存到localStorage

## 实现方案

### 步骤1：扩展 DISH_INGREDIENTS 至50道（L6810-6817 末尾追加）

在 `DISH_INGREDIENTS` 对象末尾（L6816 `'茄子炒肉末'` 后）追加13道常见家常菜：
- 炒菜类：宫保鸡丁、鱼香肉丝、回锅肉、土豆丝炒青椒
- 炖菜类：土豆炖牛肉、白菜炖豆腐、冬瓜排骨汤
- 汤类：紫菜蛋花汤、丝瓜蛋汤
- 主食类：葱油拌面、馄饨
- 素菜类：蒜蓉西兰花、清炒时蔬（应季蔬菜）

每道沿用现有格式：`{ ingredients, category, meals, perServing: [{name, quantity, unit}] }`，基准2人份=perServing×2。

### 步骤2：新增 AI 兜底函数 `getDishIngredientsAI(dishName, servings)`

位置：`getDishIngredientsWithServings` 函数后（L6839 后）。

```
function getDishIngredientsAI(dishName, servings) {
    1. 先查 localStorage `aiRecipeCache`（带家庭码前缀，用 lsGet/lsSet）
    2. 命中缓存 → 返回按 servings 缩放后的用量
    3. 未命中 → 调 callAliyunQwenApi 生成：
       sys: "请为[菜名]生成食材清单及用量，基准2人份，只返回JSON：{ingredients:[{name,quantity,unit}]}"
       usr: "菜名：X，2人份"
    4. 解析返回 → 存入 aiRecipeCache（持久化）→ 返回按 servings 缩放
    5. AI失败 → 回退到 getDishIngredients 推断（返回食材名无量）
}
```

### 步骤3：新增统一入口 `getDishIngredientsSmart(dishName, servings)`

```
function getDishIngredientsSmart(dishName, servings) {
    1. 先查 DISH_INGREDIENTS → getDishIngredientsWithServings
    2. 未命中 → 异步调 getDishIngredientsAI（返回 Promise）
    3. 都失败 → getDishIngredients 推断兜底
}
```

注：`computeIngredientNeeds` 已调用 `getOnePersonIngredients`，该函数内部用 `getDishIngredientsWithServings`。无需改 computeIngredientNeeds——现有采购联动已完整工作。

### 步骤4：在菜品详情面板接入 AI 兜底

`renderDishDetailFromKnowledge`（L12878）中，当 `savedIngredients` 为空且 `DISH_INGREDIENTS` 未命中时，异步调 `getDishIngredientsAI` 补全食材用量并重渲染。

## 关键文件

- `d:\jiayaoji\frontend\index.html` — 唯一改动文件

## 验证

1. 控制台执行 `Object.keys(DISH_INGREDIENTS).length` → 期望 50
2. 执行 `getDishIngredientsWithServings('宫保鸡丁', 3)` → 返回含花生米/鸡丁/干辣椒等，量×1.5
3. 执行 `getDishIngredientsAI('非预置菜名', 2)` → AI 生成并缓存；再调一次从缓存秒回
4. 检查 `lsGet('aiRecipeCache')` 有持久化数据
5. 食材准备页→生成采购计划→确认采购清单含用量且扣减冰箱库存
