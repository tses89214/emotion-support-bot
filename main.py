"""
DocString.
"""
import os
import time

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask import \
    Flask, request, abort, render_template, flash, url_for, \
    send_from_directory, Markup, send_file, redirect, session
import boto3

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, AudioMessage, ImageMessage
)

from src.models import OpenAIModel, DynamoDBLogHandler
from src.memory import Memory
from src.logger import logger
from src.utils import get_role_and_content

load_dotenv('.env')
app = Flask(__name__)
app.secret_key = os.urandom(24)
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
default_api_key = os.getenv('DEFAULT_API_KEY')

memory = Memory(
    system_message=os.getenv('SYSTEM_MESSAGE'),
    memory_message_count=2)
model_management = {}
api_keys = {}

dynamodb = boto3.resource(
    'dynamodb',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name='us-west-1'
)
db_logger = DynamoDBLogHandler(dynamodb)


@app.route("/callback", methods=['POST'])
def callback():
    """
    The entrypoint of Line messages.
    """
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    logger.info("Request body: %s", body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)
    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    """
    Currently we only received text message,
    so please put all logic in this part.
    """
    user_id = event.source.user_id
    if not model_management.get(user_id):
        model_management[user_id] = OpenAIModel(api_key=default_api_key)

    text = event.message.text.strip()
    logger.info('%s: %s', user_id, text)

    try:
        if text.startswith('/系統訊息'):
            memory.change_system_message(user_id, text[5:].strip())
            msg = TextSendMessage(text='輸入成功')

        else:
            memory.append(user_id, 'user', text)
            is_successful, response, error_message = \
                model_management[user_id].chat_completions(
                    memory.get(user_id), os.getenv('OPENAI_MODEL_ENGINE'))

            # pylint: disable=broad-exception-raised
            if not is_successful:
                raise BaseException(error_message)

            role, response = get_role_and_content(response)
            msg = TextSendMessage(text=response)
            memory.append(user_id, role, response)
            db_logger.write_log(
                timestamp=int(time.time()),
                user_id=user_id,
                prompt=os.getenv('SYSTEM_MESSAGE'),
                input_text=text,
                output_text=response)

    # pylint: disable=broad-exception-caught
    except Exception as error:
        logger.error(str(error))

        if str(error).startswith('Incorrect API key provided'):
            msg = TextSendMessage(text='OpenAI API Token 有誤，請重新註冊。')

        elif str(error).startswith('That model is currently overloaded with other requests.'):
            msg = TextSendMessage(text='已超過負荷，請稍後再試')

        else:
            msg = TextSendMessage(
                text='系統遇到一些錯誤，請截圖提供以下訊息給管理員。\n' + str(error))

    line_bot_api.reply_message(event.reply_token, msg)


@handler.add(MessageEvent, message=AudioMessage)
def handle_audio_message(event):
    """
    No audio message.
    """
    user_id = event.source.user_id
    line_bot_api.reply_message(event.reply_token, '我目前只接受文字訊息，未來敬請期待!')
    logger.info('%s send a audio message.', user_id)


@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    """
    No image message.
    """
    user_id = event.source.user_id
    line_bot_api.reply_message(event.reply_token, '我目前只接受文字訊息，未來敬請期待!')
    logger.info('%s send a image message.', user_id)


@app.route("/", methods=['GET'])
def home():
    """
    The entrypoint of backstage system.
    """
    return redirect('logs')


############################################
###########     後台介面    #################
############################################


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
legal_users = {'panda': {'password': 'panda'}}
######## USER #####################


class User(UserMixin):
    def __init__(self):
        self.id = None


@login_manager.user_loader
def user_loader(user_id):
    if user_id not in legal_users:
        return

    user = User()
    user.id = user_id
    return user


@login_manager.request_loader
def request_loader(request):
    user = request.form.get('user_id')
    if user not in legal_users:
        return
    user = User()
    user.id = user
    user.is_authenticated = request.form['password'] == legal_users[user]['password']
    return user


def verify_user(user_id, pwd):
    return (user_id in legal_users) and (pwd == legal_users[user_id]['password'])

################################################


##############   admin  page   #################
# ------log in page -----------
@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template("login.html")

    if request.method == 'POST':
        user_id = request.form['user_id']
        pwd = request.form['password']
        if verify_user(user_id, pwd):
            user = User()
            user.id = user_id
            login_user(user)
            return redirect(url_for('home'))
        else:
            return redirect(url_for('login'))
    else:
        return redirect(url_for('login'))


@app.route('/logout')
def logout():
    logout_user()
    return render_template('login.html')


@app.route("/home", methods=['GET', 'POST'])
@login_required
def home_page():
    return render_template("index.html")


@app.route("/logs", methods=['GET', 'POST'])
@login_required
def current_logs():
    return render_template("logs.html", tbody=Markup(db_logger.get_log_html_body()))


@app.route('/css/<path:path>')
def send_css(path):
    return send_from_directory('templates//css', path)


@app.route('/js/<path:path>')
def send_js(path):
    return send_from_directory('templates//js', path)


@app.route('/assets/<path:path>')
def send_assets(path):
    return send_from_directory('templates//assets', path)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
