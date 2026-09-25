"""`align.py` 的判据检查 —— 两样：**「删」和「改」不是一回事**，以及
**「原文未出现」标记的判据**。

    python samples/test_align.py

为什么第一样值得留：这条判据我写错过一次。第一版要求「只有一段连续匹配」，
于是 0002「大学文凭的回报~~这几年~~在下降」被判成折不出来 ——
它一个字都没改，只是删了三个字。**错在严，不严在松，一样是错**：
它会把可折的样本报成不可折，而报告里两件事长得一样。

为什么第二样值得留：那个标记是 `proposed_count` 的输入。
第一版判据想用「覆盖率 0」，**实测会改错 4 条**（B4 的重转述、F2/F3 的括号注释
覆盖率也是 0）。下面把**判对的和判错的都钉成用例** —— 只钉判对的那几例，
换个「只看覆盖率」的实现照样全过。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # 从哪个目录跑都行

import align  # noqa: E402  —— 写回测试要改 align.INPUTS，所以整个模块要进来
from align import classify, marker_mismatches, marker_should_be  # noqa: E402


class TestClassify(unittest.TestCase):
    def test_exact_slice_is_foldable(self):
        src = "这家公司开始裁员了，说明它快不行了。"
        kind, a = classify(src, "这家公司开始裁员")
        self.assertEqual(kind, "逐字")
        self.assertEqual(a["span"], (0, 8))

    def test_deletion_is_foldable_because_nothing_was_rewritten(self):
        """0002：删掉「这几年」—— 每个字都还是原文的。**删不是改写。**"""
        kind, a = classify("大学文凭的回报这几年在下降。", "大学文凭的回报在下降")
        self.assertEqual(kind, "逐字")
        self.assertEqual(a["span"], (0, 13), "边界要包住被删掉的那三个字")

    def test_substitution_is_not_foldable(self):
        """0001：`高于` vs `原文的 比…高` —— 有字是原文里没有的，边界只能推。"""
        kind, _ = classify("远程办公的效率比坐办公室高。", "远程办公的效率高于坐办公室")
        self.assertEqual(kind, "转述")

    def test_reordering_is_not_foldable(self):
        kind, _ = classify("工作强度太大", "太大工作强度")
        self.assertEqual(kind, "转述")

    def test_relation_node_has_no_boundary_by_construction(self):
        """`n1 导致 n2` 是关系不是区间 —— 报「折不出来」是错的答案。"""
        kind, a = classify("现在的工作强度太大了，所以大家都不愿意往上爬。", "n1 导致 n2")
        self.assertEqual(kind, "非文本")
        self.assertIsNone(a)

    def test_the_span_slice_always_equals_the_node_text(self):
        """`逐字` 这个判定的落地保证：拿 span 去切原文，切出来就是节点文本本身。

        除了有删的情况 —— 那个切出来是「节点文本 + 被删的字」，所以只断言包含。
        """
        cases = [("这家公司开始裁员了，说明它快不行了。", "这家公司开始裁员"),
                 ("大学文凭的回报这几年在下降。", "大学文凭的回报在下降")]
        for src, node in cases:
            kind, a = classify(src, node)
            self.assertEqual(kind, "逐字")
            got = src[a["span"][0]:a["span"][1]]
            self.assertTrue(all(c in got for c in node),
                            f"{node!r} 里有字不在 {got!r} 里 —— span 不对")


class TestAbsentMarker(unittest.TestCase):
    """「原文未出现」标记的判据：类型是 `Assumption` **且** 覆盖率 0。

    每一组都成对钉 —— **判对的和判错的都在**。只钉「判对的」，
    换个「只看覆盖率」的实现照样全过，而那版实测会改错 4 条。
    """

    def test_assumption_absent_from_source_is_marked(self):
        """C1 n3「多数人的选择是对的」—— 原文里没有任何词表达它。**该标。**"""
        kind, a = classify("既然大家都在考研，那我也得考。", "多数人的选择是对的")
        self.assertTrue(marker_should_be("Assumption", kind, a))

    def test_heavy_paraphrase_is_not_absent_even_at_zero_coverage(self):
        """B4 n2/n3 覆盖率 **0%**，但标签文件 `:91` 明写「n3 是独立命题」。

        **只看覆盖率的实现在这里判错** —— 这是那条路最硬的反例。
        """
        src = "他成绩好是因为聪明，不是因为努力。"
        for text in ("原因是聪明", "原因不是努力"):
            kind, a = classify(src, text)
            self.assertEqual(a["coverage"], 0.0, "这两条确实是 0% —— 所以覆盖率分不开")
            self.assertFalse(marker_should_be("Claim", kind, a),
                             f"{text!r} 不是「原文未出现」，它是重转述")

    def test_parenthetical_subtopic_is_not_absent(self):
        """F2 n2 / F3 n2 覆盖率 0%，但那是**括号注释** —— 内容在原文里。"""
        cases = [("都在讨论要不要限制孩子玩游戏，可没人问：为什么孩子会沉迷？",
                  "孩子沉迷的成因（因果争议）"),
                 ("这个政策好不好，取决于你说的是短期还是长期 —— 短期有效，长期有害。",
                  "时间尺度（短期 / 长期）")]
        for src, text in cases:
            kind, a = classify(src, text)
            self.assertEqual(a["coverage"], 0.0, f"{text!r} 确实是 0%")
            self.assertFalse(marker_should_be("Subtopic", kind, a))

    def test_assumption_present_in_source_is_not_marked(self):
        """E3 n2「人会理性选择」是 `Assumption`，但原文引号里就是它 —— **不该标**。

        所以判据不能只写「类型是 `Assumption`」，要带上覆盖率这一半。
        """
        kind, a = classify("你的结论建立在「人会理性选择」这个前提上，但现实中不是这样。",
                           "人会理性选择")
        self.assertEqual(kind, "逐字")
        self.assertFalse(marker_should_be("Assumption", kind, a))

    def test_relation_node_is_not_absent(self):
        """0004 B1 n3「n1 导致 n2」判 `非文本`，可「所以」占 [11:13] —— **不该标**。

        所以判据也不能只写「不是关系节点」。
        """
        src = "现在的工作强度太大了，所以大家都不愿意往上爬。"
        kind, a = classify(src, "n1 导致 n2")
        self.assertEqual(kind, "非文本")
        self.assertFalse(marker_should_be("Claim", kind, a))

    def test_sample_files_markers_all_match_the_criterion(self):
        """真文件：20 条的标记必须**全部**符合判据 —— 不符就报，不静默过。"""
        total, bad = marker_mismatches()
        self.assertEqual(total, 3, "这份样本里有 3 处标记（C1/C2/C3 的 n3）")
        self.assertEqual(bad, [], "标记与判据不符：\n" + "\n".join(bad))


class TestWriteBack(unittest.TestCase):
    """`--write` 是「重新生成」的工具，它不能顺手改掉别的东西。"""

    def _rewritten(self) -> bytes:
        """复制真文件到临时目录，对**副本**跑一次 `write_back()`，返回写回后的字节。

        不动真文件；顺手把它那句 print 吃掉，免得污染测试输出。
        """
        import io
        import tempfile
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "inputs.md"
            tmp.write_bytes(align.INPUTS.read_bytes())
            keep, align.INPUTS = align.INPUTS, tmp
            try:
                with redirect_stdout(io.StringIO()):
                    align.write_back()
            finally:
                align.INPUTS = keep
            return tmp.read_bytes()

    def test_write_back_does_not_reintroduce_crlf(self):
        """行尾必须还是 LF。

        `Path.write_text` 的默认 `newline=None` 在 Windows 上会把 `\\n` 翻成 `\\r\\n`。
        **实测踩过**：跑一次 `--write`，`inputs.md` 变成 379 个 CRLF、0 个 LF ——
        `.gitattributes` 钉的 LF 策略被悄悄抹掉，而 git 只在提交时才警告一句。
        """
        raw = self._rewritten()
        self.assertNotIn(b"\r\n", raw, "写回把行尾改成 CRLF 了 —— newline=\"\\n\" 漏了？")
        self.assertIn(b"\n", raw, "写回之后一个字都没有，这测试就是空转的")

    def test_write_back_leaves_proposed_count_alone(self):
        """`write_back` 只碰 `boundaries` / `boundaries_basis`。

        `proposed_count` 是另一件事 —— 顺手改它，值就跟着漂，而且看不出来。
        """
        after = self._rewritten().decode("utf-8")
        before = align.INPUTS.read_text(encoding="utf-8")
        for key in ("proposed_count:", "proposed_count_basis:", "agent_filled:"):
            pick = [ln for ln in after.splitlines() if ln.strip().startswith(key)]
            want = [ln for ln in before.splitlines() if ln.strip().startswith(key)]
            self.assertEqual(pick, want, f"`write_back` 动了 {key} —— 它不该动")


if __name__ == "__main__":
    unittest.main()
