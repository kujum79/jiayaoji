import time, sys
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8012/_v377_test.html?demo=%E9%93%81%E5%B2%AD%E4%B9%8B%E5%AE%B6"

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(URL, wait_until="load", timeout=60000)
    time.sleep(4)
    print("URL:", page.url)
    print("TITLE:", page.title())
    print("DEMO:", page.evaluate("window.__demoMode"), page.evaluate("window.__demoName"))
    print("TAB:", page.evaluate("typeof currentTab!=='undefined'?currentTab:''"))
    print("VIS:", page.evaluate("typeof currentKnowledgeVisibility!=='undefined'?currentKnowledgeVisibility:''"))
    print("VIEW:", page.evaluate("typeof currentKnowledgeViewMode!=='undefined'?currentKnowledgeViewMode:''"))
    print("TOAST:", page.evaluate("document.querySelector('#toast')?.textContent || ''"))
    page.screenshot(path=r"d:\jiayaoji\.screenshots\v377_01_initial.png", full_page=False)
    b.close()
