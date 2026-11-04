# -*- coding: utf-8 -*-
"""判定逻辑回归 —— 三方对撞。

  A. web/scoring.js       （浏览器里即时出结果的那份，用本机 node 当测试运行器执行）
  B. app/scoring.py       （后端发信前重算的那份）
  C. 本文件里的 oracle    （独立按自然语言定义重算的第三份实现，不 import A/B）

分工按性质划开，不是偷懒：
  - 设计断言（typeKey / verdict）来自 绳师48号某期博客 的八分法，人可能在设计层搞错，
    所以由 golden-vectors.json 逐条人工钉死，A/B/C 都必须服从它。
  - 机械字段（borderlineDims / keyQuestionIds / confidence）是纯算术，
    人工推 9 次翻题只会出错、不会更有原则（第一版就在 V01/V04 上算错过），
    所以期望值由 oracle 现算，再要求 A 与 B 都跟它一致。
  - 最后穷举全部 2^9=512 种答案向量，要求 A 与 B 完全一致。
    两份独立实现 + 一个 oracle 同时对不上才算抓到 bug；只有一份实现时，
    错的表和对的实现会一起通过。

用法：
    python tools/test_scoring.py            # 全量：设计断言 + 机械字段 + 512 穷举 + 拒绝用例
    python tools/test_scoring.py --quick    # 只跑设计断言与拒绝用例（不启 node）
需要本机 node 来执行 A（本项目 Node v24 已存在；node 只当运行器，不装任何包）。
"""
from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import content, scoring  # noqa: E402  (B)

DIM_ORDER = ("intimacy", "passion", "commitment")
BITS_TO_TYPE = ("nonlove", "empty", "infatuation", "fatuous",
                "liking", "companionate", "romantic", "consummate")


# ── C. oracle：只按注释里的自然语言定义重算，故意不沿用 scoring.py 的函数 ──────────
def oracle_type_key(scores: dict[str, int], threshold: int) -> str:
    """「在场 = 该维度至少 threshold 题答是」；三维恰好各 1 分时不属八分法。"""
    if scores["intimacy"] == 1 and scores["passion"] == 1 and scores["commitment"] == 1:
        return "sprout"
    b = 0
    if scores["intimacy"] >= threshold:
        b |= 4
    if scores["passion"] >= threshold:
        b |= 2
    if scores["commitment"] >= threshold:
        b |= 1
    return BITS_TO_TYPE[b]


def oracle_verdict(key: str) -> str:
    """除 nonlove / liking 外全部是爱 —— 直接照博主 02:41 那句原话写，不查表。"""
    if key == "sprout":
        return "mixed"
    if key == "nonlove":
        return "none"
    if key == "liking":
        return "like"
    return "love"


def oracle_borderline(scores: dict[str, int], threshold: int) -> list[str]:
    """差一题就翻边：得分等于阈值、或等于阈值减一。"""
    return [d for d in DIM_ORDER if scores[d] in (threshold, threshold - 1)]


def oracle_keys(answers: dict[str, int], questions: list[dict[str, Any]],
                threshold: int) -> list[str]:
    """翻这题重算，结论桶变了就是关键题。逐题全试，不剪枝。sprout 返回空。"""
    def score_of(a: dict[str, int]) -> dict[str, int]:
        s = {d: 0 for d in DIM_ORDER}
        for q in questions:
            s[q["dim"]] += a[q["id"]]
        return s

    base_key = oracle_type_key(score_of(answers), threshold)
    if base_key == "sprout":
        return []
    base_v = oracle_verdict(base_key)
    hits = []
    for q in questions:
        alt = dict(answers)
        alt[q["id"]] ^= 1
        if oracle_verdict(oracle_type_key(score_of(alt), threshold)) != base_v:
            hits.append(q["id"])
    return hits


def oracle_eval(answers: dict[str, int], questions: list[dict[str, Any]],
                threshold: int) -> dict[str, Any]:
    s = {d: 0 for d in DIM_ORDER}
    for q in questions:
        s[q["dim"]] += answers[q["id"]]
    key = oracle_type_key(s, threshold)
    keys = oracle_keys(answers, questions, threshold)
    return {
        "scores": s,
        "total": sum(s.values()),
        "typeKey": key,
        "verdict": oracle_verdict(key),
        "borderlineDims": oracle_borderline(s, threshold),
        "keyQuestionIds": keys,
        "confidence": "low" if keys else "normal",
    }


# ── A. 用 node 跑 web/scoring.js ────────────────────────────────────────────────
_HARNESS = r"""
const fs = require("fs"), path = require("path"), vm = require("vm");
const root = process.env.QUIZ_ROOT;   // -e 模式下额外参数落在 argv[1]，语义易错，改用环境变量
if (!root) throw new Error("QUIZ_ROOT 未设置");
const sandbox = { console };
sandbox.window = sandbox;          // 让 window 与 globalThis 指向同一对象，贴近浏览器语义
vm.createContext(sandbox);
for (const rel of ["web/scoring.js", "web/data/questions.js"]) {
  const file = path.join(root, rel);
  vm.runInContext(fs.readFileSync(file, "utf8"), sandbox, { filename: file });
}
// scoring.js 与 questions.js 都挂在 window 上，必须从 sandbox.window 取，不是 sandbox 本身
const SCORING = sandbox.window.SCORING;
const QUIZ_DATA = sandbox.window.QUIZ_DATA;
if (!SCORING || !QUIZ_DATA) {
  throw new Error("scoring.js 或 questions.js 没有挂到 window：SCORING=" +
                  !!SCORING + " QUIZ_DATA=" + !!QUIZ_DATA);
}
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const out = input.map(function (item) {
  try {
    return { id: item.id, ok: true, result: SCORING.evaluate(item.answers, QUIZ_DATA) };
  } catch (e) {
    return { id: item.id, ok: false, error: String(e && e.message || e) };
  }
});
process.stdout.write(JSON.stringify(out));
"""


def run_js(vectors: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    node = shutil.which("node")
    if not node:
        print("  [跳过] 未找到 node，无法执行 web/scoring.js 侧的断言")
        return None
    proc = subprocess.run(
        [node, "-e", _HARNESS],
        input=json.dumps(vectors), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "QUIZ_ROOT": str(ROOT)},
    )
    if proc.returncode != 0:
        raise SystemExit(f"node harness 失败：\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout)


class Tally:
    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    def check(self, label: str, got: Any, want: Any) -> None:
        if got == want:
            self.passed += 1
        else:
            self.failures.append(f"{label}\n      got  {got!r}\n      want {want!r}")

    def report(self) -> int:
        total = self.passed + len(self.failures)
        print(f"\nPASS {self.passed} / {total}")
        if self.failures:
            print(f"FAIL {len(self.failures)}:")
            for f in self.failures[:40]:
                print("  ✗ " + f)
            if len(self.failures) > 40:
                print(f"  …另有 {len(self.failures) - 40} 条")
            return 1
        print("全部通过")
        return 0


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    quick = "--quick" in argv

    data = content.load_questions()
    questions = data["questions"]
    threshold = scoring.normalize_rules(data)[0]
    golden = json.loads((ROOT / "tests" / "golden-vectors.json").read_text(encoding="utf-8"))

    if len(questions) != 9 or threshold != 2:
        print(f"数据与测试假设不符：题数 {len(questions)}，阈值 {threshold}")
        return 1

    t = Tally()
    vectors = []

    # 1) 设计断言：人工钉死的 typeKey / verdict
    for case in golden["cases"]:
        ans = case["answers"]
        got_py = scoring.evaluate(ans, data)
        t.check(f'{case["id"]} typeKey(py) — {case["why"][:28]}',
                got_py["typeKey"], case["expect"]["typeKey"])
        t.check(f'{case["id"]} verdict(py)', got_py["verdict"], case["expect"]["verdict"])
        orc = oracle_eval(ans, questions, threshold)
        t.check(f'{case["id"]} typeKey(oracle)', orc["typeKey"], case["expect"]["typeKey"])
        t.check(f'{case["id"]} verdict(oracle)', orc["verdict"], case["expect"]["verdict"])
        vectors.append({"id": case["id"], "answers": ans})

    # 2) 机械字段：oracle 现算，B 必须与之一致
    for case in golden["cases"]:
        got_py = scoring.evaluate(case["answers"], data)
        orc = oracle_eval(case["answers"], questions, threshold)
        cid = case["id"]
        t.check(f"{cid} borderlineDims", got_py["borderlineDims"], orc["borderlineDims"])
        t.check(f"{cid} keyQuestionIds", got_py["keyQuestionIds"], orc["keyQuestionIds"])
        t.check(f"{cid} confidence", got_py["confidence"], orc["confidence"])

    # 3) 拒绝用例
    for name, bad in golden["reject"].items():
        if name == "_comment":
            continue
        try:
            scoring.evaluate(bad, data)
            t.check(f"reject {name}", "未抛错", "AnswerError")
        except scoring.AnswerError:
            t.check(f"reject {name}", "AnswerError", "AnswerError")
        except Exception as exc:  # noqa: BLE001
            t.check(f"reject {name} 异常类型", type(exc).__name__, "AnswerError")

    # 4) 多余字段要容忍
    tol = golden["tolerated"]
    got = scoring.evaluate(tol["extra_key"], data)
    t.check("tolerated typeKey", got["typeKey"], tol["expect"]["typeKey"])
    t.check("tolerated verdict", got["verdict"], tol["expect"]["verdict"])

    # 5) A 侧（node 跑 scoring.js）：设计断言 + 与 B 完全一致
    if not quick:
        js = run_js(vectors)
        if js:
            by_id = {r["id"]: r for r in js}
            for case in golden["cases"]:
                r = by_id.get(case["id"]) or {}
                cid = case["id"]
                if not r.get("ok"):
                    t.check(f"{cid} js 抛错", r.get("error"), None)
                    continue
                res = r["result"]
                t.check(f"{cid} typeKey(js)", res["typeKey"], case["expect"]["typeKey"])
                t.check(f"{cid} verdict(js)", res["verdict"], case["expect"]["verdict"])
                py = scoring.evaluate(case["answers"], data)
                for field in ("typeKey", "verdict", "borderlineDims",
                              "keyQuestionIds", "confidence", "scores", "total"):
                    t.check(f"{cid} js==py {field}", res[field], py[field])

    # 6) 穷举 2^9=512：A、B、oracle 三方必须完全一致
    if not quick:
        ids = [q["id"] for q in questions]
        all_vectors = []
        for bits in itertools.product((0, 1), repeat=len(ids)):
            ans = dict(zip(ids, bits))
            all_vectors.append({"id": "".join(map(str, bits)), "answers": ans})
        js_all = run_js(all_vectors) or []
        # 数量必须是 512：若 node 侧返回空列表，下面的 zip 会零次迭代、mism 停在 0，
        # 于是「穷举一致」会假绿。这一步把假绿堵死。
        t.check("穷举向量数量", len(js_all), 512)
        if len(js_all) != 512:
            return t.report()
        mism = 0
        for v, jr in zip(all_vectors, js_all):
            py = scoring.evaluate(v["answers"], data)
            orc = oracle_eval(v["answers"], questions, threshold)
            fields_ok = (
                jr.get("ok") and
                jr["result"]["typeKey"] == py["typeKey"] and
                jr["result"]["verdict"] == py["verdict"] and
                jr["result"]["borderlineDims"] == py["borderlineDims"] and
                jr["result"]["keyQuestionIds"] == py["keyQuestionIds"] and
                jr["result"]["confidence"] == py["confidence"] and
                orc["typeKey"] == py["typeKey"] and
                orc["verdict"] == py["verdict"] and
                orc["borderlineDims"] == py["borderlineDims"] and
                orc["keyQuestionIds"] == py["keyQuestionIds"]
            )
            if not fields_ok:
                mism += 1
                if mism <= 5:
                    t.check(f"穷举 {v['id']}", "见上", "三方一致")
        t.check("穷举 512 向量三方一致", mism, 0)

    return t.report()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
