"""
DocString.
"""
import os

from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, AudioMessage, ImageMessage
)

from src.models import OpenAIModel
from src.memory import Memory
from src.logger import logger
from src.utils import get_role_and_content

load_dotenv('.env')
app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
default_api_key = os.getenv('DEFAULT_API_KEY')

memory = Memory(
    system_message=os.getenv('SYSTEM_MESSAGE'),
    memory_message_count=2)
model_management = {}
api_keys = {}


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
    return 'Hello World'


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
