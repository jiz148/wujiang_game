const state = {
  heroes: [],
  battle: null,
  selectedUnitId: "",
  selectedActionCode: "",
  hoveredActionCode: "",
  stagedPayload: null,
  screen: "draft",
  draftSelection: {
    player1: "",
    player2: "",
  },
};

const $ = (id) => document.getElementById(id);

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw payload;
  }
  return payload;
}

function hasBattle() {
  return Boolean(state.battle);
}

function isGameOver() {
  return Boolean(state.battle?.winner);
}

function canInteract() {
  return Boolean(state.battle && state.screen === "battle" && !isGameOver());
}

function inputPlayer() {
  return state.battle?.input_player ?? 1;
}

function isChainMode() {
  return Boolean(state.battle?.pending_chain);
}

function activeBundles() {
  return state.battle?.active_units ?? [];
}

function bundleFor(unitId) {
  return activeBundles().find((entry) => entry.unit_id === unitId) || null;
}

function allUnits() {
  return state.battle?.units ?? [];
}

function unitById(unitId) {
  return allUnits().find((unit) => unit.id === unitId) || null;
}

function selectedUnit() {
  return unitById(state.selectedUnitId);
}

function stagedTarget() {
  return unitById(state.stagedPayload?.targetUnitId || "");
}

function screenHash(screen) {
  return screen === "battle" ? "#battle" : "#draft";
}

function syncHash(screen) {
  const url = `${window.location.pathname}${window.location.search}${screenHash(screen)}`;
  history.replaceState(null, "", url);
}

function setScreen(screen, { renderAfter = true } = {}) {
  const next = screen === "battle" && hasBattle() ? "battle" : "draft";
  state.screen = next;
  clearActionSelection();
  syncHash(next);
  if (renderAfter) render();
}

function syncScreen({ preferBattle = false } = {}) {
  const requested = window.location.hash.replace("#", "");
  if (requested === "battle" && hasBattle()) {
    state.screen = "battle";
    return;
  }
  if (requested === "draft") {
    state.screen = "draft";
    return;
  }
  state.screen = preferBattle && hasBattle() ? "battle" : "draft";
  syncHash(state.screen);
}

function ensureDraftSelection() {
  const codes = state.heroes.map((hero) => hero.code);
  if (!codes.length) {
    state.draftSelection.player1 = "";
    state.draftSelection.player2 = "";
    return;
  }
  if (!codes.includes(state.draftSelection.player1)) {
    state.draftSelection.player1 = codes[0];
  }
  if (!codes.includes(state.draftSelection.player2)) {
    state.draftSelection.player2 = codes[1] || codes[0];
  }
}

function ensureSelectedUnit() {
  if (!state.battle) {
    state.selectedUnitId = "";
    return;
  }
  if (isChainMode()) {
    state.selectedUnitId = state.battle.pending_chain?.current_unit_id || "";
    return;
  }
  if (isGameOver()) {
    if (unitById(state.selectedUnitId)) return;
    state.selectedUnitId = allUnits()[0]?.id || "";
    return;
  }
  if (!state.selectedUnitId) {
    return;
  }
  if (unitById(state.selectedUnitId)) {
    return;
  }
  const controllable = activeBundles().map((entry) => entry.unit_id);
  state.selectedUnitId = controllable[0] || allUnits()[0]?.id || "";
}

function clearActionSelection() {
  state.selectedActionCode = "";
  state.hoveredActionCode = "";
  state.stagedPayload = null;
}

function displayActions() {
  if (isGameOver()) return [];
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
      description: "放弃本次连锁，让原动作按原本声明继续结算。",
      preview: { cells: [], target_unit_ids: [], secondary_cells: [], requires_target: false },
      available: true,
    });
    return reactions;
  }
  return (bundle.actions.actions || []).filter((action) => {
    if (!action.available) return false;
    if (action.kind === "move" || action.kind === "attack") return true;
    return action.timing === "active";
  });
}

function actionByCode(code) {
  return displayActions().find((action) => action.code === code) || null;
}

function hoveredAction() {
  return actionByCode(state.hoveredActionCode) || actionByCode(state.selectedActionCode);
}

function positionKey(pos) {
  return `${pos.x},${pos.y}`;
}

function positionsToSet(cells = []) {
  return new Set(cells.map((cell) => `${cell.x},${cell.y}`));
}

function targetIdsToSet(targets = []) {
  return new Set(targets);
}

function unitIsSelectableTarget(unit) {
  return Boolean(unit)
    && !unit.cannot_be_targeted
    && !unit.statuses.some((status) => status.name === "隐身");
}

function previewCellsForTargetIds(targetIds = []) {
  return targetIds
    .map((id) => unitById(id)?.position)
    .filter(Boolean);
}

function currentPreview() {
  if (isGameOver()) {
    return { cellKeys: new Set(), targetIds: new Set(), secondaryCellKeys: new Set() };
  }
  const action = hoveredAction();
  if (!action) {
    return { cellKeys: new Set(), targetIds: new Set(), secondaryCellKeys: new Set() };
  }

  if (state.selectedActionCode === "mana_pull" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    return {
      cellKeys: positionsToSet(manaPullDestinations(target)),
      targetIds: new Set(target ? [target.id] : []),
      secondaryCellKeys: positionsToSet(target?.position ? [target.position] : []),
    };
  }

  const filteredTargetIds = (action.preview?.target_unit_ids || []).filter((id) => unitIsSelectableTarget(unitById(id)));
  const useDirectTargetCells = action.kind === "attack"
    || (action.preview?.requires_target && ["ally", "enemy", "unit"].includes(action.target_mode));
  const previewCells = useDirectTargetCells
    ? previewCellsForTargetIds(filteredTargetIds)
    : (action.preview?.cells || []);

  return {
    cellKeys: positionsToSet(previewCells),
    targetIds: targetIdsToSet(filteredTargetIds),
    secondaryCellKeys: positionsToSet(action.preview?.secondary_cells || []),
  };
}

function manaPullDestinations(target) {
  if (!target?.position || !state.battle) return [];
  const results = [];
  const directions = [
    [-1, -1], [-1, 0], [-1, 1],
    [0, -1], [0, 1],
    [1, -1], [1, 0], [1, 1],
  ];
  directions.forEach(([dx, dy]) => {
    let current = { ...target.position };
    for (let step = 0; step < 3; step += 1) {
      current = { x: current.x + dx, y: current.y + dy };
      if (
        current.x < 0 ||
        current.y < 0 ||
        current.x >= state.battle.board.width ||
        current.y >= state.battle.board.height
      ) {
        break;
      }
      const occupied = allUnits().some(
        (unit) => unit.position && unit.id !== target.id && unit.position.x === current.x && unit.position.y === current.y,
      );
      if (occupied) break;
      results.push(current);
    }
  });
  return results;
}

function actionNeedsTarget(action) {
  if (!action) return false;
  if (isChainMode()) return Boolean(action.preview?.requires_target);
  if (action.kind === "move" || action.kind === "attack") return true;
  return Boolean(action.preview?.requires_target);
}

function actionLabel(action) {
  if (action.kind === "move") return "移";
  if (action.kind === "attack") return "攻";
  if (action.kind === "chain_skip") return "否";
  if (action.action_name) return action.action_name.length <= 2 ? action.action_name : action.action_name.slice(0, 2);
  return action.name.length <= 2 ? action.name : action.name.slice(0, 2);
}

function actionTitle(action) {
  return action.action_name || action.name;
}

function actionTimingLabel(action) {
  if (action.kind === "chain_skip") return "放弃";
  const mapping = {
    active: "速度1",
    passive: "速度2",
    reaction: "速度2",
    instant: "速度3",
  };
  return mapping[action.timing] || `速度${action.chain_speed}`;
}

function renderScreens() {
  $("draft-screen").classList.toggle("hidden", state.screen !== "draft");
  $("battle-screen").classList.toggle("hidden", state.screen !== "battle");
}

function renderNavigation() {
  const canResume = hasBattle();
  const resumeLabel = isGameOver() ? "查看终局" : "继续当前对局";
  $("nav-draft").classList.toggle("hidden", state.screen !== "battle");
  $("nav-battle").classList.toggle("hidden", !(state.screen === "draft" && canResume));
  $("resume-game").classList.toggle("hidden", !(state.screen === "draft" && canResume));
  $("nav-battle").textContent = resumeLabel;
  $("resume-game").textContent = resumeLabel;
  $("start-game").textContent = hasBattle() ? "重新开始对局" : "开始对局";
}

function renderHeroCards() {
  ensureDraftSelection();
  const cards = $("hero-cards");
  const player1Select = $("player1-select");
  const player2Select = $("player2-select");
  cards.innerHTML = "";
  player1Select.innerHTML = "";
  player2Select.innerHTML = "";

  state.heroes.forEach((hero) => {
    const option1 = document.createElement("option");
    option1.value = hero.code;
    option1.textContent = hero.name;
    const option2 = option1.cloneNode(true);
    player1Select.append(option1);
    player2Select.append(option2);

    const card = document.createElement("article");
    card.className = "hero-card";
    card.innerHTML = `
      <h3>${hero.name}</h3>
      <div class="meta">${hero.role} / ${hero.attribute} / ${hero.race} / 等级 ${hero.level}</div>
      <div class="meta">攻 ${hero.stats.attack} · 守 ${hero.stats.defense} · 速 ${hero.stats.speed} · 范 ${hero.stats.attack_range} · 魔 ${hero.stats.mana}</div>
      <div class="text"><strong>技能：</strong>${hero.raw_skill_text}</div>
      <div class="text"><strong>特性：</strong>${hero.raw_trait_text}</div>
    `;
    cards.append(card);
  });

  player1Select.value = state.draftSelection.player1;
  player2Select.value = state.draftSelection.player2;
}

function renderHeader() {
  const pill = $("turn-pill");
  const caption = $("board-caption");
  if (!state.battle) {
    pill.textContent = "尚未开始";
    caption.textContent = "选择武将后开始对局。";
    return;
  }
  if (isGameOver()) {
    pill.textContent = `游戏结束 · 玩家 ${state.battle.winner} 获胜`;
    caption.textContent = `玩家 ${state.battle.winner} 已获胜，战场已锁定。`;
    return;
  }
  if (isChainMode()) {
    const current = state.battle.pending_chain?.current_unit_id
      ? unitById(state.battle.pending_chain.current_unit_id)?.name
      : "响应方";
    const sourceAction = state.battle.pending_chain?.queued_action?.display_name || "原动作";
    pill.textContent = `玩家 ${inputPlayer()} 连锁中`;
    caption.textContent = `等待 ${current} 响应【${sourceAction}】。`;
    return;
  }
  pill.textContent = `第 ${state.battle.round_number} 轮 · 玩家 ${inputPlayer()} 行动`;
  caption.textContent = "点击己方棋子，在棋子周围选择动作。";
}

function renderBoardAlert() {
  const node = $("board-alert");
  if (!state.battle || isGameOver() || state.screen !== "battle") {
    node.className = "board-alert hidden";
    node.innerHTML = "";
    return;
  }

  if (isChainMode()) {
    const chain = state.battle.pending_chain;
    const reactor = unitById(chain?.current_unit_id || "");
    const source = unitById(chain?.queued_action?.actor_id || "");
    node.className = "board-alert is-chain";
    node.innerHTML = `
      <strong>对方可连锁</strong>
      <span>${reactor?.name || "响应单位"} 正在决定是否对 ${source?.name || "来源单位"} 的【${chain?.queued_action?.display_name || "动作"}】进行连锁。</span>
    `;
    return;
  }

  const action = actionByCode(state.selectedActionCode);
  if (action?.code === "mana_pull" && !state.stagedPayload?.targetUnitId) {
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>魔力牵引</strong>
      <span>先点击被牵引的单位，再点击 1 到 3 格直线落点。</span>
    `;
    return;
  }

  if (action?.code === "mana_pull" && state.stagedPayload?.targetUnitId) {
    const target = stagedTarget();
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>魔力牵引</strong>
      <span>已选中 ${target?.name || "目标"}，请点击其 1 到 3 格的直线落点。</span>
    `;
    return;
  }

  if (action && actionNeedsTarget(action)) {
    node.className = "board-alert is-step";
    node.innerHTML = `
      <strong>${actionTitle(action)}</strong>
      <span>请点击棋盘上蓝色高亮的可选目标或范围。</span>
    `;
    return;
  }

  node.className = "board-alert hidden";
  node.innerHTML = "";
}

function renderBoard() {
  const board = $("board");
  board.innerHTML = "";
  const preview = currentPreview();
  const selected = selectedUnit();

  if (!state.battle) return;
  const chain = state.battle.pending_chain;
  const chainSource = unitById(chain?.queued_action?.actor_id || "");
  const chainReactor = unitById(chain?.current_unit_id || "");

  for (let y = 0; y < state.battle.board.height; y += 1) {
    for (let x = 0; x < state.battle.board.width; x += 1) {
      const cell = document.createElement("button");
      cell.className = "cell";
      cell.dataset.x = x;
      cell.dataset.y = y;
      cell.disabled = !canInteract();

      const occupant = allUnits().find(
        (unit) => unit.position && unit.position.x === x && unit.position.y === y,
      );

      const key = `${x},${y}`;
      if (preview.cellKeys.has(key)) cell.classList.add("is-preview");
      if (preview.secondaryCellKeys.has(key)) cell.classList.add("is-secondary");
      if (occupant && preview.targetIds.has(occupant.id)) cell.classList.add("is-target");
      if (selected?.position?.x === x && selected?.position?.y === y) cell.classList.add("is-selected");
      if (chainSource?.position?.x === x && chainSource?.position?.y === y) cell.classList.add("is-chain-source");
      if (chainReactor?.position?.x === x && chainReactor?.position?.y === y) cell.classList.add("is-chain-reactor");

      if (occupant) {
        const piece = document.createElement("div");
        piece.className = `piece player-${occupant.player_id}`;
        const statuses = [];
        if (occupant.total_shields) statuses.push(`盾${occupant.total_shields}`);
        if (occupant.dodge_charges) statuses.push(`闪${occupant.dodge_charges}`);
        if (occupant.statuses.length) statuses.push(occupant.statuses.map((status) => status.name).join("/"));
        piece.innerHTML = `
          <div class="piece-name">${occupant.name}</div>
          <div class="piece-meta">血 ${occupant.hp} · 魔 ${occupant.mana}</div>
          <div class="piece-status">${statuses.join(" · ") || "无状态"}</div>
        `;
        cell.append(piece);
      } else {
        const coord = document.createElement("div");
        coord.className = "coord";
        coord.textContent = `${x},${y}`;
        cell.append(coord);
      }

      cell.addEventListener("click", () => onBoardClick(x, y, occupant));
      board.append(cell);
    }
  }
}

function renderActionWheel() {
  const wheel = $("action-wheel");
  wheel.innerHTML = "";
  if (!canInteract()) return;
  const unit = selectedUnit();
  if (!unit?.position) return;

  const actions = displayActions();
  if (!actions.length) return;
  if (isChainMode() && state.battle.pending_chain?.current_unit_id !== unit.id) return;
  if (!isChainMode() && unit.player_id !== inputPlayer()) return;

  const selectedCell = [...document.querySelectorAll(".cell")].find(
    (cell) => Number(cell.dataset.x) === unit.position.x && Number(cell.dataset.y) === unit.position.y,
  );
  const stageRect = $("board-stage").getBoundingClientRect();
  const cellRect = selectedCell?.getBoundingClientRect();
  if (!selectedCell || !cellRect) return;

  const centerX = cellRect.left - stageRect.left + cellRect.width / 2;
  const centerY = cellRect.top - stageRect.top + cellRect.height / 2;
  const radius = 100;

  actions.forEach((action, index) => {
    const angle = (-90 + (360 / actions.length) * index) * (Math.PI / 180);
    const left = centerX + Math.cos(angle) * radius;
    const top = centerY + Math.sin(angle) * radius;
    const btn = document.createElement("button");
    btn.className = `action-btn ${state.selectedActionCode === action.code ? "is-selected" : ""}`;
    btn.style.left = `${left - 42}px`;
    btn.style.top = `${top - 23}px`;
    btn.innerHTML = `${actionLabel(action)}<small>${actionTimingLabel(action)}</small>`;
    btn.addEventListener("mouseenter", () => {
      state.hoveredActionCode = action.code;
      renderHoverCard(action);
      renderBoard();
    });
    btn.addEventListener("mouseleave", () => {
      state.hoveredActionCode = "";
      renderHoverCard();
      renderBoard();
    });
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      onActionClick(action);
    });
    wheel.append(btn);
  });
}

function renderHoverCard(action = hoveredAction()) {
  const card = $("hover-card");
  if (!action || isGameOver() || state.screen !== "battle") {
    card.classList.add("hidden");
    card.innerHTML = "";
    return;
  }
  card.classList.remove("hidden");
  card.innerHTML = `
    <strong>${actionTitle(action)}</strong>
    <p>${action.description}</p>
    <p>${actionTimingLabel(action)} · ${actionNeedsTarget(action) ? "需要选取目标" : "无需额外目标"}</p>
  `;
}

function renderSelectedCard() {
  const panel = $("selected-card");
  const unit = selectedUnit();
  if (!unit) {
    panel.textContent = isGameOver()
      ? `玩家 ${state.battle?.winner || ""} 已获胜，战场操作已锁定。`
      : "点击棋子后，这里会显示该武将的数值、技能与状态。";
    return;
  }
  const statuses = unit.statuses.map((status) => `${status.name}${status.duration ? `(${status.duration})` : ""}`).join("，") || "无";
  const traits = unit.traits.map((trait) => trait.name).join("，") || "无";
  panel.innerHTML = `
    <strong>${unit.name}</strong>
    <div class="statline">玩家 ${unit.player_id} · ${unit.role} / ${unit.attribute} / ${unit.race} / 等级 ${unit.level}</div>
    <div class="statline">攻 ${unit.stats.attack} · 守 ${unit.stats.defense} · 速 ${unit.stats.speed} · 范 ${unit.stats.attack_range} · 魔 ${unit.mana}</div>
    <div class="statline">血 ${unit.hp} / ${unit.max_hp} · 固定护盾 ${unit.shields} · 临时护盾 ${unit.temporary_shields} · 闪避 ${unit.dodge_charges}</div>
    <div class="statline"><strong>状态：</strong>${statuses}</div>
    <div class="statline"><strong>特性：</strong>${traits}</div>
    <div class="statline"><strong>原始技能：</strong>${unit.raw_skill_text}</div>
    <div class="statline"><strong>原始特性：</strong>${unit.raw_trait_text}</div>
  `;
}

function renderUnitStrip() {
  const strip = $("unit-strip");
  strip.innerHTML = "";
  if (isGameOver()) {
    const item = document.createElement("div");
    item.className = "queue-item";
    item.innerHTML = "<strong>对局已结束</strong><p>所有行动已锁定，可返回选将重新开始。</p>";
    strip.append(item);
    return;
  }
  activeBundles().forEach((entry) => {
    const unit = unitById(entry.unit_id);
    if (!unit) return;
    const btn = document.createElement("button");
    btn.className = `unit-chip ${state.selectedUnitId === unit.id ? "is-selected" : ""}`;
    btn.disabled = !canInteract();
    btn.innerHTML = `
      <div class="chip-main">${unit.name}</div>
      <div class="chip-sub">血 ${unit.hp} · 魔 ${unit.mana} · 攻 ${unit.stats.attack} / 守 ${unit.stats.defense}</div>
    `;
    btn.addEventListener("click", () => {
      if (!canInteract()) return;
      state.selectedUnitId = unit.id;
      clearActionSelection();
      render();
    });
    strip.append(btn);
  });
}

function renderChainPanel() {
  const panel = $("chain-panel");
  const caption = $("chain-caption");
  const skipBtn = $("skip-chain");
  panel.innerHTML = "";
  if (isGameOver()) {
    caption.textContent = "对局已结束，无法再进行连锁。";
    skipBtn.classList.add("hidden");
    return;
  }
  if (!isChainMode()) {
    caption.textContent = "当前没有待响应的连锁。";
    skipBtn.classList.add("hidden");
    return;
  }

  const chain = state.battle.pending_chain;
  const sourceUnit = unitById(chain.queued_action.actor_id);
  const currentReactor = unitById(chain.current_unit_id);
  const currentOptions = bundleFor(chain.current_unit_id)?.reactions.actions || [];
  caption.textContent = `原动作：${sourceUnit?.name || "未知单位"} 的 ${chain.queued_action.display_name}`;
  skipBtn.classList.remove("hidden");

  const sourceItem = document.createElement("div");
  sourceItem.className = "queue-item";
  sourceItem.innerHTML = `
    <strong>${sourceUnit?.name || "未知单位"} · ${chain.queued_action.display_name}</strong>
    <p>速度 ${chain.queued_action.speed}。当前等待 ${currentReactor?.name || "响应方"} 选择连锁动作。</p>
  `;
  panel.append(sourceItem);

  if (currentOptions.length) {
    const optionsItem = document.createElement("div");
    optionsItem.className = "queue-item current-options";
    optionsItem.innerHTML = `
      <strong>${currentReactor?.name || "当前单位"} 可用连锁</strong>
      <p>${currentOptions.map((action) => `${action.action_name}（速度${action.chain_speed}）`).join(" / ")} / 不连锁</p>
    `;
    panel.append(optionsItem);
  }

  (chain.chosen_reactions || []).slice().reverse().forEach((reaction) => {
    const unit = unitById(reaction.actor_id);
    const item = document.createElement("div");
    item.className = "queue-item";
    item.innerHTML = `
      <strong>${unit?.name || "未知单位"} · ${reaction.display_name}</strong>
      <p>将以更快速度先于原动作结算。</p>
    `;
    panel.append(item);
  });
}

function renderLogs() {
  const logs = $("logs");
  logs.innerHTML = "";
  (state.battle?.logs || []).slice().reverse().forEach((line) => {
    const item = document.createElement("div");
    item.className = "log";
    item.textContent = line;
    logs.append(item);
  });
}

function renderGameOverOverlay() {
  const overlay = $("game-over-overlay");
  const title = $("game-over-title");
  const text = $("game-over-text");
  if (!state.battle || !isGameOver() || state.screen !== "battle") {
    overlay.classList.add("hidden");
    return;
  }
  title.textContent = "游戏结束";
  text.textContent = `玩家 ${state.battle.winner} 已获胜。战场上的行动与连锁都已锁定，你可以回到选将页面开始新的一局。`;
  overlay.classList.remove("hidden");
}

function renderMessage() {
  const node = $("message");
  if (!state.battle) {
    node.textContent = "尚未开始对局。";
    return;
  }
  if (isGameOver()) {
    node.textContent = `玩家 ${state.battle.winner} 已获胜。战场已锁定，可回到选将重新开始。`;
    return;
  }
  if (state.selectedActionCode === "mana_pull" && !state.stagedPayload?.targetUnitId) {
    node.textContent = "魔力牵引分两步：先选单位，再选落点。";
    return;
  }
  if (state.stagedPayload?.targetUnitId && state.selectedActionCode === "mana_pull") {
    node.textContent = `已选中 ${stagedTarget()?.name || "被牵引目标"}，请点击蓝色高亮落点。`;
    return;
  }
  if (isChainMode()) {
    const current = unitById(state.battle.pending_chain?.current_unit_id || "");
    const source = unitById(state.battle.pending_chain?.queued_action?.actor_id || "");
    const actionName = state.battle.pending_chain?.queued_action?.display_name || "原动作";
    node.textContent = `${current?.name || "当前单位"} 可以对 ${source?.name || "对方单位"} 的【${actionName}】进行连锁，点击其周围动作按钮或放弃连锁。`;
    return;
  }
  const action = actionByCode(state.selectedActionCode);
  if (action) {
    node.textContent = `已选择【${actionTitle(action)}】。${actionNeedsTarget(action) ? "请在棋盘上点击蓝色高亮目标。" : "再次点击会立即结算。"} `;
    return;
  }
  node.textContent = `当前由玩家 ${inputPlayer()} 操作。`;
}

function render() {
  if (isGameOver()) clearActionSelection();
  document.body.classList.toggle("battle-mode", state.screen === "battle");
  ensureDraftSelection();
  ensureSelectedUnit();
  renderScreens();
  renderNavigation();
  renderHeroCards();
  renderHeader();
  renderMessage();
  renderBoard();
  renderBoardAlert();
  renderActionWheel();
  renderHoverCard();
  renderSelectedCard();
  renderUnitStrip();
  renderChainPanel();
  renderLogs();
  renderGameOverOverlay();
  $("end-turn").disabled = !canInteract() || isChainMode();
  $("skip-chain").disabled = !canInteract() || !isChainMode();
}

async function refreshState() {
  const payload = await fetchJson("/api/state");
  state.heroes = payload.heroes;
  state.battle = payload.battle;
  ensureDraftSelection();
  syncScreen({ preferBattle: Boolean(state.battle) });
  ensureSelectedUnit();
  render();
}

async function startGame() {
  state.draftSelection.player1 = $("player1-select").value;
  state.draftSelection.player2 = $("player2-select").value;
  const payload = await fetchJson("/api/new-game", {
    method: "POST",
    body: JSON.stringify({
      player1: state.draftSelection.player1,
      player2: state.draftSelection.player2,
    }),
  });
  state.heroes = payload.heroes;
  state.battle = payload.battle;
  clearActionSelection();
  state.selectedUnitId = payload.battle?.active_units?.[0]?.unit_id || "";
  syncScreen({ preferBattle: true });
  setScreen("battle", { renderAfter: false });
  ensureSelectedUnit();
  render();
}

async function performAction(payload) {
  try {
    const response = await fetchJson("/api/action", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.heroes = response.heroes;
    state.battle = response.battle;
    clearActionSelection();
    ensureSelectedUnit();
    render();
  } catch (error) {
    if (error.state) {
      state.heroes = error.state.heroes;
      state.battle = error.state.battle;
      ensureSelectedUnit();
      render();
    }
    $("message").textContent = error.error || "执行失败。";
  }
}

function onActionClick(action) {
  if (!canInteract()) return;
  if (isChainMode()) {
    if (action.code === "chain_skip") {
      performAction({ type: "chain_skip" });
      return;
    }
    if (actionNeedsTarget(action)) {
      if (state.selectedActionCode === action.code) {
        clearActionSelection();
      } else {
        state.selectedActionCode = action.code;
        state.hoveredActionCode = action.code;
        state.stagedPayload = null;
      }
      render();
      return;
    }
    performAction({
      type: "chain_react",
      unit_id: state.selectedUnitId,
      action_code: action.code,
    });
    return;
  }

  if (!actionNeedsTarget(action)) {
    if (action.kind === "skill") {
      performAction({
        type: "skill",
        unit_id: state.selectedUnitId,
        skill_code: action.code,
      });
      return;
    }
    return;
  }

  if (state.selectedActionCode === action.code) {
    clearActionSelection();
  } else {
    state.selectedActionCode = action.code;
    state.hoveredActionCode = action.code;
    state.stagedPayload = null;
  }
  render();
}

function onBoardClick(x, y, occupant) {
  if (!canInteract()) return;
  const preview = currentPreview();
  const key = positionKey({ x, y });
  const canUseCell = preview.cellKeys.has(key);
  const canUseUnit = occupant ? preview.targetIds.has(occupant.id) : false;

  if (!state.selectedActionCode) {
    state.selectedUnitId = occupant?.id || "";
    clearActionSelection();
    render();
    return;
  }

  if (isChainMode()) {
    const action = actionByCode(state.selectedActionCode);
    if (!action || !actionNeedsTarget(action)) return;
    if (!canUseCell && !canUseUnit) return;
    const payload = {
      type: "chain_react",
      unit_id: state.selectedUnitId,
      action_code: action.code,
    };
    if (occupant && canUseUnit) {
      payload.target_unit_id = occupant.id;
    } else if (canUseCell) {
      payload.x = x;
      payload.y = y;
    }
    performAction(payload);
    return;
  }

  const action = actionByCode(state.selectedActionCode);
  if (!action) {
    clearActionSelection();
    render();
    return;
  }

  if (!canUseCell && !canUseUnit) {
    clearActionSelection();
    state.selectedUnitId = occupant?.id || "";
    render();
    return;
  }

  if (action.code === "move") {
    performAction({
      type: "move",
      unit_id: state.selectedUnitId,
      x,
      y,
    });
    return;
  }

  if (action.code === "attack") {
    performAction({
      type: "attack",
      unit_id: state.selectedUnitId,
      target_unit_id: occupant.id,
    });
    return;
  }

  if (action.code === "mana_pull") {
    if (!state.stagedPayload?.targetUnitId) {
      state.stagedPayload = { targetUnitId: occupant.id };
      render();
      return;
    }
    performAction({
      type: "skill",
      unit_id: state.selectedUnitId,
      skill_code: action.code,
      target_unit_id: state.stagedPayload.targetUnitId,
      dest_x: x,
      dest_y: y,
    });
    return;
  }

  if (action.preview?.requires_target) {
    if (action.target_mode === "cell" || action.kind === "move") {
      performAction({
        type: "skill",
        unit_id: state.selectedUnitId,
        skill_code: action.code,
        x,
        y,
      });
      return;
    }
    performAction({
      type: "skill",
      unit_id: state.selectedUnitId,
      skill_code: action.code,
      target_unit_id: occupant.id,
    });
  }
}

function bindEvents() {
  $("player1-select").addEventListener("change", (event) => {
    state.draftSelection.player1 = event.target.value;
  });
  $("player2-select").addEventListener("change", (event) => {
    state.draftSelection.player2 = event.target.value;
  });
  $("start-game").addEventListener("click", startGame);
  $("resume-game").addEventListener("click", () => setScreen("battle"));
  $("nav-draft").addEventListener("click", () => setScreen("draft"));
  $("nav-battle").addEventListener("click", () => setScreen("battle"));
  $("game-over-back").addEventListener("click", () => setScreen("draft"));
  $("end-turn").addEventListener("click", () => {
    if (!canInteract()) return;
    performAction({ type: "end_turn" });
  });
  $("skip-chain").addEventListener("click", () => {
    if (!canInteract()) return;
    performAction({ type: "chain_skip" });
  });
  window.addEventListener("hashchange", () => {
    syncScreen({ preferBattle: Boolean(state.battle) });
    render();
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await refreshState();
});
