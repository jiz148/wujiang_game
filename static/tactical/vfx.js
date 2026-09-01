// Battle visual effects and animation scheduling.
import { $ } from '../core/dom.js';
import { bundleFor, isChainMode, isGameOver, isRespawnMode, normalizedCell, unitById, unitOccupiedCells } from '../core/net.js';
import { state, ui } from '../core/state.js';

export function trimNumber(value) {
  const rounded = Math.round(Number(value || 0) * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : String(rounded).replace(/0+$/, "").replace(/\.$/, "");
}

export function hpRatio(unit) {
  if (!unit || !unit.max_hp) return 0;
  return Math.max(0, Math.min(1, Number(unit.hp || 0) / Number(unit.max_hp)));
}

function manaValue(unit) {
  return Math.max(0, Number(unit?.mana || 0));
}

export function manaDisplayClass(unit) {
  return manaValue(unit) > 5 ? "mana-pips is-compact" : "mana-pips";
}

export function manaPipsMarkup(unit) {
  const mana = manaValue(unit);
  if (mana > 5) {
    return `<span class="mana-pip is-filled"></span><span class="mana-count">${trimNumber(mana)}</span>`;
  }
  const fullPips = Math.floor(mana);
  const hasHalfPip = Math.abs(mana - fullPips - 0.5) < 0.001;
  const pips = [];
  for (let index = 0; index < fullPips; index += 1) {
    pips.push(`<span class="mana-pip is-filled"></span>`);
  }
  if (hasHalfPip) {
    pips.push(`<span class="mana-pip is-half"></span>`);
  }
  if (!pips.length) {
    return `<span class="mana-zero">0</span>`;
  }
  return pips.join("");
}

export function unitStatusSummary(unit) {
  const entries = [];
  if (!unit) return entries;
  if (unit.banished) {
    entries.push(`消失${unit.banish_turns_remaining > 0 ? `(${unit.banish_turns_remaining})` : ""}`);
  }
  if (unit.total_shields) {
    entries.push(`护盾 ${unit.total_shields}`);
  }
  if (unit.dodge_charges) {
    entries.push(`回避 ${unit.dodge_charges}`);
  }
  unit.statuses.forEach((status) => {
    entries.push(`${status.name}${status.duration ? `(${status.duration})` : ""}`);
  });
  return entries;
}

export function fieldEffects() {
  return state.battle?.field_effects || [];
}

export function fieldEffectDuration(effect) {
  if (effect?.duration == null) return "持续中";
  return `${trimNumber(effect.duration / 2)}轮`;
}

export function displayActions() {
  if (isGameOver()) return [];
  if (isRespawnMode()) return [];
  const bundle = bundleFor(state.selectedUnitId);
  if (!bundle) return [];
  if (isChainMode()) {
    const reactions = (bundle.reactions.actions || []).map((action) => ({
      ...action,
      code: action.action_code,
      kind: action.action_type === "skill" ? "skill" : "reaction_action",
      preview: action.preview || { cells: [], target_unit_ids: [] },
      available: true,
    }));
    reactions.push({
      code: "chain_skip",
      name: "不连锁",
      action_name: "不连锁",
      kind: "chain_skip",
      timing: "reaction",
      chain_speed: 0,
      description: "放弃本次连锁,让原动作按原本声明继续结算。",
      preview: { cells: [], target_unit_ids: [], secondary_cells: [], requires_target: false },
      available: true,
    });
    return filterTutorialActions(reactions);
  }
  const visible = filterTutorialActions((bundle.actions.actions || []).filter((action) => {
    if (!action.available) return false;
    if (action.kind === "move" || action.kind === "attack") return true;
    return action.timing === "active";
  }));
  return mergeAttackVariants(visible);
}

function mergeAttackVariants(actions) {
  const primaryAttack = actions.find((action) => action.kind === "attack" && !action.is_attack_variant)
    || actions.find((action) => action.kind === "attack");
  if (!primaryAttack) return actions;
  const attackVariants = actions.filter((action) => (
    action.kind === "attack"
    && action.is_attack_variant
    && action !== primaryAttack
  ));
  if (!attackVariants.length) return actions;
  return actions
    .filter((action) => action.kind !== "attack" || action === primaryAttack)
    .map((action) => (action === primaryAttack ? { ...action, attackVariants } : action));
}

export function tutorialState() {
  return state.room?.experience_kind === "tutorial" ? state.room?.tutorial || null : null;
}

function filterTutorialActions(actions) {
  const tutorial = tutorialState();
  if (!tutorial) return actions;
  const stepId = tutorial.step_id;
  let filtered = actions;
  if (stepId === "select_unit" || stepId === "end_turn") filtered = [];
  else if (stepId === "move") filtered = actions.filter((action) => action.code === "move");
  else if (stepId === "basic_attack") filtered = actions.filter((action) => action.kind === "attack");
  else if (stepId === "active_skill") {
    filtered = actions.filter((action) => action.code === "pierce").map((action) => ({
      ...action,
      preview: {
        ...(action.preview || {}),
        cells: [{x: 5, y: 4}, {x: 6, y: 4}],
        selection: {mode: "pattern_cells", patterns: [[{x: 5, y: 4}, {x: 6, y: 4}]], ordered: false},
      },
    }));
  }
  else if (stepId === "chain_response") filtered = actions.filter((action) => action.kind === "chain_skip" || action.timing === "reaction");
  if (stepId !== "move") return filtered;
  return filtered.map((action) => ({
    ...action,
    preview: {
      ...(action.preview || {}),
      cells: [{x: 4, y: 4}],
    },
  }));
}

export function actionByCode(code) {
  return displayActions().find((action) => action.code === code) || null;
}

export function selectedAction() {
  if (!state.selectedActionCode) return null;
  const live = actionByCode(state.selectedActionCode);
  if (live) {
    state.selectedActionSnapshot = live;
    return live;
  }
  if (state.selectedActionSnapshot?.code === state.selectedActionCode) {
    return state.selectedActionSnapshot;
  }
  return null;
}

export function hoveredAction() {
  return selectedAction() || actionByCode(state.hoveredActionCode);
}

export function positionKey(pos) {
  return `${pos.x},${pos.y}`;
}

export function positionsToSet(cells = []) {
  return new Set(cells.map((cell) => `${cell.x},${cell.y}`));
}

export function targetIdsToSet(targets = []) {
  return new Set(targets);
}

export function visualEvents() {
  return state.battle?.visual_events || [];
}

export function maxVisualEventId(events = visualEvents()) {
  return events.reduce((maxId, event) => Math.max(maxId, Number(event?.id || 0)), 0);
}

function battleVfxLayer() {
  return $("battle-vfx");
}

export function actionWheelLayer() {
  const stage = $("board-stage");
  if (!stage) return null;
  let layer = $("action-wheel");
  if (!layer) {
    layer = document.createElement("div");
    layer.id = "action-wheel";
    layer.className = "action-wheel";
  }
  if (layer.parentNode !== stage && typeof stage.appendChild === "function") {
    stage.appendChild(layer);
  }
  return layer;
}

function clearBattleVfxCleanupTimer() {
  if (!ui.battleVfxCleanupHandle || typeof window.clearTimeout !== "function") return;
  window.clearTimeout(ui.battleVfxCleanupHandle);
  ui.battleVfxCleanupHandle = 0;
}

function removeBattleVfxEntry(entry) {
  (entry?.nodes || []).forEach(({ node }) => node?.remove?.());
}

function clearArmyVfxTimers() {
  (ui.armyVfxTimers || []).forEach((handle) => {
    if (handle && typeof window.clearTimeout === "function") window.clearTimeout(handle);
  });
  ui.armyVfxTimers = [];
}

export function clearBattleVfx() {
  clearBattleVfxCleanupTimer();
  clearArmyVfxTimers();
  state.activeBattleVfx.forEach(removeBattleVfxEntry);
  state.activeBattleVfx = [];
  state.pendingArmyVfx = [];
  const layer = battleVfxLayer();
  if (layer) layer.innerHTML = "";
}

function armyStrikeWaveOf(event) {
  const wave = String(event?.metadata?.army_strike_wave || "");
  if (wave === "ranged" || wave === "cannon") return wave;
  return "melee";
}

function armyMarchWillAnimate() {
  const army = state.battle?.army;
  const marchId = String(army?.march_id || "");
  const traces = Array.isArray(army?.move_traces)
    ? army.move_traces.filter((item) => Array.isArray(item?.path) && item.path.length > 1)
    : [];
  if (!marchId || !traces.length) return false;
  if (globalThis.WujiangBattleFeedback?.reducedMotion()) return false;
  const maxTick = traces.reduce((max, item) => Math.max(max, item.path.length), 1) - 1;
  if (state.armyMarch?.id === marchId && Number(state.armyMarch.tick || 0) >= maxTick) return false;
  return true;
}

function playVisualEventNow(event) {
  const entry = createBattleVfxEntry(event);
  if (entry) state.activeBattleVfx.push(entry);
}

export function playArmyStrikeWaves(events, { staggerMs = 360 } = {}) {
  const pending = Array.isArray(events) ? events.filter(Boolean) : [];
  if (!pending.length) return;
  if (globalThis.WujiangBattleFeedback?.reducedMotion()) {
    pending.forEach(playVisualEventNow);
    renderBattleVfx();
    return;
  }
  const waves = ["melee", "ranged", "cannon"];
  let delay = 0;
  waves.forEach((wave) => {
    const batch = pending.filter((event) => armyStrikeWaveOf(event) === wave);
    if (!batch.length) return;
    const start = delay;
    const handle = window.setTimeout(() => {
      batch.forEach(playVisualEventNow);
      renderBattleVfx();
    }, start);
    ui.armyVfxTimers = [...(ui.armyVfxTimers || []), handle];
    delay += staggerMs;
  });
}

export function flushPendingArmyVfx() {
  const pending = state.pendingArmyVfx || [];
  state.pendingArmyVfx = [];
  playArmyStrikeWaves(pending);
}

function battleVfxStyle(event) {
  return String(event?.metadata?.vfx_style || "");
}

function battleVfxDuration(event) {
  if (globalThis.WujiangBattleFeedback?.reducedMotion()) return 160;
  const custom = Number(event?.metadata?.duration_ms || 0);
  if (custom > 0) return custom;
  if (!event) return 700;
  if (event.kind === "attack") return 620;
  if (event.kind === "defense") return 760;
  if (event.action_type === "skill_effect") return 840;
  return 900;
}

function battleVfxTheme(event) {
  if (!event) return "arcane";
  const style = battleVfxStyle(event);
  if (style === "shell") return "cannon";
  if (style === "bolt") return "bolt";
  if (event.kind === "attack") return event.action_code === "counter" ? "storm" : "attack";
  if (event.kind === "defense") {
    if (event.defense_reason === "magic_immunity") return "void";
    if (event.defense_reason === "dodge") return "wind";
    if (event.defense_reason === "shield_break" || event.defense_reason === "shield_half_break") return "shatter";
    return "barrier";
  }
  const code = `${event.action_code || ""} ${event.display_name || ""}`.toLowerCase();
  if (/(fire|burn|blaze|funeral|missile|judgment|doom)/.test(code)) return "fire";
  if (/(holy|light|sun|judg)/.test(code)) return "holy";
  if (/(dark|shadow|stealth|doomlight|curse|undead)/.test(code)) return "shadow";
  if (/(rock|earth|stone|sand|dust)/.test(code)) return "earth";
  if (/(wind|storm|kick|machine|gun|pierce|shock|thunder|lightning)/.test(code)) return "storm";
  if (/(heal|chant|mana|plasma|laser|ion|quantum|jade|motor)/.test(code)) return "arcane";
  if (/(wall|shield|protect|guard|block)/.test(code)) return "barrier";
  if (/(banish|apocalypse|doom)/.test(code)) return "void";
  return "arcane";
}

function boardCellNodes() {
  return Array.from($("board")?.children || []).filter((node) => node?.dataset?.x != null && node?.dataset?.y != null);
}

function boardCellNodeAt(x, y) {
  return boardCellNodes().find((cell) => Number(cell.dataset.x) === Number(x) && Number(cell.dataset.y) === Number(y)) || null;
}

function nodeCenterRelativeToStage(node) {
  if (!node || typeof node.getBoundingClientRect !== "function") return null;
  const rect = node.getBoundingClientRect();
  const stageRect = $("board-stage")?.getBoundingClientRect?.();
  if (!rect || !stageRect) return null;
  return {
    x: rect.left - stageRect.left + rect.width / 2,
    y: rect.top - stageRect.top + rect.height / 2,
  };
}

function nodeRectRelativeToStage(node) {
  if (!node || typeof node.getBoundingClientRect !== "function") return null;
  const rect = node.getBoundingClientRect();
  const stageRect = $("board-stage")?.getBoundingClientRect?.();
  if (!rect || !stageRect) return null;
  const left = rect.left - stageRect.left;
  const top = rect.top - stageRect.top;
  return {
    left,
    top,
    width: rect.width,
    height: rect.height,
    right: left + rect.width,
    bottom: top + rect.height,
  };
}

function cellCenterPoint(cell) {
  const normalized = normalizedCell(cell);
  if (!normalized) return null;
  return nodeCenterRelativeToStage(boardCellNodeAt(normalized.x, normalized.y));
}

function unitCenterPoint(unit) {
  const cells = unitOccupiedCells(unit);
  if (!cells.length) return null;
  const points = cells.map((cell) => cellCenterPoint(cell)).filter(Boolean);
  if (!points.length) return null;
  const sum = points.reduce((acc, point) => ({ x: acc.x + point.x, y: acc.y + point.y }), { x: 0, y: 0 });
  return { x: sum.x / points.length, y: sum.y / points.length };
}

export function unitBoundsRelativeToStage(unit) {
  const cells = unitOccupiedCells(unit);
  if (!cells.length) return null;
  const rects = cells.map((cell) => nodeRectRelativeToStage(boardCellNodeAt(cell.x, cell.y))).filter(Boolean);
  if (!rects.length) return null;
  return {
    left: Math.min(...rects.map((rect) => rect.left)),
    top: Math.min(...rects.map((rect) => rect.top)),
    right: Math.max(...rects.map((rect) => rect.right)),
    bottom: Math.max(...rects.map((rect) => rect.bottom)),
    width: Math.max(...rects.map((rect) => rect.right)) - Math.min(...rects.map((rect) => rect.left)),
    height: Math.max(...rects.map((rect) => rect.bottom)) - Math.min(...rects.map((rect) => rect.top)),
  };
}

function battleVfxSourcePoint(event) {
  const sourceCell = normalizedCell(event?.source_cell);
  if (sourceCell) return cellCenterPoint(sourceCell);
  return unitCenterPoint(unitById(event?.actor_id || ""));
}

function battleVfxTargetRefs(event) {
  const cells = Array.isArray(event?.target_cells) ? event.target_cells.map(normalizedCell).filter(Boolean) : [];
  if (cells.length) {
    return cells.map((cell, index) => ({ kind: "cell", key: `cell:${positionKey(cell)}:${index}`, cell }));
  }
  const targetUnitIds = Array.isArray(event?.target_unit_ids) ? event.target_unit_ids : [];
  return targetUnitIds.map((unitId) => ({ kind: "unit", key: `unit:${unitId}`, unitId: String(unitId) }));
}

function battleVfxPointForRef(ref) {
  if (!ref) return null;
  if (ref.kind === "cell") return cellCenterPoint(ref.cell);
  return unitCenterPoint(unitById(ref.unitId || ""));
}

function attachBattleVfxNode(node, layer, entry, type, ref = null) {
  node.dataset.vfxEventId = String(entry.event.id || 0);
  node.dataset.vfxType = type;
  layer.append(node);
  entry.nodes.push({ node, type, ref });
}

function createBattleVfxEntry(event) {
  const layer = battleVfxLayer();
  if (!layer || !event) return null;
  const duration = battleVfxDuration(event);
  const linger = battleVfxStyle(event) === "shell" || battleVfxStyle(event) === "bolt" ? 180 : 0;
  const entry = {
    event,
    expiresAt: Date.now() + duration + linger,
    nodes: [],
  };
  const refs = battleVfxTargetRefs(event);
  const theme = battleVfxTheme(event);
  const sourcePoint = battleVfxSourcePoint(event);
  const style = battleVfxStyle(event);
  const reduced = Boolean(globalThis.WujiangBattleFeedback?.reducedMotion());

  if (style === "shell") {
    const impactCell = normalizedCell(event?.metadata?.impact_cell);
    const impactRef = impactCell
      ? { kind: "cell", key: `impact:${positionKey(impactCell)}`, cell: impactCell }
      : (refs[0] || null);
    if (sourcePoint && !reduced) {
      const muzzle = document.createElement("div");
      muzzle.className = `battle-vfx-muzzle theme-${theme}`;
      muzzle.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(muzzle, layer, entry, "muzzle");
      const shell = document.createElement("div");
      shell.className = `battle-vfx-shell theme-${theme}`;
      shell.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(shell, layer, entry, "shell", impactRef);
    }
    if (impactRef) {
      const explosion = document.createElement("div");
      explosion.className = `battle-vfx-explosion theme-${theme}`;
      explosion.style.setProperty("--vfx-duration", `${duration}ms`);
      explosion.style.setProperty("--vfx-delay", reduced ? "0ms" : `${Math.round(duration * 0.58)}ms`);
      attachBattleVfxNode(explosion, layer, entry, "explosion", impactRef);
    }
    refs.forEach((ref) => {
      if (impactRef && ref.key === impactRef.key) return;
      if (impactCell && ref.kind === "cell" && ref.cell?.x === impactCell.x && ref.cell?.y === impactCell.y) return;
      const splash = document.createElement("div");
      splash.className = `battle-vfx-splash theme-${theme}`;
      splash.style.setProperty("--vfx-duration", `${duration}ms`);
      splash.style.setProperty("--vfx-delay", reduced ? "0ms" : `${Math.round(duration * 0.66)}ms`);
      attachBattleVfxNode(splash, layer, entry, "splash", ref);
    });
    return entry;
  }

  if (style === "bolt") {
    const impactCell = normalizedCell(event?.metadata?.impact_cell);
    const impactRef = impactCell
      ? { kind: "cell", key: `impact:${positionKey(impactCell)}`, cell: impactCell }
      : (refs[0] || null);
    if (sourcePoint && !reduced) {
      const muzzle = document.createElement("div");
      muzzle.className = `battle-vfx-muzzle is-bolt theme-${theme}`;
      muzzle.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(muzzle, layer, entry, "muzzle");
      const bolt = document.createElement("div");
      bolt.className = `battle-vfx-bolt theme-${theme}`;
      bolt.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(bolt, layer, entry, "bolt", impactRef);
    }
    if (impactRef) {
      const mark = document.createElement("div");
      mark.className = `battle-vfx-bolt-mark theme-${theme}`;
      mark.style.setProperty("--vfx-duration", `${duration}ms`);
      mark.style.setProperty("--vfx-delay", reduced ? "0ms" : `${Math.round(duration * 0.48)}ms`);
      attachBattleVfxNode(mark, layer, entry, "bolt-mark", impactRef);
    }
    return entry;
  }

  if (event.kind === "attack") {
    refs.forEach((ref) => {
      const projectile = document.createElement("div");
      projectile.className = `battle-vfx-projectile theme-${theme}`;
      projectile.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(projectile, layer, entry, "projectile", ref);
      const impact = document.createElement("div");
      impact.className = `battle-vfx-impact theme-${theme}`;
      impact.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(impact, layer, entry, "impact", ref);
    });
  } else if (event.kind === "skill") {
    if (sourcePoint) {
      const source = document.createElement("div");
      source.className = `battle-vfx-source theme-${theme}`;
      source.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(source, layer, entry, "source");
    }
    const burstRefs = refs.length ? refs : [{ kind: "source", key: "source" }];
    burstRefs.forEach((ref) => {
      const burst = document.createElement("div");
      burst.className = `battle-vfx-burst theme-${theme}`;
      burst.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(burst, layer, entry, "burst", ref.kind === "source" ? null : ref);
    });
  } else if (event.kind === "defense") {
    refs.forEach((ref) => {
      const shield = document.createElement("div");
      shield.className = `battle-vfx-shield theme-${theme}`;
      shield.style.setProperty("--vfx-duration", `${duration}ms`);
      attachBattleVfxNode(shield, layer, entry, "shield", ref);
    });
  }
  return entry;
}

function positionBattleVfxEntry(entry) {
  const sourcePoint = battleVfxSourcePoint(entry.event);
  entry.nodes.forEach(({ node, type, ref }) => {
    let point = ref ? battleVfxPointForRef(ref) : sourcePoint;
    if (!point && type === "projectile") {
      node.classList.add("hidden");
      return;
    }
    if (type === "muzzle") {
      if (!sourcePoint) {
        node.classList.add("hidden");
        return;
      }
      node.classList.remove("hidden");
      node.style.left = `${sourcePoint.x}px`;
      node.style.top = `${sourcePoint.y}px`;
      return;
    }
    if (type === "shell" || type === "bolt") {
      const targetPoint = battleVfxPointForRef(ref);
      if (!sourcePoint || !targetPoint) {
        node.classList.add("hidden");
        return;
      }
      node.classList.remove("hidden");
      node.style.left = `${sourcePoint.x}px`;
      node.style.top = `${sourcePoint.y}px`;
      node.style.setProperty("--vfx-dx", `${targetPoint.x - sourcePoint.x}px`);
      node.style.setProperty("--vfx-dy", `${targetPoint.y - sourcePoint.y}px`);
      const angle = Math.atan2(targetPoint.y - sourcePoint.y, targetPoint.x - sourcePoint.x) * (180 / Math.PI);
      if (type === "bolt") node.style.setProperty("--vfx-angle", `${angle}deg`);
      return;
    }
    if (type === "bolt-mark") {
      const cell = ref?.cell;
      const rect = cell ? nodeRectRelativeToStage(boardCellNodeAt(cell.x, cell.y)) : null;
      if (!rect) {
        node.classList.add("hidden");
        return;
      }
      node.classList.remove("hidden");
      node.style.left = `${rect.left}px`;
      node.style.top = `${rect.top}px`;
      node.style.width = `${rect.width}px`;
      node.style.height = `${rect.height}px`;
      return;
    }
    if (type === "projectile") {
      const targetPoint = battleVfxPointForRef(ref);
      if (!sourcePoint || !targetPoint) {
        node.classList.add("hidden");
        return;
      }
      const dx = targetPoint.x - sourcePoint.x;
      const dy = targetPoint.y - sourcePoint.y;
      const length = Math.max(18, Math.sqrt((dx ** 2) + (dy ** 2)));
      const angle = Math.atan2(dy, dx) * (180 / Math.PI);
      node.classList.remove("hidden");
      node.style.left = `${sourcePoint.x}px`;
      node.style.top = `${sourcePoint.y}px`;
      node.style.width = `${length}px`;
      node.style.transform = `translateY(-50%) rotate(${angle}deg)`;
      return;
    }
    if (!point) {
      node.classList.add("hidden");
      return;
    }
    node.classList.remove("hidden");
    node.style.left = `${point.x}px`;
    node.style.top = `${point.y}px`;
  });
}

export function renderBattleVfx() {
  const layer = battleVfxLayer();
  if (!layer) return;
  const now = Date.now();
  const alive = [];
  state.activeBattleVfx.forEach((entry) => {
    if (entry.expiresAt <= now) {
      removeBattleVfxEntry(entry);
      return;
    }
    alive.push(entry);
    positionBattleVfxEntry(entry);
  });
  state.activeBattleVfx = alive;
  layer.classList.toggle("is-empty", !alive.length);
  clearBattleVfxCleanupTimer();
  if (!alive.length || typeof window.setTimeout !== "function") return;
  const delay = Math.max(32, Math.min(...alive.map((entry) => Math.max(0, entry.expiresAt - now))) + 20);
  ui.battleVfxCleanupHandle = window.setTimeout(() => {
    ui.battleVfxCleanupHandle = 0;
    renderBattleVfx();
  }, delay);
}

export function syncBattleVfxState({ hadBattle = false, boardChanged = false } = {}) {
  if (!state.battle) {
    clearBattleVfx();
    state.lastSeenVisualEventId = 0;
    return;
  }
  const events = visualEvents();
  const newestEventId = maxVisualEventId(events);
  if (!hadBattle || boardChanged) {
    clearBattleVfx();
    state.lastSeenVisualEventId = newestEventId;
    return;
  }
  const unseen = events.filter((event) => Number(event?.id || 0) > state.lastSeenVisualEventId);
  state.lastSeenVisualEventId = Math.max(state.lastSeenVisualEventId, newestEventId);
  if (!unseen.length) return;
  if (armyMarchWillAnimate()) {
    state.pendingArmyVfx = [...(state.pendingArmyVfx || []), ...unseen];
    return;
  }
  playArmyStrikeWaves(unseen);
}
