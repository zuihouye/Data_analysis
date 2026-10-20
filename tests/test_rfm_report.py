"""utils/rfm_report.py 的单元测试。

纯 DataFrame，不碰数据库：RFM 分层的正确性由 test_rfm.py 保证，
这里只验证"页面需要的聚合结果"和 RFM 明细自洽。
"""
import pandas as pd
import pytest

from utils.rfm import SEGMENT_ORDER, rfm_table
from utils.rfm_report import (CNY_PER_USD, MMK_PER_CNY, build_report,
                              detail_rows)

SNAPSHOT = pd.Timestamp('2025-06-30')


def _customers(n=6):
    return pd.DataFrame({
        'customer_id': [f'C{i}' for i in range(1, n + 1)],
        'region': [f'地区{i}' for i in range(1, n + 1)],
        'cus_status': [1] * n,
    })


def _orders():
    """两个等长周期：当前 4–6 月，上一周期 1–3 月。

    每个客户每周期至少 1 单，金额/频次刻意不同，保证三分位能切出三档。
    """
    rows = []
    tid = 0

    def add(cid, date, amount, qty=1):
        nonlocal tid
        tid += 1
        rows.append({
            'transaction_id': f'T{tid}',
            'order_date': pd.Timestamp(date),
            'customer_id': cid,
            'product_id': 'P1',
            'quantity': qty,
            'ppu': amount / qty,
            'amount': amount,
            'order_status': 2,
        })

    # 当前周期（<= 2025-06-30，> 2025-03-30）
    add('C1', '2025-06-28', 900)   # 最近、大额
    add('C1', '2025-06-10', 800)
    add('C2', '2025-06-20', 300)
    add('C3', '2025-06-01', 120)
    add('C4', '2025-05-15', 60)
    add('C5', '2025-04-20', 30)
    add('C6', '2025-04-01', 10)
    add('C6', '2025-06-25', 15)

    # 上一周期（2025-01-01 ~ 2025-03-30）
    add('C1', '2025-03-20', 400)
    add('C2', '2025-03-10', 200)
    add('C3', '2025-02-15', 150)
    add('C4', '2025-02-01', 90)
    add('C5', '2025-01-20', 45)
    return pd.DataFrame(rows)


def _report(window='3m'):
    customers_df, orders_df = _customers(), _orders()
    # 与 views/ge_RFM.py 一致：先过滤已支付
    paid = orders_df[orders_df['order_status'] == 2]
    rfm_df, meta = rfm_table(customers_df, paid, window)
    return build_report(customers_df, paid, window, rfm_df=rfm_df, meta=meta), rfm_df


def test_report_shape():
    report, rfm_df = _report('3m')
    for key in ('window', 'label', 'snapshot', 'start', 'orders', 'customers',
                'prev_end', 'kpi', 'cut', 'segments', 'hist', 'actions',
                'totals', 'fx'):
        assert key in report
    assert report['window'] == '3m'
    # 金额下发给页面的一律是人民币，币种口径随报告一起下发
    assert report['fx']['source_currency'] == 'MMK'
    assert report['fx']['mmk_per_cny'] == MMK_PER_CNY
    # 基准日 = 数据内最新订单日（不是 today，也不是写死的月末）
    assert report['snapshot'] == '2025-06-28'
    assert report['customers'] == len(rfm_df) == 6
    assert report['totals']['customers'] == 6
    for dim in ('r', 'f', 'm'):
        assert 'bins' in report['hist'][dim]
        assert len(report['cut'][dim]) == 2
        assert report['cut'][dim][0] >= 0


def test_segments_cover_all_customers():
    report, _ = _report('3m')
    assert len(report['segments']) == 8
    assert [r['key'] for r in report['segments']] == SEGMENT_ORDER
    assert sum(r['count'] for r in report['segments']) == report['customers']
    assert abs(sum(r['share'] for r in report['segments']) - 1.0) < 1e-9
    assert abs(sum(r['rev_share'] for r in report['segments']) - 1.0) < 1e-9
    # 分群顺序固定，表里不会因为某档没人就少一行
    assert {r['key'] for r in report['segments']} == set(SEGMENT_ORDER)


def test_hist_counts_cover_all_customers():
    report, _ = _report('3m')
    for dim in ('r', 'f', 'm'):
        counted = sum(b['count'] for b in report['hist'][dim]['bins'])
        assert counted == report['customers'], (dim, counted)


def test_hist_cut_is_high_band_start():
    """图上「高/低分界」必须落在高档的起始值：R 取高档最大天数，F/M 取高档最小次数/金额。"""
    report, _ = _report('3m')
    cut_r, cut_f, cut_m = report['cut']['r'], report['cut']['f'], report['cut']['m']
    assert report['hist']['r']['cut'] == cut_r[0]
    assert report['hist']['f']['cut'] == cut_f[0]
    assert report['hist']['m']['cut'] == cut_m[0]
    # 两个展示切点按数值升序（33% 在前、67% 在后）
    assert report['hist']['f']['cuts'] == sorted(report['hist']['f']['cuts'])
    assert report['hist']['m']['cuts'] == sorted(report['hist']['m']['cuts'])


def test_m_histogram_bins_follow_real_amounts():
    """M 的桶边界要从当期金额推（等比/对数），不能写死几百到一万的小额边界。"""
    report, _ = _report('3m')
    bins = report['hist']['m']['bins']
    assert len(bins) >= 3
    # 末桶开口（hi is None），且非空客户都落进某个桶
    assert bins[-1]['hi'] is None
    assert all(b['count'] >= 0 for b in bins)
    assert sum(b['count'] for b in bins) == report['customers']


def test_cut_matches_levels():
    """页面虚线位置必须与打分分档严格一致：R 用 max(3 档)，F/M 用 min(3 档)。"""
    report, rfm_df = _report('3m')
    cut_r, cut_f, cut_m = report['cut']['r'], report['cut']['f'], report['cut']['m']

    high_r = rfm_df[rfm_df['r_leval'] == '3']['r']
    high_f = rfm_df[rfm_df['f_leval'] == '3']['f']
    high_m = rfm_df[rfm_df['m_leval'] == '3']['m'] / MMK_PER_CNY   # 页面的 M 已折人民币
    assert cut_r[0] == high_r.max()
    assert cut_f[0] == high_f.min()
    assert cut_m[0] == high_m.min()

    # 用切点回判每个客户的高低档，应与分档结果一致
    for _, row in rfm_df.iterrows():
        assert (row['r'] <= cut_r[0]) == (row['r_leval'] == '3')
        assert (row['f'] >= cut_f[0]) == (row['f_leval'] == '3')
        assert (row['m'] / MMK_PER_CNY >= cut_m[0]) == (row['m_leval'] == '3')


def test_kpi_and_core_ratio():
    report, rfm_df = _report('3m')
    kpi = report['kpi']
    assert kpi['customers'] == len(rfm_df)
    assert kpi['core_ratio'] == pytest.approx((rfm_df['rfm_group'] == '333').mean())
    assert kpi['avg_r'] == pytest.approx(rfm_df['r'].mean())
    assert kpi['avg_f'] == pytest.approx(rfm_df['f'].mean())
    # 页面的 M 是人民币：原始 MMK 先按固定汇率折算
    assert kpi['avg_m'] == pytest.approx(rfm_df['m'].mean() / MMK_PER_CNY)
    assert kpi['avg_m_usd'] == pytest.approx(kpi['avg_m'] / CNY_PER_USD)


def test_prev_period_for_window_but_not_for_all():
    report_3m, _ = _report('3m')
    assert report_3m['kpi']['prev'] is not None
    assert report_3m['prev_end'] == '2025-03-28'
    assert any(r['count_delta'] is not None for r in report_3m['segments'])

    report_all, _ = _report('all')
    assert report_all['kpi']['prev'] is None
    assert report_all['prev_end'] is None
    assert all(r['count_delta'] is None for r in report_all['segments'])


def test_actions_follow_segments():
    report, seg_rows = _report('3m')
    by_key = {r['key']: r for r in report['segments']}
    assert [a['seg'] for a in report['actions']] == ['011', '100', '001']
    recall = by_key['011']
    assert str(recall['count']) in report['actions'][0]['title']
    for item in report['actions']:
        assert item['stats'] and item['title'] and item['body']


def test_detail_rows():
    customers_df, orders_df = _customers(), _orders()
    paid = orders_df[orders_df['order_status'] == 2]
    rfm_df, meta = rfm_table(customers_df, paid, '3m')
    rows = detail_rows(rfm_df, customers_df)
    assert len(rows) == len(rfm_df) == 6
    raw_m = dict(zip(rfm_df['customer_id'], rfm_df['m']))
    required = {'customer_id', 'region', 'r', 'f', 'm', 'r_leval', 'f_leval',
                'm_leval', 'rfm_group', 'segment', 'segment_key', 'segment_color'}
    for row in rows:
        assert required <= set(row)
        assert row['region'].startswith('地区')
        assert row['segment_key'] in SEGMENT_ORDER
        # 明细表的 M 也下发人民币，前端不用再换算
        assert row['m'] == pytest.approx(raw_m[row['customer_id']] / MMK_PER_CNY)


def test_empty_orders_do_not_crash():
    """库里有客户但没有任何已支付订单时（比如刚改口径还没重新导入），页面不能 500。"""
    customers_df = _customers()
    empty_orders = pd.DataFrame(columns=[
        'transaction_id', 'order_date', 'customer_id', 'product_id',
        'quantity', 'ppu', 'amount', 'order_status'])
    rfm_df, meta = rfm_table(customers_df, empty_orders, '3m')
    report = build_report(customers_df, empty_orders, '3m',
                          rfm_df=rfm_df, meta=meta)
    assert report['customers'] == 0
    assert report['kpi']['customers'] == 0
    assert sum(r['count'] for r in report['segments']) == 0
    assert detail_rows(rfm_df, customers_df) == []
