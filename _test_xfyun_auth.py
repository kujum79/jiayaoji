"""直接测试讯飞 IAT WebSocket 鉴权，定位 401 根因。"""
import hmac, hashlib, base64, urllib.parse, asyncio, json, time, random
from email.utils import formatdate
import websockets

# 读 .env
with open('/var/www/jiayaoji/backend/.env') as f:
    for line in f:
        line = line.strip()
        if line.startswith('XFYUN_APPID='): appid = line.split('=', 1)[1]
        elif line.startswith('XFYUN_API_KEY='): api_key = line.split('=', 1)[1]
        elif line.startswith('XFYUN_API_SECRET='): api_secret = line.split('=', 1)[1]

print(f"AppID: {appid}, Key len: {len(api_key)}, Secret len: {len(api_secret)}")

host = "iat-api.xfyun.cn"
path = "/v2/iat"
date = formatdate(usegmt=True)
print(f"Date (RFC1123 GMT): {date}")

origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
print(f"签名原文: {repr(origin[:60])}...")

signature = base64.b64encode(
    hmac.new(api_secret.encode(), origin.encode(), hashlib.sha256).digest()
).decode()
print(f"签名: {signature[:40]}...")

# 方式1: 按现有代码逻辑（双引号不转义）
auth1 = f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
url1 = f"wss://{host}{path}?" + urllib.parse.urlencode({
    "authorization": auth1, "host": host, "date": date
})
print(f"\n方式1 (双引号): {url1[:100]}...")

# 方式2: authorization 里的引号也 urlencode 一次
auth2 = urllib.parse.quote(auth1, safe='')
url2 = f"wss://{host}{path}?authorization={auth2}&host={host}&date={date}"
print(f"方式2 (全urlencode): {url2[:100]}...")

# 方式3: 用 X-Param 格式（某些讯飞接口用这个）
auth3 = base64.b64encode(f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'.encode()).decode()
url3 = f"wss://{host}{path}?authorization={auth3}&host={host}&date={date}"
print(f"方式3 (auth base64): {url3[:100]}...")

# 用方式1连接试试
async def test_connect(url, label):
    try:
        async with websockets.connect(url, max_size=10*1024*1024) as ws:
            print(f"✅ {label}: WebSocket 连接成功")
            # 发一帧空音频
            import struct
            silence = struct.pack('<' + 'h' * 160, *([0]*160))  # 10ms 静音
            frame = json.dumps({
                "common": {"app_id": appid},
                "business": {"language": "zh_cn", "domain": "iat", "accent": "mandarin", "ptt": 1},
                "data": {"status": 2, "format": "audio/L16;rate=16000", "encoding": "raw",
                         "audio": base64.b64encode(silence).decode()}
            })
            await ws.send(frame)
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            print(f"   讯飞返回: code={resp.get('code')}, msg={resp.get('message','')[:60]}")
            return True
    except Exception as e:
        print(f"❌ {label}: {type(e).__name__}: {str(e)[:120]}")
        return False

asyncio.run(test_connect(url1, "方式1（现有代码）"))
time.sleep(1)
asyncio.run(test_connect(url2, "方式2（全urlencode）"))
time.sleep(1)
asyncio.run(test_connect(url3, "方式3（auth base64）"))
