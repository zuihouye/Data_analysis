/* 表单页的轻量交互（零依赖）：
 *   1) 产品表单：输入原始单价（MMK）时实时显示折合人民币；
 *   2) 下单 / 改数量：按「单价 × 数量」实时预览应付金额（人民币）。
 * 只做展示补强，真正保存的数值仍以服务端为准（单价取数据库 / 下单快照）。
 */
(function () {
  'use strict';

  // 千分位 + 两位小数：857.1348 -> 857.13
  function money(n) {
    if (!isFinite(n)) return '—';
    return n.toLocaleString('zh-CN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  /* ---------- 1. 产品表单：MMK 单价 -> 折合人民币 ---------- */
  var priceForm = document.querySelector('[data-price-form]');
  if (priceForm) {
    var ppuInput = priceForm.querySelector('input[name="ppu"]');
    var priceOuts = document.querySelectorAll('[data-price-out]');
    var rate = parseFloat(priceForm.getAttribute('data-rate')) || 0;
    var updatePrice = function () {
      var v = parseFloat(ppuInput && ppuInput.value);
      var text = (!isFinite(v) || v <= 0) ? '—' : '¥ ' + money(rate ? v / rate : 0);
      Array.prototype.forEach.call(priceOuts, function (el) { el.textContent = text; });
    };
    if (ppuInput) {
      ppuInput.addEventListener('input', updatePrice);
      updatePrice();
    }
  }

  /* ---------- 2. 应付金额预览：单价 × 数量 ---------- */
  var calc = document.querySelector('[data-amount-calc]');
  if (calc) {
    var rate2 = parseFloat(calc.getAttribute('data-rate')) || 0;
    var qtyInput = calc.querySelector('input[name="quantity"]') ||
      document.querySelector('input[name="quantity"]');
    var selectId = calc.getAttribute('data-ppu-source');
    var select = selectId ? document.querySelector(selectId) : null;
    var unitOut = calc.querySelector('[data-unit-price]');
    var amountOut = calc.querySelector('[data-amount-out]');
    var fixedPpu = parseFloat(calc.getAttribute('data-ppu'));

    var readPpu = function () {
      if (select) {
        var opt = select.options[select.selectedIndex];
        var raw = opt ? parseFloat(opt.getAttribute('data-ppu')) : 0;
        return isFinite(raw) ? raw : 0;
      }
      return isFinite(fixedPpu) ? fixedPpu : 0;
    };

    var updateAmount = function () {
      var qty = Math.max(parseInt(qtyInput && qtyInput.value, 10) || 0, 0);
      var ppu = readPpu();
      var unitCny = rate2 ? ppu / rate2 : 0;
      if (unitOut) unitOut.textContent = '¥ ' + money(unitCny);
      if (amountOut) amountOut.textContent = '¥ ' + money(unitCny * qty);
    };

    if (qtyInput) {
      qtyInput.addEventListener('input', updateAmount);
      qtyInput.addEventListener('change', updateAmount);
    }
    if (select) select.addEventListener('change', updateAmount);
    updateAmount();
  }
})();
