"""Deterministic responses for product identity and basic usage questions."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalHelpResponse:
    intent: str
    text: str


_IDENTITY_QUERIES = {
    "你是谁",
    "你是什么",
    "你叫什么",
    "你叫什么名字",
    "介绍一下你自己",
    "自我介绍",
    "你是干什么的",
    "你是谁开发的",
}

_CAPABILITY_QUERIES = {
    "你能做什么",
    "你能干什么",
    "你会什么",
    "你有什么功能",
    "有什么功能",
    "怎么用",
    "如何使用",
    "使用帮助",
    "帮助",
    "help",
}

_GREETING_QUERIES = {
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "hello",
    "hi",
}


def local_help_response(
    query: str,
    *,
    has_image: bool = False,
) -> LocalHelpResponse | None:
    """Return local product help only for an explicit, narrowly matched query."""

    if has_image:
        return None
    normalized = _normalize(query)
    if normalized in _IDENTITY_QUERIES:
        return LocalHelpResponse(
            intent="identity",
            text=(
                "我是 MedScholar，陪你学习医学的智能伙伴。你不必先把问题整理得很完整："
                "一道拿不准的题、一处没听明白的概念，甚至一句‘这里我还是不懂’，都可以成为我们讨论的起点。"
                "我会先和你一起理清问题，优先回到你提供的教材与课件寻找依据，再用尽量易懂的方式解释；"
                "如果现有资料还不能支持结论，我也会把不确定之处讲清楚。"
                "你可以随时让我换一种讲法、举个例子，或沿着这个知识点找视频和论文继续探索。"
            ),
        )
    if normalized in _CAPABILITY_QUERIES:
        return LocalHelpResponse(
            intent="capabilities",
            text=(
                "我可以帮助你：\n"
                "1. 解答基础医学与临床课程题目，并尽量引用教材或课程课件；\n"
                "2. 识别题目截图，梳理题干后再分析；\n"
                "3. 用快速讲解、引导学习、苏格拉底提问或科研拓展模式回答；\n"
                "4. 检索相关论文和官方医学证据，作为课程学习后的延伸；\n"
                "5. 将经过审核的纠错经验保存为 Skill。\n\n"
                "你可以直接输入具体题目，也可以添加题目截图。"
            ),
        )
    if normalized in _GREETING_QUERIES:
        return LocalHelpResponse(
            intent="greeting",
            text=(
                "你好，很高兴和你一起学习。我是 MedScholar。"
                "医学知识有时像一张交织的地图，第一次没看清某条路也很正常。"
                "你可以从刚上完课仍模糊的地方说起，也可以发来一道题、课件或截图；"
                "我们先找到你真正卡住的那一步，再把它一点点讲明白。"
                "如果你想换种说法、看一个例子，或者继续找视频与论文拓展，也可以随时告诉我。"
                "今天想从哪里开始？"
            ),
        )
    return None


def _normalize(query: str) -> str:
    return re.sub(r"[\s，。！？!?、,.;；:：]+", "", str(query)).lower()
