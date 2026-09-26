import asyncio
import json
import unittest


class _FakeStructuredReviewer:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.schema = None
        self.messages = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.result


class _HangingStructuredReviewer:
    def __init__(self):
        self.cancelled = False

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _target_memory():
    return {
        "id": 7,
        "version": 2,
        "memory_type": "preference",
        "workflow_scope": "drafting",
        "contact_scope": None,
        "key": "Reply tone",
        "value": "Be concise.",
        "status": "active",
    }


class MemoryConsentReviewTest(unittest.TestCase):
    def test_reviewer_receives_arbitrary_language_without_language_detection(self):
        from app.agent.memory_consent import review_memory_mutation

        messages = (
            "今後の返信では、不要なダッシュを使わないことを覚えてください。",
            "Recuerda que prefiero respuestas breves.",
            "تذكّر أنني أفضل الردود الرسمية.",
        )

        for user_message in messages:
            reviewer = _FakeStructuredReviewer(
                {
                    "decision": "allow",
                    "reason_code": "explicit_user_request",
                }
            )
            with self.subTest(user_message=user_message):
                result = asyncio.run(
                    review_memory_mutation(
                        latest_user_message=user_message,
                        tool_name="remember_memory",
                        tool_args={
                            "memory_type": "preference",
                            "workflow_scope": "drafting",
                            "key": "reply_style",
                            "value": "Preserve the user's stated preference.",
                        },
                        reviewer=reviewer,
                    )
                )

                self.assertEqual("allow", result.decision)
                payload = json.loads(reviewer.messages[-1].content)
                self.assertEqual(user_message, payload["latest_user_message"])
                self.assertEqual("remember_memory", payload["proposed_operation"])

    def test_valid_structured_decisions_are_returned(self):
        from app.agent.memory_consent import review_memory_mutation

        cases = (
            ("allow", "explicit_user_request"),
            ("ask", "ambiguous_user_intent"),
            ("deny", "inferred_behavior"),
            ("deny", "unrelated_request"),
            ("deny", "argument_mismatch"),
        )

        for decision, reason_code in cases:
            reviewer = _FakeStructuredReviewer(
                {"decision": decision, "reason_code": reason_code}
            )
            with self.subTest(decision=decision, reason_code=reason_code):
                result = asyncio.run(
                    review_memory_mutation(
                        latest_user_message="A user-authored message.",
                        tool_name="update_memory",
                        tool_args={"memory_id": 7, "value": "New value"},
                        target_memory=_target_memory(),
                        reviewer=reviewer,
                    )
                )

                self.assertEqual(decision, result.decision)
                self.assertEqual(reason_code, result.reason_code)

    def test_update_review_includes_owned_target_and_missing_target_is_denied(self):
        from app.agent.memory_consent import review_memory_mutation

        reviewer = _FakeStructuredReviewer(
            {"decision": "allow", "reason_code": "explicit_user_request"}
        )
        allowed = asyncio.run(
            review_memory_mutation(
                latest_user_message="Update my Reply tone memory to be warmer.",
                tool_name="update_memory",
                tool_args={"memory_id": 7, "value": "Be warm."},
                target_memory=_target_memory(),
                reviewer=reviewer,
            )
        )
        unavailable_reviewer = _FakeStructuredReviewer(
            {"decision": "allow", "reason_code": "explicit_user_request"}
        )
        unavailable = asyncio.run(
            review_memory_mutation(
                latest_user_message="Update memory 99.",
                tool_name="update_memory",
                tool_args={"memory_id": 99, "value": "Be warm."},
                reviewer=unavailable_reviewer,
            )
        )

        payload = json.loads(reviewer.messages[-1].content)
        self.assertEqual("allow", allowed.decision)
        self.assertEqual("Reply tone", payload["current_target"]["key"])
        self.assertEqual("Be concise.", payload["current_target"]["value"])
        self.assertEqual(
            ("deny", "target_unavailable"),
            (unavailable.decision, unavailable.reason_code),
        )
        self.assertIsNone(unavailable_reviewer.messages)

    def test_oversized_target_requires_approval_without_calling_reviewer(self):
        from app.agent.memory_consent import review_memory_mutation

        reviewer = _FakeStructuredReviewer(
            {"decision": "allow", "reason_code": "explicit_user_request"}
        )
        target = {**_target_memory(), "value": "x" * 2001}

        result = asyncio.run(
            review_memory_mutation(
                latest_user_message="Update my Reply tone memory.",
                tool_name="update_memory",
                tool_args={"memory_id": 7, "value": "Be warm."},
                target_memory=target,
                reviewer=reviewer,
            )
        )

        self.assertEqual(("ask", "target_too_large"), (result.decision, result.reason_code))
        self.assertIsNone(reviewer.messages)

    def test_inconsistent_or_malformed_reviewer_output_fails_closed(self):
        from app.agent.memory_consent import review_memory_mutation

        outputs = (
            {
                "decision": "allow",
                "reason_code": "inferred_behavior",
            },
            {
                "decision": "allow",
            },
            {
                "decision": "unexpected",
                "reason_code": "explicit_user_request",
            },
        )

        for output in outputs:
            with self.subTest(output=output):
                result = asyncio.run(
                    review_memory_mutation(
                        latest_user_message="Remember this preference.",
                        tool_name="remember_memory",
                        tool_args={"value": "Private preference"},
                        reviewer=_FakeStructuredReviewer(output),
                    )
                )

                self.assertEqual("ask", result.decision)
                self.assertEqual("reviewer_invalid", result.reason_code)

    def test_reviewer_failure_fails_closed(self):
        from app.agent.memory_consent import review_memory_mutation

        result = asyncio.run(
            review_memory_mutation(
                latest_user_message="Remember this preference.",
                tool_name="remember_memory",
                tool_args={"value": "Private preference"},
                reviewer=_FakeStructuredReviewer(error=RuntimeError("offline")),
            )
        )

        self.assertEqual("ask", result.decision)
        self.assertEqual("reviewer_unavailable", result.reason_code)

    def test_reviewer_timeout_fails_closed(self):
        from app.agent.memory_consent import review_memory_mutation

        reviewer = _HangingStructuredReviewer()
        result = asyncio.run(
            review_memory_mutation(
                latest_user_message="Remember this preference.",
                tool_name="remember_memory",
                tool_args={"value": "Private preference"},
                reviewer=reviewer,
                timeout_seconds=0.01,
            )
        )

        self.assertEqual("ask", result.decision)
        self.assertEqual("reviewer_unavailable", result.reason_code)
        self.assertTrue(reviewer.cancelled)

    def test_external_cancellation_is_not_converted_to_approval(self):
        from app.agent.memory_consent import review_memory_mutation

        reviewer = _HangingStructuredReviewer()

        async def cancel_review():
            task = asyncio.create_task(
                review_memory_mutation(
                    latest_user_message="Remember this preference.",
                    tool_name="remember_memory",
                    tool_args={"value": "Private preference"},
                    reviewer=reviewer,
                    timeout_seconds=60,
                )
            )
            await asyncio.sleep(0)
            task.cancel()
            await task

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(cancel_review())
        self.assertTrue(reviewer.cancelled)

    def test_reset_and_missing_input_require_approval_without_calling_reviewer(self):
        from app.agent.memory_consent import review_memory_mutation

        reset_reviewer = _FakeStructuredReviewer(
            {"decision": "allow", "reason_code": "explicit_user_request"}
        )
        reset = asyncio.run(
            review_memory_mutation(
                latest_user_message="Clear every memory.",
                tool_name="reset_memories",
                tool_args={},
                reviewer=reset_reviewer,
            )
        )
        missing_reviewer = _FakeStructuredReviewer(
            {"decision": "allow", "reason_code": "explicit_user_request"}
        )
        missing = asyncio.run(
            review_memory_mutation(
                latest_user_message="   ",
                tool_name="remember_memory",
                tool_args={"value": "Private preference"},
                reviewer=missing_reviewer,
            )
        )

        self.assertEqual(("ask", "bulk_destructive"), (reset.decision, reset.reason_code))
        self.assertEqual(
            ("ask", "missing_user_message"),
            (missing.decision, missing.reason_code),
        )
        self.assertIsNone(reset_reviewer.messages)
        self.assertIsNone(missing_reviewer.messages)

    def test_non_mutation_tools_are_outside_the_reviewer_contract(self):
        from app.agent.memory_consent import review_memory_mutation

        reviewer = _FakeStructuredReviewer(
            {"decision": "allow", "reason_code": "explicit_user_request"}
        )

        result = asyncio.run(
            review_memory_mutation(
                latest_user_message="List my memories.",
                tool_name="list_memories",
                tool_args={},
                reviewer=reviewer,
            )
        )

        self.assertEqual("deny", result.decision)
        self.assertEqual("unsupported_operation", result.reason_code)
        self.assertIsNone(reviewer.messages)


if __name__ == "__main__":
    unittest.main()
