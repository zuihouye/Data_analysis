/* ==========================================================================
   RFM 客户分析页（rfm.js）
   --------------------------------------------------------------------------
   数据由后端算好、以两段 JSON 内嵌在页面里：
     #rfm-data   —— 聚合结果（KPI / 分群 / 切点 / 直方图 / 行动建议）
     #rfm-detail —— 客户级明细（分页表）
   这里只负责"画"和交互，不做任何 RFM 计算。
   零依赖、零构建，SVG 手写。
   ========================================================================== */
(function () {
  'use strict';

  var dataEl = document.getElementById('rfm-data');
  var detailEl = document.getElementById('rfm-detail');
  if (!dataEl) return;

  var REPORT = JSON.parse(dataEl.textContent || '{}');
  var DETAIL = detailEl ? JSON.parse(detailEl.textContent || '[]') : [];

  /* ── 视觉常量：与 app.css 的 token 同值，SVG 内直接用字面量 ───────── */
  var INK = {
    fg: 'oklch(22% 0.02 240)', soft: 'oklch(42% 0.02 240)',
    muted: 'oklch(44% 0.018 240)', border: 'oklch(90% 0.008 240)',
    border2: 'oklch(84% 0.01 240)', surface: 'oklch(100% 0 0)',
    bg: 'oklch(98% 0.005 250)', accent: 'oklch(58% 0.16 145)',
    bar: 'oklch(76% 0.014 240)'
  };
  var KEYS = ['111', '011', '101', '001', '110', '010', '100', '000'];

  /* ── 格式化 ──────────────────────────────────────────────────────── */
  var NF = new Intl.NumberFormat('zh-CN');
  function fmtN(v) { return NF.format(Math.round(v)); }
  function fmtPct(v, d) { return (v * 100).toFixed(d === undefined ? 1 : d) + '%'; }
  function fmtWan(v) {
    var w = v / 10000, a = Math.abs(w);
    var d = a >= 1000 ? 0 : a >= 100 ? 1 : 2;
    return NF.format(Number(w.toFixed(d)));
  }
  function fmtMoney(v) { return fmtWan(v) + ' 万元'; }
  function fmtUsd(v) { return '$' + fmtN(v); }
  function fmtDays(v) { return Math.round(v) + ' 天'; }
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function tipLines(lines) { return lines.map(esc).join('&#10;'); }
  function isNarrow() { return window.innerWidth < 760; }
  function $(id) { return document.getElementById(id); }

  /* ── 状态 ────────────────────────────────────────────────────────── */
  var STORE_KEY = 'dsh.rfm.report.v2';
  var state = {
    segment: null,
    sort: { key: 'rev_share', dir: 'desc' },
    detailPage: 1,
    detailSize: 20
  };

  function save() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        segment: state.segment, sort: state.sort, detailSize: state.detailSize
      }));
    } catch (e) { /* 隐私模式下 localStorage 不可用，忽略 */ }
  }

  function restore() {
    try {
      var raw = localStorage.getItem(STORE_KEY);
      if (!raw) return;
      var o = JSON.parse(raw);
      if (o && typeof o.segment === 'string' && KEYS.indexOf(o.segment) >= 0) {
        state.segment = o.segment;
      }
      if (o && o.sort && typeof o.sort.key === 'string') state.sort = o.sort;
      if (o && [10, 20, 50, 100].indexOf(Number(o.detailSize)) >= 0) {
        state.detailSize = Number(o.detailSize);
      }
    } catch (e) { /* 坏数据直接忽略 */ }
  }

  /* ── 分群与数据索引 ──────────────────────────────────────────────── */
  var SEGMAP = {};
  REPORT.segments.forEach(function (s) { SEGMAP[s.key] = s; });
  var SEGORDER = {};
  KEYS.forEach(function (k, i) { SEGORDER[k] = i; });
  var totals = REPORT.totals || { customers: 0, revenue: 0 };
  function hasData() { return totals.customers > 0; }

  function emptyState(msg) {
    return '<div class="empty">' +
      '<svg class="empty__ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
      'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M2.4 13.6h11.2"/><path d="M5 13.6V8.8"/><path d="M8 13.6V4.6"/>' +
      '<path d="M11 13.6V7.2"/></svg>' +
      '<strong>暂无数据</strong><span>' + esc(msg) + '</span></div>';
  }

  /* ==========================================================================
     1. KPI 条
     ========================================================================== */
  var KICO = (function () {
    function w(inner) {
      return '<svg class="kpi__ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
        'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        inner + '</svg>';
    }
    return {
      active: w('<circle cx="7" cy="4.9" r="2.2"/><path d="M2.6 13.6c0-2.6 1.9-4.1 4.4-4.1.9 0 1.8.2 2.5.6"/><path d="M11 11.9l1.5 1.4 2.4-2.9"/>'),
      core: w('<circle cx="8" cy="8" r="5.6"/><circle cx="8" cy="8" r="2.8"/>'),
      recency: w('<circle cx="8" cy="8" r="5.7"/><path d="M8 4.7V8l2.5 1.6"/>'),
      freq: w('<path d="M13.3 8a5.3 5.3 0 0 1-9.1 3.6"/><path d="M2.7 8a5.3 5.3 0 0 1 9.1-3.6"/><path d="M11.8 1.9v2.7h-2.7"/><path d="M4.2 14.1v-2.7h2.7"/>'),
      money: w('<path d="M4.6 3.4 8 7.4l3.4-4"/><path d="M8 7.4v5.6"/><path d="M4.9 9.9h6.2"/>')
    };
  })();

  function deltaPill(cur, prev, betterWhen, unit, digits) {
    if (prev === null || prev === undefined) {
      return '<span class="delta delta--flat">—</span>';
    }
    var diff = cur - prev;
    var dir = diff >= 0 ? 'up' : 'down';
    var good = (betterWhen === 'up' && dir === 'up') ||
               (betterWhen === 'down' && dir === 'down');
    var text;
    if (unit === 'pp') {
      text = (diff >= 0 ? '+' : '') + (diff * 100).toFixed(digits === undefined ? 1 : digits) + ' pp';
    } else if (unit === 'rel') {
      text = (diff >= 0 ? '+' : '') + (prev ? (diff / prev * 100).toFixed(1) : '0.0') + '%';
    } else {
      text = (diff >= 0 ? '+' : '') + fmtN(diff) + (unit || '');
    }
    return '<span class="delta delta--' + (good ? 'good' : 'bad') + '">' +
      (dir === 'up' ? '▲' : '▼') + ' ' + text + '</span>';
  }

  function renderKpis() {
    var k = REPORT.kpi;
    var prev = k.prev || null;
    function p(name) { return prev ? prev[name] : null; }
    var items = [
      { id: 'kpi-active-customers', ico: KICO.active, label: '有效客户数', hint: '周期内有购买',
        value: fmtN(k.customers), unit: '人',
        delta: deltaPill(k.customers, p('customers'), 'up', 'rel') },
      { id: 'kpi-core-share', ico: KICO.core, label: '核心客户占比', hint: 'R·F·M 三维均高',
        value: fmtPct(k.core_ratio), unit: '',
        delta: deltaPill(k.core_ratio, p('core_ratio'), 'up', 'pp') },
      { id: 'kpi-avg-recency', ico: KICO.recency, label: '平均 R', hint: '越近越好',
        value: String(Math.round(k.avg_r)), unit: '天',
        delta: deltaPill(k.avg_r, p('avg_r'), 'down', ' 天') },
      { id: 'kpi-avg-frequency', ico: KICO.freq, label: '人均购买频次', hint: 'F',
        value: k.avg_f.toFixed(1), unit: '次',
        delta: deltaPill(k.avg_f, p('avg_f'), 'up', 'rel') },
      { id: 'kpi-avg-monetary', ico: KICO.money, label: '人均消费金额',
        hint: 'M · 折合 ' + fmtUsd(k.avg_m_usd),
        value: fmtWan(k.avg_m), unit: '万元',
        delta: deltaPill(k.avg_m, p('avg_m'), 'up', 'rel') }
    ];
    $('kpiStrip').innerHTML = items.map(function (it) {
      return '<div class="kpi" data-od-id="' + it.id + '">' +
        '<div class="kpi__label">' + it.ico +
        '<span class="kpi__text">' + it.label + '</span>' +
        '<span class="kpi__hint">' + it.hint + '</span></div>' +
        '<div class="kpi__value">' + it.value +
        (it.unit ? '<span class="kpi__unit">' + it.unit + '</span>' : '') + '</div>' +
        '<div class="kpi__delta">' + it.delta + '<span>' +
        (prev ? '环比上一周期' : '无上一周期') + '</span></div></div>';
    }).join('');
  }

  /* ==========================================================================
     2. 分群价值矩阵（SVG 气泡 R × F，气泡面积 = 收入贡献）
     ========================================================================== */
  function renderMatrix() {
    var host = $('matrixHost');
    if (!hasData()) { host.innerHTML = emptyState('当前窗口内没有已支付订单'); return; }

    var list = REPORT.segments;
    var narrow = isNarrow();
    var g = narrow ? { w: 400, h: 340, pl: 38, pr: 16, pt: 16, pb: 42, fs: 12.5 }
                   : { w: 680, h: 360, pl: 52, pr: 22, pt: 20, pb: 48, fs: 11 };
    var pw = g.w - g.pl - g.pr, ph = g.h - g.pt - g.pb;
    var maxR = Math.max.apply(null, list.map(function (x) { return x.avg_r; })) * 1.16 || 1;
    var maxF = Math.max.apply(null, list.map(function (x) { return x.avg_f; })) * 1.22 || 1;
    var maxRev = Math.max.apply(null, list.map(function (x) { return x.revenue; })) || 1;
    function xOf(v) { return g.pl + pw * (1 - Math.min(v, maxR) / maxR); }
    function yOf(v) { return g.pt + ph * (1 - Math.min(v, maxF) / maxF); }
    function rOf(v) { return 6 + 22 * Math.sqrt(v / maxRev); }

    var cutR = REPORT.cut.r[0], cutF = REPORT.cut.f[0];
    var cutX = xOf(cutR), cutY = yOf(cutF);
    var s = [];
    s.push('<svg class="chart" viewBox="0 0 ' + g.w + ' ' + g.h +
      '" role="img" aria-label="R 与 F 的分群气泡矩阵，气泡面积为收入贡献">');

    /* 右上象限淡底纹（R 近 + F 高） */
    s.push('<rect x="' + cutX.toFixed(1) + '" y="' + g.pt + '" width="' +
      (g.pl + pw - cutX).toFixed(1) + '" height="' + (cutY - g.pt).toFixed(1) +
      '" fill="' + INK.bg + '"/>');

    /* 网格与刻度 */
    var i, tx, ty;
    for (i = 0; i <= 4; i++) {
      tx = g.pl + pw * i / 4;
      s.push('<line x1="' + tx.toFixed(1) + '" y1="' + g.pt + '" x2="' + tx.toFixed(1) +
        '" y2="' + (g.pt + ph) + '" stroke="' + INK.border + '" stroke-width="1"/>');
      s.push('<text x="' + tx.toFixed(1) + '" y="' + (g.pt + ph + 15) + '" fill="' +
        INK.muted + '" font-size="' + (g.fs - 1) + '" text-anchor="middle">' +
        Math.round(maxR * (1 - i / 4)) + '</text>');
    }
    for (i = 0; i <= 4; i++) {
      ty = g.pt + ph * i / 4;
      s.push('<line x1="' + g.pl + '" y1="' + ty.toFixed(1) + '" x2="' + (g.pl + pw) +
        '" y2="' + ty.toFixed(1) + '" stroke="' + INK.border + '" stroke-width="1"/>');
      s.push('<text x="' + (g.pl - 7) + '" y="' + (ty + 3.5).toFixed(1) + '" fill="' +
        INK.muted + '" font-size="' + (g.fs - 1) + '" text-anchor="end">' +
        (maxF * (1 - i / 4)).toFixed(0) + '</text>');
    }

    /* 高/低分界虚线 */
    s.push('<line x1="' + cutX.toFixed(1) + '" y1="' + g.pt + '" x2="' + cutX.toFixed(1) +
      '" y2="' + (g.pt + ph) + '" stroke="' + INK.border2 +
      '" stroke-width="1" stroke-dasharray="4 4"/>');
    s.push('<line x1="' + g.pl + '" y1="' + cutY.toFixed(1) + '" x2="' + (g.pl + pw) +
      '" y2="' + cutY.toFixed(1) + '" stroke="' + INK.border2 +
      '" stroke-width="1" stroke-dasharray="4 4"/>');
    s.push('<text x="' + (cutX - 5).toFixed(1) + '" y="' + (g.pt + 11) + '" fill="' +
      INK.muted + '" font-size="' + (g.fs - 2) + '" text-anchor="end">R 33 分位 ' +
      Math.round(cutR) + ' 天</text>');
    s.push('<text x="' + (g.pl + 5) + '" y="' + (cutY - 5).toFixed(1) + '" fill="' +
      INK.muted + '" font-size="' + (g.fs - 2) + '">F 67 分位 ' + cutF.toFixed(0) + ' 次</text>');

    /* 轴标题 */
    s.push('<text x="' + (g.pl + pw / 2) + '" y="' + (g.h - 6) + '" fill="' + INK.soft +
      '" font-size="' + g.fs + '" text-anchor="middle">← 越久未购买　R（天）　最近购买 →</text>');
    s.push('<text x="' + (g.pl - 30) + '" y="' + (g.pt + ph / 2) + '" fill="' + INK.soft +
      '" font-size="' + g.fs + '" text-anchor="middle" transform="rotate(-90 ' +
      (g.pl - 30) + ' ' + (g.pt + ph / 2) + ')">人均购买频次 F（次）</text>');

    /* 气泡：小的先画，大的后画并压白描边 */
    var nodes = list.slice().sort(function (a, b) { return a.revenue - b.revenue; })
      .map(function (x) {
        return { x: x, cx: xOf(x.avg_r), cy: yOf(x.avg_f), r: rOf(x.revenue) };
      });
    nodes.forEach(function (n) {
      var on = state.segment === n.x.key;
      var dim = state.segment && !on;
      s.push('<circle cx="' + n.cx.toFixed(1) + '" cy="' + n.cy.toFixed(1) + '" r="' +
        n.r.toFixed(1) + '" fill="' + n.x.color + '" fill-opacity="' + (dim ? 0.22 : 0.9) +
        '" stroke="' + INK.surface + '" stroke-width="' + (on ? 3 : 1.8) +
        '" style="cursor:pointer" data-seg="' + n.x.key + '" data-tip="' + tipLines([
          n.x.name + '　' + fmtN(n.x.count) + ' 人（' + fmtPct(n.x.share) + '）',
          '人均 R ' + fmtDays(n.x.avg_r) + ' · 人均 F ' + n.x.avg_f.toFixed(1) +
          ' 次 · 人均 M ' + fmtMoney(n.x.avg_m),
          '收入贡献 ' + fmtMoney(n.x.revenue) + '（' + fmtPct(n.x.rev_share) + '）'
        ]) + '"/>');
    });

    /* 标签：四个候选方向做避让 */
    var placed = [];
    nodes.slice().sort(function (a, b) { return b.x.revenue - a.x.revenue; })
      .forEach(function (n) {
        var label = n.x.short, fs = g.fs;
        var wpx = label.length * fs * 1.05 + 12, hpx = fs + 9;
        var pad = 5;
        var cands = [
          { x: n.cx + n.r + pad, y: n.cy - 2, a: 'start' },
          { x: n.cx - n.r - pad, y: n.cy - 2, a: 'end' },
          { x: n.cx, y: n.cy - n.r - pad, a: 'middle' },
          { x: n.cx, y: n.cy + n.r + pad + fs, a: 'middle' }
        ];
        var pick = null;
        for (var ci = 0; ci < cands.length && !pick; ci++) {
          var c = cands[ci];
          var x0 = c.a === 'start' ? c.x : c.a === 'end' ? c.x - wpx : c.x - wpx / 2;
          var rect = { x0: x0, y0: c.y - fs, x1: x0 + wpx, y1: c.y + hpx - fs };
          if (rect.x0 < g.pl || rect.x1 > g.pl + pw ||
              rect.y0 < g.pt - 4 || rect.y1 > g.pt + ph + 2) continue;
          var clash = placed.some(function (p) {
            return !(rect.x1 < p.x0 || rect.x0 > p.x1 ||
                     rect.y1 < p.y0 || rect.y0 > p.y1);
          });
          if (!clash) pick = { c: c, rect: rect };
        }
        if (!pick) {
          var cl = { x: n.cx + n.r + pad, y: n.cy - 2, a: 'start' };
          pick = { c: cl, rect: { x0: cl.x, y0: cl.y - fs, x1: cl.x + wpx, y1: cl.y + hpx - fs } };
        }
        placed.push(pick.rect);
        if (state.segment === n.x.key) {
          var lx = pick.c.a === 'start' ? pick.c.x - 4
            : pick.c.a === 'end' ? pick.c.x - wpx + 4 : pick.c.x - wpx / 2;
          s.push('<rect x="' + (lx - 3).toFixed(1) + '" y="' + (pick.c.y - fs - 3).toFixed(1) +
            '" width="' + (wpx + 2).toFixed(1) + '" height="' + (fs + 10) +
            '" rx="3" fill="' + INK.surface + '" stroke="' + n.x.color + '" stroke-width="1.2"/>');
        }
        s.push('<text x="' + pick.c.x.toFixed(1) + '" y="' + pick.c.y.toFixed(1) +
          '" fill="' + INK.fg + '" font-size="' + fs + '" font-weight="600" text-anchor="' +
          pick.c.a + '">' + esc(label) + '</text>');
      });
    s.push('</svg>');
    host.innerHTML = s.join('');

    /* 图例：按分群顺序，标出收入占比 */
    var order = list.slice().sort(function (a, b) {
      return SEGORDER[a.key] - SEGORDER[b.key];
    });
    $('matrixLegend').innerHTML = order.map(function (x) {
      return '<span><i style="background:' + x.color + '"></i>' + esc(x.name) +
        ' <span class="mono" style="color:var(--muted)">' + fmtPct(x.rev_share) + '</span></span>';
    }).join('');
  }

  /* ==========================================================================
     3. 分群贡献排行（灰条 = 客户占比，彩条 = 收入占比）
     ========================================================================== */
  function renderRank() {
    var host = $('rankHost');
    if (!hasData()) { host.innerHTML = emptyState('暂无分群数据'); return; }
    var list = REPORT.segments.slice().sort(function (a, b) {
      return b.rev_share - a.rev_share;
    });
    var maxShare = Math.max.apply(null, list.map(function (x) {
      return Math.max(x.share, x.rev_share);
    })) || 1;
    host.innerHTML = list.map(function (x) {
      var on = state.segment === x.key;
      var ratio = x.share ? x.rev_share / x.share : 0;
      return '<button class="rank' + (on ? ' is-on' : '') + '" data-seg="' + x.key +
        '" data-tip="' + tipLines([
          x.name + '　客户 ' + fmtN(x.count) + ' 人 · 收入 ' + fmtMoney(x.revenue),
          '收入占比 ' + fmtPct(x.rev_share) + ' ÷ 客户占比 ' + fmtPct(x.share) +
          ' = 贡献倍数 ' + ratio.toFixed(2) + '×'
        ]) + '">' +
        '<span class="rank__top"><i style="background:' + x.color + '"></i>' +
        '<span class="rank__name">' + esc(x.name) + '</span>' +
        '<span class="rank__meta">' + fmtPct(x.share) + ' · ' + fmtPct(x.rev_share) +
        '</span></span>' +
        '<span class="rank__track">' +
        '<span class="rank__fill rank__fill--cust" style="width:' +
        (x.share / maxShare * 100).toFixed(1) + '%"></span>' +
        '<span class="rank__fill rank__fill--rev" style="width:' +
        (x.rev_share / maxShare * 100).toFixed(1) + '%;background:' + x.color + '"></span>' +
        '</span></button>';
    }).join('');
  }

  /* ==========================================================================
     4. 分布直方图
     ========================================================================== */
  function binIndex(bins, v) {
    for (var i = 0; i < bins.length; i++) {
      if (bins[i].hi === null || v < bins[i].hi) return i;
    }
    return bins.length - 1;
  }

  function cutFrac(bins, scale, v, bw) {
    var i = binIndex(bins, v), b = bins[i];
    var hi = b.hi === null ? (scale === 'log' ? (b.lo || 1) * 2 : b.lo + 1) : b.hi;
    var frac;
    if (scale === 'log' && b.lo > 0) {
      frac = (Math.log(Math.max(v, b.lo * 1.0001)) - Math.log(b.lo)) /
             (Math.log(hi) - Math.log(b.lo));
    } else {
      frac = (v - b.lo) / ((hi - b.lo) || 1);
    }
    return (i + Math.min(1, Math.max(0, frac))) * bw;
  }

  function renderDist() {
    var host = $('distHost');
    if (!hasData()) { host.innerHTML = emptyState('暂无分布数据'); return; }
    var dims = ['r', 'f', 'm'];
    var titles = { r: 'R 分布（最近购买距今）', f: 'F 分布（周期内购买次数）', m: 'M 分布（周期内消费金额）' };
    var descs = {
      r: '越靠左越近期活跃，深色柱为高分区（前 1/3）',
      f: '深色柱为高频区（前 1/3）',
      m: '深色柱为高消费区（前 1/3），横轴为对数分桶'
    };
    var narrow = isNarrow();
    host.innerHTML = dims.map(function (dim) {
      var h = REPORT.hist[dim];
      var unitW = Math.ceil(h.unit.length * 9.5) + 6;
      var g = narrow ? { w: 420, h: 190, pl: 30, pr: 12 + unitW, pt: 22, pb: 30, fs: 10.5 }
                     : { w: 380, h: 176, pl: 30, pr: 12 + unitW, pt: 22, pb: 30, fs: 10.5 };
      var pw = g.w - g.pl - g.pr, ph = g.h - g.pt - g.pb;
      var n = h.bins.length, bw = pw / n;
      var max = Math.max.apply(null, h.bins.map(function (b) { return b.count; })) || 1;
      var cut = cutFrac(h.bins, h.scale, h.cut, bw);
      var s = [];
      s.push('<svg class="chart" viewBox="0 0 ' + g.w + ' ' + g.h +
        '" role="img" aria-label="' + dim.toUpperCase() + ' 分布直方图">');
      s.push('<line x1="' + g.pl + '" y1="' + (g.pt + ph) + '" x2="' + (g.pl + pw) +
        '" y2="' + (g.pt + ph) + '" stroke="' + INK.border2 + '"/>');
      h.bins.forEach(function (b, i) {
        if (!b.count) return;
        var bh = Math.max(2, ph * b.count / max);
        var x = g.pl + i * bw + 1, w = Math.max(2, bw - 2);
        var mid = b.hi === null ? b.lo * 1.6 : (b.lo + b.hi) / 2;
        var good = dim === 'r' ? mid <= h.cut : mid >= h.cut;
        var range = b.hi === null ? '≥' + b.label.replace(/^[≥≤]/, '') : b.label;
        var title = dim.toUpperCase() + ' ' + range + ' ' + h.unit + '　' +
          fmtN(b.count) + ' 人 · ' + fmtPct(b.count / totals.customers);
        s.push('<rect x="' + x.toFixed(1) + '" y="' + (g.pt + ph - bh).toFixed(1) +
          '" width="' + w.toFixed(1) + '" height="' + bh.toFixed(1) +
          '" rx="1.5" fill="' + (good ? INK.accent : INK.bar) +
          '" data-tip="' + esc(title) + '"/>');
      });
      s.push('<line x1="' + (g.pl + cut).toFixed(1) + '" y1="' + g.pt + '" x2="' +
        (g.pl + cut).toFixed(1) + '" y2="' + (g.pt + ph) + '" stroke="' + INK.fg +
        '" stroke-width="1" stroke-dasharray="3 3"/>');
      s.push('<text x="' + (g.pl + cut + 4).toFixed(1) + '" y="' + (g.pt - 6) +
        '" fill="' + INK.soft + '" font-size="' + (g.fs - 1) + '">高/低分界 ' +
        esc(h.cut_label) + '</text>');
      var every = narrow ? Math.ceil(n / 6) : Math.ceil(n / 7);
      h.bins.forEach(function (b, i) {
        if (i % every) return;
        s.push('<text x="' + (g.pl + i * bw + bw / 2).toFixed(1) + '" y="' +
          (g.pt + ph + 13) + '" fill="' + INK.muted + '" font-size="' + (g.fs - 1) +
          '" text-anchor="middle">' + esc(b.label) + '</text>');
      });
      s.push('<text x="' + (g.w - 2) + '" y="' + (g.pt + ph + 13) + '" fill="' +
        INK.muted + '" font-size="' + (g.fs - 1) + '" text-anchor="end">' +
        esc(h.unit) + '</text>');
      s.push('</svg>');
      var cap = '三分位切点（33% / 67%）　' + h.cuts_labels.join(' / ');
      return '<div class="card" data-od-id="dist-' + dim + '">' +
        '<div class="dist__hd"><h3>' + titles[dim] + '</h3><p>' + descs[dim] + '</p></div>' +
        '<div style="padding:0 10px">' + s.join('') + '</div>' +
        '<div class="dist__cap">' + esc(cap) + '</div></div>';
    }).join('');
  }

  /* ==========================================================================
     5. 分群明细表（可排序、点击行筛选）
     ========================================================================== */
  function rfmPill(key) {
    var names = ['R', 'F', 'M'];
    return '<span class="rfm">' + [key.charAt(0), key.charAt(1), key.charAt(2)]
      .map(function (h, i) {
        return '<b data-h="' + h + '">' + names[i] + (h === '1' ? '高' : '低') + '</b>';
      }).join('') + '</span>';
  }

  function tableRows() {
    var rows = REPORT.segments.map(function (x) {
      return {
        key: x.key, name: x.name, color: x.color, action: x.action,
        count: x.count, share: x.share, avg_r: x.avg_r, avg_f: x.avg_f,
        avg_m: x.avg_m, revenue: x.revenue, rev_share: x.rev_share,
        count_delta: x.count_delta, order: SEGORDER[x.key]
      };
    });
    var k = state.sort.key, dir = state.sort.dir === 'asc' ? 1 : -1;
    rows.sort(function (a, b) {
      var va = a[k], vb = b[k];
      if (va === vb) return 0;
      return va < vb ? -dir : dir;
    });
    return rows;
  }

  function renderTable() {
    var host = $('tableHost');
    if (!hasData()) {
      host.innerHTML = emptyState('当前窗口内没有已支付订单，先到「导入数据」导入历史流水');
      return;
    }
    var rows = tableRows();
    var cols = [
      { k: 'order', t: '分群（R·F·M）', s: false },
      { k: 'count', t: '客户数', n: true },
      { k: 'share', t: '客户占比', n: true },
      { k: 'avg_r', t: '人均 R', n: true },
      { k: 'avg_f', t: '人均 F', n: true },
      { k: 'avg_m', t: '人均 M', n: true },
      { k: 'rev_share', t: '收入占比', n: true },
      { k: 'count_delta', t: '人数环比', n: true },
      { k: 'action', t: '建议动作', s: false }
    ];
    var head = '<tr>' + cols.map(function (c) {
      var cls = c.n ? ' class="num"' : '';
      if (c.s === false) return '<th' + cls + '>' + c.t + '</th>';
      var on = state.sort.key === c.k;
      return '<th' + cls + '><button data-sort="' + c.k + '" aria-label="按' + c.t + '排序">' + c.t +
        '<span class="arw' + (on ? ' on' : '') + '">' +
        (on ? (state.sort.dir === 'asc' ? '▲' : '▼') : '▲▼') + '</span></button></th>';
    }).join('') + '</tr>';

    var body = rows.map(function (x) {
      var on = state.segment === x.key;
      var dn = '—';
      var dnClass = '';
      if (x.count_delta !== null && x.count_delta !== undefined) {
        dn = (x.count_delta >= 0 ? '+' : '') + (x.count_delta * 100).toFixed(1) + '%';
        dnClass = x.count_delta >= 0 ? 'up' : 'down';
      }
      return '<tr data-seg="' + x.key + '"' + (on ? ' class="is-on"' : '') + '>' +
        '<td><span class="segname"><i style="background:' + x.color + '"></i>' +
        esc(x.name) + ' ' + rfmPill(x.key) + '</span></td>' +
        '<td class="num" data-l="客户数">' + fmtN(x.count) + '</td>' +
        '<td class="num" data-l="客户占比">' + fmtPct(x.share) + '</td>' +
        '<td class="num" data-l="人均 R">' + fmtDays(x.avg_r) + '</td>' +
        '<td class="num" data-l="人均 F">' + x.avg_f.toFixed(1) + ' 次</td>' +
        '<td class="num" data-l="人均 M">' + fmtMoney(x.avg_m) + '</td>' +
        '<td class="num" data-l="收入占比">' + fmtPct(x.rev_share) + '</td>' +
        '<td class="delta-cell ' + dnClass + '" data-l="人数环比">' + dn + '</td>' +
        '<td data-l="建议动作"><span class="adv">' + esc(x.action) + '</span></td></tr>';
    }).join('');
    host.innerHTML = '<table class="tbl"><thead>' + head + '</thead><tbody>' + body + '</tbody></table>';
  }

  /* ==========================================================================
     6. 行动建议（文案由后端按本期数据生成）
     ========================================================================== */
  function renderActions() {
    var host = $('actHost');
    if (!hasData() || !REPORT.actions.length) {
      host.innerHTML = emptyState('暂无行动建议');
      return;
    }
    host.innerHTML = REPORT.actions.map(function (it, i) {
      var seg = SEGMAP[it.seg] || { name: '' };
      return '<article class="act" data-od-id="action-' + (i + 1) + '">' +
        '<div class="act__top"><span class="act__p">' + esc(it.p) + '</span>' +
        '<span class="act__tag">' + esc(it.tag) + '</span>' +
        '<span class="card__note" style="margin-left:auto">' + esc(seg.name) + '</span></div>' +
        '<h3>' + esc(it.title) + '</h3><p>' + esc(it.body) + '</p>' +
        '<div class="act__stats">' + it.stats.map(function (st) {
          return '<span>' + esc(st[0]) + ' <b>' + esc(st[1]) + '</b></span>';
        }).join('') + '</div>' +
        '<button class="act__go" type="button" data-seg="' + esc(it.seg) + '">' +
        '在图表中筛选该分群 →</button></article>';
    }).join('');
  }

  /* ==========================================================================
     7. 筛选提示条
     ========================================================================== */
  function renderFilterBar() {
    var bar = $('filterBar');
    if (!bar) return;
    if (!state.segment) { bar.hidden = true; $('filterChip').innerHTML = ''; return; }
    var x = SEGMAP[state.segment];
    bar.hidden = false;
    $('filterChip').innerHTML = '<i style="background:' + x.color + '"></i>已筛选：' +
      esc(x.name) + '　<span class="mono" style="color:var(--fg-soft)">' +
      fmtN(x.count) + ' 人 · ' + fmtPct(x.share) + ' · 收入 ' + fmtPct(x.rev_share) +
      '</span>';
  }

  /* ==========================================================================
     8. 客户级明细表（前端分页 + 与分群筛选联动）
     ========================================================================== */
  function detailRowsFiltered() {
    if (!state.segment) return DETAIL;
    return DETAIL.filter(function (r) { return r.segment_key === state.segment; });
  }

  function ratingBadge(level) {
    var cls = level === '3' ? 'badge--ok' : level === '2' ? 'badge--warn' : 'badge--danger';
    return '<span class="badge ' + cls + '">' + esc(level) + '</span>';
  }

  function pagerPages(totalPages, cur) {
    var pages = [];
    var maxBtn = 7;
    if (totalPages <= maxBtn) {
      for (var i = 1; i <= totalPages; i++) pages.push(i);
      return pages;
    }
    pages.push(1);
    var lo = Math.max(2, cur - 2), hi = Math.min(totalPages - 1, cur + 2);
    if (lo > 2) pages.push('...');
    for (var j = lo; j <= hi; j++) pages.push(j);
    if (hi < totalPages - 1) pages.push('...');
    pages.push(totalPages);
    return pages;
  }

  function renderDetail() {
    var body = $('detailBody');
    var info = $('detailInfo');
    var nav = $('detailNav');
    if (!body) return;
    var rows = detailRowsFiltered();
    var size = state.detailSize;
    var totalPages = Math.max(1, Math.ceil(rows.length / size));
    state.detailPage = Math.min(Math.max(1, state.detailPage), totalPages);
    var start = (state.detailPage - 1) * size;
    var slice = rows.slice(start, start + size);

    if (!slice.length) {
      body.innerHTML = '<tr><td colspan="10">' + emptyState(
        state.segment ? '该分群在当前窗口内没有客户' : '当前窗口内没有已支付订单') + '</td></tr>';
    } else {
      body.innerHTML = slice.map(function (r) {
        return '<tr>' +
          '<td class="mono">' + esc(r.customer_id) + '</td>' +
          '<td data-l="地区">' + esc(r.region) + '</td>' +
          '<td class="num" data-l="R（天）">' + r.r + '</td>' +
          '<td class="num" data-l="F（次）">' + r.f + '</td>' +
          '<td class="num" data-l="M（万元）">' + fmtWan(r.m) + '</td>' +
          '<td data-l="R 评级">' + ratingBadge(r.r_leval) + '</td>' +
          '<td data-l="F 评级">' + ratingBadge(r.f_leval) + '</td>' +
          '<td data-l="M 评级">' + ratingBadge(r.m_leval) + '</td>' +
          '<td class="num" data-l="RFM 分组">' + esc(r.rfm_group) + '</td>' +
          '<td data-l="客户分群"><span class="segname"><i style="background:' +
          r.segment_color + '"></i>' + esc(r.segment) + '</span></td></tr>';
      }).join('');
    }

    if (info) {
      info.textContent = rows.length
        ? '第 ' + (start + 1) + '–' + Math.min(start + size, rows.length) + ' 条，共 ' +
          rows.length + ' 条' + (state.segment ? '（已筛选分群）' : '')
        : '共 0 条';
    }

    if (nav) {
      var pages = pagerPages(totalPages, state.detailPage);
      nav.innerHTML =
        '<button class="pager__btn" type="button" data-page="' + (state.detailPage - 1) + '"' +
        (state.detailPage <= 1 ? ' disabled' : '') + ' aria-label="上一页">«</button>' +
        pages.map(function (p) {
          if (p === '...') return '<span class="pager__gap">…</span>';
          return '<button class="pager__btn" type="button" data-page="' + p + '"' +
            (p === state.detailPage ? ' aria-current="page"' : '') + '>' + p + '</button>';
        }).join('') +
        '<button class="pager__btn" type="button" data-page="' + (state.detailPage + 1) + '"' +
        (state.detailPage >= totalPages ? ' disabled' : '') + ' aria-label="下一页">»</button>';
    }
  }

  /* ==========================================================================
     9. 交互
     ========================================================================== */
  function setSegment(key) {
    state.segment = state.segment === key ? null : key;
    state.detailPage = 1;
    save();
    renderAll();
  }

  function renderAll() {
    renderKpis();
    renderMatrix();
    renderRank();
    renderDist();
    renderTable();
    renderActions();
    renderFilterBar();
    renderDetail();
  }

  /* 点击：分群筛选 / 表头排序 / 清除筛选 / 导出 / 账号菜单动作 */
  document.addEventListener('click', function (ev) {
    var segEl = ev.target.closest('[data-seg]');
    if (segEl) { setSegment(segEl.dataset.seg); return; }

    var sortEl = ev.target.closest('[data-sort]');
    if (sortEl) {
      var k = sortEl.dataset.sort;
      if (state.sort.key === k) {
        state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
      } else {
        state.sort = { key: k, dir: 'desc' };
      }
      save();
      renderTable();
      return;
    }

    var clearBtn = ev.target.closest('#clearFilter');
    if (clearBtn) {
      state.segment = null;
      state.detailPage = 1;
      save();
      renderAll();
      return;
    }

    var exportBtn = ev.target.closest('#exportBtn');
    if (exportBtn) { window.print(); return; }

    var acc = ev.target.closest('[data-acc]');
    if (acc) {
      if (window.Dsh) window.Dsh.closeAccount();
      var kind = acc.dataset.acc;
      if (kind === 'print') { window.print(); }
      else if (kind === 'reset') {
        state.segment = null;
        state.sort = { key: 'rev_share', dir: 'desc' };
        state.detailPage = 1;
        save();
        renderAll();
      } else if (kind === 'gloss') {
        var g = document.querySelector('details.gloss');
        if (g) { g.open = true; g.querySelector('summary').focus(); }
      }
      return;
    }

    var pageBtn = ev.target.closest('#detailNav [data-page]');
    if (pageBtn && !pageBtn.disabled) {
      state.detailPage = parseInt(pageBtn.dataset.page, 10);
      renderDetail();
    }
  });

  /* 每页条数 */
  document.addEventListener('change', function (ev) {
    if (ev.target && ev.target.id === 'detailSize') {
      state.detailSize = parseInt(ev.target.value, 10) || 20;
      state.detailPage = 1;
      save();
      renderDetail();
    }
  });

  /* 缩放时重画两张 SVG（宽度变了但 viewBox 不变，重算排版） */
  var rz;
  window.addEventListener('resize', function () {
    clearTimeout(rz);
    rz = setTimeout(function () { renderMatrix(); renderDist(); }, 160);
  });

  /* 打印前把口径说明展开（打印出来是全的） */
  window.addEventListener('beforeprint', function () {
    var g = document.querySelector('details.gloss');
    if (g) g.open = true;
  });

  /* ── 启动 ────────────────────────────────────────────────────────── */
  restore();
  var sizeSel = $('detailSize');
  if (sizeSel) sizeSel.value = String(state.detailSize);
  renderAll();
})();
