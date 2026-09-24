import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import api
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.context import EmailContextError, MAX_CONTEXT_CHARS
from app.agent.graph import SYSTEM_PROMPT, build_agent
from app.agent.stream import new_turn_input, stream_agent
from app.services.database import DatabaseService


class _CapturingLLM:
    def __init__(self, content="completed"):
        self.calls = []
        self.content = content

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content=self.content)


class _ScriptedEventAgent:
    def __init__(self, events):
        self.events = events

    async def astream_events(self, graph_input, config, version):
        for event in self.events:
            yield event

    def get_state(self, config):
        return SimpleNamespace(tasks=[])


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

    def test_loaded_context_without_a_citation_emits_trace_only(self):
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
        self.assertNotIn("references", [event.get("type") for event in events])
        self.assertNotIn("private fixture body", json.dumps(trace))

    def test_final_answer_emits_only_claimed_context_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            for index in (1, 2):
                database.insert_email({
                    "id": f"email-{index}",
                    "subject": f"Project update {index}",
                    "sender_name": "Marcus Patel",
                    "sender_email": "marcus@example.com",
                    "received_datetime": index,
                    "body_content": f"private fixture body {index}",
                })
            agent = build_agent(
                llm=_CapturingLLM(
                    "The second update changed.<!--feedflux_refs:context-2-->"
                )
            )
            thread_id = "stream-cited-context-thread"

            async def exercise():
                events = [
                    event
                    async for event in stream_agent(
                        new_turn_input("What changed?", ["email-1", "email-2"]),
                        thread_id,
                        agent=agent,
                    )
                ]
                state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
                return events, state

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                events, state = asyncio.run(exercise())
            database.engine.dispose()

        references = [event for event in events if event.get("type") == "references"]
        self.assertEqual(
            [{
                "type": "references",
                "references": [{
                    "email_id": "email-2",
                    "subject": "Project update 2",
                    "sender": "Marcus Patel",
                }],
            }],
            references,
        )
        self.assertNotIn("feedflux_refs", json.dumps(events))
        self.assertEqual("The second update changed.", state.values["messages"][-1].content)

    def test_unknown_context_reference_key_is_removed_and_ignored(self):
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
            agent = build_agent(
                llm=_CapturingLLM("No citation.<!--feedflux_refs:context-99-->")
            )
            thread_id = "stream-invalid-context-reference-thread"

            async def exercise():
                events = [
                    event
                    async for event in stream_agent(
                        new_turn_input("Show unread mail", ["email-1"]),
                        thread_id,
                        agent=agent,
                    )
                ]
                state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
                return events, state

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                events, state = asyncio.run(exercise())
            database.engine.dispose()

        self.assertNotIn("references", [event.get("type") for event in events])
        self.assertNotIn("feedflux_refs", json.dumps(events))
        self.assertEqual("No citation.", state.values["messages"][-1].content)

    def test_reference_footer_is_hidden_across_stream_chunks(self):
        reference = {
            "email_id": "email-1",
            "subject": "Project update",
            "sender": "Marcus Patel",
        }
        agent = _ScriptedEventAgent([
            {
                "event": "on_custom_event",
                "name": "email_context_loaded",
                "data": {
                    "context_email_ids": ["email-1"],
                    "context_chars": 100,
                    "context_char_limit": MAX_CONTEXT_CHARS,
                    "references": [reference],
                },
            },
            {
                "event": "on_chat_model_stream",
                "name": "fixture-model",
                "data": {"chunk": SimpleNamespace(content="Answer<!--feed")},
            },
            {
                "event": "on_chat_model_stream",
                "name": "fixture-model",
                "data": {"chunk": SimpleNamespace(
                    content="flux_refs:context-1-->"
                )},
            },
            {
                "event": "on_chat_model_end",
                "name": "fixture-model",
                "data": {"output": AIMessage(content="Answer")},
            },
            {
                "event": "on_custom_event",
                "name": "email_context_references",
                "data": {"references": [reference]},
            },
        ])

        events = asyncio.run(self._collect_stream(agent))

        self.assertEqual(
            "Answer",
            "".join(
                event["content"] for event in events if event.get("type") == "token"
            ),
        )
        self.assertNotIn("feedflux_refs", json.dumps(events))
        reference_index = next(
            index for index, event in enumerate(events) if event.get("type") == "references"
        )
        token_indices = [
            index for index, event in enumerate(events) if event.get("type") == "token"
        ]
        self.assertGreater(reference_index, max(token_indices))

    @staticmethod
    async def _collect_stream(agent):
        return [
            event
            async for event in stream_agent(
                new_turn_input("Question", ["email-1"]),
                "scripted-reference-thread",
                agent=agent,
            )
        ]

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
