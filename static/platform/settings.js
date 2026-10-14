import { $ } from "../core/dom.js";
import { fetchJson } from "../core/net.js";
import { render } from "../core/render.js";
import { state } from "../core/state.js";

const REPLAY_OPTIONS = [
  ["off", "关闭"],
  ["actions", "只记动作"],
  ["full", "完整回放"],
];

const AI_OPTIONS = [
  ["simple", "简化"],
  ["full", "完整"],
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

function currentAiValue() {
  return state.settings?.simple_ai === true ? "simple" : "full";
}

function paintAi() {
  const host = $("settings-ai");
  if (!host) return;
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
    paintAi();
    const message = $("settings-message");
    if (message) message.textContent = state.settingsError || "";
  }
}

async function saveReplay(value, button) {
  await saveSetting(button, state.settings?.replay_recording === value, { replay_recording: value });
}

async function saveAi(value, button) {
  await saveSetting(button, currentAiValue() === value, { simple_ai: value === "simple" });
}
