import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.llm import get_llm
from app.agent import stream as agent_stream
from app.core.config import Config
from app.services.database import DatabaseService


class _RecordingAgent:
    def __init__(self, output: str = ""):
        self.output = output
        self.config = None

    async def astream_events(self, graph_input, config, version):
        self.config = config
        if self.output:
            yield {
                "event": "on_tool_end",
                "name": "test_tool",
                "data": {"output": self.output},
            }

    def get_state(self, config):
        return SimpleNamespace(tasks=[])


class EvalRuntimeTest(unittest.TestCase):
    def test_glm_uses_openai_compatible_configuration(self):
        client = object()
        with (
            patch.object(Config, "GLM_API_KEY", "glm-secret", create=True),
            patch.object(
                Config,
                "GLM_BASE_URL",
                "https://open.bigmodel.cn/api/paas/v4/",
                create=True,
            ),
            patch.object(Config, "GLM_MODEL_NAME", "glm-4.7", create=True),
            patch.object(Config, "LLM_TEMPERATURE", 0.2, create=True),
            patch("langchain_openai.ChatOpenAI", return_value=client) as chat_openai,
        ):
            result = get_llm("glm")

        self.assertIs(client, result)
        chat_openai.assert_called_once_with(
            api_key="glm-secret",
            base_url="https://open.bigmodel.cn/api/paas/v4/",
            model="glm-4.7",
            temperature=0.2,
        )

    def test_shared_temperature_is_used_when_call_has_no_override(self):
        client = object()
        with (
            patch.object(Config, "DEEPSEEK_API_KEY", "deepseek-secret"),
            patch.object(Config, "LLM_TEMPERATURE", 0.2, create=True),
            patch("langchain_openai.ChatOpenAI", return_value=client) as chat_openai,
        ):
            result = get_llm("deepseek")

        self.assertIs(client, result)
        self.assertEqual(0.2, chat_openai.call_args.kwargs["temperature"])

    def test_database_default_honors_eval_path_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            eval_db = temp_path / "trial" / "eval.db"
            fallback_data = temp_path / "fallback"
            with (
                patch.dict(os.environ, {"FEEDFLUX_DB_PATH": str(eval_db)}),
                patch("app.services.database.DATA_DIR", fallback_data),
            ):
                db = DatabaseService()
                db.engine.dispose()

            self.assertTrue(eval_db.exists())
            self.assertFalse((fallback_data / "emails.db").exists())

    def test_stream_accepts_callbacks_and_can_preserve_full_tool_output(self):
        callback = object()
        output = "x" * 2500
        recording_agent = _RecordingAgent(output)

        async def collect_events():
            with patch.object(agent_stream, "get_agent", return_value=recording_agent):
                return [
                    event
                    async for event in agent_stream.stream_agent(
                        {"messages": []},
                        "eval-thread",
                        callbacks=[callback],
                        tool_output_limit=None,
                    )
                ]

        events = asyncio.run(collect_events())

        self.assertEqual([callback], recording_agent.config["callbacks"])
        tool_event = next(event for event in events if event.get("step") == "tool_end")
        self.assertEqual(output, tool_event["output"])

    def test_stream_can_use_a_trial_specific_agent(self):
        recording_agent = _RecordingAgent("trial output")

        async def collect_events():
            return [
                event
                async for event in agent_stream.stream_agent(
                    {"messages": []},
                    "eval-thread",
                    agent=recording_agent,
                    tool_output_limit=None,
                )
            ]

        events = asyncio.run(collect_events())

        self.assertIsNotNone(recording_agent.config)
        self.assertTrue(any(event.get("output") == "trial output" for event in events))


if __name__ == "__main__":
    unittest.main()
