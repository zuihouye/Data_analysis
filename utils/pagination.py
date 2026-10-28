"""列表页分页工具。

三个列表页（订单 / 客户 / 商品）的分页规则完全一致：URL 上带 page + size，
SQL 里一条 COUNT(*) 算总数、一条 LIMIT/OFFSET 取当前页，模板用 pager 宏渲染。
逻辑抽在这里，视图里只剩「拼 SQL + 传参」。

约定：
  - size 只允许 10 / 20 / 50 / 100，非法值回落 20；
  - page 从 1 起，非法回落 1，超过总页数时夹到最后一页；
  - total = 0 时 pages = 1、start = end = 0（页脚显示「暂无数据」）。

为什么 COUNT 和取页要传同一个连接：MySQL 默认隔离级别是可重复读，同一连接里的
两条语句看到同一个快照，不会出现「总数 100 条、翻到第 5 页却是空页」这种中间态。
只读查询用 engine.connect()，不要用 begin()（多开一个事务没有意义）。
"""
from math import ceil

from sqlalchemy import text

SIZES = (10, 20, 50, 100)
DEFAULT_SIZE = 20


def parse_page(value):
    """URL 上的 page 参数 -> 合法页码（从 1 起，非法/负数/非数字都回落 1）。"""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def parse_size(value):
    """URL 上的 size 参数 -> 白名单里的每页条数，非法值回落默认 20。"""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SIZE
    return size if size in SIZES else DEFAULT_SIZE


FILTER_Q_MAX = 50


def parse_q(value, max_len=FILTER_Q_MAX):
    """URL 上的关键词参数 -> 去空格并截断的字符串；空串表示不过滤。"""
    return (value or '').strip()[:max_len]


def like_pattern(q):
    """把关键词包成 LIKE 模式，并转义 % _ \\（MySQL 默认的转义符是反斜杠）。

    不转义的话用户搜一个下划线就是通配符，搜 % 会匹配全表。
    """
    escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def parse_choice(value, allowed, default=''):
    """白名单参数：不在白名单里就回落 default（非法值不会拼进 SQL）。"""
    return value if value in allowed else default


def normalize(page, size, total):
    """算出一页的分页元数据。

    返回 dict：page / size / pages / total / offset / start / end /
    has_prev / has_next。start/end 是 1 基的「第 x–y 条」文案用的下上界。
    """
    total = max(0, int(total or 0))
    size = parse_size(size)
    page = parse_page(page)
    pages = max(1, ceil(total / size)) if total else 1
    page = min(page, pages)
    return {
        'page': page,
        'size': size,
        'pages': pages,
        'total': total,
        'offset': (page - 1) * size,
        'start': (page - 1) * size + 1 if total else 0,
        'end': min(page * size, total),
        'has_prev': page > 1,
        'has_next': page < pages,
    }


def page_window(page, pages):
    """页码序列：1 … 4 5 6 … 20。间隙用字符串 'gap' 占位，模板里渲染成省略号。"""
    if pages <= 1:
        return []
    wanted = sorted({1, page - 1, page, page + 1, pages})
    wanted = [n for n in wanted if 1 <= n <= pages]
    out = []
    for i, n in enumerate(wanted):
        if i and n - wanted[i - 1] > 1:
            out.append('gap')
        out.append(n)
    return out


def paginate(conn, count_sql, data_sql, params, page, size):
    """在同一个连接里先 COUNT、再取当前页，返回 (rows, meta)。

    - count_sql / data_sql 用 SQLAlchemy 的 :name 参数；
    - data_sql 末尾必须自己写 LIMIT :limit OFFSET :offset（本函数负责补参数）；
    - rows 是 RowMapping 列表，视图里转 dict 后传给模板。
    """
    total = conn.execute(text(count_sql), params).scalar()
    meta = normalize(page, size, total)
    meta['window'] = page_window(meta['page'], meta['pages'])
    exec_params = dict(params)
    exec_params['limit'] = meta['size']
    exec_params['offset'] = meta['offset']
    rows = conn.execute(text(data_sql), exec_params).mappings().all()
    return rows, meta
