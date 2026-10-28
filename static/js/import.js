/* 导入页：POST /clear_input（CSRF 走请求头），把质量报告与数据预览渲染出来。
   两种模式：默认「开始导入」遇到业务数据会被 409 拦截；
   「清空并重建」需要先输入确认文字，POST 时带 force_clear=true 显式授权。 */
(function () {
  var btn = document.getElementById('importBtn');
  var forceBtn = document.getElementById('forceBtn');
  var forceWord = document.getElementById('forceWord');
  var forceHint = document.getElementById('forceHint');
  var forceZone = document.getElementById('forceZone');
  var box = document.getElementById('importResult');
  var note = document.getElementById('resultNote');
  var hint = document.getElementById('importHint');
  if (!btn || !box) return;

  var busy = false;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
  }

  function stat(label, value) {
    return '<div><span>' + esc(label) + '</span><b>' + esc(value) + '</b></div>';
  }

  function setCount(id, value) {
    var el = document.getElementById(id);
    if (el && value != null) el.textContent = Number(value).toLocaleString();
  }

  function renderSuccess(data) {
    box.classList.remove('empty');  // 初始空态是 flex 居中布局，渲染结果前要去掉
    var q = data.quality_report || {};
    var warns = q.warnings || [];
    var errs = q.errors || [];
    var preview = data.data_preview || [];
    var cols = preview.length ? Object.keys(preview[0]) : [];
    var counts = data.counts || {};

    var html = '';
    html += '<div class="statrow" style="margin-bottom:14px">';
    html += stat('导入行数', (data.total_rows || 0).toLocaleString());
    html += stat('校验通过', (q.valid != null ? q.valid : '—'));
    html += stat('剔除行数', (q.dropped != null ? q.dropped : '—'));
    html += stat('警告', warns.length);
    html += stat('错误', errs.length);
    html += '</div>';

    if (counts.customers != null) {
      html += '<div class="statrow" style="margin-bottom:14px">';
      html += stat('写入客户', Number(counts.customers).toLocaleString());
      html += stat('写入商品', Number(counts.products).toLocaleString());
      html += stat('写入订单', Number(counts.orders).toLocaleString());
      html += '</div>';
      setCount('cnt-customers', counts.customers);
      setCount('cnt-products', counts.products);
      setCount('cnt-orders', counts.orders);
    }

    if (errs.length || warns.length) {
      html += '<div class="flash ' + (errs.length ? 'flash--danger' : 'flash--warning') + '" style="margin:0 0 14px">';
      if (errs.length) html += '<b>数据质量错误：</b><br>' + errs.map(function (e) {
        return '· ' + esc(e.check) + '（' + esc(e.count) + ' 行）：' + esc(e.detail || e.message || '');
      }).join('<br>');
      if (warns.length) html += (errs.length ? '<br>' : '') + '<b>数据质量警告：</b><br>' + warns.map(function (w) {
        return '· ' + esc(w.check) + '（' + esc(w.count) + ' 行）：' + esc(w.detail || w.message || '');
      }).join('<br>');
      html += '</div>';
    }

    if (cols.length) {
      // 预览列按业务习惯排：客户/地区/日期/商品/数量/单价/金额/流水号（其余列追加在后）
      var ORDER = ['customer_id', 'region', 'order_date', 'product_id', 'product_name',
        'product_category', 'quantity', 'ppu', 'amount', 'transaction_id'];
      var NUM_COLS = { quantity: true, ppu: true, amount: true };
      var ordered = ORDER.filter(function (c) { return cols.indexOf(c) >= 0; })
        .concat(cols.filter(function (c) { return ORDER.indexOf(c) < 0; }));

      html += '<div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px">';
      html += '<span class="card__note">数据预览 · 前 ' + preview.length + ' 行</span>';
      html += '<span class="card__note mono">共 ' + ordered.length + ' 列 · 金额为 MMK 原值，可横向滚动</span></div>';
      html += '<div class="tblwrap tblwrap--scroll" style="border:1px solid var(--border);border-radius:var(--r)">' +
        '<table class="tbl"><thead><tr>';
      ordered.forEach(function (c) {
        html += '<th' + (NUM_COLS[c] ? ' class="num"' : '') + '>' + esc(c) + '</th>';
      });
      html += '</tr></thead><tbody>';
      preview.forEach(function (row) {
        html += '<tr>';
        ordered.forEach(function (c) {
          var v = row[c];
          var text = v;
          if (NUM_COLS[c] && v != null && v !== '' && !isNaN(Number(v))) {
            text = Number(v).toLocaleString('zh-CN');
          }
          html += '<td class="mono' + (NUM_COLS[c] ? ' num' : '') + '">' + esc(text) + '</td>';
        });
        html += '</tr>';
      });
      html += '</tbody></table></div>';
    }
    box.innerHTML = html;
  }

  function renderError(msg, status, forced) {
    box.classList.remove('empty');
    var html = '<div class="flash flash--error" style="margin:0"><b>' +
      (forced ? '清空重建失败' : '导入未执行') + '</b><br>' +
      esc(msg) + (status ? '（HTTP ' + status + '）' : '') + '</div>';
    if (status === 409 && !forced) {
      html += '<p class="hint" style="margin:12px 0 0">' +
        '确认要覆盖现有业务数据时，可回到左侧「清空并重建」，输入「清空」后重试。</p>';
    }
    box.innerHTML = html;
  }

  function setBusy(on, trigger) {
    busy = on;
    [btn, forceBtn].forEach(function (b) {
      if (!b) return;
      b.disabled = on;
      if (b === forceBtn) b.textContent = on && trigger === forceBtn ? '清空中…' : '清空业务数据并导入';
      else b.textContent = on && trigger === btn ? '导入中…' : '开始导入';
    });
    if (forceWord) forceWord.disabled = on;
    if (hint) {
      hint.textContent = on && trigger === btn
        ? '正在清洗、校验并写入数据库，请勿关闭页面…'
        : '将清空并重建三张业务表；写库前会做数据质量校验与业务数据保护检查';
    }
    if (forceHint) forceHint.textContent = on ? '正在执行，请勿关闭页面…' : '输入正确后按钮才会启用';
  }

  function runImport(force) {
    if (busy) return;
    var trigger = force ? forceBtn : btn;
    setBusy(true, trigger);
    note.textContent = '执行中';
    box.classList.remove('empty');
    box.innerHTML = '<div class="empty"><strong>' + (force ? '正在清空并导入…' : '正在导入…') +
      '</strong><span>大文件可能需要几十秒。</span></div>';

    fetch('/clear_input', {
      method: 'POST',
      headers: {
        'X-CSRF-Token': csrf(),
        'Accept': 'application/json',
        'Content-Type': 'application/json'
      },
      credentials: 'same-origin',
      body: JSON.stringify({ force_clear: !!force })
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          renderError(data.message || '请求失败，请确认登录状态与管理员权限。', res.status, force);
          note.textContent = '失败';
          return;
        }
        renderSuccess(data);
        if (force && forceZone) forceZone.remove();  // 业务数据已清空，危险区不再需要
        note.innerHTML = '<span class="badge badge--ok">已完成</span>';
      });
    }).catch(function (err) {
      renderError('网络或服务端异常：' + err.message, 0, force);
      note.textContent = '失败';
    }).then(function () {
      setBusy(false, trigger);
    });
  }

  btn.addEventListener('click', function () {
    if (busy) return;
    if (!window.confirm('将删除并重建 customers / products / orders 三张表，确定继续？')) return;
    runImport(false);
  });

  if (forceBtn) {
    // 输入「清空」两个字才解锁，避免误点
    var syncForce = function () {
      var ok = forceWord && forceWord.value.trim() === '清空';
      forceBtn.disabled = !ok || busy;
      if (forceHint) forceHint.textContent = ok ? '已确认，点击后将清空业务数据并重新导入' : '输入正确后按钮才会启用';
    };
    if (forceWord) forceWord.addEventListener('input', syncForce);
    forceBtn.addEventListener('click', function () {
      if (busy) return;
      if (!forceWord || forceWord.value.trim() !== '清空') return;
      if (!window.confirm('将删除全部客户、商品、订单与客户账号，然后导入 CSV。此操作不可撤销，确定继续？')) return;
      runImport(true);
    });
    syncForce();
  }
})();
