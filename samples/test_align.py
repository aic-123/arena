"""`align.py` 的判据检查 —— 特别是**「删」和「改」不是一回事**那一条。

    python samples/test_align.py

为什么值得留：这条判据我写错过一次。第一版要求「只有一段连续匹配」，
于是 0002「大学文凭的回报~~这几年~~在下降」被判成折不出来 ——
它一个字都没改，只是删了三个字。**错在严，不严在松，一样是错**：
它会把可折的样本报成不可折，而报告里两件事长得一样。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # 从哪个目录跑都行

from align import classify  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
