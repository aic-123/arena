"""观测点（`§C7.2`）—— 现在埋，阈值等数据。

`§C7.2` 的判据一句话：

```
记录什么    →  现在定
什么叫"高"   →  等数据
```

**为什么观测点不能等**：前三个月的换说法行为没记录，以后也拿不到。
**为什么阈值现在不能定**：没有基线，定出来是拍脑袋，写进规格会误导实现。

--- 本模块只做两件事 -------------------------------------------------------

1. **登记**六个观测点各自怎么算（分子 / 分母 / 事实从哪来）
2. 把底层事实**派生**成视图

--- 本模块刻意不做的事（都是 `§C7.2` 明令的）--------------------------------

- **不说「高 / 低」是多少** —— 没有阈值，没有报警线，没有及格线。
  一条也没有，也不会有。`checks.py` 的 B6 盯着这件事。
- **不把派生结果写回底层** —— 比率不是事实（`§C7.1` ④）。
  `snapshot()` 是只读的，有测试断言它跑完不改动任何一行。
- **不新增需要用户配合的动作** —— 只读既有事件（重写 / 改判 / 放弃）。
  ⚠️ `§C7.2`：若用户知道自己在被计时，确认行为就会变。所以这里一个
  「请评价本次确认体验」之类的动作都没有。
- **不新建一套独立监控系统** —— `§C7.2`：观测点是底层事实，评估视图是上层派生。

--- 一条最容易犯的错，写在最前面 -------------------------------------------

**分母为 0 必须返回「算不出」，不许返回 0.0。**

0 / 0 悄悄渲染成 0，会让「这个观测点还没开始记」长得和「比率是零」一模一样。
而在这个系统里，「还没开始记」是常态 —— 分类没做、样本没标、边还没建。
把两者混起来，等于给自己造一份**看起来很正常的空数据**。
`_ratio()` 因此对分母为 0 一律返回 `None`。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from scaffold import events_of_kind, record_event

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

# ---------------------------------------------------------------------------
# 登记表（`§C7.2` 的六个观测点，逐条对应）
#
# 这里的每一条都只写**怎么记 / 怎么算**，不写「多少算高」。
# ---------------------------------------------------------------------------

OBSERVATION_POINTS = (
    {
        "id": "rewording",
        "name": "换说法率",
        "layer": "输入层",
        "clause": "§C5.6 ② §C2.4 §C13.1 #3",
        "answers": "结构化成本是否回流到用户（§C2.0 的核心承诺）",
        "numerator": "先点了「意义被歪曲」、随后重写过的用户数",
        "denominator": "提交过的用户数",
        "facts": "event：meaning_flagged → draft_revised(was_flagged=True)",
        "caveat": "这是**下界** —— 用户懒得点就漏计。只能看趋势，不能当绝对水平读。",
    },
    {
        "id": "unused",
        "name": "未采用片段占比",
        "layer": "输入层",
        "clause": "§C5.2 §C7.2",
        "answers": "分割覆盖是否足够",
        "numerator": "未被任何命题覆盖的非空白字符数（各份相加）",
        "denominator": "非空白字符总数（各份相加）",
        "facts": "draft.payload 的 unused_char_count / total_char_count",
        "caveat": "样本（§C5.5.1）折出了 proposed_count（20/20），boundaries 只折出 "
                  "4/20，而且这 4 条全是逐字样本 —— 所以「切得对不对」仍然说不了，"
                  "只能说「切了多少」。真正需要参照物的转述节点一个都没折出来。"
                  "而且折出来的值有一条共同来源（标签是需求方的、折的是 agent），"
                  "拿它算的一致率不是独立观测。见 samples/annotation-derived.md。",
    },
    {
        "id": "overrule",
        "name": "改判率",
        "layer": "结构层",
        "clause": "§C2.4",
        "answers": "AI 结构化是否可信",
        "numerator": "被用户拒绝的、机器产出的边数",
        "denominator": "机器产出的边总数",
        "facts": "relation 表里 origin 以 machine: 开头的行，按 state 分",
        "caveat": "双向歧义：长期偏高说明结构化不可用；长期接近零**不能**读作"
                  "「AI 很准」——也可能是根本没人在看。两个方向都要人工抽检。",
    },
    {
        "id": "self_edit",
        "name": "用户对自己命题的修改率",
        "layer": "结构层",
        "clause": "§C2.4",
        "answers": "反省是否发生",
        "numerator": "被作者退回填充阶段重写过的 draft 数",
        "denominator": "全部 draft 数",
        "facts": "draft 表里 revised_from 非空的行",
        "caveat": "单位是 **draft**，不是用户 —— 换说法率的单位是用户，两者不同，"
                  "不能互相替代（§C2.4）。",
    },
    {
        "id": "unrelated",
        "name": "unrelated 占比",
        "layer": "结构层",
        "clause": "§C7.2",
        "answers": "ontology 覆盖是否足够",
        "numerator": "落进 unrelated 桶的条目数",
        "denominator": "全部被分类过的条目数",
        "facts": "event：classification_assigned（分类成功）/ classification_unrelated（落进桶里）",
        "caveat": "`unrelated` 是**垃圾桶类别**。它的占比升高 = ontology 覆盖不足，"
                  "**不是**「数据质量差」。分类在第 07 步才开始跑，"
                  "所以这个分母现在是 0 —— 显示「算不出」，**不是** 0。",
    },
    {
        "id": "confirm",
        "name": "确认耗时 / 中途放弃",
        "layer": "确认环节",
        "clause": "§C5.6 ③ §C7.2",
        "answers": "确认是否过重",
        "numerator": "已放弃的 draft 数",
        "denominator": "全部 draft 数",
        "facts": "draft.created_at / decided_at 的差；event：draft_abandoned",
        "caveat": "这是 `§C5.6` ③ 的 **A 类**（已输入后离开），系统内可观测。"
                  "不要为它另建机制。",
    },
)

# `§C7.2`：B 类「未输入即离开」连门都没进，**不产生任何系统内记录**。
# 登记在这里是为了不让它被忘掉 —— 但它测不到，也不许假装测得到。
NOT_MEASURABLE = (
    {
        "name": "未输入即离开的人数",
        "layer": "确认环节",
        "clause": "§C5.6 ③ B 类",
        "why": "连门都没进，系统内不产生任何记录。只能访谈，或另做埋点。",
        "we_do": "不做。也不在系统内伪造一个近似值。",
    },
)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, TS_FORMAT).replace(tzinfo=timezone.utc)


def _ratio(num: int, den: int):
    """分母为 0 → `None`（算不出），**不是** 0.0。理由见模块开头。"""
    return None if not den else num / den


# ---------------------------------------------------------------------------
# 埋点：唯一一个需要新记的事实
# ---------------------------------------------------------------------------

def record_unrelated(
    conn: sqlite3.Connection, *, text: str, origin: str, reason: str,
) -> None:
    """记一次「落不进封闭枚举」的事实（`§C7.2` 的垃圾桶类别）。

    这是**记事实**，不是记评价：记的是「有一段内容落不进分类」，
    **不是**「这段内容质量差」。`§C7.1` ④ 禁的是后者 ——
    把评价写成底层字段，等于把派生视图固化成真值判断。

    第 07 步的分类器接上之后由它调用。现在没有任何调用方，
    所以 `unrelated` 的分母是 0 —— 视图会如实显示「算不出」，不会显示 0。
    """
    record_event(conn, "classification_unrelated", origin, "",
                 {"text": text, "reason": reason})
    conn.commit()


def record_classified(
    conn: sqlite3.Connection, *, text: str, artifact_type: str, origin: str,
) -> None:
    """记一次**分类成功**。

    它存在的唯一理由是给「unrelated 占比」提供正确的分母 ——
    分母必须是**分类尝试的次数**，不是库存里的 artifact 总数。
    少了它，那个观测点永远算不出（见 `snapshot` 里的注释）。

    第 07 步的分类器接上之后由它调用。**现在没有调用方。**
    """
    record_event(conn, "classification_assigned", origin, "",
                 {"text": text, "artifact_type": artifact_type})
    conn.commit()


# ---------------------------------------------------------------------------
# 派生视图（只读）
# ---------------------------------------------------------------------------

def _entry(point: dict, num: int, den: int) -> dict:
    value = _ratio(num, den)
    return {
        "id": point["id"],
        "观测点": point["name"],
        "层": point["layer"],
        "条款": point["clause"],
        "它回答": point["answers"],
        "怎么算": f"{point['numerator']}  /  {point['denominator']}",
        "事实来源": point["facts"],
        "分子": num,
        "分母": den,
        "值": value,
        "算得出": value is not None,
        "备注": point["caveat"],
    }


def snapshot(conn: sqlite3.Connection) -> dict:
    """把底层事实派生成视图。**只读** —— 一行都不改（有测试盯着）。"""
    by_id = {p["id"]: p for p in OBSERVATION_POINTS}
    drafts = conn.execute("SELECT * FROM draft ORDER BY id").fetchall()

    # ---- 换说法率：单位是**用户**（§C5.6 ②） ----
    flagged = {
        e["actor"]
        for e in events_of_kind(conn, "draft_revised")
        if json.loads(e["payload"])["was_flagged"]
    }

    # ---- 未采用片段占比：分子分母逐份相加 ----
    missed = total = 0
    for d in drafts:
        p = json.loads(d["payload"])
        missed += p["unused_char_count"]
        total += p["total_char_count"]

    # ---- 改判率：机器产出的边 ----
    machine_edges = conn.execute(
        "SELECT COUNT(*) AS n FROM relation WHERE origin LIKE 'machine:%'"
    ).fetchone()["n"]
    overruled = conn.execute(
        "SELECT COUNT(*) AS n FROM relation"
        " WHERE origin LIKE 'machine:%' AND state = 'rejected'"
    ).fetchone()["n"]

    # ---- 自己命题的修改率：单位是 **draft** ----
    self_edited = sum(1 for d in drafts if d["revised_from"])

    # ---- unrelated：桶里的事实 / **分类尝试过**的条目 ----
    #
    # ⚠️ 分母**不能**用 artifact 总数。用 artifact 总数的话，一个什么都还没分类的库
    # 会显示 `0 / 2 → 0.0000` —— 也就是「还没开始记」被渲染成了「比率是零」。
    # 那是本模块开头警告的那个错，只是换了个方式犯。
    # 正确的分母是**分类尝试的次数**：分类器每处理一条就记一次，
    # 落不进枚举的额外记一次进桶。
    bucket = len(events_of_kind(conn, "classification_unrelated"))
    assigned = len(events_of_kind(conn, "classification_assigned"))
    classified = bucket + assigned

    # ---- 确认环节 ----
    spent = [
        (_parse(d["decided_at"]) - _parse(d["created_at"])).total_seconds()
        for d in drafts if d["decided_at"]
    ]
    dropped = len(events_of_kind(conn, "draft_abandoned"))

    out = {
        "rewording": _entry(by_id["rewording"], len(flagged), len({d["actor"] for d in drafts})),
        "unused": _entry(by_id["unused"], missed, total),
        "overrule": _entry(by_id["overrule"], overruled, machine_edges),
        "self_edit": _entry(by_id["self_edit"], self_edited, len(drafts)),
        "unrelated": _entry(by_id["unrelated"], bucket, classified),
        "confirm": _entry(by_id["confirm"], dropped, len(drafts)),
    }
    # 确认耗时不是比率，单列 —— 不聚合、不取平均，避免「多少算久」被顺手读出来。
    out["confirm"]["每份耗时秒数"] = sorted(round(s, 3) for s in spent)
    out["confirm"]["已作出决定的 draft 数"] = len(spent)
    return out


def render(conn: sqlite3.Connection) -> str:
    """给人看的一页。**只报数，不判高低。**"""
    snap = snapshot(conn)
    lines = [
        "观测点（§C7.2）—— 现在埋，阈值等数据",
        "",
        "⚠️ 下面所有的数**没有高低之分**。这里不说什么叫「高」，也不会有。",
        "   分母为 0 的显示「算不出」—— 那是「还没开始记」，不是「比率是零」。",
        "",
    ]
    for pid in ("rewording", "unused", "overrule", "self_edit", "unrelated", "confirm"):
        e = snap[pid]
        shown = "算不出" if not e["算得出"] else f"{e['值']:.4f}"
        lines += [
            f"── {e['观测点']}  [{e['层']}]  {e['条款']}",
            f"   它回答：{e['它回答']}",
            f"   怎么算：{e['怎么算']}",
            f"   事实来源：{e['事实来源']}",
            f"   {e['分子']} / {e['分母']}  →  {shown}",
            f"   备注：{e['备注']}",
        ]
        if "每份耗时秒数" in e:
            lines.append(f"   每份确认耗时（秒）：{e['每份耗时秒数']}")
        lines.append("")

    lines.append("── 系统内测不到的（不许假装测得到）")
    for n in NOT_MEASURABLE:
        lines += [
            f"   {n['name']}  [{n['layer']}]  {n['clause']}",
            f"     {n['why']}",
            f"     我们的处理：{n['we_do']}",
        ]
    return "\n".join(lines)
