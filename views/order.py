from flask import render_template, Blueprint
import pandas as pd
from utils import db
from .auth import login_required
order_blue = Blueprint('order', __name__)


@order_blue.route('/order/list')
@login_required
def order_list():
    sql = '''
        SELECT o.transaction_id, o.order_date, o.customer_id,
               p.product_name, o.quantity, o.ppu, o.amount
        FROM orders o
        LEFT JOIN products p ON o.product_id = p.product_id
        ORDER BY o.order_date DESC
    '''
    df = pd.read_sql(sql, con=db.engine)
    df['order_date'] = df['order_date'].astype(str)
    datalist = df.to_dict(orient='records')
    return render_template('order_list_t.html', datalist=datalist)


@order_blue.route('/product/list')
@login_required
def product_list():
    df = pd.read_sql('SELECT product_id, product_name, product_category, ppu FROM products ORDER BY product_id', con=db.engine)
    datalist = df.to_dict(orient='records')
    return render_template('product_list_t.html', datalist=datalist)


@order_blue.route('/product/add', methods=['GET', 'POST'])
def product_add():
    return None


@order_blue.route('/product/delete', methods=['GET', 'POST'])
def product_delete():
    return None


@order_blue.route('/product/update', methods=['GET', 'POST'])
def product_update():
    return None


@order_blue.route('/order/update', methods=['GET', 'POST'])
def order_update():
    return None


@order_blue.route('/order/delete', methods=['GET', 'POST'])
def order_delete():
    return None
