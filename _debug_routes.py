import sys
sys.path.insert(0, '/var/www/jiayaoji')
from backend.main import app
from fastapi.testclient import TestClient
c = TestClient(app)
# 直接测
for path in ['/api/diet-knowledge/public', '/diet-knowledge/public']:
    r = c.get(path)
    print(f"GET {path}: status={r.status_code} body_preview={r.text[:100]}")
# 打印 diet 路由的 endpoint 名称
print("\nDiet routes detail:")
for r in app.routes:
    if hasattr(r, 'methods') and 'diet' in r.path:
        print(f"  path={r.path} endpoint={getattr(r, 'endpoint', None)} methods={r.methods}")
