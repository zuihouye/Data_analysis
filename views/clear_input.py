from sqlalchemy.engine import result

from utils import db
from flask import Blueprint,request,jsonify

ci_blue = Blueprint('ci_blue', __name__)

@ci_blue.route('/clear_input')
def clear_input():
    # 1. 调用数据库导入和清洗函数
    df = db.process_and_import()

    if df.empty:
        return jsonify({'status':'error','message': 'No data'})

    result_data = df.head(20).to_dict(orient='records')
    return jsonify({
        "status": "success",
        "message": "数据加载成功！",
        "total_rows": len(df),
        "data_preview": result_data
    })