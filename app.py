from flask import redirect, url_for

from views import create_app


app = create_app()


@app.route('/')
def hello_world(): # put application's code here
    # 根路径进客户列表；未登录会被 @login_required 送到登录页
    return redirect(url_for('user.customer_list'))


if __name__ == '__main__':
    app.run()
