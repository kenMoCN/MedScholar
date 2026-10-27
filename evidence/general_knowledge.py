"""Transparent fallback for low-risk foundational medicine questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class JsonClient(Protocol):
    def complete_json(
        self, system_instruction: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class JsonVerifier(Protocol):
    def verify_json(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class GeneralKnowledgeResult:
    status: str
    rendered_answer: str | None
    error: str | None = None
    question_quality: str | None = None


def is_low_risk_foundational_question(query: str, question_type: str) -> bool:
    """Keep this fallback away from diagnosis, treatment and dose decisions."""
    if question_type not in {"course_fact", "terminology"}:
        return False
    compact = "".join(str(query).lower().split())
    high_risk = (
        "患者", "病人", "治疗", "用药", "剂量", "处方", "诊断", "手术",
        "急救", "休克", "怎么办", "如何处理", "处理原则",
    )
    return bool(compact) and not any(marker in compact for marker in high_risk)


class GeneralKnowledgeAnswerEngine:
    """Answer a basic-science question, then run an independent consistency check.

    This route deliberately does not claim course-material or literature support.
    It is a usability fallback for low-risk learning questions when the uploaded
    knowledge base does not cover the subject.
    """

    def __init__(self, generator: JsonClient, verifier: JsonVerifier) -> None:
        self.generator = generator
        self.verifier = verifier

    def answer(
        self,
        query: str,
        *,
        mode: str,
        learning_intent: str = "question",
    ) -> GeneralKnowledgeResult:
        if learning_intent == "example":
            return self._answer_example(query, mode=mode)
        if learning_intent == "practice":
            return self._answer_practice(query, mode=mode)
        reexplain_style = (
            "本轮是用户点击‘换一种讲法’后的重新解释。请像耐心、亲切的学习伙伴一样回答，"
            "使用常用词和短句，按三到五步讲清因果链，解释首次出现的术语，"
            "并配一个直观类比；不要照抄上一版，也不要使用让用户感到被责备的表达。"
            if learning_intent == "reexplain"
            else ""
        )
        system = (
            "你是医学生的基础医学学习伙伴。当前没有匹配的课程教材证据。"
            "只处理低风险的基础医学与考试题，不处理患者诊断、治疗、处方或剂量。"
            "若是选择题，必须先给出明确选项，再逐项解释为什么正确或错误；"
            "特别检查题干问的是正确项还是错误项、单选还是多选。"
            "如果标为单选但按通行定义存在多个成立答案，必须将question_quality设为ambiguous，"
            "final_answer必须明确写出‘本题无唯一答案’并列出发生冲突的选项，"
            "随后解释每个冲突项；不得为了凑单选强行只选一个，也不得将原因写成证据不足。"
            "不要伪造教材页码、论文或引用。只输出JSON。"
            + reexplain_style
        )
        schema = {
            "answerable": True,
            "final_answer": "明确答案；选择题写选项字母和内容",
            "explanation": "面向医学生的简明解释",
            "option_analysis": [
                {"option": "A", "verdict": "正确或错误", "reason": "原因"}
            ],
            "question_quality": "valid | ambiguous",
            "confidence": "high | medium | low",
            "uncertainties": ["仍需课程资料确认的内容"],
        }
        try:
            draft = self.generator.complete_json(
                system,
                {"query": query, "mode": mode, "output_schema": schema},
            )
            parsed = _parse_draft(draft)
            if parsed is None:
                try:
                    draft = self.generator.complete_json(
                        system,
                        {
                            "query": query,
                            "mode": mode,
                            "output_schema": schema,
                            "previous_invalid_output": dict(draft),
                            "revision_instruction": (
                                "上一次输出缺少必填字段或字段格式错误。请完整重写，"
                                "严格遵守output_schema，不要省略逐项分析。"
                            ),
                        },
                    )
                except Exception:
                    return GeneralKnowledgeResult(
                        "review", None, "通用知识生成结果不完整，未向用户发布。"
                    )
                parsed = _parse_draft(draft)
                if parsed is None:
                    return GeneralKnowledgeResult(
                        "review", None, "通用知识生成结果不完整，未向用户发布。"
                    )
            verdict = self._verify(query, parsed)
            if verdict["verdict"] != "pass" and verdict["issues"]:
                try:
                    revised = self.generator.complete_json(
                        system,
                        {
                            "query": query,
                            "mode": mode,
                            "output_schema": schema,
                            "previous_draft": parsed,
                            "revision_instruction": (
                                "独立复核发现以下问题，请逐项修正；不能确定时将answerable设为false："
                                + "；".join(verdict["issues"])
                            ),
                        },
                    )
                except Exception:
                    return GeneralKnowledgeResult(
                        "review", None,
                        "通用知识答案未通过独立一致性复核："
                        + "；".join(verdict["issues"]),
                    )
                parsed = _parse_draft(revised)
                if parsed is None:
                    return GeneralKnowledgeResult(
                        "review", None, "通用知识修订结果不完整，未向用户发布。"
                    )
                verdict = self._verify(query, parsed)
            if verdict["verdict"] != "pass":
                return GeneralKnowledgeResult(
                    "review", None,
                    "通用知识答案未通过独立一致性复核"
                    + (f"：{'；'.join(verdict['issues'])}" if verdict["issues"] else "。"),
                )
            return GeneralKnowledgeResult(
                "pass",
                _render(parsed),
                question_quality=str(parsed["question_quality"]),
            )
        except Exception as exc:
            return GeneralKnowledgeResult(
                "fail", None, f"通用知识回答失败: {type(exc).__name__}"
            )

    def _answer_example(self, query: str, *, mode: str) -> GeneralKnowledgeResult:
        """Generate one grounded teaching example, not a second MCQ answer."""
        system = (
            "你是医学生的基础医学学习伙伴。用户已经看过原题答案，本轮只点了‘举个例子’。"
            "只给一个新的、具体的情境，说明其中发生什么，再用一句话点出对应的医学原理。"
            "不要重报原题答案，不要逐项判断 A、B、C 等选项，不要复述上一轮整段解析。"
            "只处理低风险基础知识，不作个体诊疗建议；不要伪造教材或论文来源。只输出JSON。"
        )
        schema = {
            "answerable": True,
            "example": "一个具体、简短且贴合知识点的新情境",
            "takeaway": "一句话说明例子对应的原理",
            "confidence": "high | medium | low",
            "uncertainties": ["仍需课程资料确认的内容"],
        }
        revision: dict[str, Any] = {}
        try:
            for attempt in range(2):
                draft = self.generator.complete_json(
                    system,
                    {"query": query, "mode": mode, "output_schema": schema, **revision},
                )
                parsed = _parse_example(draft)
                if parsed is None:
                    revision = {
                        "previous_invalid_output": dict(draft),
                        "revision_instruction": (
                            "只输出一个新例子和一句原理对应；不得包含原题答案或逐项解析。"
                        ),
                    }
                    continue
                raw_verdict = self.verifier.verify_json({
                    "system_instruction": (
                        "你是独立的基础医学讲解复核器。检查这个例子是否准确、贴合原知识点，"
                        "对应原理是否正确，以及是否暗中重复原题选项解析。"
                        "这只是独立一致性检查，不是教材证据核验。只输出JSON。"
                    ),
                    "question": query,
                    "draft": parsed,
                    "output_schema": {
                        "verdict": "pass | review | fail",
                        "issues": ["具体问题"],
                    },
                })
                verdict = str(raw_verdict.get("verdict", "fail")).strip().lower()
                issues = raw_verdict.get("issues", [])
                if verdict == "pass" and isinstance(issues, list) and not issues:
                    return GeneralKnowledgeResult("pass", _render_example(parsed))
                if not isinstance(issues, list):
                    issues = ["复核响应格式无效"]
                revision = {
                    "previous_draft": parsed,
                    "revision_instruction": (
                        "独立复核发现问题，请只修正例子与原理："
                        + "；".join(str(item) for item in issues[:3])
                    ),
                }
            return GeneralKnowledgeResult(
                "review", None, "举例内容未通过独立一致性复核，暂不发布。"
            )
        except Exception as exc:
            return GeneralKnowledgeResult(
                "fail", None, f"通用知识举例失败: {type(exc).__name__}"
            )

    def _answer_practice(self, query: str, *, mode: str) -> GeneralKnowledgeResult:
        """Ask one new check question, keeping its answer out of the UI."""
        system = (
            "你是医学生的基础医学学习伙伴。用户已经看过原题解析，本轮只点了‘出题检验’。"
            "围绕同一知识点出一道新的、简短且答案明确的练习题，不复述原题或原选项。"
            "expected_answer和rationale只供独立复核，绝不能放进question；等用户作答后再讲解。"
            "只处理低风险基础知识，不作个体诊疗建议。只输出JSON。"
        )
        schema = {
            "question": "一道新的简短练习题，不含答案",
            "expected_answer": "内部复核用标准答案，不向用户展示",
            "rationale": "内部复核用一句理由，不向用户展示",
            "confidence": "high | medium | low",
        }
        revision: dict[str, Any] = {}
        try:
            for _ in range(2):
                raw = self.generator.complete_json(
                    system,
                    {"query": query, "mode": mode, "output_schema": schema, **revision},
                )
                parsed = _parse_practice(raw)
                if parsed is None:
                    revision = {
                        "previous_invalid_output": dict(raw),
                        "revision_instruction": "只出一道新题；答案只放在内部复核字段，不能写进题目。",
                    }
                    continue
                review = self.verifier.verify_json({
                    "system_instruction": (
                        "独立检查这道练习题是否与原知识点相关、不同于原题，"
                        "题意清楚且标准答案正确；同时确认题面没有泄露答案。"
                        "这是通用知识一致性复核，不是教材证据核验。只输出JSON。"
                    ),
                    "original_question": query,
                    "draft": parsed,
                    "output_schema": {
                        "verdict": "pass | review | fail",
                        "issues": ["具体问题"],
                    },
                })
                verdict = str(review.get("verdict", "fail")).strip().lower()
                issues = review.get("issues", [])
                if verdict == "pass" and isinstance(issues, list) and not issues:
                    return GeneralKnowledgeResult(
                        "pass",
                        "【小练习｜通用知识，未与课程资料核对】\n\n"
                        + parsed["question"]
                        + "\n\n你先试着作答，我再帮你核对。",
                    )
                if not isinstance(issues, list):
                    issues = ["复核响应格式无效"]
                revision = {
                    "previous_draft": parsed,
                    "revision_instruction": "独立复核发现问题，请重新出一道题："
                    + "；".join(str(item) for item in issues[:3]),
                }
            return GeneralKnowledgeResult(
                "review", None, "练习题未通过独立一致性复核，暂不发布。"
            )
        except Exception as exc:
            return GeneralKnowledgeResult(
                "fail", None, f"通用知识出题失败: {type(exc).__name__}"
            )

    def _verify(self, query: str, draft: Mapping[str, Any]) -> dict[str, Any]:
        raw = self.verifier.verify_json({
            "system_instruction": (
                "你是独立的基础医学答题复核器。不要迎合初稿。检查题干方向、单选/多选、"
                "最终选项与逐项分析是否一致，以及是否存在明确的基础医学事实错误。"
                "如果原题标为单选但事实上有多个正确或错误选项，而初稿已将question_quality标为"
                "ambiguous并准确说明冲突，应判pass；不能因为原题有问题而拒绝这份纠错回答。"
                "这不是证据核验；没有课程资料时不得声称教材已核对。只输出JSON。"
            ),
            "question": query,
            "draft": dict(draft),
            "output_schema": {
                "verdict": "pass | review | fail",
                "issues": ["具体问题"],
            },
        })
        verdict = str(raw.get("verdict", "fail")).strip().lower()
        issues = raw.get("issues", [])
        if verdict not in {"pass", "review", "fail"} or not isinstance(issues, list):
            return {"verdict": "fail", "issues": ["复核响应格式无效"]}
        return {
            "verdict": verdict,
            "issues": [str(item).strip() for item in issues if str(item).strip()],
        }


def _parse_draft(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    if raw.get("answerable") is not True:
        return None
    final_answer = str(raw.get("final_answer", "")).strip()
    explanation = str(raw.get("explanation", "")).strip()
    confidence = str(raw.get("confidence", "")).strip().lower()
    question_quality = str(raw.get("question_quality", "valid")).strip().lower()
    option_analysis = raw.get("option_analysis", [])
    uncertainties = raw.get("uncertainties", [])
    if (
        not final_answer
        or not explanation
        or confidence not in {"high", "medium", "low"}
        or question_quality not in {"valid", "ambiguous"}
        or not isinstance(option_analysis, list)
        or not isinstance(uncertainties, list)
    ):
        return None
    clean_options = []
    for item in option_analysis:
        if not isinstance(item, Mapping):
            return None
        option = str(item.get("option", "")).strip()
        verdict = str(item.get("verdict", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not option or not verdict or not reason:
            return None
        clean_options.append({"option": option, "verdict": verdict, "reason": reason})
    return {
        "final_answer": final_answer,
        "explanation": explanation,
        "confidence": confidence,
        "question_quality": question_quality,
        "option_analysis": clean_options,
        "uncertainties": [
            str(item).strip() for item in uncertainties if str(item).strip()
        ],
    }


def _parse_example(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    if raw.get("answerable") is not True:
        return None
    example = str(raw.get("example", "")).strip()
    takeaway = str(raw.get("takeaway", "")).strip()
    confidence = str(raw.get("confidence", "")).strip().lower()
    uncertainties = raw.get("uncertainties", [])
    if (
        not example or not takeaway or confidence not in {"high", "medium", "low"}
        or not isinstance(uncertainties, list)
        or any(marker in example + takeaway for marker in ("逐项判断", "选项分析", "答案是"))
    ):
        return None
    return {
        "example": example[:900],
        "takeaway": takeaway[:240],
        "confidence": confidence,
        "uncertainties": [str(item).strip() for item in uncertainties if str(item).strip()],
    }


def _parse_practice(raw: Mapping[str, Any]) -> dict[str, str] | None:
    question = str(raw.get("question", "")).strip()
    answer = str(raw.get("expected_answer", "")).strip()
    rationale = str(raw.get("rationale", "")).strip()
    confidence = str(raw.get("confidence", "")).strip().lower()
    if (
        not question or not answer or not rationale
        or confidence not in {"high", "medium", "low"}
        or any(marker in question for marker in ("答案是", "正确答案", "选项解析", "逐项判断"))
        or (len(answer) >= 3 and answer in question)
    ):
        return None
    return {
        "question": question[:500],
        "expected_answer": answer[:300],
        "rationale": rationale[:500],
        "confidence": confidence,
    }


def _render_example(draft: Mapping[str, Any]) -> str:
    return "\n\n".join([
        "【举个例子｜通用知识，未与课程资料核对】",
        str(draft["example"]),
        f"对应原理：{draft['takeaway']}",
    ])


def _render(draft: Mapping[str, Any]) -> str:
    is_ambiguous = draft.get("question_quality") == "ambiguous"
    lines = [
        (
            "【题目存在矛盾｜无法确定唯一选项】"
            if is_ambiguous
            else "【通用知识讲解｜未与课程资料核对】"
        ),
        str(draft["final_answer"]),
        "",
        str(draft["explanation"]),
    ]
    if is_ambiguous:
        lines.extend([
            "",
            "矛盾点：题干要求单选，但逐项判断后存在多个符合题干要求的选项，"
            "因此不能得到唯一答案。这是题目唯一性问题，不是因为没有足够证据。",
        ])
    options = draft.get("option_analysis", [])
    if options:
        lines.extend(["", "逐项判断："])
        for item in options:
            lines.append(
                f"- {item['option']}：{item['verdict']}。{item['reason']}"
            )
    uncertainties = draft.get("uncertainties", [])
    if uncertainties:
        lines.extend(["", "仍建议结合你的课程资料确认："])
        lines.extend(f"- {item}" for item in uncertainties)
    lines.extend([
        "",
        (
            "结论：系统不强行选择某一个选项，而是保留并说明已经复核出的题目矛盾；"
            "建议核对原题、标准答案或课程教材表述。"
            if is_ambiguous
            else "说明：这是基础医学通用知识回答，已经过独立一致性复核，"
            "但尚未与你所在课程的教材或课件核对。"
        ),
    ])
    return "\n".join(lines)
