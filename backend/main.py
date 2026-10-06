"""
家肴记 Jiayaoji 后端服务（适配阿里云通义千问）

运行 README：
1. 建议使用 Python 3.9+。
2. 安装依赖（如果已有则跳过）：
   pip install fastapi uvicorn httpx
3. 获取阿里云 DashScope API Key：
   - 登录 `https://dashscope.console.aliyun.com/`
   - 进入 API-KEY 管理，创建新 Key，复制保存。
4. 将 Key 配置到环境变量（或直接写代码里）：
   Windows PowerShell:
   $env:ALIYUN_API_KEY="sk-你的阿里云Key"
   或直接在下方 ALIYUN_API_KEY = "你的Key"
5. 在项目根目录运行：
   uvicorn backend.main:app --reload
6. 前端双击 frontend/index.html 或使用 Live Server 打开。
"""

import base64
import json
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

import httpx
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ========== 阿里云通义千问配置 ==========
# 临时测试直接硬编码 API Key；正式发布建议改回环境变量配置
ALIYUN_API_KEY = "sk-d59f96e872d54e23ba692fd7d09eee1c"
ALIYUN_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
ALIYUN_MODEL = "qwen-turbo"  # 可选 qwen-plus, qwen-max, qwen-turbo（免费额度多）
# ========================================

# ========== 多模型路由层（model_config.py） ==========
# 未配置环境变量时，沿用上面的内置 Key，保证现有调用不断服；
# 换 Key / 加新服务商只需设置环境变量（DASHSCOPE_API_KEY 等）或改 model_config.py，无需动这里。
os.environ.setdefault("ALIYUN_API_KEY", ALIYUN_API_KEY)
try:
    from backend.model_config import call_ai  # 包形式导入（项目根目录启动 / Render 部署）
except ImportError:  # 直接从 backend 目录启动时按同级模块导入
    from model_config import call_ai  # noqa: E402  （需在 setdefault 之后导入，确保能读到内置 Key）
# ===================================================

app = FastAPI(
    title="家肴记 - AI家庭膳食助理",
    description="Jiayaoji backend powered by FastAPI and Aliyun Qwen.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FamilyMember(BaseModel):
    name: str
    age: Optional[int] = None
    place: Optional[str] = None
    taste: Optional[str] = None


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    family: List[FamilyMember] = Field(default_factory=list)
    mode: str = "seasonal"  # 推荐模式：seasonal/diet/fridge/preference/nutrition，默认当季推荐


class PlanRequest(BaseModel):
    system_prompt: str = Field(..., min_length=1)
    user_prompt: str = Field(..., min_length=1)
    max_tokens: int = Field(default=8192, ge=100, le=32768)
    mode: Optional[str] = "seasonal"  # AI推荐模式（由前端 RECOMMEND_MODES 传入），缺省当季推荐
    task_type: Optional[str] = None  # V119: 功能标识（plan/audit…），审核评分等走强档模型并计入用量统计


class ChatResponse(BaseModel):
    response: str


class ParseRequest(BaseModel):
    text: str = Field(..., min_length=1)


class ParseResponse(BaseModel):
    dish_name: str
    ingredients: List[str]
    steps: List[str]
    tags: List[str]


class ParseLinkRequest(BaseModel):
    url: str


class ParseLinkResponse(BaseModel):
    title: str
    content: str
    tags: List[str]
    mainCategory: str  # 食材/食谱/节气/食疗/对症
    subCategory: str   # 对应的子分类
    images: List[str] = []  # base64编码的图片列表


class ParseVoiceRequest(BaseModel):
    text: str
    context: str = "食材"  # "食材" 或 "食记"


@app.get("/")
def root() -> RedirectResponse:
    # 根路径直接进入家肴记应用，避免浏览器裸显 JSON（健康检查见 /health）
    return RedirectResponse(url="/app")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"ping": "pong"}


# ========== 语音指令解析接口 ==========
@app.post("/parse_voice")
async def parse_voice(req: ParseVoiceRequest) -> Dict[str, Any]:
    """
    统一处理食材和食记的语音意图解析。
    请求体: { "text": "用户语音转文字", "context": "食材" 或 "食记" }
    返回: 结构化JSON指令
    """
    context = req.context.strip()
    voice_text = req.text.strip()

    if context == "食记":
        system_prompt = """你是一个语音指令解析助手，专门解析"食记"（三餐计划与记录）场景的语音指令。

用户可能说的话及对应的指令类型：
1. 增加菜谱："明天晚餐加个紫菜蛋花汤"、"后天午餐加个番茄炒蛋"
2. 替换菜谱："明天午餐改成番茄炒蛋"、"今天晚餐换成红烧肉"
3. 删除菜谱："删掉明天早餐的鸡蛋"、"去掉后天午餐的排骨"
4. 清空餐次："明天午餐不要了"、"后天早餐清空"
5. 确认计划："确认明天的午餐"、"确认今天的早餐"
6. 记录实际："今天午餐吃了红烧肉，好吃"、"昨天晚餐吃了鱼汤，味道不错"

请将用户的语音文字解析为以下JSON格式（只返回JSON，不要其他文字）：
{
  "action": "add_plan" / "replace_plan" / "delete_plan" / "clear_plan" / "confirm_plan" / "record_actual",
  "date": "今天" / "明天" / "昨天" / "后天",
  "meal": "breakfast" / "lunch" / "dinner",
  "dish": "菜品名称",
  "rating": "😋" / "🙂" / "😐" / "😞"
}

规则：
- date: 从文字中提取日期关键词，默认"今天"
- meal: 早餐→breakfast, 午餐→lunch, 晚餐→dinner
- dish: 提取菜品名称，去掉量词和动词
- rating: 仅record_actual需要。根据评价语气判断：好吃/赞/棒→😋，不错/还行→🙂，一般→😐，难吃/不好→😞。其他action留空字符串""
- 如果无法识别指令，返回 {"action": "unknown"}
""".strip()
    else:
        system_prompt = """你是一个语音指令解析助手，专门解析"食材"（冰箱食材管理）场景的语音指令。

用户可能说的话及对应的指令类型：
1. 增加："番茄加2个"、"再加两个鸡蛋"、"牛肉加500克"
2. 减少："鸡蛋用掉3个"、"用了两个番茄"、"牛肉减少100克"
3. 设置："牛肉改成500克"、"鸡蛋设为10个"、"番茄改成3个"
4. 清空："鸡蛋用完了"、"番茄没了"、"牛奶清空"
5. 查询："还有多少番茄"、"鸡蛋剩多少"、"牛肉还有多少"
6. 采购计划："看看要买什么"、"采购计划"、"要买什么"
7. 确认采购："就按这个买"、"确认采购"、"按这个买"
8. 移除采购项："番茄不用买了"、"鸡蛋不用买"
9. 添加采购项："要买莴苣"、"买2斤土豆"、"需要买牛肉"、"今天要买西红柿"

请将用户的语音文字解析为以下JSON格式（只返回JSON，不要其他文字）：
{
  "action": "increase" / "decrease" / "set" / "clear" / "query" / "view_shopping" / "confirm_shopping" / "remove_shopping" / "add_to_shopping",
  "name": "食材名称",
  "quantity": 数量(数字),
  "unit": "单位"
}

规则：
- name: 提取食材名称，去掉数字、单位和动词
- quantity: 提取数字（中文数字"两"=2，"十"=10等需转换为阿拉伯数字），无数量时为0
- unit: 克/个/只/条/瓶/包/袋/盒/把/根/块/颗/斤/两/升/毫升等，默认"个"
- view_shopping/confirm_shopping: name/quantity/unit可为空
- add_to_shopping: name必填，quantity无则为0
- 如果无法识别指令，返回 {"action": "unknown"}
""".strip()

    try:
        content = await call_qwen(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": voice_text},
            ],
            json_mode=True,
            task_type="parse_voice",
        )
        result = extract_json_object(content)
        # 确保返回的字段完整
        if context == "食记":
            result.setdefault("action", "unknown")
            result.setdefault("date", "今天")
            result.setdefault("meal", "")
            result.setdefault("dish", "")
            result.setdefault("rating", "")
        else:
            result.setdefault("action", "unknown")
            result.setdefault("name", "")
            result.setdefault("quantity", 0)
            result.setdefault("unit", "个")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音解析失败: {str(e)}")



def build_family_context(family: List[FamilyMember]) -> str:
    if not family:
        return "暂无家庭档案，请按普通家庭口味给出建议。"

    lines = []
    for member in family:
        parts = [
            f"姓名：{member.name}",
            f"年龄：{member.age if member.age is not None else '未知'}",
            f"所在地：{member.place or '未知'}",
            f"口味：{member.taste or '未知'}",
        ]
        lines.append("；".join(parts))
    return "\n".join(lines)


# ========== AI推荐模式 System Prompt 模板 ==========
DEFAULT_RECOMMEND_MODE = "seasonal"

RECOMMEND_MODE_PROMPTS = {
    "seasonal": "你是家庭膳食顾问，擅长顺时养生。请根据当前节气推荐时令菜谱，优先选用当季蔬菜、水果与水产，兼顾口感、营养与家常易做程度。",
    "diet": "你是中医食疗顾问。请根据家庭成员的年龄、体质与健康状态推荐调理菜谱，结合温和的中医食疗原则，食材搭配与禁忌说明要清晰；不要编造医疗诊断。",
    "fridge": "你是家庭膳食顾问，擅长清空冰箱。请优先消耗家中现有食材推荐菜谱，尽量减少需要额外采购的食材，并对缺少的关键食材给出“缺啥补啥”建议。",
    "preference": "你是家庭膳食顾问，熟悉全家人的口味偏好。请根据用户历史好评、籍贯口味、爱吃与忌口推荐菜品，避免重复近期常吃的菜，让全家都爱吃。",
    "nutrition": "你是营养师。请根据本周饮食结构的营养缺口（如深色蔬菜、优质蛋白、奶类、豆制品、粗粮摄入不足）推荐补充菜谱，参照《中国居民膳食指南》做到荤素搭配、均衡多样。",
}

# 各模式给 /plan 接口的前置侧重指令（/chat 使用完整模板，/plan 前端自带详细上下文，仅追加侧重）
RECOMMEND_MODE_DIRECTIVES = {
    "diet": "【推荐模式：食疗调理】请以成员体质与健康状态的食疗调理为首要侧重，",
    "fridge": "【推荐模式：消耗家中食材】请以优先消耗家中现有食材、减少额外采购为首要侧重，",
    "preference": "【推荐模式：家庭偏好】请以贴合家人历史好评与口味偏好、避免近期重复为首要侧重，",
    "nutrition": "【推荐模式：营养均衡】请以补齐近期营养缺口、参照《中国居民膳食指南》均衡搭配为首要侧重，",
}


def get_system_prompt(mode: str, family: list) -> str:
    """根据推荐模式返回对应的 System Prompt 模板，并附带家庭档案与通用回答要求。"""
    base = RECOMMEND_MODE_PROMPTS.get((mode or "").strip(), RECOMMEND_MODE_PROMPTS[DEFAULT_RECOMMEND_MODE])
    family_context = build_family_context(family or [])
    return f"""
你是“家肴记”的 AI 家庭膳食助理，也是一位熟悉武汉家常菜、济南长辈饮食和青少年口味的家庭厨师。
{base}

家庭档案：
{family_context}

回答要求：
1. 使用中文，语气亲切实用。
2. 推荐菜谱时优先给出菜名、适合谁、推荐理由、主要食材、简要步骤。
3. 如果用户在清理冰箱或提供食材，请必须用 Markdown 返回，并包含“菜名”“理由”“缺啥补啥”三个部分。
4. 不要编造医疗诊断；涉及老人或孩子时只给温和饮食建议。
""".strip()


def get_mode_directive(mode: Optional[str]) -> str:
    """/plan 接口用：返回模式侧重指令前缀；默认(seasonal)或未知模式返回空串，保持前端 prompt 原样。"""
    key = (mode or "").strip()
    directive = RECOMMEND_MODE_DIRECTIVES.get(key)
    return f"{directive}并与下列要求保持一致。\n\n" if directive else ""


# ========== AI 调用核心（V113 起走多模型路由层，失败自动降级下一家服务商；V115 起带 task_type 记录调用日志） ==========
async def call_qwen(
    messages: List[Dict[str, str]],
    *,
    json_mode: bool = False,
    max_tokens: int = 8192,
    task_type: str = "chat",
) -> str:
    """按 model_config.PROVIDERS 优先级路由调用（失败自动降级），每次调用记录成本日志。

    task_type 为端点名（chat/plan/parse/parse_voice/parse_link），写入 ai_logs.jsonl 便于按功能统计。
    保留原语义：json_mode 低温度、max_tokens 透传、失败抛 HTTPException(502) 中文详情。
    """
    try:
        result = await call_ai("text", {
            "task_type": task_type,
            "messages": messages,
            "json_mode": json_mode,
            "max_tokens": max_tokens,
            "temperature": 0.2 if json_mode else 0.7,
        })
        return result["content"].strip()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务调用失败：{exc}") from exc
# ============================================


def extract_json_object(text: str) -> Dict[str, Any]:
    """从 AI 返回的文本中提取第一个完整的 JSON 对象"""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise HTTPException(status_code=502, detail="AI 未返回可解析的 JSON")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="AI 返回的 JSON 格式不合法") from exc


def normalize_parse_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    dish_name = str(raw.get("dish_name") or raw.get("菜名") or "未命名菜谱").strip()
    ingredients = raw.get("ingredients") or raw.get("主料") or raw.get("食材") or []
    steps = raw.get("steps") or raw.get("步骤") or []
    tags = raw.get("tags") or raw.get("标签") or []

    if isinstance(ingredients, str):
        ingredients = [item.strip() for item in re.split(r"[，,、\n]", ingredients) if item.strip()]
    if isinstance(steps, str):
        steps = [item.strip() for item in re.split(r"\n+|(?:\d+[.、])", steps) if item.strip()]
    if isinstance(tags, str):
        tags = [item.strip() for item in re.split(r"[，,、\n]", tags) if item.strip()]

    return {
        "dish_name": dish_name,
        "ingredients": [str(item).strip() for item in ingredients if str(item).strip()],
        "steps": [str(item).strip() for item in steps if str(item).strip()],
        "tags": [str(item).strip() for item in tags if str(item).strip()],
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> Dict[str, str]:
    # 根据推荐模式（seasonal/diet/fridge/preference/nutrition）切换 System Prompt 模板
    system_prompt = get_system_prompt(req.mode, req.family)

    content = await call_qwen(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": req.prompt},
        ],
        task_type="chat",
    )
    return {"response": content}


@app.post("/plan")
async def plan(req: PlanRequest) -> Dict[str, str]:
    """食谱计划生成接口（前端同源调用，避免 CORS）"""
    # 前端已构建详细的五维上下文 system_prompt；mode 非默认时在其最前面追加模式侧重指令
    system_content = get_mode_directive(req.mode) + req.system_prompt
    # V119: 功能标识白名单（只允许小写字母/下划线，防日志污染），缺省 plan
    task_type = (req.task_type or "plan").strip()
    if not re.fullmatch(r"[a-z_]{1,24}", task_type):
        task_type = "plan"
    content = await call_qwen(
        [
            {"role": "system", "content": system_content},
            {"role": "user", "content": req.user_prompt},
        ],
        json_mode=False,
        max_tokens=req.max_tokens,
        task_type=task_type,
    )
    return {"response": content}


@app.post("/parse", response_model=ParseResponse)
async def parse_recipe(req: ParseRequest) -> Dict[str, Any]:
    system_prompt = """
请从用户提供的菜谱文本中提取菜名、主料、步骤，并打上标签。
标签只能从以下范围中选择或组合：鱼类、肉类、素菜、汤类、春夏、秋冬、家宴、快手菜。
**必须只返回 JSON，不要任何额外文字或 Markdown 包裹。**
JSON 格式必须严格符合：
{
  "dish_name": "红烧肉",
  "ingredients": ["五花肉"],
  "steps": ["步骤1"],
  "tags": ["肉类", "秋冬", "家宴"]
}
""".strip()

    content = await call_qwen(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": req.text},
        ],
        json_mode=True,
        task_type="parse",
    )
    parsed = extract_json_object(content)
    return normalize_parse_result(parsed)


@app.post("/parse_link", response_model=ParseLinkResponse)
async def parse_link(req: ParseLinkRequest):
    """
    接收一个网文链接，抓取页面正文，用AI提取结构化饮食知识。
    """
    url = req.url

    # 1. 抓取网页内容
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.google.com/",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        session = requests.Session()
        session.headers.update(headers)
        resp = session.get(url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        # 修复编码：requests默认用ISO-8859-1，需用chardet检测的实际编码
        resp.encoding = resp.apparent_encoding or resp.encoding
        html = resp.text
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"抓取网页失败: {str(e)}")

    # 1.5 检测反爬验证页面
    verification_keywords = ["滑动验证", "安全验证", "人机验证", "请完成验证", "captcha", "verify",
                             "nc_1_n1z", "nc_iconfont", "滑动滑块", "请拖动", "_n1z_sessionid"]
    html_lower = html.lower()
    detected_keywords = [kw for kw in verification_keywords if kw.lower() in html_lower]
    if len(detected_keywords) >= 2:
        raise HTTPException(
            status_code=406,
            detail="该网页有反爬验证机制（滑动验证码），无法自动抓取。请换用其他来源的链接，或手动复制文章内容后使用「手动输入」方式添加。"
        )

    # 2. 提取正文（使用BeautifulSoup）
    soup = BeautifulSoup(html, 'lxml')
    # 移除脚本、样式、导航、页脚等干扰元素
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]):
        tag.decompose()
    # 移除常见广告/评论区
    for ad in soup.find_all(class_=re.compile(r"ad|advert|comment|sidebar|recommend|related|footer|header|nav", re.I)):
        ad.decompose()

    # 尝试从常见文章容器中提取正文
    article_body = None
    for selector in ["article", "main", ".article-content", ".article-body", ".content",
                     ".post-content", ".entry-content", ".detail-content", ".article",
                     "#article", "#content", "#main-content", ".markdown-body",
                     ".rich-text", ".text-content", "#js_content"]:
        found = soup.select_one(selector)
        if found and len(found.get_text(strip=True)) > 100:
            article_body = found
            break

    # 如果找到了文章容器，优先使用它；否则用整个页面
    text_source = article_body if article_body else soup
    text = text_source.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    clean_text = "\n".join(lines)
    # 截取前2000字符（防止token超限）
    content_preview = clean_text[:2000]

    # 如果正文太短，尝试用 <p> 标签拼接
    if len(content_preview) < 100:
        paragraphs = soup.find_all("p")
        p_text = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
        content_preview = p_text[:2000] if p_text else content_preview

    # 3. 提取图片（从文章容器或整个页面）
    img_source = article_body if article_body else soup
    img_tags = img_source.find_all("img")
    img_urls = []
    for img in img_tags:
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if not src:
            continue
        full_url = urljoin(url, src)
        # 过滤小图标和logo
        if re.search(r"logo|icon|avatar|emoji|sprite|loading|placeholder", src, re.I):
            continue
        img_urls.append(full_url)
    # 也检查 og:image
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        img_urls.insert(0, og_image["content"])
    # 去重，最多5张
    seen = set()
    unique_urls = [u for u in img_urls if not (u in seen or seen.add(u))][:5]
    # 下载并转base64
    images_b64 = []
    for img_url in unique_urls:
        try:
            r = session.get(img_url, timeout=8)
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "image/jpeg")
            if not ct.startswith("image/"):
                ct = "image/jpeg"
            b64 = f"data:{ct};base64,{base64.b64encode(r.content).decode()}"
            images_b64.append(b64)
        except Exception:
            pass

    # 4. 获取页面标题（作为fallback）
    page_title = ""
    title_tag = soup.find("title")
    if title_tag:
        page_title = title_tag.get_text(strip=True)
    h1_tag = soup.find("h1")
    if h1_tag and not page_title:
        page_title = h1_tag.get_text(strip=True)

    # 5. 调用AI提取结构化信息（通义千问）
    try:
        prompt = f"""
        你是一位饮食知识提取专家。请从以下网页正文中提取饮食相关知识，并返回JSON格式。
        要求：
        - title: 提取一个简洁的标题（如网页已有标题可参考优化）
        - content: 提取核心饮食知识内容（100-200字），尽量保留有价值的具体信息
        - tags: 生成3-5个关键词标签
        - mainCategory: 从以下分类中选择一个最合适的：食材、食谱、节气、食疗、对症
        - subCategory: 根据主分类选择子分类，例如食材的子分类可以是：蔬菜、肉类、水果等（请自由判断）

        注意：即使内容不够完整，也请尽力从中提取有价值的信息，不要回复"无法提取"。如果内容确实与饮食无关，title用网页标题，content总结网页大意，mainCategory选"食谱"。

        网页标题：{page_title}

        网页正文：
        {content_preview}
        """
        messages = [
            {"role": "system", "content": "你是一个专业的饮食知识提取助手，只返回JSON，不要其他文字。"},
            {"role": "user", "content": prompt}
        ]
        result_text = await call_qwen(messages, json_mode=True, task_type="parse_link")
        data = extract_json_object(result_text)
        # 确保字段存在
        title = data.get("title") or page_title or "未命名知识"
        content = data.get("content") or content_preview[:200]
        tags = data.get("tags", [])
        main_category = data.get("mainCategory", "食谱")
        sub_category = data.get("subCategory", "家常")
        # 验证分类是否合法
        valid_categories = ["食材", "食谱", "节气", "食疗", "对症"]
        if main_category not in valid_categories:
            main_category = "食谱"
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI解析失败: {str(e)}")

    return {
        "title": title,
        "content": content,
        "tags": tags,
        "mainCategory": main_category,
        "subCategory": sub_category,
        "images": images_b64
    }


# ========== 拍照识别食材（通义千问 VL 多模态） ==========
ALIYUN_VL_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
ALIYUN_VL_MODEL = "qwen-vl-plus"

_INGREDIENT_VL_PROMPT = (
    "你是一位家庭食材识别助手。请识别这张图片中出现的所有【可烹饪食材/生鲜食品】"
    "（例如蔬菜、水果、肉蛋奶、水产、米面粮油、豆制品、干货、调料等），忽略餐具、人手、桌面、包装袋品牌等非食材物体。\n"
    "只返回 JSON，不要包含任何解释文字或 markdown 标记，格式严格如下：\n"
    '{"ingredients":[{"name":"番茄","quantity":"3个","confidence":0.95}]}\n'
    "要求：\n"
    "1. name 为中文食材通用名称，去掉品牌、部位修饰（如『某品牌』『土』等）；\n"
    "2. 同一种食材在结果中只出现一条，quantity 为图片中该食材的总数量；\n"
    "3. quantity 根据图片估算数量与常见单位（如 3个 / 500g / 1把 / 2斤 / 1袋），"
    "无法判断数量时返回空字符串；\n"
    "4. confidence 为 0 到 1 之间的识别置信度，不确定时给较低分值；\n"
    "5. 主料、配菜、配料、调料（如葱、姜、蒜、香菜、辣椒等）只要清晰可见都要识别；\n"
    "6. 图片中实在没有可识别食材时，返回 {\"ingredients\":[]}。"
)


@app.post("/recognize_ingredients")
async def recognize_ingredients(file: UploadFile = File(...)):
    """接收食材照片，调用通义千问 VL 识别，返回结构化食材列表。"""
    image_data = await file.read()
    if not image_data:
        raise HTTPException(status_code=400, detail="图片为空，请重新拍照或选择图片")
    # 12MB 上限（前端通常已压缩到 1MB 左右）
    if len(image_data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片过大（超过12MB），请压缩后上传")
    # 图片格式预检（JPEG/PNG/GIF/WEBP magic bytes）
    is_image = (
        image_data[:3] == b"\xff\xd8\xff"
        or image_data[:8] == b"\x89PNG\r\n\x1a\n"
        or image_data[:6] in (b"GIF87a", b"GIF89a")
        or (len(image_data) >= 12 and image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP")
    )
    if not is_image:
        raise HTTPException(status_code=400, detail="请上传图片文件（JPG/PNG/GIF/WEBP）")

    mime = file.content_type or "image/jpeg"
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    image_b64 = base64.b64encode(image_data).decode("utf-8")
    data_url = f"data:{mime};base64,{image_b64}"

    headers = {
        "Authorization": f"Bearer {ALIYUN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": ALIYUN_VL_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": _INGREDIENT_VL_PROMPT},
                ],
            }
        ],
        "temperature": 0.1,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(ALIYUN_VL_API_URL, headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"视觉识别服务错误：{exc.response.text[:300]}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"无法连接视觉识别服务：{str(exc)}") from exc

    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="视觉识别服务返回格式异常") from exc

    # 容错提取 JSON（模型可能带 ```json 代码块）
    try:
        parsed = extract_json_object(content)
    except HTTPException:
        raise HTTPException(status_code=502, detail="无法解析识别结果，请重新拍照或手动输入")

    raw_list = parsed.get("ingredients") if isinstance(parsed, dict) else None
    if not isinstance(raw_list, list):
        return {"ingredients": []}

    # 清洗：去空白/去重（同名保留首个）/置信度收敛到 0~1
    ingredients, seen = [], set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        qty = item.get("quantity", "")
        qty = "" if qty is None else str(qty).strip()
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        seen.add(name)
        ingredients.append({"name": name, "quantity": qty, "confidence": round(confidence, 2)})

    return {"ingredients": ingredients}


# ========== 食材知识 AI 生成（仅超级管理员可用） ==========
# 优先调用 DeepSeek（需环境变量 DEEPSEEK_API_KEY）；
# 未配置 DeepSeek Key 时自动回退到阿里云 DashScope 的 OpenAI 兼容模式（协议一致，保证功能可用）。
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
FALLBACK_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
FALLBACK_MODEL = "qwen-turbo"

# 医疗词汇黑名单：AI 返回内容中出现即删除（Prompt 已禁止，此处二次过滤兜底）
_MEDICAL_BLOCK_WORDS = [
    "治疗", "疗效", "治愈", "痊愈", "药用", "药效", "偏方", "主治", "消炎",
    "抗癌", "防癌", "降压", "降血压", "降血糖", "降血脂", "处方", "诊断",
    "康复", "止痛", "退烧", "忌口", "对症下药", "药膳",
]


class GenerateIngredientRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=30)
    isAdmin: bool = False  # TODO 正式版：改为后端会话/令牌校验，前端标记仅作过渡


class ExploreIngredientRequest(BaseModel):
    """食记「认识这道菜」食材探索接口——面向所有用户开放。"""
    name: str = Field(..., min_length=1, max_length=30, description="食材名")
    ageStart: int = Field(default=9, ge=3, le=100, description="年龄下限")
    ageEnd: int = Field(default=12, ge=3, le=100, description="年龄上限")
    isStudentMode: bool = Field(default=False, description="学生模式：更简单语言")


def _strip_medical_words(value: Any) -> Any:
    """递归清洗：删除字符串中出现的医疗词汇（黑名单兜底过滤）。"""
    if isinstance(value, str):
        cleaned = value
        for word in _MEDICAL_BLOCK_WORDS:
            if word in cleaned:
                cleaned = cleaned.replace(word, "")
        return cleaned
    if isinstance(value, list):
        return [_strip_medical_words(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_medical_words(v) for k, v in value.items()}
    return value


def _find_medical_words(value: Any, found: set) -> None:
    """递归收集命中的医疗词（用于日志/响应提示）。"""
    if isinstance(value, str):
        for word in _MEDICAL_BLOCK_WORDS:
            if word in value:
                found.add(word)
    elif isinstance(value, list):
        for v in value:
            _find_medical_words(v, found)
    elif isinstance(value, dict):
        for v in value.values():
            _find_medical_words(v, found)


# ========== 食材探索（食记「认识这道菜」） ==========
@app.post("/explore-ingredient")
async def explore_ingredient(req: ExploreIngredientRequest) -> Dict[str, Any]:
    name = req.name.strip()
    age_range = f"{req.ageStart}-{req.ageEnd}"
    student = req.isStudentMode

    system_prompt = "你是亲切的家庭饮食知识编辑，用简单口语介绍食材。只返回严格 JSON，不要 markdown 包裹。"
    if student:
        system_prompt = "你是儿童饮食启蒙老师，用童趣、简单的语言，多 emoji、避免难词。只返回严格 JSON。"

    knowledge_style = "简短、口语化、像妈妈/老师聊天" if student else "清晰、实用，适合全家阅读"
    fun_style = "一个有趣的小冷知识，简单又惊奇（适合孩子的好奇心）" if student else "一个有趣的冷知识，让人对这食材印象深刻"
    question_style = "一个开放性引导问题，让小朋友愿意去厨房观察/尝试（用 emoji 让它更有趣）" if student else "（学生模式才提供，此处留空）"

    user_prompt = f"""
请介绍食材「{name}」，目标读者{age_range}岁（{'学生模式' if student else '普通家庭'}）。
严格按以下 JSON 结构返回：
{{
  "name": "食材名",
  "emoji": "一个最贴切的 emoji",
  "knowledge": "📖 小知识：{knowledge_style}（80-150字），介绍它是什么、来自哪里、有什么营养",
  "funFact": "🍽 有趣的事：{fun_style}（30-80字）",
  "question": "✍️ 想一想：{question_style}",
  "imagePrompt": "English prompt for a high quality, kid-friendly food photography of this ingredient"
}}

硬性要求：
1. 全文禁止出现任何医疗词汇（治疗、疗效、药用、偏方、主治、消炎、抗癌、降压、降血糖、诊断、处方、忌口、康复等），只描述营养与饮食文化；
2. 知识要积极正面，引导孩子愿意尝试；
3. 语言简单，不用专业术语；
4. emoji 放在对应开头，不要插在句子中间；
5. question 字段只有学生模式才生成，非学生模式填空字符串 ""；
6. 只返回 JSON。
""".strip()

    use_deepseek = bool(DEEPSEEK_API_KEY)
    api_url = DEEPSEEK_API_URL if use_deepseek else FALLBACK_API_URL
    api_key = DEEPSEEK_API_KEY if use_deepseek else ALIYUN_API_KEY
    model = DEEPSEEK_MODEL if use_deepseek else FALLBACK_MODEL

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.6 if student else 0.4,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(api_url, headers=headers, json=payload)
            response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        # 后端挂了时前端会 fallback 到本地 mock
        raise HTTPException(status_code=503, detail=f"AI 服务暂不可用：{str(exc)[:200]}")

    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        result = extract_json_object(content)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AI 返回格式异常")

    # 医疗词过滤
    found: set = set()
    _find_medical_words(result, found)
    result = _strip_medical_words(result)

    # 字段规整
    image_prompt = str(result.get("imagePrompt") or f"fresh {name} ingredient food photography")[:200]
    image_url = (
        f"https://image.pollinations.ai/prompt/{quote(image_prompt)}"
        f"?width=640&height=480&nologo=true&seed={random.randint(1, 9999)}"
    )

    return {
        "name": str(result.get("name") or name).strip(),
        "emoji": str(result.get("emoji") or "🥗").strip(),
        "knowledge": str(result.get("knowledge") or "").strip(),
        "funFact": str(result.get("funFact") or "").strip(),
        "question": str(result.get("question") or "").strip(),
        "imageUrl": image_url,
        "engine": model,
    }


@app.post("/generate-ingredient")
async def generate_ingredient(req: GenerateIngredientRequest) -> Dict[str, Any]:
    """
    食材知识 AI 生成：特征 / 营养特点 / 主产地 / 营养成分表（100g）/ 1-8 道搭配方案 / 配图。
    仅超级管理员可调用（当前由前端传 isAdmin 标记，正式版改为后端校验）。
    """
    # 1. 超管校验（过渡方案）
    if not req.isAdmin:
        raise HTTPException(status_code=403, detail="仅超级管理员可使用 AI 生成食材知识")

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="食材名称不能为空")

    # 2. Prompt（见需求 SPEC）：严格 JSON + 禁止医疗词汇
    system_prompt = (
        "你是家庭饮食知识编辑，为家庭食谱应用撰写食材百科条目。"
        "只返回严格 JSON，不要 markdown 包裹，不要任何解释文字。"
    )
    user_prompt = f"""
请为食材「{name}」生成百科信息，严格按以下 JSON 结构返回：
{{
  "name": "食材名称",
  "features": "特征（60-100字：外观、口感、常见品种）",
  "nutrition": "营养特点（60-100字：主要营养素与膳食价值）",
  "origin": "主产地（30字以内）",
  "nutritionTable": [["成分", "含量（每100g可食部）"], ["能量", "xx kcal"], ...共5-8行],
  "pairings": ["菜名：一句话搭配理由", ...1-8道家常搭配],
  "imagePrompt": "English prompt for a high quality food photography of this ingredient"
}}

硬性要求：
1. 全文禁止出现任何医疗词汇（如治疗、疗效、药用、偏方、主治、消炎、抗癌、降压、降血糖、处方、诊断、忌口、康复等），只描述营养与饮食搭配；
2. 营养成分数值参照《中国食物成分表》的常识范围，不确定的成分不要编造，能量单位用 kcal；
3. pairings 为 1-8 道家常菜搭配，每条格式如「番茄炒蛋：酸甜开胃，经典家常」；
4. imagePrompt 为英文，描述该食材的真实摄影照片，不含文字元素；
5. 只返回 JSON。
""".strip()

    use_deepseek = bool(DEEPSEEK_API_KEY)
    api_url = DEEPSEEK_API_URL if use_deepseek else FALLBACK_API_URL
    api_key = DEEPSEEK_API_KEY if use_deepseek else ALIYUN_API_KEY
    model = DEEPSEEK_MODEL if use_deepseek else FALLBACK_MODEL

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(api_url, headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务错误：{exc.response.text[:300]}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"无法连接 AI 服务：{str(exc)}") from exc

    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="AI 返回格式异常") from exc

    try:
        result = extract_json_object(content)
    except HTTPException:
        raise HTTPException(status_code=502, detail="AI 未返回有效 JSON，请重试")

    # 3. 医疗词二次过滤（黑名单兜底）
    found: set = set()
    _find_medical_words(result, found)
    result = _strip_medical_words(result)
    if found:
        result["medicalWordsRemoved"] = sorted(found)

    # 4. 字段规整
    pairings = result.get("pairings") or []
    if isinstance(pairings, str):
        pairings = [p.strip() for p in re.split(r"[，,、\n]", pairings) if p.strip()]
    pairings = [str(p).strip() for p in pairings if str(p).strip()][:8]
    table = result.get("nutritionTable") or []
    if not isinstance(table, list):
        table = []
    table = [[str(cell) for cell in row] if isinstance(row, (list, tuple)) else [str(row), ""] for row in table][:10]

    # 5. pollinations.ai 配图 URL
    image_prompt = str(result.get("imagePrompt") or f"fresh {name} ingredient food photography")[:300]
    image_url = (
        f"https://image.pollinations.ai/prompt/{quote(image_prompt)}"
        f"?width=640&height=480&nologo=true&seed={random.randint(1, 9999)}"
    )

    return {
        "name": str(result.get("name") or name).strip(),
        "features": str(result.get("features") or "").strip(),
        "nutrition": str(result.get("nutrition") or "").strip(),
        "origin": str(result.get("origin") or "").strip(),
        "nutritionTable": table,
        "pairings": pairings,
        "imageUrl": image_url,
        "engine": model,
    }


# ==================== V118: AI 用量监控 ====================
@app.get("/ai/usage")
def ai_usage(detail: int = 0, month: str = ""):
    """AI 用量汇总：默认本月。含总调用/Token/成本、按服务商、按功能占比、预算状态；detail=1 附最近 50 条明细。"""
    import time as _time
    try:
        from backend.model_config import get_ai_cost_summary, get_budget_status
    except ImportError:
        from model_config import get_ai_cost_summary, get_budget_status
    m = (month or "").strip() or _time.strftime("%Y-%m")
    summary = get_ai_cost_summary(month=m, recent_limit=50 if detail else 0)
    total_calls = summary["total_calls"]

    def _pct(calls: int) -> float:
        return round(calls * 100.0 / total_calls, 1) if total_calls else 0.0

    by_provider = [{"provider": k, **v, "pct": _pct(v["calls"])} for k, v in summary["by_provider"].items()]
    by_provider.sort(key=lambda x: -x["calls"])
    by_task = [{"task": k, **v, "pct": _pct(v["calls"])} for k, v in summary["by_task"].items()]
    by_task.sort(key=lambda x: -x["calls"])
    return {
        "month": m,
        "total_calls": total_calls,
        "total_tokens": summary["total_tokens"],
        "total_cost": round(summary["total_cost"], 4),
        "by_provider": by_provider,
        "by_task": by_task,
        "budget": get_budget_status(),
        "recent": summary.get("recent", []),
    }


@app.get("/ai/budget")
def ai_budget_get():
    try:
        from backend.model_config import load_budget
    except ImportError:
        from model_config import load_budget
    return load_budget()


@app.post("/ai/budget")
def ai_budget_post(body: Dict[str, Any]):
    try:
        from backend.model_config import save_budget
    except ImportError:
        from model_config import save_budget
    try:
        monthly = float((body or {}).get("monthly", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="预算格式错误")
    if monthly < 0:
        raise HTTPException(status_code=400, detail="预算不能为负数")
    save_budget(monthly)
    return {"monthly": monthly}


# ========== 前端静态文件托管（用于 TRAE 预览面板直接打开 APP） ==========
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

if FRONTEND_DIR.exists():
    @app.get("/app", include_in_schema=False)
    async def serve_app_home():
        """APP 首页入口：在 TRAE 中间预览栏直接访问即可打开家肴记 APP。"""
        index_path = FRONTEND_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="前端文件不存在")
        return FileResponse(index_path, media_type="text/html", headers={"Cache-Control": "no-store"})

    # 前端静态文件：用 catch-all GET 而非 app.mount("/")，避免 mount 抢掉所有 API 路由
    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(path: str):
        """静态文件服务：API 路由优先匹配，未命中时回退到前端文件。"""
        # 空路径或根路径 → index.html
        if not path or path == "":
            index_path = FRONTEND_DIR / "index.html"
            if index_path.exists():
                return FileResponse(index_path, media_type="text/html", headers={"Cache-Control": "no-store"})
        # 检查是否是已注册的 API 路径（跳过）
        api_paths = {
            "health", "chat", "plan", "parse", "parse_voice", "parse_link",
            "recognize_ingredients", "explore-ingredient", "generate-ingredient", "app",
            "docs", "redoc", "openapi.json", "docs/oauth2-redirect"
        }
        if path in api_paths:
            raise HTTPException(status_code=404, detail="Not Found")
        # 尝试找前端目录下的文件
        file_path = FRONTEND_DIR / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        # 都没找到 → 返回 index.html（SPA fallback）
        index_path = FRONTEND_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path, media_type="text/html", headers={"Cache-Control": "no-store"})
        raise HTTPException(status_code=404, detail="Not Found")
else:
    @app.get("/app", include_in_schema=False)
    async def serve_app_home():
        raise HTTPException(status_code=404, detail="frontend 目录不存在")
