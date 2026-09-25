import json
import unittest


class MemoryEvalTest(unittest.TestCase):
    def test_deterministic_memory_eval_reports_required_metrics(self):
        try:
            from app.evals.memory import run_memory_eval
        except ModuleNotFoundError:
            self.fail("deterministic memory eval is not implemented")

        result = run_memory_eval()
        metrics = result["metrics"]

        self.assertEqual(1, result["schema_version"])
        self.assertEqual("feedflux-semantic-memory-v1", result["suite_id"])
        self.assertEqual("deterministic", result["provider"])
        self.assertEqual(
            {
                "candidate_duplicate_merge_correctness",
                "candidate_precision",
                "conflict_correctness",
                "cross_profile_leakage",
                "false_promotion",
                "forget_correctness",
                "injection_token_overhead",
                "preference_application_accuracy",
                "relevant_retrieval_precision",
            },
            set(metrics),
        )

        self.assertEqual(1.0, metrics["preference_application_accuracy"]["rate"])
        self.assertEqual(1.0, metrics["relevant_retrieval_precision"]["rate"])
        self.assertEqual(1.0, metrics["conflict_correctness"]["rate"])
        self.assertEqual(1.0, metrics["forget_correctness"]["rate"])
        self.assertEqual(0, metrics["cross_profile_leakage"]["leaked"])
        self.assertEqual(0.0, metrics["cross_profile_leakage"]["rate"])
        self.assertEqual(1.0, metrics["candidate_precision"]["rate"])
        self.assertEqual(
            1.0,
            metrics["candidate_duplicate_merge_correctness"]["rate"],
        )
        self.assertEqual(0, metrics["false_promotion"]["count"])
        self.assertEqual(0.0, metrics["false_promotion"]["rate"])

        token_metric = metrics["injection_token_overhead"]
        self.assertEqual(3, token_metric["samples"])
        self.assertGreater(token_metric["mean_estimated_tokens"], 0)
        self.assertGreaterEqual(
            token_metric["max_estimated_tokens"],
            token_metric["mean_estimated_tokens"],
        )

        serialized = json.dumps(result)
        self.assertNotIn("Private profile preference", serialized)
        self.assertNotIn("Never use exclamation marks", serialized)
        self.assertNotIn("pat@example.com", serialized)


if __name__ == "__main__":
    unittest.main()
