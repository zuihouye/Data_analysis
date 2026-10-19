"""RFM 客户分层计算。

纯函数模块：不 import Flask、不 import 数据库 —— 输入两个 DataFrame，
输出一个 DataFrame。所以它能被 pytest 直接测，也能在 notebook 里复用。

分层口径：
  R = 距【基准日】的天数，越小越近，得分越高
  F = 窗口内的交易笔数（频次，不是购买件数）
  M = 窗口内的消费总额，越大得分越高

基准日 = 全量数据里最新的订单日，**不是 today**。
"""
import pandas as pd

# 回看窗口：key -> (下拉框显示名, 回看跨度)。None 表示全部
WINDOWS = {
    '3m':  ('近 3 个月', pd.DateOffset(months=3)),
    '6m':  ('近 6 个月', pd.DateOffset(months=6)),
    '12m': ('近一年',    pd.DateOffset(months=12)),
    'all': ('全部',      None),
}
DEFAULT_WINDOW = 'all'

# F 的业务分箱：1 次 / 2-3 次 / 4 次以上（F_MODE='fixed' 时使用）
F_BINS = [0, 1, 3, float('inf')]

# F 的分箱口径：
#   'rank'  —— 窗口内等频分箱，和 R/M 一致。任何窗口长度都均衡。
#   'fixed' —— 固定业务阈值 1 / 2-3 / 4+。好解释，但在短窗口下会退化。
#
# 为什么默认 'rank' —— 实测同一套固定阈值在不同窗口上的分布：
#   近 3 个月  {1: 296, 2: 98, 3:   4}   ← 74% 挤在最低档，F 维度几乎失去区分度
#   近 6 个月  {1: 334, 2: 227, 3:  38}
#   近一年     {1: 292, 2: 357, 3: 163}  ← 只有长窗口下才均衡
# 根因：近 3 个月人均只有 1.34 笔，「来 4 次以上」本来就极罕见。
# RFM 的标准做法是按【分析总体】做等频分箱，窗口一变尺度跟着变。
F_MODE = 'rank'

LEVELS = 3


def window_options():
    """给模板用的下拉框选项（保持 WINDOWS 的声明顺序）"""
    return [{'key': k, 'label': label} for k, (label, _) in WINDOWS.items()]


def _score_by_rank(s, reverse=False):
    """
    等频分档。先 rank(method='first') 再 qcut。

    不能直接 qcut：r 和 m 的重复值极多（实测 812 个客户里只有 166 个不同的
    r 值），分位点会落在重复值上，同一分数的客户被劈到两档，各档人数不均。
    rank 先把并列值打散成严格递增，分位点就稳定了。

    reverse=True 用于 R —— 它的语义是"值越小越好"（越近越好）。
    这里统一取负，把两个方向都归一到【值越大分越高】，labels 就永远
    是 ['1','2','3']，不会出现"R 的最高分在末位"这种反直觉的分支。
    """
    n = len(s)
    if n == 0:
        return pd.Series([], dtype='object', index=s.index)

    values = -s if reverse else s
    k = min(LEVELS, n)              # 客户数不足 3 个时切不出 3 档
    if k < 2:                       # 只有 1 个客户，没有排名信息，给最高档
        return pd.Series(['3'] * n, dtype='object', index=s.index)
    return pd.qcut(values.rank(method='first'), k, labels=['1', '2', '3'][-k:])


def rfm_table(customers_df, orders_df, window=DEFAULT_WINDOW, f_mode=F_MODE):
    """
    计算 RFM 分层表。

    返回 (rfm_df, meta)，meta 含基准日 / 窗口 / 订单数 / 客户数，供页面显示。

    ⚠️ 顺序：**先切窗口，再算 RFM**。反过来（先在全量上算好分位点、再筛客户）
    得到的是"全量排名的一个子集"，而不是"窗口内的排名"。实测近 3 个月窗口下
    有 **93.5%** 的客户 rfm_group 会被改写，等于分层结论全错。
    """
    orders = orders_df.copy()
    orders['order_date'] = pd.to_datetime(orders['order_date'])
    snapshot = orders['order_date'].max()

    if window not in WINDOWS:          # 脏 key 回落默认，别让 KeyError 冒到页面上
        window = DEFAULT_WINDOW
    label, offset = WINDOWS[window]
    if offset is not None:
        orders = orders[orders['order_date'] > snapshot - offset]

    columns = ['customer_id', 'r', 'f', 'm', 'r_leval', 'f_leval',
               'm_leval', 'rfm_group']
    meta = {
        'window': window,
        'window_label': label,
        'snapshot': snapshot.date().isoformat() if pd.notna(snapshot) else None,
        'start': (snapshot - offset).date().isoformat()
                 if offset is not None and pd.notna(snapshot) else None,
        'orders': int(len(orders)),
        'customers': 0,
    }

    if orders.empty:
        meta['range'] = '没有数据'
        return pd.DataFrame(columns=columns), meta

    orders['r_day'] = (snapshot - orders['order_date']).dt.days
    merged = pd.merge(customers_df, orders, on='customer_id', how='inner')
    rfm = merged.groupby('customer_id', as_index=False).agg(
        r=('r_day', 'min'),                    # 最近一次消费距基准日的天数
        f=('transaction_id', 'count'),         # 窗口内交易笔数 = 频次
        m=('amount', 'sum'),                   # 窗口内消费总额
    )

    rfm['r_leval'] = _score_by_rank(rfm['r'], reverse=True)   # r 越小越近 -> 分越高
    if f_mode == 'fixed':
        rfm['f_leval'] = pd.cut(rfm['f'], bins=F_BINS, labels=['1', '2', '3'])
    else:
        rfm['f_leval'] = _score_by_rank(rfm['f'])
    rfm['m_leval'] = _score_by_rank(rfm['m'])                 # m 越大 -> 分越高

    rfm['rfm_group'] = (rfm['r_leval'].astype(str)
                        + rfm['f_leval'].astype(str)
                        + rfm['m_leval'].astype(str))

    meta['customers'] = int(len(rfm))
    meta['range'] = (f'{meta["start"]} ~ {meta["snapshot"]}'
                     if meta.get('start') else f'截至 {meta["snapshot"]}')
    return rfm[columns], meta
