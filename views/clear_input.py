import logging

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import text

from utils import db, loader
from utils.loader import DEFAULT_CSV, BusinessDataGuardError
from utils.quality import DataQualityError
from .auth import admin_required
logger = logging.getLogger(__name__)

ci_blue = Blueprint('ci_blue', __name__)


def _db_counts():
    """导入页展示用：当前库里三张表的行数。

    库连不上时返回 None —— 导入页本身要能打开，不能因为读计数失败就 500。
    """
    try:
        with db.engine.connect() as conn:
            return {
                'customers': int(conn.execute(text('SELECT COUNT(*) FROM customers')).scalar() or 0),
                'products': int(conn.execute(text('SELECT COUNT(*) FROM products')).scalar() or 0),
                'orders': int(conn.execute(text('SELECT COUNT(*) FROM orders')).scalar() or 0),
            }
    except Exception as exc:  # noqa: BLE001 - 展示页容错
        logger.warning('读取库内行数失败: %s', exc)
        return None


def _guard_counts():
    """导入页展示用：库里"会被导入误删"的业务数据计数。

    读不到时返回 None —— 页面要能打开，不能因为查询失败就 500。
    """
    try:
        return loader.business_data_counts()
    except Exception as exc:  # noqa: BLE001 - 展示页容错
        logger.warning('读取业务数据计数失败: %s', exc)
        return None


@ci_blue.route('/import')
@admin_required
def import_page():
    """导入历史流水的页面（真正的写入仍是下面的 POST /clear_input）。"""
    return render_template('import_t.html',
                           csv_path=str(DEFAULT_CSV),
                           csv_exists=DEFAULT_CSV.exists(),
                           db_counts=_db_counts(),
                           guard_counts=_guard_counts())


# 安全层已补齐：
#   POST（不是 GET）+ CSRF（before_app_request 统一校验）+ 管理员权限
# 剩下的"前端二次确认"见路线图 2.1，属于体验优化，不是安全边界。
@ci_blue.route('/clear_input', methods=['POST'])
@admin_required
def clear_input():
    # 「清空并重建」是显式授权：前端要输入确认文字后才会带上这个字段
    payload = request.get_json(silent=True) or {}
    force_clear = bool(payload.get('force_clear')) or request.form.get('force_clear') == '1'
    try:
        df, report = loader.process_and_import(force_clear=force_clear)
    except DataQualityError as exc:
        logger.warning('数据质量不达标，已中止导入: %s', exc)
        return jsonify({
            'status': 'error',
            'message': f'数据质量不达标，已中止导入：{exc}',
        }), 400
    except BusinessDataGuardError as exc:
        logger.warning('检测到业务数据，已拒绝全量导入: %s', exc)
        return jsonify({
            'status': 'error',
            'message': str(exc),
            'guard_counts': _guard_counts(),
        }), 409

    # 日期转成字符串再序列化，否则 Flask 会输出 RFC 822 格式（Wed, 01 Jan 2025 ...）
    preview = df.head(20).copy()
    preview['order_date'] = preview['order_date'].dt.strftime('%Y-%m-%d')

    return jsonify({
        'status': 'success',
        'message': '数据加载成功！',
        'total_rows': len(df),
        # 导入后页面用这组数字刷新「当前库内容」，不用再单独请求
        'counts': {
            'customers': int(df['customer_id'].nunique()),
            'products': int(df['product_id'].nunique()),
            'orders': int(len(df)),
        },
        'quality_report': report,
        'data_preview': preview.to_dict(orient='records'),
    })
