"""语义确认入口（`§C5`）—— Arena 层。

`§C5` 说这一条**决定产品形态**，不要按「减少摩擦」的思路改。所以本文件里
每一个「不做」都是刻意的，不是没来得及做。

--- 确认环节是什么 ---------------------------------------------------------

确认环节是用户**反省自己那句话**的唯一时机。用户在这里第一次看到：
*我这句话里其实包含三个命题，其中第三个我没打算说。*
**这是产品价值第一次交付的地方，不是流程摩擦。**

--- 用户能做什么、不能做什么（`§C2.2.2`）----------------------------------

| 用户**能** | 用户**不能** |
|---|---|
| 指出「这句话的意义被歪曲了」 | 决定哪段文本被贴上哪个标签 |
| 看到分割结果与未采用的部分 | 决定哪段文本能进入结构 |

**推论（本实现据此写成）**：用户手上**没有**「勾选哪些命题进入结构」这个动作。
他的动作是逐条确认**意义有没有被歪曲**：

- 全部逐条确认「意义未被歪曲」 → 机器按它自己切的分，整份写入 Scaffold
- 任何一条被指为歪曲 → **整份不写入**。按 `§C5.6`，用户只有三条路：
  接受 / 换说法重交 / 不提交离开

**不能挑一部分留下。** 挑子集就是在决定「哪段文本能进入结构」，那是 `§C2.2.2` 划给机器的。

--- 刻意不做的（`§C5` 硬约束）--------------------------------------------

- **没有「一键全部确认」** —— `confirm()` 必须拿到逐条的 `reviewed`，且不设默认值
- **没有默认勾选** —— `reviewed` 为空直接拒绝
- **没有「无感后台步骤」** —— 写入必须由一次显式调用触发，没有自动提交路径
- **不能删改已确认的内容** —— §C10 已定：只进不退，靠 revision 记录后续变化
- **没有「按命题编辑」** —— 想改就退回输入框整体重写（`revise()`）。
  需求方 2026-09-25 当场定死。能按条改，就等于能按条取舍，那是 `§C2.2.2` 划给机器的。

--- 节点类型不在这里定，但在这里生效 ---------------------------------------

`§C2.2.2` 说确认的是**意义**，不是标签分配 —— 所以 `confirm()` 的入参里
**没有**「这条是什么类型」。但有一种命题，规则引擎**没有依据**给它定类型：

    「X，所以 Y」—— 因果即主张（B1）还是因果即推断（D1）？

两者字面结构一模一样，判别要世界知识。`§T0.3` 不许引擎自决，所以它
**不选**，只把两种可能写进 `type_candidates`（见 `segment.py`）。

定夺是**另一个动作**：`resolve_types()`，由人显式调用，逐条给全。

**不调用它也写得进去** —— 这是需求方 2026-09-25 定的 `§C2.5` **第 2 档**：
默认取候选第一项，默认生效、可推翻、抽检、计改判率。
理由是**不让人为少数命题多判断一次**。四件事一件都不能少，
「记录默认」在第 393 行那儿，缺了它这一档就退化成无痕默认。

我原来做的是第 1 档（不定完不写）。已推翻，见 DECLARATION §10.2。

--- 一处必须说清的局限 -----------------------------------------------------

代码**证明不了**用户真的逐条读了。`reviewed` 是一个声明，不是一个证据。
能机械保证的只有三件事：没有默认值、集合必须完整、没有 `all` 之类的批量哨兵。
「用户是否真的在反省」测不到 —— `§C2.4` 说得很清楚：四项都是**能力**，不是**义务**。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from scaffold import (
    ScaffoldError, add_artifact, add_relation, activate, next_seq, record_event,
)
from segment import segment

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# Draft 是 **Arena 侧** 的状态：它还没被确认，所以**不在 Scaffold 里**（`§C5`：
# 未经确认不得写入 Scaffold）。因此它有自己的表，不归 scaffold.py 管。
DRAFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS draft (
    id          TEXT PRIMARY KEY,
    raw_text    TEXT NOT NULL,        -- 用户原话，原样（§C2.2.1：原文本不可修改）
    payload     TEXT NOT NULL,        -- segment() 的完整输出，含版本号与 span
    actor       TEXT NOT NULL,
    state       TEXT NOT NULL,        -- open / confirmed / distorted / abandoned / revised
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    revised_from TEXT                 -- 退回填充阶段重写时，指回上一份 draft
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DRAFT_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# 提交 → 出分割结果
# ---------------------------------------------------------------------------

def _insert_draft(
    conn: sqlite3.Connection, *, text: str, by: str, revised_from: str | None = None,
) -> str:
    """新建一份 draft。**不 commit** —— 由调用方决定这一笔什么时候落地。"""
    payload = segment(text)
    # 走 Scaffold 的原子发号（`§T2` 第 12 步）。原来是 `SELECT COUNT(*)` 再拼名字 ——
    # 那是两步，两个用户同时提交会算出同一个 `draft-0001`，一个写成功一个报错。
    draft_id = f"draft-{next_seq(conn, 'draft'):04d}"
    conn.execute(
        "INSERT INTO draft (id, raw_text, payload, actor, state, created_at, revised_from)"
        " VALUES (?,?,?,?,?,?,?)",
        (draft_id, text, json.dumps(payload, ensure_ascii=False),
         by, "open", _now(), revised_from),
    )
    record_event(conn, "draft_proposed", by, draft_id,
                 {"input_snapshot": text,
                  "segmenter_version": payload["segmenter_version"],
                  "ruleset_version": payload["ruleset_version"],
                  "unused_char_ratio": payload["unused_char_ratio"],
                  "revised_from": revised_from})
    return draft_id


def propose(conn: sqlite3.Connection, *, text: str, by: str) -> str:
    """用户输入自然语言 → AI 分割为若干独立命题。

    `§C5.5` 第 5 条：连同**输入快照 + 两个版本号**一起冻结，用于解释「为什么当时这样切」。
    """
    draft_id = _insert_draft(conn, text=text, by=by)
    conn.commit()
    return draft_id


def revise(conn: sqlite3.Connection, draft_id: str, *, text: str, by: str) -> str:
    """退回填充阶段**整体**修改（`§C5.6` ②「换说法重交」）。

    需求方 2026-09-25 当场定死了这条：**没有「改某一条命题」这个动作。**
    想改，就退回输入框把整句话重写，重新走一遍分割与确认。

    所以这里**不提供**任何按命题编辑的入口 —— 那不是本函数少写了一个参数，
    而是 `§C2.2.2` 压根没给用户「决定哪段文本进入结构」的权力：能拆开改，
    就等于能按条取舍。

    旧 draft 一行不改，只记 `revised`，并留下 `revised_from` 指回关系 ——
    `§C7.2` 的「换说法率」与「用户对自己命题的修改率」都靠这条链算，
    所以链必须留下来，不能就地覆盖。
    """
    d = _load(conn, draft_id)
    if d["state"] not in ("open", "distorted"):
        raise ScaffoldError(
            f"{draft_id} 已是 {d['state']}，不能退回重写。"
            + ("（已确认的内容进了 Scaffold，改它走 §C10 的 revision，"
               "不是新开一份 draft。）" if d["state"] == "confirmed" else "")
        )
    was = d["state"]
    new_id = _insert_draft(conn, text=text, by=by, revised_from=draft_id)
    conn.execute(
        "UPDATE draft SET state = 'revised', decided_at = ? WHERE id = ?",
        (_now(), draft_id),
    )
    record_event(conn, "draft_revised", by, draft_id,
                 {"to": new_id,
                  "from_state": was,
                  # §C5.6 ②：**先点了「意义被歪曲」再重写**才计入换说法率；
                  # 没点直接重写 = 自己改主意，不计入。这个标记就是归因口径本身。
                  "was_flagged": was == "distorted",
                  "input_snapshot": d["raw_text"],
                  "new_input_snapshot": text})
    conn.commit()
    return new_id


def _load(conn: sqlite3.Connection, draft_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM draft WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise ScaffoldError(f"不存在的 draft：{draft_id}")
    return row


def payload_of(conn: sqlite3.Connection, draft_id: str) -> dict:
    return json.loads(_load(conn, draft_id)["payload"])


# ---------------------------------------------------------------------------
# 呈现（§C5.2：未进入结构的部分必须可见）
# ---------------------------------------------------------------------------

def render(conn: sqlite3.Connection, draft_id: str) -> str:
    """确认界面。三条同时呈现，缺一条就违反 `§C5.2`：

    1. 原文**全文**渲染
    2. 被贴上标签的片段高亮
    3. **未被切出的部分也必须显式呈现**（带计数），不得静默丢弃
    """
    d = _load(conn, draft_id)
    p = json.loads(d["payload"])
    text = d["raw_text"]

    marked, cursor = [], 0
    spans = sorted(
        [(x["span"][0], x["span"][1], "used") for x in p["propositions"]]
        + [(x["span"][0], x["span"][1], "unused") for x in p["unused_spans"]]
    )
    for lo, hi, kind in spans:
        if lo > cursor:
            marked.append(text[cursor:lo])
        piece = text[lo:hi]
        marked.append(f"【{piece}】" if kind == "used" else f"〔{piece}〕")
        cursor = hi
    if cursor < len(text):
        marked.append(text[cursor:])

    lines = [
        f"draft {d['id']}    状态：{d['state']}",
        "",
        "── 原文（原样保存，不可修改）" + "─" * 30,
        text,
        "",
        "── 分割结果  【】=被切出   〔〕=未切出" + "─" * 20,
        "".join(marked),
        "",
        f"── 命题（{len(p['propositions'])} 个）" + "─" * 34,
    ]
    resolved = p.get("resolved_types", {})
    for x in p["propositions"]:
        role = {"cause": "原因", "effect": "结果", "causal": "因果", "clause": "命题"}[x["role"]]
        lines.append(f"  {x['key']}. [{role}] 「{x['text']}」   位置 {x['span']}")
        # §C5：分割输出应让用户看到**为什么这样切**
        lines.append(f"      为什么这样切：{x['why']}")
        # 缺口②：引擎答不了的那一类，**当场把两种可能都摆出来**（§C2.5 第 2 档）。
        # 措辞是「默认 X，也可 Y」，不是「未定」—— 未定会读成「没它写不进去」，
        # 而第 2 档下不点它照样写得进去。**可推翻的前提是用户先看见能推翻。**
        if x.get("type_candidates"):
            cands = " 或 ".join(x["type_candidates"])
            if x["key"] in resolved:
                lines.append(f"      节点类型：{resolved[x['key']]}（由人指定 — 缺口②）")
            else:
                lines.append(
                    f"      节点类型：默认 {x['type_candidates'][0]}，也可 {cands}。"
                    f"「X，所以 Y」有因果即主张 / 因果即推断两种形态，"
                    f"字面分不出来，规则引擎不猜 —— 默认不是判断，是**待抽检的占位**。"
                    f"要改就调 resolve_types()。"
                )

    lines += [
        "",
        f"── 未采用部分（{len(p['unused_spans'])} 段，"
        f"共 {sum(len(u['text']) for u in p['unused_spans'])} 字）" + "─" * 16,
    ]
    for u in p["unused_spans"]:
        lines.append(f"  〔{u['text']}〕   位置 {u['span']}")
    lines += [
        "",
        # §C7.2：只报数，不判高低。没有阈值，也不会有阈值。
        f"未采用字符占比 = {p['unused_char_ratio']:.2%}"
        f"（口径：未被任何命题覆盖的非空白字符 / 非空白字符总数；标点计入未采用）",
        f"分割器 {p['segmenter_version']} · 规则集 {p['ruleset_version']}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 用户的两个动作
# ---------------------------------------------------------------------------

def mark_distorted(
    conn: sqlite3.Connection, draft_id: str, *, by: str, note: str = "",
) -> None:
    """`§C2.2.2` 授权给用户的动作：「这句话的意义被歪曲了」。

    `§C5.6` ②：**这个动作本身就是归因信号**。
    用户在确认界面点了它、随后重写 → 计入换说法率的分子；
    没点直接重写 → 不计入（视为自己改主意）。
    所以它在这里只记录，**不新增任何原因选择框**（那会违反 `§C7.2`）。
    """
    d = _load(conn, draft_id)
    if d["state"] != "open":
        raise ScaffoldError(f"{draft_id} 已是 {d['state']}，不能再改")
    conn.execute(
        "UPDATE draft SET state = 'distorted', decided_at = ? WHERE id = ?",
        (_now(), draft_id),
    )
    record_event(conn, "meaning_flagged", by, draft_id,
                 {"note": note, "input_snapshot": d["raw_text"]})
    conn.commit()


def abandon(conn: sqlite3.Connection, draft_id: str, *, by: str) -> None:
    """`§C5.6` ③ A 类：**已输入后离开** —— 输入与分割结果都产生了，用户没确认就走了。

    A 类**系统内可见**，所以必须记。它对应 `§C7.2` 观测点清单里的
    「确认环节：确认耗时 / 中途放弃」，**不是新机制**。
    （B 类「未输入即离开」系统内测不到，不得假装它不存在 —— 见 `§C7.2`。）
    """
    d = _load(conn, draft_id)
    if d["state"] != "open":
        raise ScaffoldError(f"{draft_id} 已是 {d['state']}")
    conn.execute(
        "UPDATE draft SET state = 'abandoned', decided_at = ? WHERE id = ?",
        (_now(), draft_id),
    )
    record_event(conn, "draft_abandoned", by, draft_id,
                 {"input_snapshot": d["raw_text"]})
    conn.commit()


def resolve_types(
    conn: sqlite3.Connection, draft_id: str, *, by: str, choices: dict[str, str],
) -> dict:
    """给「规则引擎没有依据定类型」的命题逐个定类型（缺口②）。

    与 `confirm()` 分开，正因为 `§C2.2.2` 把「标签分配」划在确认**之外** ——
    这里不是确认意义，是为一个引擎答不了的结构问题做**人的判断**。
    谁判的记进事件：`§C2.4` 的「可计量」要的就是这个。

    这是 `§C2.5` 第 2 档里「**可推翻**」那一条的落点：不调它，默认生效；
    调它，这次调用就把默认推翻掉，并留下一条 `types_resolved`。
    两条事件（`types_resolved` / `types_defaulted`）合起来，改判率才算得出来。

    `choices` 必须**把带 `type_candidates` 的命题一个不漏地**给全，
    且每个值都在它自己的候选里。**这个函数自己不给默认值、不许只给一部分** ——
    同 `reviewed` 一个道理：给一部分等于替另一些做了决定，`§C5` 不许默认勾选。
    （**不调**这个函数是另一回事，那是第 2 档允许的默认；调了就得给全。）
    """
    d = _load(conn, draft_id)
    if d["state"] != "open":
        raise ScaffoldError(f"{draft_id} 已是 {d['state']}，不能再定类型")
    p = json.loads(d["payload"])

    needs = {x["key"]: list(x["type_candidates"])
             for x in p["propositions"] if x.get("type_candidates")}
    if not needs:
        raise ScaffoldError("这份 draft 里没有待定类型的命题，不需要这个动作。")

    got = dict(choices)
    if sorted(got) != sorted(needs):
        raise ScaffoldError(
            f"待定类型的命题是 {sorted(needs)}，收到 {sorted(got)}。"
            "不能只给一部分 —— 挑子集等于替没给的那些做了决定。"
        )
    for k, v in got.items():
        if v not in needs[k]:
            raise ScaffoldError(
                f"{k} 的类型 {v!r} 不在候选 {needs[k]} 里。"
                "候选是分割器给出的**全部可能**，超出这个范围的类型没有依据。"
            )

    p["resolved_types"] = got
    conn.execute("UPDATE draft SET payload = ? WHERE id = ?",
                 (json.dumps(p, ensure_ascii=False), draft_id))
    record_event(conn, "types_resolved", by, draft_id, {"choices": got})
    conn.commit()
    return got


def confirm(
    conn: sqlite3.Connection, draft_id: str, *,
    by: str, reviewed: list[str], topic_id: str | None = None,
) -> dict:
    """用户逐条确认「意义未被歪曲」→ 整份写入 Scaffold。

    `reviewed` 必须**逐个列出**所有命题的 key。它没有默认值，
    传空、传不全、传 `["all"]` 一律拒绝 —— 那是 `§C5` 禁止的「一键全部确认」。

    `§C2.2.2`：确认的是**意义**，不是标签分配。
    所以这里**没有位置**让调用方挑选「哪些命题进入结构」：给全了才写，给不全就报错。
    """
    d = _load(conn, draft_id)
    if d["state"] != "open":
        raise ScaffoldError(
            f"{draft_id} 已是 {d['state']}，不能确认。"
            + ("（用户已指出意义被歪曲 —— 按 §C5.6 只有换说法重交或不提交两条路。）"
               if d["state"] == "distorted" else "")
        )

    p = json.loads(d["payload"])
    keys = [x["key"] for x in p["propositions"]]
    got = list(reviewed)
    if not got:
        raise ScaffoldError("reviewed 为空。§C5 禁止默认勾选，也禁止一键全部确认。")
    if sorted(got) != sorted(keys):
        raise ScaffoldError(
            f"reviewed 必须逐个列出全部命题 {keys}，收到 {got}。"
            "不能只确认一部分 —— 挑子集等于决定哪段文本能进入结构（§C2.2.2）。"
        )

    # ---- 缺口② 的类型：**默认生效，可推翻，抽检，计改判率**（§C2.5 第 2 档）----
    #
    # 这里原来是「没定完就不写」（第 1 档：新增断言，必须确认）。
    # 需求方 2026-09-25 改成第 2 档，理由是**不让人多判断一次**：
    # 「X，所以 Y」的两端在 20 条样本里是少数，为了它们把每一条都拦下来问一遍，
    # 是把引擎答不出的结构问题转嫁给每一个提交的人。
    #
    # 所以：默认取候选的第一个（`type_candidates[0]`，即因果即**主张**），
    # 谁在 `resolve_types()` 里改过就用改的。**默认不是静默判断**，
    # 因为下面把它记下来了 —— `§C2.5` 第 2 档的四件事缺一不可，
    # 「可推翻」靠 `resolve_types()`，「抽样审计 + 计改判率」靠这条记录。
    # 只说默认生效不记录，就是第 2 档退化成第 1 档的反面：无痕。
    resolved = p.get("resolved_types", {})
    candidates = {x["key"]: list(x["type_candidates"])
                  for x in p["propositions"] if x.get("type_candidates")}
    defaulted = {k: v[0] for k, v in candidates.items() if k not in resolved}
    if defaulted:
        # 只有在**真的**用了默认值时才记。一条都没有就是零条事实，
        # 不写一个空事件进去 —— 空事件会让「这轮没走到」长得像「这轮改判率为 0」。
        record_event(conn, "types_defaulted", by, draft_id,
                     {"defaults": defaulted, "candidates": candidates})

    # ---- 写入 Scaffold ----
    frozen = {
        "input_snapshot": p["input_snapshot"],   # §C5.5 第 5 条
        "segmenter_version": p["segmenter_version"],
        "ruleset_version": p["ruleset_version"],
        "draft": draft_id,
    }
    made = {}
    if topic_id is None:
        topic_id = add_artifact(
            conn, type_="Topic",
            content={**frozen, "text": p["input_snapshot"],
                     "raw_text": p["input_snapshot"]},
            origin=f"machine:segmenter/{p['segmenter_version']}",
        )
        activate(conn, topic_id, by=by)

    machine = f"machine:segmenter/{p['segmenter_version']}"
    chosen = {**defaulted, **resolved}
    for x in p["propositions"]:
        # 没有候选的命题（普通陈述句）仍旧是 Claim —— 那是个**已声明的**默认，
        # 不是这次的空白：只有「X，所以 Y」那两端是引擎答不了的。见 DECLARATION §10。
        cid = add_artifact(
            conn, type_=chosen.get(x["key"], "Claim"),
            content={**frozen, "text": x["text"], "raw_text": x["text"],
                     "role": x["role"], "span": x["span"],
                     # 这个类型是谁定的。没候选的是引擎默认，有候选的是人定的。
                     # 留痕是为了 `§C9` #9：结构不能有一个说不出出处的字段。
                     "type_decided_by": by if x["key"] in resolved else machine,
                     # 候选本身也留在结构里。**默认生效的那条靠它才看得出来**：
                     # 有候选 + type_decided_by 是机器 = 这条走的是默认，
                     # 不是有依据的判断。§C2.5 第 2 档要的就是这个可抽检的痕迹。
                     **({"type_candidates": candidates[x["key"]]}
                        if x["key"] in candidates else {})},
            origin=machine,
        )
        activate(conn, cid, by=by)
        add_relation(conn, kind="contains", from_id=topic_id, to_id=cid, origin=machine)
        made[x["key"]] = cid

    # 因果命题指向前后件。**方向照需求方样本的箭头：端 → 因果主张**
    # （B1 写的是 `n1 ─?→ n3`、`n2 ─?→ n3`；B4 写的是 `n1 ─?→ n2`）。
    # 原来这里用 `related_to` 兜，方向丢了 —— 见缺口①，改为此前没有的两个种类。
    for x in p["propositions"]:
        if x["role"] == "causal":
            for y in p["propositions"]:
                if y["role"] in ("cause", "effect"):
                    add_relation(
                        conn,
                        kind=("causal_premise" if y["role"] == "cause"
                              else "causal_conclusion"),
                        from_id=made[y["key"]], to_id=made[x["key"]],
                        origin=machine)

    conn.execute(
        "UPDATE draft SET state = 'confirmed', decided_at = ? WHERE id = ?",
        (_now(), draft_id),
    )
    record_event(conn, "draft_confirmed", by, draft_id,
                 {"topic": topic_id, "claims": made,
                  "input_snapshot": p["input_snapshot"]})
    conn.commit()
    return {"topic": topic_id, "claims": made}
