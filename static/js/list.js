/* 列表页公共分页组件（前端伪分页：数据已在服务端渲染，这里只控制显示/隐藏）。
 *
 * 用法（模板侧）：
 *   <section class="card"
 *            data-list
 *            data-list-body="listBody"     <!-- tbody 的 id -->
 *            data-list-size="listSize"     <!-- 每页条数 <select> 的 id -->
 *            data-list-info="listInfo"     <!-- 左下方「第 x–y 条，共 n 条」 -->
 *            data-list-nav="listNav">      <!-- 右下方分页按钮容器 -->
 *
 * 约定：每页条数选项必须是 10/20/50/100；页大小变化时回到第 1 页。
 * 为什么不做后端分页：当前数据量（千级）一次渲染完全够快，先统一交互；
 * 真需要后端分页时，把这里换成 fetch 取页即可，模板结构不用改。
 */
(function () {
  'use strict';

  var DEFAULT_SIZE = 20;

  function toInt(value, fallback) {
    var n = parseInt(value, 10);
    return isNaN(n) || n <= 0 ? fallback : n;
  }

  /* 生成页码序列：1 … current-1 current current+1 … last，「…」用 'gap' 占位。 */
  function pageSequence(current, pages) {
    var wanted = [1, current - 1, current, current + 1, pages];
    var pagesOut = [];
    wanted.forEach(function (p) {
      if (p >= 1 && p <= pages && pagesOut.indexOf(p) === -1) pagesOut.push(p);
    });
    pagesOut.sort(function (a, b) { return a - b; });

    var seq = [];
    pagesOut.forEach(function (p, i) {
      if (i && p - pagesOut[i - 1] > 1) seq.push('gap');
      seq.push(p);
    });
    return seq;
  }

  function initList(root) {
    var tbody = document.getElementById(root.getAttribute('data-list-body') || '');
    var sizeSel = document.getElementById(root.getAttribute('data-list-size') || '');
    var info = document.getElementById(root.getAttribute('data-list-info') || '');
    var nav = document.getElementById(root.getAttribute('data-list-nav') || '');
    if (!tbody || !nav) return;

    var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr')).filter(function (tr) {
      return tr.getAttribute('data-empty') !== '1';
    });
    if (!rows.length) return;

    var total = rows.length;
    var current = 1;

    function size() {
      return toInt(sizeSel && sizeSel.value, DEFAULT_SIZE);
    }

    function button(label, page, opts) {
      opts = opts || {};
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pager__btn';
      btn.textContent = label;
      if (opts.current) btn.setAttribute('aria-current', 'page');
      if (opts.disabled) btn.disabled = true;
      if (opts.label) btn.setAttribute('aria-label', opts.label);
      if (page && !opts.disabled) {
        btn.addEventListener('click', function () { render(page); });
      }
      return btn;
    }

    function render(page) {
      var n = size();
      var pages = Math.max(1, Math.ceil(total / n));
      current = Math.min(Math.max(1, page), pages);
      var start = (current - 1) * n;
      var end = Math.min(start + n, total);

      rows.forEach(function (tr, i) { tr.hidden = !(i >= start && i < end); });
      if (info) info.textContent = '第 ' + (start + 1) + '–' + end + ' 条，共 ' + total + ' 条';

      nav.textContent = '';
      if (pages <= 1) { nav.hidden = true; return; }
      nav.hidden = false;

      nav.appendChild(button('‹', current - 1,
        { disabled: current === 1, label: '上一页' }));
      pageSequence(current, pages).forEach(function (p) {
        if (p === 'gap') {
          var gap = document.createElement('span');
          gap.className = 'pager__gap';
          gap.textContent = '…';
          nav.appendChild(gap);
        } else {
          nav.appendChild(button(String(p), p,
            { current: p === current }));
        }
      });
      nav.appendChild(button('›', current + 1,
        { disabled: current === pages, label: '下一页' }));
    }

    if (sizeSel) sizeSel.addEventListener('change', function () { render(1); });
    render(1);
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-list]'), initList);
})();
