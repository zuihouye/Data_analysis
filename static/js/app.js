/* ==========================================================================
   xx订单平台 · 公共前端脚本（app.js）
   --------------------------------------------------------------------------
   只做跨页面复用的小事：账号菜单、tooltip。页面专属逻辑放各自的 js。
   零依赖、ES5 语法（老浏览器也能跑）。
   ========================================================================== */
(function () {
  'use strict';

  /* ── 账号菜单 ─────────────────────────────────────────────────── */
  var meBtn = document.getElementById('meBtn');
  var meMenu = document.getElementById('meMenu');

  function menuOpen(open) {
    if (!meMenu || !meBtn) return;
    meMenu.hidden = !open;
    meBtn.setAttribute('aria-expanded', String(open));
    if (open) {
      var first = meMenu.querySelector('button');
      if (first) first.focus();
    }
  }

  function closeAccount() { menuOpen(false); }

  if (meBtn) {
    meBtn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      menuOpen(meMenu.hidden);
    });
    document.addEventListener('click', function (ev) {
      if (!ev.target.closest || !ev.target.closest('.me')) closeAccount();
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') closeAccount();
    });
  }

  /* ── 全局 tooltip：任何元素带 data-tip 即可 ───────────────────── */
  var tip = document.getElementById('tip');

  function showTip(text, ev) {
    if (!tip) return;
    tip.textContent = text;
    tip.hidden = false;
    var pad = 14;
    var r = tip.getBoundingClientRect();
    var x = ev.clientX + pad;
    var y = ev.clientY + pad;
    if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - pad;
    if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - pad;
    tip.style.left = Math.max(8, x) + 'px';
    tip.style.top = Math.max(8, y) + 'px';
  }

  function hideTip() { if (tip) tip.hidden = true; }

  if (tip) {
    document.addEventListener('pointerover', function (ev) {
      var t = ev.target.closest && ev.target.closest('[data-tip]');
      if (t) showTip(t.dataset.tip, ev);
    });
    document.addEventListener('pointerout', function (ev) {
      if (ev.target.closest && ev.target.closest('[data-tip]')) hideTip();
    });
    document.addEventListener('pointermove', function (ev) {
      var t = ev.target.closest && ev.target.closest('[data-tip]');
      if (!tip.hidden && t) showTip(t.dataset.tip, ev);
    });
    window.addEventListener('scroll', hideTip, { passive: true });
  }

  /* ── 对外暴露 ─────────────────────────────────────────────────── */
  window.Dsh = {
    closeAccount: closeAccount,
    hideTip: hideTip
  };
})();
