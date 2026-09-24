"""Regression tests for the larger five-question LLM response budget."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from knowledge_base.rag.chains import (
    TEST_QUESTIONS_MAX_TOKENS,
    generate_test_questions,
)


class QuestionBudgetTests(SimpleTestCase):
    def test_five_questions_use_dedicated_output_budget(self):
        question = {
            "question_text": "What is the best first step?",
            "options": ["Pause", "Rush", "Avoid", "Guess"],
            "correct_option": 0,
            "explanation": "Pausing gives time to choose a response.",
        }
        response = {
            "questions": [question.copy() for _ in range(5)],
            "test_difficulty": "beginner",
        }
        retriever = MagicMock()
        retriever.search_with_context.return_value = []

        with patch(
            "knowledge_base.rag.chains.chat_completion",
            return_value={"content": json.dumps(response)},
        ) as completion:
            result = generate_test_questions(retriever=retriever, skill="STOP")

        self.assertEqual(len(result.questions), 5)
        self.assertEqual(TEST_QUESTIONS_MAX_TOKENS, 8192)
        self.assertEqual(completion.call_args.kwargs["max_tokens"], 8192)
