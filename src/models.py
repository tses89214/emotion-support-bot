"""
models.py
"""

import logging
import re
from typing import List, Dict

import requests
from botocore.exceptions import ClientError
import pandas as pd

logger = logging.getLogger(__name__)

# pylint: disable=missing-function-docstring,


class ModelInterface:
    """
    An interface for interacting with a model for token validation and chat completions.
    """

    def check_token_valid(self) -> bool:
        """
        Check the validity of the token.

        Returns:
            bool. True if the token is valid, False otherwise.

        Raises:
            NotImplementedError if not implemented by subclass.
        """
        raise NotImplementedError()

    def chat_completions(self, messages: List[Dict], model_engine: str) -> str:
        """
        Get chat completions from the model based on the input messages.

        Params:
            messages: List of message dictionaries.
            model_engine: str. The model engine to use for chat completions.

        Returns:
            str. The chat completions generated by the model.

        Raises:
            NotImplementedError if not implemented by subclass.
        """
        raise NotImplementedError


class OpenAIModel(ModelInterface):
    """
    A class representing an OpenAI model for token validation and chat completions.
    Inherits from ModelInterface.
    """

    def __init__(self, api_key: str):
        """
        Initialize the OpenAIModel instance.

        Args:
            api_key: str. The API key for accessing the OpenAI API.
        """
        self.api_key = api_key
        self.base_url = 'https://api.openai.com/v1'
        self.headers = {
            'Authorization': f'Bearer {self.api_key}'
        }

    # pylint: disable=missing-timeout
    def _request(self, method: str, endpoint: str, body=None, files=None):
        """
        Send a request to the OpenAI API.

        Args:
            method: str. The HTTP method to use (GET or POST).
            endpoint: str. The API endpoint.
            body: dict. The JSON body of the request.
            files: Optional. Dictionary of files to send.

        Returns:
            Tuple containing success status (bool), response data (dict), and error message (str).
        """
        try:
            if method == 'GET':
                response = requests.get(
                    f'{self.base_url}{endpoint}', headers=self.headers)
            elif method == 'POST':
                if body:
                    self.headers['Content-Type'] = 'application/json'
                response = requests.post(
                    f'{self.base_url}{endpoint}',
                    headers=self.headers, json=body, files=files)
            response = response.json()
            if response.get('error'):
                return False, None, response.get('error', {}).get('message')

        # pylint: disable=broad-exception-caught
        except Exception:
            return False, None, 'OpenAI API 系統不穩定，請稍後再試'
        return True, response, None

    def check_token_valid(self):
        """
        Check the validity of the token with the OpenAI API.

        Returns:
            Tuple containing success status (bool), response data (dict), and error message (str).
        """
        return self._request('GET', '/models')

    def chat_completions(self, messages: List[Dict], model_engine: str) -> str:
        """
        Get chat completions from the OpenAI model based on the input messages.

        Args:
            messages: List of message dictionaries.
            model_engine: str. The model engine to use for chat completions.

        Returns:
            Tuple containing success status (bool), response data (dict), and error message (str).
        """
        json_body = {
            'model': model_engine,
            'messages': messages
        }
        return self._request('POST', '/chat/completions', body=json_body)


class DynamoDBLogHandler:
    """
    A class for reading and writing logs to a DynamoDB table.
    """

    def __init__(self, resource):
        """
        Initialize the DynamoDBLogHandler instance.

        Params:
            resource: A Boto3 DynamoDB resource.
        """
        self.resource = resource
        self.table = self.resource.Table('user_log')

    def write_log(self,
                  timestamp: int, user_id: str, prompt: str, input_text: str, output_text: str):
        """
        Adds a chat log into the table.

        Params:
            timestamp: int. the timestamp event happened.
            user_id: string. user's id.
            prompt: string. the prompt we gave to chatgpt.
            input_text: string. the input text we gave to chagpt.
            output_text: string. the output text chatgpt gave.

        """
        try:
            self.table.put_item(
                Item={
                    'timestamp': timestamp,
                    'user_id': user_id,
                    'prompt': prompt,
                    'input_text': input_text,
                    'output_text': output_text})

        except ClientError as err:
            self._handle_error("write_log", err)

    def query_log(
            self,
            from_timestamp: int = None,
            to_timestamp: int = None,
            user_id: str = None,
            limit: int = 100) -> List:
        """
        Read chat logs from the table.

        Params:
            from_timestamp: int. Start timestamp for query range.
            to_timestamp: int. End timestamp for query range.
            user_id: string. User's id.
            limit: int. Row count returned.

        Returns:
            List of log items.
        """
        # Build the filter expression and expression attribute values based on the input parameters
        filter_expression = []
        expression_attribute_values = {}

        if from_timestamp is not None:
            filter_expression.append("#ts >= :from_ts")
            expression_attribute_values[":from_ts"] = from_timestamp

        if to_timestamp is not None:
            filter_expression.append("#ts <= :to_ts")
            expression_attribute_values[":to_ts"] = to_timestamp

        if user_id is not None:
            filter_expression.append("user_id = :user_id")
            expression_attribute_values[":user_id"] = user_id

        # Construct the query parameters
        query_params = {
            "FilterExpression": " AND ".join(filter_expression),
            "ExpressionAttributeNames": {"#ts": "timestamp"},
            "ExpressionAttributeValues": expression_attribute_values,
            "Limit": limit
        }

        try:
            return self.scan_log(query_params)
        except ClientError as err:
            self._handle_error("query_log", err)

    def scan_log(self, query_params: Dict = None) -> List:
        """
        Scan and retrieve log items from the table.

        Params:
            query_params: Dict. Additional query parameters.

        Return:
            List of log items.
        """
        try:
            query_params = query_params or {}
            response = self.table.scan(**query_params)
            data = response['Items']
            while 'LastEvaluatedKey' in response:
                response = self.table.scan(
                    ExclusiveStartKey=response['LastEvaluatedKey'])
                data.extend(response['Items'])
            return data
        except ClientError as err:
            self._handle_error("scan_log", err)

    def _handle_error(self, method_name: str, err: ClientError):
        """
        Handle and log errors.

        Params:
            method_name: The name of the method where the error occurred.
            err: The ClientError instance containing error details.
        """
        logger.error(
            "Couldn't execute %s on table %s. %s: %s",
            method_name, self.table.name,
            err.response['Error']['Code'], err.response['Error']['Message'])

        # pylint: disable=misplaced-bare-raise
        raise

    def get_log_html_body(self, limit=20):
        """
        get logs and turn into html.
        """
        data = self.query_log(limit)
        data = pd.DataFrame(
            data)[['timestamp', 'user_id', 'prompt', 'input_text', 'output_text']]
        data = data.sort_values(by='timestamp', ascending=False)
        data['timestamp'] = data['timestamp'].apply(
            lambda x:
            pd.to_datetime(int(x), unit='s', utc=True).tz_convert('Asia/Taipei').strftime('%Y-%m-%d %H:%M:%S'))
        tbody = data.to_html(header=False, index=False)
        tbody = re.sub('<table border="1" class="dataframe">', '', tbody)
        tbody = re.sub('</table>', '', tbody)
        tbody = re.sub('\n', '', tbody)
        return tbody
