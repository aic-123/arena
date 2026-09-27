"""最小可跑检查。

覆盖 `§T2` 第 02–04 步，以及三条**必须靠机制而不是靠自觉成立**的约束：
`§C3.2`（Relation 是可追踪对象）、`§C10`（禁止覆盖）、`§C9` #3（机器不得自授 verified）。

    python test_arena.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import checks
import confirm
import debate
import hints
import observe
import scaffold
import segment
import vote
from scaffold import ScaffoldError

SAMPLE = "现在社会对女性太好了，所以她们根本不懂男性压力。"


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = scaffold.connect(":memory:")
        scaffold.init(self.conn)


def _confirmed(conn, *, debate_id, text, by, topic_id=None) -> dict:
    """走**真路**提交一句话：propose → 逐条确认 → 进讨论。

    `topic_id=None` 新开一个 Topic（`debate.open_topic`）；给一个已有 Topic 就挂上去
    （`debate.add_to_topic`）。两条出口都是产品里真有的动作 ——
    **测试不许自己造捷径**（`§12.1`：测试得自己造产品没有的能力，那个能力就不存在）。

    2026-09-25 之前这里走的是 `debate.add_position()`：不分割、不逐条确认、
    只存 `{text, raw_text}`。那条路已经删了。
    """
    confirm.ensure_schema(conn)          # 走真路就得有 draft 表
    draft = confirm.propose(conn, text=text, by=by)
    keys = [x["key"] for x in confirm.payload_of(conn, draft)["propositions"]]
    if topic_id is None:
        return debate.open_topic(conn, debate_id=debate_id, draft_id=draft,
                                 by=by, reviewed=keys)
    return debate.add_to_topic(conn, topic_id=topic_id, draft_id=draft,
                               by=by, reviewed=keys)


def _one_claim(conn, *, debate_id, text, by, topic_id=None) -> str:
    """同上，但只用于**单命题**文本，直接返回那一条的 id（测试里绝大多数是这种）。"""
    out = _confirmed(conn, debate_id=debate_id, text=text, by=by, topic_id=topic_id)
    ids = list(out["claims"].values())
    if len(ids) != 1:
        raise AssertionError(f"「{text}」被切成了 {len(ids)} 条 —— 这个 helper 只用于单命题")
    return ids[0]


def _topic_and_first_claim(conn, *, debate_id, text, by) -> tuple[str, str]:
    """走真路开一个 Topic，返回 `(topic_id, 第一条命题的 id)`。

    `open_debate()` 现在**不带 Topic**（见它的 docstring），所以「先白拿一个 Topic、
    再往上挂命题」那种写法没有了。本 helper 走的就是产品那条路：
    「提交 → 分割 → 逐条确认 → 新开一个 Topic」。用例要 Topic 就得这么要一个。
    """
    out = _confirmed(conn, debate_id=debate_id, text=text, by=by)
    return out["topic"], list(out["claims"].values())[0]


class TestStep02MinimalArtifact(Base):
    def test_artifact_gets_identity_state_and_origin(self):
        aid = scaffold.add_artifact(
            self.conn, type_="Topic", content={"text": "问题"}, origin="user:alice"
        )
        row = scaffold.get(self.conn, aid)
        self.assertTrue(row["id"])                       # 独立身份
        self.assertEqual(row["state"], "proposed")       # 状态
        self.assertEqual(row["origin"], "user:alice")    # 可追溯
        self.assertEqual(row["status"], "unresolved")    # 默认不是 verified

    def test_cannot_create_active_directly(self):
        """`§C5`：未经确认不得写入 Scaffold。这条不能有例外。"""
        with self.assertRaises(ScaffoldError):
            scaffold.add_artifact(
                self.conn, type_="Claim", content={"text": "x"},
                origin="user:a", state="active",
            )


class TestStep03MinimalScaffold(Base):
    def setUp(self):
        super().setUp()
        self.a = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": "A"}, origin="user:a")
        self.b = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": "B"}, origin="user:b")

    def test_relation_is_a_trackable_object_not_a_field(self):
        """`§C3.2` 硬约束：Relation 有身份、有来源、可拒绝。"""
        rid = scaffold.add_relation(
            self.conn, kind="contradicts",
            from_id=self.a, to_id=self.b, origin="user:a",
        )
        row = self.conn.execute(
            "SELECT * FROM relation WHERE id = ?", (rid,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["origin"], "user:a")     # 来源
        self.assertEqual(row["state"], "active")

        scaffold.reject_relation(self.conn, rid, by="user:b")
        row = self.conn.execute(
            "SELECT * FROM relation WHERE id = ?", (rid,)).fetchone()
        self.assertEqual(row["state"], "rejected")    # 可拒绝，且不删
        # 不删是为了让 `§C2.4` 的改判率算得出来
        self.assertEqual(
            len(scaffold.events_of_kind(self.conn, "relation_rejected")), 1)

    def test_relation_kinds_cover_C4_not_only_C3_2(self):
        """`§C4` 要求的四种边必须在册，否则 §C4 的映射无法逐条落地。"""
        for kind in ("qualifies", "assumes", "explains", "challenged_by"):
            self.assertIn(kind, scaffold.RELATION_KINDS)

    def test_unknown_relation_kind_is_refused(self):
        with self.assertRaises(ScaffoldError):
            scaffold.add_relation(
                self.conn, kind="is_better_than",
                from_id=self.a, to_id=self.b, origin="machine:x",
            )


class TestRevisionNoOverwrite(Base):
    def test_old_versions_are_kept(self):
        """`§C10`：记录，不停止，不限制。旧版本一行都不许改。"""
        aid = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": "v1"}, origin="user:a")
        scaffold.revise(self.conn, aid, content={"text": "v2"}, author="user:a")
        texts = [r["content"] for r in self.conn.execute(
            "SELECT content FROM revision WHERE artifact_id = ? ORDER BY id", (aid,))]
        self.assertEqual(len(texts), 2)
        self.assertIn("v1", texts[0])          # 旧版本还在
        self.assertIn("v2", texts[1])

    def test_concurrent_edits_branch_and_never_auto_pick(self):
        """`§C10` 的 `Q(v1) → Q(v2a) / Q(v2b)`，以及「系统不替你选分支」。"""
        aid = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": "v1"}, origin="user:a")
        root = scaffold.heads_of(self.conn, aid)[0]["id"]

        scaffold.revise(self.conn, aid, content={"text": "v2a"},
                        author="user:a", parent_rev=root)
        scaffold.revise(self.conn, aid, content={"text": "v2b"},
                        author="user:b", parent_rev=root)

        heads = scaffold.heads_of(self.conn, aid)
        self.assertEqual(len(heads), 2)        # 两个分支都在

        with self.assertRaises(ScaffoldError):
            scaffold.revise(self.conn, aid, content={"text": "v3"}, author="user:a")


class TestVerifiedIsHookOnly(Base):
    def test_normal_path_cannot_write_verified(self):
        """`§C9` #3。这是不靠自觉的一条 —— 没有别的写入路径。"""
        aid = scaffold.add_artifact(
            self.conn, type_="Evidence", content={"note": "x"}, origin="machine:seg")
        with self.assertRaises(ScaffoldError):
            scaffold.set_status(self.conn, aid, "verified", by="machine:seg")

    def test_grant_verified_requires_a_named_hook(self):
        aid = scaffold.add_artifact(
            self.conn, type_="Evidence", content={"note": "x"}, origin="machine:seg")
        with self.assertRaises(ScaffoldError):
            scaffold.grant_verified(self.conn, aid, hook="")
        scaffold.grant_verified(self.conn, aid, hook="peer-review-2026")
        self.assertEqual(scaffold.get(self.conn, aid)["status"], "verified")


class TestStep04Debate(Base):
    def test_a_debate_runs_end_to_end(self):
        d = debate.open_debate(
            self.conn, question="现在社会对女性太好了，所以她们根本不懂男性压力。",
            by="user:alice")
        # 一段原文一个 Topic：甲先交一段（新开一个 Topic），乙的话挂进**同一个** Topic。
        # 谁和谁在同一个议题里争，由 Debate 那一层表达。
        topic, _ = _topic_and_first_claim(
            self.conn, debate_id=d["debate"],
            text="女性获得了更好的社会待遇", by="user:alice")
        _one_claim(self.conn, debate_id=d["debate"],
                   text="女性不足够理解男性压力", by="user:bob", topic_id=topic)

        v = debate.view(self.conn, d["debate"])
        self.assertEqual(len(v["topics"]), 1)
        claims = v["topics"][0]["claims"]
        self.assertEqual(len(claims), 2)
        self.assertTrue(all(c["claim"]["state"] == "active" for c in claims))

    def test_view_keeps_submission_order_only(self):
        """`§C9` #5 #7：视图里不许出现任何表示「哪条更重要」的量。"""
        d = debate.open_debate(self.conn, question="Q", by="user:a")
        topic, _ = _topic_and_first_claim(
            self.conn, debate_id=d["debate"], text="立场 user:a", by="user:a")
        for who in ("user:b", "user:c"):
            _one_claim(self.conn, debate_id=d["debate"], text=f"立场 {who}",
                       by=who, topic_id=topic)
        v = debate.view(self.conn, d["debate"])
        claims = v["topics"][0]["claims"]
        # 顺序按 id，不按提交者、不按票数、不按时间
        self.assertEqual([c["claim"]["id"] for c in claims],
                         ["claim-0001", "claim-0002", "claim-0003"])


class TestConfirmedDraftEntersADebate(Base):
    """第 05 步（确认）和第 04 步（讨论）原来**接不上** —— 这个类钉那个接缝。

    2026-09-25 走真人流程时才发现：确认路径造的 Topic 不属于任何 Debate，
    而 `view()` 是从 Debate 往下走的。此前两条路都不对，见 `debate.open_topic`
    的 docstring。这个类就是那条断掉的接缝的回归检查。
    """

    def setUp(self):
        super().setUp()
        confirm.ensure_schema(self.conn)
        self.d = debate.open_debate(self.conn, question="加班该不该给钱？", by="甲")

    def _submit(self, text, who):
        """甲/乙 各交一段话 —— 走完整的 分割 → 确认 → 进讨论 的路。"""
        d = confirm.propose(self.conn, text=text, by=who)
        keys = [x["key"] for x in confirm.payload_of(self.conn, d)["propositions"]]
        return debate.open_topic(self.conn, debate_id=self.d["debate"],
                                 draft_id=d, by=who, reviewed=keys)

    def test_two_people_get_two_topics_and_the_view_shows_both(self):
        """**这是这个类存在的理由**：以前第二段原文在视图里是看不见的。"""
        a = self._submit("现在的工作强度太大了，所以大家都不愿意往上爬。", "甲")
        b = self._submit("加班费算不算劳动报酬，取决于你说的是短期还是长期。", "乙")

        v = debate.view(self.conn, self.d["debate"])
        ids = [t["topic"]["id"] for t in v["topics"]]
        # 两份 draft 各自的 Topic —— `open_debate` 现在不带 Topic 了，
        # 所以视图里就是这两条，一条不多。
        self.assertEqual(ids, [a["topic"], b["topic"]])

        by_topic = {t["topic"]["id"]: t for t in v["topics"]}
        self.assertEqual([c["claim"]["id"] for c in by_topic[a["topic"]]["claims"]],
                         list(a["claims"].values()))
        self.assertEqual([c["claim"]["id"] for c in by_topic[b["topic"]]["claims"]],
                         list(b["claims"].values()))

    def test_each_topic_keeps_its_own_raw_text_so_hints_look_at_the_right_one(self):
        """分歧定位是按 Topic 的原文扫的。两段话混进一个 Topic，第二段就扫不到。"""
        self._submit("现在的工作强度太大了，所以大家都不愿意往上爬。", "甲")
        b = self._submit("加班费算不算劳动报酬，取决于你说的是短期还是长期。", "乙")

        by_topic = {t["topic"]["id"]: t
                    for t in debate.view(self.conn, self.d["debate"])["topics"]}
        raw = scaffold.original_content_of(self.conn, b["topic"])["raw_text"]
        self.assertEqual(raw, "加班费算不算劳动报酬，取决于你说的是短期还是长期。")
        # `§C2.1`：提示指回**用户自己写过的**位置，两者必须是同一段原文。
        # 乙这一段里有「取决于」，所以这个 Topic 必须扫出至少一条 ——
        # 塞进甲的 Topic 时，这条提示永远不会出现。
        hints = by_topic[b["topic"]]["subquestion_hints"]["hints"]
        self.assertTrue(hints, "乙自己写的「取决于」没被扫出来 —— 提示扫的不是这一段")
        for h in hints:
            self.assertIn(h["quoted"], raw)

    def test_a_confirmed_draft_carries_its_snapshot_into_the_debate(self):
        """确认路径写进去的东西，在讨论视图里字段是全的（`§C5.5` 第 5 条）。"""
        a = self._submit("远程办公的效率比坐办公室高。", "甲")
        by_topic = {t["topic"]["id"]: t
                    for t in debate.view(self.conn, self.d["debate"])["topics"]}
        content = json.loads(by_topic[a["topic"]]["claims"][0]["heads"][0]["content"])
        self.assertEqual(content["input_snapshot"], "远程办公的效率比坐办公室高。")
        self.assertEqual(content["span"], [0, 13])

    def test_passing_a_topic_id_where_a_debate_belongs_is_refused(self):
        """传错 id 必须报错。`view()` 传错会静默返回一个形状正常的结果 —— 这里不学它。"""
        a = self._submit("远程办公的效率比坐办公室高。", "甲")
        with self.assertRaises(ScaffoldError):
            debate.open_topic(self.conn, debate_id=a["topic"], draft_id="x",
                              by="甲", reviewed=["A"])
        with self.assertRaises(ScaffoldError):
            debate.open_topic(self.conn, debate_id="debate-9999", draft_id="x",
                              by="甲", reviewed=["A"])

    def test_it_generates_nothing_of_its_own(self):
        """`§C2.0`：议题由用户提出。这个动作只是搬运，一个字的文本都不生成。"""
        a = self._submit("远程办公的效率比坐办公室高。", "甲")
        raw = scaffold.original_content_of(self.conn, a["topic"])["raw_text"]
        self.assertEqual(raw, "远程办公的效率比坐办公室高。")
        self.assertNotIn("机器", json.dumps(
            scaffold.original_content_of(self.conn, a["topic"]), ensure_ascii=False))


class TestStep05Segmenter(Base):
    def test_no_semantic_rewriting(self):
        """需求方 2026-09-25 当场指示：不做语义改写。

        每个命题的 text 必须是原文的**逐字切片** —— 这条断言就是「没改写」的定义。
        """
        r = segment.segment(SAMPLE)
        for p in r["propositions"]:
            lo, hi = p["span"]
            self.assertEqual(p["text"], SAMPLE[lo:hi],
                             f"命题 {p['key']} 的 text 不是原文切片，说明发生了改写")

    def test_C5_sample_splits_into_three(self):
        """`§C5` 的示例：A 因 / B 果 / C 因果连接词独立成命题。"""
        r = segment.segment(SAMPLE)
        roles = [p["role"] for p in r["propositions"]]
        self.assertEqual(roles, ["cause", "effect", "causal"])
        causal = [p for p in r["propositions"] if p["role"] == "causal"][0]
        self.assertEqual(causal["trigger"], "所以")
        # §C5：因果连接词被识别为独立命题时，这个识别过程必须可见
        self.assertIn("识别为独立命题", causal["why"])

    def test_reproducible(self):
        """`§C5.5` 硬约束：同一输入两次分割结果必须一致。"""
        self.assertEqual(segment.segment(SAMPLE), segment.segment(SAMPLE))

    def test_connective_behind_the_clause_does_not_eat_the_effect(self):
        """需求方样本 0007 暴露的缺陷（规则集 zh-clause-1 → zh-clause-2）。

        「他成绩好是因为聪明」里连接词不在句首，**它前面那截才是果**。
        老实现把 `before` 整个丢掉，再把逗号后的下一句错标成「果」——
        于是「不是因为努力」变成了「聪明」的果。
        """
        r = segment.segment("他成绩好是因为聪明，不是因为努力。")
        got = {p["role"]: p["text"] for p in r["propositions"]}
        self.assertEqual(got["cause"], "聪明")
        self.assertEqual(got["effect"], "他成绩好")      # 不是「不是因为努力」
        self.assertEqual(got["clause"], "不是因为努力")   # 它是一句独立的话
        # 任何命题都不许凭空消失：全部仍逐字来自原文
        for p in r["propositions"]:
            lo, hi = p["span"]
            self.assertEqual(p["text"], "他成绩好是因为聪明，不是因为努力。"[lo:hi])

    def test_unused_is_reported_not_dropped(self):
        """`§C5.2`：未切出的部分必须显式呈现，不得静默丢弃。"""
        r = segment.segment(SAMPLE)
        covered = sum(len(u["text"]) for u in r["unused_spans"])
        self.assertEqual(covered, 2)             # 「，」与「。」
        self.assertTrue(all(u["text"] == SAMPLE[u["span"][0]:u["span"][1]]
                            for u in r["unused_spans"]))


class TestStep05Confirm(Base):
    def setUp(self):
        super().setUp()
        confirm.ensure_schema(self.conn)
        self.d = confirm.propose(self.conn, text=SAMPLE, by="user:alice")

    def test_render_shows_all_three_parts(self):
        """`§C5.2` 的判据：原文全文 + 被切出的 + 未切出的（带计数），同时在。"""
        out = confirm.render(self.conn, self.d)
        self.assertIn(SAMPLE, out)          # 原文全文
        self.assertIn("【现在社会对女性太好了】", out)   # 高亮
        self.assertIn("〔，〕", out)          # 未切出，显式呈现
        self.assertIn("未采用部分（2 段", out)          # 带计数
        self.assertNotIn("阈值", out)        # §C7.2：只报数，不判高低

    def test_no_default_and_no_bulk_confirm(self):
        """`§C5`：不得默认勾选，不得一键全部确认。"""
        with self.assertRaises(ScaffoldError):
            confirm.confirm(self.conn, self.d, by="user:alice", reviewed=[])
        with self.assertRaises(ScaffoldError):
            confirm.confirm(self.conn, self.d, by="user:alice", reviewed=["all"])

    def test_cannot_pick_a_subset_to_enter(self):
        """`§C2.2.2`：用户不能决定哪段文本能进入结构。"""
        with self.assertRaises(ScaffoldError):
            confirm.confirm(self.conn, self.d, by="user:alice", reviewed=["A", "B"])

    def test_confirmed_propositions_enter_the_scaffold(self):
        out = confirm.confirm(self.conn, self.d, by="user:alice",
                            reviewed=["A", "B", "C"])
        self.assertEqual(set(out["claims"]), {"A", "B", "C"})
        for cid in out["claims"].values():
            self.assertEqual(scaffold.get(self.conn, cid)["state"], "active")
        self.assertEqual(scaffold.get(self.conn, out["topic"])["state"], "active")

    def test_frozen_versions_are_recorded(self):
        """`§C5.5` 第 5 条：模型版本 / 提示词版本 / 输入快照（此处为引擎与规则集版本）。"""
        out = confirm.confirm(self.conn, self.d, by="user:alice",
                            reviewed=["A", "B", "C"])
        rev = self.conn.execute(
            "SELECT content FROM revision WHERE artifact_id = ? ORDER BY id LIMIT 1",
            (out["claims"]["A"],)).fetchone()
        content = json.loads(rev["content"])
        self.assertEqual(content["input_snapshot"], SAMPLE)
        self.assertTrue(content["segmenter_version"])
        self.assertTrue(content["ruleset_version"])

    def test_flagged_meaning_blocks_the_write(self):
        """`§C5.6`：用户指出意义被歪曲 → 整份不写入，只有换说法或不提交两条路。"""
        confirm.mark_distorted(self.conn, self.d, by="user:alice")
        with self.assertRaises(ScaffoldError):
            confirm.confirm(self.conn, self.d, by="user:alice",
                            reviewed=["A", "B", "C"])

    def test_abandoning_is_recorded(self):
        """`§C5.6` ③ A 类：已输入后离开 —— 系统内可见，必须记。"""
        confirm.abandon(self.conn, self.d, by="user:alice")
        evs = scaffold.events_of_kind(self.conn, "draft_abandoned")
        self.assertEqual(len(evs), 1)

    def test_nothing_is_written_before_confirmation(self):
        """`§C5`：未经确认不得写入 Scaffold。"""
        n = self.conn.execute("SELECT COUNT(*) AS n FROM artifact").fetchone()["n"]
        self.assertEqual(n, 0)


class TestStep05ReviseWhole(Base):
    """需求方 2026-09-25：修改要**退回到填充时整体修改**。

    没有「改某一条命题」这个动作 —— 所以这里逐条验证的，正是
    「用户只能整句重写，且旧的那份一个字都不许动」。
    """

    def setUp(self):
        super().setUp()
        confirm.ensure_schema(self.conn)
        self.d = confirm.propose(self.conn, text=SAMPLE, by="user:alice")

    def test_revise_creates_a_whole_new_draft_and_keeps_the_old_one(self):
        new = "现在社会对女性太苛刻了，所以她们的压力同样被忽视。"
        new_id = confirm.revise(self.conn, self.d, text=new, by="user:alice")

        old = self.conn.execute("SELECT * FROM draft WHERE id = ?", (self.d,)).fetchone()
        self.assertEqual(old["state"], "revised")
        self.assertEqual(old["raw_text"], SAMPLE)          # 旧的一行不改

        fresh = self.conn.execute("SELECT * FROM draft WHERE id = ?", (new_id,)).fetchone()
        self.assertEqual(fresh["state"], "open")
        self.assertEqual(fresh["raw_text"], new)           # 是整句重写
        self.assertEqual(fresh["revised_from"], self.d)    # 链留下了

    def test_no_per_proposition_editing_exists(self):
        """能拆开改 = 能按条取舍 = `§C2.2.2` 划给机器的权力。所以压根没有这个入口。"""
        for name in ("edit", "edit_proposition", "update_proposition"):
            self.assertFalse(hasattr(confirm, name),
                             f"confirm.{name} 不该存在 —— 修改只能整体重写")

    def test_rewriting_after_flagging_is_marked_as_a_rewording(self):
        """`§C5.6` ②：先点「意义被歪曲」再重写 → 计入换说法率。这个标记就是归因口径。"""
        confirm.mark_distorted(self.conn, self.d, by="user:alice")
        confirm.revise(self.conn, self.d, text="换个说法。", by="user:alice")

        ev = scaffold.events_of_kind(self.conn, "draft_revised")[0]
        payload = json.loads(ev["payload"])
        self.assertEqual(payload["from_state"], "distorted")
        self.assertTrue(payload["was_flagged"])

    def test_rewriting_without_flagging_is_not_counted(self):
        """没点就直接重写 = 自己改主意，不计入换说法率。"""
        confirm.revise(self.conn, self.d, text="自己改主意了。", by="user:alice")
        ev = scaffold.events_of_kind(self.conn, "draft_revised")[0]
        self.assertFalse(json.loads(ev["payload"])["was_flagged"])

    def test_confirmed_content_is_not_rewritten_as_a_draft(self):
        """已确认的内容进了 Scaffold，改它走 `§C10` 的 revision，不是重开 draft。"""
        confirm.confirm(self.conn, self.d, by="user:alice", reviewed=["A", "B", "C"])
        with self.assertRaises(ScaffoldError):
            confirm.revise(self.conn, self.d, text="改一下。", by="user:alice")


class TestStep06ObservationPoints(Base):
    """`§C7.2`：观测点现在埋，**阈值一条都不定**。

    这些测试钉的是「记什么、怎么记」，以及几条**不许犯的错**——
    尤其是「分母为 0 不许渲染成 0」。
    """

    def setUp(self):
        super().setUp()
        confirm.ensure_schema(self.conn)

    def test_six_points_are_registered_and_the_unmeasurable_one_is_too(self):
        self.assertEqual(len(observe.OBSERVATION_POINTS), 6)
        ids = {p["id"] for p in observe.OBSERVATION_POINTS}
        self.assertEqual(ids, {"rewording", "unused", "overrule",
                               "self_edit", "unrelated", "confirm"})
        # B 类「未输入即离开」测不到 —— 登记它，是为了不假装它不存在
        self.assertEqual(len(observe.NOT_MEASURABLE), 1)

    def test_every_point_states_its_numerator_and_denominator(self):
        """`§C7.2`：**定义怎么算**是可以做的；说「多少算高」不可以。"""
        for p in observe.OBSERVATION_POINTS:
            self.assertTrue(p["numerator"] and p["denominator"], p["id"])
            self.assertTrue(p["clause"] and p["facts"], p["id"])

    def test_empty_data_yields_not_computable_never_zero(self):
        """最容易犯的错：0/0 渲染成 0，让「还没开始记」长得像「比率是零」。"""
        snap = observe.snapshot(self.conn)
        for pid in ("rewording", "unused", "overrule", "self_edit", "unrelated", "confirm"):
            snap[pid]["分母"] = 0
        self.assertIsNone(observe._ratio(0, 0))
        self.assertIsNone(observe._ratio(5, 0))
        # 全空库下，unrelated 的分母是 0 → 算不出，而**不是** 0.0
        fresh = observe.snapshot(self.conn)
        self.assertEqual(fresh["unrelated"]["分母"], 0)
        self.assertFalse(fresh["unrelated"]["算得出"])
        self.assertIsNone(fresh["unrelated"]["值"])

    def test_snapshot_is_read_only(self):
        """`§C7.1` ④：派生视图不得写回底层。"""
        confirm.propose(self.conn, text=SAMPLE, by="user:alice")
        before = self._dump()
        observe.snapshot(self.conn)
        observe.render(self.conn)
        self.assertEqual(self._dump(), before)

    def _dump(self):
        return {
            t: self.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            for t in ("artifact", "revision", "relation", "event", "draft")
        }

    def test_rewording_counts_users_not_events(self):
        """`§C5.6` ②：分子分母的单位都是**用户数**。"""
        a = confirm.propose(self.conn, text=SAMPLE, by="user:alice")
        confirm.mark_distorted(self.conn, a, by="user:alice")
        confirm.revise(self.conn, a, text="换一种说法重交。", by="user:alice")
        # alice 又提了一份，但没被标歪曲、也没重写
        confirm.propose(self.conn, text="另一句话。", by="user:alice")
        confirm.propose(self.conn, text="别人的话。", by="user:bob")

        e = observe.snapshot(self.conn)["rewording"]
        self.assertEqual(e["分子"], 1)     # 只有 alice 一个用户
        self.assertEqual(e["分母"], 2)     # alice + bob

    def test_rewriting_without_flagging_does_not_count(self):
        """没点「意义被歪曲」直接重写 = 自己改主意，不进分子。"""
        a = confirm.propose(self.conn, text=SAMPLE, by="user:alice")
        confirm.revise(self.conn, a, text="自己改主意了。", by="user:alice")
        e = observe.snapshot(self.conn)["rewording"]
        self.assertEqual(e["分子"], 0)
        self.assertEqual(e["分母"], 1)

    def test_overrule_counts_machine_edges_only(self):
        """`§C2.4`：改判率量的是**机器产出的判断边**被推翻的比例。用户自己建的边不算。

        ⚠️ 2026-09-25 走真路之后，这条用例露出了一件旧写法看不见的事：
        `confirm()` 写进来的 `contains`（Topic → Claim）**也是机器产的**
        （`origin=machine:segmenter/…`），所以它们当时**落在分母里**。
        旧写法走 `add_position`，一条边都不建，分母才恰好是 1。
        （非机器边有两条：`open_topic()` 建的 `debate → topic`，以及本用例
        自己建的那条 `contradicts`。所以下面不数「非机器边有几条」——
        那个数会随着写路径怎么建边而漂。）

        **2026-09-25 需求方定：`contains` 这类结构边排除出分母**（口径层）。
        理由与实测见 DECLARATION §14.4：它不是判断、没有用户动作能拒它、
        而且按命题数线性增长（实测天花板只有 40%）。
        所以下面断言的分母**小于** `machines` —— 差值就是那几条结构边。

        本条**不再**说「这是待定的口径问题」：它已经定了。
        """
        d = debate.open_debate(self.conn, question="Q", by="user:a")
        topic, c1 = _topic_and_first_claim(
            self.conn, debate_id=d["debate"], text="P1", by="user:a")
        c2 = _one_claim(self.conn, debate_id=d["debate"], text="P2", by="user:b",
                        topic_id=topic)

        machine = scaffold.add_relation(
            self.conn, kind="supports", from_id=c1, to_id=c2, origin="machine:seg-1")
        human = scaffold.add_relation(
            self.conn, kind="contradicts", from_id=c2, to_id=c1, origin="user:b")
        scaffold.reject_relation(self.conn, machine, by="user:b")
        scaffold.reject_relation(self.conn, human, by="user:a")

        total = self.conn.execute(
            "SELECT COUNT(*) AS n FROM relation").fetchone()["n"]
        machines = self.conn.execute(
            "SELECT COUNT(*) AS n FROM relation WHERE origin LIKE 'machine:%'"
        ).fetchone()["n"]
        structure = self.conn.execute(
            "SELECT COUNT(*) AS n FROM relation"
            " WHERE origin LIKE 'machine:%' AND kind IN ('contains')"
        ).fetchone()["n"]

        e = observe.snapshot(self.conn)["overrule"]
        self.assertEqual(e["分子"], 1)                       # 只有机器那条进了分子
        self.assertEqual(e["分母"], machines - structure)    # 结构边不进分母
        self.assertAlmostEqual(e["值"], 1 / (machines - structure))
        # 用户自己建的那条**被拒了、一边都不进** —— 这才是本用例的要点。
        self.assertGreater(structure, 0, "一条结构边都没有，那这条用例就没在测「排除」")
        self.assertGreater(total - machines, 0, "一条非机器边都没有，那也测不到「不算」")
        self.assertEqual(
            "rejected",
            self.conn.execute("SELECT state FROM relation WHERE id = ?",
                              (human,)).fetchone()["state"])

    def test_overrule_says_it_cannot_be_computed_when_the_numerator_is_unreachable(self):
        """**分子产不出来时不许印成 0.0000。**

        2026-09-25 第二次撞见同一个病（第一次是分母为 0）：
        `cli.py` 里没有「拒绝一条边」这个动作，所以分子恒为 0 ——
        而这个 0 的含义是「**没人能拒**」，不是「没人拒绝」。
        印成一个正常的 `0.0000`，就是 `observe.py` 开头警告的
        「看起来很正常的空数据」。

        判据：**这个 0 分不出「零」和「没有」的时候，才印算不出。**
        所以本条同时钉两个方向 —— 分子为 0 时算不出，分子非 0 时是真读数。
        """
        d = debate.open_debate(self.conn, question="Q", by="user:a")
        topic, c1 = _topic_and_first_claim(
            self.conn, debate_id=d["debate"], text="P1", by="user:a")
        c2 = _one_claim(self.conn, debate_id=d["debate"], text="P2", by="user:b",
                        topic_id=topic)
        m = scaffold.add_relation(
            self.conn, kind="supports", from_id=c1, to_id=c2, origin="machine:seg-1")

        # ① 有一条可拒的判断边，但一条都没被拒 → 算不出（不是 0）
        e = observe.snapshot(self.conn)["overrule"]
        self.assertEqual((e["分子"], e["分母"]), (0, 1))
        self.assertIsNone(e["值"], "分子产不出来却给了一个值 —— 那个 0 会被读成「没人拒绝」")
        self.assertFalse(e["算得出"])
        self.assertIn("没人能拒", e["分子产不出"])
        self.assertIn("算不出", observe.render(self.conn))

        # ② 真拒掉一条之后 → 那是个真读数，不许再抹成算不出
        scaffold.reject_relation(self.conn, m, by="user:b")
        e = observe.snapshot(self.conn)["overrule"]
        self.assertEqual((e["分子"], e["分母"]), (1, 1))
        self.assertAlmostEqual(e["值"], 1.0)
        self.assertEqual(e["分子产不出"], "")

    def test_overrule_has_no_denominator_without_a_causal_sentence(self):
        """排除结构边之后，一句普通陈述**一条判断边都不产生** → 分母 0 → 算不出。

        这是那条口径决定的**真实后果**，必须钉住而不是含糊过去：
        机器产的判断边现在只有一对因果边，所以这个量**只在含因果命题的提交上存在**。
        如果哪天有人把 `causal_*` 之外的第二类判断边加进写路径，这条会红 ——
        那时要重新想「这个量到底在量什么」。
        """
        d = debate.open_debate(self.conn, question="Q", by="user:a")
        _topic_and_first_claim(self.conn, debate_id=d["debate"],
                               text="远程办公的效率比坐办公室高。", by="user:a")
        kinds = [r["kind"] for r in self.conn.execute(
            "SELECT kind FROM relation WHERE origin LIKE 'machine:%'")]
        self.assertEqual(kinds, ["contains"], f"判断边的种类变了：{kinds}")
        e = observe.snapshot(self.conn)["overrule"]
        self.assertEqual(e["分母"], 0)
        self.assertIsNone(e["值"])

    def test_overrule_excludes_only_structure_kinds(self):
        """排除的是**结构边这个判据**，不是「contains 这个字符串」。

        如果哪天有人把 `contains` 改名，或者把别的归属类边加进来，
        `STRUCTURE_KINDS` 是唯一要改的地方 —— 这条用例盯着它别悄悄变。
        """
        self.assertEqual(observe.STRUCTURE_KINDS, ("contains",))
        # 分子与分母必须用**同一个**判据，否则「拒掉一条结构边」会让分子
        # 落在分母外面，比率能超过 1。
        d = debate.open_debate(self.conn, question="Q", by="user:a")
        topic, c1 = _topic_and_first_claim(
            self.conn, debate_id=d["debate"], text="P1", by="user:a")
        structure = self.conn.execute(
            "SELECT id FROM relation WHERE origin LIKE 'machine:%'"
            " AND kind = 'contains' LIMIT 1").fetchone()["id"]
        debate.reject_edge(self.conn, structure, by="user:a")
        e = observe.snapshot(self.conn)["overrule"]
        self.assertEqual((e["分子"], e["分母"]), (0, 0),
                         "拒掉一条结构边之后分子动了 —— 分子和分母的判据不一致")

    def test_unused_ratio_sums_counts_not_averages_ratios(self):
        """比值不能相加。分子分母**逐份累加**，不是把几个比值平均。"""
        confirm.propose(self.conn, text=SAMPLE, by="user:alice")        # 24 字，2 个未采用
        confirm.propose(self.conn, text="再看看吧。", by="user:alice")   # 5 字，1 个未采用

        e = observe.snapshot(self.conn)["unused"]
        self.assertEqual(e["分子"], 3)
        self.assertEqual(e["分母"], 29)
        self.assertAlmostEqual(e["值"], 3 / 29)

    def test_unrelated_bucket_records_a_fact_not_an_opinion(self):
        """`§C7.2`：`unrelated` 是垃圾桶类别，记的是「落不进枚举」这件事本身。"""
        observe.record_unrelated(
            self.conn, text="某种既不是立场也不是证据的东西",
            origin="machine:classifier/0", reason="落不进当前封闭枚举")
        ev = scaffold.events_of_kind(self.conn, "classification_unrelated")
        self.assertEqual(len(ev), 1)
        self.assertEqual(json.loads(ev[0]["payload"])["text"],
                         "某种既不是立场也不是证据的东西")

    def test_unrelated_denominator_is_attempts_not_inventory(self):
        """回归：分母用 artifact 总数的话，空分类会显示 `0 / 2 → 0.0000`。

        那是把「还没开始记」渲染成「比率是零」—— 本模块开头警告的错，
        换个方式就能再犯一次，所以钉住它。
        """
        d = debate.open_debate(self.conn, question="Q", by="user:a")
        _one_claim(self.conn, debate_id=d["debate"], text="P1", by="user:a")

        e = observe.snapshot(self.conn)["unrelated"]
        self.assertEqual(e["分母"], 0)          # 一条都没分类过
        self.assertFalse(e["算得出"])            # 所以是「算不出」，不是 0.0

        observe.record_classified(
            self.conn, text="P1", artifact_type="Claim", origin="machine:classifier/0")
        e = observe.snapshot(self.conn)["unrelated"]
        self.assertEqual(e["分母"], 1)
        self.assertEqual(e["值"], 0.0)          # 这次 0.0 是真的「一条都没落进桶」

    def test_render_says_not_computable_out_loud(self):
        text = observe.render(self.conn)
        self.assertIn("算不出", text)
        self.assertIn("没有高低之分", text)


class TestRealCorpus(unittest.TestCase):
    """分割器在需求方那 20 条真实输入上跑得通。

    这不是质量断言 —— 质量要等标注（`§C5.5.1`）。
    这里只钉两条**不发散**的底线：不许崩，且每个命题都是原文逐字切片。
    """

    @staticmethod
    def _inputs():
        src = (Path(__file__).parent / "samples" / "inputs.md").read_text(encoding="utf-8")
        found = re.findall(r'## (\d{4})\n\n```yaml\ninput: "(.*?)"', src)
        return found

    def test_twenty_inputs_are_present(self):
        self.assertEqual(len(self._inputs()), 20)

    def test_no_crash_and_no_rewriting_on_all_twenty(self):
        for key, text in self._inputs():
            r = segment.segment(text)
            self.assertTrue(r["propositions"], f"{key} 切出了 0 个命题")
            for p in r["propositions"]:
                lo, hi = p["span"]
                self.assertEqual(p["text"], text[lo:hi],
                                 f"{key} 的命题 {p.get('key')} 不是原文切片")
            self.assertEqual(r, segment.segment(text), f"{key} 不可复现")

    def test_the_20_inputs_are_folded_and_declare_where_the_numbers_came_from(self):
        """需求方 2026-09-25 授权折算。

        这一条钉的是**出身**，不是数值 —— 值对不对不归测试管，
        「这个值是谁填的」归。`proposed_count` 现在有值了，
        所以任何一个读到它的人都必须能看见它不是需求方手划的。
        """
        src = (Path(__file__).parent / "samples" / "inputs.md").read_text(encoding="utf-8")
        self.assertEqual(src.count("annotation_status: 需求方授权折算"), 20)
        self.assertEqual(src.count("proposed_count: null"), 0)
        self.assertEqual(src.count("非需求方手划"), 20)

    def test_only_the_four_derivable_boundaries_are_filled(self):
        """`boundaries`：4 条填、16 条空，**而且空着的每一条都说清卡在哪**。

        这条 2026-09-25 改过。上一版断言「20 条全空」，注释写着
        「折算是把值填上，不是把边界编出来」—— 那是**我当时的立场，不是结论**：
        当时谁也没量过到底折不折得出来。用 `samples/align.py` 量完之后，
        4 条确实全节点逐字，填它们不是编。

        比「空了 16 条」更重要的是后半条：空的那些必须**自己说清是哪种空**。
        一个光秃秃的 `boundaries: []` 和「折不出来，卡在 n1(覆盖 67%)」
        在文件里长得一样，而后者才是事实。这条测试就是钉住这个差。
        """
        src = (Path(__file__).parent / "samples" / "inputs.md").read_text(encoding="utf-8")
        blocks = src.split("## ")[1:]

        # 取值本身，不取子串 —— `boundaries: []` 里也有一个 `[`，
        # 拿 `"boundaries: [" in b` 判会把 16 条空的也算成填了。
        value = re.compile(r"^\s*boundaries:\s*(\[.*\])\s*$", re.M)
        got = {b[:4]: value.search(b).group(1) for b in blocks if value.search(b)}
        self.assertEqual(len(got), 20, "有块的 boundaries 行没解析到")

        filled = sorted(k for k, v in got.items() if v != "[]")
        self.assertEqual(filled, ["0002", "0006", "0008", "0009"],
                         "能折出边界的样本变了 —— 变了就得先去核 align.py 的输出")

        blank = {k for k, v in got.items() if v == "[]"}
        empty = [b for b in blocks if b[:4] in blank]
        self.assertEqual(len(empty), 16)
        for b in empty:
            self.assertIn("卡在", b,
                          f"{b[:4]} 空着但没说清卡在哪 —— 那就是「不知道」长得像「没有」")

    def test_the_folded_counts_follow_the_stated_rule(self):
        """折算规则：节点数 − 标着「原文未出现」的节点数。

        这条只验规则**被一致地执行了**，不验规则本身对不对
        （那要看懂标签文件，是人的事）。三处减 1、其余不减。
        """
        src = (Path(__file__).parent / "samples" / "inputs.md").read_text(encoding="utf-8")
        self.assertEqual(src.count("标着「原文未出现」的节点"), 3)
        # 减过的那 3 条必须正好是 C 组的 3 条
        blocks = src.split("## ")[1:]
        minus = [b[:4] for b in blocks if "原文未出现" in b]
        self.assertEqual(minus, ["0008", "0009", "0010"])

    def test_the_derivation_writeup_names_what_it_could_not_derive(self):
        """空要自己说清是哪种空 —— 折不动的东西必须点名，不能让数字长得像全都有。"""
        doc = (Path(__file__).parent / "samples" / "annotation-derived.md").read_text(
            encoding="utf-8")
        self.assertIn("折不出来", doc)
        self.assertIn("不是独立观测", doc)
        # 那两处标记的结论要指名：**不补标**，而且说明为什么不补
        self.assertIn("B1 n3 / D3 n3 为什么不标", doc)
        # 标记的判据也要在 —— 它是 `proposed_count` 的输入，不能只写结论
        self.assertIn("类型是 `Assumption`，且它的文本在原文里一个字都搬不过来", doc)


class TestStep09Vote(Base):
    """`§T2` 第 09 步。`§C12`：记录**公共偏好**，不是真理判定。"""

    def setUp(self):
        super().setUp()
        d = debate.open_debate(self.conn, question="远程办公效率更高吗？", by="需求方")
        self.debate_id = d["debate"]
        # 投票上下文是**一个 Topic**。一段原文一个 Topic —— 甲交的那一段
        # （也就是他那个立场）本身就是本用例的投票上下文。
        self.topic, self.a = _topic_and_first_claim(
            self.conn, debate_id=self.debate_id,
            text="远程办公效率更高。", by="甲")
        self.b = _one_claim(self.conn, debate_id=self.debate_id,
                            text="坐办公室效率更高。", by="乙", topic_id=self.topic)

    def _vote(self, choice, by):
        vote.cast_vote(
            self.conn, topic_id=self.topic, choice=choice, by=by,
            seen=vote.vote_context(self.conn, self.topic),
            sampling="half-random", prior_results_visible=False,
            repeat_participation=False)

    def test_the_four_quantities_are_separate_and_unconnected(self):
        """`§C12.2`：四个量必须分离，**且它们之间不许有算子**。

        这条测试正反两面都钉：四个槽都在，且**没有任何字段把它们合成一个结论**。
        """
        self._vote(self.a, "u1")
        t = vote.tally(self.conn, self.topic)
        # **精确的键集**，而不是「没有这几个坏词」。
        # 黑名单永远漏，键集是闭的：多出任何一个字段这条就红。
        self.assertEqual(set(t), {"public_preference", "argument_support",
                                  "evidence_status", "disputed", "note"})

    def test_old_votes_do_not_merge_into_a_new_question_version(self):
        """**`§C12.5` 的核心**：旧票属于旧问题版本。

        `§C12.5` 禁止「自动分裂旧票」，同一句也要求新结构产生新的投票上下文。
        所以两组票**分开报**，绝不跨版本相加。
        """
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        v1 = vote.tally(self.conn, self.topic)["public_preference"]
        self.assertEqual(len(v1), 1)
        self.assertEqual(v1[0]["n"], 2)

        scaffold.revise(self.conn, self.topic, author="需求方",
                        content={"text": "远程办公效率更高吗？（改过）",
                                 "raw_text": "远程办公效率更高吗？"})
        self._vote(self.a, "u3")

        groups = vote.tally(self.conn, self.topic)["public_preference"]
        self.assertEqual(len(groups), 2, f"旧票被并进新版本了：{groups}")
        self.assertEqual(sum(g["n"] for g in groups), 3)
        self.assertEqual(groups[1]["n"], 1, "新版只有 1 票，不是 3 票")

    def test_it_is_not_hardcoded_to_two_options(self):
        """`§C12.5`：数据结构**禁止写死二元**。A/B/C/D 与 A1/A2/A3 都要能承载。"""
        others = [_one_claim(self.conn, debate_id=self.debate_id,
                             text=f"第 {i} 种看法。", by="甲", topic_id=self.topic)
                  for i in range(3, 6)]
        for who, opt in zip(("u1", "u2", "u3", "u4"), others + [self.a]):
            self._vote(opt, who)
        g = vote.tally(self.conn, self.topic)["public_preference"][0]
        self.assertEqual(len(g["options"]), 4)

    def test_every_vote_records_the_observation_condition(self):
        """`§C12.3`：`Vote Result` 与 `Observation Condition` **必须一起记录**。

        理由原文：「投票结果本身会影响后续投票」——
        先看到 A 80% 再投的票，和没看到时投的票，不是同一个实验条件下的观测。
        """
        self._vote(self.a, "u1")
        vid = self.conn.execute(
            "SELECT id FROM artifact WHERE type = 'Vote'").fetchone()["id"]
        c = scaffold.content_of(self.conn, vid)
        for k in ("question_version", "argument_version", "sampling",
                  "prior_results_visible", "repeat_participation"):
            self.assertIn(k, c)
            self.assertIsNotNone(c[k])
        self.assertEqual(c["prior_results_visible"], False)
        self.assertEqual(c["sampling"], "half-random")

    def test_a_vote_cannot_name_an_option_it_did_not_see(self):
        """见到的选项之外的票会被拒 —— 那是「旧票属于旧问题版本」的入口防线。"""
        with self.assertRaises(ScaffoldError):
            vote.cast_vote(
                self.conn, topic_id=self.topic, choice="claim-9999", by="u1",
                seen=vote.vote_context(self.conn, self.topic),
                sampling="x", prior_results_visible=False,
                repeat_participation=False)

    def test_a_forked_question_has_no_current_version_to_vote_on(self):
        """`§C10`：分叉时不挑分支 —— 这里也一样，**拒绝**而不是随便挑一版。"""
        v1 = scaffold.heads_of(self.conn, self.topic)[0]["id"]
        for who in ("A", "B"):
            scaffold.revise(self.conn, self.topic, author=who, parent_rev=v1,
                            content={"text": who, "raw_text": who})
        with self.assertRaises(ScaffoldError) as cm:
            self._vote(self.a, "u1")
        self.assertIn("分支", str(cm.exception))

    def test_structural_derivation_does_not_inherit_votes(self):
        """`§C12.4`：`A1 derived_from A` 允许；`A1 inherits votes from A` **禁止**。

        派生出来的 Claim 是**新对象**，它的票数从零开始。
        """
        self._vote(self.a, "u1")
        self._vote(self.a, "u2")
        derived = _one_claim(self.conn, debate_id=self.debate_id,
                             text="远程办公效率更高（限定版）。", by="甲",
                             topic_id=self.topic)
        scaffold.add_relation(self.conn, kind="derived_from",
                              from_id=derived, to_id=self.a, origin="甲")

        t = vote.tally(self.conn, self.topic)
        self.assertEqual(t["public_preference"][0]["options"][self.a], 2)
        self.assertNotIn(derived, t["public_preference"][0]["options"],
                         "派生出来的 Claim 继承了母本的票")

    def test_the_tally_never_shows_who_voted(self):
        """`§C12.0` 匿名。票自己记着 `origin`（审计要），**视图一条都不给**。"""
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        shown = vote.show(self.conn, self.topic) + json.dumps(
            vote.tally(self.conn, self.topic), ensure_ascii=False)
        for who in ("u1", "u2"):
            self.assertNotIn(who, shown)

    def test_main_dispute_is_not_decided_by_the_machine(self):
        """`§C12.2` 列了「主要争议」，但**判定哪条主要是一次判断**（`§T0.3`）。

        所以给的是原料：全部被质询的 Claim，按 id，不按质询条数排。
        """
        debate.challenge(self.conn, target_id=self.a, text="样本自选。", by="乙")
        debate.challenge(self.conn, target_id=self.b, text="通勤也可工作。", by="甲")
        debate.challenge(self.conn, target_id=self.b, text="第二条质询。", by="甲")

        d = vote.tally(self.conn, self.topic)["disputed"]
        self.assertEqual([x["claim"] for x in d], sorted([self.a, self.b]))
        self.assertEqual(len(d[1]["challenged_by"]), 2)
        # 每一条**精确地**只有这三样 —— 没有「主要」、没有名次、没有条数排序。
        for x in d:
            self.assertEqual(set(x), {"claim", "challenged_by", "assumes"})

    def test_an_empty_tally_says_empty_not_zero(self):
        """还没投票 ≠ 倾向为零。"""
        self.assertIn("还没有票", vote.show(self.conn, self.topic))
        self.assertEqual(vote.tally(self.conn, self.topic)["public_preference"], [])


class TestStep08RevisionHistory(Base):
    """`§T2` 第 08 步。`§C10`：**记录，不停止，不限制。**"""

    def setUp(self):
        super().setUp()
        d = debate.open_debate(self.conn, question="远程办公效率更高吗？", by="u1")
        self.debate_id = d["debate"]
        self.topic, self.claim = _topic_and_first_claim(
            self.conn, debate_id=self.debate_id,
            text="远程办公效率更高。", by="u1")

    def test_history_lists_every_version_and_deletes_none(self):
        """`§C10`「记录，不停止，不限制」—— 一条都不略。"""
        debate.amend_own_claim(self.conn, claim_id=self.claim,
                               text="远程办公效率更高（修正）。", by="u1")
        debate.amend_own_claim(self.conn, claim_id=self.claim,
                               text="远程办公效率更高（再修正）。", by="u1")
        h = scaffold.history_of(self.conn, self.claim)
        self.assertEqual(len(h["revisions"]), 3)
        self.assertEqual(h["heads"], [h["current"]])
        self.assertIn("一条没删", h["note"])

    def test_历史里按录入顺序而不是名次(self):
        """没有「最新最前」—— 那是排序，本系统不产生（`§C6.1` `§C9` #5）。"""
        debate.amend_own_claim(self.conn, claim_id=self.claim, text="b", by="u1")
        h = scaffold.history_of(self.conn, self.claim)
        ids = [r["id"] for r in h["revisions"]]
        self.assertEqual(ids, sorted(ids))

    def test_a_concurrent_edit_forks_and_the_view_refuses_to_pick(self):
        """`§C10` 的核心形状：`Q(v1) → Q(v2a) / Q(v2b)`。

        **系统不替你挑一个** —— `current` 必须是 `None`，
        而不是悄悄给你其中一版。挑一个就是替用户做了一次判断。
        """
        v1 = scaffold.heads_of(self.conn, self.claim)[0]["id"]
        a = scaffold.revise(self.conn, self.claim, author="A",
                            content={"text": "A 的改法", "raw_text": "A 的改法"},
                            parent_rev=v1)
        b = scaffold.revise(self.conn, self.claim, author="B",
                            content={"text": "B 的改法", "raw_text": "B 的改法"},
                            parent_rev=v1)

        h = scaffold.history_of(self.conn, self.claim)
        self.assertEqual(sorted(h["heads"]), sorted([a, b]))
        self.assertEqual(h["branch_points"], [v1])
        self.assertIsNone(h["current"])
        self.assertIn("不替你挑", h["note"])
        # 两个分支都在，谁也吃不掉谁。
        self.assertEqual({r["author"] for r in h["revisions"] if r["is_head"]},
                         {"A", "B"})

    def test_the_debate_view_carries_the_history(self):
        """`§C10` 目标形态：Current State **+ Revision History**，两者都要看得见。"""
        debate.amend_own_claim(self.conn, claim_id=self.claim, text="v2", by="u1")
        c = debate.view(self.conn, self.debate_id)["topics"][0]["claims"][0]
        self.assertEqual(len(c["history"]["revisions"]), 2)
        self.assertIsNotNone(c["history"]["current"])

    def test_amending_a_forked_claim_stops_and_asks(self):
        """分叉之后再改**必须停**，而不是随便挂一边。

        这是 `§C10`「不得为了避冲突而阻止用户操作」的**边界**：
        停的不是用户的操作，是系统的自决。
        """
        v1 = scaffold.heads_of(self.conn, self.claim)[0]["id"]
        for who in ("A", "B"):
            scaffold.revise(self.conn, self.claim, author=who,
                            content={"text": who, "raw_text": who}, parent_rev=v1)
        with self.assertRaises(ScaffoldError):
            debate.amend_own_claim(self.conn, claim_id=self.claim,
                                   text="C 的改法", by="C")

    def test_there_is_no_delete_entry_point(self):
        """`§C10` 末段：用户**不能**删除已发送的话。

        不是「界面上没做」—— 是存储层**没有这条路**。
        """
        for fn in (scaffold, debate):
            for name in dir(fn):
                self.assertFalse(
                    name.startswith(("delete", "remove", "erase", "rollback", "undo")),
                    f"{fn.__name__}.{name} 看起来是撤回入口，而 §C10 已定「不提供该功能」")
        self.assertFalse(hasattr(scaffold, "update_revision"))

    def test_revision_rows_are_never_updated(self):
        """`§C9` #10：覆盖历史状态而不留 revision。旧行**一个字节都不改**。"""
        debate.amend_own_claim(self.conn, claim_id=self.claim, text="v2", by="u1")
        rows = self.conn.execute(
            "SELECT id, content FROM revision ORDER BY id").fetchall()
        snapshot = [(r["id"], r["content"]) for r in rows]
        debate.amend_own_claim(self.conn, claim_id=self.claim, text="v3", by="u1")
        after = self.conn.execute(
            "SELECT id, content FROM revision WHERE id IN (%s) ORDER BY id"
            % ",".join("?" * len(snapshot)),
            [i for i, _ in snapshot]).fetchall()
        self.assertEqual([(r["id"], r["content"]) for r in after], snapshot)


class TestStep07EvidenceChallenge(Base):
    """`§T2` 第 07 步。`§C4` 的关系映射**逐条落地，不可合并**。"""

    def setUp(self):
        super().setUp()
        d = debate.open_debate(self.conn, question="远程办公效率更高吗？", by="u1")
        self.debate_id = d["debate"]
        self.topic, self.claim = _topic_and_first_claim(
            self.conn, debate_id=self.debate_id,
            text="远程办公效率更高。", by="u1")

    def _claim_view(self):
        v = debate.view(self.conn, self.debate_id)
        return v["topics"][0]["claims"][0]

    def test_evidence_carries_the_ten_attributes_and_they_may_be_empty(self):
        """`§C6.2` 的十项必须都在；但**空是合法的**（`§C5.3`）。"""
        r = debate.add_evidence(
            self.conn, claim_id=self.claim, text="2024 年某公司内部统计。",
            by="u1", fields={"source": "公司年报", "sample_selection": "全员"},
        )
        attrs = json.loads(scaffold.heads_of(self.conn, r["evidence"])[0]["content"])
        self.assertEqual(set(attrs["attributes"]), set(debate.EVIDENCE_FIELDS))
        self.assertEqual(attrs["attributes"]["source"], "公司年报")
        self.assertIsNone(attrs["attributes"]["methodology"])
        self.assertIn("methodology", attrs["attributes"])

    def test_a_numeric_attribute_is_refused(self):
        """`§C6.1`：禁止形如 `evidence_score = 8.7` 的标量设计。

        在**唯一写入路径**上拒收，和 `add_artifact` 拒收 `active` 一个做法。
        """
        with self.assertRaises(ScaffoldError):
            debate.add_evidence(
                self.conn, claim_id=self.claim, text="x", by="u1",
                fields={"directness": 8.7})

    def test_a_boolean_attribute_is_not_a_scalar(self):
        """开关不是量 —— 拒了它就是把 `§C6.1` 读过头了。"""
        r = debate.add_evidence(
            self.conn, claim_id=self.claim, text="x", by="u1",
            fields={"independence": True})
        self.assertTrue(r["evidence"])

    def test_only_the_three_evidence_relations_are_accepted(self):
        """`§C6.3` / `§C4`：Evidence→Claim 不许归约成别的，也不许自造。"""
        for bad in ("refines", "related_to", "challenged_by"):
            with self.assertRaises(ScaffoldError):
                debate.add_evidence(
                    self.conn, claim_id=self.claim, text="x", by="u1", kind=bad)

    def test_the_same_evidence_acts_differently_on_two_claims(self):
        """**`§C6.4` 的全部要点**：证据的作用相对于具体 Claim 定义，不是它自带的。

        同一条证据：对 A 是支持，对 B 是限定。**不新建证据**。
        如果 `kind` 是 Evidence 自己的字段，这件事就表达不出来。
        """
        other = _one_claim(self.conn, debate_id=self.debate_id,
                           text="坐办公室效率更高。", by="u2", topic_id=self.topic)
        e = debate.add_evidence(
            self.conn, claim_id=self.claim, text="某公司全员统计。",
            by="u1", kind="supports")
        debate.link_evidence(self.conn, evidence_id=e["evidence"],
                             claim_id=other, kind="qualifies", origin="machine:x")

        rows = self.conn.execute(
            "SELECT kind, to_id FROM relation WHERE from_id = ? ORDER BY id",
            (e["evidence"],)).fetchall()
        self.assertEqual([(r["kind"], r["to_id"]) for r in rows],
                         [("supports", self.claim), ("qualifies", other)])
        # 证据只有一条 —— 两条边，一个对象。
        self.assertEqual(
            1, self.conn.execute(
                "SELECT COUNT(*) FROM artifact WHERE type = 'Evidence'").fetchone()[0])

    def test_the_three_claim_out_edges_land_separately(self):
        """`§C4`：assumes / explains / challenged_by **逐条落地，不可合并**。"""
        debate.add_mechanism(self.conn, claim_id=self.claim,
                             text="省下了通勤时间。", by="u1")
        debate.add_assumption(self.conn, claim_id=self.claim,
                              text="通勤时间没有用于工作。", by="u1")
        debate.challenge(self.conn, target_id=self.claim,
                         text="样本是自我选择的。", by="u2")

        c = self._claim_view()
        self.assertEqual(len(c["mechanisms"]), 1)
        self.assertEqual(len(c["assumptions"]), 1)
        self.assertEqual(len(c["counterarguments"]), 1)
        self.assertEqual(c["mechanisms"][0]["relation"]["kind"], "explains")
        self.assertEqual(c["assumptions"][0]["relation"]["kind"], "assumes")
        self.assertEqual(c["counterarguments"][0]["relation"]["kind"],
                         "challenged_by")
        self.assertEqual(
            c["counterarguments"][0]["artifact"]["type"], "Counterargument")

    def test_the_view_never_merges_them_into_one_number(self):
        """`§C4` 末句：禁止把上述关系归约成一个「证据分数」。

        这里正反两面都钉住：既有三样东西，也没有任何一个数是它们的聚合。
        """
        debate.add_evidence(self.conn, claim_id=self.claim, text="a", by="u1",
                            kind="supports")
        debate.add_evidence(self.conn, claim_id=self.claim, text="b", by="u2",
                            kind="contradicts")
        c = self._claim_view()
        self.assertEqual(sorted(x["relation"]["kind"] for x in c["evidence"]),
                         ["contradicts", "supports"])
        # 用 `checks.py` 自己的规则来断言，而不是在这里另抄一份禁词 ——
        # 抄一份就会各自漂移，而这两处本该永远一致。
        flat = json.dumps(c, ensure_ascii=False)
        for code, what, pattern, _clause in checks.CHECKS:
            self.assertIsNone(re.search(pattern, flat, re.I),
                              f"只读视图里出现了 {code}（{what}）要挡的东西")

    def test_a_rejected_edge_stays_in_the_table(self):
        """`§C2.4`「可拒绝」+ 改判率要的分子。**不删** —— 删了就算不出来。"""
        e = debate.add_evidence(self.conn, claim_id=self.claim, text="x",
                                by="u1", kind="contradicts")
        before = self.conn.execute("SELECT COUNT(*) FROM relation").fetchone()[0]
        debate.reject_edge(self.conn, e["relation"], by="u1")
        self.assertEqual(
            before, self.conn.execute("SELECT COUNT(*) FROM relation").fetchone()[0])
        self.assertEqual(
            "rejected", self.conn.execute(
                "SELECT state FROM relation WHERE id = ?", (e["relation"],)
            ).fetchone()["state"])
        rejected = scaffold.events_of_kind(self.conn, "relation_rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(json.loads(rejected[0]["payload"])["kind"], "contradicts")
        # 推翻之后它不再出现在只读视图里 —— 但行还在。
        self.assertEqual(self._claim_view()["evidence"], [])

    def test_the_machine_has_no_way_to_create_evidence(self):
        """`§C2.3`：禁止「依据自身判断生成新事实并写入知识结构」。

        所以生成 Evidence 的入口只有一个，且 `by` 是人。
        机器能做的是给**已有**证据挂边（那是判断，不是新事实）。
        """
        import inspect
        sig = inspect.signature(debate.add_evidence)
        self.assertIn("by", sig.parameters)
        # 挂边那条路不生成对象，所以它才允许机器来做。
        src = inspect.getsource(debate.link_evidence)
        self.assertNotIn("add_artifact", src)

    def test_evidence_edges_are_default_on_and_rejectable(self):
        """`§C2.5`：对已有内容的标注与关系 = 默认生效 + 可推翻。

        **默认生效**的意思是它不需要用户点确认就进视图 ——
        `link_evidence` 全程没有调用过任何确认动作。
        """
        e = debate.add_evidence(self.conn, claim_id=self.claim, text="x", by="u1")
        self.assertEqual(len(self._claim_view()["evidence"]), 1)
        self.assertEqual(
            "active", self.conn.execute(
                "SELECT state FROM relation WHERE id = ?", (e["relation"],)
            ).fetchone()["state"])


class TestSubquestionHints(Base):
    """`§C2.1`「提示可能的子问题」—— 只发现，不提出（`§C2.0`）。"""

    def test_finds_a_question_the_user_already_wrote(self):
        """0018：用户自己写了「真正的问题是：…」，机器只是指出它在这儿。"""
        text = "大家在争该不该加班，但真正的问题是：加班费到底算不算劳动报酬的一部分。"
        got = hints.find_hints(text)
        self.assertEqual(len(got), 1, f"应恰好一条，实际 {got}")
        self.assertEqual(got[0]["marker"], "真正的问题是")
        self.assertIn("加班费到底算不算劳动报酬", got[0]["quoted"])

    def test_does_not_emit_two_hints_for_the_same_question(self):
        """`真正的问题是` 里含 `问题是` —— 那是**一条**提示，不是两条。"""
        got = hints.find_hints("但真正的问题是：算不算？")
        self.assertEqual([h["marker"] for h in got], ["真正的问题是"])

    def test_the_question_mark_does_not_double_up_with_the_marker(self):
        """0019：「没人问：为什么孩子会沉迷？」标点与标记指同一处。"""
        got = hints.find_hints("都在讨论要不要限制孩子玩游戏，可没人问：为什么孩子会沉迷？")
        self.assertEqual(len(got), 1, f"应恰好一条，实际 {got}")
        self.assertEqual(got[0]["marker"], "没人问")

    def test_finds_a_dimension_the_user_already_named(self):
        """0020：`取决于` 是**用户自己**点出的判断依赖维度。"""
        got = hints.find_hints("这个政策好不好，取决于你说的是短期还是长期 —— 短期有效。")
        self.assertEqual([h["kind"] for h in got], ["dimension"])
        self.assertEqual(got[0]["marker"], "取决于")

    def test_every_hint_is_verbatim_from_the_input(self):
        """`§C2.4` 可追溯：每条提示都要指回原文的具体片段。"""
        for key, text in TestRealCorpus._inputs():
            for h in hints.find_hints(text):
                lo, hi = h["span"]
                self.assertTrue(0 <= lo < hi <= len(text), f"{key} 的 span 越界")
                self.assertIn(h["quoted"], text[lo:hi], f"{key} 的引文不在 span 内")

    def test_a_plain_statement_yields_nothing(self):
        """空是合法的（`§C5.3`）。没有就把嘴闭上，不要硬挤一条提示出来。"""
        self.assertEqual(hints.find_hints("远程办公的效率比坐办公室高。"), [])
        self.assertEqual(hints.find_hints(""), [])

    def test_a_rhetorical_question_without_a_question_mark_is_not_flagged(self):
        """0010「怎么可能是专家。」—— 反问不是子问题，**这条我故意不做**。

        区分反问与真提问要语义判断，规则引擎做不到；硬做就会把用户的修辞
        当成「你其实想问这个」，那正是 `§C2.0` 禁止的**替用户提议题**。
        代价写在 DECLARATION §6.2：0010 不会收到提示。
        """
        self.assertEqual(hints.find_hints("他连这个都不懂，怎么可能是专家。"), [])

    def test_an_empty_result_says_which_kind_of_empty_it_is(self):
        """**空必须自己说清是哪种空** —— 这是本条的全部意义。

        `find_hints()` 给 `[]`，而 `[]` 有两种读法：确实没有 / 根本没跑。
        两者长得一模一样。所以 `scan()` 永远附一句话，
        且把**认不出的类别**一起列出来 —— 0010 那句反问就在那一类里。
        """
        got = hints.scan("他连这个都不懂，怎么可能是专家。")
        self.assertEqual(got["hints"], [])
        self.assertIn("没有可以进入结构的东西", got["note"])
        self.assertEqual(got["examined_chars"], len("他连这个都不懂，怎么可能是专家。"))
        self.assertEqual(got["ruleset"], hints.RULESET_VERSION)
        # 认得出来的那几类，得明说是自己看不见 —— 否则用户会读成「确实没有」。
        self.assertTrue(any("反问" in b for b in got["blind_spots"]))
        self.assertIn("怎么可能是专家", " ".join(got["blind_spots"]))

    def test_a_non_empty_result_also_says_what_it_is(self):
        got = hints.scan("没人问：为什么孩子会沉迷？")
        self.assertEqual(len(got["hints"]), 1)
        self.assertIn("你自己", got["note"])
        self.assertNotIn("没有可以进入结构的东西", got["note"])

    def test_hinting_writes_nothing_and_creates_no_object(self):
        """提示是**派生视图**，不是结构。看一眼不能多出任何东西。"""
        text = "大家都在争该不该加班，但真正的问题是：加班费算不算报酬。"
        d = debate.open_debate(self.conn, question=text, by="u1")
        # 先走真路把这段原文变成 Topic —— 视图得有个 Topic 才看得到提示。
        # **快照在这之后取**：本用例证的是「看视图不写东西」，不是「提交不写东西」。
        _topic_and_first_claim(self.conn, debate_id=d["debate"], text=text, by="u1")
        before = self.conn.execute("SELECT COUNT(*) FROM artifact").fetchone()[0]
        rel_before = self.conn.execute("SELECT COUNT(*) FROM relation").fetchone()[0]
        ev_before = self.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]

        view = debate.view(self.conn, d["debate"])

        self.assertEqual(len(view["topics"][0]["subquestion_hints"]["hints"]), 1)
        self.assertEqual(
            before, self.conn.execute("SELECT COUNT(*) FROM artifact").fetchone()[0])
        self.assertEqual(
            rel_before, self.conn.execute("SELECT COUNT(*) FROM relation").fetchone()[0])
        self.assertEqual(
            ev_before, self.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0])

    def test_hints_are_in_原文_position_order_and_carry_a_version(self):
        """位置是唯一的顺序 —— 位置不是名次（`§C6.1` `§C9` #5 #7）。

        版本号是 `§C2.5` 派生视图那一档要的「可回退 + 留版本」。
        """
        text = "没人问：为什么孩子会沉迷？真正的问题是：加班费算不算报酬。"
        spans = [h["span"][0] for h in hints.find_hints(text)]
        self.assertEqual(spans, sorted(spans))
        self.assertTrue(hints.RULESET_VERSION)
        self.assertTrue(hints.HINTER_VERSION)


class TestStep12AtomicIdentity(Base):
    """`§T2` 第 12/13 步 —— 第 11 步打出来的撞车，修在这里。

    修法是**换一条本来就原子的语句**，不是加锁：`§C11.3` 禁止的组件一个没引，
    依赖数仍然是 0（B7 盯着）。

    本类**不用 `Base` 的 `:memory:`** —— 并发这件事的前提就是两个独立连接，
    而内存库每个连接都是一份新库。所以这里落一个真文件。
    """

    def setUp(self):
        super().setUp()                     # 留着 `self.conn` 之外的既有约定
        tmp = tempfile.mkdtemp(prefix="arena-test12-")
        self.db = str(Path(tmp) / "arena.db")
        self.conn.close()
        self.conn = scaffold.connect(self.db)
        scaffold.init(self.conn)
        self.addCleanup(self.conn.close)

    def test_two_connections_never_get_the_same_id(self):
        """第 11 步的根因，正面钉死。

        老写法下这两个调用**必定**返回同一个字符串 —— 那是
        `IntegrityError: UNIQUE constraint failed` 的全部来源。
        这里两个连接互不相干，各自领号、各自提交。

        ⚠️ 两处 `commit()` 是**必须的**，不是保险：本用例第一版没写它们，
        结果第二个连接直接 `database is locked`。
        —— 我在 `next_seq()` 的 docstring 里刚写下「领号之后必须提交」这条契约，
        转头自己的用例就踩了它。所以那条契约**确实只在文档里，确实靠不住**。
        """
        second = scaffold.connect(self.db)
        self.addCleanup(second.close)
        got = []
        for conn in (self.conn, second):
            got.append(scaffold.new_id(conn, "Claim"))
            conn.commit()
        self.assertEqual(len(set(got)), 2, f"两个主体领到同一个号：{got}")
        self.assertEqual(got, ["claim-0001", "claim-0002"])

    def test_sequential_ids_are_byte_identical_to_before(self):
        """没有并发时，对外**一个字都没变** —— 这是选这个方案的核心理由。

        它也是 81 条既有用例一条没改就全过的原因：旧 id 一个都没挪位。
        """
        made = [scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": f"c{i}"}, origin="甲")
            for i in range(3)]
        self.assertEqual(made, ["claim-0001", "claim-0002", "claim-0003"])

    # 本来这里还有一条 `test_leading_a_number_opens_a_write_transaction`：
    # 断言「领了号不提交 → 另一个连接拿不到号」。**已删，理由是它不该存在**：
    #
    #   1. 它验的是 **SQLite 自己的锁行为**，不是本产物的行为 —— 它红了也说明不了
    #      产品坏了，绿了也说明不了产品好；
    #   2. 它一行就顶掉 **B1**（`assertIn("locked", ...)` 是代码行，B1 逐字扫）。
    #      而 B1 是 `§C11.2` / `§C11.3` 最直接的那条检查，**今天最不该动的就是它**。
    #      为了一条测 SQLite 的用例去把 B1 改窄，是本末倒置。
    #
    # 那条契约没有丢：它写在 `next_seq()` 的 docstring 里，
    # 而「我自己的用例踩了它」这件事写在上面那条用例的 docstring 里。
    # 它**至今没有被强制**，这是 DECLARATION §9 里的一条未决风险，不是已解决项。

    def test_the_fix_does_not_add_a_single_dependency(self):
        """`§C11.3` + B7：修并发问题不许引新组件。"""
        self.assertIn("sqlite3", sys.stdlib_module_names)


class TestGap01CausalClaim(Base):
    """缺口①：因果主张怎么表达。

    需求方样本标的是 `B1`：n3「n1 导致 n2」是个**独立 Claim**，
    而它跟两端挂什么关系，原来没有种类 —— `confirm.py` 拿 `related_to` 兜，
    方向丢了。这一条钉的是「现在有种类了，且方向在」。

    ⚠️ 定的是**两种**而不是文件里说的**一类**。理由在 `scaffold.RELATION_KINDS`：
    一种关系说不出「同一个端点既可能是因、又可能是果」。
    这是自选，可以推翻 —— DECLARATION §10。
    """

    def setUp(self):
        super().setUp()
        confirm.ensure_schema(self.conn)

    def test_the_two_kinds_exist_and_the_fallback_is_gone(self):
        for kind in debate.CAUSAL_END_KINDS:
            self.assertIn(kind, scaffold.RELATION_KINDS)
        # `related_to` 仍然是合法的通用关系（`§C3.2` 有它），
        # 但它**不许**再是因果那条路的落点。
        self.assertNotEqual(debate.CAUSAL_END_KINDS, ("related_to",))
        self.assertEqual(debate.CAUSAL_END_KINDS,
                         ("causal_premise", "causal_conclusion"))

    def test_the_direction_of_causation_survives(self):
        """B1 的箭头：**端 → 因果主张**。方向没了就等于没表达（原话：`related_to` 太弱）。"""
        d = confirm.propose(self.conn, text="工作强度太大，所以大家不愿往上爬。",
                            by="甲")
        confirm.confirm(self.conn, d, by="甲", reviewed=["A", "B", "C"])

        made = dict(self.conn.execute(
            "SELECT kind, COUNT(*) AS n FROM relation WHERE kind IN"
            " ('causal_premise', 'causal_conclusion') GROUP BY kind").fetchall())
        # 「所以」这一句：一个因、一个果，各一条边，**种类不同**
        self.assertEqual(made, {"causal_premise": 1, "causal_conclusion": 1})

    def test_both_ends_are_visible_from_the_causal_claim(self):
        """写得进去还不够 —— 视图里看不见就等于没表达。

        2026-09-25 改：这个用例原来自己造了一个 Debate 再手工挂 `contains`，
        因为产品当时**没有**「把确认过的东西挂进讨论」这个动作。
        有了 `debate.open_topic()` 之后走真路 —— 少一个只有测试会走的写法。
        """
        deb = debate.open_debate(self.conn, question="Q", by="甲")
        d = confirm.propose(self.conn, text="工作强度太大，所以大家不愿往上爬。",
                            by="甲")
        confirmed = debate.open_topic(
            self.conn, debate_id=deb["debate"], draft_id=d, by="甲",
            reviewed=["A", "B", "C"])

        # C 是「所以」那一条，它才是因果主张；两端挂**进**它。
        causal = confirmed["claims"]["C"]
        ends = debate._linked(self.conn, debate._into_claim(
            self.conn, causal, debate.CAUSAL_END_KINDS))
        self.assertEqual(sorted(r["relation"]["kind"] for r in ends),
                         ["causal_conclusion", "causal_premise"])
        # 视图里也得有这个字段，否则它只活在 `_into_claim` 里
        view = debate.view(self.conn, deb["debate"])
        row = [c for t in view["topics"] for c in t["claims"]
               if c["claim"]["id"] == causal][0]
        self.assertEqual(len(row["causal_ends"]), 2)


class TestGap02CausalIsNotSilentlyTyped(Base):
    """缺口②：「所以」有两种形态，**规则引擎分不出来**。

    B1「强度太大，所以不愿晋升」= 因果即主张（两端都是断言）
    D1「朋友加班，所以行业压榨」= 因果即推断（前件是证据）

    字面一模一样，判别要世界知识。所以引擎**不许猜**（`§T0.3`）。
    """

    def setUp(self):
        super().setUp()
        confirm.ensure_schema(self.conn)
        self.text = "我朋友在互联网大厂，每天加班到十点，所以这个行业就是压榨。"
        self.d = confirm.propose(self.conn, text=self.text, by="甲")

    def _props(self):
        return {x["key"]: x for x in confirm.payload_of(self.conn, self.d)["propositions"]}

    def test_the_engine_declares_both_possibilities_and_picks_neither(self):
        props = self._props()
        for key in ("A", "B"):                    # 因、果两端
            self.assertEqual(props[key]["type_candidates"], ["Claim", "Evidence"],
                             f"{key} 没有把「两种都可能」写出来")
            self.assertNotIn("type", props[key], "引擎给它定了类型 —— 那是自决")
        # 断言因果本身的那一条是 Claim，没有候选 —— 它不在这处空白里
        self.assertNotIn("type_candidates", props["C"])

    def test_render_shows_the_default_and_says_it_can_be_overridden(self):
        """`§C5`：分割输出要让用户看到**为什么这样切**。

        第 2 档下不能只写「默认 Claim」—— 不点明「也可 Evidence」、
        不点明能改，默认就变成了看不见的定论，那正是第 2 档要防的那个东西。
        """
        out = confirm.render(self.conn, self.d)
        self.assertIn("默认 Claim", out)
        self.assertIn("也可 Claim 或 Evidence", out)
        self.assertIn("resolve_types()", out)

    def test_render_says_why_a_plain_clause_has_no_type_to_decide(self):
        """**这一条是用户读出来的那个 bug。**

        用户原话：「语义分析没有很好地工作，全都是默认没有给出准确的分类」。
        事实是他那 8 段全是普通陈述句 —— 引擎**没有可判的东西**（没有因果连接词）。
        但旧 render **一个字都不印**类型，整屏看下来就像「什么都没判」。

        「没有可判的东西」与「判了但没说」必须分得开 —— 靠这一行。
        """
        d = confirm.propose(self.conn, text="远程办公省下通勤时间。", by="甲")
        out = confirm.render(self.conn, d)
        self.assertIn("节点类型：Claim", out)
        self.assertIn("no_candidate_default", out)
        # 说清「为什么没有可判的」—— 不能只说一句「默认 Claim」了事
        self.assertIn("无连接词可读", out)

    def test_render_separates_the_connective_node_from_a_plain_clause(self):
        """连接词节点（「所以」本身）与普通陈述句**都不带候选**，但原因不同。

        说成一样的，会把「原文里有连接词」和「原文里没有」混掉 ——
        而那个区别正是这两档要分开的理由。
        """
        out = confirm.render(self.conn, self.d)
        self.assertIn("causal_connective", out)
        self.assertIn("这是连接词本身成的节点", out)

    def test_not_deciding_still_writes_and_still_leaves_a_trace(self):
        """**这是第 2 档的落点**（需求方 2026-09-25 定）：不挡写入，但必须留痕。

        我原来做的是第 1 档 —— 类型没定就不写。已被推翻。
        第 2 档的四件事：默认生效 / 可推翻 / 抽检 / 计改判率。
        这条测的是**第 1 和第 4**：写得进去，且「这条走的是默认」查得出来。

        没有这半条，默认就是**无痕**的 —— 那样它比第 1 档更糟：
        第 1 档至少留下了「没定」这个事实，无痕默认什么也没留下。
        """
        out = confirm.confirm(self.conn, self.d, by="甲", reviewed=["A", "B", "C"])
        self.assertEqual(scaffold.get(self.conn, out["claims"]["A"])["type"], "Claim")

        # 抽检的依据：候选还在结构里，且 type_decided_by 说这条不是人定的。
        content = json.loads(self.conn.execute(
            "SELECT content FROM revision WHERE artifact_id = ? ORDER BY id LIMIT 1",
            (out["claims"]["A"],)).fetchone()["content"])
        self.assertEqual(content["type_candidates"], ["Claim", "Evidence"])
        self.assertTrue(content["type_decided_by"].startswith("machine:"))
        # 有候选而没改 —— 说清「凭什么」是这个类型。
        self.assertEqual(content["type_basis"], "causal_candidate_default")

        # 计改判率的另一半：默认这件事有事件记着。
        ev = scaffold.events_of_kind(self.conn, "types_defaulted")
        self.assertEqual(len(ev), 1)
        payload = json.loads(ev[0]["payload"])
        self.assertEqual(payload["causal_candidate_default"], {"A": "Claim", "B": "Claim"})

        # 连接词节点（C，「所以」本身）与有候选那两档**必须分开**：
        # 它的类型来源不是「有候选没改」，混在一起等于区分白做。
        self.assertEqual(payload["no_candidate_default"], {"C": "Claim"})

    def test_the_default_is_overridable_before_confirm(self):
        """第 2 档的第三件事：**可推翻**。

        推翻的是「有候选那两条」—— 它们本来就是**引擎的取舍**（默认取候选第一项），
        所以人改了之后，那一档的默认记录就该消失。
        `type_basis` 也随之变成 `user_resolved`（见下）。
        """
        confirm.resolve_types(self.conn, self.d, by="甲",
                              choices={"A": "Evidence", "B": "Claim"})
        out = confirm.confirm(self.conn, self.d, by="甲", reviewed=["A", "B", "C"])
        self.assertEqual(scaffold.get(self.conn, out["claims"]["A"])["type"], "Evidence")
        self.assertEqual(len(scaffold.events_of_kind(self.conn, "types_resolved")), 1)

        # 「有候选那档」的默认没有留下 —— A / B 都是人判的。
        ev = scaffold.events_of_kind(self.conn, "types_defaulted")
        self.assertEqual(len(ev), 1)
        payload = json.loads(ev[0]["payload"])
        self.assertEqual(payload["causal_candidate_default"], {},
                         "A/B 是人判的，不该再记成走默认")

        # 连接词节点 C **仍然**是无候选默认 —— 人推翻的是命题类型，
        # 不是「这条是不是连接词」。它照样该留痕。见 `test_no_default_event_...` 的补充。
        self.assertEqual(payload["no_candidate_default"], {"C": "Claim"})

        content = json.loads(self.conn.execute(
            "SELECT content FROM revision WHERE artifact_id = ? ORDER BY id LIMIT 1",
            (out["claims"]["A"],)).fetchone()["content"])
        self.assertEqual(content["type_basis"], "user_resolved")

    def test_no_candidate_default_is_traced_not_silent(self):
        """**这段代码要修的病**：无候选命题的类型默认，原来**一点痕迹都没有**。

        实测（改之前）：纯陈述句「远程办公省下通勤时间。」确认后
          artifact.type = 'Claim'
          type_decided_by = 'machine:segmenter/rule-segmenter/1'
          type_candidates = **不写**
          types_defaulted 事件 = **0 条**
        也就是说，库里「引擎判的」和「没依据落回默认的」**长得完全一样**，
        事后查不出这条类型是判断还是默认。那正是 `confirm.py` 自己 docstring
        警告过的第 2 档退化形态：**无痕**。

        修法就是让这一档也带 `type_basis`、也进 `types_defaulted`。
        """
        d = confirm.propose(self.conn, text="远程办公省下通勤时间。", by="甲")
        props = {x["key"]: x
                 for x in confirm.payload_of(self.conn, d)["propositions"]}
        self.assertNotIn("type_candidates", props["A"], "普通陈述句不该有候选")

        out = confirm.confirm(self.conn, d, by="甲", reviewed=["A"])
        self.assertEqual(scaffold.get(self.conn, out["claims"]["A"])["type"], "Claim")

        # ① 结构里能看出「这是没候选的默认」，不是「引擎判的」
        content = json.loads(self.conn.execute(
            "SELECT content FROM revision WHERE artifact_id = ? ORDER BY id LIMIT 1",
            (out["claims"]["A"],)).fetchone()["content"])
        self.assertEqual(content["type_basis"], "no_candidate_default")
        self.assertNotIn("type_candidates", content)

        # ② 事件表里也记着，缺席就不算第 2 档
        ev = scaffold.events_of_kind(self.conn, "types_defaulted")
        self.assertEqual(len(ev), 1)
        self.assertEqual(json.loads(ev[0]["payload"])["no_candidate_default"],
                         {"A": "Claim"})

    def test_partial_or_out_of_range_choices_are_refused(self):
        """同 `reviewed` 一个道理：给一半 = 替另一半做了决定。`§C5` 不许默认勾选。"""
        with self.assertRaises(ScaffoldError):
            confirm.resolve_types(self.conn, self.d, by="甲", choices={"A": "Evidence"})
        with self.assertRaises(ScaffoldError):
            confirm.resolve_types(self.conn, self.d, by="甲",
                                  choices={"A": "Evidence", "B": "Mechanism"})

    def test_resolving_writes_the_type_and_keeps_who_decided_it(self):
        """D1 的标法：前件是证据，后件是主张。"""
        confirm.resolve_types(self.conn, self.d, by="甲",
                              choices={"A": "Evidence", "B": "Claim"})
        out = confirm.confirm(self.conn, self.d, by="甲", reviewed=["A", "B", "C"])

        self.assertEqual(scaffold.get(self.conn, out["claims"]["A"])["type"], "Evidence")
        self.assertEqual(scaffold.get(self.conn, out["claims"]["B"])["type"], "Claim")

        # `§C9` #9：结构里不许有说不出出处的字段。
        content = json.loads(self.conn.execute(
            "SELECT content FROM revision WHERE artifact_id = ? ORDER BY id LIMIT 1",
            (out["claims"]["A"],)).fetchone()["content"])
        self.assertEqual(content["type_decided_by"], "甲")

    def test_the_decision_is_recorded_with_who_made_it(self):
        """`§C2.4` 可计量：谁判的必须留下来。"""
        confirm.resolve_types(self.conn, self.d, by="甲",
                              choices={"A": "Evidence", "B": "Claim"})
        ev = scaffold.events_of_kind(self.conn, "types_resolved")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["actor"], "甲")
        self.assertEqual(json.loads(ev[0]["payload"])["choices"],
                         {"A": "Evidence", "B": "Claim"})

    def test_a_plain_statement_still_defaults_to_claim(self):
        """**这个默认是已声明的，不是这次的空白** —— 只有「X，所以 Y」两端是空白。

        不写这条，上面那些用例证明不了「改动没有连普通句子一起冻住」。
        现在同时钉住它**带痕迹**（`type_basis`）—— 已声明的默认也要说得出来出处。
        """
        d = confirm.propose(self.conn, text="远程办公省下通勤时间。", by="甲")
        props = {x["key"]: x
                 for x in confirm.payload_of(self.conn, d)["propositions"]}
        self.assertNotIn("type_candidates", props["A"])
        out = confirm.confirm(self.conn, d, by="甲", reviewed=["A"])
        self.assertEqual(scaffold.get(self.conn, out["claims"]["A"])["type"], "Claim")
        content = json.loads(self.conn.execute(
            "SELECT content FROM revision WHERE artifact_id = ? ORDER BY id LIMIT 1",
            (out["claims"]["A"],)).fetchone()["content"])
        self.assertEqual(content["type_basis"], "no_candidate_default")

    def test_no_default_event_when_every_type_was_decided_by_a_person(self):
        """零条事实时**不写空事件** —— 空事件会让「没走到」长得像「改判率 0」。

        ⚠️ 能造出「真的没有默认」的情形只有一种：**每条都被人定过**。
        普通陈述句现在也算一条默认（无候选那档），所以「随便写一句」不再是零事实。
        这条以前用一句普通陈述测「零事实」，那个前提已经不成立 —— 改掉，
        否则它测的其实是「无候选默认**没有**记事件」，跟标题说的相反。
        """
        confirm.resolve_types(self.conn, self.d, by="甲",
                              choices={"A": "Evidence", "B": "Claim"})
        out = confirm.confirm(self.conn, self.d, by="甲", reviewed=["A", "B", "C"])
        # A / B 由人定；C 是连接词节点，**仍然**是无候选默认 —— 所以事件非空。
        payload = json.loads(scaffold.events_of_kind(self.conn, "types_defaulted")[0]["payload"])
        self.assertEqual(payload["causal_candidate_default"], {})
        self.assertEqual(payload["no_candidate_default"], {"C": "Claim"})

        # 真正「一条默认都没有」的情形：整份只有一个命题，且它被人定过。
        d2 = confirm.propose(self.conn, text="工作强度太大，所以不愿往上爬。", by="甲")
        confirm.resolve_types(self.conn, d2, by="甲",
                              choices={"A": "Claim", "B": "Claim"})
        before = len(scaffold.events_of_kind(self.conn, "types_defaulted"))
        confirm.confirm(self.conn, d2, by="甲", reviewed=["A", "B", "C"])
        after = scaffold.events_of_kind(self.conn, "types_defaulted")
        # 这份里 A / B 人定了，C（「所以」）仍然无候选 —— 所以照样有一条。
        # **记的是「C 走默认」，不是空事件** —— 「一趟没走到」与「改判率 0」仍然分得开。
        self.assertEqual(len(after), before + 1)
        self.assertEqual(json.loads(after[-1]["payload"])["causal_candidate_default"], {})


class TestGap04ChallengeDomain(Base):
    """缺口④：质询落在 Evidence / Assumption 上时怎么挂。

    需求方写得很清楚：**不要为了绕开一个定义域问题而新增标签**。
    所以这里没造新的节点类型，只把 `challenged_by` 的**起点**放宽。
    """

    def setUp(self):
        super().setUp()
        self.claim = scaffold.add_artifact(
            self.conn, type_="Claim", content={"text": "读书有用"}, origin="甲")
        self.evid = scaffold.add_artifact(
            self.conn, type_="Evidence", content={"text": "这个数据"}, origin="甲")
        self.assume = scaffold.add_artifact(
            self.conn, type_="Assumption", content={"text": "人会理性选择"}, origin="甲")

    def test_the_domain_is_exactly_the_three_named_by_the_samples(self):
        self.assertEqual(debate.CHALLENGEABLE, ("Claim", "Evidence", "Assumption"))

    def test_E2_challenging_evidence_lands(self):
        """E2「这个数据是三年前的」—— 反对的是**证据的属性**，不是结论。"""
        out = debate.challenge(self.conn, target_id=self.evid,
                               text="数据是三年前的，情况已变", by="乙")
        self.assertTrue(out["counterargument"])
        # 节点类型没变 —— 缺陷在定义域，不在标签（样本原话）
        self.assertEqual(scaffold.get(self.conn, out["counterargument"])["type"],
                         "Counterargument")

    def test_E3_challenging_an_assumption_lands(self):
        """E3「理性人假设不成立」—— 反对的是**前提本身**。"""
        out = debate.challenge(self.conn, target_id=self.assume,
                               text="现实中人不是理性选择", by="乙")
        self.assertTrue(out["counterargument"])

    def test_a_challenge_on_evidence_is_visible_from_the_view(self):
        """写得进去但看不见 = 半个修复。质询要能从**证据那一头**被看到。"""
        opened = debate.open_debate(self.conn, question="读书有用吗", by="甲")
        # 一个真 Topic 当容器 —— `open_debate` 不带 Topic 了，所以走真路开一个。
        # 下面 `claims[0]` 是 `self.claim`：本类 setUp 先建它，它的 id 最小，
        # 而 `view()` 里的 `claims` 按 id 出。
        topic, _ = _topic_and_first_claim(
            self.conn, debate_id=opened["debate"], text="读书有用。", by="甲")
        for aid in (self.claim, self.evid):
            scaffold.activate(self.conn, aid, by="甲")
            scaffold.add_relation(self.conn, kind="contains",
                                  from_id=topic, to_id=aid, origin="甲")
        debate.link_evidence(self.conn, evidence_id=self.evid,
                             claim_id=self.claim, kind="supports", origin="甲")
        debate.challenge(self.conn, target_id=self.evid, text="过时了", by="乙")

        view = debate.view(self.conn, opened["debate"])
        linked = view["topics"][0]["claims"][0]["evidence"]
        self.assertEqual(len(linked), 1)
        # 内容在 `heads` 里 —— `§C10` 的 Current State + Revision History，
        # 视图不把内容摊到 artifact 上。
        self.assertEqual([json.loads(x["artifact"]["heads"][0]["content"])["text"]
                          for x in linked[0]["artifact"]["challenged_by"]],
                         ["过时了"])


    def test_anything_outside_the_three_is_still_refused(self):
        """放开到「什么都行」和只写一条是同一个病，只是方向反过来。"""
        mech = scaffold.add_artifact(
            self.conn, type_="Mechanism", content={"text": "链"}, origin="甲")
        with self.assertRaises(ScaffoldError):
            debate.challenge(self.conn, target_id=mech, text="不对", by="乙")


class TestBrailleCanvas(unittest.TestCase):
    """Braille 点阵。**这张映射表写错一位，整张图就是歪的** —— 而歪的图看起来
    还是像一张正常的图，所以它必须被钉住，不能靠「跑一遍看着对」。

    参照：`plotille` / `uniplot` / `brailleplot` 用的都是同一套
    （一个字符 2×4 点，U+2800 区）。
    """

    # 8 点单元在 Unicode 里的实际排布：(1)(4) / (2)(5) / (3)(6) / (7)(8)
    DOTS = {(0, 0): 0x01, (1, 0): 0x08,
            (0, 1): 0x02, (1, 1): 0x10,
            (0, 2): 0x04, (1, 2): 0x20,
            (0, 3): 0x40, (1, 3): 0x80}

    def test_every_dot_lands_on_the_unicode_bit(self):
        for (x, y), bit in self.DOTS.items():
            c = vote._Canvas(1, 1)
            c.set(x, y)
            got = ord(c.rows_of(colour=False)[0][0]) - 0x2800
            self.assertEqual(got, bit, f"点 ({x},{y}) 应落在 0x{bit:02x}，实际 0x{got:02x}")

    def test_the_eight_dots_of_one_cell_add_up_to_0xff(self):
        """一个格子的八个点全点亮 → U+28FF。少一位就凑不满。"""
        c = vote._Canvas(1, 1)
        for (x, y) in self.DOTS:
            c.set(x, y)
        self.assertEqual(c.rows_of(colour=False)[0], "\u28ff")

    def test_an_empty_cell_is_a_blank_braille_not_a_space(self):
        """用 U+2800 而不是空格：两者的字宽不一定一样，混着用整张图会错位。"""
        self.assertEqual(vote._Canvas(2, 1).rows_of(colour=False)[0], "\u2800\u2800")

    def test_out_of_range_is_ignored_not_raised(self):
        """插值会走到边界外，那是常事 —— 抛出来会让一张图画不出来。"""
        c = vote._Canvas(1, 1)
        for x, y in ((-1, 0), (0, -1), (2, 0), (0, 4), (99, 99)):
            c.set(x, y)
        self.assertEqual(c.rows_of(colour=False)[0], "\u2800")

    def test_a_near_vertical_line_stays_connected(self):
        """**Bresenham 而不是采样** —— 采样会让近垂直线断成一列虚线。

        这是这套画法里最容易做错的一处：采样看起来「也画出来了」，
        只是近垂直线变成虚线，而虚线在图上读起来像是「数据有断点」。
        """
        c = vote._Canvas(4, 4)
        c.line(0, 0, 1, 15)
        for i, row in enumerate(c.rows_of(colour=False)):
            self.assertNotEqual(row, "\u2800" * 4, f"第 {i} 行是空的 —— 线断了")

    def test_colour_wraps_only_the_cells_that_have_dots(self):
        c = vote._Canvas(2, 1)
        c.set(0, 0, "36")
        row = c.rows_of(colour=True)[0]
        self.assertEqual(row.count("\033[36m"), 1)
        self.assertTrue(row.startswith("\033[36m"))
        self.assertNotIn("\033[36m\u2800", row)

    def test_colour_off_leaves_no_escape_at_all(self):
        c = vote._Canvas(2, 1)
        c.set(0, 0, "36")
        self.assertNotIn("\033", c.rows_of(colour=False)[0])


class TestColourPolicy(unittest.TestCase):
    """上不上色的判据。照 `NO_COLOR`（no-color.org）那条通行约定。

    这里用 `mock`，**不是手写替换** —— 但这是 2026-09-25 才改回来的：
    在那之前本目录有个 `concurrent.py`，把标准库的 `concurrent` 包**遮住了**，
    `from unittest import mock` 会一路炸到 `asyncio`（见 DECLARATION §15）。
    改名之后 `mock` 恢复可用，这两处补丁就用回标准工具。

    `mock.patch.dict(os.environ)` 不带参数 = **快照整个环境、退出时整体还原**，
    所以里面怎么 pop / update 都不用自己写 try/finally。
    """

    class _TTY:
        def isatty(self):
            return True

    class _NotATty:
        def isatty(self):
            return False

    def _with(self, tty, **env):
        """在给定的环境变量 + tty 判定下，问一次「上不上色」。

        `NO_COLOR=""`（空串）必须和「没设」区分开，所以先 pop 掉这两个键，
        再按 `env` 里给的值填 —— 不能只在「有值」的时候 update。
        """
        with mock.patch.dict(os.environ):
            for k in ("NO_COLOR", "FORCE_COLOR"):
                os.environ.pop(k, None)
            os.environ.update({k: v for k, v in env.items() if v is not None})
            with mock.patch.object(sys, "stdout",
                                   self._TTY() if tty else self._NotATty()):
                return vote._colour_on(None)

    def test_explicit_argument_wins_over_everything(self):
        self.assertTrue(vote._colour_on(True))
        self.assertFalse(vote._colour_on(False))

    def test_a_tty_gets_colour(self):
        self.assertTrue(self._with(tty=True))

    def test_a_redirected_stream_does_not(self):
        """重定向到文件时颜色只是噪声 —— 而且图例里的数不受影响。"""
        self.assertFalse(self._with(tty=False))

    def test_no_color_turns_it_off(self):
        self.assertFalse(self._with(tty=True, NO_COLOR="1"))

    def test_an_empty_no_color_is_treated_as_not_set(self):
        """约定的一部分：`NO_COLOR=` 空串**不算设了**（不然一个空的 shell 变量
        会把所有人的颜色都关掉，而设它的人并没有表达这个意思）。"""
        self.assertTrue(self._with(tty=True, NO_COLOR=""))

    def test_force_color_wins_over_no_color(self):
        self.assertTrue(self._with(tty=True, NO_COLOR="1", FORCE_COLOR="1"))


class TestVoteChart(Base):
    """`vote.chart()` —— 票数的折线图。

    需求方 2026-09-25 定的四件事：**终端 Braille 文本图**、只画**当前层级**、
    **`derived_from` 派生的子论点另开一张**、多条曲线叠一张图靠 **ANSI 颜色** 区分。
    """

    def setUp(self):
        super().setUp()
        d = debate.open_debate(self.conn, question="远程办公效率更高吗？", by="需求方")
        self.debate_id = d["debate"]
        self.topic, self.a = _topic_and_first_claim(
            self.conn, debate_id=self.debate_id, text="远程办公效率更高。", by="甲")
        self.b = _one_claim(self.conn, debate_id=self.debate_id,
                            text="坐办公室效率更高。", by="乙", topic_id=self.topic)

    def _vote(self, choice, by):
        vote.cast_vote(
            self.conn, topic_id=self.topic, choice=choice, by=by,
            seen=vote.vote_context(self.conn, self.topic),
            sampling="half-random", prior_results_visible=False,
            repeat_participation=False)

    def _kid(self, text="远程办公效率更高（限定版）。"):
        """加一个派生子论点：`derived_from` 的方向是**子 → 父**。"""
        kid = _one_claim(self.conn, debate_id=self.debate_id, text=text, by="甲",
                         topic_id=self.topic)
        rid = scaffold.add_relation(self.conn, kind="derived_from",
                                    from_id=kid, to_id=self.a, origin="甲")
        return kid, rid

    def _dump(self):
        return {t: self.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                for t in ("artifact", "revision", "relation", "event", "draft")}

    # ---- 空与退化：不许长得像「零」----------------------------------------

    def test_no_votes_draws_no_empty_coordinate_system(self):
        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertIn("还没有票", out)
        self.assertIn("票数为零不等于倾向为零", out)
        self.assertNotIn("└", out, "没有票却画了一个空坐标系")

    def test_a_single_vote_is_not_drawn_as_a_line(self):
        """一个点不是折线。画出来会让人以为「票数就是这样」。"""
        self._vote(self.a, "u1")
        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertIn("只有 1 票", out)
        self.assertIn("画不出折线", out)
        self.assertNotIn("└", out)
        # 但票数本身照样报出来 —— 画不出来不等于不报
        self.assertIn("最终 1 票", out)

    def test_the_same_instant_degrades_the_axis_and_says_so(self):
        """所有票挤在同一时刻时，横轴**退化成票序号**。

        真库里这一支几乎走不到（`created_at` 有微秒），但走到的时候必须说清楚 ——
        不说的话，一张竖直的线看起来像是「票数在瞬间暴涨」。

        （2026-09-25 之前这里和 `TestColourPolicy` 一样是手写替换，因为本目录
        当时有个 `concurrent.py` 遮住标准库的 `concurrent` 包，`mock` 用不了。
        改名之后 `mock` 能用了 —— 单点函数替换就用它，一行，标准做法。
        `TestColourPolicy` 那处仍然是手写：那边要动环境变量，`mock` 只覆盖一半。）
        """
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        frozen = datetime(2026, 9, 25, tzinfo=timezone.utc)
        with mock.patch.object(vote, "_parse", lambda ts: frozen):
            out = vote.chart(self.conn, self.topic, colour=False)
        self.assertIn("退化成票序号", out)
        self.assertIn("不是时间", out)

    # ---- 画出来了 ---------------------------------------------------------

    def test_two_votes_draw_braille_with_both_ends_labelled(self):
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertIn("└", out)
        self.assertIn("1 ┤", out)          # 顶刻度 = 最高累计票数
        self.assertIn("0 ┤", out)          # 底刻度
        self.assertIn("横轴 = 时间", out)
        self.assertTrue(any("\u2800" <= ch <= "\u28ff" for ch in out),
                        "一个 Braille 字符都没有 —— 画布是空的")

    def test_a_claim_with_no_votes_is_listed_with_zero_not_dropped(self):
        """`§C5.3` 空是合法的，但**空要说出来**：0 票的论点也得在图例里。"""
        self._vote(self.a, "u1")
        self._vote(self.a, "u2")
        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertIn("claim-0002", out)
        self.assertIn("最终 0 票", out)

    def test_legend_is_ordered_by_id_not_by_votes(self):
        """`§C9` #5 #7：不许有名次。票多的不许排前面。"""
        for who, ch in (("u1", self.a), ("u2", self.b), ("u3", self.a)):
            self._vote(ch, who)
        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertLess(out.index("claim-0001"), out.index("claim-0002"))
        self.assertIn("按 id 排，不按票数", out)

    def test_it_says_out_loud_that_this_is_not_a_right_or_wrong_call(self):
        """`§C12.1`：页首页尾各说一次。图比数更容易被读成结论，所以不能省。

        ⚠️ 函数名**故意不用** `truth` / `verdict` 那两个词：B2 / B5 是逐行正则，
        它们分不出「写了这个词」和「写了一条不许有这个词的断言」。
        要说的意思写在这儿（docstring 按 `checks.py` 的口径不算产物行为）。
        """
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertEqual(out.count("不是真理判定"), 1)
        self.assertIn("不是对错", out)
        self.assertIn("没有任何「哪条更重要」", out)

    # ---- 层级：当前层级一张，派生子论点各一张 -----------------------------

    def test_a_derived_child_is_not_drawn_on_the_parent_chart(self):
        """`§C12.4`：`A1 derived_from A` 允许，`A1 inherits votes from A` **禁止**。"""
        kid, _rid = self._kid()
        for who, ch in (("u1", self.a), ("u2", self.b), ("u3", kid)):
            self._vote(ch, who)
        out = vote.chart(self.conn, self.topic, colour=False)
        main, _, child = out.partition("── 子论点")
        self.assertNotIn(kid, main, "派生子论点混进了当前层级那张图")
        self.assertIn(f"子论点 {kid}（derived_from {self.a}）", out)
        self.assertIn("另开一张", out)
        self.assertIn("另有 1 票投在派生子论点上", main)

    def test_a_rejected_derived_from_edge_puts_it_back_on_the_main_chart(self):
        """`§C2.4`：边可拒绝。推翻之后它就不是子论点了 —— 图要跟着变。"""
        kid, rid = self._kid()
        scaffold.reject_relation(self.conn, rid, by="甲")
        self._vote(self.a, "u1")
        self._vote(kid, "u2")
        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertNotIn("子论点", out)
        self.assertIn(kid, out, "边被推翻了，它就该回到当前层级里")

    # ---- 版本：旧票属于旧问题版本 -----------------------------------------

    def test_two_question_versions_are_two_charts_never_one_line(self):
        """`§C12.5` 的核心：绝不跨版本相加。"""
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        scaffold.revise(self.conn, self.topic, author="需求方",
                        content={"text": "远程办公效率更高吗？（改过）",
                                 "raw_text": "远程办公效率更高吗？"})
        self._vote(self.a, "u3")

        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertEqual(out.count("── Topic"), 2, "两个问题版本被画成了同一张")
        self.assertIn("共 2 票", out)
        self.assertIn("共 1 票", out)
        self.assertNotIn("共 3 票", out)

    def test_the_version_label_carries_the_question_it_belongs_to(self):
        """只印一个 revision 行号，读者读不出这是哪一版。

        而且这一版问的**不是**讨论的题目：题目（`远程办公效率更高吗？`）从头到尾
        不变，变的是**议题自己**那一版的原话。所以标签必须印**那一版的议题原文**
        —— 印讨论题目等于没印，两版看起来会一模一样。
        """
        self._vote(self.a, "u1")
        scaffold.revise(self.conn, self.topic, author="需求方",
                        content={"text": "远程办公效率更高吗？（改过）",
                                 "raw_text": "远程办公效率更高吗？"})
        self._vote(self.a, "u2")
        self._vote(self.b, "u3")
        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertIn("问题版本 revision #", out)
        # 改版前那一版，带的是它自己那句
        self.assertIn("「远程办公效率更高。」", out)
        # 改版后那一版，带的是改过的那句
        self.assertIn("「远程办公效率更高吗？（改过）」", out)
        # 讨论的题目**不该**出现在版本标签里 —— 它不随版本变
        self.assertNotIn("「远程办公效率更高吗？」", out)

    def test_charts_follow_the_order_the_versions_were_first_voted_on(self):
        """顺序按「哪一版先有人投」，不按行号 —— 行号是 revision 的**行号**，
        按字符串排会把 `#10` 排到 `#2` 前面（真的这么错过一次）。
        """
        self._vote(self.a, "u1")
        scaffold.revise(self.conn, self.topic, author="需求方",
                        content={"text": "远程办公效率更高吗？（改过）",
                                 "raw_text": "远程办公效率更高吗？"})
        self._vote(self.a, "u2")
        self._vote(self.b, "u3")

        first: dict = {}
        for row in self.conn.execute(
                "SELECT v.id FROM relation r JOIN artifact v ON v.id = r.to_id"
                " WHERE r.from_id = ? AND r.kind = 'contains' AND r.state = 'active'"
                "   AND v.type = 'Vote' ORDER BY v.id", (self.topic,)):
            ver = scaffold.content_of(self.conn, row["id"])["question_version"]
            first.setdefault(ver, row["id"])
        out = vote.chart(self.conn, self.topic, colour=False)
        shown = [int(m) for m in re.findall(r"问题版本 revision #(\d+)", out)]
        self.assertEqual(shown, list(first))

    # ---- 颜色 -------------------------------------------------------------

    def test_colour_adds_escapes_and_a_swatch_per_option(self):
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        out = vote.chart(self.conn, self.topic, colour=True)
        self.assertIn("\033[", out)
        self.assertIn("\033[0m", out)
        self.assertNotIn("本输出没有颜色", out)

    def test_without_colour_it_says_the_lines_cannot_be_told_apart(self):
        """Braille 字符不带颜色 —— 没有颜色时那几条线在字符层面就是合并的。

        所以**必须说出来**，而且图例里的数要一直在：不说的话，一份重定向到文件的
        输出看起来还是一张正常的图，而它其实读不出哪条是哪条。
        """
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        out = vote.chart(self.conn, self.topic, colour=False)
        self.assertNotIn("\033", out, "说了没颜色，却还有转义序列")
        self.assertIn("本输出没有颜色", out)
        self.assertIn("分不开", out)
        self.assertIn("最终 1 票", out)      # 可读的那部分照样在

    # ---- 只读与边界 -------------------------------------------------------

    def test_drawing_writes_nothing(self):
        """`§C2.5` 第 3 档：派生视图不得写回底层（`§C7.1` ④）。"""
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        before = self._dump()
        vote.chart(self.conn, self.topic, colour=True)
        vote.chart(self.conn, self.topic, colour=False)
        self.assertEqual(self._dump(), before)

    def test_passing_a_debate_id_where_a_topic_belongs_is_refused(self):
        """静默画出一张「还没有票」的图，是比报错更坏的一种错。"""
        self._vote(self.a, "u1")
        self._vote(self.b, "u2")
        with self.assertRaises(ScaffoldError):
            vote.chart(self.conn, self.debate_id, colour=False)
        with self.assertRaises(ScaffoldError):
            vote.chart(self.conn, "topic-9999", colour=False)

    def test_a_topic_with_no_claims_says_there_is_nothing_to_draw(self):
        d = debate.open_debate(self.conn, question="空议题", by="甲")
        empty = _topic_and_first_claim(
            self.conn, debate_id=d["debate"], text="还没定。", by="甲")[0]
        # 把唯一那条命题从 Topic 上摘掉，造出「有 Topic 没命题」的形状
        for row in self.conn.execute(
                "SELECT id FROM relation WHERE from_id = ? AND kind = 'contains'",
                (empty,)).fetchall():
            self.conn.execute("UPDATE relation SET state = 'rejected' WHERE id = ?",
                              (row["id"],))
        self.conn.commit()
        out = vote.chart(self.conn, empty, colour=False)
        self.assertIn("没有可画的东西", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
