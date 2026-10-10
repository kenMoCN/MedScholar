import json
import unittest

from model_router import ModelRegistry, ModelRole
from provider_adapters import (
    DoubaoVisionAdapter,
    HttpResponse,
    OpenAICompatibleJsonClient,
    ProviderAdapterFactory,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRequestError,
    ProviderResponseError,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post_json(self, url, headers, payload, timeout_seconds):
        self.calls.append((url, dict(headers), dict(payload), timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def chat_response(content, *, finish_reason="stop", status=200, usage=None):
    body = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": content},
                }
            ],
            **({"usage": usage} if usage is not None else {}),
        }
    ).encode("utf-8")
    return HttpResponse(status=status, body=body, headers={})


class ProviderAdapterTests(unittest.TestCase):
    def _registry(self):
        config = {
            "model_registry": {
                "reasoner": {
                    "provider": "deepseek",
                    "model_name": "deepseek-example",
                    "api_base": None,
                    "api_protocol": "openai_compatible",
                    "api_key_ref": "env:DEEPSEEK_API_KEY",
                    "capabilities": ["text", "reasoning", "structured_output"],
                    "enabled": True,
                },
                "vision": {
                    "provider": "doubao",
                    "model_name": "doubao-vision-example",
                    "api_base": None,
                    "api_protocol": "openai_compatible",
                    "api_key_ref": "env:DOUBAO_API_KEY",
                    "capabilities": ["vision", "ocr", "structured_output"],
                    "enabled": True,
                },
            },
            "model_routes": {
                "reasoning": "reasoner",
                "planning": "reasoner",
                "verification": "reasoner",
                "vision": "vision",
            },
        }
        return ModelRegistry.from_config(
            config,
            {"DEEPSEEK_API_KEY": "secret-ds", "DOUBAO_API_KEY": "secret-db"},
        )

    def test_deepseek_json_request_uses_official_compatible_endpoint(self):
        transport = FakeTransport([chat_response('{"answer":"ok"}')])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).json_client(ModelRole.REASONING)

        result = client.complete_json("请输出JSON。", {"question": "test"})

        self.assertEqual(result, {"answer": "ok"})
        url, headers, payload, _ = transport.calls[0]
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["model"], "deepseek-example")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(headers["Authorization"], "Bearer secret-ds")

    def test_provider_accumulates_token_usage_for_cost_comparison(self):
        transport = FakeTransport([chat_response(
            '{"answer":"ok"}',
            usage={"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        )])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).json_client(ModelRole.REASONING)

        client.complete_json("请输出JSON。", {"question": "test"})

        self.assertEqual(client.usage_totals["total_tokens"], 16)
        self.assertEqual(client.usage_totals["requests"], 1)

    def test_api_key_is_not_exposed_in_authentication_error(self):
        transport = FakeTransport([HttpResponse(401, b"unauthorized", {})])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).json_client()

        with self.assertRaises(ProviderAuthenticationError) as context:
            client.complete_json("输出JSON。", {"question": "test"})
        self.assertNotIn("secret-ds", str(context.exception))

    def test_payment_required_has_actionable_message(self):
        transport = FakeTransport([HttpResponse(402, b"payment required", {})])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).json_client()

        with self.assertRaisesRegex(ProviderRequestError, "余额或付费额度不足"):
            client.complete_json("输出JSON。", {"question": "test"})

    def test_truncated_json_response_is_rejected(self):
        transport = FakeTransport([
            chat_response("{}", finish_reason="length"),
            chat_response("{}", finish_reason="length"),
        ])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).json_client()
        with self.assertRaises(ProviderResponseError):
            client.complete_json("输出JSON。", {"question": "test"})

    def test_truncated_json_is_retried_once_with_larger_concise_budget(self):
        transport = FakeTransport([
            chat_response("{}", finish_reason="length"),
            chat_response('{"answer":"ok"}'),
        ])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).json_client()

        result = client.complete_json("输出JSON。", {"question": "test"})

        self.assertEqual(result, {"answer": "ok"})
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0][2]["max_tokens"], 4096)
        self.assertEqual(transport.calls[1][2]["max_tokens"], 8192)
        self.assertIn("必须更精炼", transport.calls[1][2]["messages"][0]["content"])

    def test_malformed_model_json_is_rejected(self):
        transport = FakeTransport([
            chat_response("not-json"),
            chat_response("still-not-json"),
        ])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).json_client()
        with self.assertRaises(ProviderResponseError):
            client.complete_json("输出JSON。", {"question": "test"})
        self.assertEqual(len(transport.calls), 2)

    def test_malformed_model_json_is_retried_once(self):
        transport = FakeTransport([
            chat_response("not-json"),
            chat_response('{"answer":"ok"}'),
        ])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).json_client()

        result = client.complete_json("输出JSON。", {"question": "test"})

        self.assertEqual(result, {"answer": "ok"})
        self.assertEqual(len(transport.calls), 2)
        self.assertIn(
            "只能返回一个完整JSON对象",
            transport.calls[1][2]["messages"][0]["content"],
        )

    def test_rate_limit_is_retried_once(self):
        sleeps = []
        transport = FakeTransport(
            [
                HttpResponse(429, b"rate limited", {}),
                chat_response('{"answer":"ok"}'),
            ]
        )
        model = self._registry().resolve(
            ModelRole.REASONING, {"structured_output"}
        )
        client = OpenAICompatibleJsonClient(
            model,
            transport=transport,
            max_retries=1,
            sleeper=sleeps.append,
        )

        self.assertEqual(client.complete_json("输出JSON。", {}), {"answer": "ok"})
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(sleeps, [1])

    def test_connection_failure_is_retried(self):
        sleeps = []
        transport = FakeTransport([
            ProviderRequestError("模型服务连接失败: TimeoutError"),
            chat_response('{"answer":"ok"}'),
        ])
        model = self._registry().resolve(
            ModelRole.REASONING, {"structured_output"}
        )
        client = OpenAICompatibleJsonClient(
            model,
            transport=transport,
            max_retries=1,
            sleeper=sleeps.append,
        )

        self.assertEqual(client.complete_json("输出JSON。", {}), {"answer": "ok"})
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(sleeps, [1])

    def test_doubao_vision_uses_image_url_content_block(self):
        transport = FakeTransport([chat_response('{"plain_text":"题目"}')])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).vision_client()

        result = client.analyze_image(
            b"fake-png-bytes",
            "转录题目并输出JSON。",
            media_type="image/png",
        )

        self.assertEqual(result["plain_text"], "题目")
        url, headers, payload, _ = transport.calls[0]
        self.assertEqual(
            url, "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        )
        content = payload["messages"][1]["content"]
        self.assertNotIn("thinking", payload)
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(headers["Authorization"], "Bearer secret-db")

    def test_deepseek_flash_vision_uses_existing_key_and_image_input(self):
        config = {
            "model_registry": {
                "flash": {
                    "provider": "deepseek",
                    "model_name": "deepseek-flash",
                    "api_protocol": "openai_compatible",
                    "api_key_ref": "env:DEEPSEEK_API_KEY",
                    "capabilities": ["text", "vision", "structured_output"],
                    "enabled": True,
                }
            },
            "model_routes": {"vision": "flash"},
        }
        registry = ModelRegistry.from_config(config, {"DEEPSEEK_API_KEY": "secret-ds"})
        transport = FakeTransport([chat_response('{"plain_text":"题目"}')])
        client = ProviderAdapterFactory(registry, transport=transport).vision_client()

        result = client.analyze_image(b"fake-png-bytes", "转录题目并输出JSON。", media_type="image/png")

        self.assertEqual(result["plain_text"], "题目")
        url, headers, payload, _ = transport.calls[0]
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer secret-ds")
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["messages"][1]["content"][0]["type"], "image_url")

    def test_local_image_size_limit_is_enforced_before_http(self):
        transport = FakeTransport([])
        client = ProviderAdapterFactory(
            self._registry(), transport=transport
        ).vision_client()
        with self.assertRaises(ProviderRequestError):
            client.analyze_image(
                b"12345",
                "识别",
                media_type="image/png",
                max_image_bytes=4,
            )
        self.assertEqual(transport.calls, [])

    def test_non_compatible_protocol_is_rejected(self):
        config = {
            "model_registry": {
                "custom": {
                    "provider": "custom",
                    "model_name": "custom-model",
                    "api_base": "https://example.invalid/v1",
                    "api_protocol": "vendor_specific",
                    "api_key_ref": "env:CUSTOM_KEY",
                    "capabilities": ["text", "structured_output"],
                    "enabled": True,
                }
            },
            "model_routes": {"reasoning": "custom"},
        }
        registry = ModelRegistry.from_config(config, {"CUSTOM_KEY": "secret"})
        with self.assertRaises(ProviderConfigurationError):
            ProviderAdapterFactory(registry, transport=FakeTransport([])).json_client()


if __name__ == "__main__":
    unittest.main()
