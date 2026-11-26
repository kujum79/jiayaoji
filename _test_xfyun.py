"""端到端测试讯飞 ASR：生成测试 PCM → POST /api/asr → 看能否返回文本。
注意：这段代码本身不直接调讯飞，而是调我们自己后端的 /api/asr，
等于完整走通「前端录音 → 后端 → 讯飞 → 返回文本」链路。"""
import requests, wave, struct, random, io, sys, os, time

# 先验证后端配置是否生效
r = requests.post("https://jiayaoji.cn/api/asr", data=b"", timeout=10)
print(f"空请求响应: {r.status_code} {r.text[:120]}")
if r.status_code == 503:
    print("❌ 后端仍报 503 语音服务未配置，检查 .env 是否正确")
    sys.exit(1)
if "short" in r.text or "太短" in r.text:
    print("✅ 后端已接受请求并返回业务错误（不是 503），说明密钥链路 OK")
else:
    print(f"后端响应: {r.text[:200]}")

# 用 webrtcvad 的测试音频或者简单生成一段"静音 PCM"先测鉴权
# 讯飞鉴权通过但内容不对时，会返回 "没听清" 或错误码，不会返回 503/502
pcm = bytes([random.randint(0, 255) for _ in range(16000 * 2 * 2)])  # 2秒噪声
r2 = requests.post("https://jiayaoji.cn/api/asr", data=pcm, timeout=30)
print(f"\n2秒噪声测试: status={r2.status_code}, body={r2.text[:200]}")

# 读取最近的后端日志看讯飞返回了什么
print("\n=== 后端日志最后5行 ===")
