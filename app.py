from views import create_app

csv_path = '/static/archive/raw_rfm_sales_transactions_(V2).csv'

app = create_app()


@app.route('/')
def hello_world():  # put application's code here
    return 'Hello World!'


if __name__ == '__main__':
    app.run()
