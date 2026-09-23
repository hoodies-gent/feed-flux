import asyncio
import unittest
from unittest.mock import patch

import api
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.context import EmailContextError
from app.agent.graph import SYSTEM_PROMPT, build_agent
from app.agent.stream import new_turn_input


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
        self.assertIn("Pinned email context", model_messages[0].content)
        self.assertIn("private fixture body", model_messages[0].content)
        self.assertEqual("What changed?", model_messages[1].content)
        self.assertEqual(["email-1"], state.values["context_email_ids"])
        self.assertNotIn("private fixture body", repr(result))
        self.assertNotIn("private fixture body", repr(state.values))

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


if __name__ == "__main__":
    unittest.main()
