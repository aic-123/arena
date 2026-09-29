"""`cli.py` 的检查 —— **跑的是产品那一面**。

每一条都是 `subprocess` 起一个真的 python，喂真的 stdin，读真的 stdout 和退出码，
库也是真的文件。**不 import confirm / debate 来「模拟」用户** ——
那样测的是库的 API：参数校验、视图组装都覆盖得到，但**恰好绕过了入口本身**
（命令怎么解、话怎么问、拒了之后到底写没写）。而后者才是产品要过的那一关。

重点在**闸门**：任何一条路走不通时，库里必须一个字都没多。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "t.db")

    def run_cli(self, *args: str, stdin: str = ""):
        env = {**os.environ, "ARENA_DB": self.db, "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(
            [sys.executable, "cli.py", *args], cwd=ROOT, env=env,
            input=stdin, capture_output=True, text=True, encoding="utf-8",
        )

    def ok(self, *args: str, stdin: str = "") -> str:
        p = self.run_cli(*args, stdin=stdin)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def id_of(self, out: str, tag: str) -> str:
        m = re.search(rf"^{tag}: (\S+)$", out, re.M)
        self.assertIsNotNone(m, f"输出里没有 `{tag}:` 那一行：\n{out}")
        return m.group(1)

    def debate(self) -> str:
        return self.id_of(self.ok("new", "加班该不该给钱？", "甲"), "debate")

    def submit(self, d: str, who: str, text: str) -> str:
        return self.id_of(self.ok("submit", d, who, text), "draft")


class TestTheGate(Base):
    def test_逐条问_一条命题一次(self):
        """`§C5` 禁止一键全部确认 —— 所以命题有几个，就问几次。"""
        d = self.debate()
        dr = self.submit(d, "甲", "现在的工作强度太大了，所以大家都不愿意往上爬。")
        out = self.ok("confirm", d, dr, "甲", stdin="y\ny\ny\n\n\n")
        self.assertEqual(out.count("意义未被歪曲？"), 3)

    def test_确认之后议题和命题在视图里看得见(self):
        d = self.debate()
        dr = self.submit(d, "甲", "现在的工作强度太大了，所以大家都不愿意往上爬。")
        self.assertIn("已写入", self.ok("confirm", d, dr, "甲", stdin="y\ny\ny\n\n\n"))
        v = self.ok("view", d)
        self.assertIn("现在的工作强度太大了", v)
        self.assertIn("大家都不愿意往上爬", v)
        # 缺口①：因果两端的方向写在边上，视图里必须看得见。
        self.assertIn("causal_premise", v)
        self.assertIn("causal_conclusion", v)

    def test_默认类型在视图里看得见_不然推翻不了(self):
        """`§C2.5` 第 2 档：默认生效，但要**可推翻** —— 先得看得见它默认了什么。"""
        d = self.debate()
        dr = self.submit(d, "甲", "现在的工作强度太大了，所以大家都不愿意往上爬。")
        self.ok("confirm", d, dr, "甲", stdin="y\ny\ny\n\n\n")
        self.assertIn("类型是默认", self.ok("view", d))

    def test_一个都不回答就一个字都不写(self):
        """`§C5.3`：空是一个回答（空着比填错好）。但**空不是同意**。"""
        d = self.debate()
        dr = self.submit(d, "甲", "远程办公的效率比坐办公室高。")
        p = self.run_cli("confirm", d, dr, "甲", stdin="\n")
        self.assertEqual(p.returncode, 2)
        self.assertIn("没有写入任何东西", p.stdout)
        self.assertNotIn("远程办公的效率比坐办公室高", self.ok("view", d))

    def test_读到输入结束也一个字都不写(self):
        """EOF 是「用户走了」还是「脚本出错」分不出来 —— 分不出来就不许归因。

        答到一半断掉才算这条路：只答一条、后面几条根本没问就没了。
        """
        d = self.debate()
        text = "现在的工作强度太大了，所以大家都不愿意往上爬。"
        dr = self.submit(d, "甲", text)
        p = self.run_cli("confirm", d, dr, "甲", stdin="y\n")
        self.assertEqual(p.returncode, 2)
        self.assertIn("一个字都没写", p.stdout)
        self.assertNotIn("现在的工作强度太大了", self.ok("view", d))

    def test_不提交会被记下来而且不写(self):
        """`§C5.6` ③ A 类：已输入后离开 —— 系统内可见，所以必须记。"""
        d = self.debate()
        dr = self.submit(d, "甲", "远程办公的效率比坐办公室高。")
        p = self.run_cli("confirm", d, dr, "甲", stdin="q\n")
        self.assertEqual(p.returncode, 0)
        self.assertIn("abandoned", p.stdout)
        self.assertNotIn("远程办公的效率比坐办公室高", self.ok("view", d))


class TestRewordingRateCanMove(Base):
    def _rate(self, out: str) -> tuple[str, str]:
        m = re.search(r"换说法率[\s\S]*?(\d+) / (\d+)", out)
        self.assertIsNotNone(m, out)
        return m.group(1), m.group(2)

    def test_换个说法重交_换说法率才动得起来(self):
        """`§C5.6` ②：**先点了「意义被歪曲」再重写**才计入。

        这条在入口补上之前**永远算不出来** —— 换说法的动作产品里没有，
        所以分子恒为 0，而 `0 / n` 长得像「没人需要换说法」。
        这是 `§C7.2` 里被点名「最关键」的那个量（`§C2.4`）：
        数字不动，可能就是产品没给路走。
        """
        d = self.debate()
        dr = self.submit(d, "甲", "远程办公的效率比坐办公室高。")
        p = self.run_cli("confirm", d, dr, "甲", stdin="n\n")
        self.assertEqual(p.returncode, 0)
        self.assertIn("意义被歪曲", p.stdout)
        self.assertEqual(self._rate(self.ok("observe")), ("0", "1"))

        dr2 = self.id_of(
            self.ok("rewrite", d, dr, "甲", "在家干活比在工位上效率高。"), "draft")
        self.assertNotEqual(dr2, dr, "重写必须是**新的一份** draft，不许就地改")
        self.ok("confirm", d, dr2, "甲", stdin="y\n")
        self.assertEqual(self._rate(self.ok("observe")), ("1", "1"))

    def test_没点歪曲直接重写的不计入(self):
        """没点直接重写 = 自己改主意，不是被逼改口（`§C5.6` ② 的口径）。"""
        d = self.debate()
        dr = self.submit(d, "甲", "远程办公的效率比坐办公室高。")
        dr2 = self.id_of(
            self.ok("rewrite", d, dr, "甲", "在家干活比在工位上效率高。"), "draft")
        self.ok("confirm", d, dr2, "甲", stdin="y\n")
        self.assertEqual(self._rate(self.ok("observe")), ("0", "1"))

    def test_已经确认过的草稿不能退回重写(self):
        """`§C10`：进了结构的改它走 revision，不是新开一份 draft。"""
        d = self.debate()
        dr = self.submit(d, "甲", "远程办公的效率比坐办公室高。")
        self.ok("confirm", d, dr, "甲", stdin="y\n")
        p = self.run_cli("rewrite", d, dr, "甲", "换个说法")
        self.assertEqual(p.returncode, 1)
        self.assertIn("已是 confirmed", p.stderr)


class TestWrongId(Base):
    def test_视图拿到议题的号会报错(self):
        """原来它会**静默**返回一份形状正常的视图 —— 那是更坏的一种错。"""
        d = self.debate()
        dr = self.submit(d, "甲", "远程办公的效率比坐办公室高。")
        self.ok("confirm", d, dr, "甲", stdin="y\n")
        topic = re.search(r"Topic (topic-\d+)", self.ok("view", d)).group(1)
        p = self.run_cli("view", topic)
        self.assertEqual(p.returncode, 1)
        self.assertIn("不是 Debate", p.stderr)
        self.assertEqual(p.stdout, "", "报了错就不该再印一份看着正常的视图")

    def test_确认时把议题的号当讨论的号传进去会被拒(self):
        d = self.debate()
        # 视图里得先真的有一个 Topic 号可用。`new` 现在**不带 Topic**（一段原文一个
        # Topic），所以这个号只能靠确认一段别的原文挣出来 —— 以前它白送一个。
        dr1 = self.submit(d, "甲", "加班该给钱。")
        self.ok("confirm", d, dr1, "甲", stdin="y\n")
        topic = re.search(r"Topic (topic-\d+)", self.ok("view", d)).group(1)

        dr2 = self.submit(d, "甲", "远程办公的效率比坐办公室高。")
        p = self.run_cli("confirm", topic, dr2, "甲", stdin="y\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("不是 Debate", p.stderr)
        self.assertNotIn("远程办公的效率比坐办公室高", self.ok("view", d))


class TestChart(Base):
    """`chart` 命令 —— 从**终端**这一头看到的东西。

    这里能钉的只有入口和那几条「不许长得像零」的规矩：`cli.py` **没有**「投」这个
    命令（`cast_vote()` 要一份渲染时抓的 `vote_context()`，那要求先有投票页，
    见 DECLARATION §13）。所以 CLI 这一头**画不出折线** —— 折线的渲染在
    `test_arena.py::TestVoteChart` 里用库直接验。
    这里验的是：入口接上了、报错照原话、管道里不上色、以及「没有票」不许
    装成「票数为零」。
    """

    def _topic(self) -> tuple[str, str]:
        d = self.debate()
        dr = self.submit(d, "甲", "远程办公的效率比坐办公室高。")
        self.ok("confirm", d, dr, "甲", stdin="y\n")
        topic = re.search(r"Topic (topic-\d+)", self.ok("view", d)).group(1)
        return d, topic

    def test_没有票时不画空坐标系_而且照实说没有票(self):
        _d, topic = self._topic()
        out = self.ok("chart", topic)
        self.assertIn("还没有票", out)
        self.assertIn("票数为零不等于倾向为零", out)
        self.assertNotIn("└", out, "没有票却印了一个空坐标系")

    def test_把讨论的号当议题传进去会被拒(self):
        """票挂在 Topic 下，所以图也按 Topic 画 —— 拿 Debate 的号来要图是错的。"""
        d, _topic = self._topic()
        p = self.run_cli("chart", d)
        self.assertEqual(p.returncode, 1)
        self.assertIn("不是 Topic", p.stderr)
        self.assertEqual(p.stdout, "", "报了错就不该再印一张看着正常的图")

    def test_管道里不上色(self):
        """颜色按 `NO_COLOR` 约定判断（`no-color.org`）：不是终端就不上色。

        `cli.py` 这一层**不做这个判断**（它只管印），所以这里验的是
        「判断确实发生了」—— 管道里出来的必须是裸字符，不带 ANSI 转义。
        「不上色时要说出来」那一条要有票才走得到（没票就没有线可分），
        在 `test_arena.py::TestVoteChart` 里用库直接验。
        """
        _d, topic = self._topic()
        p = self.run_cli("chart", topic)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("\033[", p.stdout, "管道里不该有 ANSI 转义")

    def test_反对排序的那句话要走到终端这一头(self):
        """`§C9` #5 #7 / `§C12.1`：不许有名次，票数是偏好计数不是对错。

        库层说了不算 —— 它得**出现在用户真正看到的那些字里**。
        """
        _d, topic = self._topic()
        out = self.ok("chart", topic)
        self.assertIn("公共偏好", out)
        self.assertIn("不是真理判定", out)
        self.assertIn("没有排序轴，没有阈值", out)


class TestUsage(Base):
    def test_没有参数就印用法_退出码_2(self):
        p = self.run_cli()
        self.assertEqual(p.returncode, 2)
        self.assertIn("python cli.py new", p.stdout)

    def test_不认识的命令也印用法(self):
        p = self.run_cli("wat")
        self.assertEqual(p.returncode, 2)
        self.assertIn("python cli.py confirm", p.stdout)

    def test_缺参数不会写坏库(self):
        p = self.run_cli("submit", "debate-0001")
        self.assertEqual(p.returncode, 1)
        self.assertIn("缺参数", p.stderr)

    def test_chart_缺参数报的是用法(self):
        p = self.run_cli("chart")
        self.assertEqual(p.returncode, 1)
        self.assertIn("python cli.py chart", p.stderr)


class TestUpperCommand(Base):
    """`upper` 子命令 —— **默认只读**，要写就得把动作词说出来。

    这条约定和 `view` / `chart` / `observe` 一致。理由：提议是**高频**动作
    （每次重算都会跑），建是**低频**动作。默认写的话跑几次就多出一堆节点，
    而「不得不清理自动产出的垃圾」正是让人开始乱删结构的起点。
    """

    def _debate_with_a_challenged_claim(self) -> str:
        """造一个**真攒够信号**的库：一条命题被质询 3 次。

        信号不够时 `upper` 只会印「候选 0 条」—— 那样这些用例就测不到写路径。
        """
        d = self.debate()
        dr = self.submit(d, "甲", "现在的工作强度太大了，所以大家都不愿意往上爬。")
        self.ok("confirm", d, dr, "甲", stdin="y\ny\ny\n\n\n")
        # 三次质询走库层（CLI 没有 challenge 入口），直接用 python 补，
        # 但仍走**产品 API**（`debate.challenge`），不手写 SQL。
        code = (
            "import os, scaffold, debate\n"
            "from scaffold import get, add_relation\n"
            f"conn = scaffold.connect(os.environ['ARENA_DB'])\n"
            "claim = [r[0] for r in conn.execute("
            "\"SELECT id FROM artifact WHERE type='Claim'\")][0]\n"
            "for i in range(3):\n"
            "    c = scaffold.add_artifact(conn, type_='Counterargument',\n"
            "        content={'text': f'反驳{i}'}, origin='bob')\n"
            "    scaffold.activate(conn, c, by='bob')\n"
            "    add_relation(conn, kind='challenged_by', from_id=claim,\n"
            "                 to_id=c, origin='bob')\n"
            "conn.commit()\n"
        )
        env = {**os.environ, "ARENA_DB": self.db, "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return d

    def test_默认是只读的_不建任何东西(self):
        """不带动作 = 看。这是这条命令最要紧的约定。"""
        d = self._debate_with_a_challenged_claim()
        out = self.ok("upper", d)
        self.assertIn("还没有上层节点", out)
        # 再问一次仍然没有 —— 说明上一次真的什么都没写
        self.assertIn("还没有上层节点", self.ok("upper", d))

    def test_propose_是只读的_候选看得到但没建(self):
        d = self._debate_with_a_challenged_claim()
        out = self.ok("upper", d, "propose")
        self.assertIn("只读", out)
        self.assertIn("challenge_counts", out)
        self.assertIn("候选：1 条", out)
        # 提议之后库里**还是**没有节点 —— 提议和建是两件事
        self.assertIn("还没有上层节点", self.ok("upper", d))

    def test_promote_建出来的节点名字是空的(self):
        d = self._debate_with_a_challenged_claim()
        out = self.ok("upper", d, "promote", "bob")
        self.assertIn("ctx-0001", out)
        v = self.ok("upper", d)
        self.assertIn("ctx-0001", v)
        self.assertIn("（待命名）", v)
        # 视图必须明说这个名字是等谁来给 —— 「空」不能看起来像「有内容」
        self.assertIn("名字由人来给", v)

    def test_name_改完名之后依据还在(self):
        """⚠️ 改名**只改名字**，依据不许丢（实测踩过：`signal` 变 `None`）。"""
        d = self._debate_with_a_challenged_claim()
        self.ok("upper", d, "promote", "bob")
        out = self.ok("upper", d, "name", "ctx-0001", "甲", "围绕假设甲的分歧")
        self.assertIn("围绕假设甲的分歧", out)
        v = self.ok("upper", d)
        self.assertIn("围绕假设甲的分歧", v)
        self.assertIn("challenge_counts", v)      # 信号还在
        self.assertIn("carg-0001", v)             # 依据还在

    def test_空库上跑_说清楚是还没有还是算不出(self):
        """空结果**永远附一句话**（同 `hints.scan()` 的规矩）。"""
        d = self.debate()
        out = self.ok("upper", d, "propose")
        self.assertIn("候选：0 条", out)
        self.assertIn("「还没有」和「算不出来」不是一回事", out)

    def test_缺参数报的是用法(self):
        p = self.run_cli("upper")
        self.assertEqual(p.returncode, 1)
        self.assertIn("python cli.py upper", p.stderr)

    def test_不认识的动作报的是可选动作(self):
        d = self.debate()
        p = self.run_cli("upper", d, "wat")
        self.assertEqual(p.returncode, 1)
        self.assertIn("不认识的动作", p.stderr)


if __name__ == "__main__":
    unittest.main()
