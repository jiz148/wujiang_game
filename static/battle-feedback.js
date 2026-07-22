(function attachBattleFeedback(global) {
  "use strict";

  const STORAGE_KEY = "wujiang-accessibility-preferences";
  const defaults = {sound: false, colorblind: false, motion: "system"};
  let preferences = {...defaults};
  let audioContext = null;

  function readPreferences() {
    try {
      const saved = JSON.parse(global.localStorage?.getItem(STORAGE_KEY) || "{}");
      preferences = {
        sound: Boolean(saved.sound),
        colorblind: Boolean(saved.colorblind),
        motion: ["system", "reduce", "full"].includes(saved.motion) ? saved.motion : "system",
      };
    } catch (_error) {
      preferences = {...defaults};
    }
    return preferences;
  }

  function savePreferences() {
    try {
      global.localStorage?.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch (_error) {
      // Preference persistence is optional in privacy-restricted browsers.
    }
  }

  function systemReducesMotion() {
    return Boolean(global.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
  }

  function reducedMotion() {
    return preferences.motion === "reduce"
      || (preferences.motion === "system" && systemReducesMotion());
  }

  function byId(id) {
    return global.document?.getElementById(id) || null;
  }

  function renderPreferences() {
    const body = global.document?.body;
    body?.classList.toggle("colorblind-mode", preferences.colorblind);
    body?.classList.toggle("reduce-motion", reducedMotion());
    const sound = byId("toggle-battle-sound");
    const colorblind = byId("toggle-colorblind-mode");
    const motion = byId("toggle-reduced-motion");
    if (sound) {
      sound.textContent = `声音：${preferences.sound ? "开" : "关"}`;
      sound.setAttribute("aria-pressed", String(preferences.sound));
    }
    if (colorblind) {
      colorblind.textContent = `色弱高对比：${preferences.colorblind ? "开" : "关"}`;
      colorblind.setAttribute("aria-pressed", String(preferences.colorblind));
    }
    if (motion) {
      const label = preferences.motion === "system" ? "跟随系统" : (preferences.motion === "reduce" ? "减少" : "完整");
      motion.textContent = `动态：${label}`;
      motion.setAttribute("aria-pressed", String(reducedMotion()));
    }
  }

  function initialize() {
    readPreferences();
    renderPreferences();
    const media = global.matchMedia?.("(prefers-reduced-motion: reduce)");
    media?.addEventListener?.("change", () => {
      if (preferences.motion === "system") renderPreferences();
    });
  }

  function toggle(kind) {
    if (kind === "sound") preferences.sound = !preferences.sound;
    else if (kind === "colorblind") preferences.colorblind = !preferences.colorblind;
    else if (kind === "motion") {
      preferences.motion = preferences.motion === "system" ? "reduce" : (preferences.motion === "reduce" ? "full" : "system");
    }
    savePreferences();
    renderPreferences();
    if (kind === "sound" && preferences.sound) playCue("ready");
    return {...preferences};
  }

  function playCue(kind) {
    if (!preferences.sound) return;
    const AudioCtor = global.AudioContext || global.webkitAudioContext;
    if (!AudioCtor) return;
    try {
      audioContext = audioContext || new AudioCtor();
      if (audioContext.state === "suspended") audioContext.resume?.();
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      const now = audioContext.currentTime;
      const tones = {
        ready: [520, 0.08], attack: [180, 0.07], defense: [740, 0.1], chain: [420, 0.12], death: [120, 0.18], victory: [660, 0.28], defeat: [150, 0.28], skill: [520, 0.1],
      };
      const [frequency, duration] = tones[kind] || tones.skill;
      oscillator.type = kind === "death" || kind === "defeat" ? "sawtooth" : "sine";
      oscillator.frequency.setValueAtTime(frequency, now);
      if (kind === "victory") oscillator.frequency.exponentialRampToValueAtTime(990, now + duration);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.055, now + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      oscillator.start(now);
      oscillator.stop(now + duration + 0.02);
    } catch (_error) {
      // Audio feedback must never block battle input.
    }
  }

  function unitName(battle, unitId) {
    return (battle?.units || []).find((unit) => String(unit.id) === String(unitId))?.name || "单位";
  }

  function defenseLabel(reason) {
    return ({
      shield: "护盾挡下攻击",
      shield_break: "护盾被击破",
      shield_half_break: "护盾抵消了部分伤害",
      magic_immunity: "魔法免疫生效",
      dodge: "闪避成功",
      physical_immunity: "物理免疫生效",
    })[reason] || "防御生效";
  }

  function pushFeedback(kind, text, cue = kind) {
    const feed = byId("combat-feedback-feed");
    if (feed) {
      const item = global.document.createElement("div");
      item.className = `combat-feedback-item is-${kind}`;
      item.dataset.feedbackKind = kind;
      item.innerHTML = `<span class="combat-feedback-icon" aria-hidden="true"></span><strong>${text}</strong>`;
      feed.prepend(item);
      while (feed.children.length > 3) feed.lastElementChild?.remove();
    }
    const announcer = byId("battle-announcer");
    if (announcer) announcer.textContent = text;
    playCue(cue);
  }

  function consume({previousBattle, battle, events = [], viewerTeamId = null, replayMode = false}) {
    if (!battle || replayMode) return;
    events.forEach((event) => {
      if (event.kind === "defense") {
        pushFeedback("defense", defenseLabel(event.defense_reason), "defense");
      } else if (event.kind === "attack") {
        pushFeedback("attack", `${unitName(battle, event.actor_id)} 发动攻击`, "attack");
      } else if (event.kind === "skill") {
        pushFeedback("skill", `${unitName(battle, event.actor_id)} 使用 ${event.display_name || "技能"}`, "skill");
      }
    });
    if (!previousBattle) return;
    const previousUnits = new Map((previousBattle.units || []).map((unit) => [String(unit.id), unit]));
    (battle.units || []).forEach((unit) => {
      const before = previousUnits.get(String(unit.id));
      const wasAlive = before && !before.destroyed && Number(before.hp || 0) > 0;
      const isDead = Boolean(unit.destroyed) || Number(unit.hp || 0) <= 0;
      if (wasAlive && isDead) pushFeedback("death", `${unit.name || "单位"} 被击破`, "death");
    });
    const previousReactorId = previousBattle.pending_chain?.current_unit_id || "";
    const currentReactorId = battle.pending_chain?.current_unit_id || "";
    if (battle.pending_chain && previousReactorId !== currentReactorId) {
      const responder = unitName(battle, battle.pending_chain.current_unit_id);
      pushFeedback("chain", `连锁窗口开启：等待 ${responder} 响应`, "chain");
    }
    if (!previousBattle.winner && battle.winner) {
      const won = viewerTeamId !== null && Number(viewerTeamId) === Number(battle.winner);
      pushFeedback(won ? "victory" : "defeat", won ? "战斗胜利" : "战斗结束：对方获胜", won ? "victory" : "defeat");
    }
  }

  global.WujiangBattleFeedback = Object.freeze({
    consume,
    initialize,
    preferences: () => ({...preferences}),
    reducedMotion,
    renderPreferences,
    toggle,
  });
}(globalThis));
