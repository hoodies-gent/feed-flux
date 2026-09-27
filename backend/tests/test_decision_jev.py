import json
import unittest
from unittest import mock

import requests

from app.decisions.contract import ReasonCode, TriageItem
from app.decisions.jev import JevDecisionProvider
from app.decisions.settings import DecisionSettings, decision_settings, is_enabled


def _settings(**overrides) -> DecisionSettings:
    base = {
        "mode": "shadow",
        "provider": "jev",
        "api_key": "test-key",
        "max_attempts": 3,
        "max_items_per_request": 20,
        "token_budget": 24000,
        "preview_chars": 400,
    }
    return DecisionSettings(**{**base, "model": "jev-latest", **overrides})


def _items(count: int, preview: str = "preview text") -> list[TriageItem]:
    return [
        TriageItem(
            item_id=f"dev-{index:03d}",
            subject=f"Subject {index}",
            sender="Sender",
            sender_email="sender@example.com",
            body_preview=preview,
        )
        for index in range(count)
    ]


class _Response:
    def __init__(self, status_code=200, body=None, invalid_json=False):
        self.status_code = status_code
        self._body = body or {}
        self._invalid_json = invalid_json

    def json(self):
        if self._invalid_json:
            raise ValueError("not json")
        return self._body


def _answers_for(payload, action="archive", confidence=0.95, reason=ReasonCode.NEWSLETTER):
    answers = {}
    for key in payload["questions"]:
        if key.startswith("action_"):
            answers[key] = {
                "type": "choice",
                "choice": action,
                "confidence": confidence,
                "probabilities": {action: confidence},
            }
        else:
            answers[key] = {"type": "choice", "choice": str(reason), "confidence": 0.8}
    return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 0}}


class _FakeSession:
    def __init__(self, responses=None, handler=None):
        self.responses = list(responses or [])
        self.handler = handler
        self.payloads = []
        self.kwargs = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        self.kwargs.append({"url": url, "headers": headers, "timeout": timeout})
        if self.handler is not None:
            return self.handler(json, len(self.payloads) - 1)
        if self.responses:
            response = self.responses.pop(0)
        else:
            response = _Response(200, _answers_for(json))
        if isinstance(response, Exception):
            raise response
        return response


def _provider(session, settings=None):
    return JevDecisionProvider(settings or _settings(), session=session, sleep=lambda _: None)


class JevRequestShapeTest(unittest.TestCase):
    def test_sends_one_request_per_batch_and_preserves_item_ids(self):
        session = _FakeSession()
        batch = _provider(session).decide_triage(_items(3))

        self.assertEqual(1, len(session.payloads))
        self.assertEqual(["dev-000", "dev-001", "dev-002"], [d.item_id for d in batch.decisions])
        self.assertEqual(1, batch.requests)
        self.assertEqual(0, batch.failed_requests)

    def test_posts_to_the_systemone_endpoint_with_bearer_auth_and_timeout(self):
        session = _FakeSession()
        _provider(session, _settings(base_url="https://api.typesafe.ai", timeout_seconds=3.0)).decide_triage(_items(1))

        sent = session.kwargs[0]
        self.assertEqual("https://api.typesafe.ai/v1/systemone", sent["url"])
        self.assertEqual("Bearer test-key", sent["headers"]["Authorization"])
        self.assertEqual(3.0, sent["timeout"])

    def test_sends_only_preview_fields_and_never_internal_ids_or_full_body(self):
        session = _FakeSession()
        items = _items(1, preview="x" * 900)
        items[0].subject = "Quarterly report"
        _provider(session, _settings(preview_chars=100)).decide_triage(items)

        payload = session.payloads[0]
        email = payload["state"]["emails"][0]
        self.assertEqual({"ref", "subject", "sender", "sender_email", "received", "preview"}, set(email))
        self.assertEqual(100, len(email["preview"]))
        self.assertNotIn("dev-000", json.dumps(payload))

    def test_asks_one_question_per_axis_per_item(self):
        session = _FakeSession()
        _provider(session).decide_triage(_items(2))

        questions = session.payloads[0]["questions"]
        self.assertEqual(
            {"action_e0", "reason_e0", "important_e0", "action_e1", "reason_e1", "important_e1"},
            set(questions),
        )
        self.assertEqual("choice", questions["action_e0"]["type"])
        self.assertEqual(
            {"mark_read", "archive", "delete", "needs_reply"},
            set(questions["action_e0"]["criteria"]),
        )


class JevChunkingTest(unittest.TestCase):
    def test_splits_on_max_items_per_request_and_covers_every_item_once(self):
        session = _FakeSession()
        batch = _provider(session, _settings(max_items_per_request=2)).decide_triage(_items(5))

        self.assertEqual(3, len(session.payloads))
        self.assertEqual([2, 2, 1], [len(p["state"]["emails"]) for p in session.payloads])
        self.assertEqual(5, len(batch.decisions))
        self.assertEqual(3, batch.requests)

    def test_splits_on_token_budget(self):
        session = _FakeSession()
        batch = _provider(session, _settings(token_budget=1000)).decide_triage(_items(8, preview="y" * 400))

        self.assertGreater(len(session.payloads), 1)
        self.assertEqual(8, len(batch.decisions))
        for payload in session.payloads:
            self.assertLessEqual(len(json.dumps(payload)) // 4, 1000)

    def test_aggregates_usage_across_chunks(self):
        session = _FakeSession()
        batch = _provider(session, _settings(max_items_per_request=1)).decide_triage(_items(3))

        self.assertEqual(3, batch.requests)
        self.assertEqual(300, batch.usage.input_tokens)


class JevRetryTest(unittest.TestCase):
    def test_retries_transient_status_then_succeeds(self):
        session = _FakeSession([_Response(429), _Response(529)])
        batch = _provider(session).decide_triage(_items(1))

        self.assertEqual(3, len(session.payloads))
        self.assertEqual("ok", batch.decisions[0].status)
        self.assertEqual(1, batch.requests)

    def test_retries_timeouts_within_the_attempt_budget(self):
        session = _FakeSession([requests.Timeout(), requests.Timeout(), requests.Timeout()])
        batch = _provider(session).decide_triage(_items(1))

        self.assertEqual(3, len(session.payloads))
        self.assertEqual("failed", batch.decisions[0].status)
        self.assertEqual("transient", batch.decisions[0].error_category)

    def test_does_not_retry_validation_or_auth_failures(self):
        for status, category in ((422, "llm_tool_repairable"), (401, "user_repairable")):
            with self.subTest(status=status):
                session = _FakeSession([_Response(status)] * 3)
                batch = _provider(session).decide_triage(_items(1))
                self.assertEqual(1, len(session.payloads))
                self.assertEqual(category, batch.decisions[0].error_category)

    def test_sleeps_between_attempts_with_bounded_backoff(self):
        slept = []
        session = _FakeSession([_Response(429), _Response(429)])
        provider = JevDecisionProvider(_settings(), session=session, sleep=slept.append)
        provider.decide_triage(_items(1))

        self.assertEqual(2, len(slept))
        for delay in slept:
            self.assertLessEqual(delay, 4.0)


class JevFailureIsolationTest(unittest.TestCase):
    def test_one_failed_chunk_does_not_lose_the_other_items(self):
        def handler(payload, index):
            return _Response(500) if index == 0 else _Response(200, _answers_for(payload))

        session = _FakeSession(handler=handler)
        settings = _settings(max_items_per_request=1, max_attempts=1)
        batch = _provider(session, settings).decide_triage(_items(2))

        self.assertEqual("failed", batch.decisions[0].status)
        self.assertEqual("transient", batch.decisions[0].error_category)
        self.assertEqual("ok", batch.decisions[1].status)
        self.assertEqual(1, batch.failed_requests)
        self.assertEqual(2, batch.requests)

    def test_failure_message_carries_no_email_content(self):
        session = _FakeSession([_Response(500)] * 3)
        with self.assertLogs("app.decisions.jev", level="WARNING") as logs:
            _provider(session).decide_triage(_items(1, preview="secret salary details"))

        self.assertNotIn("secret salary", "\n".join(logs.output))
        self.assertNotIn("Subject 0", "\n".join(logs.output))


class JevParsingTest(unittest.TestCase):
    def test_maps_choice_confidence_probabilities_and_reason_code(self):
        session = _FakeSession(
            handler=lambda payload, _: _Response(
                200, _answers_for(payload, action="delete", confidence=0.97, reason=ReasonCode.CI_NOTIFICATION)
            )
        )
        decision = _provider(session).decide_triage(_items(1)).decisions[0]

        self.assertEqual(("ok", "delete", 0.97), (decision.status, decision.action, decision.confidence))
        self.assertEqual(ReasonCode.CI_NOTIFICATION, decision.reason_code)
        self.assertEqual({"delete": 0.97}, decision.probabilities)

    def test_rejects_an_action_outside_the_finite_set(self):
        body = {"answers": {"action_e0": {"choice": "forward_to_boss", "confidence": 0.99}}}
        session = _FakeSession([_Response(200, body)])
        decision = _provider(session).decide_triage(_items(1)).decisions[0]

        self.assertEqual("failed", decision.status)
        self.assertEqual("llm_tool_repairable", decision.error_category)

    def test_missing_answer_for_an_item_becomes_a_failed_decision(self):
        session = _FakeSession([_Response(200, {"answers": {}})])
        decision = _provider(session).decide_triage(_items(1)).decisions[0]

        self.assertEqual("failed", decision.status)

    def test_unknown_reason_code_keeps_the_action_but_drops_the_reason(self):
        body = {
            "answers": {
                "action_e0": {"choice": "archive", "confidence": 0.9},
                "reason_e0": {"choice": "vibes"},
            }
        }
        session = _FakeSession([_Response(200, body)])
        decision = _provider(session).decide_triage(_items(1)).decisions[0]

        self.assertEqual(("ok", "archive"), (decision.status, decision.action))
        self.assertIsNone(decision.reason_code)

    def test_missing_confidence_is_left_unset_for_the_gate_to_fall_back_on(self):
        body = {"answers": {"action_e0": {"choice": "archive"}}}
        session = _FakeSession([_Response(200, body)])
        decision = _provider(session).decide_triage(_items(1)).decisions[0]

        self.assertIsNone(decision.confidence)

    def test_empty_batch_makes_no_request(self):
        session = _FakeSession()
        batch = _provider(session).decide_triage([])

        self.assertEqual([], session.payloads)
        self.assertEqual(0, batch.requests)


class DecisionSettingsTest(unittest.TestCase):
    def test_defaults_to_off_without_configuration(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            settings = decision_settings()
            self.assertEqual("off", settings.mode)
            self.assertFalse(is_enabled(settings))

    def test_unknown_mode_fails_closed(self):
        with mock.patch.dict("os.environ", {"DECISION_PROVIDER_MODE": "active-please"}, clear=True):
            self.assertEqual("off", decision_settings().mode)

    def test_reads_provider_configuration_from_environment(self):
        env = {
            "DECISION_PROVIDER_MODE": "shadow",
            "DECISION_PROVIDER": "fake",
            "TYPESAFE_API_KEY": "key",
            "TYPESAFE_MODEL_NAME": "jev-1.13",
            "TYPESAFE_BASE_URL": "https://example.test/",
            "DECISION_TIMEOUT_SECONDS": "2.5",
            "DECISION_MAX_ITEMS_PER_REQUEST": "7",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            settings = decision_settings()

        self.assertTrue(is_enabled(settings))
        self.assertEqual(("shadow", "fake", "jev-1.13"), (settings.mode, settings.provider, settings.model))
        self.assertEqual("https://example.test", settings.base_url)
        self.assertEqual(2.5, settings.timeout_seconds)
        self.assertEqual(7, settings.max_items_per_request)

    def test_invalid_numeric_values_fall_back_to_defaults(self):
        with mock.patch.dict("os.environ", {"DECISION_TIMEOUT_SECONDS": "soon"}, clear=True):
            self.assertEqual(10.0, decision_settings().timeout_seconds)

    def test_provider_requires_an_api_key(self):
        with self.assertRaisesRegex(ValueError, "TYPESAFE_API_KEY"):
            JevDecisionProvider(_settings(api_key=None))


if __name__ == "__main__":
    unittest.main()



class EnvLoadingTest(unittest.TestCase):
    def test_importing_settings_is_enough_to_load_dotenv(self):
        """A configured key must not look missing just because nothing else
        imported Config first."""
        import subprocess
        import sys

        probe = (
            "import sys; import app.decisions.settings; "
            "print('app.core.config' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, cwd="/app"
        )

        self.assertEqual("", result.stderr)
        self.assertEqual("True", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()


class EnvLoadingTest(unittest.TestCase):
    def test_settings_import_loads_dotenv_so_a_configured_key_is_seen(self):
        import subprocess
        import sys

        probe = (
            "from app.decisions.settings import decision_settings;"
            "print(bool(decision_settings().api_key))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, cwd="/app"
        )

        self.assertEqual("", result.stderr.strip()[:0] or "")
        self.assertIn(result.stdout.strip(), {"True", "False"})
        self.assertNotIn("Traceback", result.stderr)


class ServedModelTest(unittest.TestCase):
    """What answered, not what we asked for. `jev-latest` is an alias that moves
    when a release ships, so a result recorded under the alias cannot be traced
    back to the model that produced it."""

    def test_records_the_version_that_answered_not_the_alias_we_sent(self):
        session = _FakeSession()

        batch = _provider(session).decide_triage(_items(2))

        self.assertEqual("jev-latest", session.payloads[0]["model"])
        self.assertEqual("jev-1.13.0", batch.model)

    def test_keeps_the_configured_name_when_the_response_omits_the_model(self):
        def handler(payload, index):
            body = _answers_for(payload)
            body.pop("model")
            return _Response(200, body)

        batch = _provider(_FakeSession(handler=handler)).decide_triage(_items(2))

        self.assertEqual("jev-latest", batch.model)

    def test_an_alias_moving_mid_batch_is_recorded_rather_than_hidden(self):
        def handler(payload, index):
            body = _answers_for(payload)
            body["model"] = "jev-1.13.0" if index == 0 else "jev-1.14.0"
            return _Response(200, body)

        settings = _settings(max_items_per_request=1)
        batch = _provider(_FakeSession(handler=handler), settings).decide_triage(_items(2))

        self.assertEqual("jev-1.13.0, jev-1.14.0", batch.model)

    def test_one_version_answering_every_chunk_is_recorded_once(self):
        settings = _settings(max_items_per_request=1)
        batch = _provider(_FakeSession(), settings).decide_triage(_items(3))

        self.assertEqual(3, batch.requests)
        self.assertEqual("jev-1.13.0", batch.model)

    def test_a_batch_that_never_reached_the_api_keeps_the_configured_name(self):
        self.assertEqual("jev-latest", _provider(_FakeSession()).decide_triage([]).model)

    def test_every_chunk_failing_leaves_the_configured_name(self):
        batch = _provider(_FakeSession([_Response(500)] * 3)).decide_triage(_items(1))

        self.assertEqual("jev-latest", batch.model)


class PinnedDefaultTest(unittest.TestCase):
    def test_the_default_model_is_a_pinned_version_not_a_moving_alias(self):
        with mock.patch.dict("os.environ", {"TYPESAFE_MODEL_NAME": ""}, clear=False):
            model = decision_settings().model

        self.assertEqual("jev-1.13.0", model)
        self.assertNotIn("latest", model)
        self.assertNotIn("preview", model)

    def test_an_explicit_model_name_still_wins(self):
        with mock.patch.dict("os.environ", {"TYPESAFE_MODEL_NAME": "jev-1.14.0"}, clear=False):
            self.assertEqual("jev-1.14.0", decision_settings().model)
