import json
import unittest

from langchain_core.messages import AIMessage

from app.decisions.contract import ReasonCode, TriageItem
from app.decisions.llm import LlmDecisionProvider, _LlmBatchDecision
from app.decisions.settings import DecisionSettings


def _settings(**overrides) -> DecisionSettings:
    return DecisionSettings(**{"mode": "shadow", "provider": "llm", "max_items_per_request": 20, **overrides})


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


def _raw(input_tokens=120, output_tokens=30, model="deepseek-chat") -> AIMessage:
    return AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"model_name": model},
    )


class _FakeModel:
    def __init__(self, results=None, error=None):
        self.results = list(results or [])
        self.error = error
        self.calls = []
        self.structured_schema = None

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.include_raw = include_raw
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        if self.results:
            return self.results.pop(0)
        return _ok_result(len(json.loads(messages[1].content)["emails"]))


def _ok_result(count, action="archive", confidence=0.8, reason=ReasonCode.NEWSLETTER):
    parsed = _LlmBatchDecision(
        decisions=[
            {
                "ref": f"e{index}",
                "action": action,
                "reason_code": reason,
                "confidence": confidence,
                "important": False,
                "importance_confidence": 0.7,
            }
            for index in range(count)
        ]
    )
    return {"raw": _raw(), "parsed": parsed, "parsing_error": None}


class _HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class LlmDecisionProviderTest(unittest.TestCase):
    def test_returns_one_typed_decision_per_item_in_input_order(self):
        model = _FakeModel()
        batch = LlmDecisionProvider(model, settings=_settings()).decide_triage(_items(3))

        self.assertEqual(["dev-000", "dev-001", "dev-002"], [d.item_id for d in batch.decisions])
        self.assertTrue(all(d.status == "ok" and d.action == "archive" for d in batch.decisions))
        self.assertEqual(ReasonCode.NEWSLETTER, batch.decisions[0].reason_code)
        self.assertEqual(1, batch.requests)

    def test_uses_structured_output_against_the_typed_schema(self):
        model = _FakeModel()
        LlmDecisionProvider(model, settings=_settings()).decide_triage(_items(1))

        self.assertIs(_LlmBatchDecision, model.structured_schema)
        self.assertTrue(model.include_raw)

    def test_sends_the_same_bounded_fields_as_the_other_arms(self):
        model = _FakeModel()
        LlmDecisionProvider(model, settings=_settings(preview_chars=50)).decide_triage(
            _items(1, preview="z" * 800)
        )

        payload = json.loads(model.calls[0][1].content)
        email = payload["emails"][0]
        self.assertEqual({"ref", "subject", "sender", "sender_email", "received", "preview"}, set(email))
        self.assertEqual(50, len(email["preview"]))
        self.assertNotIn("dev-000", json.dumps(payload))

    def test_prompt_lists_the_same_action_and_reason_vocabulary(self):
        model = _FakeModel()
        LlmDecisionProvider(model, settings=_settings()).decide_triage(_items(1))

        prompt = model.calls[0][0].content
        for action in ("mark_read", "archive", "delete", "needs_reply"):
            self.assertIn(action, prompt)
        self.assertIn(str(ReasonCode.CI_NOTIFICATION), prompt)

    def test_records_usage_latency_and_model_name(self):
        model = _FakeModel()
        batch = LlmDecisionProvider(model, settings=_settings()).decide_triage(_items(2))

        self.assertEqual(120, batch.usage.input_tokens)
        self.assertEqual(30, batch.usage.output_tokens)
        self.assertEqual("deepseek-chat", batch.model)
        self.assertGreaterEqual(batch.latency_ms, 0.0)

    def test_chunks_by_item_count_and_aggregates(self):
        model = _FakeModel()
        batch = LlmDecisionProvider(model, settings=_settings(max_items_per_request=2)).decide_triage(_items(5))

        self.assertEqual(3, len(model.calls))
        self.assertEqual(5, len(batch.decisions))
        self.assertEqual(360, batch.usage.input_tokens)

    def test_provider_error_degrades_only_that_chunk(self):
        results = [_HttpError(503), _ok_result(1)]

        class _Model(_FakeModel):
            def invoke(self, messages):
                self.calls.append(messages)
                result = results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

        model = _Model()
        batch = LlmDecisionProvider(model, settings=_settings(max_items_per_request=1)).decide_triage(_items(2))

        self.assertEqual(("failed", "transient"), (batch.decisions[0].status, batch.decisions[0].error_category))
        self.assertEqual("ok", batch.decisions[1].status)
        self.assertEqual(1, batch.failed_requests)

    def test_parsing_error_becomes_repairable_failures(self):
        model = _FakeModel([{"raw": _raw(), "parsed": None, "parsing_error": ValueError("bad")}])
        batch = LlmDecisionProvider(model, settings=_settings()).decide_triage(_items(1))

        self.assertEqual("failed", batch.decisions[0].status)
        self.assertEqual("llm_tool_repairable", batch.decisions[0].error_category)

    def test_unknown_ref_is_dropped_and_missing_item_becomes_failed(self):
        parsed = _LlmBatchDecision(
            decisions=[
                {"ref": "e1", "action": "delete", "reason_code": ReasonCode.CI_NOTIFICATION,
                 "confidence": 0.95, "important": False, "importance_confidence": 0.9},
                {"ref": "e9", "action": "delete", "reason_code": ReasonCode.OTHER,
                 "confidence": 0.9, "important": False, "importance_confidence": 0.9},
            ]
        )
        model = _FakeModel([{"raw": _raw(), "parsed": parsed, "parsing_error": None}])
        batch = LlmDecisionProvider(model, settings=_settings()).decide_triage(_items(2))

        self.assertEqual("failed", batch.decisions[0].status)
        self.assertEqual(("ok", "delete"), (batch.decisions[1].status, batch.decisions[1].action))

    def test_does_not_build_a_model_for_an_empty_batch(self):
        provider = LlmDecisionProvider(settings=_settings())
        batch = provider.decide_triage([])

        self.assertEqual([], batch.decisions)
        self.assertEqual(0, batch.requests)

    def test_failure_log_carries_no_email_content(self):
        model = _FakeModel(error=_HttpError(500))
        with self.assertLogs("app.decisions.llm", level="WARNING") as logs:
            LlmDecisionProvider(model, settings=_settings()).decide_triage(
                _items(1, preview="secret salary details")
            )

        self.assertNotIn("secret salary", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
