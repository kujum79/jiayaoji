/* V710q: 视频横幅卡 + 播放器 —— 独立脚本，与主业务隔离
 * 即使此文件加载失败/parse 出错，也不会影响 APP 主业务 script 块 */
(function () {
    'use strict';

    // ---- 1. 横幅卡渲染：DOMContentLoaded 后检查 localStorage 标记，显示横幅 ----
    function _renderHomeVideoCard() {
        try {
            var card = document.getElementById('homeVideoCard');
            if (!card) return; // 容器不存在（旧版 HTML）就跳过
            var hidden = false;
            try { hidden = (localStorage.getItem('homeVideoCardHidden') === '1'); } catch (e) {}
            if (hidden) { card.style.display = 'none'; return; }
            card.style.display = '';
            var v = document.getElementById('homeVideoEl');
            if (v && typeof v.play === 'function') { try { v.play().catch(function(){}); } catch(e){} }
        } catch (e) {
            console.warn('[video-banner] renderHomeVideoCard error:', e);
        }
    }

    // ---- 2. 视频播放器弹窗（n=0 功能介绍版，n=1 一天场景版） ----
    function _openVideoPlayer(n) {
        try {
            var isDay = (n === 1);
            var src = isDay ? '/videos/intro_2.mp4' : '/videos/intro_1.mp4';
            var title = isDay ? '家肴记 · 一天场景版' : '家肴记 · 功能介绍';

            var overlay = document.getElementById('__videoPlayerOverlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.id = '__videoPlayerOverlay';
                overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.88);z-index:9999;display:flex;align-items:center;justify-content:center;flex-direction:column;';
                overlay.onclick = function(e) { if (e.target === overlay) _closeVideoPlayer(); };
                document.body.appendChild(overlay);
            }
            overlay.innerHTML = '';
            overlay.style.display = 'flex';

            // 标题栏
            var head = document.createElement('div');
            head.style.cssText = 'width:100%;max-width:400px;padding:12px 16px;display:flex;align-items:center;gap:8px;color:#fff;';
            head.innerHTML = '<span style="font-size:16px;font-weight:700;flex:1;">🎬 ' + title + '</span>'
                + '<button onclick="_closeVideoPlayer()" style="background:rgba(255,255,255,0.2);border:none;color:#fff;font-size:20px;width:32px;height:32px;border-radius:50%;cursor:pointer;line-height:1;">✕</button>';
            overlay.appendChild(head);

            // 视频容器（9:16 竖屏）
            var wrap = document.createElement('div');
            wrap.style.cssText = 'width:100%;max-width:400px;aspect-ratio:9/16;background:#000;border-radius:12px;overflow:hidden;margin:0 auto;';
            wrap.innerHTML = '<video id="__videoPlayerVideo" controls playsinline autoplay style="width:100%;height:100%;object-fit:cover;"><source src="' + src + '" type="video/mp4"></video>';
            overlay.appendChild(wrap);

            // 切换按钮（数字参数，无引号嵌套）
            var switcher = document.createElement('div');
            switcher.style.cssText = 'width:100%;max-width:400px;padding:12px 16px;display:flex;gap:8px;';
            switcher.innerHTML = '<button onclick="_openVideoPlayer(0)" style="flex:1;padding:10px;background:' + (isDay ? '#374151' : '#F97316') + ';color:#fff;border:none;border-radius:9px;font-weight:700;cursor:pointer;">功能介绍版</button>'
                + '<button onclick="_openVideoPlayer(1)" style="flex:1;padding:10px;background:' + (isDay ? '#F97316' : '#374151') + ';color:#fff;border:none;border-radius:9px;font-weight:700;cursor:pointer;">一天场景版</button>';
            overlay.appendChild(switcher);
        } catch (e) {
            console.warn('[video-banner] openVideoPlayer error:', e);
        }
    }

    // ---- 3. 关闭播放器 ----
    function _closeVideoPlayer() {
        try {
            var overlay = document.getElementById('__videoPlayerOverlay');
            if (!overlay) return;
            var v = document.getElementById('__videoPlayerVideo');
            if (v && typeof v.pause === 'function') { try { v.pause(); v.src = ''; v.load(); } catch(e){} }
            overlay.style.display = 'none';
        } catch (e) { console.warn('[video-banner] closeVideoPlayer error:', e); }
    }

    // 挂 window 上让 HTML onclick 能访问
    window._openVideoPlayer = _openVideoPlayer;
    window._closeVideoPlayer = _closeVideoPlayer;

    // DOMContentLoaded 后初始化（等首页 renderHomeCards 渲染完也可能，先自己独立跑）
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _renderHomeVideoCard);
    } else {
        _renderHomeVideoCard();
    }
    // 首页切换时也重新渲染（单页 SPA hashchange）
    window.addEventListener('hashchange', _renderHomeVideoCard);
})();
