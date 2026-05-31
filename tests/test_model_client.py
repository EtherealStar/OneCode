from __future__ import annotations

import json
from unittest.mock import patch

from onecode.model_client import ChatCompletionsClient


class FakeHTTPResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode("utf-8")


def test_chat_completions_client_uses_custom_base_url_and_key():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    client = ChatCompletionsClient(
        model="custom-model",
        api_key="custom-key",
        base_url="https://example.test/custom/v1",
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        response = client.send(system="sys", messages=[], tools=[], max_output_tokens=123)

    assert response.final_text == "ok"
    assert captured["url"] == "https://example.test/custom/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer custom-key"
    assert captured["payload"]["model"] == "custom-model"
    assert captured["payload"]["max_tokens"] == 123
