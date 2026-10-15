import os
import unittest
from unittest.mock import MagicMock, patch

import container_entrypoint


class ContainerEntrypointTests(unittest.TestCase):
    def test_environment_validation_names_missing_secrets_only(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "LITELLM_MASTER_KEY, GROQ_API_KEY, GEMINI_API_KEY",
        ) as error:
            container_entrypoint.validate_environment({})

        self.assertNotIn("test-secret", str(error.exception))

    def test_environment_validation_accepts_gateway_credentials(self):
        container_entrypoint.validate_environment(
            {
                "LITELLM_MASTER_KEY": "gateway-secret",
                "GROQ_API_KEY": "groq-secret",
                "GEMINI_API_KEY": "gemini-secret",
            }
        )

    def test_gateway_command_binds_only_to_loopback(self):
        with patch("container_entrypoint.shutil.which", return_value="litellm"):
            command = container_entrypoint.gateway_command()

        self.assertEqual(command[0], "litellm")
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(command[command.index("--port") + 1], "4000")
        self.assertIn("litellm_config.yaml", command[command.index("--config") + 1])

    def test_gateway_command_fails_when_litellm_is_not_installed(self):
        with patch("container_entrypoint.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "was not installed"):
                container_entrypoint.gateway_command()

    def test_gateway_environment_excludes_app_database_settings(self):
        app_environment = {
            "DATABASE_URL": "postgresql://app-db",
            "DATABASE_URL_READ_REPLICA": "postgresql://replica",
            "LITELLM_DATABASE_URL": "postgresql://litellm-db",
            "DATABASE_CONNECTION_TIMEOUT": "10",
            "STORE_MODEL_IN_DB": "true",
            "DISABLE_SCHEMA_UPDATE": "true",
            "DISABLE_PRISMA_HEALTH_CHECK_ON_STARTUP": "true",
            "GROQ_API_KEY": "groq-provider-key",
            "GEMINI_API_KEY": "gemini-provider-key",
            "LITELLM_MASTER_KEY": "gateway-master-key",
            "PORT": "10000",
            "PATH": "system-path",
        }

        gateway_env = container_entrypoint.gateway_environment(app_environment)

        self.assertNotIn("DATABASE_URL", gateway_env)
        self.assertNotIn("DATABASE_URL_READ_REPLICA", gateway_env)
        self.assertNotIn("LITELLM_DATABASE_URL", gateway_env)
        self.assertNotIn("DATABASE_CONNECTION_TIMEOUT", gateway_env)
        self.assertNotIn("STORE_MODEL_IN_DB", gateway_env)
        self.assertNotIn("DISABLE_SCHEMA_UPDATE", gateway_env)
        self.assertNotIn("DISABLE_PRISMA_HEALTH_CHECK_ON_STARTUP", gateway_env)
        self.assertEqual(gateway_env["GROQ_API_KEY"], "groq-provider-key")
        self.assertEqual(gateway_env["GEMINI_API_KEY"], "gemini-provider-key")
        self.assertEqual(gateway_env["LITELLM_MASTER_KEY"], "gateway-master-key")
        self.assertEqual(gateway_env["PORT"], "10000")
        self.assertEqual(gateway_env["PATH"], "system-path")
        self.assertIn("DATABASE_URL", app_environment)

    def test_main_passes_isolated_environment_to_litellm_only(self):
        gateway_process = MagicMock()
        gateway_process.poll.return_value = 1
        application_database_url = "postgresql://tripmate-db"

        with (
            patch.dict(
                os.environ,
                {
                    "LITELLM_MASTER_KEY": "gateway-master-key",
                    "GROQ_API_KEY": "groq-provider-key",
                    "GEMINI_API_KEY": "gemini-provider-key",
                    "DATABASE_URL": application_database_url,
                    "LITELLM_DATABASE_URL": "postgresql://unused-gateway-db",
                },
                clear=True,
            ),
            patch("container_entrypoint.load_dotenv"),
            patch("container_entrypoint.gateway_command", return_value=["litellm"]),
            patch(
                "container_entrypoint.application_command",
                return_value=["uvicorn"],
            ),
            patch(
                "container_entrypoint.subprocess.Popen",
                return_value=gateway_process,
            ) as popen,
        ):
            self.assertEqual(container_entrypoint.main(), 1)

            gateway_env = popen.call_args.kwargs["env"]
            self.assertNotIn("DATABASE_URL", gateway_env)
            self.assertNotIn("LITELLM_DATABASE_URL", gateway_env)
            self.assertEqual(
                gateway_env["LITELLM_MASTER_KEY"],
                "gateway-master-key",
            )
            self.assertEqual(os.environ["DATABASE_URL"], application_database_url)

    def test_application_command_uses_render_port(self):
        command = container_entrypoint.application_command({"PORT": "12345"})

        self.assertIn("0.0.0.0", command)
        self.assertEqual(command[-1], "12345")

    def test_application_command_rejects_invalid_port(self):
        for port in ("invalid", "0", "65536"):
            with self.subTest(port=port):
                with self.assertRaisesRegex(RuntimeError, "PORT must be"):
                    container_entrypoint.application_command({"PORT": port})

    def test_http_readiness_requires_success_status(self):
        response = MagicMock()
        response.status = 200
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with (
            patch("container_entrypoint.build_opener", return_value=opener) as builder,
            patch("container_entrypoint.ProxyHandler") as proxy_handler,
        ):
            self.assertTrue(
                container_entrypoint.http_service_ready("http://127.0.0.1/health")
            )
        builder.assert_called_once_with(proxy_handler.return_value)
        proxy_handler.assert_called_once_with({})

        response.status = 503
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with (
            patch("container_entrypoint.build_opener", return_value=opener),
            patch("container_entrypoint.ProxyHandler"),
        ):
            self.assertFalse(
                container_entrypoint.http_service_ready("http://127.0.0.1/health")
            )

    def test_litellm_readiness_wait_succeeds_when_health_check_recovers(self):
        process = MagicMock()
        process.poll.return_value = None

        with (
            patch(
                "container_entrypoint.http_service_ready",
                side_effect=[False, True],
            ) as readiness,
            patch("container_entrypoint.time.sleep"),
        ):
            container_entrypoint.wait_for_service(
                "LiteLLM",
                "http://127.0.0.1:4000/health/liveliness",
                process,
            )

        self.assertEqual(
            [call.args[0] for call in readiness.call_args_list],
            [
                "http://127.0.0.1:4000/health/liveliness",
                "http://127.0.0.1:4000/health/liveliness",
            ],
        )

    def test_litellm_readiness_timeout_when_health_never_succeeds(self):
        process = MagicMock()
        process.poll.return_value = None

        with (
            patch("container_entrypoint.time.monotonic", side_effect=[0.0, 0.5, 1.0]),
            patch("container_entrypoint.http_service_ready", return_value=False),
            patch("container_entrypoint.time.sleep"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "LiteLLM did not become healthy within 1 seconds",
            ):
                container_entrypoint.wait_for_service(
                    "LiteLLM",
                    "http://127.0.0.1:4000/health/liveliness",
                    process,
                    timeout=1.0,
                )

    def test_startup_fails_if_service_process_exits_early(self):
        process = MagicMock()
        process.poll.return_value = 1

        with self.assertRaisesRegex(RuntimeError, "exited during startup"):
            container_entrypoint.wait_for_service(
                "LiteLLM",
                container_entrypoint.LITELLM_HEALTH_URL,
                process,
            )

    def test_startup_wait_can_be_interrupted_for_shutdown(self):
        process = MagicMock()
        process.poll.return_value = None
        with self.assertRaisesRegex(InterruptedError, "was interrupted"):
            container_entrypoint.wait_for_service(
                "LiteLLM",
                container_entrypoint.LITELLM_HEALTH_URL,
                process,
                should_stop=lambda: True,
            )
        process.poll.assert_not_called()

    def test_termination_signal_is_forwarded_to_child_process_group(self):
        process = MagicMock()
        process.poll.return_value = None

        with (
            patch("container_entrypoint.os.name", "posix"),
            patch("container_entrypoint.os.killpg", create=True) as killpg,
        ):
            container_entrypoint._signal_process(process, 15)

        killpg.assert_called_once_with(process.pid, 15)

    def test_shutdown_waits_for_children(self):
        process = MagicMock()
        process.poll.return_value = 0

        container_entrypoint._stop_processes([process])

        process.wait.assert_called_once_with(timeout=10)


if __name__ == "__main__":
    unittest.main()
