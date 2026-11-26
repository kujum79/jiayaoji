import time
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8012/_v377_test.html?demo=%E9%93%81%E5%B2%AD%E4%B9%8B%E5%AE%B6"

def state(page, label):
    print("----", label)
    print(" tab:", page.evaluate("typeof currentTab!=='undefined'?currentTab:''"),
          "| view:", page.evaluate("typeof currentKnowledgeViewMode!=='undefined'?currentKnowledgeViewMode:''"),
          "| vis:", page.evaluate("typeof currentKnowledgeVisibility!=='undefined'?currentKnowledgeVisibility:''"))
    print(" topTitle:", page.evaluate("document.getElementById('topTitle')?.textContent?.trim()"))
    print(" syTreeLib/Sel:", page.evaluate("(typeof _syTreeLib!=='undefined'?_syTreeLib:'')+' / '+(typeof _syTreeSel!=='undefined'?_syTreeSel:'')"))
    print(" toast:", page.evaluate("document.querySelector('#toast,.toast')?.textContent?.trim()||''"))

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(URL, wait_until="load", timeout=60000)
    time.sleep(4)
    page.click("#pageHome .home-tab-item:has-text('食知')")
    time.sleep(1)
    page.click("#knowledgeLibBtn")
    time.sleep(1)

    # 食养 tab
    page.click("#libTabRow button:has-text('食养')")
    time.sleep(1.5)
    state(page, "after 食养 tab")
    print("treeRows:", page.evaluate("[...document.querySelectorAll('#categoryContentList .sy-row')].map(r=>r.innerText.replace(/\\n/g,' ')"))
    print("layerTabs visible:", page.evaluate("[...document.querySelectorAll('.knowledge-layer-tab')].filter(b=>b.offsetParent).map(b=>b.innerText.replace(/\\n/g,' ')"))
    page.screenshot(path=r"d:\jiayaoji\.screenshots\v377_03_shiyang.png")

    # 公共文库层
    page.click(".knowledge-layer-tab:has-text('公共文库')")
    time.sleep(1.2)
    state(page, "after 公共文库")
    print("syTreeLib:", page.evaluate("_syTreeLib"))

    # 点树里的「食育」行
    page.click("#categoryContentList .sy-row:has-text('食育')")
    time.sleep(1.2)
    state(page, "after 食育")
    print("pane text:", page.evaluate("document.getElementById('categoryContentList')?.innerText?.slice(0,300)"))
    page.screenshot(path=r"d:\jiayaoji\.screenshots\v377_04_shiyu_lock.png")
    b.close()
