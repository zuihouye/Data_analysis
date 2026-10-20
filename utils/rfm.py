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

# ---------------------------------------------------------------- 客户分群
# 三维各 3 档 → 折成高/低两档（3 = 高，1/2 = 低）→ 2×2×2 = 8 个经典分群。
#
# 为什么要折：27 种组合没法一一配运营动作，运营只关心"值不值得投"；
# 8 分群既能一眼看懂，又能直接对上动作。key 的 3 位依次是 R/F/M，
# 1 = 高，0 = 低（例如 '110' = R 高 / F 高 / M 低）。
#
# color 是前端徽章色：重要且近期的用绿（维护），重要但流失的用橙（挽回），
# 一般的用蓝，最差一档用灰（低优先级）。
SEGMENTS = {
    '111': {'name': '重要价值客户', 'color': 'success',
            'action': '保持：VIP 服务 + 专属权益，重点维护防流失'},
    '110': {'name': '一般价值客户', 'color': 'primary',
            'action': '提客单：满减 / 套装搭配，把高频低客单往上带'},
    '101': {'name': '重要深耕客户', 'color': 'success',
            'action': '提频次：关联品类推荐 + 复购提醒'},
    '100': {'name': '一般发展客户', 'color': 'primary',
            'action': '培育：新客券 + 首购引导，先让他买第二次'},
    '011': {'name': '重要唤回客户', 'color': 'warning',
            'action': '唤回：专属召回券 + 客服回访，这类人丢了最可惜'},
    '010': {'name': '一般保持客户', 'color': 'primary',
            'action': '唤醒 + 提客单：低成本触达，试探性优惠'},
    '001': {'name': '重要挽留客户', 'color': 'warning',
            'action': '最高优先级挽回：大额券 / 一对一沟通'},
    '000': {'name': '一般挽留客户', 'color': 'secondary',
            'action': '低成本维持：短信触达即可，不投广告费'},
}

# 页面展示顺序：按运营优先级（重要 × 近/远），不是按人数
SEGMENT_ORDER = ['111', '011', '101', '001', '110', '010', '100', '000']


def band_of(level):
    """三维分档折成两档：3 → 高，1/2 → 低。"""
    return '高' if str(level) == '3' else '低'


def segment_key(r_leval, f_leval, m_leval):
    """(R, F, M) 三档 → 8 分群的 key（'111' … '000'）。"""
    return ''.join('1' if band_of(v) == '高' else '0'
                   for v in (r_leval, f_leval, m_leval))


def segment_of(r_leval, f_leval, m_leval):
    """(R, F, M) 三档 → 分群中文名。"""
    return SEGMENTS[segment_key(r_leval, f_leval, m_leval)]['name']


def _segment_summary(rfm, total):
    """按 SEGMENT_ORDER 汇总 8 分群（人数 / 占比 / 高低档 / 运营动作），给页面面板用。"""
    counts = rfm['segment'].value_counts().to_dict() if total else {}
    rows = []
    for key in SEGMENT_ORDER:
        info = SEGMENTS[key]
        count = int(counts.get(info['name'], 0))
        rows.append({
            'key': key,
            'name': info['name'],
            'color': info['color'],
            'action': info['action'],
            'r_band': '高' if key[0] == '1' else '低',
            'f_band': '高' if key[1] == '1' else '低',
            'm_band': '高' if key[2] == '1' else '低',
            'count': count,
            'ratio': round(count / total * 100, 1) if total else 0.0,
        })
    return rows


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


def rfm_table(customers_df, orders_df, window=DEFAULT_WINDOW, f_mode=F_MODE,
              as_of=None, start=None):
    """
    计算 RFM 分层表。

    返回 (rfm_df, meta)，meta 含基准日 / 窗口 / 订单数 / 客户数，供页面显示。

    ⚠️ 顺序：**先切窗口，再算 RFM**。反过来（先在全量上算好分位点、再筛客户）
    得到的是"全量排名的一个子集"，而不是"窗口内的排名"。实测近 3 个月窗口下
    有 **93.5%** 的客户 rfm_group 会被改写，等于分层结论全错。

    as_of / start 用于**环比**：传了 as_of 就以它为基准日（而不是数据里最新一天），
    start 给定时按 [start, as_of] 的显式区间取订单。这样上一周期可以在它自身的
    客群里重算三分位，不会借用本期的分位点。
    """
    orders = orders_df.copy()
    orders['order_date'] = pd.to_datetime(orders['order_date'])
    snapshot = (pd.Timestamp(as_of) if as_of is not None
                else orders['order_date'].max())

    if window not in WINDOWS:          # 脏 key 回落默认，别让 KeyError 冒到页面上
        window = DEFAULT_WINDOW
    label, offset = WINDOWS[window]
    if start is not None:
        begin = pd.Timestamp(start)
        orders = orders[(orders['order_date'] >= begin)
                        & (orders['order_date'] <= snapshot)]
    elif offset is not None:
        begin = snapshot - offset
        orders = orders[orders['order_date'] > begin]
    else:
        begin = None

    columns = ['customer_id', 'r', 'f', 'm', 'r_leval', 'f_leval',
               'm_leval', 'segment', 'segment_color', 'rfm_group']
    meta = {
        'window': window,
        'window_label': label,
        'snapshot': snapshot.date().isoformat() if pd.notna(snapshot) else None,
        'start': begin.date().isoformat()
                 if begin is not None and pd.notna(snapshot) else None,
        'orders': int(len(orders)),
        'customers': 0,
    }

    if orders.empty:
        meta['range'] = '没有数据'
        meta['segments'] = _segment_summary(None, 0)
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
    # 三档折两档 → 8 分群名称（页面表格和「分群明细」面板都用它）
    rfm['segment'] = [segment_of(r, f, m)
                      for r, f, m in zip(rfm['r_leval'], rfm['f_leval'],
                                         rfm['m_leval'])]
    # 徽章颜色跟着分群走，模板里就不用再写一遍 if 判断
    rfm['segment_color'] = rfm['segment'].map(
        {info['name']: info['color'] for info in SEGMENTS.values()})

    meta['customers'] = int(len(rfm))
    meta['segments'] = _segment_summary(rfm, len(rfm))
    meta['range'] = (f'{meta["start"]} ~ {meta["snapshot"]}'
                     if meta.get('start') else f'截至 {meta["snapshot"]}')
    return rfm[columns], meta
