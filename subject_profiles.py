"""Medical subject catalog and lightweight routing helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class SubjectProfile:
    subject_id: str
    display_name: str
    aliases: tuple[str, ...]
    description: str


SUBJECT_PROFILES: tuple[SubjectProfile, ...] = (
    SubjectProfile("anatomy", "人体解剖学", ("人体解剖学", "系统解剖学", "局部解剖学", "解剖学"), "人体结构与空间关系"),
    SubjectProfile("histology_embryology", "组织学与胚胎学", ("组织学与胚胎学", "组织胚胎学", "组织学", "胚胎学"), "微细结构与胚胎发育"),
    SubjectProfile("physiology", "生理学", ("生理学", "人体生理学", "医学生理学"), "正常人体功能与调节机制"),
    SubjectProfile("biochemistry", "生物化学与分子生物学", ("生物化学与分子生物学", "生物化学", "分子生物学", "生化"), "代谢、分子机制与信息传递"),
    SubjectProfile("pathology", "病理学", ("病理学", "基础病理"), "疾病形态学与发生发展规律"),
    SubjectProfile("pathophysiology", "病理生理学", ("病理生理学", "病生"), "疾病状态下的功能和代谢改变"),
    SubjectProfile("pharmacology", "药理学", ("药理学", "临床药理学", "药理"), "药物作用、机制与不良反应"),
    SubjectProfile("microbiology_immunology", "微生物学与免疫学", ("医学微生物学", "微生物学", "医学免疫学", "免疫学"), "病原体与免疫应答"),
    SubjectProfile("diagnostics", "诊断学", ("诊断学", "实验诊断学", "物理诊断学"), "症状、体征与辅助检查"),
    SubjectProfile("internal_medicine", "内科学", ("内科学", "内科"), "常见内科疾病的诊疗思路"),
    SubjectProfile("surgery", "外科学", ("外科学", "外科"), "外科疾病与围手术期原则"),
)

_BY_ID = {item.subject_id: item for item in SUBJECT_PROFILES}

_QUERY_HINTS: dict[str, tuple[str, ...]] = {
    "physiology": ("动作电位", "静息电位", "心动周期", "肾小球滤过", "通气血流比", "激素调节"),
    "biochemistry": ("酶", "辅酶", "辅助因子", "糖酵解", "三羧酸循环", "蛋白质合成", "dna", "rna"),
    "pathology": ("镜下", "病理切片", "肉眼观", "坏死", "肿瘤分级"),
    "pathophysiology": ("酸碱平衡紊乱", "水肿机制", "缺氧", "休克机制", "病理生理"),
    "pharmacology": ("受体激动剂", "受体拮抗剂", "药代动力学", "半衰期", "不良反应", "禁忌证"),
    "anatomy": ("起点止点", "神经支配", "解剖位置", "毗邻", "穿过", "分支"),
    "histology_embryology": ("组织结构", "胚层", "胚胎发育", "组织切片"),
    "microbiology_immunology": ("细菌", "病毒", "真菌", "抗原", "抗体", "免疫应答", "补体"),
    "diagnostics": ("体征", "实验室检查", "辅助检查", "鉴别诊断", "问诊"),
    "internal_medicine": ("内科", "诊疗", "治疗方案", "临床表现"),
    "surgery": ("手术", "术前", "术后", "外科处理"),
}

_DOCUMENT_HINTS: dict[str, tuple[str, ...]] = {
    "biochemistry": (
        "糖代谢", "脂质代谢", "氨基酸代谢", "蛋白质结构与功能",
        "核酸的结构与功能", "酶", "维生素", "分子生物学",
    ),
    "physiology": ("细胞的基本功能", "血液", "血液循环", "呼吸生理", "尿的生成"),
    "anatomy": ("系统解剖", "局部解剖", "断层解剖"),
    "histology_embryology": ("基本组织", "胚胎发生", "组织胚胎"),
    "pathology": ("细胞和组织的适应", "损伤与修复", "肿瘤病理"),
    "pathophysiology": ("水电解质代谢紊乱", "酸碱平衡紊乱", "发热", "休克"),
    "pharmacology": ("药物效应动力学", "药物代谢动力学", "传出神经系统药物"),
}


def subject_options() -> tuple[dict[str, str], ...]:
    return (
        {
            "id": "auto",
            "display_name": "自动识别学科",
            "description": "根据题目和课程资料自动选择一个或多个医学学科。",
        },
        *tuple({
            "id": item.subject_id,
            "display_name": item.display_name,
            "description": item.description,
        } for item in SUBJECT_PROFILES),
    )


def normalize_subject(value: str) -> str:
    normalized = str(value or "auto").strip().lower()
    if normalized == "auto" or normalized in _BY_ID:
        return normalized
    raise ValueError(f"未知学科: {value!r}")


def infer_document_subjects(text: str) -> tuple[str, ...]:
    """Infer conservative subject tags from a file name or document title."""
    compact = _compact(text)
    matched = []
    for profile in SUBJECT_PROFILES:
        if any(_compact(alias) in compact for alias in profile.aliases):
            matched.append(profile.subject_id)
    for subject_id, markers in _DOCUMENT_HINTS.items():
        if any(_compact(marker) in compact for marker in markers):
            matched.append(subject_id)
    return tuple(dict.fromkeys(matched))


def infer_query_subjects(text: str) -> tuple[str, ...]:
    """Infer one or more subjects; uncertainty intentionally returns empty."""
    compact = _compact(text)
    explicit = list(infer_document_subjects(text))
    scores: dict[str, int] = {subject_id: 3 for subject_id in explicit}
    for subject_id, markers in _QUERY_HINTS.items():
        scores[subject_id] = scores.get(subject_id, 0) + sum(
            1 for marker in markers if _compact(marker) in compact
        )
    ranked = sorted(
        ((score, subject_id) for subject_id, score in scores.items() if score > 0),
        reverse=True,
    )
    if not ranked:
        return ()
    return tuple(subject_id for _, subject_id in ranked[:3])


def valid_subject_ids(values: Iterable[object]) -> tuple[str, ...]:
    output = []
    for value in values:
        normalized = str(value).strip().lower()
        if normalized in _BY_ID and normalized not in output:
            output.append(normalized)
    return tuple(output[:3])


def subject_display_names(subject_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        _BY_ID[item].display_name for item in subject_ids if item in _BY_ID
    )


def _compact(value: str) -> str:
    return re.sub(r"[\s_\-—（）()·]+", "", str(value).lower())
