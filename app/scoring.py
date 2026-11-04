# -*- coding: utf-8 -*-
"""判定核心（Python 侧）—— 与 web/scoring.js 同规则的第二份独立实现。

存在的意义有两个：
  1. 发信前重新计算，不信任客户端提交的结论文本（否则任何人都能伪造一封
     「我圆满之爱」的邮件让服务器代发）。
  2. 作为 JS 实现的对撞 oracle：tools/test_scoring.py 会穷举全部 2^9=512 种
     答案向量，要求两份实现给出完全相同的 typeKey / verdict / 临界维 / 关键题。
     两边同错才会溜过去，所以规则写在这里时要独立照着注释推导，不去"翻译"JS。

规则（与 博客.mp4 博主口径同源）：
  - 三个成分各 3 题，是=1 否=0；维度得分 >= presentThreshold(2) 认定该成分「在场」。
  - 在场组合 -> 八种类型；三维各得 1 分这个向量在 3 题制里不存在，单独归 sprout（难判）。
  - verdict 只有 four 桶：none（无情感连接）/ like（喜欢但不爱）/ love（是爱）/ mixed（难判）。
    博主在 02:41 明确：除「否否否」与「是否否」外，其余六种答案全都是爱，只是形式不同。
"""
from __future__ import annotations

from typing import Any

DIM_ORDER: tuple[str, str, str] = ("intimacy", "passion", "commitment")

# 下标 = I*4 + P*2 + C。注意下标 1 是「只有承诺」（empty），下标 4 才是「只有亲密」（liking）——
# 与 JS 侧那处镜像错误同类的坑，此处独立写死以便两边互验。
TYPE_BY_BITS: tuple[str, ...] = (
    "nonlove",       # 0 000
    "empty",         # 1 I- P- C+
    "infatuation",   # 2 I- P+ C-
    "fatuous",       # 3 I- P+ C+
    "liking",        # 4 I+ P- C-  ★喜欢
    "companionate",  # 5 I+ P- C+
    "romantic",      # 6 I+ P+ C-
    "consummate",    # 7 I+ P+ C+
)

# 按 02:41 的博主口径，只有 nonlove / liking 不是爱。
VERDICT_BY_TYPE: dict[str, str] = {
    "nonlove": "none",
    "liking": "like",
    "infatuation": "love",
    "empty": "love",
    "romantic": "love",
    "companionate": "love",
    "fatuous": "love",
    "consummate": "love",
    "sprout": "mixed",
}

# 展示排序的 tie-break：I > C > P。仅用于展示，不是理论优先级。
DISPLAY_TIE_ORDER: tuple[str, ...] = ("intimacy", "commitment", "passion")


class AnswerError(ValueError):
    """答案不合法 —— 路由层把它翻译成 4xx，不能吞掉。"""


def normalize_rules(data: dict[str, Any]) -> tuple[int, int]:
    rules = data.get("rules") or {}
    threshold = rules.get("presentThreshold", 2)
    per_dim = rules.get("perDim", 3)
    if not isinstance(threshold, int) or not isinstance(per_dim, int):
        raise AnswerError("rules 字段类型错误")
    return threshold, per_dim


def validate_answers(answers: Any, data: dict[str, Any]) -> dict[str, int]:
    """九题缺一不可，值只能是 0 或 1；多余字段忽略。"""
    if not isinstance(answers, dict):
        raise AnswerError("answers 必须是对象")
    out: dict[str, int] = {}
    for q in data.get("questions", []):
        qid = q["id"]
        if qid not in answers:
            raise AnswerError(f"缺少答案：{qid}")
        v = answers[qid]
        # bool 是 int 的子类，这里显式拒绝 True/False，避免语义漂移。
        if isinstance(v, bool) or not isinstance(v, int) or v not in (0, 1):
            raise AnswerError(f"答案只能是 0 或 1：{qid}")
        out[qid] = v
    return out


def dim_scores(answers: dict[str, int], data: dict[str, Any]) -> dict[str, int]:
    scores = {d: 0 for d in DIM_ORDER}
    for q in data.get("questions", []):
        dim = q["dim"]
        if dim not in scores:
            raise AnswerError(f"未知维度：{dim}")
        scores[dim] += answers.get(q["id"], 0)
    return scores


def classify(scores: dict[str, int], threshold: int) -> tuple[str, list[int], dict[str, bool]]:
    bits = [1 if scores[d] >= threshold else 0 for d in DIM_ORDER]
    if all(scores[d] == 1 for d in DIM_ORDER):
        key = "sprout"          # 三维各 1：九题制才出现的中间态，不硬塞进八分法
    else:
        key = TYPE_BY_BITS[bits[0] * 4 + bits[1] * 2 + bits[2]]
    present = {d: bool(b) for d, b in zip(DIM_ORDER, bits)}
    return key, bits, present


def borderline_dims(scores: dict[str, int], threshold: int) -> list[str]:
    """得分正好在阈值或阈值-1 的维度：差一题就翻边，必须如实标出。"""
    return [d for d in DIM_ORDER if scores[d] in (threshold, threshold - 1)]


def key_question_ids(answers: dict[str, int], data: dict[str, Any],
                     threshold: int) -> list[str]:
    """把某题的是/否翻过来重算，结论桶会变 => 该题是关键题。全部九题逐一尝试，不做剪枝。

    sprout 例外：它是全局奇点，翻任意一题都会跳桶，逐题扫描等于 9 题全中，
    「你的结论取决于这几题」就失去了意义，所以按约定返回空列表，
    改由结果页的「逐题回顾」呈现。
    """
    base_key, _, _ = classify(dim_scores(answers, data), threshold)
    if base_key == "sprout":
        return []
    base_verdict = VERDICT_BY_TYPE[base_key]
    hits: list[str] = []
    for q in data.get("questions", []):
        qid = q["id"]
        flipped = dict(answers)
        flipped[qid] = 0 if answers[qid] == 1 else 1
        alt_key, _, _ = classify(dim_scores(flipped, data), threshold)
        if VERDICT_BY_TYPE[alt_key] != base_verdict:
            hits.append(qid)
    return hits


def rank_dims(scores: dict[str, int]) -> list[str]:
    return sorted(DISPLAY_TIE_ORDER, key=lambda d: (-scores[d],
                                                    DISPLAY_TIE_ORDER.index(d)))


def evaluate(answers: Any, data: dict[str, Any]) -> dict[str, Any]:
    """返回结构必须与 web/scoring.js 的 evaluate() 字段名一一对应。"""
    threshold, _per_dim = normalize_rules(data)
    clean = validate_answers(answers, data)
    scores = dim_scores(clean, data)
    key, bits, present = classify(scores, threshold)
    keys = key_question_ids(clean, data, threshold)
    return {
        "version": 1,
        "scores": scores,
        "total": sum(scores[d] for d in DIM_ORDER),
        "present": present,
        "bits": bits,
        "typeKey": key,
        "verdict": VERDICT_BY_TYPE[key],
        "threshold": threshold,
        "borderlineDims": borderline_dims(scores, threshold),
        "keyQuestionIds": keys,
        "confidence": "low" if keys else "normal",
        "dimRank": rank_dims(scores),
    }


def digest(answers: dict[str, int], data: dict[str, Any]) -> str:
    """答案指纹：按 data["questions"] 的题目顺序拼 9 位 01 串。

    顺序取题目数组而不是字典序，是为了让指纹与出题顺序一致 —— 同一个人在同一套题上
    的相同答案总是得到同一个串，用作发信限流的去重键（重复提交同一份答案不该重复发信）。
    """
    return "".join(str(answers[q["id"]]) for q in data.get("questions", []))
