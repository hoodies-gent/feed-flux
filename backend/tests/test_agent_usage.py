import asyncio
import unittest

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.graph import build_agent
from app.agent.stream import new_turn_input, stream_agent


class _UsageReportingLLM(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "usage-reporting-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = AIMessage(
            content="done",
            response_metadata={"model_name": "fixture-model"},
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_token_details": {"cache_read": 40},
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class AgentUsageTest(unittest.TestCase):
    def test_stream_emits_normalized_usage_for_each_model_step(self):
        agent = build_agent(llm=_UsageReportingLLM())

        async def exercise():
            return [
                event
                async for event in stream_agent(
                    new_turn_input("hello"),
                    "usage-thread",
                    agent=agent,
                )
                if event["type"] == "usage"
            ]

        usage_events = asyncio.run(exercise())

        self.assertEqual(
            [
                {
                    "type": "usage",
                    "model": "fixture-model",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 20,
                        "total_tokens": 120,
                    },
                }
            ],
            usage_events,
        )


if __name__ == "__main__":
    unittest.main()
