import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.context import EmailContextError, MAX_CONTEXT_CHARS
from app.agent.graph import SYSTEM_PROMPT, build_agent
from app.agent.stream import new_turn_input, stream_agent
from app.services.database import DatabaseService


class _CapturingLLM:
    def __init__(self):
        self.calls = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content="completed")


class AgentContextBridgeTest(unittest.TestCase):
    def test_request_contract_defaults_to_no_context(self):
        request = api.AgentChatRequest(thread_id="thread-1", message="hello")

        self.assertEqual([], request.context_email_ids)
        self.assertEqual([], new_turn_input("hello")["context_email_ids"])

    def test_chat_endpoint_passes_context_ids_into_new_turn_state(self):
        request = api.AgentChatRequest(
            thread_id="thread-1",
            message="What should I do?",
            context_email_ids=["email-1"],
        )

        with patch.object(
            api,
            "new_turn_input",
            return_value={"messages": [HumanMessage(content="sentinel")]},
        ) as build_input:
            asyncio.run(api.agent_chat_stream(request))

        build_input.assert_called_once_with("What should I do?", ["email-1"])

    def test_pinned_context_is_separate_from_question_and_not_checkpointed(self):
        model = _CapturingLLM()
        agent = build_agent(llm=model)
        resolved = {
            "email_ids": ["email-1"],
            "prompt": (
                '<email_context id="email-1">\n'
                "Subject: Project update\n"
                "Content:\nprivate fixture body\n"
                "</email_context>"
            ),
            "references": [
                {
                    "email_id": "email-1",
                    "subject": "Project update",
                    "sender": "Marcus Patel",
                }
            ],
            "context_chars": 112,
        }
        config = {"configurable": {"thread_id": "context-thread"}}

        async def exercise():
            with patch(
                "app.agent.graph.resolve_email_context",
                return_value=resolved,
            ):
                result = await agent.ainvoke(
                    new_turn_input("What changed?", ["email-1"]),
                    config,
                )
                state = await agent.aget_state(config)
            return result, state

        result, state = asyncio.run(exercise())

        model_messages = model.calls[0]
        self.assertIsInstance(model_messages[0], SystemMessage)
        self.assertIn("User question", model_messages[0].content)
        self.assertIn("Focused email context", model_messages[0].content)
        self.assertIn("private fixture body", model_messages[0].content)
        self.assertEqual("What changed?", model_messages[1].content)
        self.assertEqual(["email-1"], state.values["context_email_ids"])
        self.assertNotIn("private fixture body", repr(result))
        self.assertNotIn("private fixture body", repr(state.values))

    def test_focused_context_keeps_unrelated_inbox_tools_available(self):
        model = _CapturingLLM()
        agent = build_agent(llm=model)
        resolved = {
            "email_ids": ["email-1"],
            "prompt": '<email_context id="email-1">fixture context</email_context>',
            "references": [],
            "context_chars": 62,
        }

        async def exercise():
            with patch(
                "app.agent.graph.resolve_email_context",
                return_value=resolved,
            ):
                await agent.ainvoke(
                    new_turn_input("Show my latest unread emails", ["email-1"]),
                    {"configurable": {"thread_id": "unrelated-context-thread"}},
                )

        asyncio.run(exercise())

        system_prompt = model.calls[0][0].content
        self.assertIn("optional supporting evidence", system_prompt)
        self.assertIn("If it is unrelated, ignore it completely", system_prompt)
        self.assertIn("continue with the appropriate inbox tools", system_prompt)

    def test_empty_context_on_new_turn_clears_prior_pinned_context(self):
        model = _CapturingLLM()
        agent = build_agent(llm=model)
        resolved = {
            "email_ids": ["email-1"],
            "prompt": '<email_context id="email-1">fixture context</email_context>',
            "references": [],
            "context_chars": 62,
        }
        config = {"configurable": {"thread_id": "context-clear-thread"}}

        async def exercise():
            with patch(
                "app.agent.graph.resolve_email_context",
                return_value=resolved,
            ):
                await agent.ainvoke(
                    new_turn_input("First", ["email-1"]),
                    config,
                )
                result = await agent.ainvoke(new_turn_input("Second"), config)
                state = await agent.aget_state(config)
            return result, state

        result, state = asyncio.run(exercise())

        self.assertIn("fixture context", model.calls[0][0].content)
        self.assertEqual(SYSTEM_PROMPT, model.calls[1][0].content)
        self.assertEqual([], result["context_email_ids"])
        self.assertEqual([], state.values["context_email_ids"])

    def test_unavailable_context_stops_before_model_and_returns_safe_message(self):
        model = _CapturingLLM()
        agent = build_agent(llm=model)
        error = EmailContextError(
            "A pinned email is unavailable or not accessible in this mailbox."
        )

        async def exercise():
            with patch(
                "app.agent.graph.resolve_email_context",
                side_effect=error,
            ):
                await agent.ainvoke(
                    new_turn_input("Summarize it", ["private-email-id"]),
                    {"configurable": {"thread_id": "missing-context-thread"}},
                )

        with self.assertRaises(EmailContextError):
            asyncio.run(exercise())

        self.assertEqual([], model.calls)
        self.assertEqual(
            {
                "type": "error",
                "error_category": "user_repairable",
                "content": (
                    "A pinned email is unavailable or not accessible in this mailbox."
                ),
            },
            api._agent_error_event(error),
        )

    def test_stream_emits_sanitized_context_trace_and_references(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            database.insert_email({
                "id": "email-1",
                "subject": "Project update",
                "sender_name": "Marcus Patel",
                "sender_email": "marcus@example.com",
                "received_datetime": 1,
                "body_content": "private fixture body",
            })
            agent = build_agent(llm=_CapturingLLM())

            async def exercise():
                return [
                    event
                    async for event in stream_agent(
                        new_turn_input("What changed?", ["email-1"]),
                        "stream-context-thread",
                        agent=agent,
                    )
                ]

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                events = asyncio.run(exercise())
            database.engine.dispose()

        trace = next(
            event
            for event in events
            if event.get("type") == "trace"
            and event.get("step") == "context_loaded"
        )
        references = next(event for event in events if event.get("type") == "references")

        self.assertEqual(
            {
                "type": "trace",
                "step": "context_loaded",
                "context_email_ids": ["email-1"],
                "context_email_count": 1,
                "context_chars": trace["context_chars"],
                "context_char_limit": MAX_CONTEXT_CHARS,
            },
            trace,
        )
        self.assertGreater(trace["context_chars"], 0)
        self.assertEqual(
            {
                "type": "references",
                "references": [
                    {
                        "email_id": "email-1",
                        "subject": "Project update",
                        "sender": "Marcus Patel",
                    }
                ],
            },
            references,
        )
        self.assertNotIn("private fixture body", json.dumps([trace, references]))

    def test_stream_without_context_emits_no_context_events(self):
        agent = build_agent(llm=_CapturingLLM())

        async def exercise():
            return [
                event
                async for event in stream_agent(
                    new_turn_input("Hello"),
                    "stream-no-context-thread",
                    agent=agent,
                )
            ]

        events = asyncio.run(exercise())

        self.assertNotIn("references", [event.get("type") for event in events])
        self.assertFalse(
            any(
                event.get("type") == "trace"
                and event.get("step") == "context_loaded"
                for event in events
            )
        )


if __name__ == "__main__":
    unittest.main()
