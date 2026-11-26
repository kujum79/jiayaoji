import time
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8012/_v377_test.html?demo=%E9%93%81%E5%B2%AD%E4%B9%8B%E5%AE%B6"
SHOT = r"d:\jiayaoji\.screenshots\v377_02_libtab.png"

def state(page, label):
    print("----", label)
    print(" tab:", page.evaluate("typeof currentTab!=='undefined'?currentTab:''"),
          "| view:", page.evaluate("typeof currentKnowledgeViewMode!=='undefined'?currentKnowledgeViewMode:''"),
          "| vis:", page.evaluate("typeof currentKnowledgeVisibility!=='undefined'?currentKnowledgeVisibility:''"))
    print(" topTitle:", page.evaluate("document.getElementById('topTitle')?.textContent?.trim()"))
    print(" syTreeLib/Sel:", page.evaluate("(typeof _syTreeLib!=='undefined'?_syTreeLib:'')+' / '+(typeof _syTreeSel!=='undefined'?_syTreeSel:'')"))
    print(" visible addBtn:", page.evaluate("(()=>{const b=document.getElementById('clearBtn');return b?getComputedStyle(b).display:'none'})()"))
    print(" toast:", page.evaluate("document.querySelector('#toast,.toast')?.textContent?.trim()||''"))

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(URL, wait_until="load", timeout=60000)
    time.sleep(4)
    state(page, "initial")

    # 1) 点底部「食知」tab
    page.click("#pageHome .home-tab-item:has-text('食知')")
    time.sleep(1.2)
    state(page, "after 食知 tab")
    page.screenshot(path=r"d:\jiayaoji\.screenshots\v377_02a_knowledge.png")

    # 2) 点顶栏 🗂️ 饮食文库
    page.click("#knowledgeLibBtn")
    time.sleep(1.5)
    state(page, "after 饮食文库")
    page.screenshot(path=SHOT)

    # 3) 打印文库 tab 文本
    print("libTabs:", page.evaluate("[...document.querySelectorAll('#libTabRow button')].map(b=>b.innerText)"))
    print("layerTabs:", page.evaluate("[...document.querySelectorAll('.knowledge-layer-tab')].filter(b=>b.offsetParent).map(b=>b.innerText)"))
    print("treeRows:", page.evaluate("[...document.querySelectorAll('#categoryContentList .sy-row')].slice(0,20).map(r=>r.innerText.replace(/\\n/g,' '))"))
    b.close()
