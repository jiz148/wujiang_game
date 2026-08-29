// Sign-in, registration and the local profile.
import { $ } from '../core/dom.js';
import { render } from '../core/render.js';
import { PROFILE_NAME_KEY, PROFILE_READY_KEY, state, ui } from '../core/state.js';

export function normalizeProfileName(name) {
  return String(name || "").trim().replace(/\s+/g, " ").slice(0, 20);
}

export function effectiveProfileName() {
  return normalizeProfileName(state.profileName) || "未命名玩家";
}

export function initializeProfileState() {
  state.profileName = sessionStorage.getItem(PROFILE_NAME_KEY) || "";
  state.profileDraftName = state.profileName;
  state.profileReady = sessionStorage.getItem(PROFILE_READY_KEY) === "1";
  state.profileModalOpen = !state.profileReady;
  state.roomForm.createName = state.profileName;
  state.roomForm.joinName = state.profileName;
}

export function saveProfileName(rawName) {
  const normalized = normalizeProfileName(rawName);
  state.profileName = normalized;
  state.profileDraftName = normalized;
  state.profileReady = true;
  state.profileModalOpen = false;
  state.roomForm.createName = normalized;
  state.roomForm.joinName = normalized;
  sessionStorage.setItem(PROFILE_NAME_KEY, normalized);
  sessionStorage.setItem(PROFILE_READY_KEY, "1");
}

// 改昵称现在是账号菜单里的一项，关闭弹窗后焦点该回到那个触发按钮上。
function accountMenuTrigger() {
  return document.querySelector("#account-menu .menu__trigger");
}

export function openProfileModal() {
  ui.profileModalReturnFocus = document.activeElement;
  state.profileDraftName = state.profileName;
  state.profileModalOpen = true;
  // 只置状态不重绘，弹窗就永远不会出现——从账号菜单点"用户信息"没反应正是
  // 因为这里少了这一句。关闭与确认那两条路径本来就各自调了 render()。
  render();
}

export function closeProfileModal() {
  if (!state.profileReady) return;
  state.profileDraftName = state.profileName;
  state.profileModalOpen = false;
  render();
  window.requestAnimationFrame(() => {
    (ui.profileModalReturnFocus || accountMenuTrigger() || $("main-content"))?.focus?.();
    ui.profileModalReturnFocus = null;
  });
}

export function confirmProfile() {
  const returnFocus = ui.profileModalReturnFocus;
  saveProfileName(state.profileDraftName);
  render();
  window.requestAnimationFrame(() => {
    (returnFocus || accountMenuTrigger() || $("main-content"))?.focus?.();
    ui.profileModalReturnFocus = null;
  });
}

export function profileModalVisible() {
  return Boolean(state.authUser) && (state.profileModalOpen || !state.profileReady);
}

export function userLoggedIn() {
  return Boolean(state.authUser);
}

export function requireAuthForRoomEntry() {
  if (userLoggedIn()) return true;
  focusAuthGateForMode("武将对战房间");
  return false;
}

// 未登录时唯一可见的屏幕就是登录门，所以这里只需要留下原因，
// 下一次 render 自然会把玩家送过去。
function focusAuthGateForMode(modeLabel = "游戏") {
  if (userLoggedIn()) return false;
  state.authMessage = `进入${modeLabel}前需要先登录或注册。`;
  render();
  return true;
}

export function renderProfilePanel() {
  const joinCode = $("join-room-code");
  const createButton = $("create-room");
  const joinButton = $("join-room");
  if (createButton) createButton.disabled = !userLoggedIn() || !state.profileReady;
  if (joinButton) joinButton.disabled = !userLoggedIn() || !state.profileReady || !String(joinCode?.value || "").trim();
}
