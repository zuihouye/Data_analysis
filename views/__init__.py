from flask import Flask, request
import logging


def create_app():

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    app = Flask(__name__)

    from . import clear_input
    app.register_blueprint(clear_input.ci_blue)

    return app