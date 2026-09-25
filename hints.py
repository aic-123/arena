"""`§C2.1`「提示可能的子问题」—— **只发现，不提出**。

`§C2.0`：群众拥有提出和展开议题的权力；系统负责发现并提示。
**系统不得代替用户提出议题。**

所以本模块**不生成任何问题**，一个新问题都不生成。它只做一件事：

    在用户自己的原文里，把用户**已经写出**的问题、
    和用户**已经点名**的判断依赖维度，指出来，连位置一起交回。

    ┌ 用户写：大家都在争该不该加班，但真正的问题是：加班费算不算劳动报酬。
    │ 本模块：  ↑ 这一段是**你自己**提出的问题，位置 [14, 45]。提不提成议题，你定。
    └ 本模块**不是**：建议你讨论「加班费算不算劳动报酬」——
                      那叫替用户提议题，`§C2.0` 禁止。

--- 为什么不能挂在 segment.py 上 -------------------------------------------

`§C2.2` 明文：「提示可能的子问题」「发现相似 / 冲突」属于**后续**环节，
**不得混入分割输出**。分割环节只回答「这句话包含几个命题」，附带建议就是违规。

所以它是**独立的只读派生视图**，与分割器互不调用。

--- 什么时候生效（`§C2.5` 分层）---------------------------------------------

这是**派生视图**（那一档里点名的「推荐候选」），不是派生标注：

* 它没有对已有内容作出任何断言 —— 只把用户的原文指回给用户；
* 它没有新建任何对象 —— 不建 Artifact、不写库、不产生关系。

→ 默认生效 + 可追溯 + 可解释 + 可回退 + **留版本**，**不需确认**。
「可回退 + 留版本」落在 `RULESET_VERSION`：**换规则必须改版本号**，
这样新旧提示不会长得一样（`§C5.5` 第 5 条对分割器也是这么要求的）。

--- 本模块不做 ---------------------------------------------------------------

不建 Artifact、不写库、不排序、不加权、不打分、不设阈值、不判断哪条更重要。
返回顺序**只有一种：原文位置**。位置不是名次（`§C6.1` `§C9` #5 #7）。

**也不为了凑内容而拟合。** 一段文字里没有可以进入结构的东西，
是一个**正当结果**，照实说就行 —— 见 `NOTHING_SAID`。

--- 空结果的两种含义，必须分开说 ---------------------------------------------

`find_hints()` 返回 `[]` 时，有两种可能，而**它们印出来一模一样**：

    确实没有   ——  这段文字里用户就是没写问题
    认不出来   ——  用户写了，但本规则集看不见

只给一个空列表，用户分不出是哪种。所以对外用的是 `scan()`：
它永远附一句话，说清扫了什么、结果是什么、**认不出哪几类写法**（`BLIND_SPOTS`）。

这与 `observe.py` 的「算不出」是同一条规矩：
**没测到的东西，不许长得像测到了零。**
"""

from __future__ import annotations

import re

HINTER_VERSION = "hint-subquestion/1"
RULESET_VERSION = "zh-meta-1"

# 元话语标记 —— 全部是**用户自己写出来的词**，机器只是把它们指出来。
# 顺序即优先级：同一位置多个标记命中时，**先列的赢**（`真正的问题是` 盖过 `问题是`）。
#
# (标记, 类别, 为什么算一条提示)
MARKERS = (
    ("真正的问题是", "question", "你自己点名了「真正的问题」"),
    ("问题是", "question", "你自己提出了一个问题"),
    ("没人问", "question", "你自己指出了「没人问」的那个问题"),
    ("问的是", "question", "你自己提出了一个问题"),
    ("关键在于", "dimension", "你自己点出了关键分歧点"),
    ("取决于", "dimension", "你自己点出了一个判断依赖的维度"),
)

_SENTENCE_END = re.compile(r"[。！？；\n]")
_LEAD = "：:，,、 \t"

# 本规则集**认不出**的写法。列出来是因为「0 条」有两种含义，必须分开：
#
#   确实没有  ——  这段文字里用户就是没写出问题
#   认不出来  ——  用户写了，但本规则集看不见
#
# 不列出来的话，两者都印成「0 条」，而**看起来一模一样**。
# 这和 `observe.py` 的「算不出」、和空转的检查打印「过」是同一个病。
BLIND_SPOTS = (
    "反问 —— 「他连这个都不懂，怎么可能是专家。」分辨反问与真提问要语义判断",
    "隐含前提 —— 「既然大家都在考研，那我也得考。」里的前提是暗的",
    "触发词表以外的说法 —— 表见 MARKERS，表长大要改 RULESET_VERSION",
)

NOTHING_SAID = (
    "这段文字里没有**你自己已经写出**的问题，也没有你已点名的依赖维度。"
    "**这不等于这段文字没有内容** —— 它等于：现在没有可以进入结构的东西。"
    "不需要为了填满而拟合出一条提示。"
)

SOMETHING_SAID = (
    "下面每一条都是**你自己**写出来的，位置已标出。"
    "提不提成议题由你定 —— 系统只负责把它们指回给你。"
)


def _sentence_end(text: str, pos: int) -> int:
    m = _SENTENCE_END.search(text, pos)
    return len(text) if m is None else m.start() + 1


def _overlaps(a: dict, b: dict) -> bool:
    return not (a["span"][1] <= b["span"][0] or b["span"][1] <= a["span"][0])


def find_hints(text: str) -> list[dict]:
    """指出原文里**用户自己已经写出**的问题与依赖维度。

    每项：`kind` / `marker` / `why`（可解释）/ `quoted`（原文逐字，可追溯）/ `span`。
    返回按原文位置排序的列表。没有就返回空列表 —— **空是合法的**（`§C5.3`）。
    """
    if not text:
        return []

    # 一、元话语标记。它们比标点更具体，所以先收，且**优先占位**。
    marked = []
    for start in range(len(text)):
        for marker, kind, why in MARKERS:
            if not text.startswith(marker, start):
                continue
            end = _sentence_end(text, start)
            quoted = text[start + len(marker):end].strip(_LEAD).strip()
            marked.append({
                "kind": kind, "marker": marker, "why": why,
                "quoted": quoted or text[start:end].strip(),
                "span": [start, end],
            })
            break   # 同一位置只认一个标记：先列的赢
    kept = _drop_overlaps(marked)

    # 二、原文里本来就带问号的整句。**已被标记覆盖的整句不再单列**。
    for m in re.finditer(r"[？?]", text):
        start = m.start()
        while start > 0 and text[start - 1] not in "。！？；\n":
            start -= 1
        q = {
            "kind": "question", "marker": "？", "why": "你自己写了一个问句",
            "quoted": text[start:m.start()].strip(),
            "span": [start, m.start() + 1],
        }
        if not any(_overlaps(q, k) for k in kept):
            kept.append(q)

    kept.sort(key=lambda d: d["span"][0])
    return kept


def scan(text: str) -> dict:
    """`find_hints` 的完整答复 —— **连「什么都没找到」也一起说明白**。

    只返回一个列表的话，`[]` 有两种读法：**确实没有** / **根本没跑**。
    两者长得一模一样（`observe.py` 的 `0 / 0` 也是这个病）。
    所以这里永远给出一句话，说清这一趟扫了什么、结果是什么、
    以及**本规则集认不出哪几类写法**。

    这一趟不改任何东西 —— 不建 Artifact、不写库、不留事件。
    """
    hits = find_hints(text)
    return {
        "hinter": HINTER_VERSION,
        "ruleset": RULESET_VERSION,
        "examined_chars": len(text or ""),
        "hints": hits,
        "note": SOMETHING_SAID if hits else NOTHING_SAID,
        "blind_spots": list(BLIND_SPOTS),
    }


def _drop_overlaps(items: list[dict]) -> list[dict]:
    """互相重叠的标记合成一条。

    例：`真正的问题是` 与 `问题是` 会同时命中同一处 —— 那是**一条**提示。
    同起点时长的在前（外层盖住内层），所以先按 (起点, -终点) 排。
    """
    kept: list[dict] = []
    for it in sorted(items, key=lambda d: (d["span"][0], -d["span"][1])):
        if not any(_overlaps(it, k) for k in kept):
            kept.append(it)
    return kept
