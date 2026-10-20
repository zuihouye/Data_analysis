"""
RFM 计算口径的单元测试。

纯 DataFrame，**不依赖 MySQL** —— utils/rfm.py 刻意不 import Flask 也不
import 数据库，就是为了能这样直接测。
"""
import pandas as pd

from utils.rfm import (DEFAULT_WINDOW, SEGMENTS, SEGMENT_ORDER, WINDOWS,
                       band_of, rfm_table, segment_key, segment_of,
                       window_options)


def _orders(rows):
    """rows: [(customer_id, order_date, transaction_id, amount)]"""
    return pd.DataFrame(rows, columns=['customer_id', 'order_date',
                                       'transaction_id', 'amount'])


def _customers(ids):
    return pd.DataFrame({'customer_id': list(ids), 'region': 'Yangon'})


# ---------------------------------------------------------------- 基准日

def test_snapshot_is_data_max_not_today():
    """
    基准日必须是【数据里最新的订单日】，不是 today。

    这是整个滚动窗口功能的前提：演示数据是 2025 年的，如果按今天
    （2026 年）算，「近 3 个月」会是一张空表。
    """
    orders = _orders([('C1', '2020-01-01', 'T1', 100.0),
                      ('C1', '2020-03-31', 'T2', 100.0)])
    rfm, meta = rfm_table(_customers(['C1']), orders, '3m')

    assert meta['snapshot'] == '2020-03-31'
    assert len(rfm) == 1, '按 today 算的话这两条订单会全落在窗口外'
    assert rfm.iloc[0]['f'] == 2


def test_window_excludes_orders_before_start():
    orders = _orders([('C1', '2024-01-01', 'T1', 100.0),   # 远早于窗口
                      ('C1', '2025-12-01', 'T2', 100.0),
                      ('C1', '2025-12-31', 'T3', 100.0)])
    rfm, meta = rfm_table(_customers(['C1']), orders, '3m')

    assert meta['snapshot'] == '2025-12-31'
    assert meta['start'] == '2025-09-30'
    assert meta['orders'] == 2
    assert rfm.iloc[0]['f'] == 2, '2024 年那条应该被窗口排除'


# ---------------------------------------------------------------- 打分方向

def test_r_level_is_higher_for_more_recent():
    """r 越小（越近）分越高"""
    orders = _orders([('C1', '2025-12-31', 'T1', 100.0),   # r=0
                      ('C2', '2025-12-20', 'T2', 100.0),   # r=11
                      ('C3', '2025-12-10', 'T3', 100.0)])  # r=21
    rfm, _ = rfm_table(_customers(['C1', 'C2', 'C3']), orders, 'all')
    got = rfm.set_index('customer_id')['r_leval'].astype(str).to_dict()

    assert got['C1'] == '3'
    assert got['C3'] == '1'


def test_m_level_is_higher_for_more_spend():
    orders = _orders([('C1', '2025-12-31', 'T1', 10.0),
                      ('C2', '2025-12-31', 'T2', 500.0),
                      ('C3', '2025-12-31', 'T3', 9000.0)])
    rfm, _ = rfm_table(_customers(['C1', 'C2', 'C3']), orders, 'all')
    got = rfm.set_index('customer_id')['m_leval'].astype(str).to_dict()

    assert got['C1'] == '1'
    assert got['C3'] == '3'


def test_rank_scoring_gives_balanced_buckets():
    """
    先 rank(method='first') 再 qcut，各档人数才均等。

    这里所有订单同一天、金额各不相同，r 值全部相同（都为 0）。
    裸 qcut 会因为分位点落在同一个重复值上而切不出三档；加了 rank 才能。
    """
    ids = [f'C{i}' for i in range(1, 10)]
    orders = _orders([(f'C{i}', '2025-12-31', f'T{i}', 100.0 * i)
                      for i in range(1, 10)])
    rfm, _ = rfm_table(_customers(ids), orders, 'all')

    for col in ('r_leval', 'f_leval', 'm_leval'):
        assert rfm[col].value_counts().sort_index().tolist() == [3, 3, 3], col


# ---------------------------------------------------------------- 窗口内重算

def test_scores_are_recomputed_inside_the_window():
    """
    ★ 核心口径：先切窗口、再算分档。反过来会得到"全量排名的一个子集"。

    构造：3 个客户只在近 3 个月下单，3 个只在一年前下单。
    窗口内重算 → 这 3 人分到完整三档（1/1/1）。
    若按全量先算再筛 → 分位点被窗口外的人拉走，这 3 人分到 '3','3','2'。
    """
    orders = _orders([
        ('C1', '2025-12-31', 'T1', 100.0),
        ('C2', '2025-12-25', 'T2', 100.0),
        ('C3', '2025-12-20', 'T3', 100.0),
        ('C4', '2025-01-05', 'T4', 100.0),
        ('C5', '2025-01-04', 'T5', 100.0),
        ('C6', '2025-01-03', 'T6', 100.0),
    ])
    ids = [f'C{i}' for i in range(1, 7)]

    win, meta = rfm_table(_customers(ids), orders, '3m')
    assert meta['customers'] == 3
    assert win['r_leval'].value_counts().sort_index().tolist() == [1, 1, 1]

    # 对照：全量口径下这三个人拿到的分档，和窗口内重算的不一样
    full, _ = rfm_table(_customers(ids), orders, 'all')
    global_labels = (full.set_index('customer_id')['r_leval'].astype(str)
                     .reindex(win['customer_id']).tolist())
    assert global_labels != win['r_leval'].astype(str).tolist()


# ---------------------------------------------------------------- 边界

def test_empty_orders_returns_empty_table():
    rfm, meta = rfm_table(_customers(['C1']), _orders([]), '3m')

    assert len(rfm) == 0
    assert meta['customers'] == 0
    assert list(rfm.columns) == ['customer_id', 'r', 'f', 'm',
                                 'r_leval', 'f_leval', 'm_leval',
                                 'segment', 'segment_color', 'rfm_group']
    # 没数据时 8 个分群仍然要下发（页面面板是固定 8 行，计数全 0）
    assert [s['name'] for s in meta['segments']] == \
        [SEGMENTS[k]['name'] for k in SEGMENT_ORDER]
    assert sum(s['count'] for s in meta['segments']) == 0


def test_tiny_population_does_not_crash():
    """客户数不足 3 个时切不出三档，不能抛异常"""
    one = _orders([('C1', '2025-12-31', 'T1', 100.0),
                   ('C1', '2025-12-30', 'T2', 200.0)])
    rfm, _ = rfm_table(_customers(['C1']), one, 'all')
    assert len(rfm) == 1
    assert rfm.iloc[0]['rfm_group'] == '333'

    two = _orders([('C1', '2025-12-31', 'T1', 100.0),
                   ('C2', '2025-12-30', 'T2', 200.0)])
    rfm2, _ = rfm_table(_customers(['C1', 'C2']), two, 'all')
    assert len(rfm2) == 2


def test_unknown_window_falls_back_to_default():
    orders = _orders([('C1', '2025-12-31', 'T1', 100.0)])
    rfm, meta = rfm_table(_customers(['C1']), orders, 'nonsense')

    assert meta['window'] == DEFAULT_WINDOW
    assert len(rfm) == 1


# ---------------------------------------------------------------- F 口径

def test_fixed_f_mode_uses_business_bins():
    """fixed 口径按 1 / 2-3 / 4+ 切，不受总体人数影响"""
    rows = []
    for i, n in enumerate([1, 2, 4, 10], start=1):
        for j in range(n):
            rows.append((f'C{i}', '2025-12-31', f'T{i}-{j}', 100.0))
    rfm, _ = rfm_table(_customers([f'C{i}' for i in range(1, 5)]),
                       _orders(rows), 'all', f_mode='fixed')
    got = rfm.set_index('customer_id')['f_leval'].astype(str).to_dict()

    assert got == {'C1': '1', 'C2': '2', 'C3': '3', 'C4': '3'}


def test_rank_f_mode_is_balanced_on_skewed_data():
    """默认 rank 口径：即使绝大多数人只买一次，F 也能分出三档"""
    rows = []
    for i in range(1, 13):
        n = 1 if i <= 10 else i - 9          # 10 个人 1 笔，2 个人多笔
        for j in range(n):
            rows.append((f'C{i}', '2025-12-31', f'T{i}-{j}', 100.0))
    rfm, _ = rfm_table(_customers([f'C{i}' for i in range(1, 13)]),
                       _orders(rows), 'all')

    assert rfm['f_leval'].value_counts().sort_index().tolist() == [4, 4, 4]


# ---------------------------------------------------------------- 下拉选项

def test_window_options_are_template_ready():
    opts = window_options()
    assert [o['key'] for o in opts] == list(WINDOWS)
    assert all(set(o) == {'key', 'label'} for o in opts)
    assert {'3m', '6m', '12m', 'all'} == set(WINDOWS)


# ---------------------------------------------------------------- 客户分群

def test_band_folds_three_levels_into_two():
    """折档规则：3 = 高，1/2 = 低 —— 这是 27 组合收敛到 8 分群的唯一依据"""
    assert band_of('3') == '高'
    assert band_of('2') == '低'
    assert band_of('1') == '低'
    # 2 档（客户数不足时的降级分档）也按"只有 3 才是高"处理
    assert band_of(2) == '低'


def test_eight_segments_cover_every_combination():
    """2×2×2 必须正好铺满 8 个分群，且 key/中文名/高低档一一对应"""
    assert len(SEGMENTS) == 8
    assert len(SEGMENT_ORDER) == 8
    assert set(SEGMENT_ORDER) == set(SEGMENTS)

    names = set()
    for key in SEGMENT_ORDER:
        levals = tuple('3' if bit == '1' else '1' for bit in key)
        assert segment_key(*levals) == key
        name = segment_of(*levals)
        assert name == SEGMENTS[key]['name']
        names.add(name)
    assert len(names) == 8, '8 个分群的名字不能重复'

    # 两个极端，顺便把中文名钉死，防止以后改错方向
    assert segment_of('3', '3', '3') == '重要价值客户'
    assert segment_of('1', '1', '1') == '一般挽留客户'
    assert segment_of('1', '3', '3') == '重要唤回客户'


def test_rfm_table_carries_segment_and_summary():
    """表格每行带分群，meta 里带 8 行汇总（人数合计 = 客户数，占比 ≤ 100）"""
    rows = []
    for i in range(1, 7):
        # 让 R/F/M 三个维度都拉开差距，避免全挤在同一个分群
        for j in range(i):
            rows.append((f'C{i}', f'2025-12-{28 - i * 4:02d}', f'T{i}-{j}',
                         100.0 * i))
    rfm, meta = rfm_table(_customers([f'C{i}' for i in range(1, 7)]),
                          _orders(rows), 'all')

    assert {'segment', 'segment_color'} <= set(rfm.columns)
    # 行级不变量：segment 必须等于按该行三个分档现算出来的结果
    for row in rfm.itertuples():
        assert row.segment == segment_of(row.r_leval, row.f_leval, row.m_leval)
        assert row.segment in {info['name'] for info in SEGMENTS.values()}
        assert row.segment_color == SEGMENTS[
            segment_key(row.r_leval, row.f_leval, row.m_leval)]['color']

    summary = meta['segments']
    assert [s['key'] for s in summary] == SEGMENT_ORDER
    assert sum(s['count'] for s in summary) == meta['customers']
    # 每行占比各自保留 1 位小数，8 行相加会有 ±0.5 的凑整误差，不能写死 == 100
    assert abs(sum(s['ratio'] for s in summary) - 100.0) <= 0.5
    assert all({'name', 'color', 'action', 'count', 'ratio',
                'r_band', 'f_band', 'm_band'} <= set(s) for s in summary)
