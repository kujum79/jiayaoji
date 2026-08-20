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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ========== 阿里云通义千问配置 ==========
# 临时测试直接硬编码 API Key；正式发布建议改回环境变量配置
ALIYUN_API_KEY = "sk-d59f96e872d54e23ba692fd7d09eee1c"
ALIYUN_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
ALIYUN_MODEL = "qwen-turbo"  # 可选 qwen-plus, qwen-max, qwen-turbo（免费额度多）
# ========================================

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


class PlanRequest(BaseModel):
    system_prompt: str = Field(..., min_length=1)
    user_prompt: str = Field(..., min_length=1)
    max_tokens: int = Field(default=8192, ge=100, le=32768)


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
def root() -> Dict[str, str]:
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


# ========== 阿里云通义千问调用核心 ==========
async def call_qwen(messages: List[Dict[str, str]], *, json_mode: bool = False, max_tokens: int = 8192) -> str:
    headers = {
        "Authorization": f"Bearer {ALIYUN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": ALIYUN_MODEL,
        "input": {"messages": messages},
        "parameters": {
            "result_format": "message",
            "temperature": 0.7 if not json_mode else 0.2,
            "max_tokens": max_tokens,
        }
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(ALIYUN_API_URL, headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # 尝试解析错误响应体，可能是纯文本
        error_detail = exc.response.text[:500]  # 截取前500字符防止过大
        raise HTTPException(status_code=502, detail=f"阿里云 API 错误：{error_detail}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"无法连接阿里云 API：{str(exc)}") from exc

    # 安全解析 JSON
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raw_text = response.text[:500]
        raise HTTPException(status_code=502, detail=f"阿里云返回非 JSON 响应：{raw_text}") from exc

    try:
        return data["output"]["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="阿里云 API 响应格式异常") from exc
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
    family_context = build_family_context(req.family)
    system_prompt = f"""
你是“家肴记”的 AI 家庭膳食助理，也是一位熟悉武汉家常菜、济南长辈饮食和青少年口味的家庭厨师。
请根据以下家庭档案推荐菜谱，兼顾年龄、地域、口味、营养、易做程度和家庭场景。

家庭档案：
{family_context}

回答要求：
1. 使用中文，语气亲切实用。
2. 推荐菜谱时优先给出菜名、适合谁、推荐理由、主要食材、简要步骤。
3. 如果用户在清理冰箱或提供食材，请必须用 Markdown 返回，并包含“菜名”“理由”“缺啥补啥”三个部分。
4. 不要编造医疗诊断；涉及老人或孩子时只给温和饮食建议。
""".strip()

    content = await call_qwen(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": req.prompt},
        ]
    )
    return {"response": content}


@app.post("/plan")
async def plan(req: PlanRequest) -> Dict[str, str]:
    """食谱计划生成接口（前端同源调用，避免 CORS）"""
    content = await call_qwen(
        [
            {"role": "system", "content": req.system_prompt},
            {"role": "user", "content": req.user_prompt},
        ],
        json_mode=False,
        max_tokens=req.max_tokens,
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
        result_text = await call_qwen(messages, json_mode=True)
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
        return FileResponse(index_path, media_type="text/html")

    # 挂载前端目录的静态资源（css/js/图片等），放在最后注册以免覆盖 API 路由
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=False), name="frontend_static")
else:
    @app.get("/app", include_in_schema=False)
    async def serve_app_home():
        raise HTTPException(status_code=404, detail="frontend 目录不存在")
