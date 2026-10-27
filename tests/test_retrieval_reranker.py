import unittest

from retrieval.retrieval_reranker import RetrievalReranker
from retrieval.structure_retriever import RetrievalHit, RetrievalResult


class FakeClient:
    def complete_json(self, instruction, payload):
        return {
            "ranking": [
                {"candidate_id": "c0", "coverage": 0.2, "noise": True},
                {"candidate_id": "c1", "coverage": 0.55, "noise": False},
                {"candidate_id": "c2", "coverage": 0.95, "noise": False},
            ]
        }


def hit(page, score):
    return RetrievalHit(
        "doc", "教材", "textbook", page, None, f"PDF第{page}页", None,
        score, [], f"片段{page}", [page], f"正文{page}"
    )


class RetrievalRerankerTests(unittest.TestCase):
    def test_filters_noise_and_prioritizes_question_coverage(self):
        original = RetrievalResult("问题", "global", ["doc"], [
            hit(1, 10.0), hit(2, 8.0), hit(3, 4.0)
        ])
        reranked = RetrievalReranker(FakeClient()).rerank("问题", original)
        self.assertEqual([item.pdf_page for item in reranked.hits], [3, 2])
        self.assertIn("reranked", reranked.route_mode)


if __name__ == "__main__":
    unittest.main()
