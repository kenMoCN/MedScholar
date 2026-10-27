import unittest
import urllib.parse

from evidence.video_learning import VideoLearningRetriever


class FakeFetcher:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url, *, timeout_seconds=8.0):
        self.urls.append(url)
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


class FakeJsonClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, system_instruction, payload):
        self.calls.append((system_instruction, payload))
        return self.response


class VideoLearningTests(unittest.TestCase):
    def test_filters_bilibili_video_results_and_keeps_traceable_url(self):
        rss = """<?xml version="1.0"?><rss><channel>
        <item><title>动作电位的产生机制</title>
        <link>https://www.bilibili.com/video/BV123/?from=search</link>
        <description>用动画解释动作电位和离子通道</description></item>
        <item><title>无关网页</title><link>https://example.com/a</link></item>
        </channel></rss>"""
        retriever = VideoLearningRetriever(FakeFetcher(rss))

        resources = retriever.search("动作电位")

        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].platform, "Bilibili")
        self.assertEqual(resources[0].resource_kind, "video_candidate")
        self.assertEqual(resources[0].url, "https://www.bilibili.com/video/BV123/")

    def test_reads_specific_videos_from_bilibili_structured_search(self):
        payload = """{
          "code": 0,
          "data": {"result": [{
            "bvid": "BV1iA411N74m",
            "title": "动作电位的去极化与复极化",
            "description": "动画讲解钾离子外流和复极化过程",
            "author": "生理学课堂",
            "duration": "03:25"
          }]}
        }"""
        retriever = VideoLearningRetriever(FakeFetcher(payload))

        resources = retriever.search("动作电位复极化")

        self.assertEqual(len(resources), 1)
        self.assertEqual(
            resources[0].url,
            "https://www.bilibili.com/video/BV1iA411N74m/",
        )
        self.assertIn("UP主", resources[0].description)
        self.assertIn("03:25", resources[0].description)

    def test_network_failure_falls_back_to_honest_platform_search(self):
        retriever = VideoLearningRetriever(FakeFetcher(TimeoutError()))

        resources = retriever.search("心动周期")

        self.assertEqual(resources[0].resource_kind, "platform_search")
        self.assertIn("并非已精选", resources[0].description)
        self.assertIn("search.bilibili.com", resources[0].url)

    def test_model_extracts_search_keywords_instead_of_using_whole_question(self):
        fetcher = FakeFetcher(TimeoutError())
        planner = FakeJsonClient({
            "concept": "动作电位复极化机制",
            "search_queries": ["动作电位 复极化 钾通道 动画", "动作电位 离子机制 图解"],
            "selection_focus": "看清钾通道开放与膜电位恢复的关系",
        })
        retriever = VideoLearningRetriever(fetcher, planner=planner)

        resources = retriever.search("我还是不懂动作电位为什么会复极化，可以找视频吗？")

        searched = " ".join(urllib.parse.unquote(url) for url in fetcher.urls)
        self.assertIn("动作电位 复极化 钾通道 动画", searched)
        self.assertNotIn("我还是不懂", searched)
        self.assertEqual(len(resources), 2)
        self.assertEqual(resources[0].search_keyword, "动作电位 复极化 钾通道 动画")

    def test_model_selects_specific_candidate_and_adds_recommendation_reason(self):
        rss = """<?xml version="1.0"?><rss><channel>
        <item><title>动作电位复极化动画详解</title>
        <link>https://www.bilibili.com/video/BVGOOD/</link>
        <description>演示钾通道开放和膜电位恢复</description></item>
        <item><title>动作电位口诀速记</title>
        <link>https://www.bilibili.com/video/BVFAST/</link>
        <description>一分钟背诵口诀</description></item>
        </channel></rss>"""
        planner = FakeJsonClient({
            "concept": "动作电位复极化",
            "search_queries": ["动作电位 复极化 钾通道 动画"],
            "selection_focus": "理解离子机制",
        })
        selector = FakeJsonClient({
            "selected": [{
                "resource_id": "video_1_1",
                "reason": "动画直接展示钾通道开放与复极化过程",
            }]
        })
        retriever = VideoLearningRetriever(
            FakeFetcher(rss), planner=planner, selector=selector
        )

        resources = retriever.search("动作电位为什么会复极化？")

        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].url, "https://www.bilibili.com/video/BVGOOD/")
        self.assertIn("推荐理由", resources[0].description)
        self.assertIn("钾通道", resources[0].recommendation_reason)


if __name__ == "__main__":
    unittest.main()
