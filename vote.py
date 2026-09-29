"""Arena 层 —— 投票（`§T2` 第 09 步，`§C12`）。

`§C12.1` 一句话定死了它的地位：

> 投票记录的是**公共偏好 / 公共判断**。**不是真理判定。**

本模块从头到尾没有「谁更正确」这个量，也没有任何算子能把它推出来。

--- `§C12.2`：四个量必须分离，且**永不给它们之间加算子** -----------------------

`§C12.2` 要求在界面上分开放：

    Public Preference   公众倾向：A 61% / B 39%
    Argument Support    论点支持：A 4/6 claims 有支持材料
    Evidence Status     证据状态：3 verified / 3 unresolved
    Main Dispute        主要争议：Premise P1

并明文禁止呈现成 `A 61% → A 更正确`。

所以 `tally()` 返回的四个量之间**没有任何把它们连起来的字段** ——
没有合计、没有总分、没有结论、没有排序。它们只是并排躺着。

**「主要争议」这一项我没有实现**：判定「哪一条算主要」是一次判断，
`§T0.3` 要我先问你。代替物是 `disputed` —— **全部**有质询的 Claim，
一条不略、**不排名**，谁主要你自己看。见 DECLARATION §6.0.2。

--- `§C12.5`：旧票属于**旧问题版本** ------------------------------------------

    Q(v1) 上投的票  ≠  Q(v2) 上投的票

所以公众倾向**按 `question_version` 分组**，各组**绝不跨版本相加**。
这正是「禁止自动分裂旧票」要防的事：

    原始：      A = 61%   B = 39%
    A 分裂为：  A1 + A2
    禁止推导：  A1 = 30%   A2 = 31%      ← 本模块做不出这件事，结构上做不出

--- `§C12.4`：结构继承 ≠ 判断继承 ---------------------------------------------

`A1 derived_from A` 允许；`A1 inherits votes from A` 禁止。
本模块按 `(topic, question_version)` 取票，新结构是**新 context**，
所以继承在结构上不可能发生。

--- 本模块不做 ---------------------------------------------------------------

不排序、不加权、不设阈值、不预占比、不分「主要」、不显示投票人。

--- 折线图（`chart()`，2026-09-25 需求方加的）--------------------------------

票数随时间怎么长，画成**终端 Braille 文本图**（零依赖，见下面「为什么是 Braille」）。
它同样是**只读派生视图**，上面那些「不做」一条不少 —— 具体靠四件事兜住：
**一个 `question_version` 一张图**（不跨版本）、**派生子论点另开一张**（不混层级）、
图例**按 id 排且永远带数**（没有名次）、页首页尾各说一次「这是偏好计数，不是对错」。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

from scaffold import (
    ScaffoldError,
    activate,
    add_artifact,
    add_relation,
    content_of,
    get,
    heads_of,
    original_content_of,
    record_event,
)

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# `§C12.3` 要求随票一起记录的观测条件。
# `question_version` / `argument_version` 由 `vote_context()` 抓（见那边）；
# `timestamp` 就是票自己的 `created_at`。剩下三项**必须由调用方显式给出**。
#
# 为什么**不给默认值**：给了默认值，忘了传和"确实是那个值"就分不出来了 ——
# 而 `§C12.3` 那句「投票结果本身会影响后续投票」的前提就是这三个量**记准**。
# 不知道就显式传 `None`：那是"不知道"，不是"没有"。
CONDITION_FIELDS = ("sampling", "prior_results_visible", "repeat_participation")


def _votable_options(conn: sqlite3.Connection, topic_id: str) -> list:
    """这个 Topic 上可投的选项。

    **优先用立场（`§C4` 的 `Position`）**，没有立场才回退到 Claim。
    需求方 2026-09-27：「投一张票投的是两个 Position」——
    投立场才是真正的二元对立；投某一句具体的话，投的是论据，不是立场。

    回退那一条**不能删**：既有数据（Topic 直挂 Claim）没有立场节点，
    那些 Topic 照样要能投。这也让本层是**增量**而非破坏性改动。

    ⚠️ **不假设只有两个。** `§C12.5` 明文「数据结构**禁止写死二元**」，
    要能承载 `A / B / C / D`。这里只做「取全部」，一个字都没写死个数。
    """
    positions = conn.execute(
        "SELECT a.id FROM relation r JOIN artifact a ON a.id = r.to_id"
        " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
        "   AND a.type = 'Position' ORDER BY a.id",
        (topic_id,),
    ).fetchall()
    if positions:
        return [p["id"] for p in positions]
    claims = conn.execute(
        "SELECT a.id FROM relation r JOIN artifact a ON a.id = r.to_id"
        " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
        "   AND a.type = 'Claim' ORDER BY a.id",
        (topic_id,),
    ).fetchall()
    return [c["id"] for c in claims]


def vote_context(conn: sqlite3.Connection, topic_id: str) -> dict:
    """**渲染那一刻**的问题版本与论点版本（`§C12.3`）。

    要在**渲染页面时**抓一次、随页面交给用户，提交时**原样交回**。
    不要等到提交时再抓 —— 用户打开页面之后别人可能改过，
    那样记下来的就不是「他看到的那一版」了，而 `§C12.3` 要的正是后者。

    问题已分叉时**没有「当前版本」**（`§C10`：系统不替你挑分支），
    所以这里照实给出 `heads`，由 `cast_vote()` 拒绝投票并说明原因。
    """
    heads = heads_of(conn, topic_id)
    options = _votable_options(conn, topic_id)
    return {
        "topic": topic_id,
        "question_version": heads[0]["id"] if len(heads) == 1 else None,
        "question_heads": [h["id"] for h in heads],
        "argument_version": {
            o: (heads_of(conn, o)[0]["id"]
                if len(heads_of(conn, o)) == 1 else None)
            for o in options
        },
    }


def cast_vote(
    conn: sqlite3.Connection, *,
    topic_id: str, choice: str, by: str, seen: dict,
    sampling, prior_results_visible, repeat_participation,
) -> str:
    """投一票。`choice` 是这张票选的**那个选项的 Artifact id**。

    `seen` 是 `vote_context()` 在渲染时抓的那一份，**原样交回**。
    这里把它整份存进票里 —— `§C12.3`：「Vote Result 与 Observation Condition
    必须一起记录」。

    `§C12.5`：数据结构**不写死二元**。`choice` 是任意一个选项 id，
    所以 `A / B / C / D` 与 `A1 / A2 / A3` 都承载得了 —— 没有任何一处假设只有两个。
    """
    if not seen or seen.get("topic") != topic_id:
        raise ScaffoldError(
            "票必须带上渲染时抓的 vote_context()（`§C12.3`："
            "Vote Result 与 Observation Condition 一起记录）。"
        )
    if seen.get("question_version") is None:
        raise ScaffoldError(
            f"问题 {topic_id} 有 {len(seen.get('question_heads', []))} 个分支 "
            f"{seen.get('question_heads')}，没有「当前版本」可投。"
            "系统不替你挑一个分支（`§C10`）。先让它收敛，或按分支各投一次。"
        )
    options = seen.get("argument_version") or {}
    if choice not in options:
        raise ScaffoldError(
            f"{choice} 不在这张票所见的选项里（{sorted(options)}）。"
            "投票只能针对**你看到的那一版**里的选项（`§C12.5`：旧票属于旧问题版本）。"
        )
    if options[choice] is None:
        raise ScaffoldError(f"选项 {choice} 在所见版本里是分叉的，同样不替你挑。")

    condition = {"sampling": sampling,
                 "prior_results_visible": prior_results_visible,
                 "repeat_participation": repeat_participation}
    missing = [k for k in CONDITION_FIELDS if k not in condition]
    if missing:
        raise ScaffoldError(f"缺观测条件：{missing}（`§C12.3`）")

    vote_id = add_artifact(
        conn, type_="Vote",
        content={
            "choice": choice,
            "question_version": seen["question_version"],
            "argument_version": options,
            **{k: condition[k] for k in CONDITION_FIELDS},
        },
        origin=by,
    )
    activate(conn, vote_id, by=by)
    # `§C4` 的树：Vote 挂在 Topic 下。这条边让「这个议题投过什么票」可追踪。
    add_relation(conn, kind="contains", from_id=topic_id, to_id=vote_id, origin=by)
    record_event(conn, "vote_cast", by, vote_id,
                 {"topic": topic_id, "question_version": seen["question_version"]})
    conn.commit()
    return vote_id


def tally(conn: sqlite3.Connection, topic_id: str) -> dict:
    """`§C12.2` 的四个量，**分开给，永不合并**。只读。

    公众倾向**按 `question_version` 分组**（`§C12.5`），各组不跨版本相加。
    一个议题通常只有一组；有多组时那意味着问题改过版 —— 那两组**不是同一件事**，
    加在一起就是「自动分裂旧票」。
    """
    votes = conn.execute(
        "SELECT v.id FROM relation r JOIN artifact v ON v.id = r.to_id"
        " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
        "   AND v.type = 'Vote' ORDER BY v.id",
        (topic_id,),
    ).fetchall()

    groups: dict = {}
    for v in votes:
        c = content_of(conn, v["id"])
        g = groups.setdefault(c["question_version"], {"options": {}, "n": 0})
        g["options"][c["choice"]] = g["options"].get(c["choice"], 0) + 1
        g["n"] += 1

    return {
        "public_preference": [
            {
                "question_version": ver,
                "options": {k: g["options"][k] for k in sorted(g["options"])},
                "n": g["n"],
                "note": "这是**偏好计数**，不是对错（`§C12.1`）。"
                        "它只属于问题第 %s 版；别的版本上的票不并入这里。" % ver,
            }
            for ver, g in sorted(groups.items())
        ],
        "argument_support": _argument_support(conn, topic_id),
        "evidence_status": _evidence_status(conn),
        "disputed": _disputed(conn, topic_id),
        "note": (
            "上面四项**是四件事，不许合成一句话**（`§C12.2`）。"
            "尤其：公众倾向高**不等于**更正确。"
        ),
    }


def _claims_of(conn: sqlite3.Connection, topic_id: str) -> list[str]:
    return [r["id"] for r in conn.execute(
        "SELECT a.id FROM relation r JOIN artifact a ON a.id = r.to_id"
        " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
        "   AND a.type = 'Claim' ORDER BY a.id",
        (topic_id,),
    ).fetchall()]


def _argument_support(conn: sqlite3.Connection, topic_id: str) -> dict:
    """论点支持：每个 Claim **各有多少**支持 / 限定 / 反驳材料。

    三种**分列**，不合成一个数（`§C6.3` / `§C4`：不可归约）。
    这里不给「有无支持材料」的布尔值 —— 那是个判断（几条才算有？），
    要判断就得定阈值，`§T0.3` 不许我定。
    """
    out = {}
    for cid in _claims_of(conn, topic_id):
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS n FROM relation"
            " WHERE to_id = ? AND state = 'active'"
            "   AND kind IN ('supports','contradicts','qualifies')"
            " GROUP BY kind",
            (cid,),
        ).fetchall()
        got = {r["kind"]: r["n"] for r in rows}
        out[cid] = {k: got.get(k, 0)
                    for k in ("supports", "contradicts", "qualifies")}
    return out


def _evidence_status(conn: sqlite3.Connection) -> dict:
    """证据状态：`unresolved` / `verified` 各几条（`§C12.2` 的第三项）。

    `verified` 只有钩子能给（`§C9` #3），所以这个计数**机器推不动**。
    """
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM artifact WHERE type = 'Evidence'"
        " GROUP BY status ORDER BY status",
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def _disputed(conn: sqlite3.Connection, topic_id: str) -> list[dict]:
    """**全部**有质询的 Claim，一条不略、**不排名**。

    这是 `§C12.2`「主要争议」那一项的位置，但我**不判断哪个是主要的** ——
    判定「主要」需要一次判断，`§T0.3` 要我先问你。所以这里给的是原料：
    谁被质询了、质询说的是什么、它依赖哪些前提。谁主要，你看。

    排序规则只有一条：**按 id**。不是按质询条数 —— 那是名次。
    """
    out = []
    for cid in _claims_of(conn, topic_id):
        challenges = conn.execute(
            "SELECT a.id, a.type FROM relation r JOIN artifact a ON a.id = r.to_id"
            " WHERE r.from_id = ? AND r.kind = 'challenged_by' AND r.state = 'active'"
            " ORDER BY a.id",
            (cid,),
        ).fetchall()
        if not challenges:
            continue
        premises = conn.execute(
            "SELECT a.id FROM relation r JOIN artifact a ON a.id = r.to_id"
            " WHERE r.from_id = ? AND r.kind = 'assumes' AND r.state = 'active'"
            " ORDER BY a.id",
            (cid,),
        ).fetchall()
        out.append({
            "claim": cid,
            "challenged_by": [c["id"] for c in challenges],
            "assumes": [p["id"] for p in premises],
        })
    return out


def show(conn: sqlite3.Connection, topic_id: str) -> str:
    """把 `tally()` 印成人看的。**四个量分段，中间没有任何箭头。**"""
    t = tally(conn, topic_id)
    lines = ["=" * 60, "§C12.1：下面是**公共偏好**，不是真理判定。", "=" * 60, ""]

    lines.append("【公众倾向】")
    if not t["public_preference"]:
        lines.append("  还没有票。**票数为零不等于倾向为零**，就是还没有票。")
    for g in t["public_preference"]:
        lines.append(f"  问题第 {g['question_version']} 版（共 {g['n']} 票）")
        for opt, n in g["options"].items():
            lines.append(f"    {opt}  {n} 票")
    lines.append("")

    lines.append("【论点支持】三种分开计，不合成一个数")
    for cid, got in t["argument_support"].items():
        lines.append(f"  {cid}  支持 {got['supports']} / 限定 {got['qualifies']}"
                     f" / 反驳 {got['contradicts']}")
    lines.append("")

    lines.append("【证据状态】")
    if not t["evidence_status"]:
        lines.append("  还没有证据。")
    for st, n in t["evidence_status"].items():
        lines.append(f"  {st}  {n}")
    lines.append("")

    lines.append("【被质询的 Claim】**全部列出，不排名**")
    if not t["disputed"]:
        lines.append("  没有 Claim 被质询。")
    for d in t["disputed"]:
        lines.append(f"  {d['claim']}  质询 {len(d['challenged_by'])} 条"
                     f"  依赖前提 {d['assumes']}")
    lines.append("")
    lines.append("⚠️ " + t["note"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 折线图 —— 「公众倾向」那一项的**派生视图**（`§C2.5` 第 3 档）
# ---------------------------------------------------------------------------
#
# 这一节画的是「票数随时间怎么长」。它是**只读派生视图**：不建对象、不写库、
# 不记事件（和 `hints.scan()` / `observe.snapshot()` 一个规矩）。
#
# --- 为什么是 Braille（2026-09-25，需求方选了「终端文本图」）-----------------
#
# 一个 Braille 字符（U+2800 区）是 **2×4 个可独立寻址的点**，所以一格终端能放
# 8 个「像素」：80×20 的终端就是 160×80 的位图。这是终端里画折线图的成熟做法
# （`plotille` / `uniplot` / `brailleplot` 都是这个路子），而且**零依赖** ——
# 只要字体有 U+2800 区（现代终端字体基本都有；看到方块是字体的问题）。
#
# 点号 → 位值 → 行列 的对照（Unicode 里 8 点单元的实际排布）：
#
#     (1) (4)      0x01 0x08
#     (2) (5)      0x02 0x10
#     (3) (6)      0x04 0x20
#     (7) (8)      0x40 0x80
#
# **这张表写错一位，整张图就是歪的，而且歪得很像「数据就是这样」。**
# 所以它有一条专门的用例钉着（`test_arena.py` 的 `TestBrailleCanvas`）。
#
# --- 两条已知的代价（都是这套画法的固有限制，不是我漏了）------------------
#
# 1. **Braille 字符不带颜色。** 叠在一起的两条线在**字符层面**就是合并的 ——
#    所以颜色只用来「追线」，可读的那部分永远是图例里的数（见 `_one_chart`）。
#    需求方 2026-09-25 选了「叠一张图 + ANSI 颜色」；颜色在重定向到文件时按
#    约定关掉（`NO_COLOR`），那时候图例就是唯一能读的东西 —— 所以图例**永远**
#    带数，不做成「有颜色才显示」。
# 2. **一格里的点来自两条线时，先画的那条保留颜色。** 画序按 id（不按票数），
#    所以交叉点归谁是可以预期的。这是有损的合并，`brailleplot` 的 README
#    也把这一条写在明面上。

# 前景色码。**不用 31/32 起头**是有意的：红绿在中文语境里容易被读成涨跌，
# 而这里画的是偏好计数，不是行情。前六个是标准 16 色里的非红绿项。
_PALETTE = ("36", "33", "35", "34", "96", "93", "95", "94",
            "31", "32", "91", "92")
_RESET = "\033[0m"

# Braille 的 2×4 点阵：`_BRAILLE[行][列]` → 位值。
_BRAILLE = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)
_BRAILLE_BLANK = "\u2800"


def _paint(text: str, code: str | None, on: bool) -> str:
    return f"\033[{code}m{text}{_RESET}" if (on and code) else text


def _colour_on(explicit: bool | None) -> bool:
    """上不上色。`explicit` 给定时就用它，否则按环境判断。

    判据照 `NO_COLOR`（no-color.org）那条通行约定：**只要 `NO_COLOR` 被设成
    非空就不上色**，不管它是什么值（设成空串视为没设，这也是约定的一部分）。
    `FORCE_COLOR` 优先于它 —— 那是「CI 里也要颜色」的那一半。
    都不是，就看 stdout 是不是终端：重定向到文件时颜色只是噪声。
    """
    if explicit is not None:
        return explicit
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


class _Canvas:
    """Braille 点阵。坐标以**点**为单位，原点**左上** —— 和 Braille 的实际排布一致。

    `plotille` 那类库用的是原点左下（数学坐标系），这里不跟：
    左上原点和「字符的第几行」同向，`rows_of()` 就不用再翻一次。
    """

    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        # (格列, 格行) -> [位值, 颜色码]。**颜色码是第一个写这一格的那条线**。
        self._cells: dict[tuple[int, int], list] = {}

    def set(self, x: int, y: int, code: str | None = None) -> None:
        """点一个点。**出界忽略，不抛** —— 插值走到边界外是常事。"""
        if not (0 <= x < self.cols * 2 and 0 <= y < self.rows * 4):
            return
        cell = self._cells.setdefault((x // 2, y // 4), [0, code])
        cell[0] |= _BRAILLE[y % 4][x % 2]

    def line(self, x0: int, y0: int, x1: int, y1: int, code: str | None = None) -> None:
        """Bresenham。**不采样** —— 采样会让近垂直线断成一列虚线。"""
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.set(x0, y0, code)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def rows_of(self, *, colour: bool) -> list[str]:
        out = []
        for cy in range(self.rows):
            row = []
            for cx in range(self.cols):
                cell = self._cells.get((cx, cy))
                if cell is None:
                    row.append(_BRAILLE_BLANK)
                else:
                    row.append(_paint(chr(0x2800 + cell[0]), cell[1], colour))
            out.append("".join(row))
        return out


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, TS_FORMAT).replace(tzinfo=timezone.utc)


def _votes(conn: sqlite3.Connection, topic_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT v.id, v.created_at FROM relation r JOIN artifact v ON v.id = r.to_id"
        " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
        "   AND v.type = 'Vote' ORDER BY v.id",
        (topic_id,),
    ).fetchall()
    out = []
    for r in rows:
        c = content_of(conn, r["id"])
        out.append({"id": r["id"], "at": r["created_at"],
                    "choice": c["choice"], "version": c["question_version"]})
    return out


def _claims_and_children(
    conn: sqlite3.Connection, topic_id: str,
) -> tuple[list[str], list[str], dict[str, str]]:
    """本 Topic 下的 Claim，分成「当前层级」与「派生的子论点」。

    子论点的判据是**有出边 `derived_from`**。方向别记反了：
    `add_relation(kind="derived_from", from_id=子, to_id=父)` —— 子指向父。

    **只认 active 的边**：被推翻的 `derived_from` 不算数（`§C2.4`：可拒绝）。
    返回 `(当前层级, 子论点, 子→父)`，两个列表都**按 id 升序**。
    """
    rows = conn.execute(
        "SELECT a.id FROM relation r JOIN artifact a ON a.id = r.to_id"
        " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
        "   AND a.type = 'Claim' ORDER BY a.id",
        (topic_id,),
    ).fetchall()
    parents = {
        r["from_id"]: r["to_id"] for r in conn.execute(
            "SELECT from_id, to_id FROM relation"
            " WHERE kind = 'derived_from' AND state = 'active' ORDER BY id")
    }
    top, kids = [], []
    for r in rows:
        (kids if r["id"] in parents else top).append(r["id"])
    return top, kids, parents


def _label_of(conn: sqlite3.Connection, claim_id: str, *, room: int = 22) -> str:
    """图例里那一行的人话。

    读 `original_content_of()` 而**不是** `content_of()`：前者是用户当初提交的
    那一段（`§C2.2.1` 原文本不可修改），而且**分叉时也读得到** ——
    `content_of()` 在分叉时会拒绝，图例不该因为有人并发改过就整张图出不来。
    """
    text = original_content_of(conn, claim_id).get("text", "")
    return text if len(text) <= room else text[:room] + "…"


def _question_at(conn: sqlite3.Connection, rev_id, *, room: int = 20) -> str:
    """某个 `question_version` 当时问的是什么。

    `question_version` 是**问题那次修订的 revision 行号**（`vote_context()` 取
    `heads_of(topic)[0]["id"]`）。只印一个行号读者读不出这是哪一版，
    所以这里把那一版的原话也带出来 —— 图属于哪个问题，得在图上看得到。

    行号在库里找不到时**照实说找不到**，不印一个空引号（那和「问题就是空的」
    长得一样）。
    """
    row = conn.execute("SELECT content FROM revision WHERE id = ?",
                       (rev_id,)).fetchone()
    if row is None:
        return "（库里没有这一版 —— 行号对不上）"
    text = json.loads(row["content"]).get("text", "")
    return text if len(text) <= room else text[:room] + "…"


def _legend(conn: sqlite3.Connection, series: list, codes: list[str],
            colour: bool) -> list[str]:
    """图例。**永远带数** —— 颜色只是用来追线，可读的那部分在这里。

    Braille 字符不带颜色，所以重定向到文件（颜色按约定关掉）之后，
    几条线在字符层面就是分不开的。**所以图例不做成「有颜色才显示」** ——
    那样一来那份输出里就只剩一团糊，而它看上去还是一张正常的图。
    """
    out = ["   图例（**按 id 排，不按票数**）—— "
           "颜色只用来追线，可读的那部分是下面的数："]
    for (o, c), code in zip(series, codes):
        out.append(f"     {_paint('██', code, colour)} {o}"
                   f"  「{_label_of(conn, o)}」   最终 {c} 票")
    if not colour:
        out.append("   ⚠️ 本输出没有颜色（不是终端，或 `NO_COLOR` 已设）——"
                   "上面那几条线在字符层面**分不开**，请读图例里的数。")
    return out


def _one_chart(
    conn: sqlite3.Connection, title: str, group: list[dict], options: list[str], *,
    ver, colour: bool, width: int, height: int, note: str = "", rest: str = "",
) -> list[str]:
    """一张图。`group` 是**同一个 `question_version`** 的票（按 id 升序）。

    一次只画 `options` 里那些论点 —— 当前层级一张，每个派生子论点各一张。
    `rest` 说明「本图之外的那些票去哪了」：主图和子图不一样，所以由调用方给。
    """
    head = [f"── {title}" + (f"    （{note}）" if note else ""),
            f"   问题版本 revision #{ver}「{_question_at(conn, ver)}」"]
    mine = [v for v in group if v["choice"] in options]
    if not mine:
        # 不画空坐标系。一张 Y 轴全零的图，和「这个论点一票都没有」
        # 在眼睛看来是两件事，而后者才是事实（同 `observe` 的「算不出」）。
        head.append("   这一段里**没有票** —— 不画空坐标系。")
        return head + [""]

    n = len(mine)
    head.append(f"   共 {n} 票" + (f"（{rest}）" if rest else ""))
    codes = [_PALETTE[i % len(_PALETTE)] for i in range(len(options))]
    counts = {o: sum(1 for v in mine if v["choice"] == o) for o in options}
    legend = _legend(conn, [(o, counts[o]) for o in options], codes, colour)

    if n == 1:
        # 一个点不是折线。画出来只会让人以为「票数就是这样」—— 和「没有票」
        # 同一个规矩：画不出来的东西就说画不出来，不拿一个近似品顶上。
        head.append("   只有 1 票 —— **画不出折线**（至少两票才有线）。"
                    "票数见下面的图例。")
        return head + [""] + legend + [""]

    times = [_parse(v["at"]) for v in mine]
    span = (times[-1] - times[0]).total_seconds()
    if span:
        def xdot(k: int, v: dict) -> int:
            return round((_parse(v["at"]) - times[0]).total_seconds()
                         / span * (width * 2 - 1))
        x_note = (f"横轴 = 时间：{mine[0]['at']} → {mine[-1]['at']}（{span:.3f} 秒）")
    else:
        def xdot(k: int, v: dict) -> int:
            return round(k / (n - 1) * (width * 2 - 1))
        x_note = (f"⚠️ 这 {n} 票都在同一时刻（{mine[0]['at']}）——"
                  " 横轴**退化成票序号**，不是时间。")

    # 每个论点的累计序列：在**每一个**投票时刻都取一次值，
    # 所以几条线共用同一条时间轴（不这么做的话，某条线只在它自己那些时刻有点，
    # 折线会把「两票之间」画成斜的，读出来像是票数在连续变化）。
    series = []
    for o in options:
        c = 0
        pts = []
        for k, v in enumerate(mine):
            if v["choice"] == o:
                c += 1
            pts.append((xdot(k, v), c))
        series.append((o, c, pts))
    top_count = max(c for _o, c, _p in series)

    canvas = _Canvas(width, height)
    for (_o, _c, pts), code in zip(series, codes):
        dots = [(x, round((1 - c / top_count) * (height * 4 - 1))) for x, c in pts]
        for i in range(1, len(dots)):
            canvas.line(dots[i - 1][0], dots[i - 1][1],
                        dots[i][0], dots[i][1], code)

    gutter = max(len(str(top_count)), 1)
    body = []
    for i, row in enumerate(canvas.rows_of(colour=colour)):
        # 只标**两头**的刻度：它们的值恰好是 `top_count` 和 `0`。
        # 中间那几行不标 —— 一个「大概一半」的数是编的，不如不写。
        tick = str(top_count) if i == 0 else ("0" if i == height - 1 else "")
        body.append(f"{tick:>{gutter}} ┤{row}")
    body.append(" " * gutter + " └" + "─" * width)

    head += body
    head.append(f"   {x_note}")
    head += legend
    head.append("")
    return head


def chart(
    conn: sqlite3.Connection, topic_id: str, *,
    colour: bool | None = None, width: int = 58, height: int = 12,
) -> str:
    """把票数随时间怎么长画成折线图。**只读派生视图** —— 一行都不写。

    `§C2.5` 第 3 档（派生视图：默认生效 + 可解释）。和 `tally()` / `show()` 一样
    只读；和它们不同的是它**给的是图形**，所以可解释性靠这几件事兜住：

    | 兜的是什么 | 怎么兜的 |
    |---|---|
    | `§C12.5` 旧票属于旧问题版本 | **一个 `question_version` 一张图**，绝不跨版本画在同一条线上 |
    | `§C12.4` 结构继承 ≠ 判断继承 | 派生子论点**另开一张**，不混进当前层级那张 |
    | `§C9` #5 #7 不许有名次 | 图例**按 id 排**，不按票数；没有排序轴，也没有阈值 |
    | `§C12.1` 不是真理判定 | 页首页尾各说一次「这是偏好计数，不是对错」 |
    | 「空」不许长得像「零」 | 没有票就不画空坐标系；同一时刻的票会让横轴退化，**退化要说出来** |

    `colour=None` 时按环境判断（见 `_colour_on`）。`width` / `height` 是
    **字符**数，画布实际是 `width×2` 点宽、`height×4` 点高。

    --- 我自选的、可以推翻的 -------------------------------------------------

    - **折线是直的，不是阶梯。** 累计票数严格来说该画阶梯（票只在投票那一刻
      跳一次）。需求方 2026-09-25 说的是「折线图」，所以按折线画。
      票少的时候折线会把「两票之间」读成连续变化 —— 要改就改这里。
    - **横轴用真实时间**，不是票序号（除上面那个退化情形）。时间跨度是真实秒数，
      所以几秒内投完的一轮，图上会挤在一起 —— 那是事实，不是画错。
    """
    got = get(conn, topic_id)
    if got["type"] != "Topic":
        raise ScaffoldError(
            f"{topic_id} 是 {got['type']}，不是 Topic。"
            "（票挂在 Topic 下，所以图也按 Topic 画 —— 见 `§C12.5`。）"
        )
    on = _colour_on(colour)
    top, kids, parents = _claims_and_children(conn, topic_id)
    votes = _votes(conn, topic_id)

    lines = [
        "=" * 66,
        "§C12.1：下面是**公共偏好**，不是真理判定。",
        "折线图：横轴 = 时间，纵轴 = **累计票数**。没有排序轴，没有阈值。",
        "=" * 66,
        "",
    ]
    if not top and not kids:
        lines.append("这个议题下还没有命题 —— 没有可画的东西。")
        return "\n".join(lines)
    if not votes:
        lines += [
            "还没有票。**票数为零不等于倾向为零** —— 就是还没有票。",
            "",
            f"当前层级（{len(top)} 条）：" + ("、".join(top) if top else "（没有）"),
        ]
        if kids:
            lines.append(f"派生子论点（{len(kids)} 条）：" + "、".join(kids))
        return "\n".join(lines)

    by_version: dict = {}
    for v in votes:
        by_version.setdefault(v["version"], []).append(v)

    # **不排序**：`votes` 已经按 id 升序（= 投票先后），所以 `by_version` 的插入
    # 顺序就是「哪一版先有人投」。按 `question_version` 排是错的 ——
    # 那是 revision 的**行号**，改过版之后行号大的反而是新的，
    # 字典序排还会把 `#10` 排在 `#2` 前面。
    for ver, group in by_version.items():
        here = [v for v in group if v["choice"] in top]
        lines += _one_chart(
            conn, f"Topic {topic_id} · 当前层级", group, top,
            ver=ver, colour=on, width=width, height=height,
            rest=(f"另有 {len(group) - len(here)} 票投在派生子论点上 —— "
                  "它们各自另开一张" if len(here) < len(group) else ""))
        for kid in kids:
            mine = [v for v in group if v["choice"] == kid]
            lines += _one_chart(
                conn, f"子论点 {kid}（derived_from {parents[kid]}）", group, [kid],
                ver=ver, colour=on, width=width, height=height, note="另开一张",
                rest=(f"另有 {len(group) - len(mine)} 票投在别的论点上"
                      if len(mine) < len(group) else ""))

    lines.append("⚠️ 上面没有任何「哪条更重要」。票数是**偏好计数**，不是对错"
                 "（`§C12.1`）。")
    if kids:
        lines.append("⚠️ 子论点是**另开一张**的：`§C12.4` 明写 A1 不继承 A 的票，"
                     "所以它们不该画在同一条线上。")
    return "\n".join(lines)
