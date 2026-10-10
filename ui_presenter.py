"""Pure-Python conversion from agent results to frontend display records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_orchestrator import AgentRunResult, RunStatus


@dataclass(frozen=True)
class PresentedResult:
    title: str
    body: str
    tone: str
    status_text: str
    research_body: str = ""
    resources: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    research_status: str | None = None
    actions: tuple[dict[str, str], ...] = field(default_factory=tuple)


def present_agent_result(result: AgentRunResult) -> PresentedResult:
    if result.status == RunStatus.COMPLETED.value:
        is_partial = bool(
            result.answer is not None
            and result.answer.answerability == "partial"
        )
        body = result.rendered_answer or "答案已完成，但没有可展示的正文。"
        extension = result.research_extension or ""
        suffix = f"\n\n{extension}" if extension else ""
        if suffix and body.endswith(suffix):
            body = body[: -len(suffix)]
        if result.answer_source == "research_extension":
            body = (
                body if result.research_synthesis_status == "error" else
                f"围绕当前知识点找到 {len(result.external_resources)} 篇候选论文。"
                "论文卡片可继续阅读；摘要中的研究结论只有通过独立复核后才会展示。"
                if result.external_resources else
                "这次没有找到足够相关的论文。可以换一个更具体的知识点，或调整年份与研究类型筛选。"
            )
        visible_resources = _visible_resources(result)
        return PresentedResult(
            title=(
                "答案复核"
                if result.answer_source == "answer_challenge"
                else
                "官方指南答复"
                if result.answer_source == "official_external"
                else "题目矛盾分析"
                if result.answer_source == "question_diagnostic"
                else "通用知识讲解"
                if result.answer_source == "general_knowledge"
                else "学习助手"
                if result.answer_source == "local_help"
                else "视频学习建议"
                if result.answer_source == "video_learning"
                else "学习进度"
                if result.answer_source == "learning_feedback"
                else "轻松聊天"
                if result.answer_source == "casual_chat"
                else "论文拓展"
                if result.answer_source == "research_extension"
                else "课程部分回答"
                if is_partial
                else "课程助手"
            ),
            body=body,
            tone="answer",
            status_text=(
                "已重新核查原题和争议选项"
                if result.answer_source == "answer_challenge"
                else
                "官方指南答复已通过逐条独立核验"
                if result.answer_source == "official_external"
                else "已指出题目矛盾，不强行给出唯一选项"
                if result.answer_source == "question_diagnostic"
                else "已通过独立一致性复核，尚未与课程资料核对"
                if result.answer_source == "general_knowledge"
                else "已提供助手介绍与使用帮助"
                if result.answer_source == "local_help"
                else "已准备视频学习资源"
                if result.answer_source == "video_learning"
                else "已记录本次理解反馈"
                if result.answer_source == "learning_feedback"
                else "已直接回复日常聊天"
                if result.answer_source == "casual_chat"
                else "已完成论文检索，未核验内容不作为结论"
                if result.answer_source == "research_extension"
                else "课程答案已核验，科研证据已通过复核"
                if result.research_synthesis_status == "pass"
                else "已回答证据充分的部分，并继续查找缺失依据"
                if is_partial
                else "回答完成并通过课程证据核验"
            ),
            research_body=_research_narrative(extension, bool(result.external_resources)),
            resources=visible_resources,
            research_status=result.research_synthesis_status,
            actions=tuple(result.learning_actions),
        )

    if result.status == RunStatus.CLARIFICATION_REQUIRED.value:
        questions = result.clarification_questions or ["请补充更具体的问题。"]
        return PresentedResult(
            title="需要补充信息",
            body="\n".join(f"• {question}" for question in questions),
            tone="clarification",
            status_text="等待你补充信息",
        )

    if result.status == RunStatus.REVIEW_REQUIRED.value:
        if result.answer_source == "answer_challenge_unresolved":
            return PresentedResult(
                title="答案争议待核实",
                body=(result.rendered_answer or result.error or "本轮复核尚未解决分歧。"),
                tone="warning",
                status_text="原答案与反馈仍有分歧，等待进一步证据",
            )
        body, status_text = _review_explanation(result)
        return PresentedResult(
            title="暂不回答",
            body=body,
            tone="warning",
            status_text=status_text,
            resources=tuple(result.external_resources[:3]),
            research_status=result.fallback_status,
        )

    if result.status == RunStatus.CANCELLED.value:
        return PresentedResult(
            title="操作已取消",
            body=result.error or "本次任务已经取消。",
            tone="warning",
            status_text="操作已取消",
        )

    return PresentedResult(
        title="运行失败",
        body=result.error or "运行过程中发生未知错误。",
        tone="error",
        status_text="运行失败",
    )


def _research_narrative(extension: str, has_cards: bool) -> str:
    if not extension or not has_cards:
        return extension
    return "\n".join(
        line for line in extension.splitlines()
        if not line.lstrip().startswith("- [paper_")
    ).strip()


def _review_explanation(result: AgentRunResult) -> tuple[str, str]:
    """Explain a withheld answer in user language instead of a bare refusal."""

    original_reason = (result.error or "").strip()
    fallback_status = result.fallback_status or ""

    if fallback_status == "candidates_found":
        reason = (
            original_reason
            or "当前课程资料中没有找到能够直接支持结论的相关段落。"
        )
        check = (
            "虽然找到了外部候选资料，但目前只确认了题名、摘要或来源信息，"
            "还没有完成正文内容、具体引文与本题结论的逐项核对。"
        )
        next_step = (
            "可以上传包含这一知识点的教材或课件；也可以让我继续核对候选资料正文。"
        )
        status = "已说明暂不回答的原因，并列出可继续核对的资料"
    elif fallback_status == "disabled":
        reason = (
            original_reason
            or "当前使用“仅课程资料”模式，但课程资料没有覆盖这个问题。"
        )
        check = "按照当前模式，系统不能使用课程资料之外的来源补充答案。"
        next_step = "可以补充教材或课件，或者将证据设置切换为允许外部补证。"
        status = "当前资料模式不足以回答，已说明解决方法"
    elif fallback_status == "no_candidates":
        reason = original_reason or "当前课程资料没有覆盖这个问题。"
        check = "继续检索后也没有找到可以核对正文和出处的可靠资料。"
        next_step = "请补充相关教材、课件或更具体的题干，我再重新检索和核验。"
        status = "没有找到可核验资料，已说明需要补充的内容"
    elif fallback_status == "failed":
        reason = original_reason or "当前课程资料不足，外部检索也没有正常完成。"
        check = "在检索或读取外部资料时发生了问题，因此无法完成必要的核验。"
        next_step = "可以稍后重试，或上传相关教材、课件后改用本地课程资料回答。"
        status = "资料核验未完成，已说明失败原因"
    else:
        reason = original_reason or "生成的答案没有通过事实与前后一致性复核。"
        check = (
            "系统发现仍有关键结论无法确认，直接给出答案可能把未经确认的内容当成事实。"
        )
        next_step = "请补充更完整的题干、清晰图片或相关课程资料，我会重新判断。"
        status = "答案未通过核验，已说明暂不回答的原因"

    body = "\n".join([
        "为什么暂不回答：",
        reason,
        "",
        "当前卡在哪里：",
        check,
        "",
        "怎样可以继续：",
        next_step,
    ])
    return body, status


def _visible_resources(result: AgentRunResult) -> tuple[dict[str, Any], ...]:
    if result.answer_source == "video_learning":
        return tuple(result.external_resources[:4])
    if result.answer_source == "official_external":
        verified = tuple(
            resource
            for resource in result.external_resources
            if resource.get("may_determine_answer")
        )
        return (verified or tuple(result.external_resources[:1]))[:2]
    if result.research_synthesis_status is not None:
        return tuple(result.external_resources[:3])
    return ()
