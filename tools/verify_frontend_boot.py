"""Boot the app in a real browser and assert each entry screen actually renders.

The other two frontend checks are static: verify_frontend_modules.mjs proves the
import graph links, verify_frontend_globals.py proves no identifier is missing.
Neither can see a runtime DOM error -- assigning to a read-only property, say --
and refreshState() used to swallow whatever render() threw, so such a failure
reached the player as a silent blank screen.

Screens are checked by driving Chrome over CDP rather than with --dump-dom,
because this app decides which screen to show only after awaiting /api/auth/me
and /api/heroes; any snapshot flag fires before that and always lands on the
login gate. See tools/cdp.py for why the virtual-time flags do not help.

It walks the whole entry path -- gate, menu, campaign list, live campaign,
skirmish, battle -- because each screen is reached only through the one before
it, and a screen that no check opens is a screen that can break unnoticed. The
live campaign is its own step: the list that leads to it shares almost no code
with it, so a crash in the map left the list looking fine and every check
passing while the screen behind the button rendered nothing.

It also asserts the address bar names the destination. The draft screen hosts
four unrelated flows, so a hash of `#draft` says nothing about where the player
is; writing it meant the next visit skipped the menu and dropped into whichever
flow happened to be default.

Usage:
    python tools/verify_frontend_boot.py [--screenshot-gate a.png] [--screenshot-menu b.png]
                                         [--screenshot-campaign c.png] [--screenshot-skirmish d.png]
                                         [--screenshot-battle e.png]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from cdp import CdpError, connect  # noqa: E402

ROOT = ROOT.parent

CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]

AUTH_TOKEN_KEY = "wujiang-auth-token"
PROFILE_NAME_KEY = "wujiang-profile-name"
PROFILE_READY_KEY = "wujiang-profile-ready"


def find_browser() -> Path | None:
    return next((path for path in CHROME_CANDIDATES if path.exists()), None)


def free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.closing(socket.socket()) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def register_account(port: int) -> tuple[str, str]:
    """Create a throwaway account so the post-login screens can be reached."""
    name = f"probe{int(time.time() * 1000) % 10_000_000}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/auth/register",
        data=json.dumps({"username": name, "password": "probe-password-1"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    return payload["session_token"], name


def boot_failure(page) -> str:
    return page.evaluate(
        "(document.querySelector('.boot-error__trace') || {}).textContent || ''"
    ) or ""


def describe(page) -> str:
    return page.evaluate("document.body.className") or "(无 class)"


def fail(page, message: str) -> int:
    """Report a screen that never appeared, with whatever the page can still tell us."""
    print(f"{message}")
    print(f"  body: {describe(page)}")
    trace = boot_failure(page)
    if trace:
        print("\n未捕获异常：")
        for line in trace.strip().splitlines():
            print(f"  {line}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot-gate", default="")
    parser.add_argument("--screenshot-menu", default="")
    parser.add_argument("--screenshot-campaign", default="")
    parser.add_argument("--screenshot-skirmish", default="")
    parser.add_argument("--screenshot-lobby", default="")
    parser.add_argument("--screenshot-room-setup", default="")
    parser.add_argument("--screenshot-hero-picker", default="")
    parser.add_argument("--screenshot-battle", default="")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args()

    browser_path = find_browser()
    if browser_path is None:
        print("SKIP: 未找到 Chrome 或 Edge")
        return 0

    app_port = free_port()
    debug_port = free_port()
    profile = Path(tempfile.mkdtemp(prefix="wujiang-boot-"))

    server = subprocess.Popen(
        [sys.executable, "run.py", "--port", str(app_port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    chrome = subprocess.Popen(
        [
            str(browser_path),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            f"--window-size={args.width},{args.height}",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={debug_port}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    page = None
    try:
        if not wait_for_server_ready(app_port):
            print("FAIL: 服务未能启动")
            return 1

        page = connect(debug_port)
        url = f"http://127.0.0.1:{app_port}/"

        # --- 未登录：必须落在登录门 ---
        page.navigate(url)
        if not page.wait_for("!!document.querySelector('.gate-card')", timeout=25):
            return fail(page, "BOOT FAILED: 登录门没有渲染")

        problems = check(page, {
            "#gate-username": "用户名输入框",
            "#gate-login": "登录按钮",
        })
        if page.evaluate("document.body.innerHTML.includes('Online Tactical Duel Prototype')"):
            problems.append("旧的英文副标题仍然存在")
        if problems:
            print("BOOT FAILED: " + "；".join(problems))
            return 1

        if args.screenshot_gate:
            page.screenshot(resolve(args.screenshot_gate))
        print("BOOT OK: 登录门在真实浏览器中完成渲染")

        # --- 已登录：必须落在主菜单 ---
        token, name = register_account(app_port)
        page.evaluate(
            f"localStorage.setItem({json.dumps(AUTH_TOKEN_KEY)}, {json.dumps(token)});"
            f"localStorage.setItem({json.dumps(PROFILE_NAME_KEY)}, {json.dumps(name)});"
            f"localStorage.setItem({json.dumps(PROFILE_READY_KEY)}, '1');"
            f"sessionStorage.setItem({json.dumps(PROFILE_NAME_KEY)}, {json.dumps(name)});"
            f"sessionStorage.setItem({json.dumps(PROFILE_READY_KEY)}, '1');"
        )
        page.navigate(url)
        if not page.wait_for("!!document.querySelector('.menu-layout')", timeout=25):
            return fail(page, "MENU FAILED: 登录后没有落在主菜单")

        problems = check(page, {
            ".menu-entry": "主菜单条目",
        })
        text = page.evaluate("document.querySelector('.menu-layout').textContent") or ""
        for label in ("战役", "遭遇战", "战绩与回放"):
            if label not in text:
                problems.append(f"主菜单缺少「{label}」")
        for label in ("继续战役", "新手教学"):
            if label in text:
                problems.append(f"主菜单仍保留「{label}」")
        # 主菜单不该在地址栏留下内层容器名，否则重开就跳过菜单直奔内层。
        if page.evaluate("location.hash") not in ("", "#menu"):
            problems.append(f"主菜单把地址写成了 {page.evaluate('location.hash')}")
        if problems:
            print("MENU FAILED: " + "；".join(problems))
            return 1

        if args.screenshot_menu:
            page.screenshot(resolve(args.screenshot_menu))
        print("MENU OK: 登录后落在主菜单")

        # --- 从菜单进入战役：验证屏幕切换真的把玩家送到了游戏里 ---
        page.evaluate(
            "[...document.querySelectorAll('.menu-entry')]"
            ".find((node) => node.querySelector('.menu-entry__title').textContent === '战役').click()"
        )
        entered = page.wait_for(
            "document.body.classList.contains('screen-draft')"
            " && !document.getElementById('strategy-panel').classList.contains('hidden')",
            timeout=20,
        )
        if not entered:
            return fail(page, "NAV FAILED: 从主菜单进不去战役")
        if page.evaluate("location.hash") != "#campaign":
            return fail(page, f"NAV FAILED: 战役的地址写成了 {page.evaluate('location.hash')}")
        # 左上角写的是"你在哪"。战役和遭遇战共用一个外壳，此前两者顶着同一个「武将」。
        if page.evaluate("document.getElementById('brand-title').textContent") != "战役":
            return fail(page, "NAV FAILED: 战役路由的左上角标题不是「战役」")

        print("NAV OK: 主菜单可以进入战役，地址栏与左上角标题都写的是战役")

        # --- 新建战役：三步向导，走完落到开局准备屏 ---
        def click_text(selector: str, label: str) -> bool:
            return bool(page.evaluate(
                "(() => {"
                f" const node = [...document.querySelectorAll({json.dumps(selector)})]"
                f"   .find((item) => (item.textContent || '').trim() === {json.dumps(label)});"
                " if (node) { node.click(); return true; }"
                " return false; })()"
            ))

        page.evaluate("document.getElementById('strategy-new-campaign').click()")
        if not page.wait_for("!!document.querySelector('.strategy-wizard')", timeout=20):
            return fail(page, "CAMPAIGN FAILED: 打不开新建战役流程")
        for step in ("下一步", "下一步", "创建战役"):
            if not click_text(".strategy-wizard__footer button", step):
                return fail(page, f"CAMPAIGN FAILED: 新建战役流程里找不到「{step}」")
            page.wait_for("true", timeout=2)

        # --- 开局准备：出身抉择独立成屏，选定之后房主才锁定开战 ---
        if not page.wait_for("!!document.querySelector('.campaign-prep')", timeout=40):
            return fail(page, "CAMPAIGN FAILED: 创建后没有进入开局准备屏")
        if page.evaluate("!!document.querySelector('.campaign-prep .strategy-map')"):
            return fail(page, "CAMPAIGN FAILED: 开局准备屏上不该出现地图")
        if not click_text(".campaign-prep__footer button", "锁定并开始战役"):
            return fail(page, "CAMPAIGN FAILED: 开局准备屏上没有开始战役的入口")

        # --- 战役屏：地图渲染曾整个崩掉（给 SVG 的 className 赋值），列表照常显示，
        # 于是每一步检查都放行，而点下去只得到一屏空白。 ---
        if not page.wait_for(
            "!!document.querySelector('.campaign-screen .strategy-map-canvas')",
            timeout=40,
        ):
            return fail(page, "CAMPAIGN FAILED: 开了战役却渲染不出地图")
        problems = check(page, {
            ".campaign-hud": "顶部状态栏",
            ".campaign-stage": "地图舞台",
            ".strategy-map-viewport": "可拖拽的地图视口",
            ".campaign-dock__tabs": "浮层操作面板的页签",
        })
        # 地图是主对象，它得真的占满舞台，而不是缩在角落里的一小块。
        ratio = page.evaluate(
            "(() => {"
            " const stage = document.querySelector('.campaign-stage');"
            " const view = document.querySelector('.strategy-map-viewport');"
            " if (!stage || !view) return 0;"
            " const a = stage.getBoundingClientRect();"
            " const b = view.getBoundingClientRect();"
            " return (b.width * b.height) / Math.max(1, a.width * a.height); })()"
        )
        if isinstance(ratio, (int, float)) and ratio < 0.95:
            problems.append(f"地图只占了舞台的 {int(ratio * 100)}%（应当铺满）")
        # 战役屏要占满一屏：不能让页面滚起来把顶栏推走。
        overflow = page.evaluate(
            "document.documentElement.scrollHeight - window.innerHeight"
        )
        if isinstance(overflow, (int, float)) and overflow > 4:
            problems.append(f"战役屏超出视口 {int(overflow)}px（应当在面板内滚动）")
        if problems:
            print("CAMPAIGN FAILED: " + "；".join(problems))
            return 1

        if args.screenshot_campaign:
            page.screenshot(resolve(args.screenshot_campaign))
        print("CAMPAIGN OK: 开局准备与整屏地图分开，浮层面板齐备，且不撑破视口")

        # --- 浮层面板：一页只回答一个问题 ---
        # 页签名单是这条规则唯一能被看见的地方；城市页混进动作、武将页一次摊开
        # 所有履历，都是从这里开始失控的。
        def open_dock_tab(label: str) -> bool:
            return bool(page.evaluate(
                "(() => {"
                f" const tab = [...document.querySelectorAll('.campaign-dock__tab')]"
                f"   .find((node) => (node.textContent || '').trim().startsWith({json.dumps(label)}));"
                " if (!tab) return false;"
                " if (!tab.classList.contains('is-active')) tab.click();"
                " return true; })()"
            ))

        problems = []
        tab_labels = page.evaluate(
            "[...document.querySelectorAll('.campaign-dock__tab')]"
            ".map((node) => (node.textContent || '').trim().replace(/\\s+\\d+$/, ''))"
        )
        for label in ("城市", "武将", "军令", "科技"):
            if label not in (tab_labels or []):
                problems.append(f"面板里没有「{label}」页")
        if open_dock_tab("城市"):
            page.wait_for("true", timeout=2)
            if page.evaluate("!!document.querySelector('.campaign-dock__page .strategy-command-stack')"):
                problems.append("城市页仍然挂着军令动作")
            if not page.evaluate("!!document.querySelector('.campaign-dock__page .strategy-city-detail-card')"):
                problems.append("城市页没有城市详情")
        else:
            problems.append("翻不到城市页")
        if open_dock_tab("武将"):
            page.wait_for("true", timeout=2)
            if page.evaluate("!!document.querySelector('.campaign-dock__page .strategy-hero-detail')"):
                problems.append("武将页没点人就摊开了详情")
            page.evaluate("document.querySelector('.campaign-dock__page .hero-slot')?.click()")
            page.wait_for("true", timeout=2)
            if not page.evaluate("!!document.querySelector('.campaign-dock__page .strategy-hero-detail')"):
                problems.append("点了武将却没有展开详情")
        else:
            problems.append("翻不到武将页")
        if problems:
            print("DOCK FAILED: " + "；".join(problems))
            return 1
        print("DOCK OK: 城市页只讲城，武将详情点开才出现，科技单独成页")

        # --- 遭遇战：不经营大地图直接开战的入口，与战役并列而不是它的子集 ---
        page.navigate(url + "#skirmish")
        if not page.wait_for(
            "document.body.classList.contains('screen-draft')"
            " && !document.getElementById('skirmish-panel').classList.contains('hidden')",
            timeout=25,
        ):
            return fail(page, "NAV FAILED: 直接访问 #skirmish 到不了遭遇战")
        if page.evaluate("document.getElementById('brand-title').textContent") != "战场对战":
            return fail(page, "NAV FAILED: 遭遇战路由的左上角标题不是「战场对战」")
        problems = check(page, {
            "#create-room": "建立房间按钮",
            "#join-room-code": "房间码输入框",
        })
        if page.evaluate("!!document.getElementById('start-quick-ai')"):
            problems.append("立即开战入口还在")
        if page.evaluate("document.documentElement.scrollWidth - window.innerWidth > 4"):
            problems.append("遭遇战页出现横向滚动")
        if problems:
            print("NAV FAILED: " + "；".join(problems))
            return 1
        if args.screenshot_skirmish:
            page.screenshot(resolve(args.screenshot_skirmish))
        print("NAV OK: 遭遇战可直达，建立与加入入口齐备")

        # --- 建房页：整页只回答"谁坐在哪、带了谁" ---
        # 设置与选将都收进了弹窗，所以它们各自是一段没有任何静态检查看得见的
        # 代码：席位卡渲染得再对，点开的那一层也可能是空的。
        page.evaluate("document.getElementById('create-room').click()")
        if not page.wait_for(
            "!document.getElementById('room-lobby').classList.contains('hidden')"
            " && document.querySelectorAll('#seat-cards .seat-card').length > 0",
            timeout=25,
        ):
            return fail(page, "LOBBY FAILED: 建房后没有渲染出席位")

        problems = []
        if not page.evaluate("!!document.getElementById('auto-configure-room')"):
            problems.append("建房页没有自动配置")
        if page.evaluate("document.getElementById('open-room-setup')?.textContent.trim()") != "修改设置":
            problems.append("修改按钮文案不是「修改设置」")
        # 模式和席位数是只读的字：整页除了席位卡自己的队伍/状态下拉之外，
        # 不该再有别的输入控件，改动只能从"修改设置"进弹窗。
        if page.evaluate(
            "[...document.querySelectorAll('#room-lobby select, #room-lobby input')]"
            ".some((node) => !node.closest('.seat-controls'))"
        ):
            problems.append("建房页上仍然摆着可直接改的配置控件")
        page.evaluate("document.getElementById('open-room-setup').click()")
        if not page.wait_for(
            "!document.getElementById('room-setup-dialog').classList.contains('hidden')",
            timeout=10,
        ):
            problems.append("打不开房间设置弹窗")
        elif not page.evaluate("document.querySelectorAll('#room-mode-select option').length > 0"):
            problems.append("房间设置弹窗里没有模式可选")
        elif not page.evaluate("!!document.getElementById('room-hero-limit-enabled')"):
            problems.append("房间设置弹窗里没有限定武将数量开关")
        elif not page.evaluate("!!document.getElementById('room-board-width-input') && !!document.getElementById('room-board-height-input')"):
            problems.append("房间设置弹窗里没有战场大小")
        elif args.screenshot_room_setup:
            page.screenshot(resolve(args.screenshot_room_setup))
        page.evaluate("document.getElementById('room-setup-cancel').click()")

        # 选将：每一行是名字、等级标签和攻守速范魔，加进来是一枚标签，点标签才看详情。
        page.evaluate("document.querySelector('#seat-cards .seat-hero-add').click()")
        if not page.wait_for(
            "document.querySelectorAll('#hero-picker-list .hero-row').length > 0",
            timeout=15,
        ):
            problems.append("打不开选将弹窗，或名单是空的")
        else:
            if page.evaluate("!!document.querySelector('#hero-picker-list .hero-row__counter ~ *')"):
                problems.append("选将名单一行里塞了计数器之外的东西")
            if not page.evaluate("!!document.querySelector('#hero-picker-list .hero-level-tag')"):
                problems.append("选将名单上等级不是标签")
            if not page.evaluate("!!document.querySelector('#hero-picker-list .hero-row__stats')"):
                problems.append("选将名单上看不到攻守速范魔")
            page.evaluate(
                "document.querySelectorAll('#hero-picker-list .hero-row__step')[1].click()"
            )
            if not page.wait_for(
                "document.querySelectorAll('#seat-cards .hero-tag').length > 0",
                timeout=15,
            ):
                reason = page.evaluate("document.getElementById('lobby-caption').textContent") or ""
                problems.append(f"加了武将，席位上却没有出现标签（{reason or '无提示'}）")
            if args.screenshot_hero_picker:
                page.screenshot(resolve(args.screenshot_hero_picker))
            page.evaluate("document.getElementById('hero-picker-close').click()")

        if not problems:
            page.evaluate("document.querySelector('#seat-cards .hero-tag__name').click()")
            if not page.wait_for(
                "!document.getElementById('hero-detail').classList.contains('hidden')"
                " && document.querySelectorAll('#hero-detail-body .hero-detail__row').length > 0",
                timeout=10,
            ):
                problems.append("点武将标签看不到详情")
            elif page.evaluate(
                "document.getElementById('hero-detail-level').classList.contains('hidden')"
                " || !document.getElementById('hero-detail-level').textContent.includes('Lv')"
            ):
                problems.append("武将详情里等级不是标签")
            page.evaluate("document.getElementById('hero-detail-close').click()")

        if problems:
            print("LOBBY FAILED: " + "；".join(problems))
            return 1
        if args.screenshot_lobby:
            page.screenshot(resolve(args.screenshot_lobby))
        print("LOBBY OK: 配置与选将都在弹窗里，席位用标签列出阵容，标签点开是详情")

        # --- 战场：最重的一屏，也是唯一会被上面三步全部放过的一屏 ---
        # 删掉大标题时 renderHeader() 仍在写已不存在的 #topbar-subline，
        # 前三步照样通过，而每一条进入战斗的路径都被这个异常挡死了。
        page.navigate(url)
        if not page.wait_for("!!document.querySelector('.menu-layout')", timeout=25):
            return fail(page, "BATTLE FAILED: 回不到主菜单")
        page.evaluate(
            "[...document.querySelectorAll('.menu-entry')]"
            ".find((node) => node.querySelector('.menu-entry__title').textContent === '遭遇战').click()"
        )
        if not page.wait_for(
            "!document.getElementById('skirmish-panel').classList.contains('hidden')",
            timeout=20,
        ):
            return fail(page, "BATTLE FAILED: 打不开遭遇战入口")
        page.evaluate("document.getElementById('create-room').click()")
        if not page.wait_for(
            "!document.getElementById('room-lobby').classList.contains('hidden')"
            " && !!document.getElementById('auto-configure-room')",
            timeout=25,
        ):
            return fail(page, "BATTLE FAILED: 建房后进不了房间大厅")
        page.evaluate("document.getElementById('auto-configure-room').click()")
        if not page.wait_for(
            "!document.getElementById('start-room').disabled",
            timeout=25,
        ):
            return fail(page, "BATTLE FAILED: 自动配置后仍不能开战")
        page.evaluate("document.getElementById('start-room').click()")
        if not page.wait_for(
            "document.body.classList.contains('screen-battle')"
            " && document.querySelectorAll('#board .cell').length > 0",
            timeout=40,
        ):
            return fail(page, "BATTLE FAILED: 进不去战场，或棋盘没有渲染")

        problems = []
        if not page.evaluate("!!document.getElementById('board-world')"):
            problems.append("棋盘没有镜头层")
        if not page.evaluate("!!document.getElementById('battle-chain-bar')"):
            problems.append("顶栏没有连锁状态")
        if page.evaluate("!!document.querySelector('#board-alert.is-chain')"):
            problems.append("棋盘左侧仍在弹出连锁面板")
        overflow = page.evaluate(
            "document.documentElement.scrollHeight - window.innerHeight"
        )
        if isinstance(overflow, (int, float)) and overflow > 4:
            problems.append(f"战场页超出视口 {int(overflow)}px")
        hscroll = page.evaluate(
            "document.documentElement.scrollWidth - window.innerWidth"
        )
        if isinstance(hscroll, (int, float)) and hscroll > 4:
            problems.append(f"战场页出现横向滚动 {int(hscroll)}px")
        if problems:
            print("BATTLE FAILED: " + "；".join(problems))
            return 1

        if args.screenshot_battle:
            page.screenshot(resolve(args.screenshot_battle))
        print("BATTLE OK: 可以从主菜单一路打到战场")
        return 0
    except CdpError as error:
        print(f"FAIL: {error}")
        return 1
    finally:
        if page is not None:
            page.close()
        chrome.terminate()
        server.terminate()
        for process in (chrome, server):
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)


def wait_for_server_ready(port: int) -> bool:
    return wait_for_port(port)


def resolve(value: str) -> Path:
    target = Path(value)
    return target if target.is_absolute() else ROOT / target


def check(page, selectors: dict[str, str]) -> list[str]:
    missing = []
    for selector, label in selectors.items():
        if not page.evaluate(f"!!document.querySelector({json.dumps(selector)})"):
            missing.append(f"缺少{label}")
    return missing


if __name__ == "__main__":
    sys.exit(main())
