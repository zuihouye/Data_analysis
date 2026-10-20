import logging

from flask import Blueprint, jsonify
from utils.loader import BusinessDataGuardError
from utils import loader
from utils.quality import DataQualityError
from .auth import admin_required
logger = logging.getLogger(__name__)

ci_blue = Blueprint('ci_blue', __name__)


# 安全层已补齐：
#   POST（不是 GET）+ CSRF（before_app_request 统一校验）+ 管理员权限
# 剩下的"前端二次确认"见路线图 2.1，属于体验优化，不是安全边界。
@ci_blue.route('/clear_input', methods=['POST'])
@admin_required
def clear_input():
    try:
        df, report = loader.process_and_import()
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
        }), 409

    # 日期转成字符串再序列化，否则 Flask 会输出 RFC 822 格式（Wed, 01 Jan 2025 ...）
    preview = df.head(20).copy()
    preview['order_date'] = preview['order_date'].dt.strftime('%Y-%m-%d')

    return jsonify({
        'status': 'success',
        'message': '数据加载成功！',
        'total_rows': len(df),
        'quality_report': report,
        'data_preview': preview.to_dict(orient='records'),
    })