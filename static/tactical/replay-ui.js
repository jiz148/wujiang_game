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
      const options = (simulation.speed_options || [0.5, 1, 2, 4, 6, 8, 16]).map(String);
      const existing = Array.from(speed.options).map((item) => item.value);
      if (existing.join() !== options.join()) {
        speed.replaceChildren();
        options.forEach((value) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = `${value}x`;
          speed.append(option);
        });
      }
      speed.value = String(simulation.speed || 1);
      speed.disabled = !state.room?.viewer_is_host;
    }
    if (omniscient) {
      omniscient.checked = Boolean(state.replayOmniscient);
      omniscient.disabled = !replay.can_use_omniscient;
    }
    if (timeline) {
      timeline.max = String(lastIndex);
      if (!state.replayTimelineDragging) {
        timeline.value = String(currentIndex);
      }
      timeline.disabled = !replay.available || lastIndex <= 0;
    }
    if (back) back.disabled = currentIndex <= 0;
    if (forward) forward.disabled = currentIndex >= lastIndex;
    if (live) {
      live.textContent = state.historicalMatchId ? "返回战绩" : "最新";
      live.disabled = state.historicalMatchId ? false : !replayMode;
    }
    if (pause) {
      pause.textContent = simulation.paused ? "继续" : "暂停";
      pause.disabled = !simulation.can_control;
    }
    if (status) {
      const completed = Number(state.battle?.completed_turns || 0);
      const turnIndex = Math.max(1, Number(state.battle?.turn_number || completed + 1 || 1));
      const turnLimit = Number(state.battle?.turn_timeout_limit || 0);
      const turnLabel = turnLimit > 0 ? `第 ${turnIndex}/${turnLimit} 回合` : `第 ${turnIndex} 回合`;
      if (replayMode) status.textContent = `回放 ${turnLabel}（${currentIndex}/${lastIndex}）`;
      else if (simulation.enabled) status.textContent = simulation.paused
        ? `已暂停 ${turnLabel}（${liveIndex}/${lastIndex}）`
        : `实时 ${turnLabel}（${liveIndex}/${lastIndex}）`;
      else status.textContent = `本局 ${turnLabel}（${currentIndex}/${lastIndex}）`;
    }
  }

  global.WujiangReplayUi = Object.freeze({renderToolbar});
}(globalThis));
