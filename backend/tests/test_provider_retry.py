import asyncio
import unittest

from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from app.agent.fault_injection import FaultInjectingChatModel, ProviderFault
from app.agent.graph import build_agent
from app.agent.provider_retry import PROVIDER_RETRY_POLICY


class ProviderRetryTest(unittest.TestCase):
    @staticmethod
    def _retry_without_delay():
        return PROVIDER_RETRY_POLICY._replace(
            initial_interval=0,
            max_interval=0,
            jitter=False,
        )

    @staticmethod
    def _invoke(model: FaultInjectingChatModel):
        agent = build_agent(
            llm=model,
            provider_retry_policy=ProviderRetryTest._retry_without_delay(),
        )
        return asyncio.run(
            agent.ainvoke(
                {"messages": [HumanMessage(content="hello")]},
                {"configurable": {"thread_id": "provider-retry-test"}},
            )
        )

    def test_transient_provider_failures_recover_within_attempt_budget(self):
        model = FaultInjectingChatModel(
            [ProviderFault.TIMEOUT, ProviderFault.RATE_LIMIT],
            response="recovered",
        )

        state = self._invoke(model)

        self.assertEqual("recovered", state["messages"][-1].content)
        self.assertEqual(3, model.attempt_count)

    def test_transient_provider_failure_stops_at_attempt_budget(self):
        model = FaultInjectingChatModel(
            [
                ProviderFault.SERVER_ERROR,
                ProviderFault.SERVER_ERROR,
                ProviderFault.SERVER_ERROR,
                ProviderFault.SERVER_ERROR,
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "503"):
            self._invoke(model)

        self.assertEqual(3, model.attempt_count)

    def test_permanent_validation_failure_is_not_retried(self):
        model = FaultInjectingChatModel([ProviderFault.VALIDATION])

        with self.assertRaisesRegex(ValidationError, "fault_value"):
            self._invoke(model)

        self.assertEqual(1, model.attempt_count)


if __name__ == "__main__":
    unittest.main()
