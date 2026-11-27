import re
from collections import Counter
with open(r'd:\jiayaoji\frontend\index.html', 'r', encoding='utf-8') as f:
    c = f.read()
m = re.search(r'let DEFAULT_DIET_KNOWLEDGE = \[(.*?)\];', c, re.DOTALL)
# 提取所有条目的 mainCategory + subCategory + leafCategory
entries = re.findall(r"\{[^}]*?mainCategory:\s*'([^']+)',\s*subCategory:\s*'([^']+)'(?:,\s*leafCategory:\s*'([^']+)')?", m.group(1))
print('=== 32 条种子完整结构 ===')
for mc, sc, lc in entries:
    print(f"  main={mc:15s} sub={sc:15s} leaf={lc or ''}")
