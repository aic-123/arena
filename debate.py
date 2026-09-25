"""Arena 层 —— 讨论行为。

依赖方向：Arena `uses` Scaffold（`§C1`），**不可反向**。
本文件调用 scaffold.py，scaffold.py 不认识本文件的任何概念。

--- 本文件不得出现的东西（分层不变量 #11）----------------------------------

一个本应由 Scaffold 表达的关系（需要身份 / 来源 / 可追溯性 / 版本历史），
**不得**被硬编码成 Arena 侧的一个字段。本文件里没有一个 `parent_id` 之类的列 ——
所有结构关系都走 `scaffold.add_relation()`，因为那些关系需要被追踪、被推翻、被计量。

--- 关于 `§C4` 结构的一处声明（文档未规定，我选的）--------------------------

`§C4` 的数据形状是 `Topic ├── Position/Claim`；
`§C8.1` 的嵌套图是 `Scaffold S0 └── Debate Q0 ├── Claim A`。
两处都以 Claim 为叶子，但没说 Debate 与 Topic 谁装谁。

我选的形状：**Debate `contains` Topic，Topic `contains` Claim**。
理由：`§C4` 的树可以逐字落地；`§C8.1` 里 Debate 到 Claim 的路径仍然可达；
且 `§C8.1` 要求「一个 Debate 可以成为另一个 Scaffold 中的 Artifact」——
Debate 是那个可被搬走的整体，Topic 是它内部的问题。
**这是我的选择，不是文档已经说清的。** 见 DECLARATION.md §5。
"""

from __future__ import annotations

import json
import sqlite3

import confirm
from hints import scan as scan_hints
from scaffold import (
    ScaffoldError,
    activate,
    add_artifact,
    add_relation,
    get,
    history_of,
    original_content_of,
    record_event,
    reject_relation,
    revise,
    heads_of,
)


def open_debate(conn: sqlite3.Connection, *, question: str, by: str) -> dict:
    """开一个讨论 —— **只建 Debate 容器，不自带 Topic**。

    `question` 是这次讨论的题目，**原样存**（`§C2.2.1`：原文本不可修改）。
    它是容器的标题，**不是一个 Topic** —— 一个 Debate 下可以挂多个 Topic
    （「一段原文一个 Topic」），谁和谁在同一个议题里争由 Debate 这一层表达。

    --- 为什么不再自带一个 Topic（2026-09-25 改）-----------------------------

    原来这里顺手建一个 `{text, raw_text}` 的 Topic。那属于**第二条写路径**：
    不分割、同一个调用里自我 `activate()`、只存两个字段。后果是那条路上的
    `§C5.6` 换说法率与确认耗时**永远是 0/0**（分母来自 draft，而它不产生 draft）。

    现在只剩一条写路径：`confirm.propose()` → `confirm.confirm(reviewed=[…])`
    → `open_topic()` / `add_to_topic()`。Topic 因此只可能由一次**逐条确认**产生，
    `input_snapshot`、两个版本号、`span`、`role`、`type_decided_by` 一个不少。
    """
    debate_id = add_artifact(
        conn, type_="Debate",
        content={"question": question},
        origin=by,
    )
    activate(conn, debate_id, by=by)
    record_event(conn, "debate_opened", by, debate_id, {"question": question})
    conn.commit()
    return {"debate": debate_id}


def open_topic(
    conn: sqlite3.Connection, *, debate_id: str, draft_id: str,
    by: str, reviewed: list[str],
) -> dict:
    """把一份**已确认的** draft 作为一个**新 Topic** 挂进一个已有的 Debate。

    本函数**不生成任何文本**：Topic 的原文、命题、分割都是 draft 里
    用户自己确认过的那一份（`§C2.0`：议题由用户提出）。

    --- 为什么需要它（2026-09-25 走真人流程走出来的）-------------------------

    `confirm.confirm()` 造出来的 Topic **不属于任何 Debate**，而 `view()` 是
    从 Debate 顺着 `contains` 往下走的。所以此前只有两条路，两条都不对：

    | 路 | 后果 |
    |---|---|
    | 不传 `topic_id` | Topic **悬空**，任何 `view()` 都到不了它 |
    | 传一个已存在的 `topic_id` | 把**另一段原文**的命题塞进**别人**的 Topic。那个 Topic 的 `raw_text` 只认第一段，`hints.scan()` 也只看那一段 ——**第二个人写的问题，分歧定位永远看不到** |

    **悬空那条以前是靠测试里一个辅助函数补的**（`test_arena.py` 的
    `_wrap_in_a_debate`）——测试得自己造产品没有的能力，那就是缺口的形状。

    这条补的是第三条路：**一段原文一个 Topic，都挂在同一个 Debate 下。**
    谁和谁在同一个议题里争，由 Debate 那一层表达；Topic 就是「一段原文」。

    **落点在 Arena 层，不在 `confirm.py`** —— `confirm()` 不需要知道
    Debate 这种东西（`§C9` #11：本应由 Scaffold 表达的关系不许硬编码，
    反过来说，不属于某一层词汇的东西也不许塞进去）。

    ⚠️ **剩下的一半没修**：走「塞进同一个 topic」那条老路时，
    `view()` 里的 `subquestion_hints` 仍然只扫 `topic.raw_text`，
    扫不到后交上来那些人的 `input_snapshot`。那是一条独立的事，见 DECLARATION §12。
    """
    # 传错 id 要报错，不能像 `view()` 那样静默返回一个形状正常的结果。
    got = get(conn, debate_id)
    if got["type"] != "Debate":
        raise ScaffoldError(
            f"{debate_id} 是 {got['type']}，不是 Debate。"
            "（把 Topic id 传进来会得到一份看起来正常的视图，那是更坏的一种错。）"
        )
    out = confirm.confirm(conn, draft_id, by=by, reviewed=reviewed)
    add_relation(conn, kind="contains", from_id=debate_id, to_id=out["topic"],
                 origin=by)
    record_event(conn, "topic_opened", by, debate_id,
                 {"topic": out["topic"], "draft": draft_id})
    conn.commit()
    return out


def add_to_topic(
    conn: sqlite3.Connection, *, topic_id: str, draft_id: str,
    by: str, reviewed: list[str],
) -> dict:
    """把一份**已确认的** draft 的命题挂进一个**已有的** Topic。

    和 `open_topic()` 是**同一条路的两个出口**（新 Topic / 已有 Topic）——
    两者都落到 `confirm.confirm()`，所以分割、逐条确认、完整工件字段一个不少。

    --- 这里原来是 `add_position()`（2026-09-25 改）--------------------------

    `add_position()` 直接 `add_artifact(Claim, {text, raw_text})` 再在**同一个调用里**
    `activate()`。它**看着像**「提交也要过一次确认」，可那次确认是函数自己做的：
    调用方没有列出任何一条 `reviewed`，`§C5` 的闸门在这条路上只靠自觉。
    它也不产生 draft，于是 `§C5.6` 的换说法率与确认耗时在这条路上永远是 `0/0`。
    **那是遗留，不是设计**，没有文档授权它。

    ⚠️ `cli.py` 现在**不走**这条路：它每次提交都新开一个 Topic（`open_topic`）。
    留着它，是因为它是同一写路径的另一个出口，而「同一个 Topic 下多个命题」
    这个形状需要它。**要不要让产品走它，是 DECLARATION §12.3 那个未决的产品判断**
    （「一份原文一个 Topic」是不是硬约定），不是这里能自己定的。
    """
    return confirm.confirm(conn, draft_id, by=by, reviewed=reviewed,
                           topic_id=topic_id)


def amend_own_claim(
    conn: sqlite3.Connection, *, claim_id: str, text: str, by: str,
) -> int:
    """用户修改自己的命题（`§C7.2` 观测点「用户对自己命题的修改率」）。

    **不覆盖**：旧版本留在 revision 表里，新版本挂上去（`§C10`）。
    若该 Claim 已被并发改出多个分支，`revise` 会拒绝自动挑一个分支 —— 见 scaffold.revise。
    """
    rev = revise(conn, claim_id, content={"text": text, "raw_text": text}, author=by)
    record_event(conn, "own_claim_amended", by, claim_id, {"revision": rev})
    conn.commit()
    return rev


# ---------------------------------------------------------------------------
# `§T2` 第 07 步：Claim / Evidence / Challenge
# ---------------------------------------------------------------------------

# `§C6.2` 必须记录的十个属性。**它们是字段，不是分** —— 见 _no_scalars。
EVIDENCE_FIELDS = (
    "source", "accessibility", "directness", "reproducibility", "methodology",
    "sample_selection", "representativeness", "temporal_validity",
    "independence", "conflict_incentive",
)

# `§C6.3`：Evidence → Claim 只有这三种。**不许归约**（`§C4` 末句）。
EVIDENCE_TO_CLAIM = ("supports", "contradicts", "qualifies")

# 缺口①：因果主张的两端。**两种**，不是一种 —— 理由见 `scaffold.RELATION_KINDS`。
CAUSAL_END_KINDS = ("causal_premise", "causal_conclusion")


def _evidence_attributes(fields: dict | None) -> dict:
    """`§C6.1`：禁止形如 `evidence_score = 8.7` 的标量设计。

    理由是原文给的：「一旦引入标量分，系统即在实质上充当裁判」。
    所以这里在**唯一的写入路径**上拒收数字 —— 不靠调用方自觉，
    和 `add_artifact` 拒收 `state='active'` 是同一个做法。

    `True` / `False` 不算数（那是开关，不是量）。
    没填的属性一律落成 `None` —— 空着比填错好（`§C5.3`），
    但要**显式地空**，不能整个键消失。
    """
    given = dict(fields or {})
    unknown = set(given) - set(EVIDENCE_FIELDS)
    if unknown:
        raise ScaffoldError(f"未知的 Evidence 属性：{sorted(unknown)}（见 §C6.2）")
    for k, v in given.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            raise ScaffoldError(
                f"{k} 收到一个数（{v!r}）。`§C6.1` 禁止标量 —— "
                "证据的属性是描述，不是分数。没有就写 None。"
            )
    return {k: given.get(k) for k in EVIDENCE_FIELDS}


def add_evidence(
    conn: sqlite3.Connection, *,
    claim_id: str, text: str, by: str,
    kind: str = "supports", fields: dict | None = None,
) -> dict:
    """提交一条证据，并说明它对**这一个** Claim 起什么作用。

    `kind` 属于**边**，不属于证据 —— `§C6.4`：
    「证据的作用是相对于具体 Claim 定义的，不是证据自带的属性」。
    所以同一条证据挂到另一个 Claim 上可以是另一种作用，那时走 `link_evidence()`，
    **不新建一条证据**。

    `§C2.3`：机器不得「依据自身判断生成新事实并写入知识结构」。
    所以 `by` 是人 —— **机器没有生成 Evidence 的入口**。
    机器能做的是给已有证据挂边（那是判断，不是新事实），见 `link_evidence()`。
    """
    if kind not in EVIDENCE_TO_CLAIM:
        raise ScaffoldError(
            f"Evidence→Claim 只能是 {EVIDENCE_TO_CLAIM}（`§C6.3`），收到 {kind!r}"
        )
    get(conn, claim_id)
    eid = add_artifact(
        conn, type_="Evidence",
        content={"text": text, "raw_text": text,
                 "attributes": _evidence_attributes(fields)},
        origin=by,
    )
    activate(conn, eid, by=by)
    rid = add_relation(conn, kind=kind, from_id=eid, to_id=claim_id, origin=by)
    record_event(conn, "evidence_submitted", by, eid,
                 {"claim": claim_id, "relation": rid, "kind": kind})
    conn.commit()
    return {"evidence": eid, "relation": rid}


def link_evidence(
    conn: sqlite3.Connection, *,
    evidence_id: str, claim_id: str, kind: str, origin: str,
) -> int:
    """把**已有**证据按另一种作用挂到**另一个** Claim 上（`§C6.4` 的全部要点）。

    这是 `§C2.5` 那一档里的「对已有内容的标注与关系」——
    默认生效 + 可推翻 + 抽样审计 + 计改判率。
    **不需确认**，所以 `origin` 可以是机器；
    改判率正靠 `origin` 区分「AI 判定」与「用户改判」（`§C2.4`）。
    """
    if kind not in EVIDENCE_TO_CLAIM:
        raise ScaffoldError(f"Evidence→Claim 只能是 {EVIDENCE_TO_CLAIM}（`§C6.3`）")
    get(conn, evidence_id)
    get(conn, claim_id)
    rid = add_relation(conn, kind=kind, from_id=evidence_id, to_id=claim_id,
                       origin=origin)
    record_event(conn, "evidence_linked", origin, evidence_id,
                 {"claim": claim_id, "relation": rid, "kind": kind})
    conn.commit()
    return rid


# `§C4` 原本只写了 `Claim ─challenged_by→ Counterargument` 一条。
# 但真实辩论里最常见的两种有效反驳落不进那条：质询**证据的属性**（E2：数据过时）
# 与质询**假设**（E3：理性人假设不成立）—— 它们反对的都不是结论。
# 缺口④要求把定义域扩到这三个。**只扩到这三个**：装不知道比装知道安全，
# 但这个集合是照样本标的，不是照「什么都行」编的。
CHALLENGEABLE = ("Claim", "Evidence", "Assumption")


def _attach(
    conn: sqlite3.Connection, *, kind: str, type_: str,
    from_id: str, text: str, by: str,
) -> str:
    """→（assumes / explains / challenged_by）→ X 的公共部分。

    `§C4` 要求这三条**逐条落地、不可合并**，所以对外是三个具名函数；
    但落地方式一样，这里只写一次。

    `from_id` 原来叫 `claim_id` —— 缺口④之后 `challenged_by` 的起点
    不再只能是 Claim，所以那个名字**变成了假的**。名字假了就得改。
    """
    get(conn, from_id)
    aid = add_artifact(conn, type_=type_,
                       content={"text": text, "raw_text": text}, origin=by)
    activate(conn, aid, by=by)
    add_relation(conn, kind=kind, from_id=from_id, to_id=aid, origin=by)
    record_event(conn, f"{type_.lower()}_added", by, aid, {"from": from_id})
    conn.commit()
    return aid


def add_mechanism(conn: sqlite3.Connection, *, claim_id: str, text: str, by: str) -> str:
    """Claim `explains` Mechanism（`§C4`）。"""
    return _attach(conn, kind="explains", type_="Mechanism",
                   from_id=claim_id, text=text, by=by)


def add_assumption(conn: sqlite3.Connection, *, claim_id: str, text: str, by: str) -> str:
    """Claim `assumes` Assumption（`§C4`）。"""
    return _attach(conn, kind="assumes", type_="Assumption",
                   from_id=claim_id, text=text, by=by)


def challenge(conn: sqlite3.Connection, *, target_id: str, text: str, by: str) -> dict:
    """质询一个 Claim / Evidence / Assumption（`§C3.4`：一个 Relation + 一个 Artifact）。

    落成 Counterargument 这个 Artifact，加一条 `challenged_by` 边。
    **不是**给 Claim 减分，也不是给证据降权 —— 本系统不产生那种量（`§C6.1`）。

    --- 起点为什么从 `claim_id` 变成 `target_id`（缺口④）---------------

    原来的定义域只有 Claim 一条边。于是 E2「这个数据是三年前的」和
    E3「理性人假设不成立」**无处可放** —— 而它们恰恰是最常见的两种有效反驳。
    真实辩论里，反对的常常不是结论，是**证据的属性**或**前提本身**。

    ⚠️ 需求方样本里写得很清楚：**不要为了绕开定义域问题而新增标签**。
    所以这里没造 `Challenge` 这个新节点类型 —— 只把起点放宽到
    `CHALLENGEABLE` 那三个。节点类型仍是 Counterargument 一个不变。

    ⚠️ 仍然**不是**「什么都行」。放开到 `get()` 能拿到的任何 Artifact，
    就等于声明了一个没有依据的定义域 —— 那和当初只写一条是同一个病，
    只是方向反过来。
    """
    row = get(conn, target_id)
    if row["type"] not in CHALLENGEABLE:
        raise ScaffoldError(
            f"challenged_by 的起点只能是 {CHALLENGEABLE}，"
            f"{target_id} 是 {row['type']}（缺口④）。"
        )
    aid = _attach(conn, kind="challenged_by", type_="Counterargument",
                  from_id=target_id, text=text, by=by)
    return {"counterargument": aid}


def reject_edge(conn: sqlite3.Connection, relation_id: int, *, by: str) -> None:
    """用户推翻一条边（`§C2.4`「可拒绝」）。

    改判率观测点要的分子就在这里 —— `scaffold.reject_relation` 已经把
    `origin` 和 `kind` 一起记进事件，所以「机器边被改判了几条」算得出来。
    """
    reject_relation(conn, relation_id, by=by)


def _linked(conn: sqlite3.Connection, rows, *, with_challenges: bool = True) -> list[dict]:
    """把「边 + 对端 Artifact + 它的全部版本」摊平，只读。"""
    out = []
    for r in rows:
        d = dict(r)
        rel = {"id": d.pop("relation_id"), "kind": d.pop("relation_kind"),
               "origin": d.pop("relation_origin")}
        d["heads"] = [dict(h) for h in heads_of(conn, d["id"])]
        if with_challenges:
            # 缺口④：质询可能落在这个节点**本身**（E2 质询证据、E3 质询假设），
            # 而不是它挂着的那个 Claim。不列出来的话，放宽定义域只是让边写得进去、
            # 看不见 —— 那是半个修复。
            # 只摊一层：「那这条质询又被谁质询」是个没有边界的视图，不展开。
            d["challenged_by"] = _linked(
                conn, _out_of_claim(conn, d["id"], "challenged_by"),
                with_challenges=False)
        out.append({"artifact": d, "relation": rel})
    return out


def _into_claim(conn: sqlite3.Connection, claim_id: str, kinds: tuple) -> list:
    """方向：Evidence → Claim（`§C6.3`）。"""
    marks = ",".join("?" * len(kinds))
    return conn.execute(
        "SELECT a.*, r.id AS relation_id, r.kind AS relation_kind,"
        " r.origin AS relation_origin"
        " FROM relation r JOIN artifact a ON a.id = r.from_id"
        f" WHERE r.to_id = ? AND r.state = 'active' AND r.kind IN ({marks})"
        " ORDER BY a.id, r.id",
        (claim_id, *kinds),
    ).fetchall()


def _out_of_claim(conn: sqlite3.Connection, claim_id: str, kind: str) -> list:
    """方向：Claim → X（`§C4` 的 assumes / explains / challenged_by）。"""
    return conn.execute(
        "SELECT a.*, r.id AS relation_id, r.kind AS relation_kind,"
        " r.origin AS relation_origin"
        " FROM relation r JOIN artifact a ON a.id = r.to_id"
        " WHERE r.from_id = ? AND r.state = 'active' AND r.kind = ?"
        " ORDER BY a.id, r.id",
        (claim_id, kind),
    ).fetchall()


def view(conn: sqlite3.Connection, debate_id: str) -> dict:
    """只读视图。**不做排序、不做聚合、不给任何一项加权。**

    `§C6.1` `§C9` #5 #7：本系统不产生「哪条更重要」这个量。
    所以这里按 id 出，不按任何东西排序。

    `subquestion_hints` 是 `§C2.1` 的派生视图：把**用户自己原文里已经写出**的
    问题与依赖维度指回去（见 `hints.py`）。**它不建任何对象、不写库**，
    所以「提示」不会变成「系统替用户提议题」（`§C2.0`）。
    """
    # 传错 id 要报错。原来这里不查 —— 传一个 Topic id 进来会得到一份
    # **形状正常**的视图（`topics` 里一项，里面那些 Claim 也照列），
    # 比报错更坏的一种错。`open_topic()` 早就查了，`view()` 是根因那一处。
    got = get(conn, debate_id)
    if got["type"] != "Debate":
        raise ScaffoldError(
            f"{debate_id} 是 {got['type']}，不是 Debate。"
            "（你要看的是某个议题里的一段原文吧？那段原文是 Topic，"
            " 它挂在某个 Debate 下面。）"
        )
    debate = got
    topics = conn.execute(
        "SELECT a.* FROM relation r JOIN artifact a ON a.id = r.to_id"
        " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
        " ORDER BY a.id",
        (debate_id,),
    ).fetchall()
    out = {"debate": dict(debate), "topics": []}
    for t in topics:
        claims = conn.execute(
            "SELECT a.* FROM relation r JOIN artifact a ON a.id = r.to_id"
            " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
            " ORDER BY a.id",
            (t["id"],),
        ).fetchall()
        raw = original_content_of(conn, t["id"]).get("raw_text", "")
        out["topics"].append({
            "topic": dict(t),
            "subquestion_hints": scan_hints(raw),
            "claims": [
                {
                    "claim": dict(c),
                    "heads": [dict(h) for h in heads_of(conn, c["id"])],
                    # `§C10` 的 Current State + Revision History。
                    # 多头时 `history["current"]` 是 None，视图**不挑分支**。
                    "history": history_of(conn, c["id"]),
                    # `§C6.3` 的三种作用和 `§C4` 的三条出边，**分开列，不合并**。
                    # 每条边都带 `relation.kind` 与 `relation.origin`：
                    # 前者是它对**这一个** Claim 起的作用（`§C6.4`），
                    # 后者是「谁判的」—— 改判率全靠它（`§C2.4`）。
                    "evidence": _linked(conn, _into_claim(
                        conn, c["id"], EVIDENCE_TO_CLAIM)),
                    # 缺口①：因果主张的两端。方向是端 → 因果主张，
                    # 所以这里跟 `evidence` 一样是**入边**。哪端是因、哪端是果
                    # 由 `relation.kind` 区分（causal_premise / causal_conclusion），
                    # 视图不合并它们、也不替谁排出先后。
                    "causal_ends": _linked(conn, _into_claim(
                        conn, c["id"], CAUSAL_END_KINDS)),
                    "mechanisms": _linked(conn, _out_of_claim(conn, c["id"], "explains")),
                    "assumptions": _linked(conn, _out_of_claim(conn, c["id"], "assumes")),
                    "counterarguments": _linked(conn, _out_of_claim(
                        conn, c["id"], "challenged_by")),
                }
                for c in claims
            ],
        })
    return out


# ---------------------------------------------------------------------------
# 呈现（`cli.py` 用的那一面）
# ---------------------------------------------------------------------------

def _said(heads: list[dict]) -> str:
    """一个节点**现在**那版说的话。

    多头 = 没有「现在」这版（`§C10`），照实说，**不挑一个**。
    """
    if len(heads) != 1:
        return "（分叉：没有当前版本，几条分支都留着）"
    return json.loads(heads[0]["content"]).get("text", "")


def _type_note(heads: list[dict]) -> str:
    """缺口② 的痕迹：这一条的类型是**默认生效**的还是人定过的（`§C2.5` 第 2 档）。

    默认那一条必须**看得见** —— 不然「可推翻」是空话，抽样审计也无从下手。
    """
    if len(heads) != 1:
        return ""
    c = json.loads(heads[0]["content"])
    if not c.get("type_candidates"):
        return ""
    if str(c.get("type_decided_by", "")).startswith("machine"):
        return f"    ⚠ 类型是默认（候选 {c['type_candidates']}）—— 可推翻"
    return f"    （类型由人指定，候选 {c['type_candidates']}）"


def _edge_lines(label: str, items: list[dict]) -> list[str]:
    if not items:
        return []
    out = [f"       {label}："]
    for e in items:
        a, r = e["artifact"], e["relation"]
        out.append(f"         [{r['kind']}] {a['id']}  {_said(a['heads'])}")
        # 缺口④：质询可能落在这个节点**本身**（E2 质询证据、E3 质询假设）。
        for q in a.get("challenged_by") or []:
            out.append(f"             └ 被质询：{q['artifact']['id']}"
                       f"  {_said(q['artifact']['heads'])}")
    return out


def render(conn: sqlite3.Connection, debate_id: str) -> str:
    """把一个 Debate 印成人看的。只读 —— 和 `view()` 一样一行都不写。

    和 `vote.show()` 一个道理：**分开列，不合并**。
    三种证据作用不合成一句，因果两端不排先后，被质询的 Claim 全部列出、不排名。
    """
    v = view(conn, debate_id)
    # Debate 的问题存在 `question` 键里，Topic/Claim 存在 `text` 里（§C4 两处形状不同）。
    heads = [dict(h) for h in heads_of(conn, debate_id)]
    question = (json.loads(heads[0]["content"]).get("question", "")
                if len(heads) == 1 else "（分叉：没有当前版本）")
    lines = [
        "=" * 64,
        f"Debate {v['debate']['id']}   「{question}」",
        f"  状态：{v['debate']['state']} · 建库时间 {v['debate']['created_at']}",
        "",
    ]
    if not v["topics"]:
        lines += [
            "  还没有议题。**一段原文一个 Topic** —— 提交走：",
            f'    python cli.py submit {debate_id} <谁> "<你的原话>"',
        ]
        return "\n".join(lines)

    for t in v["topics"]:
        raw = original_content_of(conn, t["topic"]["id"]).get("raw_text", "")
        lines += ["─" * 64,
                  f"Topic {t['topic']['id']}",
                  f"  原文（原样保存，不可修改）：{raw}",
                  "",
                  "  ── 你自己已经写出的问题（`§C2.1` 派生视图：机器只指出来，不建对象）"]
        h = t["subquestion_hints"]
        if h["hints"]:
            for x in h["hints"]:
                lines.append(f"     · 「{x['quoted']}」    {x['why']}")
        else:
            lines.append(f"     {h['note']}")
        lines.append("")

        if not t["claims"]:
            lines += ["  还没有命题。", ""]
            continue

        for c in t["claims"]:
            cl = c["claim"]
            lines.append(f"  ── {cl['type']} {cl['id']}   [{cl['status']}]"
                         f"  「{_said(c['heads'])}」")
            note = _type_note(c["heads"])
            if note:
                lines.append(note)
            for label, key in (("证据", "evidence"),
                               ("因果两端", "causal_ends"),
                               ("机制", "mechanisms"),
                               ("依赖前提", "assumptions"),
                               ("质询", "counterarguments")):
                lines += _edge_lines(label, c[key])
            hist = c["history"]
            if len(hist["revisions"]) > 1 or hist["current"] is None:
                lines.append(f"       版本 {len(hist['revisions'])} 条"
                             f"（`§C10`：旧版一行都没删）")
            lines.append("")
    lines.append("⚠️ 上面没有任何「哪条更重要」。本系统不产生那个量（`§C6.1`）。")
    return "\n".join(lines)
