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
import asyncio
import json
import os
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

import httpx
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
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
    from backend.model_config import call_ai, route_vision  # 包形式导入（项目根目录启动 / Render 部署）
except ImportError:  # 直接从 backend 目录启动时按同级模块导入
    from model_config import call_ai, route_vision  # noqa: E402  （需在 setdefault 之后导入，确保能读到内置 Key）
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
    # V140: json_mode 显式开关（缺省关）。/plan 是多任务统一代理入口（周计划/聊天回复/食材估算等均经此路由），
    # V139 曾无条件强制 JSON 导致聊天回复也输出原始 JSON（用户不可读）——现仅周计划等明确要 JSON 的调用方传 true
    json_mode: bool = False
    subscriptionPlan: Optional[str] = None  # V199/V202: 订阅 tier（basic/premium），后端追加专属 Prompt 指令


class RecommendRestaurantsRequest(BaseModel):
    """V132: 外出就餐·选餐厅推荐入参（前端同源调用）"""
    scene: str = ""      # 场景（家庭聚餐/朋友聚会…）
    people: str = ""     # 人数
    budget: str = ""     # 人均预算（如 50-100）
    cuisine: str = ""    # 菜系偏好
    location: str = ""   # 位置/地理范围
    taste: str = ""      # 口味偏好
    history: str = ""    # 历史就餐记录摘要


class DishRestaurantInfo(BaseModel):
    """V133: AI点菜引擎·餐厅信息"""
    name: str = ""
    cuisine: str = ""
    city: str = ""
    signatureDishes: List[Any] = Field(default_factory=list)


class DishMenuItem(BaseModel):
    """V133: AI点菜引擎·菜单项"""
    name: str
    price: float = 0
    category: str = ""


class DishDinerProfile(BaseModel):
    """V133: AI点菜引擎·就餐人员"""
    name: str = ""
    age: int = 0
    taste: str = ""
    constitution: str = ""
    tags: List[str] = Field(default_factory=list)


class DishHistoryInfo(BaseModel):
    """V133: AI点菜引擎·历史偏好；V136: 增加点菜历史学习字段"""
    lastVisit: Dict[str, Any] = Field(default_factory=dict)
    ratings: Dict[str, Any] = Field(default_factory=dict)
    lastCombos: List[Any] = Field(default_factory=list)   # 同批就餐者上次点菜组合
    historyText: str = ""                                  # 历史点菜记录文本摘要（注入Prompt）


class RecommendDishesRequest(BaseModel):
    """V133: AI点菜引擎·点菜推荐入参"""
    restaurant: DishRestaurantInfo = Field(default_factory=DishRestaurantInfo)
    menu: List[DishMenuItem] = Field(default_factory=list)
    people: int = 0
    budget: float = 0
    scene: str = ""
    diners: List[DishDinerProfile] = Field(default_factory=list)
    allergens: List[str] = Field(default_factory=list)
    history: DishHistoryInfo = Field(default_factory=DishHistoryInfo)


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
def root(request: Request) -> RedirectResponse:
    # 根路径直接进入家肴记应用，避免浏览器裸显 JSON（健康检查见 /health）
    # V195: 保留查询参数（如 ?demo=铁岭之家 的演示模式入口）
    q = request.url.query
    return RedirectResponse(url="/app" + (("?" + q) if q else ""))


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
    timeout: float = 60.0,
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
            "timeout": timeout,
        })
        return result["content"].strip()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务调用失败：{exc}") from exc


class VisionRequest(BaseModel):
    user_prompt: str
    image_data_url: str
    system_prompt: str = ""
    max_tokens: int = Field(default=2000, ge=100, le=4096)
    task_type: Optional[str] = None  # V131: 功能标识（flyer_ocr…），写入用量统计


@app.post("/ai/vision", response_model=ChatResponse)
async def ai_vision(req: VisionRequest) -> ChatResponse:
    """V131: 视觉理解（图片输入）统一入口——Agnes 免费视觉模型优先，qwen-vl-plus 兜底。

    前端宣传单 OCR 等多模态任务由此走多服务商自动降级链，不再受单一服务商欠费影响。
    """
    content_parts: List[Dict[str, Any]] = []
    if req.image_data_url:
        content_parts.append({"type": "image_url", "image_url": {"url": req.image_data_url}})
    content_parts.append({"type": "text", "text": req.user_prompt})
    messages: List[Dict[str, Any]] = []
    if req.system_prompt:
        messages.append({"role": "system", "content": req.system_prompt})
    messages.append({"role": "user", "content": content_parts})
    task_type = req.task_type if (req.task_type and re.fullmatch(r"[a-z_]{1,24}", req.task_type)) else "vision"
    try:
        content, _provider = await route_vision(messages, max_tokens=req.max_tokens, task_type=task_type)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI 视觉服务调用失败：{exc}") from exc
    return ChatResponse(response=content.strip())
# ============================================


# ========== V135: 菜单截图/链接识别 ==========
MENU_CATEGORIES = ["荤菜", "素菜", "凉菜", "汤", "主食", "饮品", "其他"]
_MENU_CAT_ALIAS = {
    "荤": "荤菜", "荤菜": "荤菜", "肉": "荤菜", "热菜": "其他",
    "素": "素菜", "素菜": "素菜", "蔬": "素菜",
    "凉": "凉菜", "凉菜": "凉菜", "凉菜/素": "凉菜",
    "汤": "汤", "汤羹": "汤",
    "主食": "主食", "小吃": "其他",
    "饮品": "饮品", "饮料": "饮品", "酒水": "饮品",
    "其他": "其他",
}


class ParseMenuImageRequest(BaseModel):
    """V135: 菜单截图识别入参（前端压缩后的单张 base64 图）"""
    image: str = ""            # base64 图片（可带 data:image/...;base64, 前缀）
    restaurantName: str = ""   # 已知餐厅名（辅助识别）
    cuisine: str = ""          # 已知菜系


def _normalize_menu_parse(parsed: Dict[str, Any], req_name: str, req_cuisine: str) -> Dict[str, Any]:
    """归一化识别结果：餐厅信息用已知入参补齐、菜品去重、价格/置信度/分类归一。"""
    rest = parsed.get("restaurant") if isinstance(parsed.get("restaurant"), dict) else {}
    restaurant = {
        "name": (rest.get("name") or req_name or "").strip(),
        "cuisine": (rest.get("cuisine") or req_cuisine or "").strip(),
        "address": (rest.get("address") or "").strip(),
    }
    dishes, seen = [], set()
    for d in parsed.get("dishes") or []:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        if not name or name in seen:
            continue
        try:
            price = float(d.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        try:
            conf = float(d.get("confidence", 0.8))
        except (TypeError, ValueError):
            conf = 0.8
        if conf > 1:  # 兼容 0-100 置信度口径
            conf = conf / 100
        conf = max(0.0, min(1.0, conf))
        cat = str(d.get("category") or "").strip()
        cat = _MENU_CAT_ALIAS.get(cat, "其他")
        seen.add(name)
        dishes.append({
            "name": name,
            "price": round(price, 2),
            "category": cat,
            "confidence": round(conf, 2),
        })
    return {"restaurant": restaurant, "dishes": dishes}


@app.post("/parse-menu-image")
async def parse_menu_image(req: ParseMenuImageRequest) -> Dict[str, Any]:
    """V135: 菜单截图识别——多模态模型提取餐厅信息与菜品（名称/价格/分类/置信度）。"""
    image = req.image.strip()
    if image.startswith("data:"):
        image = image.split(",", 1)[-1]
    if not image:
        raise HTTPException(status_code=400, detail="缺少图片数据")
    try:
        base64.b64decode(image, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="图片数据格式无效")

    system_prompt = (
        "你是菜单截图识别专家。请识别以下菜单截图中的信息：\n"
        "1. 餐厅名称、菜系、地址\n"
        "2. 所有菜品名称、价格、描述\n"
        "3. 菜品分类（荤菜/素菜/凉菜/汤/主食，饮品归 饮品，无法判断归 其他）\n"
        "4. 每道菜给出识别置信度 confidence（0到1的小数，字迹模糊或需要推测的给低分）\n\n"
        "注意：\n"
        "- 价格只保留数字（单位元）\n"
        "- 每道菜单一分类，不要合并\n"
        "- 只提取图中真实存在的菜品，不要编造\n"
        "- 返回严格 JSON 格式，不要任何额外文字或 Markdown 包裹\n\n"
        'JSON 格式：{"restaurant":{"name":"餐厅名","cuisine":"菜系","address":"地址"},'
        '"dishes":[{"name":"菜名","price":38,"category":"荤菜","confidence":0.95}]}'
    )
    hint = ""
    if req.restaurantName or req.cuisine:
        hint = f"\n\n已知信息参考：餐厅名「{req.restaurantName}」菜系「{req.cuisine}」。图中未显示的信息留空字符串，不要编造地址。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image}},
            {"type": "text", "text": "请识别这张菜单截图，返回严格 JSON。" + hint},
        ]},
    ]
    try:
        content, _provider = await route_vision(messages, max_tokens=3000, task_type="parse_menu")
        parsed = extract_json_object(content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"菜单识别失败：{exc}") from exc
    return _normalize_menu_parse(parsed, req.restaurantName, req.cuisine)


class ParseMenuUrlRequest(BaseModel):
    """V135: 菜单链接解析入参（美团/点评等页面链接）"""
    url: str = ""
    restaurantName: str = ""
    cuisine: str = ""


@app.post("/parse-menu-url")
async def parse_menu_url(req: ParseMenuUrlRequest) -> Dict[str, Any]:
    """V135: 粘贴美团链接解析菜单——抓取页面文本交给文本模型提取菜品。

    页面需登录/动态渲染/反爬导致抓取不到有效菜单内容时，返回 422 提示改用截图识别。
    """
    url = (req.url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请输入有效的网页链接")
    headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"}
    text = ""
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:6000]
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"链接抓取失败（{type(exc).__name__}），建议使用截图识别")
    if len(text) < 50 or not re.search(r"(¥|\d+(\.\d+)?\s*元)", text):
        raise HTTPException(status_code=422, detail="该链接页面未能提取到菜单内容（可能需要登录或为动态页面），建议使用截图识别")

    system_prompt = (
        "你是菜单信息提取专家。从网页文本中提取菜品列表。\n"
        "分类归入：荤菜/素菜/凉菜/汤/主食/饮品/其他；每道菜给出置信度 confidence（0-1）。\n"
        "返回严格 JSON，不要任何额外文字：\n"
        '{"restaurant":{"name":"餐厅名","cuisine":"菜系","address":"地址"},'
        '"dishes":[{"name":"菜名","price":38,"category":"荤菜","confidence":0.9}]}'
    )
    try:
        content = await call_qwen(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    f"网页文本：\n{text}\n\n"
                    f"已知餐厅名：「{req.restaurantName}」菜系「{req.cuisine}」。"
                    "提取文本中出现的所有菜品及价格，返回严格 JSON。"
                )},
            ],
            json_mode=True,
            max_tokens=3000,
            task_type="parse_menu_url",
        )
        parsed = extract_json_object(content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"菜单解析失败：{exc}") from exc
    return _normalize_menu_parse(parsed, req.restaurantName, req.cuisine)
# ========== V135 菜单识别结束 ==========


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
    # V119: 功能标识白名单（只允许小写字母/下划线，防日志污染），缺省 plan
    task_type = (req.task_type or "plan").strip()
    if not re.fullmatch(r"[a-z_]{1,24}", task_type):
        task_type = "plan"
    # 前端已构建详细的五维上下文 system_prompt；mode 非默认时在其最前面追加模式侧重指令
    system_content = get_mode_directive(req.mode)
    # V143: 仅对食谱规划类任务注入食养堂上下文（近7天已选主题防重复 + 近3天未选候选池），
    # 绝不作用于聊天回复，避免重蹈 V139 无差别污染输出格式的覆辙
    if "plan" in task_type:
        foodhall_ctx = _foodhall_plan_context()
        if foodhall_ctx:
            system_content += foodhall_ctx + "\n\n"
        # V199: 订阅用户的食谱计划——追加专属 Prompt 指令（必须是 plan 类任务，聊天回复不污染）
        sub_plan = (req.subscriptionPlan or "").strip()
        if sub_plan and sub_plan in _SUBSCRIPTION_PROMPT_SEGMENTS:
            system_content += _SUBSCRIPTION_PROMPT_SEGMENTS[sub_plan] + "\n\n"
    system_content += req.system_prompt
    content = await call_qwen(
        [
            {"role": "system", "content": system_content},
            {"role": "user", "content": req.user_prompt},
        ],
        # V140: json_mode 改为请求级开关（req.json_mode）——V139 无条件强制 JSON 曾导致
        # 经 /plan 代理的聊天回复（如"今天吃什么？"）输出原始 JSON；
        # 仅前端周计划等明确要 JSON 的调用方传 true，其余任务回归提示词驱动
        json_mode=req.json_mode,
        max_tokens=req.max_tokens,
        task_type=task_type,
        timeout=150.0,  # V139: 7天完整计划正文可达 10k+ token，60s 会掐断长生成
    )
    return {"response": content}


@app.post("/recommend-restaurants")
async def recommend_restaurants(req: RecommendRestaurantsRequest) -> Dict[str, Any]:
    """V132: 外出就餐·选餐厅推荐，返回1-3家（名称/理由/人均/推荐菜）。

    入参为结构化字段，由后端统一组装提示词；json_mode 低温度保证稳定 JSON 输出。
    """
    system_prompt = (
        "你是家庭外出就餐顾问，根据就餐信息推荐合适的餐厅。"
        "必须只返回 JSON，不要任何额外文字或 Markdown 包裹。"
        'JSON 格式：{"restaurants":[{"name":"餐厅名","reason":"50字内推荐理由（结合场景/预算/口味）",'
        '"location":"大致位置（区/街道/商圈级别，如：海淀区中关村商圈；不确定则留空）",'
        '"avg_price":"人均约XX元","dishes":[{"name":"招牌菜","price":价格数字}]}]}。'
        "要求：1) 推荐1-3家，按匹配度排序；2) dishes 为该店3-5道招牌或推荐菜，price 为人均消费下的合理估值数字；"
        "3) 综合考虑预算、场景、菜系、口味偏好与历史就餐记录；"
        "4) location 只写到商圈/街道级别，禁止编造精确门牌地址与电话。"
    )
    user_lines = [
        f"就餐场景：{req.scene or '未指定'}",
        f"人数：{req.people or '未指定'}",
        f"人均预算：{req.budget or '不限'}（元/人）",
        f"菜系偏好：{req.cuisine or '不限'}",
        f"位置范围：{req.location or '不限'}",
        f"口味偏好：{req.taste or '不限'}",
    ]
    if req.history.strip():
        user_lines.append("历史就餐记录：\n" + req.history.strip()[:800])
    content = await call_qwen(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(user_lines)},
        ],
        json_mode=True,
        max_tokens=2000,
        task_type="dining_out",
    )
    parsed = extract_json_object(content)
    raw_list = None
    if isinstance(parsed, dict):
        raw_list = parsed.get("restaurants") or parsed.get("recommendations")
    result = []
    for item in (raw_list or [])[:3]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        dishes = []
        for d in (item.get("dishes") or []):
            if isinstance(d, dict) and d.get("name"):
                try:
                    price = float(d.get("price") or 0)
                except (TypeError, ValueError):
                    price = 0.0
                dishes.append({"name": str(d["name"]), "price": price})
        # V137: 地址冗余进 reason 文本——即使前端是旧缓存代码（无📍行渲染）也能看到地址
        _reason = str(item.get("reason") or "")
        _loc = str(item.get("location") or "")
        if _loc and _loc not in _reason:
            _reason = (_reason + " （📍" + _loc + "）").strip()
        result.append({
            "name": str(item["name"]),
            "reason": _reason,
            "location": _loc,
            "avg_price": str(item.get("avg_price") or item.get("avgPrice") or ""),
            "dishes": dishes,
        })
    return {"restaurants": result}


# ========== V140: 食养堂·AI 每日草稿生成 ==========
class FoodHallDraftRequest(BaseModel):
    date: str = ""
    city: str = ""
    solarTerm: str = ""
    weather: dict = Field(default_factory=dict)


class FoodHallAuditRequest(BaseModel):
    articleId: str = ""
    title: str = ""
    summary: str = ""
    content: str = ""
    tips: List[str] = Field(default_factory=list)


# 规格 Prompt：三篇主题分散 + 口语化 + 严格禁词（医疗/功效承诺类）
_FOODHALL_SYSTEM = (
    '请为家肴记"食养堂"栏目生成 3 篇候选内容。\n'
    "【日期】{date}\n"
    "【节气】{solar_term}\n"
    "\n"
    "生成要求：\n"
    '1. 3 篇内容主题尽量分散：1 篇挂靠节气或时令（category 用"节气食养"）；'
    '1 篇讲当季食材（category 用"当季食材"）；1 篇讲烹饪技巧或饮食文化（category 用"烹饪技巧"或"饮食文化"）\n'
    "2. 每篇：标题 15 字内、摘要 60 字内、正文 200-300 字、小贴士 2 条（每条 25 字内）\n"
    "3. 语言口语化，像和朋友聊天\n"
    '4. 主题必须全国通用，不得指定或暗示某座城市/地区，不得绑定某地天气（如"杭州降温""北京下雪"等都被禁止）；'
    '可结合近期全国性饮食话题（如"秋燥""进补"）\n'
    "\n"
    "严格禁止出现这些词：治疗、治愈、改善、降低、预防、功效、食疗、润肺、止咳、降三高、祛湿、排毒。"
    "禁止提及疾病名称，禁止承诺任何功效。\n"
    "\n"
    '返回严格 JSON：{{"articles":[{{"category":"...","title":"...","summary":"...","content":"...","tips":["...","..."]}}]}}，'
    "articles 必须恰好 3 个元素。"
)

# 验收禁词表（命中自动重试 1 次，仍命中则随响应返回 violations 供前端记 auditNote）
_FOODHALL_BANNED = ["治疗", "治愈", "改善", "降低", "预防", "功效", "食疗",
                    "润肺", "止咳", "降三高", "祛湿", "排毒"]

# V141 审核黑名单（发布前合规审核·error 级，本地匹配 0 token）
_FOODHALL_AUDIT_BANNED = ["治疗", "治愈", "根治", "改善", "降低", "预防", "抗癌", "排毒",
                          "降三高", "消炎", "杀菌", "缓解", "调理", "祛湿", "补血",
                          "润肺", "止咳", "明目", "安神", "养胃", "护肝"]
# 疾病名称（error 级）
_FOODHALL_DISEASES = ["高血压", "糖尿病", "癌症", "肿瘤", "肝炎", "肾炎", "胃炎", "肠炎",
                      "失眠", "感冒", "心脏病", "痛风", "贫血", "哮喘", "中风"]
# 功效承诺短语（error 级）
_FOODHALL_PHRASES = ["能治", "可改善", "有疗效", "包治", "药到病除", "保证治好"]
# 弱信号词：本地黑名单未命中但出现这些字眼时，触发 AI 语义二次确认（疑似风险）
_FOODHALL_WEAK = ["功效", "偏方", "秘方", "古方", "神效", "疗效", "炎", "咳", "肿",
                  "痛", "症", "菌", "毒", "药", "虚", "补气", "上火", "去火", "下火"]

# 发布前 AI 合规审核员 Prompt（词表硬校验之后的第二道关）
_FOODHALL_AUDIT_SYSTEM = (
    "你是食养科普内容的合规审核员。请审核以下内容能否在家肴记APP食养堂栏目发布。\n"
    "不通过标准（命中任一条即不通过）：\n"
    "1. 含治疗、治愈、改善、降低、预防、功效、食疗、润肺、止咳、降三高、祛湿、排毒等功效承诺类词汇\n"
    "2. 提及具体疾病名称，或暗示某种吃法对疾病有作用\n"
    "3. 夸大宣传或绝对化用语（如最有效、保证、根治）\n"
    "4. 推荐药物，或暗示某种食材可替代药物/治疗\n"
    "通过标准：日常食材科普、烹饪方法、时令饮食建议等安全内容。\n"
    '返回严格 JSON：{"passed": true或false, "reason": "不通过时给出具体原因（50字内），通过时为空字符串"}'
)


def _foodhall_banned_hits(text: str) -> List[str]:
    return [w for w in _FOODHALL_BANNED if w in (text or "")]


@app.post("/api/food-hall/generate-drafts")
async def food_hall_generate_drafts(req: FoodHallDraftRequest) -> Dict[str, Any]:
    """V140: AI 每日生成 3 篇食养堂草稿。

    入参 date/city/solarTerm/weather 由前端组装（城市/天气/节气均有现成数据源）。
    禁词命中自动重试 1 次；仍命中则在 violations 标注。前端负责把 drafts 写入
    localStorage foodHallArticles（status=draft）并记 foodHallDrafts 批次。
    V143: 主体抽到 _foodhall_generate_core，供 22:30 自动发布流水线复用。
    """
    date = (req.date or time.strftime("%Y-%m-%d"))[:10]
    weather = req.weather if isinstance(req.weather, dict) else {}
    drafts, violations, regenerated = await _foodhall_generate_core(
        date, req.city, req.solarTerm, weather
    )
    return {"ok": True, "date": date, "regenerated": regenerated,
            "violations": violations, "drafts": drafts}


async def _foodhall_generate_core(
    date: str, city: str, solar_term: str, weather: Optional[Dict[str, Any]]
) -> tuple:
    """V143: 食养堂 3 篇候选生成核心（端点与自动流水线共用）。

    返回 (drafts, violations, regenerated)；格式异常直接抛 502。
    V145: 选题全国通用——city/weather 入参保留以兼容旧调用，但不再注入 prompt。
    """
    system_prompt = _FOODHALL_SYSTEM.format(
        date=date, solar_term=solar_term or "无",
    )
    user_prompt = f"日期：{date}；节气：{solar_term or '无'}。请生成 3 篇食养堂候选内容，主题必须全国通用。"

    drafts: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    attempt = 0
    for attempt in range(2):  # 首轮 + 禁词命中重试 1 次
        content = await call_qwen(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            max_tokens=4096,
            task_type="foodhall_draft",
            timeout=120.0,
        )
        parsed = extract_json_object(content)
        raw_list = parsed.get("articles") if isinstance(parsed, dict) else None
        if not isinstance(raw_list, list) or not raw_list:
            raise HTTPException(status_code=502, detail="AI返回数据格式异常，缺少articles字段")
        drafts = []
        violations = []
        ts = int(time.time() * 1000)
        for i, item in enumerate(raw_list[:3]):
            if not isinstance(item, dict):
                continue
            blob = " ".join([str(item.get("title", "")), str(item.get("summary", "")),
                             str(item.get("content", "")), " ".join(str(t) for t in (item.get("tips") or []))])
            hits = _foodhall_banned_hits(blob)
            if hits:
                violations.append({"index": i, "words": hits})
            drafts.append({
                "id": f"fh_{ts}_{i}",
                "category": str(item.get("category") or "节气食养"),
                "title": str(item.get("title") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "content": str(item.get("content") or "").strip(),
                "tips": [str(t).strip() for t in (item.get("tips") or []) if str(t).strip()][:2],
            })
        if drafts and not violations:
            break  # 干净即返回；命中禁词则再生成一轮
    return drafts, violations, attempt > 0


@app.post("/api/food-hall/audit")
async def food_hall_audit(req: FoodHallAuditRequest) -> Dict[str, Any]:
    """V141: 食养堂发布前合规审核——本地关键词优先（0 token），弱信号才调 AI 二次确认。
    V143: 主体抽到 _foodhall_audit_core，自动发布流水线复用同一把尺子。
    """
    return await _foodhall_audit_core(req.title, req.summary, req.content, req.tips)


async def _foodhall_audit_core(
    title: str, summary: str, content: str, tips: List[str]
) -> Dict[str, Any]:
    """审核核心：1) 黑名单/疾病名/功效短语本地匹配（error 直接拒，0 token）；
    2) 长度检查（warning，不阻断）；3) 弱信号词 → AI 语义二次确认。
    """
    tips_text = " ".join(str(t) for t in (tips or []))
    blob = " ".join([title or "", summary or "", content or "", tips_text])
    issues: List[Dict[str, str]] = []

    # 1. 本地硬校验（error，0 token）
    for w in _FOODHALL_AUDIT_BANNED:
        if w in blob:
            issues.append({"level": "error", "message": f"含医疗词汇'{w}'"})
    for d in _FOODHALL_DISEASES:
        if d in blob:
            issues.append({"level": "error", "message": f"提及疾病名称'{d}'"})
    for p in _FOODHALL_PHRASES:
        if p in blob:
            issues.append({"level": "error", "message": f"功效承诺'{p}'"})
    if any(i["level"] == "error" for i in issues):
        return {"ok": True, "passed": False, "issues": issues, "suggestions": []}

    # 2. 长度检查（warning，不阻断）
    title_len = len((title or "").strip())
    content_len = len((content or "").strip())
    if title_len > 15:
        issues.append({"level": "warning", "message": f"标题 {title_len} 字，建议 15 字内"})
    if content_len < 150:
        issues.append({"level": "warning", "message": f"正文 {content_len} 字，建议 150-500 字"})
    elif content_len > 500:
        issues.append({"level": "warning", "message": f"正文 {content_len} 字，建议 150-500 字"})

    # 3. 弱信号 → AI 语义二次确认
    if any(k in blob for k in _FOODHALL_WEAK):
        try:
            ai_content = await call_qwen(
                [
                    {"role": "system", "content": _FOODHALL_AUDIT_SYSTEM},
                    {"role": "user", "content": f"【标题】{title or ''}\n【摘要】{summary or ''}\n【正文】{content or ''}\n【小贴士】{tips_text}"},
                ],
                json_mode=True,
                max_tokens=512,
                task_type="foodhall_audit",
                timeout=90.0,
            )
            parsed = extract_json_object(ai_content)
            if isinstance(parsed, dict) and not parsed.get("passed"):
                issues.append({"level": "error", "message": str(parsed.get("reason") or "AI审核未通过")})
        except Exception as exc:  # AI 层异常不阻塞发布（本地层已通过），降级放行
            log_ai_error("audit", "local-first", {"task_type": "foodhall_audit"}, exc)

    return {
        "ok": True,
        "passed": not any(i["level"] == "error" for i in issues),
        "issues": issues,
        "suggestions": [],
    }


# ========== V143: 食养堂自动发布流水线（22:30 生成→审核→选篇入库，次日首页直接展示） ==========
import secrets as _secrets
import sqlite3 as _sqlite3
from datetime import date as _date_cls, datetime as _dt_cls, timedelta as _td_cls

_FH_DATA_DIR = Path(__file__).parent / "data"
_FH_DB_PATH = _FH_DATA_DIR / "jiayaoji.db"

# 2026 节气日期表（与前端 SOLAR_TERMS_2026 一致）；相邻年份仅 ±1 天漂移，匹配容差 1 天
_FH_TERMS = [
    ("01-05", "小寒"), ("01-20", "大寒"), ("02-04", "立春"), ("02-19", "雨水"),
    ("03-05", "惊蛰"), ("03-20", "春分"), ("04-05", "清明"), ("04-20", "谷雨"),
    ("05-05", "立夏"), ("05-21", "小满"), ("06-05", "芒种"), ("06-21", "夏至"),
    ("07-07", "小暑"), ("07-23", "大暑"), ("08-07", "立秋"), ("08-23", "处暑"),
    ("09-07", "白露"), ("09-23", "秋分"), ("10-08", "寒露"), ("10-23", "霜降"),
    ("11-07", "立冬"), ("11-22", "小雪"), ("12-07", "大雪"), ("12-22", "冬至"),
]


def _fh_solar_term(date_str: str) -> str:
    """按节气表匹配 date 当天（±1 天容差，覆盖跨年日期漂移）；无命中返回空串。"""
    try:
        d = _dt_cls.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    for mmdd, name in _FH_TERMS:
        try:
            term_day = d.replace(month=int(mmdd[:2]), day=int(mmdd[3:]))
        except ValueError:
            continue
        if abs((d - term_day).days) <= 1:
            return name
    return ""


def _internal_token() -> str:
    """内部定时任务令牌：环境变量优先，否则首次自动生成落到 data/.internal_token（仅本机定时任务读取）。"""
    env = os.environ.get("JIAYAOJI_INTERNAL_TOKEN", "").strip()
    if env:
        return env
    token_file = _FH_DATA_DIR / ".internal_token"
    if token_file.exists():
        tok = token_file.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    _FH_DATA_DIR.mkdir(parents=True, exist_ok=True)
    tok = _secrets.token_hex(24)
    token_file.write_text(tok, encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    return tok


def _fh_db():
    """SQLite 连接（标准库零依赖），首次调用自动建表。"""
    _FH_DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(_FH_DB_PATH), timeout=10)
    conn.row_factory = _sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS food_hall_pub_days (
            publish_date   TEXT PRIMARY KEY,
            category       TEXT DEFAULT '',
            title          TEXT DEFAULT '',
            summary        TEXT DEFAULT '',
            content        TEXT DEFAULT '',
            tips           TEXT DEFAULT '[]',
            related        TEXT DEFAULT '[]',
            drafts         TEXT DEFAULT '[]',
            chosen_id      TEXT DEFAULT '',
            auto_published INTEGER DEFAULT 1,
            review_status  TEXT DEFAULT 'pending',
            run_note       TEXT DEFAULT '',
            created_at     TEXT DEFAULT '',
            updated_at     TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS food_hall_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publish_date TEXT DEFAULT '',
            status       TEXT DEFAULT '',
            note         TEXT DEFAULT '',
            payload      TEXT DEFAULT '[]',
            created_at   TEXT DEFAULT ''
        )
        """
    )
    conn.commit()
    return conn


@app.on_event("startup")
def _fh_startup_init() -> None:
    """V143: 启动即建表 + 生成内部令牌文件（供本机 22:30 定时任务读取，避免鉴权鸡生蛋问题）。"""
    try:
        _internal_token()
        _fh_db().close()
    except Exception as exc:
        print(f"[foodhall] 启动初始化失败: {exc}")


def _fh_record_run(pub_date: str, status: str, note: str, audited: List[Dict[str, Any]]) -> None:
    """流水线审计日志（含全部候选与不通过原因），供超管排查使用。"""
    try:
        with _fh_db() as conn:
            conn.execute(
                "INSERT INTO food_hall_runs (publish_date, status, note, payload, created_at) "
                "VALUES (?,?,?,?,?)",
                (pub_date, status, note[:500],
                 json.dumps(audited, ensure_ascii=False)[:20000],
                 _dt_cls.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
    except Exception as exc:
        print(f"[foodhall] run 日志写入失败: {exc}")


def _fh_row_to_article(row) -> Dict[str, Any]:
    """DB 行 → 前端 tip 结构（与 foodHallArticles 卡片渲染字段对齐）。"""
    try:
        tips = json.loads(row["tips"] or "[]")
    except json.JSONDecodeError:
        tips = []
    return {
        "id": f"autotip_{(row['publish_date'] or '').replace('-', '')}",
        "date": row["publish_date"],
        "category": row["category"] or "节气食养",
        "title": row["title"] or "",
        "summary": row["summary"] or "",
        "content": row["content"] or "",
        "tips": tips if isinstance(tips, list) else [],
        "relatedIngredients": [],
        "autoPublished": bool(row["auto_published"]),
        "reviewStatus": row["review_status"] or "pending",
    }


def _fh_get_published_row(date_str: str):
    """取某日已发布行（不存在或空内容返回 None）。"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str or ""):
        return None
    try:
        with _fh_db() as conn:
            return conn.execute(
                "SELECT * FROM food_hall_pub_days WHERE publish_date=? "
                "AND title<>'' AND COALESCE(review_status,'')='confirmed'",
                (date_str,),
            ).fetchone()
    except Exception as exc:
        print(f"[foodhall] 读取已发布内容失败: {exc}")
        return None


def _fh_pick_score(article: Dict[str, Any], recent_cats: List[str], index: int) -> float:
    """选篇打分：主题不与近 3 天重复 > 时效性（节气/天气类）> 正文篇幅适中 > 双小贴士。
    同分时靠 index 微差保持生成顺序（第 1 篇通常挂靠节气，时效性最强）。"""
    score = 1.0 - index * 0.01
    if article.get("category") not in recent_cats:
        score += 2.0
    if article.get("category") in ("节气食养", "天气食养"):
        score += 1.5
    content_len = len(article.get("content") or "")
    if 180 <= content_len <= 320:
        score += 1.0
    elif 150 <= content_len <= 420:
        score += 0.5
    if len(article.get("tips") or []) == 2:
        score += 0.5
    # V145: 城市名/天气词硬扣分（夜间备稿不再注入城市/天气，但 AI 仍可能自塞，需硬扣分兜底）
    blob = (str(article.get("title", "")) + " " + str(article.get("summary", "")) + " "
            + str(article.get("content", "")))
    if any(w in blob for w in _FH_CITY_HINTS):
        score -= 5.0
    if any(w in blob for w in _FH_WEATHER_HINTS):
        score -= 3.0
    return score


# V145: 城市词与天气词硬扣分兜底表
_FH_CITY_HINTS = ["北京", "上海", "杭州", "广州", "深圳", "成都", "重庆", "西安",
                  "武汉", "南京", "天津", "苏州", "长沙", "青岛", "大连", "宁波",
                  "厦门", "福州", "哈尔滨", "沈阳", "长春", "昆明", "郑州", "济南"]
_FH_WEATHER_HINTS = ["降温", "升温", "暴雨", "大雪", "暴雪", "寒潮", "高温", "酷暑",
                     "梅雨", "台风", "沙尘", "冰雹", "雷雨", "连阴雨", "骤降", "骤升"]


class FoodHallAutoPublishRequest(BaseModel):
    date: str = ""               # 展示日期，缺省=明天
    city: str = ""
    solarTerm: str = ""
    weather: Optional[Dict[str, Any]] = None
    force: bool = False          # 强制覆盖（即使超管已确认）


@app.post("/api/food-hall/auto-publish", include_in_schema=False)
async def food_hall_auto_publish(
    req: FoodHallAutoPublishRequest,
    x_internal_token: str = Header(default=""),
) -> Dict[str, Any]:
    """V143: 定时流水线（22:30 由本机 cron/计划任务调用，仅认 X-Internal-Token）。

    生成 3 篇 → 逐篇合规审核 → 打分选 1 篇 → 写入 food_hall_pub_days（展示日期=明天）。
    幂等：超管已确认(review_status=confirmed)的日期默认跳过；全部审核不通过则不发布，
    仅写 food_hall_runs 审计记录，等超管当天手工处理。
    """
    if not x_internal_token or not _secrets.compare_digest(x_internal_token, _internal_token()):
        raise HTTPException(status_code=403, detail="禁止访问：内部令牌无效")

    pub_date = (req.date or "").strip()
    if not pub_date:
        pub_date = (_date_cls.today() + _td_cls(days=1)).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pub_date):
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")

    now_iso = _dt_cls.now().strftime("%Y-%m-%d %H:%M:%S")

    # 已存在且已被超管确认 → 不覆盖人工干预
    try:
        with _fh_db() as conn:
            existed = conn.execute(
                "SELECT review_status FROM food_hall_pub_days WHERE publish_date=?",
                (pub_date,),
            ).fetchone()
        if existed and existed["review_status"] == "confirmed" and not req.force:
            return {"ok": True, "published": False, "skipped": True,
                    "reason": "超管已确认，跳过自动覆盖", "date": pub_date}
    except Exception as exc:
        print(f"[foodhall] 预检失败: {exc}")

    term = (req.solarTerm or "").strip() or _fh_solar_term(pub_date)
    try:
        drafts, violations, regenerated = await _foodhall_generate_core(
            pub_date, req.city, term, req.weather if isinstance(req.weather, dict) else {}
        )
    except HTTPException as exc:
        _fh_record_run(pub_date, "error", f"生成失败: {exc.detail}", [])
        raise

    # 逐篇审核（与超管发布前同一把尺子）
    audited: List[Dict[str, Any]] = []
    for d in drafts:
        audit = await _foodhall_audit_core(
            d.get("title", ""), d.get("summary", ""), d.get("content", ""), d.get("tips", [])
        )
        audited.append({**d, "audit": audit})

    passed = [a for a in audited if a.get("audit", {}).get("passed")]
    note = f"生成{len(drafts)}篇，通过{len(passed)}篇" + ("，触发禁词重试" if regenerated else "")
    if violations:
        note += f"，禁词提示{len(violations)}处"

    if not passed:
        _fh_record_run(pub_date, "no_pass", note + "；无合格候选，不自动发布", audited)
        return {"ok": True, "published": False, "passed": 0, "total": len(drafts),
                "date": pub_date, "solarTerm": term, "note": note}

    # 近 3 天已选类别，避免主题/品类连撞
    recent_cats: List[str] = []
    try:
        with _fh_db() as conn:
            rows = conn.execute(
                "SELECT category FROM food_hall_pub_days WHERE title<>'' AND publish_date < ? "
                "ORDER BY publish_date DESC LIMIT 3",
                (pub_date,),
            ).fetchall()
            recent_cats = [r["category"] for r in rows if r["category"]]
    except Exception:
        recent_cats = []

    chosen_idx = max(
        range(len(audited)),
        key=lambda i: _fh_pick_score(audited[i], recent_cats, i)
        if audited[i].get("audit", {}).get("passed") else -999,
    )
    chosen = dict(audited[chosen_idx])
    chosen_score = round(_fh_pick_score(chosen, recent_cats, chosen_idx), 2)

    # upsert（重跑同一日期覆盖旧的自动结果）
    try:
        with _fh_db() as conn:
            conn.execute(
                """
                INSERT INTO food_hall_pub_days
                  (publish_date, category, title, summary, content, tips, related, drafts,
                   chosen_id, auto_published, review_status, run_note, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,1,'pending',?,?,?)
                ON CONFLICT(publish_date) DO UPDATE SET
                  category=excluded.category, title=excluded.title, summary=excluded.summary,
                  content=excluded.content, tips=excluded.tips, related=excluded.related,
                  drafts=excluded.drafts, chosen_id=excluded.chosen_id, auto_published=1,
                  review_status='pending', run_note=excluded.run_note, updated_at=excluded.updated_at
                """,
                (pub_date, chosen.get("category", ""), chosen.get("title", ""),
                 chosen.get("summary", ""), chosen.get("content", ""),
                 json.dumps(chosen.get("tips", []), ensure_ascii=False), "[]",
                 json.dumps(audited, ensure_ascii=False), chosen.get("id", ""),
                 note, now_iso, now_iso),
            )
    except Exception as exc:
        _fh_record_run(pub_date, "error", f"入库失败: {exc}", audited)
        raise HTTPException(status_code=500, detail=f"自动发布入库失败: {exc}")

    _fh_record_run(pub_date, "published", f"{note}；选中《{chosen.get('title')}》", audited)
    return {
        "ok": True, "published": True, "date": pub_date, "solarTerm": term,
        "chosen": {"id": chosen.get("id"), "category": chosen.get("category"),
                   "title": chosen.get("title"), "score": chosen_score},
        "passed": len(passed), "total": len(drafts), "note": note,
    }


@app.get("/api/food-hall/published", include_in_schema=False)
async def food_hall_get_published(date: str = "") -> Dict[str, Any]:
    """首页读取某日服务端预选内容（超管手动发布在浏览器 localStorage，本接口是自动流水线渠道）。"""
    date = (date or time.strftime("%Y-%m-%d"))[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}
    row = _fh_get_published_row(date)
    if not row:
        return {"ok": True, "published": False, "date": date}
    return {"ok": True, "published": True, "date": date, "article": _fh_row_to_article(row)}


@app.get("/api/food-hall/published-list", include_in_schema=False)
async def food_hall_published_list(days: int = 120) -> Dict[str, Any]:
    """V144: 公共文库·食养堂归档——近 N 天已发布（仅超管 confirmed）。

    只返回 publish_date <= 今天 的记录，避免提前泄露次日预生成稿。
    """
    days = max(1, min(int(days or 120), 366))
    today_s = _date_cls.today().strftime("%Y-%m-%d")
    start_s = (_date_cls.today() - _td_cls(days=days - 1)).strftime("%Y-%m-%d")
    try:
        with _fh_db() as conn:
            rows = conn.execute(
                "SELECT * FROM food_hall_pub_days WHERE title<>'' "
                "AND COALESCE(review_status,'')='confirmed' "
                "AND publish_date BETWEEN ? AND ? ORDER BY publish_date DESC",
                (start_s, today_s),
            ).fetchall()
    except Exception as exc:
        print(f"[foodhall] 归档列表读取失败: {exc}")
        return {"ok": False, "error": "归档读取失败"}
    return {"ok": True, "articles": [_fh_row_to_article(r) for r in rows]}


class FoodHallReviewRequest(BaseModel):
    date: str
    action: str = ""  # confirm 确认保留 | withdraw 撤回换篇


@app.post("/api/food-hall/review", include_in_schema=False)
async def food_hall_review(req: FoodHallReviewRequest) -> Dict[str, Any]:
    """V143: 超管次日复核 AI 预选篇——confirm 确认保留 / withdraw 撤回（首页立即不再展示）。

    鉴权沿用前端超管口令门（与全站管理操作同一信任模型），每次操作写 food_hall_runs 审计。
    """
    pub_date = (req.date or "").strip()
    action = (req.action or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pub_date):
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
    if action not in ("confirm", "withdraw"):
        raise HTTPException(status_code=400, detail="action 仅支持 confirm/withdraw")
    new_status = "confirmed" if action == "confirm" else "withdrawn"
    now_iso = _dt_cls.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _fh_db() as conn:
            cur = conn.execute(
                "UPDATE food_hall_pub_days SET review_status=?, updated_at=? "
                "WHERE publish_date=? AND title<>''",
                (new_status, now_iso, pub_date),
            )
            if cur.rowcount == 0:
                return {"ok": False, "error": "该日期没有可复核的自动发布内容"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"复核操作失败: {exc}")
    _fh_record_run(pub_date, f"review_{new_status}", f"超管执行 {action}", [])
    return {"ok": True, "date": pub_date, "reviewStatus": new_status}


@app.get("/api/food-hall/pending-list", include_in_schema=False)
async def food_hall_pending_list() -> Dict[str, Any]:
    """V145: 超管审稿——返回所有 review_status='pending' 的自动备稿（含今天与明天）。

    供超管管理页"AI 待审" tab 展示。无鉴权（沿用前端超管口令门信任模型）。
    """
    try:
        with _fh_db() as conn:
            rows = conn.execute(
                "SELECT * FROM food_hall_pub_days WHERE title<>'' "
                "AND COALESCE(review_status,'')='pending' "
                "ORDER BY publish_date DESC",
            ).fetchall()
    except Exception as exc:
        print(f"[foodhall] 待审列表读取失败: {exc}")
        return {"ok": False, "error": "待审列表读取失败"}
    return {"ok": True, "articles": [_fh_row_to_article(r) for r in rows]}


class FoodHallAutoConfirmRequest(BaseModel):
    date: str = ""  # 缺省=今天


@app.post("/api/food-hall/auto-confirm", include_in_schema=False)
async def food_hall_auto_confirm(
    req: FoodHallAutoConfirmRequest,
    x_internal_token: str = Header(default=""),
) -> Dict[str, Any]:
    """V145: 保底自动确认——每天 6:00 由 systemd timer 调用。

    把当天 publish_date 还是 pending 的记录转为 confirmed，保证首页 6:00 后一定有内容。
    幂等：已是 confirmed/withdrawn 的不动。
    """
    if not x_internal_token or not _secrets.compare_digest(x_internal_token, _internal_token()):
        raise HTTPException(status_code=403, detail="禁止访问：内部令牌无效")
    pub_date = (req.date or "").strip()
    if not pub_date:
        pub_date = _date_cls.today().strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pub_date):
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
    now_iso = _dt_cls.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _fh_db() as conn:
            cur = conn.execute(
                "UPDATE food_hall_pub_days SET review_status='confirmed', updated_at=? "
                "WHERE publish_date=? AND title<>'' "
                "AND COALESCE(review_status,'')='pending'",
                (now_iso, pub_date),
            )
            affected = cur.rowcount
    except Exception as exc:
        _fh_record_run(pub_date, "error", f"保底确认失败: {exc}", [])
        raise HTTPException(status_code=500, detail=f"保底确认失败: {exc}")
    note = f"保底自动确认 {affected} 篇" if affected else "无可确认记录"
    _fh_record_run(pub_date, "auto_confirmed", note, [])
    return {"ok": True, "date": pub_date, "confirmed": affected}


def _foodhall_plan_context() -> str:
    """V143: 给 /api/plan 注入食养堂上下文——
    近 7 天已选主题（避免重复）+ 近 3 天未选用但审核通过的候选标题（3/7 天规划可参考）。
    审核不通过的内容永不进入规划上下文。无数据返回空串。"""
    try:
        today = _date_cls.today()
        d7 = (today - _td_cls(days=6)).strftime("%Y-%m-%d")
        d3 = (today - _td_cls(days=2)).strftime("%Y-%m-%d")
        today_s = today.strftime("%Y-%m-%d")
        with _fh_db() as conn:
            rows = conn.execute(
                "SELECT publish_date, title, category, drafts, chosen_id FROM food_hall_pub_days "
                "WHERE title<>'' AND publish_date BETWEEN ? AND ? ORDER BY publish_date",
                (d7, today_s),
            ).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    selected = [f"{r['publish_date'][5:]} {r['category']}《{r['title']}》" for r in rows]
    pool: List[str] = []
    for r in rows:
        if r["publish_date"] < d3:
            continue
        try:
            drafts = json.loads(r["drafts"] or "[]")
        except json.JSONDecodeError:
            continue
        for d in drafts:
            if not isinstance(d, dict) or d.get("id") == r["chosen_id"]:
                continue
            if not (d.get("audit") or {}).get("passed"):
                continue  # 审核不通过篇不喂规划
            t = (d.get("title") or "").strip()
            if t:
                pool.append(f"{d.get('category') or '食养'}《{t}》")
    lines: List[str] = []
    if selected:
        lines.append("【本周食养堂已选主题】" + "、".join(selected)
                     + "——规划菜谱时请避免重复这些主题与主打食材。")
    if pool:
        seen, uniq = set(), []
        for p in pool:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        lines.append("【食养堂未用候选主题】" + "、".join(uniq[:8])
                     + "——这些是近 3 天已生成但未选用的合规选题，3 天/7 天食谱规划可优先参考。")
    return "\n".join(lines)


# ========== AI 点菜引擎（V133） ==========
# 低评分阈值：历史评分 ≤ 2（5分制）视为"评分低"，本地预过滤
_DISH_LOW_RATING = 2.0

# 场景英文标识 → 中文（未识别时原样透传）
DISH_SCENE_LABELS = {
    "family_dinner": "家庭聚餐",
    "friends_party": "朋友聚会",
    "business": "商务宴请",
    "date": "情侣约会",
    "birthday": "生日宴",
    "daily": "日常简餐",
}


def filter_menu_by_rules(menu: List[DishMenuItem], allergens: List[str], ratings: Dict[str, Any]):
    """V133: AI点菜引擎·本地规则预过滤。

    依次执行：1) 过滤菜名含过敏原/忌口关键词的菜；2) 过滤历史评分低的菜。
    返回 (保留菜单, 过滤明细列表)。
    """
    blocked = [str(a).strip() for a in (allergens or []) if str(a).strip()]
    low_rated = set()
    for dish, score in (ratings or {}).items():
        try:
            if float(score) <= _DISH_LOW_RATING:
                low_rated.add(str(dish).strip())
        except (TypeError, ValueError):
            continue
    kept, removed = [], []
    for item in menu:
        name = (item.name or "").strip()
        hit = next((k for k in blocked if k and k in name), None)
        if hit:
            removed.append({"name": name, "reason": f"含过敏原/忌口「{hit}」"})
            continue
        if name in low_rated:
            removed.append({"name": name, "reason": "上次点过且评分低"})
            continue
        kept.append(item)
    return kept, removed


# ========== V147: 个人饮食偏好·语义提取层（AI 提炼评论/行为/档案 → 结构化偏好） ==========

class ExtractPreferenceRequest(BaseModel):
    comments: List[str] = Field(default_factory=list)   # 用户近期评论（食记 overallComment/userComment）
    behavior: Dict[str, Any] = Field(default_factory=dict)  # 前端 behaviorProfile 行为统计
    familyBrief: str = ""                               # 压缩后的家庭档案文本


# 偏好提炼禁词：复用食养堂审核词表（医疗词 + 疾病名 + 功效短语），输出前本地硬过滤兜底
# 注意："控糖/控油/控盐"是用户行为偏好，允许保留；"降糖/降压"等功效承诺才禁
_PREF_BANNED_WORDS = list(dict.fromkeys(
    _FOODHALL_AUDIT_BANNED + _FOODHALL_DISEASES + _FOODHALL_PHRASES
    + ["功效", "食疗", "药效", "药用", "降糖", "降脂", "降压",
       "降血糖", "降血脂", "降血压", "血糖高", "血糖偏高"]
))

_PREF_EXTRACT_SYSTEM = (
    "你是家庭饮食偏好分析师。请从用户反馈里提炼饮食偏好。\n"
    "输出要求：\n"
    "1. likes：用户喜欢的食材、口味、烹饪方式（短语数组）\n"
    "2. dislikes：用户明确不喜欢的食材、口味、做法（短语数组）\n"
    "3. concerns：用户的饮食关注点，如少油、控糖、预算、省时、儿童爱吃等（短语数组）\n"
    "4. familyNotes：家人的特殊偏好，键为家人称呼、值为偏好描述（对象）\n"
    "规则：\n"
    "- 只基于用户真实表达，不要推测、不要补充用户没说过的内容\n"
    "- 禁止医疗词汇（治疗、功效、改善、降低、预防等）\n"
    "- 禁止提及任何疾病名称\n"
    "- 没有依据的字段返回空数组/空对象\n"
    '返回严格 JSON：{"likes": [], "dislikes": [], "concerns": [], "familyNotes": {}}'
)


def _pref_find_banned(text: str) -> List[str]:
    """返回字符串中命中的违禁词列表。"""
    return [w for w in _PREF_BANNED_WORDS if w in text]


def _pref_coerce_str_list(raw: Any) -> List[str]:
    """把 AI 返回的数组归一化为去重、去空的短语字符串数组（元素为对象时取 name/desc/text）。"""
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if isinstance(item, str):
            s = item.strip()
        elif isinstance(item, dict):
            s = str(item.get("name") or item.get("desc") or item.get("text") or "").strip()
        else:
            s = ""
        if s and s not in out:
            out.append(s[:50])  # 单条限长，避免 AI 写长句
    return out[:20]


def _pref_coerce_notes(raw: Any) -> Dict[str, str]:
    """归一化 familyNotes 为 {家人称呼: 偏好描述}；兼容 [{member,note}] 数组形态。"""
    notes: Dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            key = str(k or "").strip()[:20]
            val = str(v or "").strip()[:100]
            if key and val:
                notes[key] = val
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                key = str(item.get("member") or item.get("name") or "").strip()[:20]
                val = str(item.get("note") or item.get("preference") or item.get("desc") or "").strip()[:100]
                if key and val:
                    notes[key] = val
    return notes


def _pref_sanitize(result: Dict[str, Any]) -> Dict[str, Any]:
    """本地硬过滤：丢弃任何含医疗词/疾病名的列表项与 familyNotes 条目（无医疗词汇兜底）。"""
    likes = [s for s in result.get("likes", []) if not _pref_find_banned(s)]
    dislikes = [s for s in result.get("dislikes", []) if not _pref_find_banned(s)]
    concerns = [s for s in result.get("concerns", []) if not _pref_find_banned(s)]
    notes = {k: v for k, v in result.get("familyNotes", {}).items()
             if not _pref_find_banned(k) and not _pref_find_banned(v)}
    return {"likes": likes, "dislikes": dislikes, "concerns": concerns, "familyNotes": notes}


def _pref_normalize(parsed: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "likes": _pref_coerce_str_list(parsed.get("likes")),
        "dislikes": _pref_coerce_str_list(parsed.get("dislikes")),
        "concerns": _pref_coerce_str_list(parsed.get("concerns")),
        "familyNotes": _pref_coerce_notes(parsed.get("familyNotes")),
    }


def _pref_has_banned(result: Dict[str, Any]) -> bool:
    if any(_pref_find_banned(s) for s in result["likes"]):
        return True
    if any(_pref_find_banned(s) for s in result["dislikes"]):
        return True
    if any(_pref_find_banned(s) for s in result["concerns"]):
        return True
    for k, v in result["familyNotes"].items():
        if _pref_find_banned(k) or _pref_find_banned(v):
            return True
    return False


@app.post("/api/extract-preference")
async def extract_preference(req: ExtractPreferenceRequest) -> Dict[str, Any]:
    """V147: 从用户评论+行为统计+家庭档案中 AI 语义提炼饮食偏好（7 天缓存由前端控制）。

    安全：prompt 禁止医疗词/疾病名 → AI 返回后本地词表扫描，命中则带警告重试 1 次 →
    仍命中的条目在本地直接丢弃（_pref_sanitize），保证输出无医疗词汇。
    """
    comments = [str(c).strip() for c in (req.comments or []) if str(c or "").strip()]
    behavior = req.behavior if isinstance(req.behavior, dict) else {}
    family_brief = (req.familyBrief or "").strip()
    if not comments and not behavior:
        raise HTTPException(status_code=400, detail="暂无可分析的评论或行为数据")

    comments_text = "\n".join(f"- {c[:200]}" for c in comments[-60:]) or "（暂无评论）"
    try:
        behavior_text = json.dumps(behavior, ensure_ascii=False)[:3000] or "{}"
    except (TypeError, ValueError):
        behavior_text = "{}"

    prompt = (
        "你是家庭饮食偏好分析师。请从以下用户反馈里提炼偏好：\n\n"
        f"【用户近期评论】\n{comments_text}\n\n"
        f"【已有行为数据】\n{behavior_text}\n\n"
        f"【家庭档案】\n{family_brief or '普通家庭'}\n\n"
        "请提炼：\n"
        "1. 用户喜欢什么（食材、口味、烹饪方式）\n"
        "2. 用户不喜欢什么\n"
        "3. 用户有什么饮食关注\n"
        "4. 家人的特殊偏好\n\n"
        "要求：\n"
        "- 只基于用户真实表达，不要推测\n"
        "- 禁止医疗词汇（治疗、功效、改善、降低）\n"
        "- 禁止提及疾病名称\n"
        "- 返回严格 JSON"
    )

    async def _ask(extra_warning: str = "") -> Dict[str, Any]:
        user_content = prompt + extra_warning
        ai_text = await call_qwen(
            [
                {"role": "system", "content": _PREF_EXTRACT_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            json_mode=True,
            max_tokens=1024,
            task_type="preference_extract",
            timeout=90.0,
        )
        parsed = extract_json_object(ai_text)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=502, detail="AI 返回结构不是 JSON 对象")
        return _pref_normalize(parsed)

    try:
        result = await _ask()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"偏好提炼失败：{exc}") from exc

    # 命中医疗词 → 带明确警告重试 1 次
    if _pref_has_banned(result):
        try:
            result = await _ask(
                "\n\n【重要纠正】你上一次的回答中出现了医疗词汇或疾病名称，这是严格禁止的。"
                "请重新提炼，确保 likes/dislikes/concerns/familyNotes 中不含任何医疗、功效、疾病相关表述。"
            )
        except HTTPException:
            raise
        except Exception as exc:
            print(f"[extract-preference] 重试失败，沿用首条结果并本地净化: {exc}")

    # 本地硬过滤兜底：仍含违禁词的条目直接丢弃
    return _pref_sanitize(result)


@app.post("/recommend-dishes")
async def recommend_dishes(req: RecommendDishesRequest) -> Dict[str, Any]:
    """V133: AI 点菜引擎——按人数/预算/口味/忌口推荐菜品组合。

    流程：本地规则预过滤（过敏原/忌口/低评分）→ AI 按 12 条点菜规则推荐 →
    归一化（只保留菜单内菜品、二次过敏原校验、重算估算总价与预算校验）。
    """
    # ---- 规则引擎：本地预过滤 ----
    kept, removed = filter_menu_by_rules(req.menu, req.allergens, req.history.ratings)
    for r in removed:
        print(f"[recommend-dishes] 规则过滤: {r['name']} ({r['reason']})")

    empty_combination = {
        "totalDishes": 0, "coldDishes": 0, "hotDishes": 0,
        "meatVegRatio": "-", "estimatedTotal": 0, "withinBudget": True,
    }
    if not kept:
        tips = ["菜单为空，无法推荐"] if not req.menu else [
            "菜单中所有菜品均被过敏原/忌口或低评分规则过滤，无法推荐",
        ]
        return {"recommendedDishes": [], "combination": empty_combination, "tips": tips, "filteredOut": removed}

    # ---- 组装 Prompt（数据块 + 点菜规则模板） ----
    rest = req.restaurant
    sig = "、".join(str(s) for s in (rest.signatureDishes or [])[:8]) or "无"
    rest_line = (
        f"{rest.name or '未指定'}（菜系：{rest.cuisine or '未指定'}；城市：{rest.city or '未指定'}）；"
        f"招牌菜：{sig}"
    )
    menu_lines = "\n".join(
        f"{i}. {it.name}（{it.category or '未分类'}，{it.price}元）"
        for i, it in enumerate(kept, 1)
    )
    if req.diners:
        diner_lines = "\n".join(
            f"- {d.name or '成员'}：{d.age if d.age else '年龄未知'}岁，口味：{d.taste or '不限'}，"
            f"体质：{d.constitution or '不限'}" + (f"，标签：{'、'.join(d.tags)}" if d.tags else "")
            for d in req.diners
        )
    else:
        diner_lines = "未提供"
    rating_str = "；".join(f"{k}：{v}分" for k, v in list((req.history.ratings or {}).items())[:20]) or "无"
    last_visit = json.dumps(req.history.lastVisit, ensure_ascii=False)[:300] if req.history.lastVisit else "无"
    # V136: 点菜历史学习——注入历史记录文本 + 同批人上次组合
    history_line = f"【历史偏好】上次到访：{last_visit}；历史评分：{rating_str}"
    if req.history.historyText.strip():
        history_line += (
            f"\n用户在这家餐厅的历史点菜记录：{req.history.historyText.strip()[:500]}"
            "\n请参考历史偏好，避开上次评分低的菜。"
        )
    if req.history.lastCombos:
        combos_str = json.dumps(req.history.lastCombos, ensure_ascii=False)[:400]
        history_line += f"\n同批就餐者上次点菜组合（可参考）：{combos_str}"
    scene_label = DISH_SCENE_LABELS.get((req.scene or "").strip(), req.scene or "未指定")
    allergen_str = "、".join(str(a) for a in req.allergens) if req.allergens else "无"

    system_prompt = (
        "你是专业点菜顾问。请根据用户提供的就餐信息推荐菜品。\n\n"
        "### 点菜规则\n"
        "1. 菜品数量 = 人数 + 1 至 2\n"
        "2. 荤素比例 3:7 或 4:6\n"
        "3. 凉菜 1-2 道，热菜 4-6 道，汤 1 道\n"
        "4. 口味平衡：至少 1 道清淡、1 道浓郁\n"
        "5. 价格分配：1-2 道贵菜 + 3-4 道中等价\n"
        "6. 主料不重复\n"
        "7. 优先招牌菜\n"
        "8. 优先时令菜\n"
        "9. 避开过敏原和忌口\n"
        "10. 老人优先软烂易嚼\n"
        "11. 孩子优先清淡易消化\n"
        "12. 只从菜单中推荐，不推荐菜单外的菜\n"
        "13. 参考历史偏好：这家餐厅历史评分高（≥4分）的菜优先推荐，历史评分低（≤2分）的菜必须避开\n\n"
        "### 输出要求\n"
        "- 每道菜说明推荐理由\n"
        "- 标注菜品分类（荤/素/凉/汤）\n"
        "- 估算总价\n"
        "- 给出上菜顺序建议\n"
        "- 返回严格 JSON 格式，不要任何额外文字或 Markdown 包裹\n\n"
        'JSON 格式：{"recommendedDishes":[{"name":"菜名","category":"荤/素/凉/汤","reason":"30字内推荐理由","price":价格数字,"portion":"整份或半份"}],'
        '"combination":{"totalDishes":总道数,"coldDishes":凉菜数,"hotDishes":热菜数,"meatVegRatio":"荤素比如4:6","estimatedTotal":估算总价数字,"withinBudget":true或false},'
        '"tips":["上菜顺序建议","其他点菜提示"]}'
    )
    user_content = (
        f"【餐厅】{rest_line}\n"
        f"【菜单】\n{menu_lines}\n"
        f"【就餐人数】{req.people or '未指定'}\n"
        f"【预算】{req.budget if req.budget else '不限'}元\n"
        f"【场景】{scene_label}\n"
        f"【就餐人员】\n{diner_lines}\n"
        f"【忌口与过敏】{allergen_str}\n"
        f"{history_line}"
    )

    content = await call_qwen(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        json_mode=True,
        max_tokens=2500,
        task_type="recommend_dishes",
    )
    parsed = extract_json_object(content)

    # ---- 归一化：只保留（过滤后）菜单内菜品 + 二次过敏原校验 + 数量上限 ----
    raw_list = parsed.get("recommendedDishes") or parsed.get("recommendations") or []
    valid_names = {(it.name or "").strip() for it in kept}
    blocked = [str(a).strip() for a in (req.allergens or []) if str(a).strip()]
    limit = (req.people + 2) if req.people and req.people > 0 else len(raw_list)

    result, seen = [], set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        if any(k and k in name for k in blocked):
            continue  # 规则9：二次校验过敏原/忌口
        if valid_names and name not in valid_names:
            continue  # 规则12：只从菜单中推荐
        try:
            price = float(item.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        seen.add(name)
        result.append({
            "name": name,
            "category": str(item.get("category") or ""),
            "reason": str(item.get("reason") or ""),
            "price": price,
            "portion": str(item.get("portion") or "整份"),
        })
        if len(result) >= limit:
            break

    # ---- combination：以 AI 结果为基底，重算总价与预算校验 ----
    comb = parsed.get("combination") if isinstance(parsed.get("combination"), dict) else {}

    def _int(key, default):
        try:
            return int(comb.get(key, default))
        except (TypeError, ValueError):
            return default

    total_n = len(result)
    cold_n = _int("coldDishes", sum(1 for d in result if "凉" in d["category"]))
    soup_n = sum(1 for d in result if "汤" in d["category"])
    hot_n = max(_int("hotDishes", total_n - cold_n - soup_n), 0)
    meat_veg = str(comb.get("meatVegRatio") or "-")
    if meat_veg == "-":
        meat = sum(1 for d in result if "荤" in d["category"] or "肉" in d["category"])
        veg = sum(1 for d in result if "素" in d["category"])
        if meat + veg > 0:
            meat_veg = f"{meat}:{veg}"
    estimated_total = round(sum(d["price"] for d in result), 2)
    within = comb.get("withinBudget")
    if req.budget and req.budget > 0:
        within = estimated_total <= req.budget

    tips = [str(t) for t in (parsed.get("tips") or []) if str(t).strip()]
    if req.budget and req.budget > 0 and estimated_total > req.budget:
        tips.append(f"估算总价 {estimated_total} 元已超预算 {req.budget} 元，可减少贵菜或改半份")

    combination = {
        "totalDishes": total_n,
        "coldDishes": cold_n,
        "hotDishes": hot_n,
        "meatVegRatio": meat_veg,
        "estimatedTotal": estimated_total,
        "withinBudget": bool(within) if within is not None else True,
    }
    return {"recommendedDishes": result, "combination": combination, "tips": tips, "filteredOut": removed}


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
        from backend.model_config import get_ai_cost_summary, get_budget_status, get_provider_status
    except ImportError:
        from model_config import get_ai_cost_summary, get_budget_status, get_provider_status
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
        "total_errors": summary.get("total_errors", 0),
        "errors_recent": summary.get("errors_recent", []),
        "providers_configured": get_provider_status(),
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


# ========== V157: /api 前缀别名（修复线上 Nginx 去前缀代理导致 /api/* 路由不可达） ==========
# 线上 Nginx 对 /api/ 采用去前缀代理（proxy_pass 尾斜杠），/api/food-hall/published
# 到达本服务时路径为 /food-hall/published，老路由 /chat、/health 因此可达，而 V139+
# 新增的 /api/* 路由（daily-tip、weather、food-hall、migration）全部落空。
# 为所有 /api/xxx 路由注册无前缀别名 /xxx，两种路径形态均可命中。
# 注意：GET 别名必须在 catch-all 之前注册，否则会被 /{path:path} 吞掉返回 HTML；
# 而 /api/daily-tip、/api/weather、/api/migration 定义在本块之后，文件末尾会补跑一遍。
_api_alias_done: set = set()

def _register_api_prefix_aliases() -> None:
    for _alias_route in list(app.routes):
        _alias_path = getattr(_alias_route, "path", "")
        if (
            _alias_path.startswith("/api/")
            and "{" not in _alias_path
            and _alias_path not in _api_alias_done
        ):
            _api_alias_done.add(_alias_path)
            _alias_methods = sorted(set(getattr(_alias_route, "methods", None) or ["GET"]))
            try:
                app.add_api_route(
                    _alias_path[len("/api"):],
                    _alias_route.endpoint,
                    methods=_alias_methods,
                    include_in_schema=False,
                    name="api_alias_" + _alias_path.strip("/").replace("/", "_"),
                )
            except Exception as _alias_exc:
                print(f"[alias] 注册别名失败 {_alias_path}: {_alias_exc}")

_register_api_prefix_aliases()


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
            "health", "chat", "plan", "recommend-restaurants", "recommend-dishes", "parse", "parse_voice", "parse_link",
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


# ========== V137: localStorage 数据迁移（换端口 8010→8011 后旧源数据中转） ==========
MIGRATION_FILE = Path(__file__).parent / "data_migration.json"

class MigrationPayload(BaseModel):
    payload: Dict[str, Any]

@app.post("/api/migration/upload", include_in_schema=False)
async def migration_upload(req: MigrationPayload):
    """8010 源页面把本源 localStorage 快照上传到服务端暂存。"""
    try:
        MIGRATION_FILE.write_text(json.dumps(req.payload, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "keys": len(req.payload)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/migration/download", include_in_schema=False)
async def migration_download():
    """8011 源页面拉取暂存的旧数据快照（用POST避开先注册的GET catch-all静态路由）。"""
    if MIGRATION_FILE.exists():
        try:
            return json.loads(MIGRATION_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ========== V138: 天气代理接口（和风天气，POST /api/weather） ==========
import time as _wx_time

QWEATHER_API_KEY = os.environ.get("QWEATHER_API_KEY", "").strip()
# 和风 2024-08 后新注册项目须用专属 API Host（控制台查看，形如 abc123.re.qweatherapi.com）；
# 旧域名 devapi.qweather.com 对新 Key 返回 403。QWEATHER_API_HOST 留空则用旧域名（兼容老 Key）。
_wx_host = os.environ.get("QWEATHER_API_HOST", "").strip().rstrip("/")
if _wx_host and not _wx_host.startswith("http"):
    _wx_host = "https://" + _wx_host
# 免费开发版 Key 的 GeoAPI 走 devapi 域名（geoapi.qweather.com 为付费版专属，免费 Key 请求 404）
QWEATHER_GEO_URL = (_wx_host + "/geo/v2/city/lookup") if _wx_host else "https://devapi.qweather.com/geo/v2/city/lookup"
QWEATHER_7D_URL = (_wx_host + "/v7/weather/7d") if _wx_host else "https://devapi.qweather.com/v7/weather/7d"
_WEATHER_CACHE: Dict[str, Dict[str, Any]] = {}  # "city|days" -> {"ts": 时间戳, "data": 响应}
_WEATHER_CACHE_TTL = 3 * 3600  # 3 小时
_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class WeatherRequest(BaseModel):
    city: str
    days: int = 7


def _weather_error(detail: str = "") -> Dict[str, str]:
    """统一降级响应：error 固定文案，detail 带原因，不抛异常不阻塞主流程。"""
    err: Dict[str, str] = {"error": "天气获取失败"}
    if detail:
        err["detail"] = detail[:120]
    return err


def _wx_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _wx_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _summarize_weather(daily: List[Dict[str, Any]]) -> str:
    """本地规则拼接一句话摘要（不调 AI）：未来N天以X为主，周X有降水，气温A-B°C。"""
    if not daily:
        return ""
    from collections import Counter
    texts = [str(d.get("textDay") or "").strip() for d in daily if d.get("textDay")]
    mode_text = Counter(texts).most_common(1)[0][0] if texts else ""
    try:
        tmin = min(int(d["tempMin"]) for d in daily if d.get("tempMin") is not None)
        tmax = max(int(d["tempMax"]) for d in daily if d.get("tempMax") is not None)
        temp_part = f"气温{tmin}-{tmax}°C"
    except (ValueError, TypeError):
        temp_part = ""
    rain_days: List[str] = []
    for d in daily:
        t = str(d.get("textDay") or "") + str(d.get("textNight") or "")
        if "雨" in t or "雪" in t:
            try:
                from datetime import datetime as _dt
                wd = _WEEKDAY_CN[_dt.strptime(str(d.get("date")), "%Y-%m-%d").weekday()]
                if wd not in rain_days:
                    rain_days.append(wd)
            except (ValueError, TypeError):
                pass
    n = len(daily)
    parts = []
    if mode_text:
        parts.append(f"未来{n}天以{mode_text}为主" if n > 1 else f"今天{mode_text}")
    if rain_days:
        parts.append("、".join(rain_days[:3]) + "有降水")
    if temp_part:
        parts.append(temp_part)
    return "，".join(parts)


@app.post("/api/weather")
async def api_weather(req: WeatherRequest):
    """天气代理：前端同源调用，避免暴露 Key 与 CORS；同城市 3 小时内存缓存；任何失败返回 error 字段。"""
    city = (req.city or "").strip()
    if not city:
        return _weather_error("城市不能为空")
    try:
        days = max(1, min(7, int(req.days or 7)))
    except (TypeError, ValueError):
        days = 7

    # 1) 缓存命中：3 小时内同城市+天数直接返回
    cache_key = f"{city}|{days}"
    now = _wx_time.time()
    hit = _WEATHER_CACHE.get(cache_key)
    if hit and now - hit.get("ts", 0) < _WEATHER_CACHE_TTL:
        return hit["data"]

    if not QWEATHER_API_KEY:
        return _weather_error("未配置 QWEATHER_API_KEY（backend/.env）")

    # 认证只用 X-QW-Api-Key（实测专属 Host 下与 Bearer 双发时上游按 Bearer 校验导致 401）
    headers = {"X-QW-Api-Key": QWEATHER_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 2) 城市搜索 → LocationID
            geo = await client.get(
                QWEATHER_GEO_URL,
                params={"location": city, "number": 1, "lang": "zh"},
                headers=headers,
            )
            geo.raise_for_status()
            geo_data = geo.json()
            if geo_data.get("code") != "200" or not geo_data.get("location"):
                return _weather_error(f"城市查询失败(code={geo_data.get('code')})")
            location_id = str(geo_data["location"][0].get("id") or "")
            city_name = str(geo_data["location"][0].get("name") or city)
            # 3) 7 天预报（days<7 时截取前 N 天）
            wx = await client.get(
                QWEATHER_7D_URL,
                params={"location": location_id, "lang": "zh"},
                headers=headers,
            )
            wx.raise_for_status()
            wx_data = wx.json()
    except Exception as exc:  # 兜底：任何上游异常（httpx/解析/未知）都降级，不阻塞主流程
        return _weather_error(str(exc))

    if wx_data.get("code") != "200" or not wx_data.get("daily"):
        return _weather_error(f"预报获取失败(code={wx_data.get('code')})")

    daily_out = []
    for d in wx_data.get("daily", [])[:days]:
        daily_out.append({
            "date": str(d.get("fxDate") or ""),
            "textDay": str(d.get("textDay") or ""),
            "textNight": str(d.get("textNight") or ""),
            "tempMax": _wx_int(d.get("tempMax")),
            "tempMin": _wx_int(d.get("tempMin")),
            "humidity": _wx_int(d.get("humidity")),
            "precip": _wx_float(d.get("precip")),
            "windScale": str(d.get("windScaleDay") or ""),
        })

    result = {"city": city_name, "daily": daily_out, "summary": _summarize_weather(daily_out)}
    _WEATHER_CACHE[cache_key] = {"ts": now, "data": result}
    # 防膨胀：超过 50 个城市缓存时淘汰最旧条目
    if len(_WEATHER_CACHE) > 50:
        oldest = min(_WEATHER_CACHE.items(), key=lambda kv: kv[1].get("ts", 0))[0]
        _WEATHER_CACHE.pop(oldest, None)
    return result


# ========== V139: 食养堂每日一课（POST /api/daily-tip） ==========
# 同一家庭同一天只生成一次，内存缓存到当天 23:59（键 "familyId|date"）
_DAILY_TIP_CACHE: Dict[str, Dict[str, Any]] = {}

_DAILY_TIP_SYSTEM = (
    "你是家庭饮食知识编辑。请为今天的家庭推送一条食养知识。\n\n"
    "### 选题优先级\n"
    "1. 如果今天或明天是节气 → 讲该节气的饮食讲究\n"
    "2. 如果今天有显著天气变化（降温>5°C、高温>32°C、雨雪）→ 讲对应饮食\n"
    "3. 如果都没有 → 讲当季食材或家常烹饪技巧\n\n"
    "### 内容要求\n"
    "1. 标题：15字以内，有悬念或画面感\n"
    "2. 摘要：60字以内，卡片展示用\n"
    "3. 正文：200-300字，口语化，像和老朋友聊天\n"
    "4. 小贴士：2条，每条20字以内\n"
    "5. 可关联 1-2 种当季食材\n\n"
    "### 严格禁止\n"
    "- 禁止使用：治疗、治愈、改善、降低、预防、功效、食疗、润肺、止咳、降三高、祛湿\n"
    "- 禁止提及任何疾病名称\n"
    "- 禁止承诺健康效果\n"
    "- 禁止\"XX能治XX\"的句式\n\n"
    "### 返回格式\n"
    "严格 JSON，不要任何解释性文字，格式：\n"
    '{"category":"节气食养","title":"","summary":"","content":"",'
    '"tips":["",""],"relatedIngredients":[""]}'
)

# 输出侧禁用词（与 prompt 双保险；V139 规格 11 词）
_DAILY_TIP_BANNED = ["治疗", "治愈", "改善", "降低", "预防", "功效", "食疗", "润肺", "止咳", "降三高", "祛湿"]


def _strip_banned_words(text: str) -> str:
    for w in _DAILY_TIP_BANNED:
        text = text.replace(w, "")
    return text


def _clean_daily_tip(data: Dict[str, Any]) -> Dict[str, Any]:
    """归一化 AI 返回的每日一课内容：字段齐全、清洗禁用词、限长。"""
    title = _strip_banned_words(str(data.get("title") or "").strip())[:20]
    summary = _strip_banned_words(str(data.get("summary") or "").strip())[:70]
    content = _strip_banned_words(str(data.get("content") or "").strip())
    tips = []
    for t in (data.get("tips") or [])[:2]:
        t = _strip_banned_words(str(t).strip())
        if t:
            tips.append(t[:25])
    related = [str(x).strip()[:10] for x in (data.get("relatedIngredients") or [])[:2] if str(x).strip()]
    category = str(data.get("category") or "节气食养").strip()[:10] or "节气食养"
    return {"category": category, "title": title, "summary": summary,
            "content": content, "tips": tips, "relatedIngredients": related}


def _daily_tip_expire_ts(date_str: str) -> float:
    """缓存有效期：当天 23:59:59（date 非法时退化为 24 小时）。"""
    try:
        import datetime as _dt
        day = _dt.datetime.strptime(date_str, "%Y-%m-%d")
        return day.replace(hour=23, minute=59, second=59).timestamp()
    except (ValueError, TypeError):
        return time.time() + 86400


class DailyTipRequest(BaseModel):
    date: str
    familyId: str = ""
    city: str = ""
    familyBrief: str = ""
    solarTerm: str = ""
    weather: Optional[Dict[str, Any]] = None
    variant: int = 0  # V139: "换一条"用，0=默认（缓存键兼容原规格 daily_tip_${familyId}_${date}）
    subscriptionPlan: Optional[str] = None  # V199/V202: 订阅 tier（basic/premium），后端追加专属 Prompt
    needTags: Optional[List[str]] = None  # V202: premium 全家桶套餐下勾选的家庭需求（婴儿/老人/增高/体育生/康养），动态追加人群 Prompt


# V199: 订阅专属 Prompt 段——按套餐类型追加不同的侧重指令
# 注意：必须与 _DAILY_TIP_SYSTEM 的「禁止疾病名称/医疗词汇」条款兼容，
# 因此慢病关怀一律用生活化表述（低盐低糖、长辈适量、不宜多吃），不得出现疾病名。
# V199.1 提高 prompt 版本号，缓存键带上它 → 改动措辞后旧缓存自动失效
_SUBSCRIPTION_PROMPT_VER = "v4"  # V202: 双轨 tier + 动态 needTags
_SUBSCRIPTION_PROMPT_SEGMENTS = {
    # V202: 基础服务——日常营养建议，无特殊人群定制
    "basic": (
        "【订阅专享：基础服务】\\n"
        "- 内容聚焦家庭日常三餐营养建议，语气亲切实用，适合普通家庭参考\\n"
        "- tips 侧重食材选择/搭配/储存的生活化小技巧\\n"
    ),
    # V202: 全家桶订阅——动态叠加 needTags 人群段
    "premium": (
        "【订阅专享：全家桶订阅】\\n"
        "- 内容必须紧扣该家庭勾选的特殊人群需求（详见下文【家庭需求绑定】段）\\n"
        "- 正文必须明确指出 2-3 个与家庭需求匹配的实操建议或食材选择要点\\n"
        "- 语气严谨克制，避免空泛的养生话术\\n"
    ),
}

# V202: premium 全家桶下 needTags 命中时追加的人群专属 Prompt 段
_SUBSCRIPTION_NEED_PROMPT_SEGMENTS = {
    "婴儿": (
        "- 婴儿（0-3岁辅食）：正文中必须包含 1-2 条适合婴儿初次添加的泥糊状辅食建议，"
        "标注「初次添加请少量尝试，观察 3 天」，不得推荐含有蜂蜜/盐/糖的辅食，不得提及牛奶（1岁以下）\\n"
    ),
    "老人": (
        "- 老人（慢病/软食）：正文必须列出 2-3 种「不宜多吃」的食材或调味方式（如腌制品、浓汤宝、糖醋做法），"
        "末尾必须有一行以「⚠️ 留意：」开头的生活化提醒（少盐/少糖/少油/适量/少吃），不得出现疾病名称\\n"
    ),
    "增高": (
        "- 发育期青少年增高：内容需提到高蛋白/高钙食材的搭配，以及有助于长高的作息/运动小提示，"
        "避免推荐容易饱腹感强但营养密度低的食物\\n"
    ),
    "体育生": (
        "- 体育生（高能/增肌）：正文至少标注 2 处食材的大致每 100g 热量范围，"
        "tips 第 1 条必须给出「推荐摄入时段」（如训练前 1h / 训练后 30min）\\n"
    ),
    "康养": (
        "- 康养恢复（术后/血虚/孕妇）：内容侧重清淡易消化、补气养血类食材的温和做法，"
        "避免寒凉生冷、辛辣刺激食材的推荐，语气平和克制\\n"
    ),
}

# V199.3/V202: 硬性格式要求放在 user 轮次（system 里规则过多时模型容易漏）
_SUBSCRIPTION_USER_REQUIREMENTS = {
    "basic": "【输出硬要求】tips 数组第 1 条必须是生活化小技巧，20 字以内。",
    "premium": (
        "【输出硬要求】正文必须明确列出 2-3 条与该家庭需求匹配的实操建议；"
        "正文最后一句话必须单独成段、以「⚠️ 留意：」开头，内容与勾选的家庭需求相关，"
        "只许用生活化表述，不得出现任何疾病名称。"
    ),
}


@app.post("/api/daily-tip")
async def daily_tip(req: DailyTipRequest) -> Dict[str, Any]:
    """食养堂每日一课：按 节气>天气>当季食材 优先级生成，同家庭同日仅生成一次。"""
    date_str = (req.date or "").strip()
    family_id = (req.familyId or "default").strip()[:32]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return {"error": "日期格式应为 YYYY-MM-DD"}

    sub_plan = (req.subscriptionPlan or "").strip()
    # V199: 订阅类型必须进缓存键——否则用户先未订阅访问（缓存一条非订阅 tip），
    # 再订阅会命中旧缓存、拿不到 subscriptionExclusive=true 的内容
    need_tags = [str(t).strip() for t in (req.needTags or []) if str(t).strip()]
    need_tags_for_cache = "|".join(sorted(set(need_tags)))
    cache_key = f"{family_id}|{date_str}|{max(0, int(req.variant))}|{sub_plan}|{need_tags_for_cache}|{_SUBSCRIPTION_PROMPT_VER if sub_plan else ''}"
    hit = _DAILY_TIP_CACHE.get(cache_key)
    now_ts = time.time()
    if hit:
        if now_ts <= hit.get("expire", 0):
            return hit["data"]
        _DAILY_TIP_CACHE.pop(cache_key, None)  # 过期清理

    # V143: 首屏（variant=0）优先返回前一天 22:30 流水线预选入库的内容，
    # 早高峰零 AI 延迟；换一换(variant>0)仍走实时生成
    # V199: 订阅用户跳过预选（预选文章不感知套餐），改走专属生成；生成失败可用发布稿兜底
    if req.variant == 0 and not sub_plan:
        pub_row = _fh_get_published_row(date_str)
        if pub_row:
            pub_result = _fh_row_to_article(pub_row)
            _DAILY_TIP_CACHE[cache_key] = {"expire": _daily_tip_expire_ts(date_str), "data": pub_result}
            return pub_result

    # 选题上下文：天气描述行
    weather_line = "未提供"
    if isinstance(req.weather, dict) and req.weather:
        parts = []
        if req.weather.get("textDay"):
            parts.append(str(req.weather["textDay"]))
        if req.weather.get("tempMax") is not None and req.weather.get("tempMin") is not None:
            parts.append(f"{req.weather['tempMin']}~{req.weather['tempMax']}°C")
        if parts:
            weather_line = " ".join(parts)

    # 订阅专属 Prompt 段（V199/V202）
    exclusive_seg = _SUBSCRIPTION_PROMPT_SEGMENTS.get(sub_plan, "") if sub_plan else ""
    # V202: premium 全家桶下按 needTags 动态拼接人群专属 Prompt（need_tags 已在上方 cache_key 前声明）
    if sub_plan == "premium" and need_tags:
        tagged = [_SUBSCRIPTION_NEED_PROMPT_SEGMENTS[t] for t in need_tags if t in _SUBSCRIPTION_NEED_PROMPT_SEGMENTS]
        need_seg = "【家庭需求绑定】\\n" + "".join(tagged)
        exclusive_seg = exclusive_seg + need_seg if exclusive_seg else need_seg
    system_prompt = _DAILY_TIP_SYSTEM + ("\\n\\n" + exclusive_seg if exclusive_seg else "")

    user_lines = [
        f"【日期】{date_str}",
        f"【节气】{req.solarTerm.strip() or '不在节气当天'}",
        f"【天气】{weather_line}",
        f"【城市】{(req.city or '').strip() or '未提供'}",
        f"【家庭】{(req.familyBrief or '').strip()[:200] or '普通家庭'}",
    ]
    if req.variant > 0:
        user_lines.append(f"【要求】这是该家庭今天看到的第 {req.variant + 1} 条，请换一个与之前完全不同的选题角度（如不同食材、烹饪技巧、节气习俗），标题和内容都不要与之前重复。")
    if sub_plan in _SUBSCRIPTION_USER_REQUIREMENTS:
        user_lines.append(_SUBSCRIPTION_USER_REQUIREMENTS[sub_plan])
    def _published_fallback():
        """V199: 订阅用户专属生成失败时，用已发布文章兜底，避免卡片空窗（不标专享，避免误导）"""
        if req.variant != 0:
            return None
        row = _fh_get_published_row(date_str)
        if not row:
            return None
        art = _fh_row_to_article(row)
        return art  # 不写缓存：下次请求仍会重试专属生成

    # V199.4: 订阅内容质量校验——模型偶发漏要求或输出截断，校验不达标自动重试一次
    def _sub_content_ok(t: Dict[str, Any]) -> bool:
        c = str(t.get("content") or "")
        if len(c) < 80:
            return False
        tips_ = t.get("tips") or []
        if sub_plan == "basic":
            return bool(tips_)
        if sub_plan == "premium":
            # premium 全家桶：至少要有 ⚠️ 提醒或一个实操建议
            return "⚠️" in c or any(k in c for k in ("建议", "适合", "推荐", "不宜"))
        return True

    tip = None
    for attempt in range(2 if sub_plan else 1):
        try:
            content = await call_qwen(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "\n".join(user_lines)},
                ],
                json_mode=True,
                max_tokens=1200,
                task_type="daily_tip",
            )
        except Exception as e:  # AI 不可用不阻塞：返回降级提示
            print(f"[daily-tip] call_qwen error (attempt {attempt + 1}): {e}")
            continue
        try:
            parsed = extract_json_object(content)
        except Exception as e:
            print(f"[daily-tip] json parse error (attempt {attempt + 1}): {e}")
            continue
        if not isinstance(parsed, dict) or not parsed.get("title"):
            continue
        cand = _clean_daily_tip(parsed)
        if not cand["title"] or not cand["content"]:
            continue
        if not sub_plan or _sub_content_ok(cand):
            tip = cand
            break
        print(f"[daily-tip] 订阅内容未达标，重试（plan={sub_plan}, len={len(cand['content'])}）")
        tip = cand  # 保留最后一次结果，重试仍不达标时兜底展示

    if tip is None:
        return _published_fallback() or {"error": "每日一课生成失败，请稍后重试"}

    result = {"id": f"tip_{date_str.replace('-', '')}", "date": date_str, **tip}
    # V199: 订阅用户的 tip 带 subscriptionExclusive=true，前端据此渲染「订阅专享内容」徽标
    if sub_plan:
        result["subscriptionExclusive"] = True
        result["subscriptionPlan"] = sub_plan
        if sub_plan == "premium" and need_tags:
            result["needTags"] = need_tags
    _DAILY_TIP_CACHE[cache_key] = {"expire": _daily_tip_expire_ts(date_str), "data": result}
    # 防膨胀：最多缓存 120 个 家庭|日期 条目，超出淘汰最先过期者
    if len(_DAILY_TIP_CACHE) > 120:
        oldest = min(_DAILY_TIP_CACHE.items(), key=lambda kv: kv[1].get("expire", 0))[0]
        _DAILY_TIP_CACHE.pop(oldest, None)
    return result


# ========== V202.1: 未来 7 天饮食沙盘（POST /api/deep-plan） ==========
# 仅 premium 全家桶订阅用户可用；basic 订阅走轻量版（3 天）；免费用户不调用此接口
_DEEP_PLAN_CACHE: Dict[str, Dict[str, Any]] = {}


class DeepPlanRequest(BaseModel):
    """V202.1: 未来 N 天饮食沙盘推演入参"""
    familyBrief: str = ""  # 家庭画像文本（成员年龄/体质/饮食偏好/职业节奏等）
    weather: str = ""      # 未来天气趋势文本（前端聚合接口已给）
    solarTerm: str = ""    # 当前节气名
    tier: str = "basic"    # basic 轻量 3 天 / premium 全家桶 7 天
    needTags: Optional[List[str]] = None  # premium 下命中的家庭需求标签
    familyId: str = "default"
    days: int = 7          # 推演天数（premium 默认 7，basic 默认 3）


_DEEP_PLAN_SYSTEM = """你是一位高级营养专家兼家庭饮食规划顾问。
你需要为一个中国家庭排布未来若干天的饮食沙盘推演。

你的推演必须同时满足：
1. 跨天食材复用最大化：一次买的整鸡/萝卜/白菜等尽量拆进 2-3 天的菜品，避免食材浪费。
2. 结合天气与家庭作息动态调整：降温日多安排汤品/炖菜，工作日午餐倾向快手/便当友好，训练日/熬夜日多安排高蛋白或滋补。
3. 语气克制实用，避免空泛养生话术，不得使用医疗词汇（治疗/治愈/功效/改善/疾病名称等）。

【输出 JSON 格式（必须严格遵守，不要输出任何额外文字）】
{
  "dailyPrediction": [
    {"date": "2026-10-22", "highlight": "周三降温，推荐羊肉萝卜汤配蛋炒饭", "alert": "儿子高强度训练周，需加餐补蛋白"}
  ],
  "ingredientReuse": "本周二买的整鸡，周三吃肉片烧口蘑，周四龙骨熬汤；买的半颗大白菜，周三醋溜白菜，周四白菜豆腐汤。"
}

规则：
- dailyPrediction 数组长度必须与请求的 days 一致。
- highlight 必须紧扣当天天气或家庭作息，20-40 字。
- alert 为当天的特殊提醒（可不出现，空字符串即可），不得提及疾病名称。
- ingredientReuse 必须具体指出 2-3 种跨天复用的食材及其对应日期，不少于 60 字。
"""


@app.post("/api/deep-plan")
async def deep_plan(req: DeepPlanRequest) -> Dict[str, Any]:
    """V202.1: 订阅专属——未来 N 天饮食沙盘推演。

    premium 全家桶走 7 天 + needTags 人群段；basic 订阅走 3 天轻量版；
    免费用户前端不应调用此接口（降级到本地规则推演）。
    """
    tier = (req.tier or "basic").strip()
    days = max(3, min(7, int(req.days or (7 if tier == "premium" else 3))))
    family_id = (req.familyId or "default").strip()[:32]

    # 缓存：TODAY + tier + days + needTags + solarTerm 组合键
    import datetime as _dt
    today = _dt.date.today().isoformat()
    need_tags_norm = "|".join(sorted(set([str(t).strip() for t in (req.needTags or []) if str(t).strip()])))
    solar_term = (req.solarTerm or "").strip()
    cache_key = f"{family_id}|{today}|{tier}|{days}|{need_tags_norm}|{solar_term}"
    hit = _DEEP_PLAN_CACHE.get(cache_key)
    if hit:
        # TTL = 18h（沙盘推演一天变一次足够）
        if time.time() - hit.get("ts", 0) < 18 * 3600:
            return hit["data"]
        _DEEP_PLAN_CACHE.pop(cache_key, None)

    # 构造 user prompt
    family_brief = (req.familyBrief or "").strip()[:600]
    weather_text = (req.weather or "").strip()[:300]

    # 未来 N 天日期字符串
    future_dates = []
    base = _dt.date.today()
    for i in range(days):
        d = base + _dt.timedelta(days=i)
        future_dates.append(d.isoformat())

    user_lines = [
        f"【推演天数】{days} 天",
        f"【家庭画像】{family_brief or '（未提供，按普通 3-4 口之家处理）'}",
        f"【当前节气】{solar_term or '不在节气当天'}",
        f"【天气趋势】{weather_text or '（未提供，按季节常温处理）'}",
        f"【订阅 tier】{tier}",
    ]
    if tier == "premium" and need_tags_norm:
        tag_names = "、".join([n for n in ("婴儿", "老人", "增高", "体育生", "康养") if n in need_tags_norm])
        user_lines.append(f"【家庭需求绑定】{tag_names}——推演必须覆盖这些人群的饮食要点")

    user_lines.append(f"【推演日期范围】{future_dates[0]} ~ {future_dates[-1]}")
    user_lines.append("请严格按上方 JSON schema 返回，不要输出任何额外文字。")

    messages = [
        {"role": "system", "content": _DEEP_PLAN_SYSTEM},
        {"role": "user", "content": "\n".join(user_lines)},
    ]

    result: Dict[str, Any] = {"dailyPrediction": [], "ingredientReuse": ""}
    try:
        raw = await call_qwen(messages, json_mode=True, max_tokens=2048, task_type="deep_plan", timeout=90.0)
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            # 严格清洗 dailyPrediction
            raw_list = parsed.get("dailyPrediction") or parsed.get("daily_prediction") or []
            cleaned_list: List[Dict[str, str]] = []
            for item in raw_list[:days]:
                if not isinstance(item, dict):
                    continue
                d = str(item.get("date", "") or "").strip()
                # 补全日期：模型可能漏了，按 future_dates 顺序补
                if not d and len(cleaned_list) < len(future_dates):
                    d = future_dates[len(cleaned_list)]
                hl = str(item.get("highlight", "") or "").strip()
                al = str(item.get("alert", "") or "").strip()
                if d or hl:
                    cleaned_list.append({"date": d, "highlight": hl, "alert": al})
            result["dailyPrediction"] = cleaned_list
            result["ingredientReuse"] = str(parsed.get("ingredientReuse", "") or parsed.get("ingredient_reuse", "") or "").strip()

        # 至少有 1 天数据才算可用
        if result["dailyPrediction"]:
            _DEEP_PLAN_CACHE[cache_key] = {"ts": time.time(), "data": result}
            if len(_DEEP_PLAN_CACHE) > 40:
                # 淘汰最老的
                oldest = min(_DEEP_PLAN_CACHE.items(), key=lambda kv: kv[1].get("ts", 0))[0]
                _DEEP_PLAN_CACHE.pop(oldest, None)
    except Exception as exc:
        # AI 失败：返回空结果，前端会降级提示
        print(f"[deep_plan] AI failed: {exc}")

    result["tier"] = tier
    result["days"] = days
    return result


# ========== V202.2: 一人一案分餐指导（POST /api/portion-guide） ==========
# 订阅用户专属：基于家庭成员画像给同一道菜生成每人专属分餐量 + 饮食提醒
_PORTION_GUIDE_CACHE: Dict[str, Dict[str, Any]] = {}


class PortionGuideMember(BaseModel):
    name: str = ""
    age: Optional[int] = None
    gender: Optional[str] = None
    constitution: Optional[str] = None       # 体质（平和/气虚/阳虚/阴虚/痰湿/湿热/血瘀/气郁/特禀）
    tags: Optional[List[str]] = None         # 标签（婴儿/高龄/体育生/产后/体力劳动者/孕妇/外国人…）
    preference: Optional[Dict[str, Any]] = None  # {likes/dislikes/concerns/notes}


class PortionGuideRequest(BaseModel):
    dishName: str = ""
    dishBrief: str = ""                       # 可选：菜品简介/主要食材，帮助模型分餐
    familyMembers: List[PortionGuideMember]
    tier: str = "basic"
    familyId: str = "default"


_PORTION_GUIDE_SYSTEM = """你是一位高级营养专家，擅长根据家庭成员的年龄、体质、标签与饮食偏好，为同一道菜制定每人专属的分餐指导。

【规则】
1. 分餐量（portion）必须用可量化的表述，如「排骨3块，米饭1碗」「红烧肉2块，米饭小半碗」，不得用抽象词「适量/多吃/少吃」。
2. tips 必须紧扣该成员画像——体质（气虚/阴虚/湿热/痰湿…）、标签（体育生/高龄/婴儿/体力劳动者/孕妇…）、忌口；语气克制实用，不得使用治疗/治愈/功效/改善/疾病名称等医疗词汇。
3. 特殊人群分餐系数参考（供你在分餐量里体现，不是硬规则）：
   - 婴儿（0-3岁）：不计入家庭主餐分餐，portion 写「不参与主餐分餐，按婴儿辅食方案准备」
   - 70+高龄老人：主菜量 ×0.8，主食量 ×0.7
   - 3-6岁儿童：主菜量 ×0.5，主食量 ×0.6
   - 体育生/体力劳动者：主菜量 ×1.2~1.5（高蛋白），主食量 ×1.3
   - 孕妇/产后：主菜量 ×1.1，主食量 ×1.1，tips 侧重清淡养血、避免寒凉
   - 慢病（高血压/高尿酸/血糖偏高）：tips 侧重避开汤汁/腌制品/高嘌呤，不得在 portion 里出现疾病词
4. 菜品总量 = 所有成员分餐量之和（如排骨总共 3+5+1+2=11 块）；无需你在输出里给总量，但分餐时要心里有数。

【输出 JSON 格式（严格遵守，不要额外文字）】
{
  "guide": [
    {"member": "成员姓名", "portion": "排骨3块，米饭1碗", "tips": "气虚质，排骨用水煮去油"}
  ]
}
"""


def _portion_guide_fallback(members: List["PortionGuideMember"], dish_name: str) -> List[Dict[str, str]]:
    """AI 不可用时的本地规则兜底：按年龄/标签/体质给出可执行的分餐量与提醒。"""
    _constitution_tips = {
        "气虚质": "宜温软易消化，少生冷",
        "阳虚质": "宜温热熟食，少生冷",
        "阴虚质": "清淡少辛辣，多喝温水",
        "痰湿质": "少油少糖，清淡为主",
        "湿热质": "清淡少油炸辛辣",
        "血瘀质": "饮食温和，少过咸",
        "气郁质": "清淡多样，规律进食",
        "特禀质": "避开已知过敏食材",
        "平和质": "均衡搭配即可",
    }
    _tag_tips = [
        (("体育生",), "高蛋白需求，可分开两次补充"),
        (("体力劳动者",), "主食适量增加，保证能量"),
        (("高龄", "老人"), "食材切软煮烂，细嚼慢咽"),
        (("孕妇", "产后"), "清淡为主，注意温热"),
        (("外国人",), "可按口味偏好调整调味"),
    ]
    guide = []
    for m in members:
        name = (m.name or "").strip() or "家人"
        tags = [str(t) for t in (m.tags or []) if t]
        age = m.age or 0
        if "婴儿" in tags or (age and age < 3):
            guide.append({
                "member": name,
                "portion": "不参与主餐分餐，按婴儿辅食方案单独准备",
                "tips": "辅食从少量泥糊状开始，观察3天无不适再加量，遵医嘱",
            })
            continue
        if "体育生" in tags or "体力劳动者" in tags:
            portion = "主菜约1.3人份，米饭约1碗半"
            tags_hit = "体育生" if "体育生" in tags else "体力劳动者"
        elif age >= 70:
            portion = "主菜约0.8人份，米饭约大半碗"
            tags_hit = "高龄"
        elif 3 <= age <= 6:
            portion = "主菜约半人份，米饭约小半碗"
            tags_hit = ""
        else:
            portion = "主菜1人份，米饭1碗"
            tags_hit = ""
        tips_list = []
        for keys, tip in _tag_tips:
            if any(k in tags for k in keys) or (tags_hit and tags_hit in keys):
                tips_list.append(tip)
                break
        cons = (m.constitution or "").strip()
        if cons and cons in _constitution_tips:
            tips_list.append(f"{cons}，{_constitution_tips[cons]}")
        pref = m.preference or {}
        dislikes = [str(x) for x in (pref.get("dislikes") or []) if x]
        if dislikes:
            tips_list.append("避开" + "、".join(dislikes[:3]))
        guide.append({
            "member": name,
            "portion": portion,
            "tips": "；".join(tips_list) if tips_list else "按家常口味正常食用即可",
        })
    return guide


@app.post("/api/portion-guide")
async def portion_guide(req: PortionGuideRequest) -> Dict[str, Any]:
    """V202.2: 订阅专属——一人一案分餐指导。"""
    dish_name = (req.dishName or "").strip()
    if not dish_name:
        return {"guide": []}

    members = req.familyMembers or []
    if not members:
        return {"guide": []}

    family_id = (req.familyId or "default").strip()[:32]
    tier = (req.tier or "basic").strip()

    # 缓存：dishName + familyMembers 签名 + tier 组合键
    member_sig = "|".join([
        str(m.name or "") + ":" + str(m.age or "") + ":" + ",".join(sorted(m.tags or [])) + ":" + str(m.constitution or "")
        for m in members
    ])
    cache_key = f"{family_id}|{dish_name}|{member_sig}|{tier}"
    hit = _PORTION_GUIDE_CACHE.get(cache_key)
    if hit:
        if time.time() - hit.get("ts", 0) < 12 * 3600:
            return hit["data"]
        _PORTION_GUIDE_CACHE.pop(cache_key, None)

    # 构造 user prompt：每人一行画像
    member_lines = []
    for m in members:
        parts = []
        if m.name: parts.append(m.name)
        if m.age: parts.append(f"{m.age}岁")
        if m.gender: parts.append(m.gender)
        if m.constitution: parts.append(f"体质={m.constitution}")
        if m.tags: parts.append("标签=" + "、".join([str(t) for t in m.tags if t]))
        pref = m.preference or {}
        dislikes = pref.get("dislikes") or []
        concerns = pref.get("concerns") or ""
        if dislikes: parts.append("忌口=" + "、".join([str(x) for x in dislikes if x]))
        if concerns: parts.append(f"饮食关注={concerns}")
        member_lines.append("  - " + "；".join(parts))

    user_lines = [
        f"【菜品】{dish_name}",
        f"【菜品简介】{(req.dishBrief or '').strip() or '（未提供）'}",
        f"【订阅 tier】{tier}",
        f"【家庭成员（{len(members)}人）】",
        *member_lines,
        "请为每位成员分别制定这道菜的分餐量 + 专属饮食提醒，严格按上方 JSON schema 返回。",
    ]

    messages = [
        {"role": "system", "content": _PORTION_GUIDE_SYSTEM},
        {"role": "user", "content": "\n".join(user_lines)},
    ]

    result: Dict[str, Any] = {"guide": []}
    try:
        # 30s 内必须返回，避免 Nginx 默认 60s 读超时后返回 504 导致前端报「生成失败」
        raw = await call_qwen(messages, json_mode=True, max_tokens=2048, task_type="portion_guide", timeout=30.0)
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            raw_guide = parsed.get("guide") or parsed.get("portionGuide") or []
            cleaned = []
            for item in raw_guide[:len(members) + 2]:
                if not isinstance(item, dict):
                    continue
                cleaned.append({
                    "member": str(item.get("member") or item.get("name") or "").strip(),
                    "portion": str(item.get("portion") or item.get("amount") or "").strip(),
                    "tips": str(item.get("tips") or item.get("advice") or item.get("note") or "").strip(),
                })
            result["guide"] = cleaned

        if result["guide"]:
            _PORTION_GUIDE_CACHE[cache_key] = {"ts": time.time(), "data": result}
            if len(_PORTION_GUIDE_CACHE) > 60:
                oldest = min(_PORTION_GUIDE_CACHE.items(), key=lambda kv: kv[1].get("ts", 0))[0]
                _PORTION_GUIDE_CACHE.pop(oldest, None)
    except Exception as exc:
        print(f"[portion_guide] AI failed: {exc}")

    if not result["guide"]:
        # AI 超时/限流/解析失败 → 本地规则兜底，保证前端永远能看到分餐结果
        result["guide"] = _portion_guide_fallback(members, dish_name)
        result["fallback"] = True
        _PORTION_GUIDE_CACHE[cache_key] = {"ts": time.time(), "data": result}
        if len(_PORTION_GUIDE_CACHE) > 60:
            oldest = min(_PORTION_GUIDE_CACHE.items(), key=lambda kv: kv[1].get("ts", 0))[0]
            _PORTION_GUIDE_CACHE.pop(oldest, None)

    result["dishName"] = dish_name
    return result


# ========== V204: 家庭画像 AI 课程定制（POST /api/custom-course） ==========
# 基于基础课程 + 单个家庭成员画像，生成专属课程；前端落 customCourses 队列，状态 pending_review
_CUSTOM_COURSE_CACHE: Dict[str, Dict[str, Any]] = {}

_MEDICAL_TERMS = [
    "治疗", "治愈", "根治", "疗效", "药效", "功效", "抗癌", "防癌", "降三高",
    "降压", "降糖", "降脂", "消炎", "杀菌", "排毒", "处方", "药膳", "对症",
    "改善", "降低", "预防", "缓解", "调理", "养生",
]


def _sanitize_course_medical(text: str) -> Any:
    """医疗承诺词兜底清洗：命中词替换为中性表述，并返回命中清单。"""
    hits: List[str] = []
    s = str(text or "")
    for term in _MEDICAL_TERMS:
        if term in s:
            hits.append(term)
            s = s.replace(term, "日常饮食安排")
    return s, sorted(set(hits))


# ----- 定制课程审核队列（服务端持久化，超管审核的唯一事实源） -----
_CCQ_PATH = Path(__file__).resolve().parent / "data" / "custom_course_queue.json"


def _ccq_load() -> Dict[str, Any]:
    try:
        if _CCQ_PATH.exists():
            data = json.loads(_CCQ_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"[custom_course] 队列读取失败: {exc}")
    return {}


def _ccq_save(data: Dict[str, Any]) -> None:
    try:
        _CCQ_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CCQ_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_CCQ_PATH)  # 原子替换，避免半截文件
    except Exception as exc:
        print(f"[custom_course] 队列写入失败: {exc}")


def _ccq_store(req, family_id: str, base_id: str, base_title: str, base_content: str,
               course: Dict[str, Any], member, is_child: bool, is_elder: bool,
               has_insomnia: bool, is_picky: bool) -> None:
    """把定制课程写入服务端审核队列（超管后台的唯一事实源）。

    幂等规则：
    1. 同一 course id（custom_xxx）不再重复入队；
    2. 同一 familyId + baseCourseId + member.name 若已有 pending_review → 跳过
       （避免用户反复点「确认并定制」、同画像多次提交导致队列膨胀）。
    """
    cid = (course or {}).get("id") or ""
    if not cid or not family_id or not base_id:
        return
    queue = _ccq_load()
    if cid in queue:
        return

    # 复合键幂等：同家庭 + 同基础课 + 同成员名，pending_review 只留一条
    member_name = str(course.get("member", {}).get("name") or member.name or "").strip()
    composite = f"{family_id}|{base_id}|{member_name}"
    for rec in queue.values():
        if rec.get("status") != "pending_review":
            continue
        key2 = f"{rec.get('familyId','')}|{rec.get('baseCourseId','')}|{(rec.get('member') or {}).get('name','')}"
        if key2 == composite:
            # 已经有待审核的同一条 → 直接丢弃本次
            return

    queue[cid] = {
        "id": cid,
        "familyId": family_id,
        "familyName": (req.familyName or "").strip()[:40],
        "baseCourseId": base_id,
        "baseTitle": base_title,
        "baseContent": base_content,
        "member": course.get("member") or {},
        "reason": _ccq_reason(member, is_child, is_elder, has_insomnia, is_picky),
        "course": course,
        "status": course.get("status") or "pending_review",
        "rejectCount": 0,
        "createdAt": course.get("createdAt") or int(time.time() * 1000),
    }
    _ccq_save(queue)


def _member_age(member) -> Any:
    return member.get("age") if isinstance(member, dict) else getattr(member, "age", None)

def _member_health_tags(member) -> list:
    v = member.get("healthTags") if isinstance(member, dict) else getattr(member, "healthTags", [])
    return list(v or [])

def _ccq_reason(member, is_child: bool, is_elder: bool,
                has_insomnia: bool, is_picky: bool) -> str:
    """生成"定制原因"摘要，超管列表直接展示。member 可以是 Pydantic Model 或 dict。"""
    parts: List[str] = []
    age = _member_age(member)
    tags = _member_health_tags(member)
    if has_insomnia:
        parts.append("该家庭成员有失眠/睡眠不佳")
    if is_child and age is not None:
        parts.append(f"为 {age} 岁孩子定制（童话口吻）")
    if is_elder and age is not None:
        parts.append(f"为 {age} 岁老人定制（通俗软食版）")
    if is_picky:
        parts.append("该家庭成员存在挑食情况")
    if not parts and tags:
        parts.append("健康标签：" + "、".join(tags))
    if not parts:
        parts.append("家庭通用定制")
    return "；".join(parts)


class CustomCourseMember(BaseModel):
    """V204: 课程定制目标成员画像"""
    name: str = ""
    age: Optional[int] = None
    gender: str = ""
    profession: str = ""
    healthTags: List[str] = Field(default_factory=list)  # 失眠 / 气郁质 / 儿童挑食 / 高血压…


class CustomCourseRequest(BaseModel):
    baseCourseId: str = ""
    baseTitle: str = ""
    baseContent: str = ""   # 前端从 eduCourses 取全文传入，避免后端重复维护课程库
    familyMember: CustomCourseMember = Field(default_factory=CustomCourseMember)
    familyId: str = "default"
    tier: str = "premium"
    familyName: str = ""      # 超管审核列表展示用
    force: bool = False       # 超管驳回重生成时跳过缓存
    skipStore: bool = False   # 内部调用（驳回重生成）不重复入库


_CUSTOM_COURSE_SYSTEM = """你是一位专业的饮食教育专家。请基于给定的基础课程内容，为目标用户定制一份专属课程。

【硬性要求】
1. 正文控制在 500 字以内（按中文字符计），是一篇可直接朗读的完整短文，段落自然，不要大量分点罗列。
2. 严禁出现"治疗""治愈""根治""疗效""功效""处方""药膳"等医疗承诺词汇，不得提及任何疾病名称，不得暗示课程可以替代就医或用药。
3. 内容必须紧扣基础课程的知识点，只是在表达方式与侧重点上做个性化调整，不得凭空编造与基础内容冲突的结论。
4. 只输出 JSON，不要输出任何额外文字。

【输出 JSON 格式（严格遵守）】
{"title": "课程标题（不超过 18 字）", "content": "课程正文"}"""


@app.post("/api/custom-course")
async def custom_course(req: CustomCourseRequest) -> Dict[str, Any]:
    """V204: 基于家庭画像的 AI 课程定制。

    入参只需 baseCourseId + familyMember；前端同时传入基础课程全文（baseContent），
    后端拼装定制要求后调用多通道 AI，并做医疗词兜底清洗。
    """
    member = req.familyMember or CustomCourseMember()
    tags = [str(t).strip() for t in (member.healthTags or []) if str(t).strip()]
    age = member.age
    base_id = (req.baseCourseId or "").strip()[:40]
    base_title = (req.baseTitle or "").strip()[:60]
    base_content = (req.baseContent or "").strip()[:2000]
    family_id = (req.familyId or "default").strip()[:32]

    if not base_content:
        return {"ok": False, "error": "缺少基础课程内容", "course": None}

    # 画像规则判定
    is_child = age is not None and age < 12
    is_elder = age is not None and age >= 65
    has_insomnia = any(("失眠" in t) or ("气郁" in t) or ("睡" in t) for t in tags)
    is_picky = any(("挑食" in t) or ("偏食" in t) for t in tags)

    member_sig = f"{member.name}|{age}|{member.profession}|{','.join(sorted(tags))}"
    cache_key = f"{family_id}|{base_id}|{member_sig}"
    if not req.force:
        hit = _CUSTOM_COURSE_CACHE.get(cache_key)
        if hit and time.time() - hit.get("ts", 0) < 24 * 3600:
            cached = hit.get("data") or {}
            # 缓存命中同样要入审核队列：否则同一画像 24 小时内再次点击「为家人定制课程」
            # 时前端提示已提交，超管后台却查不到记录
            if not req.skipStore:
                _ccq_store(req, family_id, base_id, base_title, base_content,
                           cached.get("course") or {}, member,
                           is_child, is_elder, has_insomnia, is_picky)
            return cached

    profile_bits = []
    if member.name:
        profile_bits.append(member.name)
    if age is not None:
        profile_bits.append(f"{age}岁")
    if member.gender:
        profile_bits.append(member.gender)
    if member.profession:
        profile_bits.append(f"职业={member.profession}")
    if tags:
        profile_bits.append("健康/体质标签=" + "、".join(tags))
    profile_text = "，".join(profile_bits) or "（未提供）"

    rules = [
        "1. 内容必须围绕基础课程的核心知识点展开，可调整例子、顺序与表述。",
        "2. 字数控制在 500 字以内，结尾给出一句可以马上照做的日常建议。",
        "3. 严禁出现治疗、治愈、根治、疗效、功效等医疗承诺词汇，不得提及疾病名称。",
    ]
    if has_insomnia:
        rules.append(
            "4. 该成员有失眠/睡眠不佳标签：重点突出富含色氨酸与镁元素的日常食材"
            "（如香蕉、小米、燕麦、南瓜籽、豆制品），并明确提示晚餐避免辛辣、浓茶、咖啡因与酒精。"
        )
    if is_child:
        rules.append(
            "5. 该成员是儿童：全文改用童话故事口吻讲述（可用小厨师、蔬菜小精灵等角色），"
            "穿插 2 个互动提问（用「你知道……吗？」这类句式），语言活泼、句子简短。"
        )
    if is_elder:
        rules.append(
            "6. 该成员是老人：语言通俗口语，不使用专业术语，句子短；推荐软食、少油少盐的做法；"
            "并在正文末尾附一句建议「阅读时可放大字号，或请家人读给您听」。"
        )
    if is_picky:
        rules.append(
            "7. 该成员存在挑食情况：给出「反复轻松接触、改变形态、参与备菜」的具体做法，不强迫进食。"
        )

    user_lines = [
        f"【基础课程】{base_title or base_id}",
        f"【基础内容】\n{base_content}",
        f"【用户画像】{profile_text}",
        "【定制要求】",
    ] + rules + ["请严格按 JSON schema 返回，不要输出任何额外文字。"]

    messages = [
        {"role": "system", "content": _CUSTOM_COURSE_SYSTEM},
        {"role": "user", "content": "\n".join(user_lines)},
    ]

    title = ""
    content = ""
    fallback = False
    try:
        raw = await call_qwen(messages, json_mode=True, max_tokens=1400, task_type="custom_course", timeout=45.0)
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            title = str(parsed.get("title", "") or "").strip()
            content = str(parsed.get("content", "") or "").strip()
    except Exception as exc:
        print(f"[custom_course] AI failed: {exc}")

    if not content:
        # AI 失败兜底：截取基础正文 + 画像提示，仍返回可用内容（标记 fallback）
        fallback = True
        snippet = re.sub(r"\s+", "", base_content)[:320]
        tips = []
        if has_insomnia:
            tips.append("晚餐可以常备小米、香蕉、燕麦这类食材，晚饭后不喝浓茶与咖啡。")
        if is_child:
            tips.append("可以一边读一边问孩子：今天你想当哪个蔬菜小精灵？")
        if is_elder:
            tips.append("做法尽量软烂少油，阅读时可放大字号，或请家人读给您听。")
        content = snippet + (("　" + "".join(tips)) if tips else "")
        title = (base_title or "专属课程") + "·家庭定制版"

    content, medical_hits = _sanitize_course_medical(content)
    title, _ = _sanitize_course_medical(title)
    # 字数上限 500：按句号/问号收尾处截断，避免半句
    if len(content) > 500:
        cut = content[:500]
        for mark in ("。", "！", "？", "；"):
            pos = cut.rfind(mark)
            if pos >= 380:
                cut = cut[:pos + 1]
                break
        content = cut

    if not title:
        title = (base_title or "专属课程") + "·家庭定制版"

    course = {
        "id": "custom_" + uuid.uuid4().hex[:10],
        "title": title[:24],
        "category": "custom",
        "targetAudience": tags or ["all"],
        "duration": max(2, min(20, round(len(content) / 220) or 3)),
        "content": content,
        "isPremium": True,
        "baseCourseId": base_id,
        "member": {
            "name": member.name,
            "age": age,
            "gender": member.gender,
            "profession": member.profession,
            "healthTags": tags,
        },
        "status": "pending_review",
        "createdAt": int(time.time() * 1000),
    }
    result: Dict[str, Any] = {
        "ok": True,
        "course": course,
        "fallback": fallback,
        "warnings": medical_hits,
    }

    _CUSTOM_COURSE_CACHE[cache_key] = {"ts": time.time(), "data": result}
    if len(_CUSTOM_COURSE_CACHE) > 60:
        oldest = min(_CUSTOM_COURSE_CACHE.items(), key=lambda kv: kv[1].get("ts", 0))[0]
        _CUSTOM_COURSE_CACHE.pop(oldest, None)

    # 入服务端审核队列（超管审核的唯一事实源；前端 localStorage 仅为家庭侧镜像）
    if not req.skipStore:
        _ccq_store(req, family_id, base_id, base_title, base_content,
                   course, member, is_child, is_elder, has_insomnia, is_picky)
    return result


# ========== V205/V208: 定制课程审核（超管后台） ==========
class AdminCourseReviewRequest(BaseModel):
    id: str
    action: str = ""  # approve 通过并推送 / reject 驳回


class AdminCourseListRequest(BaseModel):
    status: str = "pending_review"


class AdminCourseGenerateRequest(BaseModel):
    id: str = Field(..., min_length=1)
    force: bool = False


class AdminCourseUpdateRequest(BaseModel):
    id: str = Field(..., min_length=1)
    title: str = ""
    content: str = ""


class SubmitCourseOrderRequest(BaseModel):
    """家庭端确认课程建议后提交到超管队列（仅元数据，超管手动触发 AI 生成正文）。"""
    familyId: str = Field("default", max_length=32)
    familyName: str = Field("", max_length=40)
    baseCourseId: str = Field("", max_length=40)
    baseCourseTitle: str = Field("", max_length=60)
    baseCourseContent: str = Field("", max_length=2000)
    member: Dict[str, Any] = Field(default_factory=dict)
    suggestionTitle: str = Field("", max_length=40)
    suggestionBaseId: str = Field("", max_length=40)


# 家庭端提交建议订单入后端审核队列（status=pending_review，course.content 为空 → 超管手动触发 AI）
@app.post("/api/admin/custom-course/submit-order", include_in_schema=False)
async def admin_submit_course_order(req: SubmitCourseOrderRequest) -> Dict[str, Any]:
    """V208: 家庭端确认课程建议时直接入库审核队列，不再自动调 AI 生成正文。"""
    fid = (req.familyId or "default").strip()[:32]
    if not fid or not req.baseCourseId:
        return {"ok": False, "error": "缺少家庭码或基础课"}
    queue = _ccq_load()

    # 幂等：同 familyId+baseCourseId+member.name 已有 pending_review 不重复入队
    member_name = str((req.member or {}).get("name") or "").strip()
    composite = f"{fid}|{req.baseCourseId}|{member_name}"
    for rec in queue.values():
        if rec.get("status") != "pending_review":
            continue
        key2 = f"{rec.get('familyId','')}|{rec.get('baseCourseId','')}|{(rec.get('member') or {}).get('name','')}"
        if key2 == composite:
            return {"ok": True, "id": rec.get("id"), "deduped": True}

    new_id = "ord_" + uuid.uuid4().hex[:8]
    member = req.member or {}
    tags = [str(t).strip() for t in (member.get("healthTags") or []) if str(t).strip()]
    age = member.get("age")
    is_child = age is not None and age < 12
    is_elder = age is not None and age >= 65
    has_insomnia = any(("失眠" in t) or ("气郁" in t) or ("睡" in t) for t in tags)
    is_picky = any(("挑食" in t) or ("偏食" in t) for t in tags)

    queue[new_id] = {
        "id": new_id,
        "familyId": fid,
        "familyName": (req.familyName or "").strip()[:40],
        "baseCourseId": req.baseCourseId.strip()[:40],
        "baseTitle": req.baseCourseTitle.strip()[:60],
        "baseContent": req.baseCourseContent.strip()[:2000],
        "member": member,
        "suggestionTitle": req.suggestionTitle.strip()[:40],
        "suggestionBaseId": req.suggestionBaseId.strip()[:40],
        "reason": _ccq_reason(member, is_child, is_elder, has_insomnia, is_picky),
        "course": {
            "id": new_id,
            "title": req.suggestionTitle.strip()[:24] or "待生成定制课程",
            "content": "",
            "status": "pending_review",
            "category": "custom",
            "duration": 5,
            "targetAudience": [t[:10] for t in tags[:3]],
            "member": member,
            "baseCourseId": req.baseCourseId,
            "createdAt": int(time.time() * 1000),
        },
        "status": "pending_review",
        "rejectCount": 0,
        "createdAt": int(time.time() * 1000),
    }
    _ccq_save(queue)
    return {"ok": True, "id": new_id}


# 超管触发 AI 生成正文（只有在 course.content 为空或 force=True 时才调用 AI）
@app.post("/api/admin/custom-course/generate", include_in_schema=False)
async def admin_generate_course(req: AdminCourseGenerateRequest) -> Dict[str, Any]:
    """V208: 超管手动触发 AI 为待生成建议订单生成完整课程正文。"""
    queue = _ccq_load()
    rec = queue.get(req.id)
    if not rec:
        raise HTTPException(status_code=404, detail="订单不存在")
    course = rec.get("course") or {}
    if course.get("content") and not req.force:
        return {"ok": True, "id": req.id, "skipped": True, "course": course}

    try:
        result = await custom_course(CustomCourseRequest(
            baseCourseId=rec.get("baseCourseId", ""),
            baseTitle=rec.get("baseTitle", ""),
            baseContent=rec.get("baseContent", ""),
            familyMember=CustomCourseMember(**(rec.get("member") or {})),
            familyId=rec.get("familyId", "default"),
            familyName=rec.get("familyName", ""),
            force=True,
            skipStore=True,
        ))
        if result.get("ok") and result.get("course"):
            nc = result["course"]
            nc["id"] = rec.get("id")
            nc["status"] = "pending_review"
            rec["course"] = nc
            rec["generatedAt"] = int(time.time() * 1000)
            _ccq_save(queue)
            return {"ok": True, "id": req.id, "course": nc, "generated": True}
        return {"ok": False, "error": "AI 未返回有效课程"}
    except Exception as exc:
        print(f"[admin_generate] AI 失败: {exc}")
        raise HTTPException(status_code=500, detail=f"AI 生成失败: {exc}")


# 超管编辑保存（预览编辑页保存标题和正文）
@app.post("/api/admin/custom-course/update", include_in_schema=False)
async def admin_update_course(req: AdminCourseUpdateRequest) -> Dict[str, Any]:
    """V208: 超管预览编辑课程后保存。"""
    queue = _ccq_load()
    rec = queue.get(req.id)
    if not rec:
        raise HTTPException(status_code=404, detail="订单不存在")
    now = int(time.time() * 1000)
    if req.title or req.content:
        course = rec.get("course") or {}
        if req.title:
            new_title, _ = _sanitize_course_medical(req.title.strip())
            course["title"] = new_title[:24]
        if req.content:
            new_content, hits = _sanitize_course_medical(req.content.strip())
            course["content"] = new_content[:2000]
            rec["sanitizeHits"] = hits
        rec["course"] = course
        rec["editedAt"] = now
        _ccq_save(queue)
    return {"ok": True, "id": req.id, "course": rec.get("course")}


# 读取用 POST 而非 GET——先注册的 GET catch-all（/{path:path} 静态回退）会吞掉后注册的 GET 路由
@app.post("/api/admin/custom-courses", include_in_schema=False)
async def admin_custom_courses(req: AdminCourseListRequest) -> Dict[str, Any]:
    """V205: 超管审核列表。鉴权沿用前端超管口令门信任模型（同食养堂 pending-list）。"""
    status = (req.status or "").strip()
    queue = _ccq_load()
    items = [v for v in queue.values() if (not status or v.get("status") == status)]
    items.sort(key=lambda x: x.get("createdAt", 0), reverse=True)
    return {"ok": True, "courses": items, "total": len(items)}


@app.post("/api/admin/custom-course/review", include_in_schema=False)
async def admin_custom_course_review(req: AdminCourseReviewRequest) -> Dict[str, Any]:
    """V205: 超管审核操作——approve 发布到该家庭"我的专属课堂"；reject 触发 AI 重新生成。"""
    queue = _ccq_load()
    rec = queue.get(req.id)
    if not rec:
        raise HTTPException(status_code=404, detail="课程不存在")
    action = (req.action or "").strip()
    now_ms = int(time.time() * 1000)

    if action == "approve":
        rec["status"] = "published"
        rec["reviewedAt"] = now_ms
        rec["publishedAt"] = now_ms
        rec["course"]["status"] = "published"
        _ccq_save(queue)
        return {"ok": True, "id": req.id, "status": "published"}

    if action == "reject":
        rec["rejectCount"] = int(rec.get("rejectCount") or 0) + 1
        rec["reviewedAt"] = now_ms
        # 退回 AI 重新生成（force 跳过缓存，避免拿到同一篇）
        try:
            regen = await custom_course(CustomCourseRequest(
                baseCourseId=rec.get("baseCourseId", ""),
                baseTitle=rec.get("baseTitle", ""),
                baseContent=rec.get("baseContent", ""),
                familyMember=CustomCourseMember(**(rec.get("member") or {})),
                familyId=rec.get("familyId", "default"),
                familyName=rec.get("familyName", ""),
                force=True,
                skipStore=True,
            ))
            if regen.get("ok") and regen.get("course") and regen["course"].get("content"):
                nc = regen["course"]
                nc["id"] = rec["id"]  # 保留原课程 id，审核列表不重复
                rec["course"] = nc
                rec["status"] = "pending_review"
                rec["regeneratedAt"] = now_ms
                _ccq_save(queue)
                return {"ok": True, "id": req.id, "status": "pending_review", "regenerated": True}
        except Exception as exc:
            print(f"[custom_course] 驳回重生成失败: {exc}")
        # 重新生成失败 → 标记 rejected，家庭端可重新发起
        rec["status"] = "rejected"
        _ccq_save(queue)
        return {"ok": True, "id": req.id, "status": "rejected", "regenerated": False}

    raise HTTPException(status_code=400, detail="action 仅支持 approve/reject")


class CustomCourseMineRequest(BaseModel):
    familyId: str = ""


# 同样用 POST 避开 catch-all
@app.post("/api/custom-course/mine", include_in_schema=False)
async def custom_course_mine(req: CustomCourseMineRequest) -> Dict[str, Any]:
    """V205: 家庭端拉取已发布的专属课程（"我的专属课堂"数据源）。"""
    fid = (req.familyId or "").strip()[:32]
    if not fid:
        return {"ok": True, "courses": []}
    queue = _ccq_load()
    items = []
    for v in queue.values():
        if v.get("familyId") == fid and v.get("status") == "published":
            c = dict(v.get("course") or {})
            c["status"] = "published"
            c["publishedAt"] = v.get("publishedAt")
            items.append(c)
    items.sort(key=lambda x: x.get("createdAt", 0), reverse=True)
    return {"ok": True, "courses": items}


# ========== V207: AI 课程建议接口 ==========
class SuggestCoursesRequest(BaseModel):
    familyMembers: List[Dict[str, Any]] = Field(default_factory=list)


_SUGGEST_COURSES_SYSTEM = """你是一位专业的饮食教育专家。请分析以下家庭档案，挑选最符合他们需求的 2-3 门课程建议。

【硬性要求】
1. 指出每门课程的适用成员（用家庭成员的真实姓名）。
2. 说明推荐理由。
3. 不涉及医疗治疗承诺，不得出现治疗、治愈、根治、疗效、功效等词汇，不得提及疾病名称。
4. 只返回严格 JSON 格式，不要输出任何额外文字。

【输出 JSON 格式（严格遵守）】
{"suggestions":[{"title":"课程标题","targetMember":"适用成员姓名","reason":"推荐理由","baseCourseId":"course_nutrition_01"}]}

baseCourseId 可选值：course_nutrition_01（基础营养学）、course_sleep_01（睡眠饮食）、course_child_01（儿童饮食）、course_elder_01（老人饮食）、course_history_01（中国饮食史）。"""


@app.post("/api/suggest-courses")
async def suggest_courses(req: SuggestCoursesRequest) -> Dict[str, Any]:
    """V207: 基于家庭档案的 AI 课程建议。返回 2-3 条课程建议列表。"""
    members = req.familyMembers or []
    member_text = json.dumps(members, ensure_ascii=False)[:2000] if members else "（未提供家庭成员信息）"

    messages = [
        {"role": "system", "content": _SUGGEST_COURSES_SYSTEM},
        {"role": "user", "content": f"【家庭档案】{member_text}\n请给出课程建议。"},
    ]

    suggestions: List[Dict[str, Any]] = []
    try:
        raw = await call_qwen(messages, json_mode=True, max_tokens=800, task_type="suggest_courses", timeout=45.0)
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            arr = parsed.get("suggestions")
            if isinstance(arr, list):
                for item in arr:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "") or "").strip()
                    target = str(item.get("targetMember", "") or "").strip()
                    reason = str(item.get("reason", "") or "").strip()
                    base_id = str(item.get("baseCourseId", "") or "").strip()
                    if not title or not base_id:
                        continue
                    title, _ = _sanitize_course_medical(title)
                    reason, _ = _sanitize_course_medical(reason)
                    suggestions.append({
                        "id": "sug_" + uuid.uuid4().hex[:8],
                        "title": title[:24],
                        "targetMember": target[:20],
                        "reason": reason[:200],
                        "baseCourseId": base_id[:40],
                    })
    except Exception as exc:
        print(f"[suggest_courses] AI failed: {exc}")

    return {"ok": True, "suggestions": suggestions}


# ========== V162: 我的创作 · AI 作品配图（通义万相 wanx2.1-t2i-turbo 异步文生图） ==========
class ChefImageRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=500)
    size: str = "1024*1024"


def _chef_image_prompt(raw: str) -> str:
    """把菜名/食材拼成美食摄影提示词，固定风格、限制长度。"""
    p = re.sub(r"[\r\n]+", "，", str(raw or "")).strip()[:280]
    return (
        f"中式家常美食摄影：{p}，盛在素雅陶瓷餐具中，精致摆盘，"
        "自然柔和光线，浅色木质桌面背景，景深虚化，高清晰度，色彩真实，令人有食欲"
    )


@app.post("/api/chef-image")
async def chef_image(req: ChefImageRequest) -> Dict[str, Any]:
    """我的创作：按菜名/食材生成一张作品配图，返回万相临时图片 URL。"""
    size = req.size if req.size in ("1024*1024", "720*1280", "1280*720") else "1024*1024"
    prompt = _chef_image_prompt(req.prompt)
    headers = {
        "Authorization": f"Bearer {ALIYUN_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    submit_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    payload: Dict[str, Any] = {
        "model": "wanx2.1-t2i-turbo",
        "input": {"prompt": prompt},
        "parameters": {"size": size, "n": 1, "prompt_extend": True},
    }
    task_id = None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(submit_url, headers=headers, json=payload)
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail=f"图片服务提交失败：{r.text[:200]}")
            data = r.json()
            task_id = (data.get("output") or {}).get("task_id")
            if not task_id:
                raise HTTPException(status_code=502, detail="图片服务未返回任务号")
            poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
            poll_headers = {"Authorization": f"Bearer {ALIYUN_API_KEY}"}
            # 万相出图通常 5~20 秒，最多轮询 60 秒
            for _ in range(30):
                await asyncio.sleep(2)
                pr = await client.get(poll_url, headers=poll_headers)
                if pr.status_code != 200:
                    continue
                pdata = pr.json()
                output = pdata.get("output") or {}
                status = output.get("task_status")
                if status == "SUCCEEDED":
                    results = output.get("results") or []
                    img_url = results[0].get("url") if results else None
                    if not img_url:
                        raise HTTPException(status_code=502, detail="图片已生成但未取得地址")
                    return {"url": img_url}
                if status in ("FAILED", "CANCELED", "UNKNOWN"):
                    msg = output.get("message") or "图片生成失败，请稍后重试"
                    raise HTTPException(status_code=502, detail=str(msg)[:200])
        raise HTTPException(status_code=504, detail="图片生成超时，请稍后重试")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"图片服务网络错误：{exc}") from exc


# ========== V187: 家庭数据云端自动备份（localStorage 兜底） ==========
# 家庭成员等数据仅存浏览器 localStorage，清缓存/换设备即丢。此处提供按家庭码存取的
# 快照接口：前端数据变动时防抖推送全量快照，本地数据缺失且云端有备份时自动恢复。
_FAMILY_BACKUP_DIR = Path(__file__).resolve().parent / "data" / "family_backups"
_FAMILY_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
# 允许备份的键（与前端 familyKey 隔离清单一致；值一律为原始字符串）
_FAMILY_BACKUP_KEYS = {
    "dietCalendar", "familyData", "contactsData", "diningGroups", "familyHomeAddress",
    "shoppingDay", "chatHistory", "dietKnowledge", "fridgeItems", "shoppingPlans",
    "purchaseHistory", "shoppingChecked", "currentDiningGroup", "snackItems",
    "lastAutoRecommendDate", "behaviorProfile", "dismissedRecommendDishes",
    "implicitPreferences", "preferenceProfile", "diningOutPlans", "courseProgress",
    "customCourses", "subscriptionStatus", "customCourseOrders",
}
_FAMILY_BACKUP_ID_RE = re.compile(r"^[A-HJ-NP-Z2-9]{8}$")  # 与前端 generateFamilyCode 字符集一致
_FAMILY_BACKUP_MAX_BYTES = 3 * 1024 * 1024


class FamilyBackupRequest(BaseModel):
    familyId: str = Field(..., min_length=8, max_length=8)
    data: Dict[str, str] = Field(default_factory=dict)


def _family_backup_path(fid: str) -> Path:
    return _FAMILY_BACKUP_DIR / f"{fid}.json"


@app.post("/api/family-backup")
async def save_family_backup(req: FamilyBackupRequest) -> Dict[str, Any]:
    fid = (req.familyId or "").strip().upper()
    if not _FAMILY_BACKUP_ID_RE.match(fid):
        return {"ok": False, "error": "家庭码格式不正确"}
    clean: Dict[str, str] = {}
    total = 0
    for k, v in (req.data or {}).items():
        if k not in _FAMILY_BACKUP_KEYS or not isinstance(v, str):
            continue
        total += len(v.encode("utf-8"))
        if total > _FAMILY_BACKUP_MAX_BYTES:
            return {"ok": False, "error": "备份内容过大（>3MB），已拒绝"}
        clean[k] = v
    if not clean:
        return {"ok": False, "error": "备份内容为空"}
    payload = {"familyId": fid, "updatedAt": int(time.time()), "data": clean}
    tmp = _family_backup_path(fid).with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _family_backup_path(fid))  # 原子替换，防写一半断电留坏文件
    except OSError as exc:
        return {"ok": False, "error": f"写入失败：{exc}"}
    return {"ok": True, "updatedAt": payload["updatedAt"]}


class FamilyBackupGetRequest(BaseModel):
    familyId: str = Field(..., min_length=8, max_length=8)


# 读取用 POST 而非 GET——先注册的 GET catch-all（/{path:path} 静态回退）会吞掉后注册的 GET 路由
@app.post("/api/family-backup/get")
async def get_family_backup(req: FamilyBackupGetRequest) -> Dict[str, Any]:
    fid = (req.familyId or "").strip().upper()
    if not _FAMILY_BACKUP_ID_RE.match(fid):
        return {"ok": False, "error": "家庭码格式不正确"}
    path = _family_backup_path(fid)
    if not path.exists():
        return {"ok": False, "error": "无云端备份"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "备份文件损坏"}
    return {"ok": True, "updatedAt": payload.get("updatedAt", 0), "data": payload.get("data", {})}


# ========== V188: 意见反馈收集（测试期轻量方案，追加写 jsonl） ==========
_FEEDBACK_PATH = Path(__file__).resolve().parent / "data" / "feedback.jsonl"


class FeedbackRequest(BaseModel):
    content: str = Field(..., max_length=2000)
    contact: str = Field(default="", max_length=100)
    familyId: str = Field(default="", max_length=20)
    page: str = Field(default="", max_length=300)
    appVer: str = Field(default="", max_length=40)
    ua: str = Field(default="", max_length=300)


@app.post("/api/feedback")
async def save_feedback(req: FeedbackRequest) -> Dict[str, Any]:
    content = req.content.strip()
    if not content:
        return {"ok": False, "error": "反馈内容为空"}
    record = {
        "ts": _dt_cls.now().strftime("%Y-%m-%d %H:%M:%S"),
        "familyId": req.familyId.strip().upper(),
        "contact": req.contact.strip(),
        "page": req.page.strip(),
        "appVer": req.appVer.strip(),
        "ua": req.ua.strip(),
        "content": content,
    }
    try:
        _FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_FEEDBACK_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        return {"ok": False, "error": f"写入失败：{exc}"}
    return {"ok": True}


# ========== V224: 复盘「治未病风险预警」AI 归因（POST /api/review-risk） ==========
class ReviewRiskRequest(BaseModel):
    date: str = ""
    familyId: str = "default"
    familyBrief: str = ""
    weekStats: Dict[str, Any] = {}
    dishes: List[str] = []


_REVIEW_RISK_SYSTEM = """你是一名预防医学营养师。你的任务是找出用户饮食中的风险点，并用科学预防的角度提示。

【强制约束】
1. 禁止使用“治疗”“治愈”“导致疾病”“远离疾病”等词汇。
2. 必须使用“增加风险”“不利于指标稳定”“负担加重”等预防性科普词汇。
3. 例如：不能写“吃多了会得高血压”，必须写“长期高盐摄入容易增加心血管压力，建议控盐”。
4. 只做饮食结构分析与生活方式建议，不做诊断，不推荐药物或保健品。

【输出要求】只返回一个 JSON 对象，不要代码块，不要任何多余说明：
{"riskLevel":"warning","issue":"钠摄入偏高","scientificAdvice":"长期高钠不利于血压稳定，建议下周尝试用香料代替盐","callExpertSuggest":true}
riskLevel 只能取 ok / warning / danger；issue 不超过 12 个字；scientificAdvice 控制在 40-80 字；callExpertSuggest 为布尔值。"""

_REVIEW_RISK_BAD_WORDS = ["治疗", "治愈", "导致疾病", "远离疾病", "包治", "药到病除", "诊断", "处方"]
_REVIEW_RISK_CACHE: Dict[str, Any] = {}


def _clean_review_risk(parsed: Dict[str, Any]) -> Dict[str, Any]:
    level = str(parsed.get("riskLevel") or "warning").strip().lower()
    if level not in ("ok", "warning", "danger"):
        level = "warning"
    issue = str(parsed.get("issue") or "").strip()[:20]
    advice = str(parsed.get("scientificAdvice") or "").strip()[:200]
    for w in _REVIEW_RISK_BAD_WORDS:
        if w in advice:
            advice = advice.replace(w, "")
    for w in _REVIEW_RISK_BAD_WORDS[:2]:
        if w in issue:
            issue = issue.replace(w, "")
    expert = parsed.get("callExpertSuggest")
    if isinstance(expert, str):
        expert = expert.strip().lower() in ("true", "1", "yes", "是")
    return {
        "riskLevel": level,
        "issue": issue,
        "scientificAdvice": advice,
        "callExpertSuggest": bool(expert),
    }


@app.post("/api/review-risk")
async def review_risk(req: ReviewRiskRequest) -> Dict[str, Any]:
    """V224: 依本周饮食统计做风险归因，返回预防医学口径的 JSON。"""
    date_str = (req.date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        date_str = _date_cls.today().strftime("%Y-%m-%d")
    family_id = (req.familyId or "default").strip()[:32]
    st = req.weekStats or {}
    sig = "|".join(str(st.get(k, "")) for k in ("days", "total", "oilCount", "saltCount", "sugarCount", "vegCount"))
    cache_key = f"{family_id}|{date_str}|{sig}"
    hit = _REVIEW_RISK_CACHE.get(cache_key)
    if hit and time.time() <= hit.get("expire", 0):
        return hit["data"]

    dishes = [str(d).strip() for d in (req.dishes or []) if str(d).strip()][:30]
    user_lines = [
        f"【统计周期】最近 7 天，有记录 {st.get('days', 0)} 天，共 {st.get('total', 0)} 道菜",
        f"【高油菜品】{st.get('oilCount', 0)} 道（占比 {round(float(st.get('oilRatio', 0) or 0) * 100)}%）",
        f"【高盐菜品】{st.get('saltCount', 0)} 道（占比 {round(float(st.get('saltRatio', 0) or 0) * 100)}%）",
        f"【高糖菜品】{st.get('sugarCount', 0)} 道（占比 {round(float(st.get('sugarRatio', 0) or 0) * 100)}%）",
        f"【蔬菜类】{st.get('vegCount', 0)} 道",
        f"【家庭情况】{(req.familyBrief or '').strip()[:200] or '普通家庭'}",
        f"【本周菜品】{'、'.join(dishes) if dishes else '未记录'}",
    ]
    result = None
    for attempt in range(2):
        try:
            content = await call_qwen(
                [
                    {"role": "system", "content": _REVIEW_RISK_SYSTEM},
                    {"role": "user", "content": "\n".join(user_lines)},
                ],
                json_mode=True,
                max_tokens=400,
                task_type="review_risk",
            )
        except Exception as e:
            print(f"[review-risk] call_qwen error (attempt {attempt + 1}): {e}")
            continue
        try:
            parsed = extract_json_object(content)
        except Exception as e:
            print(f"[review-risk] json parse error (attempt {attempt + 1}): {e}")
            continue
        if not isinstance(parsed, dict) or not parsed.get("issue"):
            continue
        cand = _clean_review_risk(parsed)
        if not cand["scientificAdvice"]:
            continue
        result = cand
        break

    if result is None:
        return {"ok": False, "error": "风险归因生成失败"}

    data = {"ok": True, **result}
    _REVIEW_RISK_CACHE[cache_key] = {"expire": time.time() + 6 * 3600, "data": data}
    return data


# ========== V225: AI 专家营养师评估（POST /api/expert-assess） ==========
class ExpertAssessRequest(BaseModel):
    familyId: str = ""
    familyBrief: str = ""
    imageBase64: str = ""   # 可带 data:image/...;base64, 前缀
    userPrompt: str = ""
    mode: str = "ai"        # ai（默认）；human 为未来真人营养师接入预留


_EXPERT_DISCLAIMER = "本评估由 AI 营养模型生成，仅供日常膳食参考，不能替代医生临床诊断。慢病及用药调整请务必遵医嘱。"

_EXPERT_SYSTEM = """你是高级临床营养师。请分析用户上传的体检报告并给出膳食建议。

【红线要求】
1. 严禁做疾病诊断！
2. 严禁说“吃什么药”！
3. 严禁使用“治疗/治愈”词汇！
4. 不得承诺疗效，不得推荐药品、保健品或替代治疗方案。
5. 只从膳食结构、烹饪方式、食材选择、作息与运动等生活方式角度给建议。
6. 涉及指标异常时，用“提示关注”“建议复查”“遵医嘱”等表述。

【输出要求】
输出结构清晰的中文报告，依次包含：
一、报告要点解读（只描述指标与饮食的关联，不下结论）
二、饮食原则
三、一日三餐搭配建议
四、需要留意的提醒
全文控制在 700 字以内，不使用 Markdown 表格。"""

_EXPERT_BAD_WORDS = ["治疗", "治愈", "导致疾病", "远离疾病", "包治", "药到病除", "吃药", "处方药", "停药"]


@app.post("/api/expert-assess")
async def expert_assess(req: ExpertAssessRequest) -> Dict[str, Any]:
    """V225: 体检单 + 问题描述 → AI 专家营养师膳食评估（带合规免责声明）。"""
    prompt = (req.userPrompt or "").strip()[:600]
    brief = (req.familyBrief or "").strip()[:300]
    img = (req.imageBase64 or "").strip()
    if not prompt and not img:
        return {"ok": False, "error": "请上传体检单或描述需要关注的问题"}

    if img and not img.startswith("data:"):
        img = "data:image/jpeg;base64," + img
    if img and len(img) > 11 * 1024 * 1024:
        return {"ok": False, "error": "图片过大（超过 8MB），请压缩后重试"}

    user_lines = [
        "【家庭情况】" + (brief or "未提供"),
        "【用户关注】" + (prompt or "请根据体检单给出日常膳食建议"),
        "请按输出要求生成膳食评估报告。",
    ]
    messages: List[Dict[str, Any]] = [{"role": "system", "content": _EXPERT_SYSTEM}]
    if img:
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": img}},
                {"type": "text", "text": "\n".join(user_lines)},
            ],
        })
    else:
        messages.append({"role": "user", "content": "\n".join(user_lines)})

    result = ""
    used_vision = False
    try:
        if img:
            result, _p = await route_vision(messages, max_tokens=2000, timeout=120.0, task_type="expert_assess")
            used_vision = True
        else:
            result = await call_qwen(messages, max_tokens=2000, task_type="expert_assess")
    except Exception as e:
        print(f"[expert-assess] AI 调用失败：{e}")
        return {"ok": False, "error": "AI 评估服务暂时不可用，请稍后重试"}

    text = str(result or "").strip()
    if not text:
        return {"ok": False, "error": "AI 未能生成评估内容，请补充问题描述后重试"}
    for w in _EXPERT_BAD_WORDS:
        if w in text:
            text = text.replace(w, "")
    text = text.replace("**", "").strip()
    if not text.startswith(_EXPERT_DISCLAIMER):
        text = _EXPERT_DISCLAIMER + "\n\n" + text
    if not text.endswith(_EXPERT_DISCLAIMER):
        text = text + "\n\n" + _EXPERT_DISCLAIMER

    return {"ok": True, "result": text, "usedVision": used_vision, "mode": "ai"}


# ========== V226: 全局错误日志（后端异常拦截 + 前端错误上报） ==========
import traceback as _tb
import logging
import logging.handlers

_ERR_LOG_DIR = Path(__file__).parent / "data"
_ERR_LOG_FILE = _ERR_LOG_DIR / "system_error.log"
_ERR_LOG_DIR.mkdir(parents=True, exist_ok=True)

_err_logger = logging.getLogger("jiayaoji.error")
if not _err_logger.handlers:
    _err_logger.setLevel(logging.ERROR)
    try:
        _err_handler = logging.handlers.RotatingFileHandler(
            str(_ERR_LOG_FILE), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        _err_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _err_logger.addHandler(_err_handler)
    except Exception as _e:  # 只读文件系统等场景降级为仅控制台
        print(f"[error-log] 日志文件不可写，降级为控制台输出：{_e}")
    _err_logger.propagate = False

_ERR_TYPES = {"api_timeout", "ai_error", "frontend_crash", "payment_error"}


def _err_new_id() -> str:
    return "err_" + time.strftime("%Y%m%d%H%M%S") + "_" + _secrets.token_hex(3)


def _err_write_line(payload: Dict[str, Any]) -> str:
    """追加一条 JSON 行到 system_error.log（同时写 logger），返回 errorId。"""
    error_id = str(payload.get("errorId") or _err_new_id())
    payload["errorId"] = error_id
    line = json.dumps(payload, ensure_ascii=False)
    try:
        _err_logger.error(line)
    except Exception:
        print("[error-log] " + line)


class SystemErrorReport(BaseModel):
    id: str = ""
    type: str = "api_timeout"
    message: str = ""
    familyId: str = ""
    familyName: str = ""
    createdAt: int = 0
    url: str = ""
    detail: str = ""


@app.post("/api/system-error")
async def report_system_error(req: SystemErrorReport, request: Request) -> Dict[str, Any]:
    """V226: 接收前端错误上报，落盘到 data/system_error.log（不落客户端 localStorage）。"""
    etype = (req.type or "").strip()
    if etype not in _ERR_TYPES:
        etype = "api_timeout"
    payload = {
        "source": "frontend",
        "type": etype,
        "message": (req.message or "").strip()[:500],
        "familyId": (req.familyId or "").strip()[:64],
        "familyName": (req.familyName or "").strip()[:64],
        "clientCreatedAt": int(req.createdAt or 0),
        "url": (req.url or "").strip()[:300],
        "detail": (req.detail or "").strip()[:1000],
        "ip": (request.client.host if request.client else ""),
        "ua": (request.headers.get("user-agent") or "")[:200],
    }
    _err_write_line(payload)
    return {"ok": True, "errorId": payload["errorId"]}


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """V226: 未捕获异常统一拦截——记录 traceback，返回带 errorId 的 500。"""
    payload = {
        "source": "backend",
        "type": "api_timeout" if isinstance(exc, (TimeoutError,)) else "ai_error" if "ai" in request.url.path else "frontend_crash",
        "message": f"{type(exc).__name__}: {exc}"[:500],
        "path": request.url.path,
        "method": request.method,
        "detail": _tb.format_exc()[:3000],
    }
    _err_write_line(payload)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "服务异常，请稍后重试", "errorId": payload["errorId"]},
    )


@app.middleware("http")
async def _error_status_middleware(request: Request, call_next):
    """V226: 记录 5xx 与 429（限流）响应；异常本身由上面的 exception_handler 兜底。"""
    started = time.time()
    try:
        response = await call_next(request)
    except Exception:
        # 交给 exception_handler（此处不吞异常，重新抛出）
        raise
    if response.status_code >= 500 or response.status_code == 429:
        _err_write_line({
            "source": "backend",
            "type": "ai_error" if "ai" in request.url.path else "api_timeout",
            "message": f"HTTP {response.status_code} {request.method} {request.url.path}",
            "path": request.url.path,
            "method": request.method,
            "status": response.status_code,
            "elapsedMs": int((time.time() - started) * 1000),
        })
    return response


# ========== V227: 超管「系统监控」（错误总览 + 活跃付费客户数） ==========
_ERR_RESOLVED_FILE = _ERR_LOG_DIR / "error_resolved.json"


class ErrorResolveRequest(BaseModel):
    errorIds: List[str] = []


def _err_load_entries(limit: int = 300) -> List[Dict[str, Any]]:
    """从 system_error.log 里解析最近的 JSON 行（倒序）。"""
    if not _ERR_LOG_FILE.exists():
        return []
    try:
        lines = _ERR_LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    items: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        idx = line.find('{"')
        if idx < 0:
            continue
        try:
            obj = json.loads(line[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            # 记录日志行前缀时间（后端直接产生、没有 clientCreatedAt 的记录靠它归日）
            obj["_logTime"] = line[:idx].strip()[:19]
            items.append(obj)
    items.reverse()
    return items


def _err_load_resolved() -> set:
    try:
        if _ERR_RESOLVED_FILE.exists():
            data = json.loads(_ERR_RESOLVED_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(x) for x in data}
    except (OSError, json.JSONDecodeError):
        pass
    return set()


def _err_save_resolved(ids: set) -> None:
    try:
        _ERR_RESOLVED_FILE.write_text(json.dumps(sorted(ids), ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        print(f"[admin-monitor] 写入解决状态失败：{e}")


def _count_active_paid() -> int:
    """扫描家庭云备份里的 subscriptionStatus，统计未到期的付费家庭数（premium + basic）。"""
    if not _FAMILY_BACKUP_DIR.exists():
        return 0
    now = int(time.time())
    count = 0
    for p in _FAMILY_BACKUP_DIR.glob("*.json"):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            raw = (payload.get("data") or {}).get("subscriptionStatus")
            if not raw or not isinstance(raw, str):
                continue
            sub = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(sub, dict) or not sub.get("isSubscribed"):
            continue
        if str(sub.get("tier") or sub.get("planType") or "") not in ("premium", "basic"):
            continue
        expire = int(sub.get("expireAt") or sub.get("expireDate") or 0)
        if expire and expire < now:
            continue
        count += 1
    return count


@app.post("/api/admin/monitor")
async def admin_monitor() -> Dict[str, Any]:
    """V227: 返回今日报错总数、活跃付费客户数、未处理报警列表。"""
    resolved = _err_load_resolved()
    today = _date_cls.today().strftime("%Y-%m-%d")
    entries = _err_load_entries(300)
    today_count = 0
    unresolved: List[Dict[str, Any]] = []
    for obj in entries:
        ts = int(obj.get("clientCreatedAt") or 0)
        ts_str = ""
        if ts > 0:
            ts_str = _dt_cls.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
            if ts_str.startswith(today):
                today_count += 1
        elif str(obj.get("_logTime") or "").startswith(today):
            ts_str = str(obj.get("_logTime") or "")[:16]
            today_count += 1
        eid = str(obj.get("errorId") or "")
        if eid and eid in resolved:
            continue
        if len(unresolved) >= 100:
            continue
        unresolved.append({
            "id": eid,
            "type": str(obj.get("type") or "api_timeout"),
            "message": str(obj.get("message") or ""),
            "familyId": str(obj.get("familyId") or ""),
            "familyName": str(obj.get("familyName") or ""),
            "source": str(obj.get("source") or "frontend"),
            "createdAt": ts,
            "timeText": ts_str,
            "resolved": False,
        })
    return {
        "ok": True,
        "todayErrorCount": today_count,
        "totalErrorCount": len(entries),
        "activePaidCount": _count_active_paid(),
        "unresolvedCount": len(unresolved),
        "unresolved": unresolved,
    }


@app.post("/api/admin/error-resolve")
async def admin_error_resolve(req: ErrorResolveRequest) -> Dict[str, Any]:
    """V227: 将报警标记为已处理（errorId 列表）。"""
    ids = [str(x).strip() for x in (req.errorIds or []) if str(x).strip()]
    if not ids:
        return {"ok": False, "error": "缺少 errorIds"}
    resolved = _err_load_resolved()
    resolved.update(ids)
    _err_save_resolved(resolved)
    return {"ok": True, "resolvedCount": len(resolved)}


# V157: 文件末尾补跑别名注册——覆盖定义在本块之后的 /api/daily-tip、/api/weather、
# /api/migration 等 POST 路由（catch-all 仅 GET，POST 别名无被吞风险；已注册路径自动去重）
_register_api_prefix_aliases()

