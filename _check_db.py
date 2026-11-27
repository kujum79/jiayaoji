import sqlite3
conn = sqlite3.connect('backend/data/jiayaoji.db')
rows = conn.execute("SELECT publish_date,content_type,review_status,substr(title,1,35) FROM food_hall_pub_days WHERE publish_date >= '2026-11-25' ORDER BY publish_date, content_type").fetchall()
print("=== 最近几天发布记录 ===")
for r in rows:
    print(r)
print(f"\n共 {len(rows)} 条")
