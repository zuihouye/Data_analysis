"""分页纯函数 / 查询助手的单元测试（不依赖 MySQL）。

utils.pagination 的约定：
  - size 只认 10/20/50/100，非法回落 20；
  - page 从 1 起，非法回落 1，越界夹到最后一页；
  - total=0 时 pages=1、start=end=0。

这些规则一旦被改错，三个列表页会同时出现「翻页没数据」「页码 0」之类的问题，
所以单独用纯函数锁住，跑得快、也不受测试库状态影响。
"""
from sqlalchemy import create_engine, text

from utils.pagination import (DEFAULT_SIZE, SIZES, normalize, page_window,
                              paginate, parse_page, parse_size)


# ---------------------------------------------------------------- parse_*

def test_parse_page_rejects_garbage():
    for bad in (None, '', 'abc', '1.5', 0, -1, -99):
        assert parse_page(bad) == 1
    assert parse_page('1') == 1
    assert parse_page('42') == 42
    assert parse_page(7) == 7


def test_parse_size_only_allows_whitelist():
    assert SIZES == (10, 20, 50, 100)
    assert DEFAULT_SIZE == 20
    for good in SIZES:
        assert parse_size(good) == good
        assert parse_size(str(good)) == good
    for bad in (None, '', 'abc', 0, 1, 5, 15, 21, 999, -20):
        assert parse_size(bad) == DEFAULT_SIZE


# ---------------------------------------------------------------- normalize

def test_normalize_first_page():
    p = normalize(1, 20, 1999)
    assert p['page'] == 1
    assert p['size'] == 20
    assert p['pages'] == 100
    assert p['total'] == 1999
    assert p['offset'] == 0
    assert (p['start'], p['end']) == (1, 20)
    assert p['has_prev'] is False
    assert p['has_next'] is True


def test_normalize_last_page_is_partial():
    p = normalize(100, 20, 1999)
    assert (p['start'], p['end']) == (1981, 1999)
    assert p['has_prev'] is True
    assert p['has_next'] is False


def test_normalize_clamps_out_of_range_page():
    assert normalize(9999, 20, 5)['page'] == 1
    assert normalize(5, 20, 45)['page'] == 3          # 45 条 / 每页 20 -> 3 页
    assert normalize(5, 50, 45)['page'] == 1


def test_normalize_empty_table():
    for page in (1, 3, 99):
        p = normalize(page, 20, 0)
        assert p['page'] == 1
        assert p['pages'] == 1
        assert p['total'] == 0
        assert (p['start'], p['end']) == (0, 0)
        assert p['has_prev'] is False and p['has_next'] is False


def test_normalize_falls_back_on_bad_size():
    assert normalize(1, 999, 100)['size'] == DEFAULT_SIZE
    assert normalize(1, 'abc', 100)['size'] == DEFAULT_SIZE


# ---------------------------------------------------------------- page_window

def test_page_window_single_page_is_empty():
    assert page_window(1, 1) == []
    assert page_window(1, 0) == []


def test_page_window_uses_gap_markers():
    assert page_window(1, 100) == [1, 2, 'gap', 100]
    assert page_window(2, 100) == [1, 2, 3, 'gap', 100]
    assert page_window(50, 100) == [1, 'gap', 49, 50, 51, 'gap', 100]
    assert page_window(100, 100) == [1, 'gap', 99, 100]


def test_page_window_middle_and_edges_never_overflow():
    for page, pages in ((1, 3), (2, 3), (3, 3), (1, 2), (2, 2)):
        seq = page_window(page, pages)
        assert all(1 <= n <= pages for n in seq if n != 'gap')
        assert seq.count('gap') <= 2


# ---------------------------------------------------------------- paginate

def _sqlite(n=25):
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)'))
        for _ in range(n):
            conn.execute(text('INSERT INTO t (name) VALUES (:n)'), {'n': 'x'})
    return engine


def test_paginate_counts_and_slices_in_one_connection():
    engine = _sqlite()
    with engine.connect() as conn:
        rows, p = paginate(conn,
                           'SELECT COUNT(*) FROM t',
                           'SELECT id, name FROM t ORDER BY id LIMIT :limit OFFSET :offset',
                           {}, 2, 10)
    assert [r['id'] for r in rows] == list(range(11, 21))
    assert p['total'] == 25
    assert p['pages'] == 3
    assert (p['start'], p['end']) == (11, 20)
    assert p['has_prev'] is True and p['has_next'] is True


def test_paginate_clamps_page_beyond_last():
    engine = _sqlite()
    with engine.connect() as conn:
        rows, p = paginate(conn,
                           'SELECT COUNT(*) FROM t',
                           'SELECT id FROM t ORDER BY id LIMIT :limit OFFSET :offset',
                           {}, 99, 10)
    assert [r['id'] for r in rows] == [21, 22, 23, 24, 25]
    assert p['page'] == 3
    assert (p['start'], p['end']) == (21, 25)


def test_paginate_keeps_filter_params():
    engine = _sqlite()
    with engine.connect() as conn:
        rows, p = paginate(conn,
                           'SELECT COUNT(*) FROM t WHERE id > :after',
                           'SELECT id FROM t WHERE id > :after ORDER BY id '
                           'LIMIT :limit OFFSET :offset',
                           {'after': 20}, 1, 10)
    assert [r['id'] for r in rows] == [21, 22, 23, 24, 25]
    assert p['total'] == 5
    assert p['pages'] == 1


# ---------------------------------------------------------------- 筛选参数

def test_parse_q_strips_and_truncates():
    from utils.pagination import FILTER_Q_MAX, parse_q
    assert parse_q(None) == ''
    assert parse_q('   ') == ''
    assert parse_q('  abc  ') == 'abc'
    assert parse_q('x' * (FILTER_Q_MAX + 10)) == 'x' * FILTER_Q_MAX


def test_like_pattern_escapes_wildcards():
    from utils.pagination import like_pattern
    # % _ 反斜杠都是 LIKE 的通配符/转义符，必须转义（否则搜 % 会匹配全表）
    assert like_pattern('abc') == '%abc%'
    assert like_pattern('50%') == '%50\\%%'
    assert like_pattern('a_b') == '%a\\_b%'
    assert like_pattern('a\\b') == '%a\\\\b%'
    assert like_pattern('%_\\') == '%\\%\\_\\\\%'


def test_parse_choice_whitelist():
    from utils.pagination import parse_choice
    allowed = ('1', '2')
    assert parse_choice('1', allowed) == '1'
    assert parse_choice('2', allowed) == '2'
    assert parse_choice('', allowed) == ''
    assert parse_choice(None, allowed) == ''
    # 非法值（也算注入尝试）不能进 SQL，回落默认值
    assert parse_choice('1 OR 1=1', allowed) == ''
    assert parse_choice('9', allowed, '2') == '2'
