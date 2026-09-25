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
"""

from __future__ import annotations

import json
import sqlite3

from scaffold import (
    ScaffoldError,
    activate,
    add_artifact,
    add_relation,
    content_of,
    heads_of,
    record_event,
)

# `§C12.3` 要求随票一起记录的观测条件。
# `question_version` / `argument_version` 由 `vote_context()` 抓（见那边）；
# `timestamp` 就是票自己的 `created_at`。剩下三项**必须由调用方显式给出**。
#
# 为什么**不给默认值**：给了默认值，忘了传和"确实是那个值"就分不出来了 ——
# 而 `§C12.3` 那句「投票结果本身会影响后续投票」的前提就是这三个量**记准**。
# 不知道就显式传 `None`：那是"不知道"，不是"没有"。
CONDITION_FIELDS = ("sampling", "prior_results_visible", "repeat_participation")


def vote_context(conn: sqlite3.Connection, topic_id: str) -> dict:
    """**渲染那一刻**的问题版本与论点版本（`§C12.3`）。

    要在**渲染页面时**抓一次、随页面交给用户，提交时**原样交回**。
    不要等到提交时再抓 —— 用户打开页面之后别人可能改过，
    那样记下来的就不是「他看到的那一版」了，而 `§C12.3` 要的正是后者。

    问题已分叉时**没有「当前版本」**（`§C10`：系统不替你挑分支），
    所以这里照实给出 `heads`，由 `cast_vote()` 拒绝投票并说明原因。
    """
    heads = heads_of(conn, topic_id)
    claims = conn.execute(
        "SELECT a.id FROM relation r JOIN artifact a ON a.id = r.to_id"
        " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
        "   AND a.type = 'Claim' ORDER BY a.id",
        (topic_id,),
    ).fetchall()
    return {
        "topic": topic_id,
        "question_version": heads[0]["id"] if len(heads) == 1 else None,
        "question_heads": [h["id"] for h in heads],
        "argument_version": {
            c["id"]: (heads_of(conn, c["id"])[0]["id"]
                      if len(heads_of(conn, c["id"])) == 1 else None)
            for c in claims
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
