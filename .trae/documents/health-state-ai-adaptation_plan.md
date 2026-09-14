# 健康状态 + 营养食疗 + AI 适配 实施计划

## Context（为什么做）
用户希望 AI 推荐食谱能根据家庭成员的阶段性健康状态（生长发育/术后恢复/慢病/孕期/产后）自动适配，并在食谱卡片上显示人群标签。调研发现：现有代码已有「人群类型 groupType」字段且已接入 AI 链路，选项含孕妇/术后恢复/慢病管理/体育生等，与用户需求高度重叠；食知·营养食疗也已有「常见疾病营养/乳母营养/术后恢复」等子分类。

为避免重复造轮子，经与用户确认采用**最小改动方案**：扩展现有 groupType（补齐缺失选项 + 增加日期/备注伴生字段）、复用并改名现有食疗分类、AI 推荐理由含关键词即自动打标签。这样 AI 链路（collectMemberHealthProfiles/getGroupTypeConstraints）基本已通，改动面可控。

## 关键文件
- 前端单页：`frontend/index.html`（所有 UI/JS 逻辑都在此文件）
- 后端：`backend/main.py`（/plan 接口，本次基本不动后端，AI 逻辑在前端 prompt 侧）

## 实施步骤

### 一、我家·成员健康状态设置（扩展现有 groupType）
**1. 补齐 groupType 下拉选项**（两处表单同步改）
- 添加成员表单 [index.html:3667-3675](file:///d:/jiayaoji/frontend/index.html#L3667) `#famGroupType`
- 编辑成员表单 [index.html:16630-16639](file:///d/huayaoji/frontend/index.html#L16630) `#editGroupType_${id}`
- 在现有选项基础上新增：`生长发育期`（👦）、`产后调理`（🤱）。最终选项顺序：普通/生长发育期/孕妇/产后调理/幼儿/体育生/术后恢复/老年人/慢病管理/简脂餐

**2. 新增 3 个健康状态伴生输入字段**（紧跟在 groupType 下拉后的动态字段区）
在两处表单 groupType 下方加：
- `startDate`（date input，必填提示"开始日期"）
- `endDate`（date input，可选，留空=至今）
- `note`（text input，placeholder"如：中学生正在长高"）
仅当 groupType ≠ 普通 时显示这些字段（复用现有 `toggleAddMemberDynamicFields`/`toggleEditMemberDynamicFields` 动态字段机制）。

**3. 保存逻辑写回 familyData**（两处）
- 添加成员保存：搜索 `famGroupType` 的写入处，新增 `healthStartDate/healthEndDate/healthNote` 三个字段写入成员对象
- 编辑成员保存 [index.html:16662-16695](file:///d:/jiayaoji/frontend/index.html#L16662)：在 `member.groupType = ...` 后追加三行写入
- 字段命名：`healthStartDate`、`healthEndDate`、`healthNote`（扁平字段，不嵌套对象，与现有 height/weight/groupType 风格一致）

**4. DEFAULT_FAMILY 种子数据补一个示例** [index.html:5285-5289](file:///d:/jiayaoji/frontend/index.html#L5285)
给"儿子"加 `groupType:'生长发育期', healthStartDate:'2026-09-01', healthNote:'中学生正在长高'`，让新用户开箱即见效果。

### 二、食知·营养食疗分类复用与补齐
**1. 改名现有子分类** [index.html:20151-20218](file:///d:/jiayaoji/frontend/index.html#L20151) CATEGORY_TREE_DATA
- `常见疾病营养` → `慢病调理`（低盐低脂）
- `乳母营养` → `产后调理`
（保留 `青少年营养/术后恢复/孕妇营养/幼儿营养/简脂餐` 不变）

**2. 给知识记录打 suitableFor 人群标签**
- DEFAULT_DIET_KNOWLEDGE [index.html:5551-5801](file:///d:/jiayaoji/frontend/index.html#L5551) 每条记录新增 `suitableFor: []` 字段，按菜品属性填入 `青少年营养`/`术后恢复`/`孕妇营养`/`慢病调理`/`产后调理` 之一或多个
- DIET_KNOWLEDGE_BASE [index.html:5293-5296](file:///d:/jiayaoji/frontend/index.html#L5293) 的 suitableFor 已有，保持

**3. 补充食疗种子条目**
在 DEFAULT_DIET_KNOWLEDGE 追加 4-6 条慢病调理/产后调理/青少年营养/术后恢复的示范菜（如：清蒸鲈鱼/山药排骨汤/菠菜猪肝汤/牛奶鸡蛋羹），mainCategory=food-therapy，subCategory 对应新分类名，带 suitableFor 标签。

**4. 筛选逻辑无需改** [handleSubCategoryClick L20313](file:///d:/jiayaoji/frontend/index.html#L20313) 现有按 mainCategory+subCategory 过滤会自动生效，改名后子分类即匹配新条目。

### 三、AI 推荐逻辑升级（复用现有链路，注入健康状态）
**1. collectMemberHealthProfiles 暴露健康状态** [index.html:27159-27191](file:///d:/jiayaoji/frontend/index.html#L27159)
在现有 `人群类型:${m.groupType}` 后追加：
- 健康状态起始/备注：`if(m.healthStartDate) parts.push('状态起始:'+m.healthStartDate)`；`if(m.healthNote) parts.push('状态说明:'+m.healthNote)`
- 计算 active：endDate 为空或 ≥今天视为进行中，标注"(进行中)"

**2. getGroupTypeConstraints 补齐规则** [index.html:27197-27220](file:///d:/jiayaoji/frontend/index.html#L27197) rules map 新增两条：
- `生长发育期` → 优先高钙高蛋白（牛奶、鸡蛋、鱼、豆腐、虾皮、芝麻酱）
- `产后调理` → 优先补气血易消化（鸡汤、鱼汤、小米粥、红枣、猪肝）
（术后恢复/慢病管理/孕妇已有规则或可补强，保持）

**3. 单餐 AI 推荐 prompt 注入健康状态** [_generateAiRecipesForSingleDateMeal L14767](file:///d:/jiayaoji/frontend/index.html#L14767)
当前该函数 prompt 只含节气/食材，**未含家庭成员**。改为：读取当前家庭成员，调用 collectMemberHealthProfiles + getGroupTypeConstraints 拼进 user_prompt；并在 sys prompt 追加"若因某成员健康状态推荐，reason 中标注'适合XX状态'"。复用 L27288 已有的"👤 适合/🎯 目标"指令口径。

**4. 后端不动**：/plan 接口接收前端已组装 prompt，无需改后端。

### 四、推荐结果展示人群标签（AI 理由关键词 → 自动打标）
**1. 新增解析工具函数**（在 escapeHtml 附近）
`parseHealthTags(reason)`：扫描 reason 文本，返回标签数组：
- 含 `长高/发育/生长/青少年` → `{emoji:'👦', label:'长高推荐'}`
- 含 `术后/恢复/愈合` → `{emoji:'💊', label:'术后调理'}`
- 含 `慢病/低盐/低脂/降压/降糖` → `{emoji:'🫀', label:'慢病适宜'}`
- 含 `孕期/孕妇/叶酸/胎儿` → `{emoji:'🤰', label:'孕期营养'}`
去重后返回。

**2. 食记·计划菜品卡片显示标签** [index.html:17529-17537](file:///d:/jiayaoji/frontend/index.html#L17529) plan-dish-card
在 `dish-reason` 渲染前，对 `reason` 调 parseHealthTags，把每个标签渲染成小色块 span（绿底），插在 dish-name 右侧或 reason 上方。

**3. 食材推荐食谱汇总卡片** [index.html:11431-11435](file:///d:/jiayaoji/frontend/index.html#L11431)
若 dish.reason 存在，同样调 parseHealthTags 渲染标签（紧贴 sourceLabel）。

**4. 食记·食谱选择浮层** [index.html:15584-15589](file:///d:/jiayaoji/frontend/index.html#L15584)
该浮层目前只显示菜名，可给 ai 分组的菜加 reason 解析后的标签（若有）。

## 验证（如何测试）
1. 启动后端 `py -m uvicorn backend.main:app --reload`，浏览器开 http://127.0.0.1:8000/app
2. **我家**：编辑"儿子"→人群类型选"生长发育期"→出现开始日期/备注输入→填 2026-09-01 / "中学生正在长高"→保存→重开编辑确认数据回填
3. **食知**：进食疗分类→营养食疗→看到"慢病调理""产后调理"子分类→点进去有种子菜条目
4. **AI 推荐**：食材推荐食谱→生成→确认推荐理由里出现"适合生长发育期"等字样→菜品卡片显示 👦 长高推荐 标签
5. 控制台无 parseHealthTags / collectMemberHealthProfiles 报错

## 不做
- 不新建独立 healthState 对象（避免与 groupType 重复）
- 不改后端 /plan 接口
- 不删除现有 groupType 选项（只新增）
- 不强行让 AI 返回结构化 tags 字段（用 reason 关键词即可，改动最小）
