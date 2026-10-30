"""Settings and channel-spec contracts for the Pi substrate and the CLI improver channels."""

import os
import unittest
from unittest.mock import patch

from harness2 import config
from harness2.core.spec import INSTALL_MODES, resolve_channel_model


class ChannelSpecTests(unittest.TestCase):
    def test_codex_channel_takes_bare_id(self):
        ch = resolve_channel_model("codex_cli", "gpt-5.6-sol", effort="high")
        self.assertEqual((ch.kind, ch.model, ch.effort), ("codex_cli", "gpt-5.6-sol", "high"))

    def test_cli_channels_reject_provider_prefix(self):
        for kind in ("claude_cli", "codex_cli"):
            with self.assertRaises(ValueError):
                resolve_channel_model(kind, "anthropic/opus")

    def test_pi_workspace_is_an_install_mode(self):
        self.assertIn("pi_workspace", INSTALL_MODES)


class ConfigTests(unittest.TestCase):
    def test_needs_opencode(self):
        self.assertTrue(config.needs_opencode("oc", "claude_cli"))
        self.assertTrue(config.needs_opencode("pi", "opencode"))
        self.assertFalse(config.needs_opencode("pi", "claude_cli"))
        self.assertFalse(config.needs_opencode("cx", "codex_cli"))

    def test_validate_models_cli_improver(self):
        # patch BOTH os.environ (require() reads it) and the module constants
        with (
            patch.dict(os.environ, {"HARNESS2_SOLVER_MODEL": "zai/glm-5.3-flash"}),
            patch.object(config, "SOLVER_MODEL_OC", "zai/glm-5.3-flash"),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "claude_cli"),
        ):
            with patch.object(config, "IMPROVER_MODEL", "opus"):
                config.validate_models()
            with (
                patch.object(config, "IMPROVER_MODEL", "anthropic/opus"),
                self.assertRaises(config.ConfigError),
            ):
                config.validate_models()

    def test_validate_models_cli_judge_needs_bridge(self):
        with (
            patch.dict(
                os.environ,
                {"HARNESS2_SOLVER_MODEL": "zai/x", "HARNESS2_JUDGE_MODEL": "cli/codex:gpt-5.6-sol"},
            ),
            patch.object(config, "SOLVER_MODEL_OC", "zai/x"),
            patch.object(config, "IMPROVER_MODEL", "zai/x"),
            patch.object(config, "DEFAULT_PROPOSER_CHANNEL", "opencode"),
            patch.object(config, "JUDGE_MODEL", "cli/codex:gpt-5.6-sol"),
            patch.object(config, "JB_JUDGE_API_BASE", ""),
        ):
            with self.assertRaisesRegex(config.ConfigError, "HARNESS2_JUDGE_BASE_URL"):
                config.validate_models(judge=True)


if __name__ == "__main__":
    unittest.main()
