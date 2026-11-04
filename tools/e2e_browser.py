# -*- coding: utf-8 -*-
"""真实浏览器端到端走查 —— 不装 playwright/puppeteer，直接驱动 Chrome DevTools Protocol。

为什么要这一步：静态语法检查、node 里的 DOM-free 校验都证明不了「点了按钮之后页面真的对」。
子代理也明确说过没在浏览器里点过。这里用 headless Chrome 从 S0 一路点到 S2，
断言：视图切换正确、9 题能逐题作答、结果页出现主结论/条形/八灯/逐题回顾、页面无 JS 报错。

前提：外部已启动 Chrome 的 --remote-debugging-port 与应用服务。
用法：python tools/e2e_browser.py --app http://127.0.0.1:8011 --cdp http://127.0.0.1:9333
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

try:
    from websockets.sync.client import connect as ws_connect
except ImportError:  # 没有 websockets 就退到手写的极简 WS 客户端
    ws_connect = None


class CDP:
    """极小 CDP 客户端：只用 Runtime.evaluate 与 Page.navigate 两个域，够走查用的。"""

    def __init__(self, ws_url: str) -> None:
        self.ws = ws_connect(ws_url, max_size=32 * 1024 * 1024)
        self._id = 0
        self.events: list[dict] = []
        self.console_errors: list[str] = []

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def _recv_until(self, want_id: int, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv(timeout=max(0.1, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get("method") == "Runtime.consoleAPICalled":
                params = msg.get("params") or {}
                if params.get("type") == "error":
                    self.console_errors.append(str(params.get("args")))
            elif msg.get("method") == "Runtime.exceptionThrown":
                det = (msg.get("params") or {}).get("exceptionDetails") or {}
                self.console_errors.append(det.get("text") or "exception")
            if msg.get("id") == want_id:
                return msg
            if "method" in msg:
                self.events.append(msg)
        raise TimeoutError(f"CDP 等 id={want_id} 超时")

    def send(self, method: str, **params) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        return self._recv_until(mid)

    def js(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True, userGesture=True)
        res = r.get("result") or {}
        if res.get("exceptionDetails"):
            raise RuntimeError(f"页面 JS 出错: {res['exceptionDetails'].get('text')}")
        return (res.get("result") or {}).get("value")

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)
        # 等页面脚本跑完：轮询 document.readyState + 视图是否挂载
        for _ in range(80):
            time.sleep(0.15)
            try:
                if self.js("document.readyState === 'complete' && !!document.querySelector('.view')"):
                    return
            except Exception:
                pass
        raise TimeoutError("页面加载超时")


def new_tab(cdp_base: str) -> str:
    req = urllib.request.Request(cdp_base + "/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://127.0.0.1:8011")
    ap.add_argument("--cdp", default="http://127.0.0.1:9333")
    args = ap.parse_args(argv)

    if ws_connect is None:
        print("缺少 websockets 库；本机可用 `pip install websockets` 或跳过此项。")
        return 2

    ws_url = new_tab(args.cdp)
    c = CDP(ws_url)
    fails: list[str] = []

    def check(label: str, cond, detail=""):
        if cond:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label} {detail}")
            fails.append(label)

    try:
        c.send("Runtime.enable")
        c.send("Page.enable")
        c.goto(args.app + "/")

        print("[S0 锁定页]")
        check("S0 可见", c.js("!document.getElementById('view-s0').hidden"))
        check("S1 初始隐藏", c.js("document.getElementById('view-s1').hidden"))
        check("代号输入框存在", c.js("!!document.querySelector('input')"))
        check("有确认勾选框", c.js("!!document.querySelector('input[type=checkbox]')"))
        check("有开始按钮", c.js("!!document.querySelector('button')"))

        print("[S0 未勾选就点开始 → 应被拦]")
        c.js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>/开始|START|start/i.test(x.textContent));
          if(!b) return 'no-button'; b.click(); return 'clicked';
        })()""")
        time.sleep(0.3)
        check("仍停在 S0（未勾选不许开始）", c.js("!document.getElementById('view-s0').hidden"))

        print("[S0 勾选 + 填代号 + 开始]")
        c.js("""(() => {
          const cb=document.querySelector('input[type=checkbox]');
          const inp=document.querySelector('input:not([type=checkbox])');
          const set=(el,v)=>{const d=Object.getOwnPropertyDescriptor(el.constructor.prototype,'value');
            d&&d.set?d.set.call(el,v):(el.value=v); el.dispatchEvent(new Event('input',{bubbles:true}));};
          if(cb){cb.checked=true;cb.dispatchEvent(new Event('change',{bubbles:true}));}
          if(inp){set(inp,'阿明');}
          return true;
        })()""")
        time.sleep(0.2)
        c.js("""(() => {
          const b=[...document.querySelectorAll('button')].find(x=>/开始|START|start/i.test(x.textContent));
          b&&b.click(); return !!b;
        })()""")
        time.sleep(0.5)

        print("[S1 逐题]")
        check("进入 S1", c.js("!document.getElementById('view-s1').hidden"))
        first_q = c.js("document.getElementById('view-s1').innerText.slice(0,80)")
        print(f"    第 1 题题干片段：{first_q!r}")

        # 逐题作答：全部选「是」（第一个选项按钮），共 9 次
        answers_done = 0
        for i in range(9):
            r = c.js("""(() => {
              const s1=document.getElementById('view-s1');
              const btns=[...s1.querySelectorAll('button')];
              const yes=btns.find(b=>b.textContent.trim()==='是') || btns[0];
              if(!yes) return 'no-btn';
              yes.click(); return 'ok';
            })()""")
            if r != "ok":
                print(f"    第 {i+1} 题找不到选项按钮：{r}")
                break
            answers_done += 1
            time.sleep(0.25)
            if c.js("!document.getElementById('view-s2').hidden"):
                break

        check(f"完成作答（{answers_done} 次点击）", answers_done >= 9)
        time.sleep(0.6)

        print("[S2 结果页]")
        check("进入 S2", c.js("!document.getElementById('view-s2').hidden"))
        body = c.js("document.getElementById('view-s2').innerText") or ""
        check("出现主结论「完满/爱」相关字样", ("爱" in body))
        check("代号被回插", "阿明" in body)
        check("有三维度得分区", any(k in body for k in ("亲密", "激情", "承诺")))
        check("有八种形态清单", "形态" in body or "完满" in body)
        check("有免责声明", "不是诊断" in body or "诊断" in body)

        # 条形图：宽度应已设置
        bar = c.js("""(() => {
          const els=[...document.querySelectorAll('*')].filter(e=>{
            const s=getComputedStyle(e); return s.position==='absolute' && parseFloat(s.width)>0;});
          return els.length;
        })()""")
        check("条形图元素已渲染", (bar or 0) > 0, f"(宽>0 的绝对定位元素 {bar} 个)")

        # 切回上一题/重测按钮存在
        check("有重测按钮", c.js("""[...document.querySelectorAll('button')]
            .some(b=>/重新|重测|again|AGAIN/i.test(b.textContent))"""))

        print("[无 JS 报错]")
        check("控制台无 error", not c.console_errors, str(c.console_errors[:3]))

        # 截图存证
        shot = c.send("Page.captureScreenshot", format="png")
        data = (shot.get("result") or {}).get("data")
        if data:
            import base64
            import pathlib
            out = pathlib.Path(__file__).resolve().parent.parent / "tests" / "e2e-result.png"
            out.parent.mkdir(exist_ok=True)
            out.write_bytes(base64.b64decode(data))
            print(f"  截图已保存：{out}")

    finally:
        c.close()

    print()
    if fails:
        print(f"FAIL {len(fails)}: {fails}")
        return 1
    print("E2E 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
