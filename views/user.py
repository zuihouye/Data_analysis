from flask import Blueprint, render_template
from utils import db
import pandas as pd

user_blue = Blueprint('user', __name__)


@user_blue.route('/customer/list')
def customer_list():
    df = pd.read_sql('SELECT customer_id, region FROM customers ORDER BY customer_id', con=db.engine)
    datalist = df.to_dict(orient='records')
    return render_template('customer_list_t.html', datalist=datalist)


@user_blue.route('/customer/update')
def customer_update():
    pass


@user_blue.route('/customer/delete')
def customer_delete():
    pass
