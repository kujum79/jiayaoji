import requests, random, time, sys

# 等限速冷却 3 秒
time.sleep(3)

# 测试 3 段 PCM（噪声模拟，讯飞会返回"没听清"或某段噪声误识别，不会 502/503）
pcm = bytes([random.randint(0, 255) for _ in range(16000 * 2 * 3)])  # 3秒
r = requests.post("https://jiayaoji.cn/api/asr", data=pcm, timeout=30)
print(f"3秒噪声: status={r.status_code}, body={r.text[:300]}")

# 如果上面还是 429，说明前端请求太频繁被挡
if "429" in r.text:
    time.sleep(3)
    r2 = requests.post("https://jiayaoji.cn/api/asr", data=pcm, timeout=30)
    print(f"重试后: status={r2.status_code}, body={r2.text[:300]}")
