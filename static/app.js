// Application entry point: wires the modules together and boots the UI.
import { bindEvents, ensureDynamicUiScaffolding } from './core/events.js';
import { recordProductEvent, roomQueryId } from './core/net.js';
import { refreshState } from './core/render.js';
import { state, ui } from './core/state.js';
import { initializeProfileState } from './platform/auth.js';
import { initializeAuthState, refreshAuthSession, refreshRecentMatches } from './platform/home.js';
import { hydrateStaticLabels, syncIdentityFromUrl } from './tactical/session.js';

// 界面整个由 JS 渲染，因此一次未捕获的异常就等于一块空白或一个卡住不动的按钮，
// 玩家无从判断是网络问题还是程序缺陷。render() 又几乎被所有交互调用，它抛出的
// 错误往往在某个 async 回调里变成静默的 promise rejection——控制台干净，界面已死。
// 所以这里把三条路径（启动、同步异常、未处理的 rejection）都收进同一个横幅。
let failureReported = false;

function reportFailure(error) {
  if (failureReported) return;
  failureReported = true;
  // 启动阶段出错时外壳还是隐藏的，不揭开就只剩一片空白。
  document.body.classList.remove("is-booting");
  const detail = error?.stack || error?.message || String(error);
  const host = document.getElementById("main-content") || document.body;
  const banner = document.createElement("div");
  banner.className = "boot-error";
  banner.setAttribute("role", "alert");

  const title = document.createElement("strong");
  title.textContent = "界面出错";
  const note = document.createElement("p");
  note.textContent = "这是程序缺陷，不是网络问题。请把下面的信息反馈给我们。";
  const trace = document.createElement("pre");
  trace.className = "boot-error__trace";
  trace.textContent = detail;

  banner.append(title, note, trace);
  host.prepend(banner);
}

window.addEventListener("error", (event) => reportFailure(event.error || event.message));
window.addEventListener("unhandledrejection", (event) => reportFailure(event.reason));

window.addEventListener("DOMContentLoaded", async () => {
  try {
    hydrateStaticLabels();
    initializeAuthState();
    initializeProfileState();
    await refreshAuthSession();
    recordProductEvent("home_view", {
      entry_state: state.authUser ? "logged_in" : "anonymous",
    });
    syncIdentityFromUrl();
    ensureDynamicUiScaffolding();
    globalThis.WujiangBattleFeedback?.initialize();
    bindEvents();
    await refreshRecentMatches({renderAfter: false});
    await refreshState({ preserveScreen: false });
  } catch (error) {
    reportFailure(error);
    return;
  }

  // 这不是 WebSocket。大厅和对局里别人的变化靠短轮询拉 /api/rooms/state。
  // 定时器本身 400ms 响一次，真正发请求会再按场景拉开：大厅 1.5s、对局 0.8s、
  // 切到后台 5s。上一次还没回来就不发下一次，断线会逐步退避，避免把页面堵死。
  ui.pollHandle = window.setInterval(() => {
    if (ui.refreshInFlight) return;
    const now = Date.now();
    if (!roomQueryId()) {
      if (now < ui.nextHomePollAt) return;
      ui.nextHomePollAt = now + 5000;
      refreshState({ preserveScreen: false });
      return;
    }
    const delay = document.hidden
      ? 5000
      : (ui.pollBackoffMs || (state.room?.status === "battle" ? 800 : 1500));
    if (now < ui.nextRoomPollAt) return;
    ui.nextRoomPollAt = now + delay;
    refreshState();
  }, 400);

  document.addEventListener("visibilitychange", () => {
    ui.nextRoomPollAt = document.hidden ? Date.now() + 5000 : 0;
  });
});
