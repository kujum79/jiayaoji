import base64, hmac, hashlib

# 用户给的密钥
appid = "7c785af5"
api_key = "28920d5af897052d742d6e34b01b9161"
api_secret_encoded = "YzM4NmIxZDlhOGY3ZjhkNDIxYWJhNTAz"

print("=== 密钥解码尝试 ===")
print(f"Secret 原始: {api_secret_encoded}")
try:
    decoded = base64.b64decode(api_secret_encoded + "==").decode()
    print(f"base64解码: {decoded}")
except Exception as e:
    print(f"base64解码失败: {e}")
    decoded = api_secret_encoded

# 两种方式做签名对比
from email.utils import formatdate
import urllib.parse, websockets, asyncio, json

host = "iat-api.xfyun.cn"
path = "/v2/iat"
date = formatdate(usegmt=True)

for label, secret in [("原始Secret", api_secret_encoded), ("base64解码后", decoded)]:
    origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    sig = base64.b64encode(hmac.new(secret.encode(), origin.encode(), hashlib.sha256).digest()).decode()
    auth = f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{sig}"'
    url = f"wss://{host}{path}?" + urllib.parse.urlencode({"authorization": auth, "host": host, "date": date})
    
    async def test(label2, url2):
        try:
            async with websockets.connect(url2, max_size=10*1024*1024) as ws:
                print(f"✅ {label2}: 连接成功!")
                frame = json.dumps({
                    "common": {"app_id": appid},
                    "business": {"language": "zh_cn", "domain": "iat", "ptt": 1},
                    "data": {"status": 2, "format": "audio/L16;rate=16000",
                             "encoding": "raw", "audio": base64.b64encode(b'\x00'*320).decode()}
                })
                await ws.send(frame)
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                print(f"   讯飞返回: code={resp.get('code')}, msg={resp.get('message','')[:80]}")
                return True
        except Exception as e:
            print(f"❌ {label2}: HTTP 401 —— 密钥不对或服务未开通")
            return False
    
    asyncio.run(test(label, url))
