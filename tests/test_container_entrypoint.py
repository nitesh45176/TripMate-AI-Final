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
        with patch("container_entrypoint.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            self.assertTrue(
                container_entrypoint.http_service_ready("http://127.0.0.1/health")
            )

        response.status = 503
        with patch("container_entrypoint.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            self.assertFalse(
                container_entrypoint.http_service_ready("http://127.0.0.1/health")
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
