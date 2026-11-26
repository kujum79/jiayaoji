#!/bin/bash
ls -la /var/www/jiayaoji/backend/data/family_backups/ | head -12
echo "--- 当前 BHDVWN36 备份内容 ---"
python3 - <<'PYEOF'
import json
p = '/var/www/jiayaoji/backend/data/family_backups/BHDVWN36.json'
d = json.load(open(p))
data = d.get('data', d)
print('top keys:', list(d.keys())[:8])
for k in sorted(data):
    v = data[k]
    n = ''
    if 'dietKnowledge' in k or 'knowledgeCategories' in k:
        try: n = ' entries=' + str(len(json.loads(v)))
        except Exception: n = ' (parse err)'
    print(k, 'bytes=' + str(len(v)) + n)
PYEOF
echo "--- 全盘找历史副本 ---"
find /var/www/jiayaoji /var/backups /home -name '*BHDVWN36*' 2>/dev/null | head -10
find / -maxdepth 4 -name '*backup*BHDVWN36*' -o -maxdepth 4 -name 'BHDVWN36*.json.*' 2>/dev/null | grep -v proc | head -5
