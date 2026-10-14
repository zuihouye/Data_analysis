import logging

from flask import Flask


def create_app():

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    app = Flask(__name__, template_folder='../templates', static_folder='../static')

    from . import clear_input,ge_RFM,order,user
    app.register_blueprint(clear_input.ci_blue)
    app.register_blueprint(ge_RFM.ge_RFM_blue)
    app.register_blueprint(order.order_blue)
    app.register_blueprint(user.user_blue)

    return app

