"""`checks.py` 自己的测试 —— 证明 B1–B13 **不是空转**。

--- 为什么会有这个文件 -----------------------------------------------------

2026-09-25 一天之内，`checks.py` 有**三条检查**是空转的，而它们全都打印「过」：

| 检查 | 空转的原因 | 怎么发现的 |
|---|---|---|
| B6 | 逐 **token** 匹配，跨 token 的写法匹配不到 | 手工注入阈值，没响 |
| B6 | 正则打错靶子（抓百分号字面量，不是比较运算） | 同上 |
| B8 | 靠标记字段触发，标记一改名它就没事可做 | 我改了标记，它照样「过」 |

**这三个都不是被"跑一遍看结果"发现的，都是被注入验伪发现的。**
而手工注入本身又出了第四个错：我做 B6 验伪时把探针打进了 `debate.py`，
没备份、事后 `rm` 了一个不存在的 `.bak`，**残留物留在了产物文件里，而 checks 是绿的**。

所以这件事不能再靠手工仪式。这里把它变成自动化测试：

* 往 `arena/` 下写一个**临时** `.py` / `samples/` 下写一个**临时** `.md`
* 让 `checks.py` 去扫
* 断言它**命中**
* 删掉临时文件

**全程不碰任何产物文件** —— 所以「忘了还原」在结构上不可能发生。

    python test_checks.py
"""

from __future__ import annotations

import unittest
from pathlib import Path

import checks

ROOT = Path(__file__).parent


def _clean_probes() -> None:
    """把验伪探针**连编译产物一起**清掉。

    为什么不是只删 `_tmp_probe_zzz.py` / `samples/_tmp_probe_zzz.md` 那两个路径：
    探针 `.py` 只要被**编译**过一次（`python -m compileall .`、或任何 import 它的
    东西），就会在 `__pycache__/` 下留下 `_tmp_probe_zzz.cpython-3xx.pyc`。
    那个 `.pyc` 删不掉的话，`Test00NoResidueAtStart` 下一轮就红，
    而它报的是「上一轮被打断了」—— **误导**。

    **2026-09-25 实测踩到过**：跑完 `python -m compileall .` 之后
    `test_checks.py` 连红两次（`Test00` 和 `TestNoResidue` 各一次），
    真正的原因是一个 `.pyc`，而 `rm -f _tmp_probe_zzz.py` 永远删不到它。
    所以这里用 `rglob` 一次扫干净 —— 「残留自愈」这条规矩要自愈得彻底。

    `missing_ok=True`：收尾不该因为「文件已经不在」而抛 —— 那会把**真正的**
    异常盖掉，报出来变成一个看不懂的 `FileNotFoundError`。
    """
    for p in ROOT.rglob("_tmp_probe*"):
        p.unlink(missing_ok=True)


def _scan_with_temp_py(source: str) -> dict:
    """把 source 写成一个临时 .py 放进 arena/，跑完**全部**检查，删掉。"""
    probe = ROOT / "_tmp_probe_zzz.py"
    # 残留自愈：**不是** `assert not probe.exists()`。
    # 为什么改成自愈，见 `Test00NoResidueAtStart` —— 一条残留会让十几条用例
    # 连锁失败，而且第一条失败报的地方和真正的原因无关。
    # 「有没有残留」这个问题由开头那一条和结尾那一条各管一次。
    _clean_probes()
    probe.write_text(source, encoding="utf-8")
    try:
        return {code: run() for code, _what, _clause, run in checks.all_checks()}
    finally:
        # 「有没有残留」由 Test00（开跑前）和 TestNoResidue（跑完后）各管一次。
        _clean_probes()


class Test00NoResidueAtStart(unittest.TestCase):
    """**开跑之前**，产物目录必须是干净的。

    为什么单开一条、而且类名排在最前（`Test00…` 在字母序里先于 `TestB12…`）：

    残留的**后果会扩散**。每个 `_probe()` / `_scan_with_temp_py()` 原来都以
    `assert not probe.exists()` 开头，于是一个残留文件会让后面十几条用例连锁失败 ——
    **而第一条失败报的地方和真正的原因无关**。

    实测（手动放一个残留探针，跑一遍）：**19 条失败**，
    第一条报的是

        FAIL: test_B12_accepts_a_type_that_comes_from_a_decision

    看起来像 B12 的检查坏了。真正的原因（「上次没清干净」）只在
    **最后一条** `TestNoResidue.test_no_probe_files_left_behind` 里才出现。
    一条残留，19 条误导性失败。

    所以把「有没有残留」**收敛成两条、各管一头**：
    - 本类管**上一轮**留下的（跑在第一条）
    - `TestNoResidue` 管**这一轮**留下的（跑在最后一条）
    中间的 helper 一律 `_clean_probes()` 自愈，不再各自断言。

    这和本仓库一直在防的那类病是同一个：**残留和检查失效长得一模一样**。
    """

    def test_no_residue_before_the_run_starts(self):
        # 报**绝对路径**，不报 `p.name` —— `rglob` 是递归的，只给文件名的话
        # 看不出残留在哪个目录，等于没报。
        left = sorted(str(p.resolve()) for p in ROOT.rglob("_tmp_probe*"))
        self.assertEqual(
            left, [],
            f"开跑前就有残留：{left} —— 上一轮被打断了"
            f"（管道提前关闭 / Ctrl-C / 进程被杀）。"
            # ⚠️ 这句必须把 `__pycache__` 说出来。2026-09-25 实测：
            # 探针 `.py` 被 `compileall` 编译过一次之后，残留物是
            # `__pycache__/_tmp_probe_zzz.cpython-3xx.pyc` —— 而
            # `rm -f _tmp_probe_zzz.py` **删不到它**。旧文案只说「删掉再跑」，
            # 于是照着做的人会卡在「删了还是红」的循环里。
            "清掉再跑：`rm -f _tmp_probe_zzz.py samples/_tmp_probe_zzz.md`"
            " **以及** `rm -f __pycache__/_tmp_probe_zzz.*.pyc`"
            "（探针被编译过的话，残留物是那个 .pyc）。",
        )


class TestEveryCheckFires(unittest.TestCase):
    """每一条检查：喂它一个真违规，必须命中。"""

    def test_B1_fires_on_a_real_lock(self):
        for src in ("import threading\nmutex = threading.Lock()\n",
                    "flock = open('x')\n",
                    "if not deadlock_free: pass\n"):
            hits = _scan_with_temp_py(src)
            self.assertTrue([h for h in hits["B1"] if "_tmp_probe" in h[0]],
                            f"B1 没抓到：{src!r}")

    def test_B1_does_not_fire_on_the_word_blocks(self):
        """`blocks` / `blocking` 里含 `lock` 是子串巧合，不是锁。"""
        hits = _scan_with_temp_py("def f():\n    return blocks_the_write()\n")
        self.assertFalse([h for h in hits["B1"] if "_tmp_probe" in h[0]])

    def test_B5_fires_on_a_scalar_verdict(self):
        hits = _scan_with_temp_py("evidence_score = 8.7\n")
        self.assertTrue([h for h in hits["B5"] if "_tmp_probe" in h[0]])

    def test_B6_fires_on_a_threshold(self):
        """这是 2026-09-25 真实空转过的那一条。"""
        for src in ("unused_char_ratio = 0.5\nif unused_char_ratio > 0.3:\n    pass\n",
                    "v = m['unused_ratio']\nif v >= 0.2:\n    pass\n"):
            hits = _scan_with_temp_py(src)
            self.assertTrue([h for h in hits["B6"] if "_tmp_probe" in h[0]],
                            f"B6 没抓到：{src!r}")

    def test_B6_does_not_fire_on_a_ratio_displayed_as_a_percentage(self):
        """`.2%` 是把比率印给人看，不是阈值。"""
        hits = _scan_with_temp_py("print(f\"占比 = {r:.2%}\")\n")
        self.assertFalse([h for h in hits["B6"] if "_tmp_probe" in h[0]])

    def test_B6_does_not_fire_on_a_return_annotation(self):
        """`-> float` 里的 `>` 不是比较运算 —— 少了 `[0-9]` 就会误报。"""
        hits = _scan_with_temp_py(
            "def elapsed_seconds(a, b) -> float:\n    return b - a\n")
        self.assertFalse([h for h in hits["B6"] if "_tmp_probe" in h[0]])

    def test_B5_does_not_fire_on_a_docstring_that_quotes_the_clause(self):
        """修订四：**文档不是产物行为**。

        `debate.py` 为了说明 `§C6.1` 为什么禁止标量分，得引用规范的原例 ——
        引用一次就被自己的检查抓一次。而 docstring 里写不出一个分数来。
        """
        hits = _scan_with_temp_py(
            '"""§C6.1 禁止形如 evidence_score = 8.7 的标量设计。"""\n'
            "def f():\n"
            '    """这里再引一次 evidence_score。"""\n'
            "    return 1\n"
        )
        for code in ("B2", "B5"):
            self.assertFalse([h for h in hits[code] if "_tmp_probe" in h[0]],
                             f"{code} 把 docstring 里的引文当成产物行为了")

    def test_B5_still_fires_on_a_string_literal(self):
        """但**字符串字面量照抓** —— 把它写成 dict 的键是真产物行为。

        这两条必须分开验：只验上一条的话，「docstring 不算」和
        「引号里的都不算」长得一模一样。
        """
        hits = _scan_with_temp_py('row = {"evidence_score": 1}\n')
        self.assertTrue([h for h in hits["B5"] if "_tmp_probe" in h[0]],
                        "B5 连字符串字面量都不抓了 —— 口径放得比该放的宽")

    def test_B7_fires_on_a_third_party_import(self):
        hits = _scan_with_temp_py("import requests\n")
        self.assertTrue([h for h in hits["B7"] if "_tmp_probe" in h[0]])

    def test_all_checks_are_non_vacuous(self):
        """一句总账：每条检查都必须至少抓到一个真违规。

        将来加了 B9、B10，忘了在这个文件里给它写用例 —— 这条会失败。
        遍历的是 `checks.all_checks()`（唯一登记表），不是 `CHECKS` 列表，
        所以 B6 / B7 / B8 也在这条总账里 ——
        它们以前是 `main()` 里手工追加的，总账盖不到。
        """
        # B8 扫 samples/*.md、B9 扫固定那一个 concurrency.py、B12 扫 confirm.py ——
        # 喂这个 .py 探针没用。它们的非空转各自单独钉住：
        # B8 在 TestB8Fires，B9 在 TestB9Fires，B12 在 TestB12Fires。
        needs_md_probe = {"B8", "B9", "B12", "B13"}
        probe_src = (
            "import requests\n"                      # B7
            "mutex = 1\n"                            # B1
            "weight = 2\n"                           # B2
            "threshold = 3\n"                        # B3
            "consensus = 4\n"                        # B4
            "evidence_score = 5\n"                   # B5
            "v = m['unused_ratio']\n"                # B6（跨行那种写法）
            "if v >= 0.2:\n    pass\n"
            "conn.execute(\"SELECT n FROM seq\")\n"  # B10：号跑出了 scaffold.py
            "conn.execute(\"UPDATE revision SET content = 1\")\n"   # B11：覆盖版本
        )
        hits = _scan_with_temp_py(probe_src)
        for code, _what, _clause, _run in checks.all_checks():
            if code in needs_md_probe:
                continue
            self.assertTrue([h for h in hits[code] if "_tmp_probe" in h[0]],
                            f"{code} 是空转的：喂它违规它也不响")


class TestB8Fires(unittest.TestCase):
    """B8 靠标记触发 —— 标记改名它就会静默空转，所以必须单独验。"""

    def test_B8_fires_on_a_filled_annotation(self):
        probe = ROOT / "samples" / "_tmp_probe_zzz.md"
        _clean_probes()
        probe.write_text(
            "## 9999\n\n```yaml\n"
            "input: \"x\"\n"
            "annotation_status: 待需求方标注\n"
            "annotation:\n"
            "  proposed_count: 2\n"          # ← 声明了待标注，却填了数
            "  agent_filled: 需求方填写\n"
            "```\n",
            encoding="utf-8",
        )
        try:
            hits = checks.check_placeholder_not_annotated()
            mine = [h for h in hits if "_tmp_probe" in h[0]]
            self.assertEqual(len(mine), 2, f"B8 没抓到，实际：{hits}")
        finally:
            _clean_probes()

    def test_B8_lets_a_truly_annotated_sample_through(self):
        """需求方真标完之后，B8 就不该再管它。"""
        probe = ROOT / "samples" / "_tmp_probe_zzz.md"
        _clean_probes()
        probe.write_text(
            "## 9999\n\n```yaml\n"
            "input: \"x\"\n"
            "annotation_status: 需求方已标注\n"
            "annotation:\n"
            "  proposed_count: 2\n"
            "  agent_filled: 需求方填写\n"
            "```\n",
            encoding="utf-8",
        )
        try:
            hits = checks.check_placeholder_not_annotated()
            self.assertFalse([h for h in hits if "_tmp_probe" in h[0]])
        finally:
            _clean_probes()


class TestB9Fires(unittest.TestCase):
    """B9 盯的是并发装置 —— 它**自己最容易被写成剧本**，所以必须正反两向验。

    这一条防的是本阶段最容易犯的错：为了让「并发问题」出现，
    顺手塞一个 `sleep` 把窗口撑开。那样出现的现象是我安排的。
    """

    def _probe(self, src: str) -> list:
        probe = ROOT / "_tmp_probe_zzz.py"
        _clean_probes()
        probe.write_text(src, encoding="utf-8")
        try:
            return checks.check_apparatus_is_not_a_script(probe)
        finally:
            _clean_probes()

    def test_B9_fires_on_each_of_the_three_red_lines(self):
        for src, why in (
            ("import time\ntime.sleep(0.5)\n", "注入延迟"),
            ("conn.execute(\"INSERT INTO event VALUES (1)\")\n", "直接改库"),
            ("conn.execute(\"UPDATE artifact SET state = 'active'\")\n", "直接改库"),
            ("conn.execute(\"DELETE FROM relation\")\n", "直接改库"),
            ("import threading\n", "内部协调"),
            ("barrier = Barrier(2)\n", "内部协调"),
        ):
            self.assertTrue(self._probe(src), f"B9 没抓到「{why}」：{src!r}")

    def test_B9_allows_reads_but_not_writes(self):
        """边界钉在这里：**读可以，写不行**。

        `§C11.2` 拦的是「我在摆布状态」，不是「我在看状态」——
        一条 `SELECT` 改不了谁先谁后，伪造不出竞争窗口。
        第一版把 `SELECT` 也拦了，代价是拦住了自己：
        `_interleaving()` 靠读 event 表来回答「这两个主体到底有没有真重叠」，
        而那正是本步最该测的一件事。
        """
        self.assertFalse(self._probe(
            "conn.execute('SELECT actor FROM event ORDER BY id')\n"))
        self.assertTrue(self._probe(
            "conn.execute('UPDATE event SET actor = 1')\n"))

    def test_B9_accepts_the_real_apparatus(self):
        """真装置必须是干净的，否则这条检查本身就是在骂自己。

        ⚠️ 光断言 `== []` 是**不够**的：B9 在目标文件不存在时也返回 `[]`
        （「还没写装置，这条暂不适用」），所以「装置干净」和「没有装置」
        在这一句里长得一模一样 —— 用例会**空过**。
        所以先单独钉住「B9 默认扫的那个文件真的在」。

        2026-09-25 给装置改名（`concurrent.py` → `concurrency.py`）时踩到这个形状：
        漏改 `checks.py` 里的路径，B9 从此再也不会响，而且**什么都不会说**。
        """
        self.assertTrue(
            checks.APPARATUS.is_file(),
            f"B9 默认扫的 {checks.APPARATUS} 不存在 —— 这条检查现在是空转的，"
            "它打印的「过」没有任何含义。改了装置文件名就要同步改 `checks.APPARATUS`。",
        )
        self.assertEqual(checks.check_apparatus_is_not_a_script(), [])


class TestB13Fires(unittest.TestCase):
    """B13 是 B8 的另一半：B8 管「待标注 → 必须空」，B13 管「有值 → 必须署名」。

    加它的原因就是：折算之后 B8 在这份文件上罩不住了，还在打印「过」。
    所以这一条也**必须正反两向验** —— 一条只会打印「过」的检查，
    和一条真的通过了的检查，长得一模一样。
    """

    def _probe(self, body: str) -> list:
        probe = ROOT / "samples" / "_tmp_probe_zzz.md"
        _clean_probes()
        probe.write_text(body, encoding="utf-8")
        try:
            return [h for h in checks.check_annotated_samples_name_their_source()
                    if "_tmp_probe" in h[0]]
        finally:
            _clean_probes()

    def test_B13_fires_when_a_value_has_no_author(self):
        hits = self._probe(
            "## 9999\n\n```yaml\ninput: \"x\"\n"
            "annotation_status: 需求方授权折算\n"
            "annotation:\n"
            "  proposed_count: 2\n"          # ← 填了值
            "```\n")                          # ← 却没说谁填的
        self.assertEqual(len(hits), 1, f"B13 没抓到，实际：{hits}")

    def test_B13_lets_a_signed_annotation_through(self):
        self.assertEqual(self._probe(
            "## 9999\n\n```yaml\ninput: \"x\"\n"
            "annotation_status: 需求方授权折算\n"
            "annotation:\n"
            "  proposed_count: 2\n"
            "  agent_filled: 需求方授权折算（2026-09-25）\n"
            "```\n"), [])

    def test_B13_does_not_touch_blocks_that_B8_owns(self):
        """两块地不能重叠：待标注的块归 B8，B13 少报，免得同一件事响两遍。"""
        self.assertEqual(self._probe(
            "## 9999\n\n```yaml\ninput: \"x\"\n"
            "annotation_status: 待需求方标注\n"
            "annotation:\n"
            "  proposed_count: 2\n"          # B8 会抓这个
            "```\n"), [])

    def test_B13_accepts_the_real_samples(self):
        """真样本目录必须是干净的。"""
        self.assertEqual(checks.check_annotated_samples_name_their_source(), [])


class TestB12Fires(unittest.TestCase):
    """B12 盯的是缺口② 那一行 —— 「命题类型被写死」。

    它挡的动作很具体：把 `confirm.py` 里
    `type_=resolved.get(x["key"], "Claim")` 改回 `type_="Claim"`。
    改回去之后「X，所以 Y」的两端又会静默变成主张。
    """

    def _probe(self, src: str) -> list:
        probe = ROOT / "_tmp_probe_zzz.py"
        _clean_probes()
        probe.write_text(src, encoding="utf-8")
        try:
            return checks.check_proposition_type_is_not_hardcoded(probe)
        finally:
            _clean_probes()

    def test_B12_fires_on_a_hardcoded_proposition_type(self):
        for ty in ("Claim", "Evidence"):
            self.assertTrue(self._probe(f'add_artifact(conn, type_="{ty}", content=x)\n'),
                            f"B12 没抓到 type_=\"{ty}\"")

    def test_B12_accepts_a_type_that_comes_from_a_decision(self):
        """类型只要**不是字面量**就放过 —— 这个检查管的是来源，不是名字。"""
        self.assertFalse(self._probe(
            'add_artifact(conn, type_=resolved.get(x["key"], DEFAULT), content=x)\n'))

    def test_B12_accepts_the_real_confirm_module(self):
        """真 `confirm.py` 必须是干净的，否则这条检查在骂自己。"""
        self.assertEqual(checks.check_proposition_type_is_not_hardcoded(), [])


class TestNoResidue(unittest.TestCase):
    """产物目录里不许有验伪留下的临时文件。

    这一条管的是**这一轮跑完**留下的。上一轮留下的由 `Test00NoResidueAtStart`
    在开跑前管 —— 两条各管一头，中间的 helper 一律自愈。
    分开的原因见 `Test00NoResidueAtStart` 的 docstring（一条残留 → 19 条误导性失败）。
    """

    def test_no_probe_files_left_behind(self):
        # 同样报绝对路径 —— `rglob` 是递归的，只给文件名看不出在哪。
        left = sorted(str(p.resolve()) for p in ROOT.rglob("_tmp_probe*"))
        self.assertEqual(left, [], f"有验伪残留物没清掉：{left}")

    def test_exempt_set_is_exactly_the_falsification_harness(self):
        """豁免就是留洞。洞的大小必须是被钉住的，不能随手扩大。

        `checks.py` 与 `test_checks.py` 都**必须**写出违规词才能工作
        （前者要拿它们当正则，后者要拿它们当语料），所以只能豁免。
        但豁免名单一旦能悄悄变长，B1–B13 就随时可以被架空 ——
        想多豁免一个，就来改这条用例，改的时候会被看见。
        """
        self.assertEqual(
            checks.EXEMPT, {"checks.py", "test_checks.py"},
            "豁免集合变了 —— 如果是有意的，在这里写明理由再改；"
            "如果是无意的，说明有一条检查被架空了。",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
