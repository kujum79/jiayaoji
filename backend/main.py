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

import json
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ========== 阿里云通义千问配置 ==========
# 临时测试直接硬编码 API Key；正式发布建议改回环境变量配置
import os
ALIYUN_API_KEY = os.getenv("ALIYUN_API_KEY", "")
if not ALIYUN_API_KEY:
    raise ValueError("ALIYUN_API_KEY 未设置，请检查环境变量")
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


class ChatResponse(BaseModel):
    response: str


class ParseRequest(BaseModel):
    text: str = Field(..., min_length=1)


class ParseResponse(BaseModel):
    dish_name: str
    ingredients: List[str]
    steps: List[str]
    tags: List[str]


@app.get("/")
def root() -> Dict[str, str]:
    return {"ping": "pong"}


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
async def call_qwen(messages: List[Dict[str, str]], *, json_mode: bool = False) -> str:
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
