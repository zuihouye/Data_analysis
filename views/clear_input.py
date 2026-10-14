import logging

from flask import Blueprint, jsonify

from utils import loader
from utils.quality import DataQualityError

logger = logging.getLogger(__name__)

ci_blue = Blueprint('ci_blue', __name__)


# TODO(安全): 这是个破坏性操作（清空三张表重灌），必须改成 methods=['POST']
#  + @login_required + CSRF 校验。目前仍是 GET，配在导航栏下拉里，
#  浏览器预取 / 爬虫 / 手滑都会触发。改 POST 时要同步改 templates/layout_t.html。
@ci_blue.route('/clear_input', methods=['POST'])
def clear_input():
    try:
        df, report = loader.process_and_import()
    except DataQualityError as exc:
        logger.warning('数据质量不达标，已中止导入: %s', exc)
        return jsonify({
            'status': 'error',
            'message': f'数据质量不达标，已中止导入：{exc}',
        }), 400

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