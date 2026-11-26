import time, os, traceback
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8012/_v377_test.html?demo=%E9%93%81%E5%B2%AD%E4%B9%8B%E5%AE%B6"
SD = r"d:\jiayaoji\.screenshots"
LOG = SD + r"\v377_flow.log"
TITLE = "霜降食育小课堂·家庭餐桌礼仪"
CONTENT = "霜降时节天凉进补，长辈先动筷、公筷分餐、少盐少油、按需取食不浪费。带孩子一起择菜摆盘，讲讲节气与食物的来历，把饮食教养融进一日三餐。"

logf = open(LOG, "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    logf.write(s + "\n"); logf.flush()

def toast_text(page):
    return page.evaluate("Array.from(document.querySelectorAll('.toast-msg')).map(t=>t.textContent.trim()).join(' | ')")

def clear_disclaimers(page, rounds=3):
    for i in range(rounds):
        n = page.evaluate("document.querySelectorAll('#dietDisclaimerMask').length")
        if n == 0:
            return True
        page.evaluate("document.getElementById('dietDisclaimerOk')?.click()")
        time.sleep(0.8)
    return page.evaluate("document.querySelectorAll('#dietDisclaimerMask').length") == 0

ok = False
b = None
page = None
try:
    p = sync_playwright().start()
    b = p.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.set_default_timeout(8000)
    page.goto(URL, wait_until="load", timeout=60000)
    time.sleep(4)
    log("STEP0 demo:", page.evaluate("window.__demoMode"), "| user:", page.evaluate("JSON.stringify(getLoginUser())"))

    page.locator("#pageHome .home-tab-item:has-text('食知')").click(timeout=8000)
    time.sleep(1.2)
    log("STEP1 食知 ->", page.evaluate("currentTab"))

    page.locator("#knowledgeLibBtn").click(timeout=8000)
    time.sleep(1.2)
    log("STEP2 饮食文库 ->", page.evaluate("currentKnowledgeViewMode"))

    page.locator("#libTabRow button:has-text('食养')").click(timeout=8000)
    time.sleep(1.5)
    log("STEP3 食养 tab | disclaimer cleared:", clear_disclaimers(page))
    page.screenshot(path=SD + r"\v377_05_shiyang.png")

    # 公共文库层（树节点）
    page.locator('#categoryContentList .sy-row[data-key="public"]').click(timeout=8000)
    time.sleep(1.2)
    log("STEP4 公共文库 -> lib:", page.evaluate("_syTreeLib"), "| sel:", page.evaluate("_syTreeSel"))
    page.screenshot(path=SD + r"\v377_06a_public.png")

    # 若根节点被折叠，食育子行会消失 -> 再点一次展开
    if page.evaluate("!document.querySelector('#categoryContentList .sy-row[data-key=\"public|食育\"]')"):
        page.locator('#categoryContentList .sy-row[data-key="public"]').click(timeout=8000)
        time.sleep(1)
        log("STEP4b re-expand public")

    page.locator('#categoryContentList .sy-row[data-key="public|食育"]').click(timeout=8000)
    time.sleep(1.2)
    log("STEP5 食育 -> sel:", page.evaluate("_syTreeSel"))
    log("  pane:", page.evaluate("document.getElementById('categoryContentList')?.innerText?.slice(0,160).replace(/\\n/g,' ') || ''"))
    page.screenshot(path=SD + r"\v377_06_shiyu_lock.png")

    clear_disclaimers(page)
    page.locator("#clearBtn").click(timeout=8000)
    time.sleep(1)
    log("STEP6 ＋ modal shown:", page.evaluate("document.getElementById('knowledgeModalOverlay')?.classList.contains('show')"))
    page.screenshot(path=SD + r"\v377_07_addmodal.png")
    log("  mainOptions:", page.evaluate("[...document.querySelectorAll('#newKnowledgeMainCategory option')].map(o=>o.value+':'+o.textContent.trim())"))

    page.fill("#newKnowledgeTitle", TITLE, timeout=8000)
    page.fill("#newKnowledgeContent", CONTENT, timeout=8000)
    mv = page.evaluate("(()=>{const o=[...document.getElementById('newKnowledgeMainCategory').options].find(o=>o.textContent.includes('食养'));return o?o.value:null})()")
    log("STEP7 main value:", mv)
    page.select_option("#newKnowledgeMainCategory", value=mv, timeout=8000)
    time.sleep(0.8)
    log("  subOptions:", page.evaluate("[...document.querySelectorAll('#newKnowledgeSubCategory option')].map(o=>o.value+':'+o.textContent.trim())"))
    sv = page.evaluate("(()=>{const o=[...document.getElementById('newKnowledgeSubCategory').options].find(o=>o.textContent.includes('食育'));return o?o.value:null})()")
    log("  sub value:", sv)
    page.select_option("#newKnowledgeSubCategory", value=sv, timeout=8000)
    time.sleep(0.8)
    log("  selected:", page.evaluate("document.getElementById('newKnowledgeMainCategory').value+'/'+document.getElementById('newKnowledgeSubCategory').value"))
    page.screenshot(path=SD + r"\v377_08_filled.png")

    page.locator("#knowledgeModalSave").click(timeout=8000)
    time.sleep(0.6)
    log("STEP8 save toast:", toast_text(page))
    page.screenshot(path=SD + r"\v377_09_saved.png")
    time.sleep(0.8)
    log("  modal still shown:", page.evaluate("document.getElementById('knowledgeModalOverlay')?.classList.contains('show')"))
    log("  new item:", page.evaluate("(()=>{const k=dietKnowledge[0];return JSON.stringify({id:k.id,title:k.title,layer:k.layer,main:k.mainCategory,sub:k.subCategory})})()"))

    layer = page.evaluate("dietKnowledge[0].layer") or 'family'
    key = 'public' if layer == 'public' else 'family'
    page.locator('#categoryContentList .sy-row[data-key="%s"]' % key).click(timeout=8000)
    time.sleep(1.2)
    log("STEP9 root ->", page.evaluate("_syTreeLib"), "| sel:", page.evaluate("_syTreeSel"))
    page.screenshot(path=SD + r"\v377_10_list.png")

    item = page.locator("#categoryContentList .cat-search-item").filter(has_text=TITLE).first
    log("  article count:", page.locator("#categoryContentList .cat-search-item").filter(has_text=TITLE).count())
    item.click(timeout=8000)
    time.sleep(0.35)
    t = toast_text(page)
    log("STEP10 TOAST:", repr(t))
    page.screenshot(path=SD + r"\v377_11_toast.png")
    ok = True
except Exception:
    log("ERROR\n" + traceback.format_exc())
    try:
        page.screenshot(path=SD + r"\v377_error.png")
    except Exception:
        pass
finally:
    log("RESULT PASS" if ok else "RESULT FAIL")
    logf.close()
    try:
        if b: b.close()
    except Exception:
        pass
    os._exit(0 if ok else 1)
