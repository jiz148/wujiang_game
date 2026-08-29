// Screen routing and modal isolation: UI plumbing both domains need.
import { hasBattle, roomQueryId, syncLocation } from '../core/net.js';
import { render } from '../core/render.js';
import { SCREENS, routeTarget } from '../core/router.js';
import { state } from '../core/state.js';
import { refreshRecentMatches } from '../platform/home.js';
import { clearActionSelection } from '../tactical/session.js';
import { $ } from './dom.js';

export function keyboardHelpIsOpen() {
  return Boolean($("keyboard-help") && !$("keyboard-help").classList.contains("hidden"));
}

export function setModalIsolation(active) {
  document.body.classList.toggle("modal-open", active);
  const shell = document.querySelector(".shell");
  if (!shell) return;
  shell.toggleAttribute("inert", active);
  if (active) shell.setAttribute("aria-hidden", "true");
  else shell.removeAttribute("aria-hidden");
}

// 弹窗不止一个，谁也不该单独决定背景要不要失活：各自渲染完之后统一看一眼当前
// 还有没有开着的层。否则渲染顺序一变，后一个就会把前一个的隔离状态抹掉。
const MODAL_IDS = ["profile-modal", "keyboard-help", "room-setup-dialog", "hero-picker", "hero-detail"];

export function anyModalIsOpen() {
  return MODAL_IDS.some((id) => {
    const node = $(id);
    return Boolean(node) && !node.classList.contains("hidden");
  });
}

export function syncModalIsolation() {
  setModalIsolation(anyModalIsOpen());
}

export function setScreen(screen, { renderAfter = true } = {}) {
  if (screen !== "battle" && state.historicalMatchId && screen !== "menu") {
    state.historicalMatchId = "";
    state.room = null;
    state.battle = null;
    state.liveBattle = null;
    state.replayMode = false;
    state.replayStepIndex = 0;
    state.replayOmniscient = false;
    state.playerToken = "";
    state.screen = "draft";
    clearActionSelection();
    syncLocation("draft", "");
    refreshRecentMatches({renderAfter: false}).then(() => render());
    if (renderAfter) render();
    return;
  }
  let next;
  if (screen === "battle") next = hasBattle() ? "battle" : "draft";
  else if (SCREENS.includes(screen)) next = screen;
  else next = "draft";
  state.screen = next;
  clearActionSelection();
  syncLocation(next);
  if (renderAfter) render();
}

export function syncScreen({ preferBattle = false } = {}) {
  const requested = window.location.hash.replace("#", "");
  if (requested) {
    const target = routeTarget(requested);
    // 地址请求进战斗，但手上没有战斗时不能空落一屏。
    if (target.screen === "battle" && !hasBattle()) {
      state.screen = roomQueryId() ? "draft" : "menu";
      return;
    }
    state.screen = target.screen;
    if (target.flow) state.homeFlow = target.flow;
    return;
  }
  // 轮询每几秒就会走到这里。菜单和登录门是玩家主动停留的位置，没有明确的
  // 地址请求时不能把它们冲掉，否则玩家会被反复弹回大厅。
  if (state.screen === "menu" || state.screen === "gate") return;
  if (preferBattle && hasBattle()) {
    state.screen = "battle";
  } else if (roomQueryId()) {
    state.screen = "draft";
  } else {
    state.screen = "menu";
  }
  syncLocation(state.screen);
}
