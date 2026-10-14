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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

import httpx
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
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
    "【天气】{weather}\n"
    "\n"
    "生成要求：\n"
    '1. 3 篇内容主题尽量分散：1 篇挂靠节气或天气（category 用"节气食养"或"天气食养"）；'
    '1 篇讲当季食材（category 用"当季食材"）；1 篇讲烹饪技巧或饮食文化（category 用"烹饪技巧"或"饮食文化"）\n'
    "2. 每篇：标题 15 字内、摘要 60 字内、正文 200-300 字、小贴士 2 条（每条 25 字内）\n"
    "3. 语言口语化，像和朋友聊天\n"
    '4. 可结合近期饮食话题（如"秋燥"）\n'
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
    """
    weather_desc = "未知"
    if isinstance(weather, dict) and weather:
        parts = []
        if weather.get("textDay"):
            parts.append(str(weather["textDay"]))
        if weather.get("tempMax") is not None and weather.get("tempMin") is not None:
            parts.append(f"{weather['tempMin']}~{weather['tempMax']}℃")
        if parts:
            weather_desc = "，".join(parts)

    system_prompt = _FOODHALL_SYSTEM.format(
        date=date, solar_term=solar_term or "无",
        weather=f"{city or ''} {weather_desc}".strip(),
    )
    user_prompt = f"日期：{date}；节气：{solar_term or '无'}；城市：{city or '未提供'}。请生成 3 篇食养堂候选内容。"

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
                "AND title<>'' AND COALESCE(review_status,'')<>'withdrawn'",
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
    return score


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
    """V144: 公共文库·食养堂归档——近 N 天已发布（含超管确认/待复核，不含撤回）。

    只返回 publish_date <= 今天 的记录，避免提前泄露次日预生成稿。
    """
    days = max(1, min(int(days or 120), 366))
    today_s = _date_cls.today().strftime("%Y-%m-%d")
    start_s = (_date_cls.today() - _td_cls(days=days - 1)).strftime("%Y-%m-%d")
    try:
        with _fh_db() as conn:
            rows = conn.execute(
                "SELECT * FROM food_hall_pub_days WHERE title<>'' "
                "AND COALESCE(review_status,'')<>'withdrawn' "
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


@app.post("/api/daily-tip")
async def daily_tip(req: DailyTipRequest) -> Dict[str, Any]:
    """食养堂每日一课：按 节气>天气>当季食材 优先级生成，同家庭同日仅生成一次。"""
    date_str = (req.date or "").strip()
    family_id = (req.familyId or "default").strip()[:32]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return {"error": "日期格式应为 YYYY-MM-DD"}

    cache_key = f"{family_id}|{date_str}|{max(0, int(req.variant))}"
    hit = _DAILY_TIP_CACHE.get(cache_key)
    now_ts = time.time()
    if hit:
        if now_ts <= hit.get("expire", 0):
            return hit["data"]
        _DAILY_TIP_CACHE.pop(cache_key, None)  # 过期清理

    # V143: 首屏（variant=0）优先返回前一天 22:30 流水线预选入库的内容，
    # 早高峰零 AI 延迟；换一换(variant>0)仍走实时生成
    if req.variant == 0:
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

    user_lines = [
        f"【日期】{date_str}",
        f"【节气】{req.solarTerm.strip() or '不在节气当天'}",
        f"【天气】{weather_line}",
        f"【城市】{(req.city or '').strip() or '未提供'}",
        f"【家庭】{(req.familyBrief or '').strip()[:200] or '普通家庭'}",
    ]
    if req.variant > 0:
        user_lines.append(f"【要求】这是该家庭今天看到的第 {req.variant + 1} 条，请换一个与之前完全不同的选题角度（如不同食材、烹饪技巧、节气习俗），标题和内容都不要与之前重复。")
    try:
        content = await call_qwen(
            [
                {"role": "system", "content": _DAILY_TIP_SYSTEM},
                {"role": "user", "content": "\n".join(user_lines)},
            ],
            json_mode=True,
            max_tokens=1200,
            task_type="daily_tip",
        )
    except Exception as e:  # AI 不可用不阻塞：返回降级提示
        print(f"[daily-tip] call_qwen error: {e}")
        return {"error": "每日一课生成失败，请稍后重试"}

    parsed = extract_json_object(content)
    if not isinstance(parsed, dict) or not parsed.get("title"):
        return {"error": "每日一课生成失败，请稍后重试"}

    tip = _clean_daily_tip(parsed)
    if not tip["title"] or not tip["content"]:
        return {"error": "每日一课生成失败，请稍后重试"}

    result = {"id": f"tip_{date_str.replace('-', '')}", "date": date_str, **tip}
    _DAILY_TIP_CACHE[cache_key] = {"expire": _daily_tip_expire_ts(date_str), "data": result}
    # 防膨胀：最多缓存 120 个 家庭|日期 条目，超出淘汰最先过期者
    if len(_DAILY_TIP_CACHE) > 120:
        oldest = min(_DAILY_TIP_CACHE.items(), key=lambda kv: kv[1].get("expire", 0))[0]
        _DAILY_TIP_CACHE.pop(oldest, None)
    return result

