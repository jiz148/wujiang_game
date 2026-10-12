import { $ } from "../core/dom.js";
import { fetchJson } from "../core/net.js";
import { render } from "../core/render.js";
import { state } from "../core/state.js";

const REPLAY_OPTIONS = [
  ["off", "关闭"],
  ["actions", "只记动作"],
  ["full", "完整回放"],
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
        state.settings = state.settings || { replay_recording: "" };
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
    button.dataset.replay = value;
    button.textContent = label;
    button.addEventListener("click", () => saveReplay(value, button));
    host.append(button);
  }
  paintReplay();
}

function paintReplay() {
  const current = state.settings?.replay_recording || "";
  for (const button of document.querySelectorAll("#settings-replay button")) {
    const selected = button.dataset.replay === current;
    button.classList.toggle("primary", selected);
    button.classList.toggle("ghost", !selected);
    button.disabled = Boolean(state.settingsBusy);
  }
}

async function saveReplay(value, button) {
  if (state.settingsBusy || state.settings?.replay_recording === value) return;
  state.settingsBusy = true;
  button.classList.add("is-busy");
  paintReplay();
  try {
    state.settings = await fetchJson("/api/settings", {
      method: "POST",
      body: JSON.stringify({ replay_recording: value }),
    });
    state.settingsError = "";
  } catch (error) {
    state.settingsError = error.error || "保存设置失败。";
  } finally {
    state.settingsBusy = false;
    button.classList.remove("is-busy");
    paintReplay();
    const message = $("settings-message");
    if (message) message.textContent = state.settingsError || "";
  }
}
