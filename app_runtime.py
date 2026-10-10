"""Application service and bootstrap layer for CLI and future PySide frontends."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import mimetypes
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping
from uuid import uuid4

from agent_orchestrator import (
    AgentOrchestrator,
    AgentRequest,
    AgentRunResult,
    ExecutionEvent,
    ExecutionPlan,
    QuestionPlanner,
)
from answer_challenge import (
    disputed_option_terms,
    final_choice_set,
    is_answer_challenge,
    is_question_format_correction,
    previous_answer_text,
    previous_evidence_locations,
)
from config_loader import LoadedConfig, load_legacy_config
from casual_chat import CasualConversationEngine, is_casual_chat_candidate
from evidence_bundle import EvidenceBundleBuilder
from evidence_verify import EvidenceVerifier
from external_retrievers import PmcOpenAccessRetriever
from execution_control import RunCancelled, RunTimedOut, ensure_run_active
from external_answer_engine import ExternalAnswerEngine
from general_knowledge import (
    GeneralKnowledgeAnswerEngine,
    is_low_risk_foundational_question,
)
from dynamic_config import dynamic_config_exists, load_dynamic_config
from evidence_fallback import (
    EvidenceFallbackController,
    EvidencePolicy,
    classify_fallback_question,
    evidence_policy_options,
    normalize_evidence_policy,
)
from interaction_modes import InteractionMode, mode_options, normalize_mode
from learning_companion import (
    LearningIntent,
    analyze_learning_turn,
    learning_action_options,
    learning_turn_from_action,
)
from local_help import local_help_response
from medical_horizon import (
    MedicalHorizonPolicy,
    medical_horizon_policy_options,
    normalize_medical_horizon_policy,
    should_expand_medical_horizon,
)
from official_guidelines import (
    OfficialGuidelineRetriever,
    official_guideline_source_options,
)
from model_router import ModelRegistry, ModelRole
from provider_adapters import HttpTransport, ProviderAdapterFactory
from research_evidence import (
    HybridResearchRetriever,
    ResearchQueryPlanner,
    ResearchFilters,
    ResearchSynthesisEngine,
    render_research,
)
from retrieval_agent import LayeredRetrievalAgent, render_external_resources
from retrieval_reranker import RetrievalReranker
from skill_memory import SkillStore, skill_prompt_records
from structure_retriever import StructureAwareRetriever
from subject_profiles import normalize_subject, subject_options
from video_learning import VideoLearningRetriever


class RuntimeConfigurationError(ValueError):
    """Raised with display-safe issues when the application is not ready."""

    def __init__(self, issues: tuple[str, ...] | list[str]) -> None:
        self.issues = tuple(dict.fromkeys(str(item) for item in issues if str(item)))
        super().__init__("；".join(self.issues))


@dataclass(frozen=True)
class RuntimePaths:
    base_dir: Path
    config_path: Path
    document_map_dir: Path
    page_index_dir: Path

    @classmethod
    def from_base_dir(cls, base_dir: str | Path) -> "RuntimePaths":
        base = Path(base_dir).resolve()
        return cls(
            base_dir=base,
            config_path=base / "config.env",
            document_map_dir=base / "data" / "document_maps",
            page_index_dir=base / "data" / "page_indexes",
        )


@dataclass(frozen=True)
class RuntimeInspection:
    ready: bool
    issues: tuple[str, ...]
    modes: tuple[dict[str, Any], ...]
    default_mode: str
    initial_proficiency_label: str


@dataclass(frozen=True)
class ProgressUpdate:
    phase_id: str
    message: str
    state: str = "running"


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    title: str
    description: str
    action_label: str
    risk_level: str = "external_data"


class TutorApplicationService:
    """UI-safe facade: mode state plus one synchronous ask entry point.

    A PySide worker thread can call :meth:`ask`; widgets only need stable mode
    IDs from :meth:`available_modes`, not agent internals or prompt text.
    """

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        *,
        default_mode: str | InteractionMode = InteractionMode.AUTO,
        initial_proficiency_label: str = "beginner",
        retrieval_agent: LayeredRetrievalAgent | None = None,
        research_retriever: HybridResearchRetriever | None = None,
        research_synthesis: ResearchSynthesisEngine | None = None,
        skill_store: SkillStore | None = None,
        external_environment: str = "cn",
        fallback_controller: EvidenceFallbackController | None = None,
        default_evidence_policy: str | EvidencePolicy = EvidencePolicy.LOCAL_FIRST,
        default_medical_horizon_policy: str | MedicalHorizonPolicy = (
            MedicalHorizonPolicy.RESEARCH_MODE_ONLY
        ),
        external_answer_engine: ExternalAnswerEngine | None = None,
        general_knowledge_engine: GeneralKnowledgeAnswerEngine | None = None,
        casual_conversation_engine: CasualConversationEngine | None = None,
        video_retriever: VideoLearningRetriever | None = None,
        default_subject: str = "auto",
        baseline_client: Any | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._lock = RLock()
        self._mode = normalize_mode(default_mode)
        self._initial_proficiency_label = _normalize_proficiency(
            initial_proficiency_label
        )
        self._retrieval_agent = retrieval_agent
        self._research_retriever = research_retriever
        self._research_synthesis = research_synthesis
        self._skill_store = skill_store
        self._external_environment = external_environment
        self._fallback_controller = fallback_controller
        self._evidence_policy = normalize_evidence_policy(default_evidence_policy)
        self._medical_horizon_policy = normalize_medical_horizon_policy(
            default_medical_horizon_policy
        )
        self._external_answer_engine = external_answer_engine
        self._general_knowledge_engine = general_knowledge_engine
        self._casual_conversation_engine = casual_conversation_engine
        self._video_retriever = video_retriever
        self._subject = normalize_subject(default_subject)
        self._baseline_client = baseline_client
        self._conversation_topics: dict[str, str] = {}

    def baseline_answer(self, query: str) -> str:
        """Answer directly with the same model, without the agent workflow.

        This is intentionally available only for anonymized experience
        comparisons; it must never be presented as a verified course answer.
        """

        if self._baseline_client is None:
            raise RuntimeConfigurationError(["当前未配置直接大模型对照通道"])
        response = self._baseline_client.complete_json(
            (
                "你是面向医学生的学习伙伴。直接回答用户问题，先给结论，再用清楚、"
                "易懂的中文解释原因；若题干本身矛盾或缺少必要条件，要明确指出。"
                "不要声称查阅了未提供的教材、课件或论文。只输出JSON。"
            ),
            {
                "question": str(query).strip(),
                "output_schema": {"answer": "完整中文回答"},
            },
        )
        answer = str(response.get("answer", "")).strip()
        if not answer:
            raise RuntimeError("直接大模型对照未返回有效回答")
        return answer

    def usage_snapshot(self) -> dict[str, int]:
        """Return deduplicated provider usage for evaluation cost comparison."""

        totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
        }
        seen: set[int] = set()
        attribute_names = (
            "reasoning_client", "search_client", "client", "generator",
            "verifier", "semantic_verifier", "_baseline_client",
            "_orchestrator", "_general_knowledge_engine",
            "_external_answer_engine", "_casual_conversation_engine",
            "_video_retriever", "planner",
        )

        def visit(value: Any, depth: int = 0) -> None:
            if value is None or id(value) in seen or depth > 4:
                return
            seen.add(id(value))
            usage = getattr(value, "usage_totals", None)
            if isinstance(usage, Mapping):
                for key in totals:
                    try:
                        totals[key] += max(0, int(usage.get(key, 0)))
                    except (TypeError, ValueError):
                        pass
                return
            for name in attribute_names:
                visit(getattr(value, name, None), depth + 1)

        visit(self)
        return totals

    @staticmethod
    def available_modes() -> tuple[dict[str, Any], ...]:
        return mode_options()

    @staticmethod
    def available_subjects() -> tuple[dict[str, str], ...]:
        return subject_options()

    @property
    def current_subject(self) -> str:
        with self._lock:
            return self._subject

    def set_subject(self, subject: str) -> str:
        normalized = normalize_subject(subject)
        with self._lock:
            self._subject = normalized
        return normalized

    @property
    def current_mode(self) -> str:
        with self._lock:
            return self._mode.value

    def set_mode(self, mode: str | InteractionMode) -> str:
        normalized = normalize_mode(mode)
        with self._lock:
            self._mode = normalized
        return normalized.value

    @property
    def initial_proficiency_label(self) -> str:
        with self._lock:
            return self._initial_proficiency_label

    def set_initial_proficiency_label(self, value: str) -> str:
        normalized = _normalize_proficiency(value)
        with self._lock:
            self._initial_proficiency_label = normalized
        return normalized

    def settings_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self._mode.value,
                "initial_proficiency_label": self._initial_proficiency_label,
                "evidence_policy": self._evidence_policy.value,
                "medical_horizon_policy": self._medical_horizon_policy.value,
                "subject": self._subject,
            }

    @staticmethod
    def available_evidence_policies() -> tuple[dict[str, str], ...]:
        return evidence_policy_options()

    @property
    def current_evidence_policy(self) -> str:
        with self._lock:
            return self._evidence_policy.value

    def set_evidence_policy(self, policy: str | EvidencePolicy) -> str:
        normalized = normalize_evidence_policy(policy)
        with self._lock:
            self._evidence_policy = normalized
        return normalized.value

    @staticmethod
    def available_medical_horizon_policies() -> tuple[dict[str, str], ...]:
        return medical_horizon_policy_options()

    @staticmethod
    def available_official_guideline_sources() -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "id": item.source_id,
                "display_name": item.display_name,
                "authority": item.authority,
                "automation_status": item.automation_status,
                "requirement": item.requirement,
                "search_url": item.search_url,
            }
            for item in official_guideline_source_options()
        )

    @property
    def current_medical_horizon_policy(self) -> str:
        with self._lock:
            return self._medical_horizon_policy.value

    def set_medical_horizon_policy(
        self,
        policy: str | MedicalHorizonPolicy,
    ) -> str:
        normalized = normalize_medical_horizon_policy(policy)
        with self._lock:
            self._medical_horizon_policy = normalized
        return normalized.value

    def _review_answer_challenge(
        self,
        feedback: str,
        *,
        prior_result: AgentRunResult | None,
        prior_query: str,
        prior_image: str | None,
        conversation_context: list[dict[str, str]],
        conversation_key: str | None,
        subject: str,
        progress_callback: Callable[[ExecutionEvent], None],
        approval_callback: Callable[[ApprovalRequest], bool] | None,
        cancel_check: Callable[[], bool] | None,
        deadline_monotonic: float | None,
    ) -> AgentRunResult:
        """Re-answer the original question; neither old nor proposed answer is evidence."""
        previous_plan = getattr(prior_result, "plan", None)
        original = (
            str(prior_query or "").strip()
            or str(getattr(previous_plan, "topic_query", "") or "").strip()
        )
        previous = previous_answer_text(prior_result) if prior_result else ""
        if not original or not previous:
            # Restored conversations retain OCR question text, but never the
            # image itself. Pair the stored question with its own answer.
            for item in reversed(conversation_context):
                saved_question = item.get("question_context", "").strip()
                if item["role"] != "assistant" or not saved_question:
                    continue
                if not original or saved_question == original:
                    original = original or saved_question
                    previous = previous or item["body"][:1800]
                    break
        if not original or not previous:
            # A saved text-only history can recover an immediately preceding
            # question/answer pair. Never guess across a video or action turn.
            if len(conversation_context) >= 2:
                earlier, later = conversation_context[-2:]
                candidate = earlier["body"].strip()
                if (
                    earlier["role"] == "user"
                    and later["role"] == "assistant"
                    and not is_answer_challenge(candidate)
                    and "[图片题目" not in candidate
                    and not _is_auxiliary_learning_query(candidate)
                ):
                    original = original or candidate
                    previous = previous or later["body"][:1800]
        if not original or not previous:
            return AgentRunResult(
                status="clarification_required",
                plan=None,
                answer=None,
                rendered_answer=None,
                evidence_bundle=None,
                deterministic_report=None,
                semantic_report=None,
                clarification_questions=[
                    "我需要上一道题的完整题干和原回答才能复核；如果是图片题，请重新上传原图。"
                ],
                events=[ExecutionEvent("correction_review", "needs_input", "缺少上一题或原回答")],
                answer_source="answer_challenge",
            )

        ensure_run_active(cancel_check, deadline_monotonic)
        progress_callback(ExecutionEvent("correction_review", "running", "正在重新核对上一题及争议选项"))
        previous_subjects = list(getattr(previous_plan, "subject_ids", []) or [])
        review_subject = previous_subjects[0] if len(previous_subjects) == 1 else subject
        focus_terms = disputed_option_terms(original, previous, feedback)
        format_correction = is_question_format_correction(feedback)
        review_image = None
        if prior_image and Path(prior_image).is_file() and approval_callback is not None:
            approval = ApprovalRequest(
                request_id=uuid4().hex,
                title="允许重新读取上一张题目图片？",
                description=(
                    "原图保存在本机。为了核对题干和选项，需要再次发送给已配置的视觉模型；"
                    "若拒绝，本轮仍会根据已保存的题干文字继续复核。"
                ),
                action_label="允许重新识图",
            )
            progress_callback(ExecutionEvent("approval", "needs_input", "等待批准重新识图"))
            if approval_callback(approval):
                review_image = prior_image
                progress_callback(ExecutionEvent("approval", "ok", "已批准重新识图"))
            else:
                progress_callback(ExecutionEvent("approval", "warning", "未重新上传原图，改用已存文字"))
        review_query = (
            f"{original}\n用户补充题型：{feedback}。请按此题型重新判断，"
            "但题干与选项仍以原题为准。"
            if format_correction else original
        )
        request = AgentRequest(
            query=review_query,
            image=review_image,
            image_media_type=(mimetypes.guess_type(review_image)[0] if review_image else None),
            mode=InteractionMode.QUICK,
            conversation_context=conversation_context,
            subject=review_subject,
            resolved_learning_intent=LearningIntent.QUESTION.value,
            resolved_topic_query=review_query,
            resolved_effective_query=review_query,
            challenge_feedback=feedback,
            previous_answer=previous,
            previous_evidence_locations=(
                previous_evidence_locations(prior_result) if prior_result else []
            ),
            challenge_focus_terms=focus_terms,
        )
        run_parameters = inspect.signature(self._orchestrator.run).parameters
        accepts_extra = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in run_parameters.values()
        )
        run_kwargs: dict[str, Any] = {"progress_callback": progress_callback}
        if cancel_check is not None and (accepts_extra or "cancel_check" in run_parameters):
            run_kwargs["cancel_check"] = cancel_check
        if deadline_monotonic is not None and (accepts_extra or "deadline_monotonic" in run_parameters):
            run_kwargs["deadline_monotonic"] = deadline_monotonic
        result = self._orchestrator.run(request, **run_kwargs)
        ensure_run_active(cancel_check, deadline_monotonic)
        if format_correction and result.plan is not None:
            # Keep the stable question as the conversation topic; the format
            # correction belongs to this turn, not to every future follow-up.
            result.plan.topic_query = original
        if conversation_key:
            with self._lock:
                self._conversation_topics[conversation_key] = original
        if result.status != "completed" or not result.rendered_answer:
            result.answer_source = "answer_challenge_unresolved"
            result.error = (
                "重新检索和核验后，仍没有足够证据确认应维持还是更改原答案。"
                "这不是接受或否定你提出的答案；请补充参考解析或相关教材页。"
            )
            return result

        new_direct = str(getattr(result.answer, "direct_answer", "") or result.rendered_answer)
        old_set = final_choice_set(previous)
        new_set = final_choice_set(new_direct)
        proposed_set = final_choice_set(feedback)
        if proposed_set and new_set and proposed_set != new_set:
            result.answer_source = "answer_challenge_unresolved"
            result.status = "review_required"
            result.rendered_answer = (
                "复核后仍与您提出的选项不一致，暂不把任一答案标为已解决。\n"
                f"您提出：{', '.join(sorted(proposed_set))}；本轮证据支持："
                f"{', '.join(sorted(new_set))}。\n\n{result.rendered_answer}"
            )
            result.error = "原答案与用户反馈的分歧尚需进一步核对原始资料。"
            return result

        if old_set and new_set:
            verdict = (
                "原回答有误，现已更正。"
                if old_set != new_set
                else "重新核对后，暂未发现需要更改的选项。"
            )
            comparison = (
                f"原选项：{', '.join(sorted(old_set))}；"
                f"复核选项：{', '.join(sorted(new_set))}。"
            )
        else:
            verdict = (
                "已按你补充的题型重新核查，请对照原回答与下面的结论。"
                if format_correction else
                "已根据本轮证据重新核查，请对照原回答与下面的结论。"
            )
            comparison = f"原回答：{previous[:280]}"
        result.rendered_answer = f"复核结果：{verdict}\n{comparison}\n\n{result.rendered_answer}"
        result.answer_source = "answer_challenge"
        result.learning_actions = learning_action_options(LearningIntent.QUESTION, completed=True)
        progress_callback(ExecutionEvent("correction_review", "ok", verdict))
        return result

    def ask(
        self,
        query: str = "",
        *,
        image: str | bytes | None = None,
        image_media_type: str | None = None,
        mode: str | InteractionMode | None = None,
        research_filters: ResearchFilters | None = None,
        progress_callback: Callable[[ProgressUpdate], None] | None = None,
        use_skills: bool = True,
        evidence_policy: str | EvidencePolicy | None = None,
        medical_horizon_policy: str | MedicalHorizonPolicy | None = None,
        approval_callback: Callable[[ApprovalRequest], bool] | None = None,
        conversation_context: list[dict[str, str]] | None = None,
        subject: str | None = None,
        conversation_key: str | None = None,
        learning_action_id: str | None = None,
        learning_topic: str | None = None,
        prior_result: AgentRunResult | None = None,
        prior_query: str = "",
        prior_image: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        deadline_monotonic: float | None = None,
    ) -> AgentRunResult:
        """Run one request; a mode override applies only to this request."""

        with self._lock:
            selected_mode = self._mode if mode is None else normalize_mode(mode)
            proficiency = self._initial_proficiency_label
            selected_subject = (
                self._subject if subject is None else normalize_subject(subject)
            )
            selected_evidence_policy = (
                self._evidence_policy
                if evidence_policy is None
                else normalize_evidence_policy(evidence_policy)
            )
            selected_horizon_policy = (
                self._medical_horizon_policy
                if medical_horizon_policy is None
                else normalize_medical_horizon_policy(medical_horizon_policy)
            )
        clean_context = _sanitize_conversation_context(conversation_context)
        ensure_run_active(cancel_check, deadline_monotonic)
        def core_progress(event: ExecutionEvent) -> None:
            _publish_progress(
                progress_callback,
                ProgressUpdate(event.step, event.message, event.status),
            )

        if image is None and (
            is_answer_challenge(query) or is_question_format_correction(query)
        ):
            return self._review_answer_challenge(
                query,
                prior_result=prior_result,
                prior_query=prior_query,
                prior_image=prior_image,
                conversation_context=clean_context,
                conversation_key=conversation_key,
                subject=selected_subject,
                progress_callback=core_progress,
                approval_callback=approval_callback,
                cancel_check=cancel_check,
                deadline_monotonic=deadline_monotonic,
            )

        if image is None and prior_image and _is_image_revisit_request(query):
            if not Path(prior_image).is_file():
                return AgentRunResult(
                    status="clarification_required",
                    plan=None,
                    answer=None,
                    rendered_answer=None,
                    evidence_bundle=None,
                    deterministic_report=None,
                    semantic_report=None,
                    clarification_questions=["本地保存的原图已无法读取，请重新上传图片。"],
                    events=[],
                    answer_source="local_control",
                )
            elif approval_callback is None:
                return AgentRunResult(
                    status="clarification_required",
                    plan=None,
                    answer=None,
                    rendered_answer=None,
                    evidence_bundle=None,
                    deterministic_report=None,
                    semantic_report=None,
                    clarification_questions=["重新读取原图需要你确认允许再次发送给视觉模型。"],
                    events=[],
                    answer_source="local_control",
                )
            else:
                image = prior_image
                image_media_type = mimetypes.guess_type(prior_image)[0]

        if image is not None and approval_callback is not None:
            approval = ApprovalRequest(
                request_id=uuid4().hex,
                title="允许发送题目图片？",
                description=(
                    "为了识别题目，图片将发送给你已配置的视觉模型服务。"
                    "原图与识别出的题干文字会保存在本地会话中，删除对话时一并删除。"
                    "模型供应商可能按照其服务条款处理请求。"
                ),
                action_label="允许本次发送",
            )
            _publish_progress(
                progress_callback,
                ProgressUpdate("approval", "等待你批准图片识别", "needs_input"),
            )
            if not approval_callback(approval):
                return AgentRunResult(
                    status="cancelled",
                    plan=None,
                    answer=None,
                    rendered_answer=None,
                    evidence_bundle=None,
                    deterministic_report=None,
                    semantic_report=None,
                    clarification_questions=[],
                    events=[
                        ExecutionEvent(
                            "approval",
                            "cancelled",
                            "用户拒绝发送图片，任务已取消",
                        )
                    ],
                    error="你已拒绝发送图片，本次任务未继续执行。",
                    answer_source="local_control",
                )
            _publish_progress(
                progress_callback,
                ProgressUpdate("approval", "已批准本次图片识别", "ok"),
            )

        with self._lock:
            current_topic = (
                self._conversation_topics.get(conversation_key, "")
                if conversation_key else ""
            )
        if learning_action_id:
            learning_turn = learning_turn_from_action(
                learning_action_id,
                learning_topic or current_topic,
                display_prompt=query,
                conversation_context=clean_context,
            )
        else:
            learning_turn = analyze_learning_turn(
                query, clean_context, current_topic=current_topic
            )
        if conversation_key and learning_turn.topic_query:
            with self._lock:
                self._conversation_topics[conversation_key] = learning_turn.topic_query
        ensure_run_active(cancel_check, deadline_monotonic)
        if image is None and learning_turn.intent is LearningIntent.UNDERSTOOD:
            return AgentRunResult(
                status="completed",
                plan=ExecutionPlan(
                    effective_query=learning_turn.effective_query,
                    mode="learning_feedback",
                    clarification_questions=[],
                    search_queries=[],
                    learning_intent=learning_turn.intent.value,
                    topic_query=learning_turn.topic_query,
                ),
                answer=None,
                rendered_answer=(
                    "很好，这个知识点先学到这里。之后如果复习时又卡住，"
                    "可以继续从这段对话接着问。"
                ),
                evidence_bundle=None,
                deterministic_report=None,
                semantic_report=None,
                clarification_questions=[],
                events=[ExecutionEvent("learning_feedback", "ok", "用户已理解")],
                answer_source="learning_feedback",
            )
        if (
            image is None
            and learning_turn.intent is LearningIntent.VIDEO
            and self._video_retriever is not None
        ):
            _publish_progress(
                progress_callback,
                ProgressUpdate("video_search", "正在寻找适合当前知识点的视频", "running"),
            )
            resources = self._video_retriever.search(learning_turn.topic_query)
            ensure_run_active(cancel_check, deadline_monotonic)
            specific_count = sum(
                item.resource_kind == "video_candidate" for item in resources
            )
            search_keywords = list(dict.fromkeys(
                getattr(item, "search_keyword", "")
                for item in resources
                if getattr(item, "search_keyword", "")
            ))
            keyword_text = "、".join(f"“{item}”" for item in search_keywords)
            displayed_keywords = keyword_text or f"“{learning_turn.topic_query}”"
            rendered = (
                f"我先把你的问题提炼为检索词：{displayed_keywords}。"
                f"随后从具体视频候选中筛选出 {specific_count} 个更贴合当前知识点的视频。"
                "每张卡片会说明推荐理由；视频用于辅助理解，不作为医学结论的证据。"
                if specific_count
                else f"我先把你的问题提炼为检索词：{displayed_keywords}，"
                "但暂时没有获得可核对的具体视频，因此只保留关键词搜索入口。"
                "这些入口不是已经筛选过的视频推荐。"
            )
            result = AgentRunResult(
                status="completed",
                plan=ExecutionPlan(
                    effective_query=learning_turn.effective_query,
                    mode="video_learning",
                    clarification_questions=[],
                    search_queries=[learning_turn.topic_query],
                    learning_intent=learning_turn.intent.value,
                    topic_query=learning_turn.topic_query,
                ),
                answer=None,
                rendered_answer=rendered,
                evidence_bundle=None,
                deterministic_report=None,
                semantic_report=None,
                clarification_questions=[],
                events=[ExecutionEvent("video_search", "ok", "视频候选检索完成")],
                external_resources=[
                    {
                        "source": "video_search",
                        "resource_id": item.resource_id,
                        "title": item.title,
                        "url": item.url,
                        "platform": item.platform,
                        "description": item.description,
                        "resource_kind": item.resource_kind,
                        "search_keyword": getattr(item, "search_keyword", ""),
                        "recommendation_reason": getattr(
                            item, "recommendation_reason", ""
                        ),
                    }
                    for item in resources
                ],
                answer_source="video_learning",
            )
            result.learning_actions = learning_action_options(
                LearningIntent.VIDEO,
                completed=True,
                followup=True,
            )
            _publish_progress(
                progress_callback,
                ProgressUpdate("video_search", "视频学习资源已准备", "ok"),
            )
            return result

        if (
            image is None
            and learning_turn.intent is LearningIntent.RESEARCH
            and self._research_retriever is not None
        ):
            if not should_expand_medical_horizon(
                selected_horizon_policy, mode="research"
            ):
                return AgentRunResult(
                    status="completed",
                    plan=ExecutionPlan(
                        effective_query=learning_turn.effective_query,
                        mode="research",
                        clarification_questions=[],
                        search_queries=[],
                        learning_intent=LearningIntent.RESEARCH.value,
                        topic_query=learning_turn.topic_query,
                    ),
                    answer=None,
                    rendered_answer="论文拓展目前已在设置中关闭；开启后可以再试。",
                    evidence_bundle=None,
                    deterministic_report=None,
                    semantic_report=None,
                    clarification_questions=[],
                    events=[],
                    answer_source="local_control",
                )
            paper_result = AgentRunResult(
                status="completed",
                plan=ExecutionPlan(
                    effective_query=learning_turn.effective_query,
                    mode="research",
                    clarification_questions=[],
                    search_queries=[learning_turn.topic_query],
                    learning_intent=LearningIntent.RESEARCH.value,
                    topic_query=learning_turn.topic_query,
                ),
                answer=None,
                rendered_answer="",
                evidence_bundle=None,
                deterministic_report=None,
                semantic_report=None,
                clarification_questions=[],
                events=[],
                answer_source="research_extension",
            )
            return self._expand_research(
                paper_result,
                filters=research_filters,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                deadline_monotonic=deadline_monotonic,
                standalone=True,
                followup=bool(learning_action_id),
            )

        local_help = local_help_response(query, has_image=image is not None)
        if local_help is not None:
            _publish_progress(
                progress_callback,
                ProgressUpdate(
                    "local_help",
                    "已识别为助手介绍或使用帮助",
                    "ok",
                ),
            )
            return AgentRunResult(
                status="completed",
                plan=ExecutionPlan(
                    effective_query=query.strip(),
                    mode="local_help",
                    clarification_questions=[],
                    search_queries=[],
                ),
                answer=None,
                rendered_answer=local_help.text,
                evidence_bundle=None,
                deterministic_report=None,
                semantic_report=None,
                clarification_questions=[],
                events=[
                    ExecutionEvent(
                        "local_help",
                        "ok",
                        f"本地帮助意图：{local_help.intent}",
                    )
                ],
                answer_source="local_help",
            )

        if (
            self._casual_conversation_engine is not None
            and is_casual_chat_candidate(query, has_image=image is not None)
        ):
            casual_parameters = inspect.signature(
                self._casual_conversation_engine.answer
            ).parameters
            casual_answer = self._casual_conversation_engine.answer(
                query,
                **({"conversation_context": clean_context}
                   if "conversation_context" in casual_parameters else {}),
            )
            ensure_run_active(cancel_check, deadline_monotonic)
            if casual_answer:
                _publish_progress(
                    progress_callback,
                    ProgressUpdate("casual_chat", "已识别为非知识型日常聊天", "ok"),
                )
                return AgentRunResult(
                    status="completed",
                    plan=ExecutionPlan(
                        effective_query=query.strip(),
                        mode="casual_chat",
                        clarification_questions=[],
                        search_queries=[],
                    ),
                    answer=None,
                    rendered_answer=casual_answer,
                    evidence_bundle=None,
                    deterministic_report=None,
                    semantic_report=None,
                    clarification_questions=[],
                    events=[
                        ExecutionEvent(
                            "casual_chat",
                            "ok",
                            "非知识型日常聊天直接回复",
                        )
                    ],
                    answer_source="casual_chat",
                )

        recalled = (
            self._skill_store.match(query)
            if use_skills and self._skill_store is not None
            else ()
        )
        if recalled:
            _publish_progress(
                progress_callback,
                ProgressUpdate("skill_recall", f"调用 {len(recalled)} 个已审核 Skill 作为答题提示", "ok"),
            )
        orchestrator_kwargs: dict[str, Any] = {"progress_callback": core_progress}
        run_parameters = inspect.signature(self._orchestrator.run).parameters
        accepts_extra = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in run_parameters.values()
        )
        if cancel_check is not None and (
            accepts_extra or "cancel_check" in run_parameters
        ):
            orchestrator_kwargs["cancel_check"] = cancel_check
        if deadline_monotonic is not None and (
            accepts_extra or "deadline_monotonic" in run_parameters
        ):
            orchestrator_kwargs["deadline_monotonic"] = deadline_monotonic
        result = self._orchestrator.run(
            AgentRequest(
                query=query,
                mode=(
                    InteractionMode.RESEARCH
                    if learning_turn.intent is LearningIntent.RESEARCH
                    else selected_mode
                ),
                image=image,
                image_media_type=image_media_type,
                initial_proficiency_label=proficiency,
                approved_skills=skill_prompt_records(recalled),
                conversation_context=clean_context,
                subject=selected_subject,
                resolved_learning_intent=learning_turn.intent.value,
                resolved_topic_query=learning_turn.topic_query,
                resolved_effective_query=learning_turn.effective_query,
            ),
            **orchestrator_kwargs,
        )
        ensure_run_active(cancel_check, deadline_monotonic)
        result.recalled_skills = [
            {
                "skill_id": item.skill_id,
                "kind": item.kind,
                "trigger_terms": list(item.trigger_terms),
            }
            for item in recalled
        ]
        result.learning_actions = learning_action_options(
            result.plan.learning_intent if result.plan is not None else "question",
            completed=result.status == "completed",
            followup=bool(learning_action_id),
        )
        if result.plan is None:
            return result
        if conversation_key and result.plan.topic_query:
            with self._lock:
                self._conversation_topics[conversation_key] = result.plan.topic_query

        # A low-risk foundational question should not have to wait for a paper
        # search merely because the uploaded course library does not cover it.
        # Answer it through the independently checked general-knowledge route
        # first; clinical decisions remain behind the strict evidence gate.
        general_attempted = False
        initial_partial_answer = (
            result.status == "completed"
            and result.answer is not None
            and result.answer.answerability == "partial"
        )
        if (
            (result.status == "review_required" or initial_partial_answer)
            and self._general_knowledge_engine is not None
            and selected_evidence_policy is not EvidencePolicy.LOCAL_ONLY
        ):
            preliminary_type = classify_fallback_question(
                result.plan.effective_query,
                mode=result.plan.mode,
            ).value
            if is_low_risk_foundational_question(
                result.plan.effective_query,
                preliminary_type,
            ):
                _publish_progress(
                    progress_callback,
                    ProgressUpdate(
                        "general_knowledge",
                        "课程资料未覆盖，正在生成并独立复核基础医学讲解",
                        "running",
                    ),
                )
                general_attempted = True
                ensure_run_active(cancel_check, deadline_monotonic)
                general = self._general_knowledge_engine.answer(
                    result.plan.effective_query,
                    mode=result.plan.mode,
                    learning_intent=result.plan.learning_intent,
                )
                ensure_run_active(cancel_check, deadline_monotonic)
                if general.status == "pass":
                    result.status = "completed"
                    result.answer = None
                    result.rendered_answer = general.rendered_answer
                    result.evidence_bundle = None
                    result.deterministic_report = None
                    result.semantic_report = None
                    result.error = None
                    result.answer_source = (
                        "question_diagnostic"
                        if getattr(general, "question_quality", None) == "ambiguous"
                        else "general_knowledge"
                    )
                    result.learning_actions = learning_action_options(
                        result.plan.learning_intent,
                        completed=True,
                        followup=bool(learning_action_id),
                    )
                    _publish_progress(
                        progress_callback,
                        ProgressUpdate(
                            "general_knowledge",
                            "基础医学讲解已通过独立一致性复核",
                            "ok",
                        ),
                    )
                else:
                    _publish_progress(
                        progress_callback,
                        ProgressUpdate(
                            "general_knowledge",
                            general.error or "基础医学讲解未通过复核，继续寻找外部资料",
                            "warning",
                        ),
                    )
        is_partial_answer = (
            result.status == "completed"
            and result.answer is not None
            and result.answer.answerability == "partial"
        )
        if result.status != "completed" or is_partial_answer:
            if (
                (result.status == "review_required" or is_partial_answer)
                and self._fallback_controller is not None
            ):
                _publish_progress(
                    progress_callback,
                    ProgressUpdate("external_fallback", "课程证据不足，正在查找外部候选资料", "running"),
                )
                fallback = self._fallback_controller.run(
                    result.plan.effective_query,
                    mode=result.plan.mode,
                    policy=selected_evidence_policy,
                    environment=self._external_environment,
                )
                ensure_run_active(cancel_check, deadline_monotonic)
                result.fallback_status = fallback.status
                result.fallback_policy = fallback.policy
                result.fallback_message = fallback.message
                result.fallback_publication_decision = fallback.publication_decision
                result.fallback_publication_reason = fallback.publication_reason
                localized_resources: dict[str, dict[str, str]] = {}
                if self._research_synthesis is not None and hasattr(
                    self._research_synthesis, "localize_resources"
                ):
                    try:
                        localized_resources = self._research_synthesis.localize_resources([
                            {
                                "resource_id": item.resource_id,
                                "title": item.title,
                                "description": item.description,
                            }
                            for item in fallback.resources
                        ])
                    except Exception:
                        # Translation is an optional presentation enhancement;
                        # retrieval and evidence gating remain usable without it.
                        localized_resources = {}
                result.external_resources = [
                    {
                        "source": item.source,
                        "resource_id": item.resource_id,
                        "title": item.title,
                        "title_zh": localized_resources.get(item.resource_id, {}).get(
                            "chinese_title", ""
                        ),
                        "url": item.url,
                        "year": item.year,
                        "authors": list(item.authors),
                        "identifiers": dict(item.identifiers),
                        "evidence_scope": item.evidence_scope,
                        "verification_status": item.verification_status,
                        "may_determine_answer": item.may_determine_answer,
                        "cross_validated": item.cross_validated,
                        "evidence_level": item.evidence_level,
                        "has_abstract": item.has_abstract,
                        "has_excerpt": item.has_excerpt,
                        "publication_types": list(item.publication_types),
                        "authority_level": item.authority_level,
                        "authority_reason": item.authority_reason,
                        "is_guideline_or_consensus": item.is_guideline_or_consensus,
                        "has_full_text": item.has_full_text,
                        "full_text_url": item.full_text_url,
                        "full_text_sha256": item.full_text_sha256,
                        "full_text_char_count": item.full_text_char_count,
                        "description": localized_resources.get(item.resource_id, {}).get(
                            "purpose", item.description
                        ),
                    }
                    for item in fallback.resources
                ]
                result.external_evidence = [
                    {
                        "evidence_id": item.evidence_id,
                        "source": item.source,
                        "resource_id": item.resource_id,
                        "title": item.title,
                        "url": item.url,
                        "locator": item.locator,
                        "evidence_text": item.evidence_text,
                        "span_start": item.span_start,
                        "span_end": item.span_end,
                        "full_text_sha256": item.full_text_sha256,
                        "matched_terms": list(item.matched_terms),
                        "recommendation_markers": list(item.recommendation_markers),
                        "retrieval_score": item.retrieval_score,
                        "access_level": item.access_level,
                    }
                    for item in fallback.official_evidence
                ]
                if (
                    fallback.publication_decision
                    == "eligible_for_verified_external_answer"
                    and fallback.official_evidence
                    and self._external_answer_engine is not None
                ):
                    _publish_progress(
                        progress_callback,
                        ProgressUpdate(
                            "external_answer_verification",
                            "正在生成并独立核验官方指南答复",
                            "running",
                        ),
                    )
                    external_answer = self._external_answer_engine.answer(
                        result.plan.effective_query,
                        mode=result.plan.mode,
                        evidence=fallback.official_evidence,
                    )
                    ensure_run_active(cancel_check, deadline_monotonic)
                    if external_answer.status == "pass":
                        result.status = "completed"
                        result.answer = external_answer.answer
                        result.rendered_answer = external_answer.rendered_answer
                        result.semantic_report = external_answer.semantic_report
                        result.evidence_bundle = external_answer.evidence_bundle
                        result.error = None
                        result.answer_source = "official_external"
                        _publish_progress(
                            progress_callback,
                            ProgressUpdate(
                                "external_answer_verification",
                                "官方指南答复已通过逐条独立核验",
                                "ok",
                            ),
                        )
                    else:
                        if not is_partial_answer:
                            result.error = external_answer.error or result.error
                        _publish_progress(
                            progress_callback,
                            ProgressUpdate(
                                "external_answer_verification",
                                (
                                    "外部补证未通过核验，保留已经核验的课程部分回答"
                                    if is_partial_answer
                                    else "官方指南答复未通过核验，继续保持拦截"
                                ),
                                "warning",
                            ),
                        )
                if (
                    result.status == "review_required"
                    and not is_partial_answer
                    and self._general_knowledge_engine is not None
                    and not general_attempted
                    and selected_evidence_policy is not EvidencePolicy.LOCAL_ONLY
                    and is_low_risk_foundational_question(
                        result.plan.effective_query,
                        fallback.question_type,
                    )
                ):
                    _publish_progress(
                        progress_callback,
                        ProgressUpdate(
                            "general_knowledge",
                            "课程资料未覆盖，正在生成并独立复核通用知识讲解",
                            "running",
                        ),
                    )
                    general_attempted = True
                    ensure_run_active(cancel_check, deadline_monotonic)
                    general = self._general_knowledge_engine.answer(
                        result.plan.effective_query,
                        mode=result.plan.mode,
                        learning_intent=result.plan.learning_intent,
                    )
                    ensure_run_active(cancel_check, deadline_monotonic)
                    if general.status == "pass":
                        result.status = "completed"
                        result.answer = None
                        result.rendered_answer = general.rendered_answer
                        result.evidence_bundle = None
                        result.deterministic_report = None
                        result.semantic_report = None
                        result.error = None
                        result.answer_source = (
                            "question_diagnostic"
                            if getattr(general, "question_quality", None) == "ambiguous"
                            else "general_knowledge"
                        )
                        result.learning_actions = learning_action_options(
                            result.plan.learning_intent,
                            completed=True,
                            followup=bool(learning_action_id),
                        )
                        _publish_progress(
                            progress_callback,
                            ProgressUpdate(
                                "general_knowledge",
                                "通用知识讲解已通过独立一致性复核",
                                "ok",
                            ),
                        )
                    else:
                        _publish_progress(
                            progress_callback,
                            ProgressUpdate(
                                "general_knowledge",
                                general.error or "通用知识讲解未通过复核",
                                "warning",
                            ),
                        )
                _publish_progress(
                    progress_callback,
                    ProgressUpdate(
                        "external_fallback",
                        fallback.message,
                        "warning" if fallback.status != "candidates_found" else "ok",
                    ),
                )
            if (
                result.plan.learning_intent == LearningIntent.RESEARCH.value
                and self._research_retriever is not None
                and should_expand_medical_horizon(
                    selected_horizon_policy, mode=result.plan.mode
                )
            ):
                return self._expand_research(
                    result,
                    filters=research_filters,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                    deadline_monotonic=deadline_monotonic,
                    standalone=True,
                    followup=bool(learning_action_id),
                )
            return result
        if (
            should_expand_medical_horizon(
                selected_horizon_policy,
                mode=result.plan.mode,
            )
            and self._research_retriever is not None
        ):
            return self._expand_research(
                result,
                filters=research_filters,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                deadline_monotonic=deadline_monotonic,
                standalone=(result.plan.learning_intent == LearningIntent.RESEARCH.value),
                followup=bool(learning_action_id),
            )
        if self._retrieval_agent is not None:
            ensure_run_active(cancel_check, deadline_monotonic)
            packet = self._retrieval_agent.retrieve(
                result.plan.effective_query,
                mode=result.plan.mode,
                environment=self._external_environment,
                include_local=False,
            )
            ensure_run_active(cancel_check, deadline_monotonic)
            result.external_resources = [
                {
                    "source": item.source,
                    "resource_id": item.resource_id,
                    "title": item.title,
                    "url": item.url,
                    "year": item.year,
                    "authors": list(item.authors),
                    "identifiers": dict(item.identifiers),
                }
                for item in packet.external_resources
            ]
            extension = render_external_resources(packet)
            if extension:
                result.rendered_answer = f"{result.rendered_answer}\n\n{extension}"
        return result

    def _expand_research(
        self,
        result: AgentRunResult,
        *,
        filters: ResearchFilters | None,
        progress_callback: Callable[[ProgressUpdate], None] | None,
        cancel_check: Callable[[], bool] | None,
        deadline_monotonic: float | None,
        standalone: bool,
        followup: bool,
    ) -> AgentRunResult:
        """A requested paper search may succeed even when course QA cannot."""
        try:
            ensure_run_active(cancel_check, deadline_monotonic)
            topic = result.plan.topic_query or result.plan.effective_query
            packet = self._research_retriever.search(
                topic,
                filters=filters,
                progress_callback=lambda phase, message, state: _publish_progress(
                    progress_callback, ProgressUpdate(phase, message, state)
                ),
            )
            ensure_run_active(cancel_check, deadline_monotonic)
            synthesis = (
                self._research_synthesis.synthesize(
                    packet,
                    progress_callback=lambda phase, message, state: _publish_progress(
                        progress_callback, ProgressUpdate(phase, message, state)
                    ),
                )
                if self._research_synthesis is not None and packet.papers
                else None
            )
            ensure_run_active(cancel_check, deadline_monotonic)
            result.research_synthesis_status = synthesis.status if synthesis else "not_configured"
            verified_introductions = {
                intro.paper_id: intro.text
                for intro in (
                    synthesis.introductions
                    if synthesis and synthesis.status == "pass"
                    else ()
                )
            }
            result.external_resources = [
                {
                    "resource_kind": "research_paper",
                    "paper_id": paper.paper_id,
                    "title": paper.title,
                    "url": paper.url,
                    "year": paper.year,
                    "authors": list(paper.authors),
                    "doi": paper.doi,
                    "sources": list(paper.sources),
                    "cross_validated": paper.cross_validated,
                    "publication_types": list(paper.publication_types),
                    "evidence_level": paper.evidence_level,
                    "description": verified_introductions.get(paper.paper_id, ""),
                }
                for paper in packet.papers
            ]
            extension = render_research(packet, synthesis)
            result.research_extension = extension
            if standalone:
                result.status = "completed"
                result.answer = None
                result.answer_source = "research_extension"
                result.rendered_answer = extension
                result.error = None
                result.learning_actions = learning_action_options(
                    LearningIntent.RESEARCH, completed=True, followup=followup
                )
            elif result.status == "completed":
                result.rendered_answer = f"{result.rendered_answer}\n\n{extension}"
            else:
                result.rendered_answer = extension
                result.error = "未检索到相关论文；课程问题也尚未获得足够证据。"
            _publish_progress(
                progress_callback,
                ProgressUpdate(
                    "research_extension",
                    f"科研拓展完成，找到 {len(packet.papers)} 篇候选论文",
                    "ok" if packet.papers else "warning",
                ),
            )
        except (RunCancelled, RunTimedOut):
            raise
        except Exception as exc:
            _publish_progress(
                progress_callback,
                ProgressUpdate(
                    "research_extension", "科研检索或核验未完成，已保留原有回答", "warning"
                ),
            )
            result.research_synthesis_status = "error"
            original = result.rendered_answer or ""
            preserved = (
                "原有回答不受影响" if standalone
                else "课程证据答案不受影响"
            )
            result.rendered_answer = (
                f"{original}\n\n科研检索提示：外部检索或核验暂时失败"
                f"（{type(exc).__name__}），{preserved}。"
            ).strip()
        return result


def inspect_runtime(
    paths: RuntimePaths,
    *,
    loaded_config: LoadedConfig | None = None,
) -> RuntimeInspection:
    issues: list[str] = []
    config: LoadedConfig | None = loaded_config
    if config is None:
        if dynamic_config_exists(paths.base_dir):
            try:
                config = load_dynamic_config(paths.base_dir)
            except ValueError as exc:
                issues.append(str(exc))
        elif not paths.config_path.is_file():
            issues.append(f"缺少配置文件: {paths.config_path}")
        else:
            try:
                config = load_legacy_config(paths.config_path)
            except ValueError as exc:
                issues.append(str(exc))

    default_mode = InteractionMode.AUTO.value
    proficiency = "beginner"
    if config is not None:
        issues.extend(config.issues)
        registry = ModelRegistry.from_config(config.config, config.secrets)
        issues.extend(registry.validate())
        defaults = config.config.get("user_defaults", {})
        try:
            default_mode = normalize_mode(
                defaults.get("interaction_style", InteractionMode.AUTO.value)
            ).value
        except ValueError as exc:
            issues.append(str(exc))
        proficiency = _normalize_proficiency(
            str(defaults.get("initial_proficiency_label", "beginner"))
        )

    if not paths.document_map_dir.is_dir():
        issues.append(f"缺少文档结构索引目录: {paths.document_map_dir}")
    if not paths.page_index_dir.is_dir():
        issues.append(f"缺少逐页文本索引目录: {paths.page_index_dir}")

    unique_issues = tuple(dict.fromkeys(issues))
    return RuntimeInspection(
        ready=not unique_issues,
        issues=unique_issues,
        modes=mode_options(),
        default_mode=default_mode,
        initial_proficiency_label=proficiency,
    )


def build_vision_client(
    base_dir: str | Path,
    *,
    config_path: str | Path | None = None,
    transport: HttpTransport | None = None,
):
    """Build the configured vision adapter without making an API request."""

    paths = RuntimePaths.from_base_dir(base_dir)
    resolved_config = Path(config_path).resolve() if config_path else paths.config_path
    loaded = (
        load_dynamic_config(paths.base_dir)
        if dynamic_config_exists(paths.base_dir)
        else load_legacy_config(resolved_config)
    )
    registry = ModelRegistry.from_config(loaded.config, loaded.secrets)
    if ModelRole.VISION.value not in registry.effective_routes:
        raise RuntimeConfigurationError(("尚未配置可用的视觉模型",))
    return ProviderAdapterFactory(registry, transport=transport).vision_client()


def build_application_service(
    base_dir: str | Path,
    *,
    config_path: str | Path | None = None,
    document_map_dir: str | Path | None = None,
    page_index_dir: str | Path | None = None,
    transport: HttpTransport | None = None,
) -> TutorApplicationService:
    """Build the production service without making an external API request."""

    defaults = RuntimePaths.from_base_dir(base_dir)
    paths = RuntimePaths(
        base_dir=defaults.base_dir,
        config_path=Path(config_path).resolve() if config_path else defaults.config_path,
        document_map_dir=(
            Path(document_map_dir).resolve()
            if document_map_dir
            else defaults.document_map_dir
        ),
        page_index_dir=(
            Path(page_index_dir).resolve() if page_index_dir else defaults.page_index_dir
        ),
    )
    loaded = (
        load_dynamic_config(paths.base_dir)
        if dynamic_config_exists(paths.base_dir)
        else load_legacy_config(paths.config_path)
    )
    inspection = inspect_runtime(paths, loaded_config=loaded)
    if not inspection.ready:
        raise RuntimeConfigurationError(inspection.issues)

    registry = ModelRegistry.from_config(loaded.config, loaded.secrets)
    adapters = ProviderAdapterFactory(registry, transport=transport)
    reasoning_client = adapters.json_client(ModelRole.REASONING)
    planning_client = adapters.json_client(ModelRole.PLANNING)
    semantic_client = adapters.json_client(ModelRole.VERIFICATION)
    vision_client = (
        adapters.vision_client()
        if ModelRole.VISION.value in registry.effective_routes
        else None
    )

    retriever = StructureAwareRetriever.from_directories(
        paths.document_map_dir, paths.page_index_dir
    )
    evidence_builder = EvidenceBundleBuilder.from_directories(
        paths.document_map_dir, paths.page_index_dir
    )
    evidence_verifier = EvidenceVerifier.from_directories(
        paths.document_map_dir, paths.page_index_dir
    )
    orchestrator = AgentOrchestrator(
        retriever=retriever,
        evidence_builder=evidence_builder,
        evidence_verifier=evidence_verifier,
        reasoning_client=reasoning_client,
        semantic_client=semantic_client,
        vision_client=vision_client,
        planner=QuestionPlanner(planning_client),
        max_revisions=2,
        retrieval_reranker=RetrievalReranker(planning_client),
    )
    layered_retrieval = LayeredRetrievalAgent(local_retriever=retriever)
    research_retriever = HybridResearchRetriever(
        planner=ResearchQueryPlanner(planning_client)
    )
    fallback_controller = EvidenceFallbackController(
        layered_retrieval,
        research_retriever=research_retriever,
        full_text_retriever=PmcOpenAccessRetriever(),
        official_guideline_retriever=OfficialGuidelineRetriever(),
    )
    research_synthesis = ResearchSynthesisEngine(
        reasoning_client, semantic_client
    )
    skill_store = SkillStore(paths.base_dir / "data" / "skills.json")
    return TutorApplicationService(
        orchestrator,
        default_mode=inspection.default_mode,
        initial_proficiency_label=inspection.initial_proficiency_label,
        retrieval_agent=layered_retrieval,
        research_retriever=research_retriever,
        research_synthesis=research_synthesis,
        skill_store=skill_store,
        fallback_controller=fallback_controller,
        external_answer_engine=ExternalAnswerEngine(
            reasoning_client, semantic_client
        ),
        general_knowledge_engine=GeneralKnowledgeAnswerEngine(
            reasoning_client, semantic_client
        ),
        casual_conversation_engine=CasualConversationEngine(reasoning_client),
        video_retriever=VideoLearningRetriever(
            planner=planning_client,
            selector=reasoning_client,
        ),
        baseline_client=reasoning_client,
    )


def _normalize_proficiency(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"beginner", "proficient"}:
        raise ValueError("用户基础标签必须是 beginner 或 proficient")
    return normalized


def _sanitize_conversation_context(
    context: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    cleaned = []
    for item in (context or [])[-12:]:
        role = str(item.get("role", "")).strip()
        body = str(item.get("body", "")).strip()
        if role not in {"user", "assistant"} or not body:
            continue
        cleaned.append({
            "role": role,
            "body": body[:1800],
            "question_context": str(item.get("question_context", "")).strip()[:6000],
        })
    return cleaned


def _is_auxiliary_learning_query(query: str) -> bool:
    compact = "".join(str(query).lower().split())
    return any(
        marker in compact
        for marker in (
            "找视频", "换一种讲法", "换种讲法", "举个例子", "出题检验",
            "回到课件", "找论文", "科研拓展", "测测我",
        )
    )


def _is_image_revisit_request(query: str) -> bool:
    compact = "".join(str(query).lower().split())
    return any(marker in compact for marker in (
        "重新看图", "再看图片", "再看一下图片", "重新识图",
        "再识别图片", "重新看原图", "再看原图",
    ))


def _publish_progress(
    callback: Callable[[ProgressUpdate], None] | None,
    update: ProgressUpdate,
) -> None:
    if callback is None:
        return
    try:
        callback(update)
    except Exception:
        # A broken UI observer must never alter the tutoring result.
        pass
