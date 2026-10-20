from flask import redirect, session, url_for

from views import create_app


app = create_app()


@app.route('/')
def hello_world():
    """根路径分流：客户进商品页，员工进客户管理页（未登录会被 @login_required 送到登录页）。"""
    if session.get('role') == 0:
        return redirect(url_for('order.product_list'))
    return redirect(url_for('user.customer_list'))


if __name__ == '__main__':
    app.run()
