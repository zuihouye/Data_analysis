"""RFM 报告聚合层。

页面要的不是"客户级明细"，而是 KPI / 分群汇总 / 分位数切点 / 分布直方图 /
行动建议这些**已经算好、能直接渲染**的数字。把这段聚合逻辑放在这里，好处是：

1. 保持 utils/rfm.py 纯粹只做分层，不掺排版用的东西；
2. 颜色、文案、金额换算只在一处，模板和 JS 都不用各自算一遍；
3. 纯函数、不 import Flask / 数据库，可以直接用 DataFrame 测。

口径与 utils/rfm.py 完全一致：先切周期、再按全体客户做三分位，
上一周期（环比）也在它自身客群里重算，避免跨期口径漂移。
"""
import pandas as pd

from utils.rfm import (DEFAULT_WINDOW, SEGMENTS, SEGMENT_ORDER, WINDOWS,
                       rfm_table)

# 8 分群在图表/表格里的短名（表格第一列太长会换行）
SEG_SHORT = {
    '111': '重要价值', '011': '重要唤回', '101': '重要深耕', '001': '重要挽留',
    '110': '一般价值', '010': '一般保持', '100': '一般发展', '000': '一般挽留',
}

# 分群色板：与设计稿 CSS 里的 --seg-* 同值（SVG 内直接用字面量）
SEG_COLORS = {
    '111': 'oklch(58% 0.16 145)',
    '011': 'oklch(60% 0.15 58)',
    '101': 'oklch(70% 0.13 145)',
    '001': 'oklch(70% 0.14 58)',
    '110': 'oklch(60% 0.08 245)',
    '010': 'oklch(66% 0.07 245)',
    '100': 'oklch(72% 0.055 245)',
    '000': 'oklch(77% 0.04 245)',
}

# 8 分群 -> 名字反查（用于把客户明细里的中文分群映射回 key）
NAME_TO_KEY = {info['name']: key for key, info in SEGMENTS.items()}

# F 的展示分桶：整数且分布很偏，用固定业务边界比等分更容易读懂
F_EDGES = [1, 2, 3, 4, 5, 6, 8, 10, 13, 17, 22, 30, 61]
F_LABELS = ['1', '2', '3', '4', '5', '6–7', '8–9', '10–12',
            '13–16', '17–21', '22–29', '30+']

# M 的单位是「元」，分桶边界也按元切；标签按万元展示（与详情表口径一致）
M_EDGES = [0, 50, 100, 200, 400, 800, 1600, 3200, 6400, 12800]

# ------------------------------------------------------------------ 币种换算
# 数据集（Kaggle raw sales）的金额原始币种是缅甸缅币 MMK。库里存原值、不洗数据，
# 页面统一折算成人民币展示：1 CNY ≈ 291.67 MMK，1 USD ≈ 2,100 MMK（≈ 7.2 CNY）。
# 汇率写死成展示口径、不联网取实时价 —— 演示项目要的是可复现，不是炒汇。
# 想换币种口径只改这两个常量，KPI / 表格 / 图表 / 行动建议会一起变。
MMK_PER_CNY = 291.67
MMK_PER_USD = 2100.0
CNY_PER_USD = MMK_PER_USD / MMK_PER_CNY  # ≈ 7.2


def _to_cny(rfm):
    """把 RFM 表的 M 列由 MMK 折算成人民币，返回副本（不改调用方的 DataFrame）。"""
    if rfm is None or rfm.empty:
        return rfm
    out = rfm.copy()
    out['m'] = out['m'] / MMK_PER_CNY
    return out


# ------------------------------------------------------------------ 格式化
def fmt_n(v):
    return f'{round(v):,}'


def fmt_pct(v, digits=1):
    return f'{v * 100:.{digits}f}%'


def fmt_wan(v):
    """人民币元 -> 万元。小数位随量级收敛，避免长串小数。"""
    w = v / 10000
    a = abs(w)
    digits = 0 if a >= 1000 else 1 if a >= 100 else 2
    return f'{round(w, digits):,}'


def fmt_money(v):
    return f'{fmt_wan(v)} 万元'


def fmt_usd(v):
    """人民币金额 -> 美元展示串，只用于 KPI 的等价说明。"""
    return f'${v / CNY_PER_USD:,.0f}'


def fmt_days(v):
    return f'{round(v)} 天'


def fmt_wan_edge(v):
    """分桶边界可能有 0.005 万这种小值，多留一位小数才不会被舍成同一个数。"""
    if abs(v) < 100:
        return f'{round(v / 10000, 3):g}'
    return fmt_wan(v)


# ------------------------------------------------------------------ 内部工具
def _score_cut(rfm, dim):
    """由客户级分档反推两档切点，保证页面虚线位置与打分的分档严格一致。

    R：值越小越好，切点取该档内的最大天数；
    F / M：值越大越好，切点取该档内的最小次数 / 最小金额。
    返回 [高/低分界, 33% 与 67% 之间的分界]。
    """
    if rfm.empty:
        return None
    high = rfm[rfm[f'{dim}_leval'] == '3'][dim]
    mid = rfm[rfm[f'{dim}_leval'] == '2'][dim]
    if dim == 'r':
        hi = high.max() if len(high) else rfm[dim].max()
        lo = mid.max() if len(mid) else hi
    else:
        hi = high.min() if len(high) else rfm[dim].min()
        lo = mid.min() if len(mid) else hi
    return [float(hi), float(lo)]


def _kpi_block(rfm):
    if rfm.empty:
        return {'customers': 0, 'core_ratio': 0.0,
                'avg_r': 0.0, 'avg_f': 0.0, 'avg_m': 0.0, 'avg_m_usd': 0.0}
    n = len(rfm)
    avg_m = float(rfm['m'].mean())
    return {
        'customers': int(n),
        'core_ratio': float((rfm['rfm_group'] == '333').sum() / n),
        'avg_r': float(rfm['r'].mean()),
        'avg_f': float(rfm['f'].mean()),
        'avg_m': avg_m,
        'avg_m_usd': avg_m / CNY_PER_USD,
    }


def _bins_from_edges(edges, counts, labels, open_tail):
    """edges 的最后一个值只是"倒数第二桶的右边界"，不是独立桶的起点。"""
    bins = []
    for i, label in enumerate(labels):
        last = i == len(labels) - 1
        bins.append({
            'lo': float(edges[i]),
            'hi': None if (last and open_tail) else float(edges[i + 1]),
            'label': label,
            'count': int(counts[i]) if i < len(counts) else 0,
        })
    return bins


def _bucket_counts(values, edges, open_tail):
    """左闭右开分桶，桶数恒为 len(edges)-1；open_tail 时最后一桶吃下所有更大值。"""
    n = len(edges) - 1
    counts = [0] * n
    for v in values:
        placed = False
        for i in range(n - 1):
            if v < edges[i + 1]:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[n - 1] += 1
    return counts


def _m_edges(m_vals, n_bins=9):
    """M 跨好几个数量级（数据集里单笔金额从几万到上亿），按等比（对数）分桶。

    固定边界只适合"金额都落在几百到一万"的小数据；真实数据集一进来就会全部
    挤进最后一个桶，直方图退化成一根柱子，所以边界必须从当期分布推。
    """
    lo = float(m_vals.min())
    hi = float(m_vals.max())
    if not (hi > lo > 0):
        return [lo, max(hi, lo + 1)]
    ratio = (hi / lo) ** (1.0 / n_bins)
    edges = [lo * ratio ** i for i in range(n_bins)]
    edges.append(hi)
    # 浮点误差可能让相邻边界重合，去掉退化的桶
    out = [edges[0]]
    for e in edges[1:]:
        if e > out[-1] * 1.0001:
            out.append(e)
    return out


def _money_bin_labels(edges):
    """元 -> 万元的桶标签；首桶「≤」、末桶「≥」，中间「lo–hi」。"""
    labels = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if i == 0:
            labels.append('≤' + fmt_wan_edge(hi))
        else:
            labels.append(fmt_wan_edge(lo) + '–' + fmt_wan_edge(hi))
    labels[-1] = '≥' + fmt_wan_edge(edges[-2])
    return labels


def _histograms(rfm, span_days):
    """R / F / M 三张分布图的桶。R 用等步长（步长随周期长度取整到 7 天）。"""
    if rfm.empty:
        empty = {'unit': '', 'scale': 'linear', 'bins': [],
                 'cut': 0.0, 'cut_label': '', 'cuts': [], 'cuts_labels': []}
        return {'r': empty, 'f': empty, 'm': empty}

    step = max(7, int(round(span_days / 98.0)) * 7)
    r_edges = []
    i = 0
    while i * step < max(span_days, step):
        r_edges.append(i * step)
        i += 1
    r_edges.append(i * step)
    r_counts = _bucket_counts(rfm['r'].tolist(), r_edges, open_tail=False)
    r_bins = _bins_from_edges(r_edges, r_counts,
                              [str(int(e)) for e in r_edges[:-1]], False)

    f_counts = _bucket_counts(rfm['f'].tolist(), F_EDGES, open_tail=True)
    f_bins = _bins_from_edges(F_EDGES, f_counts, F_LABELS, True)

    m_edges = _m_edges(rfm['m'])
    m_counts = _bucket_counts(rfm['m'].tolist(), m_edges, open_tail=True)
    m_bins = _bins_from_edges(m_edges, m_counts, _money_bin_labels(m_edges), True)

    cut_r = _score_cut(rfm, 'r')
    cut_f = _score_cut(rfm, 'f')
    cut_m = _score_cut(rfm, 'm')

    def labels_for(cuts, dim):
        if dim == 'r':
            return [f'{round(c)} 天' for c in cuts]
        if dim == 'f':
            return [f'{c:.0f} 次' for c in cuts]
        return [fmt_money(c) for c in cuts]

    # 展示用的两个切点按数值升序（33% 在前、67% 在后）；
    # 「高/低分界」在图上取的则是高档的起始值：
    #   R 越小越好 -> 高档的最大天数（升序第一个）
    #   F / M 越大越好 -> 高档的最小次数 / 金额（升序最后一个）
    cuts_r, cuts_f, cuts_m = sorted(cut_r), sorted(cut_f), sorted(cut_m)

    return {
        'r': {'unit': '天', 'scale': 'linear', 'bins': r_bins,
              'cut': cuts_r[0], 'cut_label': f'{round(cuts_r[0])} 天',
              'cuts': cuts_r, 'cuts_labels': labels_for(cuts_r, 'r')},
        'f': {'unit': '次', 'scale': 'linear', 'bins': f_bins,
              'cut': cuts_f[-1], 'cut_label': f'{cuts_f[-1]:.0f} 次',
              'cuts': cuts_f, 'cuts_labels': labels_for(cuts_f, 'f')},
        'm': {'unit': '万元', 'scale': 'log', 'bins': m_bins,
              'cut': cuts_m[-1], 'cut_label': fmt_money(cuts_m[-1]),
              'cuts': cuts_m, 'cuts_labels': labels_for(cuts_m, 'm')},
    }


def _segment_rows(rfm, prev_rfm):
    """8 分群聚合（固定顺序，没有客户的档也下发，页面表格是固定 8 行）。"""
    if rfm.empty:
        base = {key: {'count': 0, 'sr': 0.0, 'sf': 0.0, 'sm': 0.0}
                for key in SEGMENT_ORDER}
        total = 0
        total_m = 0.0
    else:
        work = rfm.copy()
        work['seg_key'] = work['segment'].map(NAME_TO_KEY)
        agg = work.groupby('seg_key').agg(
            count=('customer_id', 'count'),
            sr=('r', 'sum'), sf=('f', 'sum'), sm=('m', 'sum'))
        base = {key: {'count': int(agg['count'].get(key, 0)),
                      'sr': float(agg['sr'].get(key, 0.0)),
                      'sf': float(agg['sf'].get(key, 0.0)),
                      'sm': float(agg['sm'].get(key, 0.0))}
                for key in SEGMENT_ORDER}
        total = len(rfm)
        total_m = float(rfm['m'].sum())

    prev_counts = {}
    if prev_rfm is not None and not prev_rfm.empty:
        pc = prev_rfm['segment'].map(NAME_TO_KEY).value_counts().to_dict()
        prev_counts = {k: int(v) for k, v in pc.items()}

    rows = []
    for key in SEGMENT_ORDER:
        info = SEGMENTS[key]
        a = base[key]
        count = a['count']
        prev_count = prev_counts.get(key)
        rows.append({
            'key': key,
            'name': info['name'],
            'short': SEG_SHORT[key],
            'color': SEG_COLORS[key],
            'action': info['action'],
            'count': count,
            'share': count / total if total else 0.0,
            'avg_r': a['sr'] / count if count else 0.0,
            'avg_f': a['sf'] / count if count else 0.0,
            'avg_m': a['sm'] / count if count else 0.0,
            'revenue': a['sm'],
            'rev_share': a['sm'] / total_m if total_m else 0.0,
            'r_band': '高' if key[0] == '1' else '低',
            'f_band': '高' if key[1] == '1' else '低',
            'm_band': '高' if key[2] == '1' else '低',
            'count_delta': ((count - prev_count) / prev_count)
            if prev_count else None,
        })
    return rows


def _actions(seg_map, has_prev):
    """P1/P2/P3 三段行动建议：规则固定、数字全部来自本期数据。"""
    def g(key):
        return seg_map.get(key) or {'count': 0, 'share': 0.0, 'avg_f': 0.0,
                                    'avg_m': 0.0, 'avg_r': 0.0,
                                    'rev_share': 0.0, 'name': ''}

    recall, dev, retain, value = g('011'), g('100'), g('001'), g('111')
    items = [
        {'p': 'P1', 'tag': '高价值唤回', 'seg': '011',
         'title': f'唤醒 {fmt_n(recall["count"])} 位「重要唤回客户」',
         'body': (f'这批客户曾经买得又多又贵，但已平均沉默 {fmt_days(recall["avg_r"])}，'
                  f'人均历史消费 {fmt_money(recall["avg_m"])}，与仍在活跃的'
                  f'「重要价值客户」（{fmt_money(value["avg_m"])}）几乎持平。'
                  '建议 7 天内用高价值品类券 + 短信与私域双通道触达，'
                  '并按最近一次购买的品类做个性化推荐。'),
         'stats': [['覆盖客户', f'{fmt_n(recall["count"])} 人'],
                   ['收入贡献', fmt_pct(recall['rev_share'])],
                   ['人均 M', fmt_money(recall['avg_m'])]]},
        {'p': 'P2', 'tag': '频次培养', 'seg': '100',
         'title': f'推动 {fmt_n(dev["count"])} 位「一般发展客户」完成第二单',
         'body': (f'该群规模占全量 {fmt_pct(dev["share"])}，人均仅 '
                  f'{dev["avg_f"]:.1f} 次、人均消费 {fmt_money(dev["avg_m"])}，'
                  '是拉高频次成本最低的池子。建议用第二单立减、任务式积分与'
                  '限时复购券，在 30 天内推动第二次购买。'),
         'stats': [['覆盖客户', f'{fmt_n(dev["count"])} 人'],
                   ['客户占比', fmt_pct(dev['share'])],
                   ['人均 F', f'{dev["avg_f"]:.1f} 次']]},
        {'p': 'P3', 'tag': '流失前挽留', 'seg': '001',
         'title': f'对 {fmt_n(retain["count"])} 位「重要挽留客户」做一对一挽回',
         'body': (f'人均消费 {fmt_money(retain["avg_m"])} 但人均频次仅 '
                  f'{retain["avg_f"]:.1f} 次、平均沉默 '
                  f'{fmt_days(retain["avg_r"])}，属于高价值低频的流失风险人群。'
                  '建议客服一对一回访定位原因（价格 / 履约 / 品类），'
                  '再发大额专属券，避免无差别补贴。'),
         'stats': [['覆盖客户', f'{fmt_n(retain["count"])} 人'],
                   ['收入贡献', fmt_pct(retain['rev_share'])],
                   ['人均 M', fmt_money(retain['avg_m'])]]},
    ]
    # 上一周期不存在时（全部窗口），建议卡照常给，只是不宣称"环比"
    if not has_prev:
        for item in items:
            item['since'] = '本周期'
    return items


def _prev_period(customers_df, orders_df, window, snapshot):
    """紧邻的等长上一周期：窗长与本期一致、在它自身的客群里重算三分位。

    返回 (prev_rfm_df 或 None, 上一周期的结束日)。全部窗口没有"上一周期"。
    """
    offset = WINDOWS[window][1]
    if offset is None or snapshot is None or pd.isna(snapshot):
        return None, None
    snap = pd.Timestamp(snapshot)
    prev_end = snap - offset
    prev_start = prev_end - offset + pd.Timedelta(days=1)
    prev_rfm, _ = rfm_table(customers_df, orders_df, window='all',
                            as_of=prev_end, start=prev_start)
    if prev_rfm.empty:
        return None, prev_end.date().isoformat()
    return prev_rfm, prev_end.date().isoformat()


# ------------------------------------------------------------------ 对外入口
def build_report(customers_df, orders_df, window=DEFAULT_WINDOW,
                 rfm_df=None, meta=None):
    """把客户级 RFM 结果聚合成一整个页面的渲染数据。

    :param customers_df: 客户表（只用到 customer_id，供 merge）
    :param orders_df:    订单表（**调用方已过滤成已支付**）
    :param window:       '3m' / '6m' / '12m' / 'all'
    :param rfm_df/meta:  已算好的 rfm_table 结果，避免重复计算
    """
    if rfm_df is None or meta is None:
        rfm_df, meta = rfm_table(customers_df, orders_df, window)

    if window not in WINDOWS:
        window = DEFAULT_WINDOW
    label = WINDOWS[window][0]
    snapshot = meta.get('snapshot')

    prev_rfm, prev_end = _prev_period(customers_df, orders_df, window, snapshot)

    # 金额在聚合前统一折算成人民币：KPI / 分群 / 直方图 / 行动建议读的是同一套数字，
    # 页面各处的「万元」才不会有的按 MMK 有的按 CNY。
    cny_rfm = _to_cny(rfm_df)
    cny_prev = _to_cny(prev_rfm) if prev_rfm is not None else None
    prev_kpi = _kpi_block(cny_prev) if cny_prev is not None else None
    kpi = _kpi_block(cny_rfm)
    kpi['prev'] = prev_kpi

    # 直方图横轴跨度：显式窗口用客户最大 R，全部窗口也够用
    span_days = 1
    if not cny_rfm.empty:
        span_days = max(1, int(cny_rfm['r'].max()) + 1)

    segments = _segment_rows(cny_rfm, cny_prev)
    seg_map = {row['key']: row for row in segments}

    # 矩阵两侧的虚线位置：R 的 33 分位、F 的 67 分位（与打分同源）
    cut_r = _score_cut(cny_rfm, 'r') or [0.0, 0.0]
    cut_f = _score_cut(cny_rfm, 'f') or [0.0, 0.0]
    cut_m = _score_cut(cny_rfm, 'm') or [0.0, 0.0]

    return {
        'window': window,
        'label': label,
        'snapshot': snapshot,
        'start': meta.get('start'),
        'range': meta.get('range'),
        'orders': int(meta.get('orders', 0)),
        'customers': int(meta.get('customers', 0)),
        'prev_end': prev_end,
        'kpi': kpi,
        'cut': {'r': cut_r, 'f': cut_f, 'm': cut_m},
        'segments': segments,
        'hist': _histograms(cny_rfm, span_days),
        'actions': _actions(seg_map, prev_rfm is not None),
        'totals': {'customers': int(len(cny_rfm)),
                   'revenue': float(cny_rfm['m'].sum()) if not cny_rfm.empty else 0.0},
        # 币种口径下发给模板/前端，用于说明行与 KPI 的美元等价金额
        'fx': {'source_currency': 'MMK',
               'mmk_per_cny': MMK_PER_CNY,
               'mmk_per_usd': MMK_PER_USD,
               'cny_per_usd': round(CNY_PER_USD, 2)},
    }


def detail_rows(rfm_df, customers_df):
    """客户级明细（页面最下方那张分页表），带上地区与分群 key。"""
    if rfm_df is None or rfm_df.empty:
        return []
    region = dict(zip(customers_df['customer_id'], customers_df['region']))
    rows = []
    for row in rfm_df.to_dict(orient='records'):
        key = NAME_TO_KEY.get(row['segment'])
        rows.append({
            'customer_id': row['customer_id'],
            'region': region.get(row['customer_id']) or '—',
            'r': int(row['r']),
            'f': int(row['f']),
            'm': float(row['m']) / MMK_PER_CNY,   # 明细表的 M 也是人民币
            'r_leval': str(row['r_leval']),
            'f_leval': str(row['f_leval']),
            'm_leval': str(row['m_leval']),
            'rfm_group': str(row['rfm_group']),
            'segment': row['segment'],
            'segment_key': key,
            'segment_color': SEG_COLORS.get(key, ''),
        })
    return rows
