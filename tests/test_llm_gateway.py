import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml


with patch.dict(os.environ, {"LITELLM_MASTER_KEY": "unit-test-master-key"}):
    import mcp_client


class LlmGatewayTests(unittest.TestCase):
    def test_client_uses_configured_gateway_url_and_master_key(self):
        settings = {
            "LITELLM_MASTER_KEY": "unit-test-master-key",
            "LITELLM_BASE_URL": "https://litellm.internal/v1/",
        }
        with patch("mcp_client.ChatOpenAI") as chat_openai:
            mcp_client.create_llm_client(settings)

        chat_openai.assert_called_once_with(
            model="tripmate-primary",
            api_key="unit-test-master-key",
            base_url="https://litellm.internal/v1",
            timeout=60,
            max_retries=0,
        )

    def test_client_defaults_to_local_gateway(self):
        with patch("mcp_client.ChatOpenAI") as chat_openai:
            mcp_client.create_llm_client(
                {"LITELLM_MASTER_KEY": "unit-test-master-key"}
            )

        self.assertEqual(
            chat_openai.call_args.kwargs["base_url"],
            "http://127.0.0.1:4000/v1",
        )

    def test_missing_master_key_has_actionable_error(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "LITELLM_MASTER_KEY is missing.*service environment",
        ):
            mcp_client.create_llm_client({})

    def test_invalid_gateway_url_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "LITELLM_BASE_URL"):
            mcp_client.create_llm_client(
                {
                    "LITELLM_MASTER_KEY": "unit-test-master-key",
                    "LITELLM_BASE_URL": "litellm.internal",
                }
            )

    def test_gateway_connection_failure_is_not_silenced(self):
        gateway_client = MagicMock()
        gateway_client.invoke.side_effect = ConnectionError("gateway unavailable")
        with patch("mcp_client.ChatOpenAI", return_value=gateway_client):
            client = mcp_client.create_llm_client(
                {
                    "LITELLM_MASTER_KEY": "unit-test-master-key",
                    "LITELLM_BASE_URL": "http://litellm.internal/v1",
                }
            )

        with self.assertRaisesRegex(ConnectionError, "gateway unavailable"):
            client.invoke("test request")

    def test_gateway_error_messages_redact_environment_secrets(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-provider-secret"}):
            message = mcp_client.safe_error_message(
                RuntimeError("provider rejected test-provider-secret")
            )

        self.assertNotIn("test-provider-secret", message)
        self.assertIn("[REDACTED]", message)

    def test_configured_aliases_and_primary_fallback_match_client(self):
        config_path = Path(__file__).resolve().parents[1] / "litellm_config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        models = {
            item["model_name"]: item["litellm_params"]
            for item in config["model_list"]
        }

        self.assertEqual(
            models["tripmate-primary"]["model"],
            "groq/openai/gpt-oss-120b",
        )
        self.assertEqual(
            models["tripmate-fallback"]["model"],
            "gemini/gemini-3.8-flash",
        )
        self.assertEqual(
            config["router_settings"]["fallbacks"],
            [{"tripmate-primary": ["tripmate-fallback"]}],
        )
        self.assertEqual(
            config["general_settings"]["master_key"],
            "os.environ/LITELLM_MASTER_KEY",
        )
        self.assertEqual(
            models["tripmate-primary"]["api_key"],
            "os.environ/GROQ_API_KEY",
        )
        self.assertEqual(
            models["tripmate-fallback"]["api_key"],
            "os.environ/GEMINI_API_KEY",
        )


if __name__ == "__main__":
    unittest.main()
