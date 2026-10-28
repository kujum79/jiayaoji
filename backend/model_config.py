"""多模型路由层：统一管理多个 AI 服务商的配置与调用。

- 配置：PROVIDERS（按 priority 升序即调用优先级）
- 路由：route_chat() 按优先级依次尝试「配置了 API Key」的服务商，
  单家失败（无 Key/超时/HTTP 错误）自动降级到下一家，全部失败才抛异常。
- 通用任务：call_ai(task_type, payload) 支持 text / image / video 等任务类型路由，
  每次调用经 log_ai_call 记录 tokens 到 ai_logs.jsonl（成本分析）。
- 协议：所有服务商统一走 OpenAI 兼容协议（chat/completions、{task}/generations）。
  因此 dashscope 的 base_url 用 compatible-mode/v1（原生 Generation API 不兼容该协议）。

环境变量（也可沿用旧名 ALIYUN_API_KEY）：
    AGNES_API_KEY / SILICONFLOW_API_KEY / DASHSCOPE_API_KEY / DEEPSEEK_API_KEY
    可写在 backend/.env（无需 python-dotenv，启动时自动加载，已有环境变量优先）。
"""
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx


def _load_env_file() -> None:
    """轻量加载 backend/.env（KEY=VALUE，# 注释，已有环境变量优先）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_env_file()

# AI 调用日志文件（JSONL，每行一条）
AI_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_logs.jsonl")

# 模型服务商配置（priority 越小越优先；cost 为每 token 单价，元）
# V119: Agnes 免费模型为首选（apihub 域名），文本先 flash 后 pro；图像/视频端点与 OpenAI 风格有差异，用 *_path 覆盖。
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "agnes": {
        "base_url": "https://apihub.agnes-ai.com/v1",
        "api_key": os.getenv("AGNES_API_KEY", ""),
        "models": {
            "text_fast": "agnes-2.5-flash",   # 日常对话/推荐菜谱/食材知识
            # V137: 原为 agnes-2.5-pro，该 Key 对 pro 模型持续 403（从未成功），强档降为同 flash，省一次必败尝试
            "text_pro": "agnes-2.5-flash",
            "vision": ["agnes-2.5-flash", "agnes-3.0-flash"],  # V131: 视觉理解（image_url 多模态输入，免费flash档实测支持）
            "image": "agnes-image-2.1-flash",
            "video": "agnes-video-2.5",
        },
        "image_path": "/images/generations",
        "video_path": "/videos",
        # V139: agnes-2.5-flash 为推理模型，思维链(reasoning_content)不受控会耗尽全部 max_tokens
        # 导致 content 为空（finish_reason=length，reasoning_tokens=8192/text_tokens=0）。
        # 压制到 low 后 reasoning_tokens≈75，正文正常输出（实测 4 种参数变体均有效，此参数最省额度）。
        "chat_extra": {"reasoning_effort": "low"},
        "priority": 1,
        "cost": 0,  # 免费
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": os.getenv("SILICONFLOW_API_KEY", ""),
        "models": {
            "text": "deepseek-ai/DeepSeek-V4-Pro",
            "text_fast": "deepseek-ai/DeepSeek-V4-Flash",
        },
        "priority": 2,
        "cost": 0.0000001,  # 约 0.1元/百万token
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        # 兼容旧环境变量名 ALIYUN_API_KEY（main.py 历史用法）
        "api_key": os.getenv("DASHSCOPE_API_KEY", "") or os.getenv("ALIYUN_API_KEY", ""),
        "models": {
            "text": "qwen-turbo",
            "vision": "qwen-vl-plus",  # V131: 视觉理解兜底（宣传单OCR等图片输入任务）
        },
        "priority": 3,
        "cost": 0.0000003,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "models": {
            "text": "deepseek-chat",
        },
        "priority": 4,
        "cost": 0.0000005,
    },
    # V119: 图像兜底（免费无 Key 公共服务，GET /prompt/{prompt} 返回图片二进制）
    "pollinations": {
        "base_url": "https://image.pollinations.ai",
        "api_key": "",
        "public": True,  # 无需 Key 也参与路由（仅图像任务）
        "models": {
            "image": "flux",
        },
        "priority": 5,
        "cost": 0,
        "image_path": "/prompt/{prompt}",  # 占位符由调用分支替换
    },
}


# 按优先级排序
def get_provider_order() -> List[Tuple[str, Dict[str, Any]]]:
    return sorted(PROVIDERS.items(), key=lambda x: x[1]["priority"])


# V119: 场景路由——以下 task_type 用强档模型（审核评分/营养配餐/复杂推理），其余走快速档
PRO_MODEL_TASKS = {"audit", "nutrition_plan"}


def _text_model_sequence(cfg: Dict[str, Any], model_kind: str = "text") -> List[str]:
    """服务商的文本模型尝试序列（V119 同服务商内部先快后强降级）。

    - model_kind="text"：agnes → [flash, pro]；仅有 text 键的服务商 → [text]（行为不变）
    - model_kind="text_pro"（审核/配餐）：agnes → [pro, flash]
    """
    models = cfg.get("models", {})
    order = ["text_pro", "text_fast"] if model_kind == "text_pro" else ["text_fast", "text_pro"]
    seq: List[str] = []
    primary = models.get(model_kind) or models.get("text")
    if primary:
        seq.append(primary)
    for key in order:
        m = models.get(key)
        if m and m not in seq:
            seq.append(m)
    return seq


def get_provider_status() -> Dict[str, Dict[str, Any]]:
    """调试/展示用：每个服务商是否配置了 Key、当前优先级与模型。"""
    return {
        name: {
            "priority": cfg["priority"],
            "has_key": bool(cfg["api_key"]),
            "text_model": cfg["models"].get("text", ""),
            "cost": cfg["cost"],
        }
        for name, cfg in get_provider_order()
    }


async def route_chat(
    messages: List[Dict[str, str]],
    *,
    model_kind: str = "text",
    json_mode: bool = False,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    timeout: float = 60.0,
) -> Tuple[str, str]:
    """按优先级路由一次对话补全，失败自动降级下一家。

    返回 (回复文本, 实际使用的服务商名)。全部服务商不可用时抛 RuntimeError。
    model_kind：模型用途，如 "text"（质量优先）/"text_fast"（速度优先，
    仅配置了该档的服务商支持，缺失时回落到 "text"）。
    """
    errors: List[str] = []
    for name, cfg in get_provider_order():
        api_key = cfg.get("api_key") or ""
        # V119: 同服务商内部先快后强逐个尝试（agnes: flash→pro），失败再降级下一家
        model_seq = _text_model_sequence(cfg, model_kind)
        if not api_key or not model_seq:
            errors.append(f"{name}: 未配置 Key 或模型")
            continue
        last_err: Optional[str] = None
        for model in model_seq:
            try:
                data = await _call_provider_chat(
                    name, cfg, model, messages,
                    json_mode=json_mode, max_tokens=max_tokens,
                    temperature=temperature, timeout=timeout,
                )
                content = data["choices"][0]["message"]["content"]
                if content:
                    return content, name
                last_err = "返回空内容"
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                log_ai_error(name, model, {"task_type": "chat"}, exc)
        errors.append(f"{name}: {last_err or '全部模型失败'}")
    raise RuntimeError("所有模型服务商均调用失败 -> " + "；".join(errors))


async def route_vision(
    messages: List[Dict[str, Any]],
    *,
    max_tokens: Optional[int] = None,
    timeout: float = 90.0,
    task_type: str = "vision",
) -> Tuple[str, str]:
    """V131: 视觉理解（图片输入）路由——按优先级尝试配置了 vision 模型的服务商，失败自动降级。

    agnes: agnes-2.5-flash / agnes-3.0-flash（免费档实测支持 image_url 多模态）；
    dashscope: qwen-vl-plus 兜底。messages 的 content 可为 OpenAI 多模态数组。
    返回 (回复文本, 服务商名)。全部失败抛 RuntimeError。
    """
    errors: List[str] = []
    for name, cfg in get_provider_order():
        vision_models = cfg.get("models", {}).get("vision")
        api_key = cfg.get("api_key") or ""
        if not vision_models:
            continue
        if not api_key:
            errors.append(f"{name}: 未配置 Key")
            continue
        model_seq: List[str] = vision_models if isinstance(vision_models, list) else [vision_models]
        last_err: Optional[str] = None
        for model in model_seq:
            try:
                data = await _call_provider_chat(name, cfg, model, messages, max_tokens=max_tokens, timeout=timeout)
                content = data["choices"][0]["message"]["content"]
                if content:
                    log_ai_call(name, model, {"task_type": task_type}, data)
                    return content, name
                last_err = "返回空内容"
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                log_ai_error(name, model, {"task_type": task_type}, exc)
        errors.append(f"{name}: {last_err or '全部模型失败'}")
    raise RuntimeError("所有视觉模型服务商均调用失败 -> " + "；".join(errors))


# ========== 通用任务路由（text / image / video）与调用日志 ==========
def _call_provider_chat(
    name: str,
    cfg: Dict[str, Any],
    model: str,
    messages: List[Dict[str, Any]],
    *,
    json_mode: bool = False,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    timeout: float = 60.0,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """单服务商 chat/completions 调用（同步发起由调用方 await），返回完整响应 JSON。

    extra_body：按服务商透传的附加参数（如 agnes 的 reasoning_effort），仅文本链路传入，
    视觉链路不带（避免多模态模型不认识该参数被拒）。
    """
    payload: Dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature
    if extra_body:
        payload.update(extra_body)

    async def _post() -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{cfg['base_url'].rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.get('api_key') or ''}"},
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    return _post()


def save_ai_log(log_entry: Dict[str, Any]) -> None:
    """追加写入 JSONL 日志文件（后端无 localStorage，文件即最轻存储，便于成本分析）。"""
    try:
        with open(AI_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"save_ai_log failed: {exc}")


def log_ai_call(provider: str, model: str, payload: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """记录每次 AI 调用，便于成本分析（tokens 来自响应 usage）。"""
    usage = result.get("usage", {}) if isinstance(result, dict) else {}
    log_entry = {
        "provider": provider,
        "model": model,
        "tokens": usage.get("total_tokens", 0),
        "timestamp": time.time(),
        "task": payload.get("task_type", ""),
    }
    save_ai_log(log_entry)
    return log_entry


def log_ai_error(provider: str, model: str, payload: Dict[str, Any], error: Any) -> None:
    """V132: 记录 AI 调用失败（tokens=0 + error 字段），供用量面板异常监控与超管提醒。"""
    entry = {
        "provider": provider,
        "model": model,
        "tokens": 0,
        "timestamp": time.time(),
        "task": str(payload.get("task_type", "") or ""),
        "error": str(error)[:200],
    }
    save_ai_log(entry)


# ========== V118: 月度预算与用量监控 ==========
BUDGET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_budget.json")


def load_budget() -> Dict[str, Any]:
    """读取月度预算（元）；0 = 未设置不限制。"""
    try:
        with open(BUDGET_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {"monthly": float(data.get("monthly", 0) or 0)}
    except (OSError, ValueError):
        return {"monthly": 0}


def save_budget(monthly: float) -> None:
    try:
        with open(BUDGET_PATH, "w", encoding="utf-8") as f:
            json.dump({"monthly": float(monthly)}, f, ensure_ascii=False)
    except OSError as exc:
        print(f"save_budget failed: {exc}")


def _month_key(ts: float) -> str:
    t = time.localtime(float(ts or 0))
    return f"{t.tm_year:04d}-{t.tm_mon:02d}"


def _read_ai_logs() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if not os.path.exists(AI_LOG_PATH):
        return entries
    try:
        with open(AI_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def _log_entry_cost(entry: Dict[str, Any]) -> float:
    tokens = entry.get("tokens", 0) or 0
    unit = PROVIDERS.get(str(entry.get("provider", "")), {}).get("cost", 0) or 0
    return float(tokens) * float(unit)


def get_ai_cost_summary(month: Optional[str] = None, recent_limit: int = 0) -> Dict[str, Any]:
    """成本统计：读 ai_logs.jsonl 按服务商/任务汇总调用次数、tokens 与估算成本（元）。

    month 形如 "2026-10"；None 统计全部。recent_limit>0 时附时间倒序明细。
    成本 = tokens × PROVIDERS[provider].cost（免费服务商为 0）。
    """
    summary: Dict[str, Any] = {
        "month": month or "all",
        "total_calls": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "total_errors": 0,
        "errors_recent": [],
        "by_provider": {},
        "by_task": {},
        "recent": [],
    }
    entries = _read_ai_logs()
    if month:
        entries = [e for e in entries if _month_key(e.get("timestamp", 0)) == month]
    for entry in entries:
        provider = str(entry.get("provider", "?"))
        task = str(entry.get("task", "?"))
        error = str(entry.get("error") or "")
        if error:
            # V132: 失败调用单独统计（不计入 calls/tokens/cost）
            summary["total_errors"] += 1
            for key, name2 in (("by_provider", provider), ("by_task", task)):
                b2 = summary[key].setdefault(name2, {"calls": 0, "tokens": 0, "cost": 0.0})
                b2["errors"] = b2.get("errors", 0) + 1
            continue
        tokens = entry.get("tokens", 0) or 0
        cost = _log_entry_cost(entry)
        summary["total_calls"] += 1
        summary["total_tokens"] += tokens
        summary["total_cost"] += cost
        for key, name in (("by_provider", provider), ("by_task", task)):
            bucket = summary[key].setdefault(name, {"calls": 0, "tokens": 0, "cost": 0.0})
            bucket["calls"] += 1
            bucket["tokens"] += tokens
            bucket["cost"] += cost
    summary["total_cost"] = round(summary["total_cost"], 6)
    for bucket in summary["by_provider"].values():
        bucket["cost"] = round(bucket["cost"], 6)
    for bucket in summary["by_task"].values():
        bucket["cost"] = round(bucket["cost"], 6)
    if recent_limit > 0 and entries:
        ok_entries = [e for e in entries if not e.get("error")]
        for entry in ok_entries[-recent_limit:][::-1]:
            summary["recent"].append({
                "provider": str(entry.get("provider", "?")),
                "model": str(entry.get("model", "?")),
                "tokens": entry.get("tokens", 0),
                "cost": round(_log_entry_cost(entry), 6),
                "timestamp": entry.get("timestamp", 0),
                "task": str(entry.get("task", "")),
            })
    # V132: 最近 20 条失败明细（时间倒序），供用量面板异常区块与超管提醒
    err_entries = [e for e in entries if e.get("error")]
    if err_entries:
        for entry in err_entries[-20:][::-1]:
            summary["errors_recent"].append({
                "provider": str(entry.get("provider", "?")),
                "model": str(entry.get("model", "?")),
                "task": str(entry.get("task", "")),
                "error": str(entry.get("error", "")),
                "timestamp": entry.get("timestamp", 0),
            })
    return summary


def get_budget_status() -> Dict[str, Any]:
    """本月预算状态：ratio = used/预算 → ok(<80%) / warn(80~100%) / over(≥100%，call_ai 只走免费)。"""
    budget = load_budget().get("monthly", 0)
    month = _month_key(time.time())
    used = get_ai_cost_summary(month=month)["total_cost"]
    ratio = (used / budget) if budget > 0 else 0.0
    status = "ok" if ratio < 0.8 else ("warn" if ratio < 1.0 else "over")
    return {"monthly": budget, "month": month, "used": round(used, 4), "ratio": round(ratio, 4), "status": status}


# ========== V285: 服务商健康熔断 ==========
# 某服务商连续失败达到阈值后，短时间内直接跳过。旧逻辑里 agnes 免费档偶发 ReadTimeout 时，
# 每次请求都要先等满 timeout 才降级，叠加多家服务商后单次请求可拖到 60s+（首页食养堂 1 分钟空白）。
_PROVIDER_HEALTH: Dict[str, Dict[str, float]] = {}
PROVIDER_FAIL_THRESHOLD = 2      # 连续失败次数阈值
PROVIDER_COOLDOWN_SEC = 180.0    # 熔断时长（秒）


def _provider_cooling(name: str) -> bool:
    st = _PROVIDER_HEALTH.get(name) or {}
    return float(st.get("until") or 0) > time.time()


def _mark_provider_fail(name: str) -> None:
    st = _PROVIDER_HEALTH.setdefault(name, {"fails": 0, "until": 0})
    st["fails"] = float(st.get("fails") or 0) + 1
    if st["fails"] >= PROVIDER_FAIL_THRESHOLD:
        st["until"] = time.time() + PROVIDER_COOLDOWN_SEC


def _mark_provider_ok(name: str) -> None:
    _PROVIDER_HEALTH.pop(name, None)


def get_provider_health() -> Dict[str, Dict[str, Any]]:
    """调试用：各服务商当前熔断状态。"""
    now = time.time()
    return {
        name: {
            "cooling": float(st.get("until") or 0) > now,
            "fails": int(st.get("fails") or 0),
            "remaining": max(0, int(float(st.get("until") or 0) - now)),
        }
        for name, st in _PROVIDER_HEALTH.items()
    }


async def call_ai(task_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """多模型路由（通用任务入口）。

    task_type: text / image / video（服务商 models 表需配置对应档位）。
    payload（text）: { messages, json_mode?, max_tokens?, temperature?, timeout?, task_type? }
      - V119 场景路由：payload.task_type ∈ PRO_MODEL_TASKS（audit/nutrition_plan）→ 强档模型
        （agnes: text_pro），其余 → 快速档（agnes: text_flash）；同服务商先快后强，失败降级下一家。
    payload（image/video）: { prompt, ...其他生成参数, timeout? }，另可带 task_type 字段供日志用。
    返回 { provider, model, content?, usage?, result? }；全部失败抛 Exception("All providers failed -> ...")。
    """
    errors: List[str] = []
    # V118: 预算熔断——本月成本达到预算时，只允许免费服务商（80% 提醒由 /ai/usage 状态字段提示）
    restrict_free = False
    try:
        _b = load_budget().get("monthly", 0)
        if _b > 0 and get_budget_status()["used"] >= _b:
            restrict_free = True
    except Exception as _be:
        print(f"budget check failed: {_be}")
    # V119: 文本场景路由档位（audit/nutrition_plan 用强档，其余快速档）
    text_model_kind = "text_pro" if str(payload.get("task_type") or "") in PRO_MODEL_TASKS else "text"
    # V285: 熔断中的服务商直接跳过（全部熔断时兜底按原顺序尝试，避免整体不可用）
    _order = get_provider_order()
    _active = [(n, c) for n, c in _order if not _provider_cooling(n)]
    for name, config in (_active or _order):
        # public 服务商（pollinations）无需 Key 也参与路由
        if not config.get("api_key") and not config.get("public"):
            continue
        if restrict_free and float(config.get("cost", 0) or 0) > 0:
            errors.append(f"{name}: 已达月度预算，跳过付费服务商")
            continue
        # V119: 文本任务按场景档位取模型序列（agnes: [flash, pro] / [pro, flash]），图像/视频单模型
        # 注意：agnes models 只有 text_fast/text_pro（无 text 键），文本任务不能再用键存在性判断
        if task_type == "text":
            model_seq = _text_model_sequence(config, text_model_kind)
            if not model_seq:
                continue
        else:
            if task_type not in config.get("models", {}):
                continue
            model_seq = [config["models"][task_type]]
        for model in model_seq:
            try:
                if task_type == "text":
                    data = await _call_provider_chat(
                        name, config, model,
                        payload.get("messages") or [],
                        json_mode=bool(payload.get("json_mode")),
                        max_tokens=payload.get("max_tokens"),
                        temperature=payload.get("temperature"),
                        timeout=payload.get("timeout", 60.0),
                        extra_body=config.get("chat_extra"),
                    )
                    # V139: 空 content 视为该服务商失败（如推理模型思维链耗尽 max_tokens 时
                    # content=""，finish_reason=length），自动降级下一家，而不是把空串当成功返回
                    _content = data["choices"][0]["message"]["content"]
                    if not (_content or "").strip():
                        _finish = data["choices"][0].get("finish_reason")
                        raise ValueError(f"返回内容为空(finish_reason={_finish})")
                    result: Dict[str, Any] = {
                        "provider": name,
                        "model": model,
                        "content": _content,
                        "usage": data.get("usage", {}),
                    }
                else:
                    # image/video 生成任务：端点可按服务商覆盖（agnes: /images/generations、/videos）
                    path = config.get(f"{task_type}_path") or f"/{task_type}/generations"
                    headers = {"Authorization": f"Bearer {config['api_key']}"} if config.get("api_key") else {}
                    async with httpx.AsyncClient(timeout=payload.get("timeout", 120.0)) as client:
                        if "{prompt}" in path:
                            # V119: pollinations 免费图像兜底（GET /prompt/{prompt} → 图片二进制 → base64）
                            from urllib.parse import quote
                            url = config["base_url"].rstrip("/") + path.replace("{prompt}", quote(str(payload.get("prompt") or "")))
                            resp = await client.get(
                                url, headers=headers,
                                params={"width": payload.get("width", 1024), "height": payload.get("height", 1024), "nologo": "true"},
                            )
                            resp.raise_for_status()
                            import base64 as _b64
                            data = {"data": [{"b64_json": _b64.b64encode(resp.content).decode()}]}
                        else:
                            body = {"model": model}
                            body.update({k: v for k, v in payload.items() if k not in ("task_type", "messages")})
                            resp = await client.post(
                                config["base_url"].rstrip("/") + path,
                                headers=headers,
                                json=body,
                            )
                            resp.raise_for_status()
                            data = resp.json()
                    result = {"provider": name, "model": model, "result": data}
                # 日志缺 task_type 时补当前任务类型（image/video 直调不传 task_type 也能正确归类）
                log_payload = dict(payload)
                log_payload.setdefault("task_type", task_type)
                log_ai_call(name, model, log_payload, result)
                _mark_provider_ok(name)
                return result
            except Exception as e:
                print(f"{name}/{model} failed: {e}")
                errors.append(f"{name}/{model}: {type(e).__name__}: {e}")
                log_ai_error(name, model, payload, e)
                _mark_provider_fail(name)
                continue
    msg = "All providers failed -> " + "；".join(errors)
    if task_type == "video":
        msg = "视频暂不可用（" + msg + "）"
    raise Exception(msg)
