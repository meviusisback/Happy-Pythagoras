import os
import json
import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from pydantic import ValidationError

from agency_finder.ai_config import (
    get_registered_providers, provider_info, get_api_key, set_api_key,
    clear_api_key, clear_all_api_keys, is_configured, configured_providers,
    redact_keys, PROVIDER_REGISTRY,
)
from agency_finder.ai_schemas import AIEnhancedReport, AIApproach, AIQueryResult, PortfolioHighlight
from agency_finder.ai_providers import achat, achat_json, alist_models, AIError, clear_model_cache
from agency_finder.ai_pipeline import aoptimize_search_query, aenhance_report, acommercial_approach, aprocess_full, _best_provider, _best_model
from agency_finder.core import alookup_agency


class TestAiConfig(unittest.TestCase):
    def setUp(self):
        for p in get_registered_providers():
            clear_api_key(p)

    def test_get_registered_providers(self):
        providers = get_registered_providers()
        self.assertIn("openai", providers)
        self.assertIn("claude", providers)
        self.assertIn("gemini", providers)
        self.assertIn("openrouter", providers)
        self.assertIn("deepseek", providers)
        self.assertIn("opencodego", providers)

    def test_opencodego_base_url(self):
        info = provider_info("opencodego")
        self.assertEqual(info["base_url"], "https://opencode.ai/zen/go/v1")

    def test_opencodego_supports_models_endpoint(self):
        info = provider_info("opencodego")
        self.assertTrue(info["supports_models_endpoint"])

    def test_opencodego_fallback_models(self):
        info = provider_info("opencodego")
        expected = [
            "minimax-m3", "minimax-m2.7", "minimax-m2.5",
            "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
            "glm-5.2", "glm-5.1", "glm-5",
            "deepseek-v4-pro", "deepseek-v4-flash",
            "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus",
            "mimo-v2-pro", "mimo-v2-omni", "mimo-v2.5-pro", "mimo-v2.5",
            "hy3-preview",
        ]
        self.assertEqual(info["fallback_models"], expected)

    def test_provider_info_known(self):
        info = provider_info("openai")
        self.assertEqual(info["label"], "OpenAI")
        self.assertEqual(info["sdk_family"], "openai")
        self.assertEqual(info["env_var"], "OPENAI_API_KEY")

    def test_provider_info_unknown_raises(self):
        with self.assertRaises(ValueError):
            provider_info("nonexistent")

    def test_set_get_clear_key(self):
        self.assertFalse(is_configured("openai"))
        set_api_key("openai", "sk-test123")
        self.assertTrue(is_configured("openai"))
        self.assertEqual(get_api_key("openai"), "sk-test123")
        clear_api_key("openai")
        self.assertFalse(is_configured("openai"))

    def test_clear_all_keys(self):
        set_api_key("openai", "sk-test")
        set_api_key("claude", "sk-ant-test")
        self.assertTrue(configured_providers())
        clear_all_api_keys()
        self.assertFalse(configured_providers())

    def test_configured_providers_empty(self):
        self.assertEqual(configured_providers(), [])

    def test_configured_providers_non_empty(self):
        set_api_key("gemini", "test-key")
        configured = configured_providers()
        self.assertIn("gemini", configured)
        self.assertEqual(len(configured), 1)

    def test_bootstrap_from_env(self):
        os.environ["OPENAI_API_KEY"] = "env-key-123"
        self.assertEqual(get_api_key("openai"), "env-key-123")
        del os.environ["OPENAI_API_KEY"]

    def test_redact_keys_dict(self):
        data = {"normal": "value", "API_KEY": "secret123", "nested": {"api_key": "nested-secret"}}
        redacted = redact_keys(data)
        self.assertEqual(redacted["normal"], "value")
        self.assertEqual(redacted["API_KEY"], "***")
        self.assertEqual(redacted["nested"]["api_key"], "***")

    def test_redact_keys_list(self):
        data = [{"api_key": "secret"}, {"other": "public"}]
        redacted = redact_keys(data)
        self.assertEqual(redacted[0]["api_key"], "***")
        self.assertEqual(redacted[1]["other"], "public")

    def test_redact_keys_primitives(self):
        self.assertEqual(redact_keys("hello"), "hello")
        self.assertEqual(redact_keys(42), 42)


class TestAiSchemas(unittest.TestCase):
    def test_ai_query_result_valid(self):
        result = AIQueryResult(queries=["q1", "q2", "q3"])
        self.assertEqual(len(result.queries), 3)
        self.assertIn("q1", result.queries)

    def test_ai_query_result_empty(self):
        result = AIQueryResult(queries=[])
        self.assertEqual(result.queries, [])

    def test_ai_enhanced_report_valid(self):
        report = AIEnhancedReport(
            summary="A company",
            company_profile="A profile",
            services_grouped={"Web": ["dev"]},
            portfolio_highlights=[PortfolioHighlight(domain="client.com", description="desc")],
            key_strengths=["fast"],
        )
        self.assertEqual(report.summary, "A company")
        self.assertEqual(len(report.portfolio_highlights), 1)
        self.assertEqual(report.portfolio_highlights[0].domain, "client.com")

    def test_ai_approach_valid(self):
        approach = AIApproach(
            recap="A recap",
            ideal_outreach_angle="Angle",
            suggested_first_message="Ciao",
            talking_points=["point1"],
            red_flags=[],
            best_channel="linkedin",
            best_channel_reason="Active",
            partnership_angle="partnership",
        )
        self.assertEqual(approach.best_channel, "linkedin")
        self.assertEqual(approach.partnership_angle, "partnership")

    def test_ai_approach_invalid_channel(self):
        with self.assertRaises(ValidationError):
            AIApproach(
                recap="r", ideal_outreach_angle="a", suggested_first_message="m",
                talking_points=[], red_flags=[], best_channel="snailmail",
                best_channel_reason="", partnership_angle="partnership",
            )

    def test_ai_approach_partnership_models_field(self):
        approach = AIApproach(
            recap="r", ideal_outreach_angle="a", suggested_first_message="m",
            talking_points=[], red_flags=[], best_channel="email",
            best_channel_reason="R", partnership_angle="partnership",
            partnership_models=["referral", "technology_integration"],
        )
        self.assertEqual(len(approach.partnership_models), 2)
        self.assertIn("referral", approach.partnership_models)

    def test_ai_approach_default_partnership_angle(self):
        approach = AIApproach(
            recap="r", ideal_outreach_angle="a", suggested_first_message="m",
            talking_points=[], red_flags=[], best_channel="email",
            best_channel_reason="R",
        )
        self.assertEqual(approach.partnership_angle, "partnership")

    def test_portfolio_highlight_from_json(self):
        raw = '{"domain": "example.com", "description": "An e-commerce site"}'
        ph = PortfolioHighlight.model_validate_json(raw)
        self.assertEqual(ph.domain, "example.com")
        self.assertEqual(ph.description, "An e-commerce site")


class TestAiPrompts(unittest.TestCase):

    def test_approach_prompt_mentions_payments_and_partner(self):
        from agency_finder.prompts import _build_approach_prompt
        prompt = _build_approach_prompt()
        self.assertIn("payments", prompt.lower())
        self.assertIn("partner", prompt.lower())

    def test_build_approach_prompt_substitutes_sender(self):
        from agency_finder.prompts import _build_approach_prompt
        prompt = _build_approach_prompt("Stripe Italia")
        self.assertIn("Stripe Italia", prompt)
        self.assertNotIn("an Italian payments company", prompt)

    def test_build_approach_prompt_default_when_empty(self):
        from agency_finder.prompts import _build_approach_prompt
        prompt = _build_approach_prompt("")
        self.assertIn("an Italian payments company", prompt)
        prompt2 = _build_approach_prompt("   ")
        self.assertIn("an Italian payments company", prompt2)


class TestAiProviders(unittest.TestCase):
    def setUp(self):
        clear_model_cache()
        set_api_key("openai", "sk-test")
        set_api_key("claude", "sk-ant-test")
        set_api_key("gemini", "test-key")

    def tearDown(self):
        clear_all_api_keys()

    @patch("agency_finder.ai_providers._get_openai_client")
    def test_achat_openai(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello from OpenAI"
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = asyncio.run(achat("openai", "gpt-4o-mini", [{"role": "user", "content": "Hi"}]))
        self.assertEqual(result, "Hello from OpenAI")

    @patch("agency_finder.ai_providers._get_anthropic_client")
    def test_achat_claude(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_content = MagicMock()
        mock_content.text = "Hello from Claude"
        mock_response.content = [mock_content]
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = asyncio.run(achat("claude", "claude-sonnet-4", [{"role": "user", "content": "Hi"}]))
        self.assertEqual(result, "Hello from Claude")

    @patch("agency_finder.ai_providers._get_gemini_client")
    def test_achat_gemini(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Hello from Gemini"
        mock_client.models.generate_content_async = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = asyncio.run(achat("gemini", "gemini-2.5-flash", [{"role": "user", "content": "Hi"}]))
        self.assertEqual(result, "Hello from Gemini")

    @patch("agency_finder.ai_providers._get_openai_client")
    def test_achat_json_openai(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"queries": ["q1", "q2"]}'
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        result = asyncio.run(achat_json("openai", "gpt-4o-mini", [{"role": "user", "content": "Test"}], schema=AIQueryResult))
        self.assertIsInstance(result, AIQueryResult)
        self.assertEqual(result.queries, ["q1", "q2"])

    @patch("agency_finder.ai_providers._get_openai_client")
    def test_achat_error_401(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("Incorrect API key"))
        mock_get_client.return_value = mock_client

        with self.assertRaises(AIError) as ctx:
            asyncio.run(achat("openai", "gpt-4o-mini", [{"role": "user", "content": "Hi"}]))
        self.assertIn("openai", str(ctx.exception))

    @patch("agency_finder.ai_providers._get_openai_client")
    def test_alist_models_openai(self, mock_get_client):
        mock_client = MagicMock()
        mock_model_1 = MagicMock()
        mock_model_1.id = "gpt-4o-mini"
        mock_model_1.object = "model"
        mock_model_2 = MagicMock()
        mock_model_2.id = "gpt-4o"
        mock_model_2.object = "model"
        mock_response = MagicMock()
        mock_response.data = [mock_model_1, mock_model_2]
        mock_client.models.list = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        models = asyncio.run(alist_models("openai"))
        self.assertIn("gpt-4o-mini", models)
        self.assertIn("gpt-4o", models)

    def test_alist_models_claude_fallback(self):
        models = asyncio.run(alist_models("claude"))
        self.assertTrue(len(models) > 0)
        self.assertIn("claude-sonnet-4-20250514", models)

    def test_alist_models_no_key_uses_fallback(self):
        clear_api_key("openai")
        models = asyncio.run(alist_models("openai"))
        self.assertTrue(len(models) > 0)
        self.assertIn("gpt-4o-mini", models)

    @patch("agency_finder.ai_providers._get_openai_client")
    def test_alist_models_opencodego_with_api(self, mock_get_client):
        set_api_key("opencodego", "sk-test")
        try:
            mock_client = MagicMock()
            models_data = [
                "minimax-m3", "minimax-m2.7", "minimax-m2.5",
                "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
                "glm-5.2", "glm-5.1", "glm-5",
                "deepseek-v4-pro", "deepseek-v4-flash",
                "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus",
                "mimo-v2-pro", "mimo-v2-omni", "mimo-v2.5-pro", "mimo-v2.5",
                "hy3-preview",
            ]
            mock_models = [MagicMock(id=m, object="model") for m in models_data]
            mock_response = MagicMock()
            mock_response.data = mock_models
            mock_client.models.list = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            models = asyncio.run(alist_models("opencodego"))
            self.assertEqual(len(models), 20)
            for m in models_data:
                self.assertIn(m, models)
        finally:
            clear_api_key("opencodego")

    def test_achat_unknown_provider(self):
        with self.assertRaises(AIError):
            asyncio.run(achat("unknown", "model", [{"role": "user", "content": "Hi"}]))
    
    @patch("agency_finder.ai_providers._get_openai_client")
    def test_achat_openai_handles_json_string_response(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value='{"content": "response"}')
        mock_get_client.return_value = mock_client

        result = asyncio.run(achat("openai", "gpt-4o-mini", [{"role": "user", "content": "Hi"}]))
        self.assertEqual(result, '{"content": "response"}')
    
    @patch("agency_finder.ai_providers._get_openai_client")
    def test_achat_json_openai_handles_string_response(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value='{"queries": ["q1"]}')
        mock_get_client.return_value = mock_client

        from agency_finder.ai_schemas import AIQueryResult
        result = asyncio.run(achat_json("openai", "gpt-4o-mini", [{"role": "user", "content": "Test"}], schema=AIQueryResult))
        self.assertEqual(result.queries, ["q1"])

    @patch("agency_finder.ai_providers._get_openai_client")
    def test_achat_json_openai_chat_wrapper_string_response(self, mock_get_client):
        wrapper = json.dumps({
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "minimax-m3",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"queries": ["q1", "q2"]}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        })
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=wrapper)
        mock_get_client.return_value = mock_client

        from agency_finder.ai_schemas import AIQueryResult
        result = asyncio.run(achat_json("openai", "gpt-4o-mini", [{"role": "user", "content": "Test"}], schema=AIQueryResult))
        self.assertEqual(result.queries, ["q1", "q2"])

    @patch("agency_finder.ai_providers._get_openai_client")
    def test_achat_json_openai_uses_json_object_mode(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"queries": ["q1"]}'
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        from agency_finder.ai_schemas import AIQueryResult
        asyncio.run(achat_json("openai", "gpt-4o-mini", [{"role": "user", "content": "Test"}], schema=AIQueryResult))
        _, call_kwargs = mock_client.chat.completions.create.call_args
        self.assertEqual(call_kwargs["response_format"], {"type": "json_object"})

    @patch("agency_finder.ai_providers._get_openai_client")
    def test_achat_openai_non_json_string_raises_clean_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value="Not Found")
        mock_get_client.return_value = mock_client

        with self.assertRaises(AIError) as ctx:
            asyncio.run(achat("openai", "gpt-4o-mini", [{"role": "user", "content": "Hi"}]))
        self.assertIn("non-chat response", str(ctx.exception))

    @patch("agency_finder.ai_providers._get_openai_client")
    def test_achat_json_openai_non_json_string_raises_clean_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value="Not Found")
        mock_get_client.return_value = mock_client

        from agency_finder.ai_schemas import AIQueryResult
        with self.assertRaises(AIError) as ctx:
            asyncio.run(achat_json("openai", "gpt-4o-mini", [{"role": "user", "content": "Test"}], schema=AIQueryResult))
        self.assertIn("non-JSON response", str(ctx.exception))


class TestAiPipeline(unittest.TestCase):
    def setUp(self):
        set_api_key("openai", "sk-test")

    def tearDown(self):
        clear_all_api_keys()

    def test_best_provider(self):
        self.assertEqual(_best_provider(), "openai")

    def test_best_model(self):
        model = _best_model("openai")
        self.assertTrue(model)
        self.assertIn(model, PROVIDER_REGISTRY["openai"]["fallback_models"])

    def test_best_provider_no_keys(self):
        clear_all_api_keys()
        self.assertIsNone(_best_provider())

    @patch("agency_finder.ai_pipeline.achat_json")
    def test_aoptimize_search_query(self, mock_achat_json):
        mock_achat_json.return_value = AIQueryResult(queries=["q1", "q2", "q3"])
        queries = asyncio.run(aoptimize_search_query("Test Agency"))
        self.assertEqual(queries, ["q1", "q2", "q3"])

    @patch("agency_finder.ai_pipeline.achat_json")
    def test_aoptimize_search_query_no_provider(self, mock_achat_json):
        clear_all_api_keys()
        queries = asyncio.run(aoptimize_search_query("Test Agency"))
        self.assertEqual(queries, [])

    @patch("agency_finder.ai_pipeline.achat_json")
    def test_aoptimize_search_query_error(self, mock_achat_json):
        mock_achat_json.side_effect = Exception("API error")
        queries = asyncio.run(aoptimize_search_query("Test Agency", provider="openai", model="gpt-4o-mini"))
        self.assertEqual(queries, [])

    @patch("agency_finder.ai_pipeline.achat_json")
    def test_aenhance_report(self, mock_achat_json):
        mock_achat_json.return_value = AIEnhancedReport(
            summary="Test agency summary",
            company_profile="A profile",
            services_grouped={"Web": ["dev"]},
            portfolio_highlights=[PortfolioHighlight(domain="x.com", description="desc")],
            key_strengths=["fast"],
        )
        result = {"website": "https://example.com", "services": ["Web Development"], "portfolio_sites": ["x.com"]}
        report = asyncio.run(aenhance_report(result))
        self.assertIsNotNone(report)
        self.assertEqual(report.summary, "Test agency summary")

    @patch("agency_finder.ai_pipeline.achat_json")
    def test_aenhance_report_error(self, mock_achat_json):
        mock_achat_json.side_effect = Exception("Fail")
        report = asyncio.run(aenhance_report({"test": True}, provider="openai", model="gpt-4o-mini"))
        self.assertIsNone(report)

    @patch("agency_finder.ai_pipeline.achat_json")
    def test_acommercial_approach(self, mock_achat_json):
        mock_achat_json.return_value = AIApproach(
            recap="A good agency",
            ideal_outreach_angle="E-commerce angle",
            suggested_first_message="Ciao, siamo interessati...",
            talking_points=["point1", "point2"],
            red_flags=[],
            best_channel="email",
            best_channel_reason="Active on email",
            partnership_angle="consultative",
        )
        result = {"website": "https://example.com", "services": ["E-commerce"]}
        approach = asyncio.run(acommercial_approach(result))
        self.assertIsNotNone(approach)
        self.assertEqual(approach.best_channel, "email")

    @patch("agency_finder.ai_pipeline.aenhance_report")
    @patch("agency_finder.ai_pipeline.acommercial_approach")
    def test_aprocess_full(self, mock_approach, mock_enhance):
        mock_enhance.return_value = AIEnhancedReport(
            summary="S", company_profile="P", services_grouped={},
            portfolio_highlights=[], key_strengths=[],
        )
        mock_approach.return_value = AIApproach(
            recap="R", ideal_outreach_angle="A", suggested_first_message="M",
            talking_points=[], red_flags=[], best_channel="email",
            best_channel_reason="R", partnership_angle="formal",
        )
        result = {"website": "https://example.com"}
        updated = asyncio.run(aprocess_full(result, provider="openai", model="gpt-4o-mini"))
        self.assertIn("ai_enhanced", updated)
        self.assertIn("ai_approach", updated)
        self.assertEqual(updated["ai_enhanced"]["summary"], "S")
        self.assertEqual(updated["ai_approach"]["recap"], "R")

    @patch("agency_finder.ai_pipeline.aenhance_report")
    @patch("agency_finder.ai_pipeline.acommercial_approach")
    def test_aprocess_full_no_provider(self, mock_approach, mock_enhance):
        clear_all_api_keys()
        result = {"website": "https://example.com"}
        updated = asyncio.run(aprocess_full(result))
        self.assertEqual(updated, result)
        self.assertNotIn("ai_enhanced", updated)
        self.assertNotIn("ai_approach", updated)

    @patch("agency_finder.ai_pipeline.aenhance_report")
    @patch("agency_finder.ai_pipeline.acommercial_approach")
    def test_aprocess_full_partial_failure(self, mock_approach, mock_enhance):
        mock_enhance.side_effect = Exception("Enhance failed")
        mock_approach.return_value = AIApproach(
            recap="R", ideal_outreach_angle="A", suggested_first_message="M",
            talking_points=[], red_flags=[], best_channel="email",
            best_channel_reason="R", partnership_angle="formal",
        )
        result = {"website": "https://example.com"}
        updated = asyncio.run(aprocess_full(result, provider="openai", model="gpt-4o-mini"))
        self.assertNotIn("ai_enhanced", updated)
        self.assertIn("ai_approach", updated)


class TestAiSecurity(unittest.TestCase):
    def test_redact_keys_protects_json_export(self):
        data = {"result": "OK", "OPENAI_API_KEY": "sk-abc123", "ANTHROPIC_API_KEY": "sk-ant-xyz"}
        redacted = redact_keys(data)
        self.assertEqual(redacted["result"], "OK")
        self.assertEqual(redacted["OPENAI_API_KEY"], "***")
        self.assertEqual(redacted["ANTHROPIC_API_KEY"], "***")

    def test_redact_keys_deeply_nested(self):
        data = {"config": {"providers": [{"name": "openai", "api_key": "secret"}]}}
        redacted = redact_keys(data)
        self.assertEqual(redacted["config"]["providers"][0]["api_key"], "***")


class TestAiGuardClauses(unittest.TestCase):
    """Tests that the AI layer degrades gracefully when pydantic is unavailable."""

    def test_pydantic_available_flag(self):
        from agency_finder.ai_providers import _PYDANTIC_AVAILABLE
        self.assertTrue(_PYDANTIC_AVAILABLE)

    def test_schemas_available_flag(self):
        from agency_finder.ai_pipeline import _SCHEMAS_AVAILABLE
        self.assertTrue(_SCHEMAS_AVAILABLE)

    @patch("agency_finder.ai_pipeline._SCHEMAS_AVAILABLE", False)
    def test_pipeline_functions_return_empty_when_missing_pydantic(self):
        from agency_finder.ai_pipeline import (
            aoptimize_search_query, aenhance_report,
            acommercial_approach, aprocess_full,
        )
        queries = asyncio.run(aoptimize_search_query("Test"))
        self.assertEqual(queries, [])

        report = asyncio.run(aenhance_report({"test": True}))
        self.assertIsNone(report)

        approach = asyncio.run(acommercial_approach({"test": True}))
        self.assertIsNone(approach)

        result = asyncio.run(aprocess_full({"test": True}))
        self.assertEqual(result, {"test": True})

    def test_core_lookup_works_with_ai_disabled(self):
        from agency_finder.ai_config import clear_all_api_keys
        from agency_finder.config import Config
        clear_all_api_keys()
        Config.AI_ENABLED = False

        result = asyncio.run(alookup_agency(name=""))
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Provide at least a company name or a VAT number.")


if __name__ == "__main__":
    unittest.main()
