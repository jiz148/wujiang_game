import { $ } from "../core/dom.js";
import { fetchJson } from "../core/net.js";
import { render } from "../core/render.js";
import { state } from "../core/state.js";

const REPLAY_OPTIONS = [
  ["off", "关闭"],
  ["actions", "仅记录动作"],
  ["full", "本场回看"],
];

const CHESS_REPLAY_OPTIONS = [
  ["off", "关闭"],
  ["on", "本场回看"],
];

const AI_OPTIONS = [
  ["full", "完整"],
  ["simple", "简化"],
  ["tendency", "倾向"],
  ["model", "模型"],
];

export function renderSettings() {
  const panel = $("settings-panel");
  if (!panel) return;
  const active = state.homeFlow === "settings" && state.screen === "draft" && !state.room;
  panel.classList.toggle("hidden", !active);
  if (!active) return;
  if (!state.settings && !state.settingsBusy) {
    state.settingsBusy = true;
    fetchJson("/api/settings")
      .then((payload) => {
        state.settings = payload;
        state.settingsError = "";
      })
      .catch((error) => {
        state.settingsError = error.error || "读取设置失败。";
        state.settings = state.settings || { replay_recording: "", simple_ai: false };
      })
      .finally(() => {
        state.settingsBusy = false;
        render();
      });
  }
  const host = $("settings-replay");
  const message = $("settings-message");
  if (message) message.textContent = state.settingsError || "";
  if (!host) return;
  if (!host.childElementCount) for (const [value, label] of REPLAY_OPTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.dataset.value = value;
    button.textContent = label;
    button.addEventListener("click", () => saveReplay(value, button));
    host.append(button);
  }
  paintReplay();
  paintChessReplay();
  paintAi();
}

function paintSegment(selector, current) {
  for (const button of document.querySelectorAll(selector)) {
    const selected = button.dataset.value === current;
    button.classList.toggle("primary", selected);
    button.classList.toggle("ghost", !selected);
    button.disabled = Boolean(state.settingsBusy);
  }
}

function paintReplay() {
  paintSegment("#settings-replay button", state.settings?.replay_recording || "");
}

function chessReplayValue() {
  return state.settings?.autochess_replay === true ? "on" : "off";
}

function paintChessReplay() {
  const host = $("settings-chess-replay");
  if (!host) return;
  if (host.childElementCount !== CHESS_REPLAY_OPTIONS.length) host.replaceChildren();
  if (!host.childElementCount) for (const [value, label] of CHESS_REPLAY_OPTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.dataset.value = value;
    button.textContent = label;
    button.addEventListener("click", () => saveChessReplay(value, button));
    host.append(button);
  }
  paintSegment("#settings-chess-replay button", chessReplayValue());
}

function currentAiValue() {
  const mode = state.settings?.ai_mode;
  if (mode === "simple" || mode === "tendency" || mode === "full" || mode === "model") return mode;
  return state.settings?.simple_ai === true ? "simple" : "full";
}

function paintAi() {
  const host = $("settings-ai");
  if (!host) return;
  if (host.childElementCount !== AI_OPTIONS.length) {
    host.replaceChildren();
  }
  if (!host.childElementCount) for (const [value, label] of AI_OPTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.dataset.value = value;
    button.textContent = label;
    button.addEventListener("click", () => saveAi(value, button));
    host.append(button);
  }
  paintSegment("#settings-ai button", currentAiValue());
}

async function saveSetting(button, unchanged, body) {
  if (state.settingsBusy || unchanged) return;
  state.settingsBusy = true;
  button.classList.add("is-busy");
  paintReplay();
  paintChessReplay();
  paintAi();
  try {
    state.settings = await fetchJson("/api/settings", {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.settingsError = "";
  } catch (error) {
    state.settingsError = error.error || "保存设置失败。";
  } finally {
    state.settingsBusy = false;
    button.classList.remove("is-busy");
    paintReplay();
    paintChessReplay();
    paintAi();
    const message = $("settings-message");
    if (message) message.textContent = state.settingsError || "";
  }
}

async function saveReplay(value, button) {
  await saveSetting(button, state.settings?.replay_recording === value, { replay_recording: value });
}

async function saveChessReplay(value, button) {
  const enabled = value === "on";
  await saveSetting(button, (state.settings?.autochess_replay === true) === enabled, { autochess_replay: enabled });
}

async function saveAi(value, button) {
  await saveSetting(button, currentAiValue() === value, { ai_mode: value });
}
