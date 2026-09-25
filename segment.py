"""语义分割（`§C2.2` `§C5.5`）。

--- 本模块只回答一个问题 ---------------------------------------------------

「**这句话包含几个命题**」—— `§C5` 硬约束。
它**不**回答「这句话是真是假」，**不**附带建议、倾向、解读、评价（`§C2.2`），
**不**暗示哪个命题更成立。

--- 不做语义改写（已由需求方当场确认）--------------------------------------

`§C5.4` 允许「措辞可规范化，意义不可变」，`§C5` 的示例把
「现在社会对女性太好了」规范化成了「女性获得了更好的社会待遇」。
但**规则引擎做这种改写必然改意义**，撞 `#2`（AI 自动修改用户原意）。

所以这里的选择是：**一个实词都不动**。
每个命题的 `text` 都是原文的**逐字切片**，`span` 指回原文位置。
所谓「规范化」只剩两件事：去掉首尾空白、去掉附着在边界上的标点。
**没有同义替换，没有词序调整，没有压缩。**
（需求方 2026-09-25 当场指示：不做语义改写。）

--- 可复现（`§C5.5` 硬约束）------------------------------------------------

本模块是**纯函数**：同一个输入永远得到同一个输出。
没有随机、没有时间、没有环境依赖、没有网络。
`§C5.5` 第 1 条要求「同一输入 + 同一模型版本 + 同一提示词版本 → 结果必须一致」——
对规则引擎，对应的两个版本号是 `SEGMENTER_VERSION`（引擎）与 `RULESET_VERSION`（规则集），
两者都会随分割结果一起记进 Artifact，用于解释「为什么当时这样切」（第 5 条）。
"""

from __future__ import annotations

import re

# `§C5.5` 第 5 条要求记录「模型版本 / 提示词版本 / 输入快照」。
# 本引擎没有模型与提示词，对应物是下面两个常量 —— 换规则就必须同时改这两个。
SEGMENTER_VERSION = "rule-segmenter/1"
# zh-clause-2：补上「Y 是因为 X」这一支（需求方样本 0007 暴露的）。
# 换规则必须同时改这个常量 —— §C5.5 第 5 条要的就是「当时为什么这样切」有据可查。
RULESET_VERSION = "zh-clause-2"

# 句末终止符。切在这里，两侧各自成句。
SENTENCE_TERMINATORS = "。！？!?；;"

# 因果连接词。`§C5` 要求「隐含的因果连接词被识别为独立命题时，要让这个识别过程可见」。
# 分两侧：引出「因」的，与引出「果」的。
CAUSE_FIRST = ("因为", "由于", "鉴于")
EFFECT_FIRST = ("所以", "因此", "因而", "于是", "导致", "致使", "从而", "故")

# 命题边界上要剥掉的标点 —— 它们不是命题内容，但会出现在切片的边缘。
_EDGE_PUNCT = "，,、 \t\n\r　"

# 缺口②：「X，所以 Y」有**两种形态**，而两者的字面结构一模一样。
#
#   B1「工作强度太大，所以不愿往上爬」   因果即**主张**：两端都是断言，因果本身是主张
#   D1「朋友加班到十点，所以行业压榨」   因果即**推断**：前件是证据，后件是主张
#
# 判别它们要的是世界知识，不是连接词 —— 「所以」两个字在两边长得一样。
# 规则引擎**没有依据**选，`§T0.3` 也不许它自决。
#
# 所以这里**不选**：把两端都标上「两种都可能」，谁定夺交给确认的人
# （`confirm.resolve_types`）。在此之前它们进不了结构。
# 这是 `§C2.5` 第 1 档（新增断言）到第 2 档（派生标注）之间的一个空白 ——
# 定夺动作在两档里都没有位置，只能由人补。见 DECLARATION §10。
CAUSAL_END_CANDIDATES = ("Claim", "Evidence")


def _trim(text: str, start: int) -> tuple[str, int]:
    """剥掉切片两端的空白与边界标点，返回 (干净文本, 新的起点)。

    ⚠️ 只剥**边界**标点。句子内部的标点保留：
    删中间的东西就是在删内容。
    """
    lo, hi = 0, len(text)
    while lo < hi and text[lo] in _EDGE_PUNCT:
        lo += 1
    while hi > lo and text[hi - 1] in _EDGE_PUNCT:
        hi -= 1
    return text[lo:hi], start + lo


def _find_connective(s: str) -> tuple[int, str, str] | None:
    """在句子里找第一个因果连接词。返回 (位置, 连接词, 它引出的是 cause 还是 effect)。"""
    best = None
    for word in CAUSE_FIRST:
        i = s.find(word)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, word, "cause")
    for word in EFFECT_FIRST:
        i = s.find(word)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, word, "effect")
    return best


def _clauses_of(sentence: str, base: int) -> list[dict]:
    """把一句话切成若干命题片段。(text, span, role, trigger)"""
    hit = _find_connective(sentence)
    if hit is None:
        text, start = _trim(sentence, base)
        if not text:
            return []
        return [{"text": text, "span": [start, start + len(text)],
                 "role": "clause", "why": "由标点切分为独立命题"}]

    pos, word, side = hit
    # 「Y 是因为 X」——「是」是系词，属于因果结构本身，不属于果命题。
    # 不收回来的话，果会变成「他成绩好是」。
    if side == "cause" and pos > 0 and sentence[pos - 1] == "是":
        pos -= 1
        word = "是" + word

    before, after = sentence[:pos], sentence[pos + len(word):]
    after_at = base + pos + len(word)
    tail_src, tail_base = "", after_at

    if side == "effect":
        # X，所以 Y  →  因 = X，果 = Y
        cause_src, cause_base = before, base
        effect_src, effect_base = after, after_at
    elif before.strip():
        # Y 是因为 X —— 连接词不在句首，**它前面那截才是果**。
        # 少了这一支会出两种错：`before` 整个丢掉（真正的果不再是命题），
        # 而 `after` 里逗号之后的下一句被错标成「果」。
        # 需求方样本 0007「他成绩好是因为聪明，不是因为努力」撞到的就是这个。
        effect_src, effect_base = before, base
        cut = re.search(r"[，,]", after)
        if cut is None:
            cause_src, cause_base = after, after_at
        else:
            cause_src, cause_base = after[:cut.start()], after_at
            tail_src, tail_base = after[cut.end():], after_at + cut.end()
    else:
        # 因为 X，Y  →  因 = X，果 = Y（连接词引出的是因）
        cut = re.search(r"[，,]", after)
        if cut is None:
            cause_src, cause_base = after, after_at
            effect_src, effect_base = "", after_at
        else:
            cause_src, cause_base = after[:cut.start()], after_at
            effect_src, effect_base = after[cut.end():], after_at + cut.end()

    cause_text, cause_at = _trim(cause_src, cause_base)
    effect_text, effect_at = _trim(effect_src, effect_base)
    tail_text, tail_at = _trim(tail_src, tail_base)

    out = []
    if cause_text:
        out.append({"text": cause_text, "span": [cause_at, cause_at + len(cause_text)],
                    "role": "cause",
                    "type_candidates": list(CAUSAL_END_CANDIDATES),
                    "why": f"由连接词「{word}」识别出的原因命题"})
    if effect_text:
        out.append({"text": effect_text, "span": [effect_at, effect_at + len(effect_text)],
                    "role": "effect",
                    "type_candidates": list(CAUSAL_END_CANDIDATES),
                    "why": f"由连接词「{word}」识别出的结果命题"})
    # 连接词本身成为一个**独立命题**：它断言「因导致果」。
    # `§C5`：这个识别过程必须可见 —— 所以它有 span、有 trigger、有 why。
    if cause_text and effect_text:
        out.append({"text": word, "span": [base + pos, base + pos + len(word)],
                    "role": "causal", "trigger": word,
                    "why": f"「{word}」被识别为独立命题：它断言前件导致后件"})
    # 连接词所在子句之后、逗号之外的下一句。它和因果无关，是一句独立的话 ——
    # 不能挂到「果」上（那正是 0007 的老毛病）。
    if tail_text:
        out.append({"text": tail_text, "span": [tail_at, tail_at + len(tail_text)],
                    "role": "clause",
                    "why": f"「{word}」所在子句之后的独立命题"})
    return out


def _split_sentences(text: str) -> list[tuple[str, int]]:
    """按句末终止符切句，返回 (句子, 起点)。终止符本身不属于任何句子。"""
    out, start = [], 0
    for i, ch in enumerate(text):
        if ch in SENTENCE_TERMINATORS:
            out.append((text[start:i], start))
            start = i + 1
    if start < len(text):
        out.append((text[start:], start))
    return out


def segment(text: str) -> dict:
    """把一段自然语言切成若干独立命题。

    返回结构：
        input_snapshot   原文，原样（`§C5.4`：原文本不可修改）
        propositions     被切出的命题，每个都带原文 span
        unused_spans     未被切出的部分（`§C5.2`：不得静默丢弃）
        coverage         未采用字符占比 —— 只记数，**不判高低**（`§C7.2`）
    """
    props: list[dict] = []
    for sentence, base in _split_sentences(text):
        props.extend(_clauses_of(sentence, base))

    used = [False] * len(text)
    for p in props:
        for i in range(p["span"][0], p["span"][1]):
            used[i] = True

    unused, run_start = [], None
    for i, u in enumerate(used):
        if not u and run_start is None:
            run_start = i
        elif u and run_start is not None:
            unused.append({"text": text[run_start:i], "span": [run_start, i]})
            run_start = None
    if run_start is not None:
        unused.append({"text": text[run_start:], "span": [run_start, len(text)]})

    total = sum(1 for c in text if not c.isspace())
    missed = sum(1 for i, c in enumerate(text) if not used[i] and not c.isspace())
    coverage = (missed / total) if total else 0.0

    for n, p in enumerate(props, 1):
        p["key"] = chr(ord("A") + n - 1)   # A / B / C …

    return {
        "input_snapshot": text,
        "segmenter_version": SEGMENTER_VERSION,
        "ruleset_version": RULESET_VERSION,
        "propositions": props,
        "unused_spans": unused,
        "unused_char_ratio": coverage,
        # 分子与分母**原样留下**，不只留比值（`§C7.2`：只记怎么算）。
        # 上层派生视图要能把多份 draft 的分数正确相加 —— 比值做不到这件事。
        # 只加计数、不改切法，所以规则集版本号不动。
        "unused_char_count": missed,
        "total_char_count": total,
    }
