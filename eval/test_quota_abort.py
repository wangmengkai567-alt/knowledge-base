import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_eval import is_quota_denied


class QuotaAbortTest(unittest.TestCase):
    def test_embedding_403(self):
        self.assertTrue(
            is_quota_denied(
                'POST /api/v1/knowledge/bases/2/retrieve -> 500 '
                'b\'{"code":500001,"reason":"EMBEDDING_API_ERROR","message":"Embedding API HTTP 403"}\''
            )
        )

    def test_unpurchased(self):
        self.assertTrue(is_quota_denied("AccessDenied.Unpurchased"))

    def test_llm_free_quota_exhausted(self):
        self.assertTrue(
            is_quota_denied(
                'POST /api/v1/knowledge/bases/6/rag/query -> 500 '
                'b\'{"code":500002,"reason":"LLM_ERROR","message":"LLM API returned 403: '
                '{\\"error\\":{\\"message\\":\\"Free quota exhausted.\\"}}"}\''
            )
        )

    def test_structured_run_authorization_error(self):
        self.assertTrue(is_quota_denied("model_failed: model_error (LLM_AUTHORIZATION_FAILED)"))
        self.assertTrue(is_quota_denied("model_failed: model_error (LLM_RATE_LIMITED)"))

    def test_normal_error_not_quota(self):
        self.assertFalse(is_quota_denied("POST /rag/query -> 500 timeout"))


if __name__ == "__main__":
    unittest.main()
