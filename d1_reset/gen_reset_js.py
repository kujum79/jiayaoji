# D1 数据重置：生成四家注入脚本（浏览器登录后用 lsSet 写入，自动触发云同步）
import json, os

OUT = os.path.dirname(os.path.abspath(__file__))

def fmt(d):
    return d.strftime('%Y-%m-%d')

class D:
    def __init__(self):
        from datetime import date, timedelta
        self.today = date.today()
        self.td = timedelta
    def fmt(self, off=0):
        return fmt(self.today + self.td(days=off))

t = D()

def member(mid, name, age, place, city, role, rel, taste, health, constit, isFamily=True):
    return {"id": mid, "name": name, "age": age, "place": place, "currentCity": city,
            "taste": taste, "dietaryPreference": "", "relationship": rel, "isFamily": isFamily,
            "role": role, "healthConcerns": health, "constitution": constit, "constitutionDetail": ""}

def item(iid, name, emoji, cat, qty, exp_off, buy_off=0):
    return {"id": iid, "name": name, "emoji": emoji, "category": cat, "quantity": qty,
            "purchaseDate": t.fmt(buy_off), "expiryDate": t.fmt(exp_off)}

FAMILIES = {}

# ===== 铁岭家 Q9CMSHB9（6口三代，非会员）=====
FAMILIES['Q9CMSHB9'] = {
    "familyData": [
        member(1, '大姐', 27, '铁岭', '铁岭', '管理员', '妈妈', '东北口味', '', '平和质'),
        member(2, '铁蛋', 29, '铁岭', '铁岭', '普通人', '爸爸', '东北口味，爱炖菜', '', '平和质'),
        member(3, '爷爷', 62, '铁岭', '铁岭', '普通人', '爷爷', '软烂清淡', '高血压', '气虚质'),
        member(4, '奶奶', 60, '铁岭', '铁岭', '普通人', '奶奶', '软烂', '关节不适', '阳虚质'),
        member(5, '小蛋', 6, '铁岭', '铁岭', '普通人', '儿子', '不吃辣', '', '平和质'),
        member(6, '小欢', 3, '铁岭', '铁岭', '普通人', '女儿', '挑食', '', '平和质'),
    ],
    "fridgeItems": [
        item(101, '面粉', '🌾', '主粮', '10斤', 120), item(102, '玉米面', '🌽', '主粮', '2斤', 90),
        item(103, '小米', '🥣', '主粮', '3斤', 150), item(104, '大米', '🍚', '主粮', '10斤', 180),
        item(105, '酸菜', '🥬', '蔬菜', '2棵', 2, -3), item(106, '白菜', '🥬', '蔬菜', '2棵', 15),
        item(107, '土豆', '🥔', '蔬菜', '8个', 20), item(108, '萝卜', '🥕', '蔬菜', '4根', 12),
        item(109, '大葱', '🧅', '蔬菜', '3把', 10), item(110, '豆腐', '🧈', '蔬菜', '3块', 2, -1),
        item(111, '排骨', '🍖', '肉蛋奶', '2kg', 2, -1), item(112, '鸡腿', '🍗', '肉蛋奶', '1kg', 3),
        item(113, '鸡蛋', '🥚', '肉蛋奶', '30个', 18), item(114, '牛奶', '🥛', '肉蛋奶', '2盒', 5),
        item(115, '儿童酸奶', '🥛', '肉蛋奶', '8杯', 7), item(116, '香肠', '🌭', '肉蛋奶', '2根', 25, -2),
        item(117, '鳕鱼', '🐟', '水产', '1条', 1, -1), item(118, '虾皮', '🦐', '水产', '1袋', 90),
        item(119, '酱油', '🍶', '调味品', '1瓶', 200), item(120, '醋', '🧴', '调味品', '1瓶', 200),
        item(121, '蚝油', '🫙', '调味品', '1瓶', 120), item(122, '生姜', '🧄', '调味品', '1块', 20),
        item(123, '苹果', '🍎', '水果', '10个', 12), item(124, '橘子', '🍊', '水果', '5个', 6),
        item(125, '干香菇', '🍄', '干货', '1袋', 180),
    ],
    "subscriptionStatus": None,  # 非会员
}

# ===== 小高 5CQ566DT（贵阳4口，两高龄老人，食养会员）=====
FAMILIES['5CQ566DT'] = {
    "familyData": [
        member(1, '儿媳', 39, '浙江', '贵阳', '管理员', '妈妈', '营养均衡，低盐', '', '平和质'),
        member(2, '儿子', 40, '贵阳', '贵阳', '普通人', '爸爸', '厨师，重食材本味', '', '平和质'),
        member(3, '父亲', 83, '四川', '贵阳', '普通人', '爷爷', '软烂，微辣可', '高血压、咀嚼力弱', '气虚质'),
        member(4, '母亲', 81, '浙江', '贵阳', '普通人', '奶奶', '清淡软食', '骨质疏松', '阳虚质'),
    ],
    "fridgeItems": [
        item(201, '小米', '🥣', '主粮', '2斤', 90), item(202, '燕麦', '🌾', '主粮', '1袋', 120),
        item(203, '大米', '🍚', '主粮', '5斤', 150), item(204, '面条', '🍜', '主粮', '1kg', 60),
        item(205, '山药', '🍠', '蔬菜', '3根', 10), item(206, '南瓜', '🎃', '蔬菜', '1个', 15),
        item(207, '嫩叶菜', '🥬', '蔬菜', '4把', 2, -1), item(208, '菠菜', '🥬', '蔬菜', '2把', 3),
        item(209, '白菜', '🥬', '蔬菜', '1棵', 18), item(210, '莲藕', '🪷', '蔬菜', '2节', 8),
        item(211, '豆腐', '🧈', '蔬菜', '2块', 2, -1), item(212, '鳊鱼', '🐟', '水产', '1条', 1, 0),
        item(213, '鸡蛋', '🥚', '肉蛋奶', '15个', 20), item(214, '牛奶', '🥛', '肉蛋奶', '2盒', 6),
        item(215, '无糖酸奶', '🥛', '肉蛋奶', '6杯', 9), item(216, '猪里脊', '🥩', '肉蛋奶', '500g', 3),
        item(217, '木耳', '🍄', '干货', '1袋', 180), item(218, '莲子', '🌰', '干货', '1袋', 150),
        item(219, '枸杞', '🫐', '干货', '1罐', 180), item(220, '红枣', '🍎', '干货', '1袋', 90),
    ],
    "subscriptionStatus": "member",
}

# ===== 非英中 MS2BQ9XG（上海3口带半岁婴儿，食养会员）=====
FAMILIES['MS2BQ9XG'] = {
    "familyData": [
        member(1, '非英', 34, '武汉', '上海', '管理员', '妈妈', '武汉口味偏辣，产后调理', '', '气虚质'),
        member(2, '中义', 28, '意大利', '上海', '普通人', '爸爸', '意餐，接受中式', '', '平和质'),
        member(3, '小义', 1, '上海', '上海', '普通人', '儿子（半岁）', '辅食期，无盐无糖', '', '平和质'),
    ],
    "fridgeItems": [
        item(301, '婴儿米粉', '🥣', '主粮', '2盒', 60), item(302, '大米', '🍚', '主粮', '5斤', 150),
        item(303, '意面', '🍝', '主粮', '3包', 120), item(304, '南瓜', '🎃', '蔬菜', '1个', 15),
        item(305, '西兰花', '🥦', '蔬菜', '2颗', 4), item(306, '番茄', '🍅', '蔬菜', '6个', 3, -1),
        item(307, '菠菜', '🥬', '蔬菜', '2把', 2), item(308, '鸡蛋', '🥚', '肉蛋奶', '12个', 18),
        item(309, '牛奶', '🥛', '肉蛋奶', '2盒', 6), item(310, '无盐黄油', '🧈', '肉蛋奶', '1块', 30),
        item(311, '鸡胸肉', '🍗', '肉蛋奶', '600g', 2), item(312, '牛肉', '🥩', '肉蛋奶', '500g', 4),
        item(313, '三文鱼', '🐟', '水产', '1块', 1, 0), item(314, '番茄罐头', '🥫', '调味品', '2罐', 300),
        item(315, '橄榄油', '🫒', '调味品', '1瓶', 200), item(316, '香蕉', '🍌', '水果', '6根', 4),
        item(317, '苹果', '🍎', '水果', '6个', 12),
    ],
    "subscriptionStatus": "member",
}

# ===== 三仁佳欢 BHDVWN36（武汉5口，超管直通会员）=====
FAMILIES['BHDVWN36'] = {
    "familyData": [
        member(1, '木三', 57, '西安', '武汉', '管理员（超管）', '爸爸', '面食爱好，少辣', '', '平和质'),
        member(2, '佳姐', 50, '广州', '武汉', '普通人', '妈妈', '粤式清淡', '', '阴虚质'),
        member(3, '纪尧鱼', 32, '武汉', '武汉', '普通人', '儿子', '家常口味', '', '平和质'),
        member(4, '配偶', 31, '武汉', '武汉', '普通人', '儿媳', '家常口味', '', '平和质'),
        member(5, '儿子', 16, '武汉', '武汉', '普通人', '孙子（体育生）', '高蛋白大份量', '训练期', '湿热质'),
    ],
    "fridgeItems": [
        item(401, '面粉', '🌾', '主粮', '5斤', 120), item(402, '糙米', '🍚', '主粮', '2kg', 90),
        item(403, '燕麦', '🌾', '主粮', '1罐', 120), item(404, '牛奶', '🥛', '肉蛋奶', '2L', 5),
        item(405, '鸡蛋', '🥚', '肉蛋奶', '20个', 20), item(406, '鸡胸肉', '🍗', '肉蛋奶', '1kg', 3),
        item(407, '牛肉', '🥩', '肉蛋奶', '800g', 4), item(408, '豆浆', '🥛', '肉蛋奶', '1L', 2, -1),
        item(409, '酸奶', '🥛', '肉蛋奶', '8杯', 9), item(410, '豆腐', '🧈', '蔬菜', '2块', 3),
        item(411, '西兰花', '🥦', '蔬菜', '3颗', 5), item(412, '胡萝卜', '🥕', '蔬菜', '6根', 15),
        item(413, '番茄', '🍅', '蔬菜', '8个', 4, -2), item(414, '黄瓜', '🥒', '蔬菜', '5根', 6),
        item(415, '山药', '🍠', '蔬菜', '2根', 12), item(416, '冬瓜', '🍈', '蔬菜', '1块', 10),
        item(417, '三文鱼', '🐟', '水产', '2块', 2), item(418, '鲜虾', '🦐', '水产', '500g', 2),
        item(419, '香蕉', '🍌', '水果', '10根', 5), item(420, '苹果', '🍎', '水果', '8个', 14),
        item(421, '坚果混合', '🥜', '干货', '1罐', 60), item(422, '排骨', '🍖', '肉蛋奶', '1kg', 3),
    ],
    "subscriptionStatus": None,  # 超管直通，无需写状态
}

import time
now_ms = int(time.time() * 1000)
year_ms = now_ms + 365 * 24 * 3600 * 1000

for fid, data in FAMILIES.items():
    lines = ['(function(){']
    lines.append('try {')
    lines.append("lsSet('familyData', " + json.dumps(json.dumps(data['familyData'], ensure_ascii=False)) + ");")
    lines.append("lsSet('fridgeItems', " + json.dumps(json.dumps(data['fridgeItems'], ensure_ascii=False)) + ");")
    if data['subscriptionStatus'] == 'member':
        sub = {"foodMember": True, "listenMember": True, "foodExpireAt": year_ms, "listenExpireAt": 0}
        lines.append("lsSet('subscriptionStatus', " + json.dumps(json.dumps(sub)) + ");")
    lines.append("return 'OK ' + Object.keys(localStorage).filter(function(k){return k.indexOf('fridgeItems')>=0;}).length;")
    lines.append('} catch(e) { return \"ERR:\" + e.message; }')
    lines.append('})()')
    path = os.path.join(OUT, f'reset_{fid}.js')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(path, os.path.getsize(path), 'bytes')
print('DONE')
