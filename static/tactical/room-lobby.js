// 遭遇战大厅的弹窗：房间设置、自动配置、选将、武将详情。
//
// 建房页此前把模式、席位数、席位卡和一整座武将库摊在同一屏上。每样东西都只分到
// 一点地方，谁也看不清；而武将库那几十张卡片会把席位挤到屏幕之外，偏偏"谁坐在
// 哪、带了谁"才是这一页真正要回答的问题。
//
// 现在页面上只留席位。设置是开局前调一次的东西，选将是一次挑完就走的动作，
// 两者都点开才出现。武将名单也只给名字和等级：够用来找人；看数值和技能再点开
// 详情。一上来就摊开全部档案，等于什么都没突出。
import { $ } from '../core/dom.js';
import { fetchJson, hasRoom, viewerPlayerId } from '../core/net.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { applyRoomPayload, autoConfigureRoom, availableRoomModes, isRandomRoomMode, reportRoomError, selectRoomHero, setRandomRosterSize, setRoomBoardSize, setRoomHeroLimit, setRoomMode, setRoomSeatCount, setRoomTurnTimeout } from '../tactical/room-api.js';
import { randomRoomRosterSize, seatHeroCount, seatIdentityLabel, setRoomEditSeat } from '../tactical/targeting.js';

function heroByCode(code) {
  return (state.heroes || []).find((hero) => hero.code === code) || null;
}

function heroName(code) {
  return heroByCode(code)?.name || code;
}

function seatById(seatId) {
  const id = Number(seatId || 0);
  if (!id) return null;
  return (state.room?.seats || []).find((seat) => seat.player_id === id) || null;
}

/** 真人点了准备之后，这张卡就锁住：改阵容得先取消准备。AI 是自动准备，房主仍要能配。 */
export function isSeatLocked(seat) {
  return Boolean(seat?.ready && seat.is_human);
}

/** 这个席位的阵容归谁管：自己的席位，或房主手上的 AI 席位。准备锁定另算。 */
export function canManageSeatRoster(seat) {
  if (!seat || !hasRoom() || state.room?.status !== "lobby") return false;
  if (state.room?.launch_context && state.room.launch_context.allow_roster_edit === false) return false;
  if (isRandomRoomMode()) return false;
  if (seat.player_id === viewerPlayerId()) return true;
  return Boolean(state.room?.viewer_is_host && seat.is_ai);
}

/** 遭遇战配兵：随机模式下也可以加士兵，战役房间则跟阵容一样锁住。 */
export function canManageSeatArmy(seat) {
  if (!seat || !hasRoom() || state.room?.status !== "lobby") return false;
  if (state.room?.launch_context && state.room.launch_context.allow_roster_edit === false) return false;
  if (seat.player_id === viewerPlayerId()) return true;
  return Boolean(state.room?.viewer_is_host && seat.is_ai);
}

/** 能不能给这个席位改阵容。准备之后只是锁住，叉号还在，只是点不了。 */
export function canConfigureSeat(seat) {
  return canManageSeatRoster(seat) && !isSeatLocked(seat);
}

export function roomHeroLimit() {
  const limit = Number(state.room?.hero_limit || 0);
  return limit > 0 ? limit : 0;
}

export function seatAtHeroLimit(seat) {
  const limit = roomHeroLimit();
  if (!limit) return false;
  return Number(seat?.hero_total_count || 0) >= limit;
}

export function canEditRoomSetup() {
  if (state.room?.launch_context && state.room.launch_context.allow_roster_edit === false) return false;
  return Boolean(hasRoom() && state.room?.viewer_is_host && state.room?.status === "lobby");
}

export function seatHeroEntries(seat) {
  return Object.entries(seat?.hero_counts || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([code, count]) => ({ code, name: heroName(code), count: Number(count) }));
}

function rosterExactlyMatches(seat, heroCodes) {
  if (!seat || !Array.isArray(heroCodes)) return false;
  const expected = new Map();
  heroCodes.forEach((code) => expected.set(code, (expected.get(code) || 0) + 1));
  const selected = seatHeroEntries(seat);
  if (selected.length !== expected.size) return false;
  return selected.every((entry) => entry.count === Number(expected.get(entry.code) || 0));
}

export function openRoomSetup() {
  if (!canEditRoomSetup()) return;
  const currentLimit = Number(state.room.hero_limit || 0);
  state.roomSetupDraft = {
    mode: String(state.room.mode || ""),
    seatCount: String(state.room.seat_count || 2),
    randomRosterSize: String(randomRoomRosterSize()),
    heroLimitEnabled: currentLimit > 0,
    heroLimit: String(currentLimit > 0 ? currentLimit : 5),
    turnTimeout: String(Number(state.room.turn_timeout_seconds ?? 0)),
    boardWidth: String(Number(state.room.board_width || 10)),
    boardHeight: String(Number(state.room.board_height || 10)),
  };
  state.roomSetupOpen = true;
  render();
}

export function closeRoomSetup() {
  state.roomSetupOpen = false;
  state.roomSetupDraft = null;
  render();
}

function clampAutoConfigureCount(value) {
  const raw = Number.parseInt(value, 10);
  if (!Number.isFinite(raw)) return 3;
  return Math.max(1, Math.min(12, raw));
}

function clampAutoConfigurePoints(value) {
  const raw = Number.parseInt(value, 10);
  if (!Number.isFinite(raw)) return 15;
  return Math.max(10, Math.min(50, raw));
}

export function openAutoConfigure() {
  if (!canEditRoomSetup()) return;
  state.autoConfigureDraft = {
    method: "count",
    count: "3",
    points: "15",
    allowDuplicates: false,
  };
  state.autoConfigureOpen = true;
  render();
}

export function closeAutoConfigure() {
  state.autoConfigureOpen = false;
  state.autoConfigureDraft = null;
  render();
}

export function updateAutoConfigureDraft(field, value) {
  if (!state.autoConfigureDraft) return;
  if (field === "allowDuplicates") {
    state.autoConfigureDraft.allowDuplicates = Boolean(value);
    return;
  }
  if (field === "method") {
    state.autoConfigureDraft.method = value === "points" ? "points" : "count";
    return;
  }
  state.autoConfigureDraft[field] = String(value);
}

export async function confirmAutoConfigure() {
  const draft = state.autoConfigureDraft;
  if (!draft || !canEditRoomSetup()) {
    closeAutoConfigure();
    return;
  }
  const method = draft.method === "points" ? "points" : "count";
  const count = clampAutoConfigureCount(draft.count);
  const points = clampAutoConfigurePoints(draft.points);
  const allowDuplicates = Boolean(draft.allowDuplicates);
  state.autoConfigureOpen = false;
  state.autoConfigureDraft = null;
  await autoConfigureRoom({ method, count, points, allowDuplicates });
  render();
}

export function renderAutoConfigureDialog() {
  const modal = $("auto-configure-dialog");
  if (!modal) return;
  const open = Boolean(state.autoConfigureOpen && state.autoConfigureDraft && canEditRoomSetup());
  modal.classList.toggle("hidden", !open);
  modal.setAttribute("aria-hidden", open ? "false" : "true");
  if (!open) {
    state.autoConfigureOpen = false;
    state.autoConfigureDraft = null;
    return;
  }
  const draft = state.autoConfigureDraft;
  const methodCount = $("auto-configure-method-count");
  const methodPoints = $("auto-configure-method-points");
  if (methodCount) methodCount.checked = draft.method !== "points";
  if (methodPoints) methodPoints.checked = draft.method === "points";
  const countControl = $("auto-configure-count-control");
  const pointsControl = $("auto-configure-points-control");
  countControl?.classList.toggle("hidden", draft.method === "points");
  pointsControl?.classList.toggle("hidden", draft.method !== "points");
  const countInput = $("auto-configure-count-input");
  if (countInput && document.activeElement !== countInput) countInput.value = draft.count;
  const pointsInput = $("auto-configure-points-input");
  if (pointsInput && document.activeElement !== pointsInput) pointsInput.value = draft.points;
  const duplicates = $("auto-configure-allow-duplicates");
  if (duplicates) duplicates.checked = Boolean(draft.allowDuplicates);
}

/**
 * 逐项提交改动。
 *
 * 席位数一改就会真的增删席位，模式一换就会清空所有人的选将。这些都不该在拖动
 * 数字框的中途发生，所以草稿留在本地，按下确定才一次性落地。
 */
export async function confirmRoomSetup() {
  const draft = state.roomSetupDraft;
  if (!draft || !canEditRoomSetup()) {
    closeRoomSetup();
    return;
  }
  const nextMode = draft.mode;
  const nextSeatCount = draft.seatCount;
  const nextRosterSize = draft.randomRosterSize;
  const nextHeroLimit = draft.heroLimitEnabled
    ? Math.max(1, Math.min(20, Number.parseInt(draft.heroLimit, 10) || 1))
    : 0;
  state.roomSetupOpen = false;
  state.roomSetupDraft = null;
  if (nextMode && nextMode !== state.room.mode) await setRoomMode(nextMode);
  if (Number(nextSeatCount) !== Number(state.room?.seat_count || 0)) await setRoomSeatCount(nextSeatCount);
  if (isRandomRoomMode() && Number(nextRosterSize) !== randomRoomRosterSize()) {
    await setRandomRosterSize(nextRosterSize);
  }
  if (nextHeroLimit !== Number(state.room?.hero_limit || 0)) await setRoomHeroLimit(nextHeroLimit);
  const nextTurnTimeout = [0, 30, 60, 120].includes(Number.parseInt(draft.turnTimeout, 10))
    ? Number.parseInt(draft.turnTimeout, 10)
    : 0;
  if (nextTurnTimeout !== Number(state.room?.turn_timeout_seconds ?? 0)) await setRoomTurnTimeout(nextTurnTimeout);
  const clampBoard = (value) => Math.max(6, Math.min(100, Number.parseInt(value, 10) || 10));
  const nextWidth = clampBoard(draft.boardWidth);
  const nextHeight = clampBoard(draft.boardHeight);
  if (
    nextWidth !== Number(state.room?.board_width || 10)
    || nextHeight !== Number(state.room?.board_height || 10)
  ) {
    await setRoomBoardSize(nextWidth, nextHeight);
  }
  render();
}

export function updateRoomSetupDraft(field, value) {
  if (!state.roomSetupDraft) return;
  state.roomSetupDraft[field] = String(value);
}

export function renderRoomSetupDialog() {
  const modal = $("room-setup-dialog");
  if (!modal) return;
  const open = Boolean(state.roomSetupOpen && state.roomSetupDraft && canEditRoomSetup());
  modal.classList.toggle("hidden", !open);
  modal.setAttribute("aria-hidden", open ? "false" : "true");
  if (!open) {
    state.roomSetupOpen = false;
    state.roomSetupDraft = null;
    return;
  }
  const draft = state.roomSetupDraft;
  // 轮询会把这个函数再跑一遍。展开着的下拉不能重建，否则选项列表会在指针底下收回去。
  const modeSelect = $("room-mode-select");
  if (modeSelect && document.activeElement !== modeSelect) {
    modeSelect.replaceChildren();
    availableRoomModes().forEach((mode) => {
      const option = document.createElement("option");
      option.value = mode.code;
      option.textContent = mode.name;
      modeSelect.append(option);
    });
    modeSelect.value = draft.mode;
  }
  const seatCountInput = $("room-seat-count-input");
  if (seatCountInput) {
    seatCountInput.min = String(state.room.seat_count_min || 2);
    seatCountInput.max = String(state.room.seat_count_max || 6);
    if (document.activeElement !== seatCountInput) seatCountInput.value = draft.seatCount;
  }
  // 随机模式才有"每队随机几个"可言，标准模式下这一项没有意义。
  const randomControl = $("random-roster-size-control");
  const randomInput = $("random-roster-size-input");
  randomControl?.classList.toggle("hidden", draft.mode !== "random");
  if (randomInput && document.activeElement !== randomInput) {
    randomInput.value = draft.randomRosterSize;
  }
  const limitEnabled = $("room-hero-limit-enabled");
  if (limitEnabled) limitEnabled.checked = Boolean(draft.heroLimitEnabled);
  const limitControl = $("room-hero-limit-control");
  const limitInput = $("room-hero-limit-input");
  limitControl?.classList.toggle("hidden", !draft.heroLimitEnabled);
  if (limitInput && document.activeElement !== limitInput) {
    limitInput.value = draft.heroLimit;
  }
  const timeoutSelect = $("room-turn-timeout-select");
  if (timeoutSelect && document.activeElement !== timeoutSelect) {
    timeoutSelect.value = String(draft.turnTimeout ?? "0");
  }
  const widthInput = $("room-board-width-input");
  if (widthInput && document.activeElement !== widthInput) {
    widthInput.value = String(draft.boardWidth || "10");
  }
  const heightInput = $("room-board-height-input");
  if (heightInput && document.activeElement !== heightInput) {
    heightInput.value = String(draft.boardHeight || "10");
  }
}

export function openHeroPicker(seatId) {
  const seat = seatById(seatId);
  if (!canConfigureSeat(seat)) return;
  setRoomEditSeat(seat.player_id);
  state.heroPickerSeatId = seat.player_id;
  state.heroSearchQuery = "";
  render();
  window.requestAnimationFrame?.(() => $("hero-search")?.focus());
}

export function closeHeroPicker() {
  state.heroPickerSeatId = null;
  render();
}

export function heroPickerSeat() {
  return seatById(state.heroPickerSeatId);
}

function heroSortValue(hero, key) {
  if (key === "name") return String(hero.name || "");
  if (key === "level") return Number(hero.level || 0);
  return Number(hero.stats?.[key] || 0);
}

/** 按名称搜索、按任一属性排序后的名单。 */
export function heroPickerList() {
  const query = String(state.heroSearchQuery || "").trim().toLowerCase();
  const key = String(state.heroSortKey || "name");
  const direction = state.heroSortDesc ? -1 : 1;
  return (state.heroes || [])
    .filter((hero) => !query || String(hero.name || "").toLowerCase().includes(query))
    .sort((left, right) => {
      const a = heroSortValue(left, key);
      const b = heroSortValue(right, key);
      if (typeof a === "string") return String(a).localeCompare(String(b), "zh-CN") * direction;
      // 数值相等时再按名称定序，否则同等级的一批人每次渲染的先后都可能不同。
      if (a !== b) return (a - b) * direction;
      return String(left.name || "").localeCompare(String(right.name || ""), "zh-CN");
    });
}

async function applyRecommendedRoster(rosterCode, seatId) {
  if (!hasRoom() || !state.playerToken) return;
  try {
    const payload = await fetchJson("/api/rooms/apply-recommended-roster", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room.room_id,
        player_token: state.playerToken,
        roster_code: rosterCode,
        seat_id: seatId != null ? Number(seatId) : undefined,
      }),
    });
    applyRoomPayload(payload, { preserveScreen: true });
    render();
  } catch (error) {
    reportRoomError(error.error || "应用推荐阵容失败。");
  }
}

function renderHeroPickerRosters(seat) {
  const host = $("hero-picker-rosters");
  if (!host) return;
  const rosters = state.onboarding?.recommended_rosters || [];
  host.replaceChildren();
  host.classList.toggle("hidden", !rosters.length);
  rosters.forEach((roster) => {
    const applied = rosterExactlyMatches(seat, roster.hero_codes);
    const overLimit = Boolean(roomHeroLimit() && (roster.hero_codes || []).length > roomHeroLimit());
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `hero-roster-chip${applied ? " is-applied" : ""}`;
    chip.textContent = roster.name;
    chip.title = overLimit
      ? `超过每席 ${roomHeroLimit()} 名上限`
      : roster.hero_codes.map(heroName).join(" + ");
    chip.disabled = overLimit;
    chip.setAttribute("aria-pressed", applied ? "true" : "false");
    chip.addEventListener("click", () => {
      if (chip.disabled) return;
      applyRecommendedRoster(roster.code, seat.player_id);
    });
    host.append(chip);
  });
}

function renderHeroPickerList(seat) {
  const list = $("hero-picker-list");
  if (!list) return;
  list.replaceChildren();
  const heroes = heroPickerList();
  if (!heroes.length) {
    const empty = document.createElement("p");
    empty.className = "hero-picker__empty";
    empty.textContent = "没有匹配的武将。";
    list.append(empty);
    return;
  }
  heroes.forEach((hero) => {
    const count = seatHeroCount(seat, hero.code);
    const row = document.createElement("div");
    row.className = `hero-row${count > 0 ? " is-selected" : ""}`;
    row.dataset.heroCode = hero.code;

    // 一行一张标签：左边是名字和等级，右边是攻守速范魔。能按属性排序，
    // 就得让人在名单上直接看到这些数，不必先点进详情。
    const tag = document.createElement("button");
    tag.type = "button";
    tag.className = "hero-row__tag";
    tag.dataset.heroDetail = hero.code;
    const name = document.createElement("strong");
    name.className = "hero-row__name";
    name.textContent = hero.name;
    const level = createHeroLevelTag(hero.level);
    const stats = document.createElement("span");
    stats.className = "hero-row__stats";
    stats.textContent = heroStatLine(hero);
    tag.append(name, level, stats);
    tag.addEventListener("click", () => openHeroDetail(hero.code));

    const counter = document.createElement("div");
    counter.className = "hero-row__counter";
    const minus = document.createElement("button");
    minus.type = "button";
    minus.className = "hero-row__step";
    minus.textContent = "-";
    minus.setAttribute("aria-label", `减少 ${hero.name}`);
    minus.disabled = count <= 0;
    minus.addEventListener("click", () => selectRoomHero(hero.code, -1, seat.player_id));
    const value = document.createElement("span");
    value.className = "hero-row__count";
    value.textContent = String(count);
    const plus = document.createElement("button");
    plus.type = "button";
    plus.className = "hero-row__step";
    plus.textContent = "+";
    plus.setAttribute("aria-label", `增加 ${hero.name}`);
    plus.disabled = seatAtHeroLimit(seat);
    plus.addEventListener("click", () => selectRoomHero(hero.code, 1, seat.player_id));
    counter.append(minus, value, plus);

    row.append(tag, counter);
    list.append(row);
  });
}

export function renderHeroPicker() {
  const modal = $("hero-picker");
  if (!modal) return;
  const seat = heroPickerSeat();
  const open = canConfigureSeat(seat);
  modal.classList.toggle("hidden", !open);
  modal.setAttribute("aria-hidden", open ? "false" : "true");
  if (!open) {
    state.heroPickerSeatId = null;
    return;
  }
  const title = $("hero-picker-title");
  if (title) title.textContent = `${seatIdentityLabel(seat)} · 选择武将`;
  const search = $("hero-search");
  if (search && search.value !== state.heroSearchQuery) search.value = state.heroSearchQuery;
  const sort = $("hero-sort");
  if (sort) sort.value = String(state.heroSortKey || "name");
  const order = $("hero-sort-order");
  if (order) {
    order.textContent = state.heroSortDesc ? "降序" : "升序";
    order.setAttribute("aria-pressed", state.heroSortDesc ? "true" : "false");
  }
  renderHeroPickerRosters(seat);
  renderHeroPickerList(seat);
}

export function openHeroDetail(heroCode) {
  state.heroDetailCode = String(heroCode || "");
  render();
}

export function closeHeroDetail() {
  state.heroDetailCode = "";
  render();
}

function createHeroLevelTag(level) {
  const tag = document.createElement("span");
  tag.className = "hero-level-tag";
  tag.textContent = `Lv ${level}`;
  return tag;
}

function heroStatValues(hero) {
  const stats = hero?.stats || {};
  return [stats.attack, stats.defense, stats.speed, stats.attack_range, stats.mana]
    .map((value) => String(Number(value || 0)));
}

function heroStatLine(hero) {
  return heroStatValues(hero).join("  |  ");
}

function appendDetailLine(host, label, value) {
  const row = document.createElement("div");
  row.className = "hero-detail__row";
  const caption = document.createElement("span");
  caption.className = "hero-detail__label";
  caption.textContent = label;
  const body = document.createElement("span");
  body.className = "hero-detail__value";
  body.textContent = value;
  row.append(caption, body);
  host.append(row);
}

export function renderHeroDetail() {
  const modal = $("hero-detail");
  if (!modal) return;
  const hero = heroByCode(state.heroDetailCode);
  modal.classList.toggle("hidden", !hero);
  modal.setAttribute("aria-hidden", hero ? "false" : "true");
  const level = $("hero-detail-level");
  if (!hero) {
    level?.classList.add("hidden");
    return;
  }
  const title = $("hero-detail-title");
  if (title) title.textContent = hero.name;
  if (level) {
    level.textContent = `Lv ${hero.level}`;
    level.classList.remove("hidden");
  }
  const body = $("hero-detail-body");
  if (!body) return;
  body.replaceChildren();
  appendDetailLine(body, "定位", `${hero.role} / ${hero.attribute} / ${hero.race}`);
  appendDetailLine(
    body,
    "数值",
    `攻 ${hero.stats.attack} · 守 ${hero.stats.defense} · 速 ${hero.stats.speed} · 范 ${hero.stats.attack_range} · 魔 ${hero.stats.mana}`,
  );
  appendDetailLine(body, "技能", hero.raw_skill_text || "无");
  appendDetailLine(body, "特性", hero.raw_trait_text || "无");
}
