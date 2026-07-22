(function attachReplayUi(global) {
  "use strict";

  function renderToolbar({document, state, replay, simulation, replayMode}) {
    const toolbar = document.getElementById("replay-toolbar");
    if (!toolbar) return;
    const visible = Boolean(state.battle && replay.available);
    toolbar.classList.toggle("hidden", !visible);
    if (!visible) return;
    const lastIndex = Number(replay.last_step_index || 0);
    const liveIndex = Number(simulation.live_step_index || 0);
    const currentIndex = replayMode
      ? Math.max(0, Math.min(lastIndex, Number(state.replayStepIndex || 0)))
      : Math.max(0, Math.min(lastIndex, liveIndex));
    const byId = (id) => document.getElementById(id);
    const back = byId("replay-step-back");
    const pause = byId("replay-pause");
    const live = byId("replay-live");
    const forward = byId("replay-step-forward");
    const speed = byId("replay-speed");
    const omniscient = byId("replay-omniscient");
    const timeline = byId("replay-timeline");
    const status = byId("replay-status");
    if (speed) {
      speed.value = String(simulation.speed || 1);
      speed.disabled = !state.room?.viewer_is_host;
    }
    if (omniscient) {
      omniscient.checked = Boolean(state.replayOmniscient);
      omniscient.disabled = !replay.can_use_omniscient;
    }
    if (timeline) {
      timeline.max = String(lastIndex);
      timeline.value = String(currentIndex);
      timeline.disabled = !replay.available;
    }
    if (back) back.disabled = currentIndex <= 0;
    if (forward) forward.disabled = currentIndex >= lastIndex;
    if (live) {
      live.textContent = state.historicalMatchId ? "返回战绩" : "LIVE";
      live.disabled = state.historicalMatchId ? false : !replayMode;
    }
    if (pause) {
      pause.textContent = simulation.paused ? "▶" : "II";
      pause.disabled = !simulation.can_control;
    }
    if (status) {
      if (replayMode) status.textContent = `回放 ${currentIndex}/${lastIndex}`;
      else if (simulation.enabled) status.textContent = simulation.paused
        ? `已暂停 ${liveIndex}/${lastIndex}`
        : `实时 ${liveIndex}/${lastIndex}`;
      else status.textContent = `本局 ${currentIndex}/${lastIndex}`;
    }
  }

  global.WujiangReplayUi = Object.freeze({renderToolbar});
}(globalThis));
