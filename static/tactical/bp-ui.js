import { $ } from '../core/dom.js';
import { state } from '../core/state.js';
import { bpChoose, openBpAssign, setBpTeamAi, startRoomBattle } from './room-api.js';
import { hideHeroHover, renderHeroDetails } from './hero-hover.js';

function element(tag, text, className = '') {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function option(value, label) {
  const node = element('option', label);
  node.value = String(value);
  return node;
}

function heroName(code) {
  return (state.heroes || []).find((hero) => hero.code === code)?.name || code;
}

function teamName(team) {
  return Number(team) === 1 ? '红队' : '蓝队';
}

function createHeroDetails() {
  const panel = element('aside', '', 'bp-hero-details');
  panel.dataset.bpHeroDetails = 'true';
  panel.setAttribute('aria-label', '武将信息');
  panel.append(element('h4', '武将信息'));
  const body = element('div', '', 'bp-hero-details__body');
  panel.append(body);
  let activeAnchor = null;
  const reset = (anchor = null) => {
    if (anchor && activeAnchor !== anchor) return;
    activeAnchor = null;
    panel.classList.remove?.("has-hero");
    body.replaceChildren(element('p', '点一名武将查看资料。'));
  };
  const show = (anchor, hero) => {
    if (!hero) return reset(anchor);
    activeAnchor = anchor;
    panel.classList.add("has-hero");
    renderHeroDetails(body, hero);
  };
  const bind = (anchor, heroOrGetter) => {
    const reveal = () => show(anchor, typeof heroOrGetter === 'function' ? heroOrGetter() : heroOrGetter);
    anchor.addEventListener('mouseenter', reveal);
    anchor.addEventListener('focus', reveal);
    anchor.addEventListener('mouseleave', () => reset(anchor));
    anchor.addEventListener('blur', () => reset(anchor));
  };
  reset();
  return { panel, bind, reset, show };
}

function heroByCode(code) {
  return (state.heroes || []).find((hero) => hero.code === code);
}

let selectedHeroCode = "";
let selectedStep = null;
const collapsedLevels = new Set();

function seatLabel(team, seats) {
  const people = (seats || []).filter((seat) => Number(seat.team_id) === team && (seat.name || seat.occupied));
  if (people.length === 1) return people[0].name || teamName(team);
  return teamName(team);
}

function syncBpSelection(bp) {
  const step = Number(bp?.step_index ?? -1);
  if (selectedStep !== step) {
    selectedStep = step;
    selectedHeroCode = "";
  }
}

function heroTag(action, details, kind) {
  const tag = element("button", action.hero_name || heroName(action.hero_code), `bp-tag is-${kind}`);
  tag.type = "button";
  const hero = heroByCode(action.hero_code);
  tag.addEventListener("click", () => {
    if (!hero) return;
    selectedHeroCode = hero.code;
    details.show(tag, hero);
    details.panel.classList.add("has-hero");
  });
  return tag;
}

function sideColumn(team, bp, seats, details) {
  const kind = bp.current_kind === "ban" ? "ban" : "pick";
  const active = Number(bp.current_team_id) === team && bp.phase === "draft";
  const col = element("section", "", `bp-side is-team-${team}${active ? ` is-active is-${kind}` : ""}`);
  const head = element("div", "", "bp-side__rows");
  const title = element("div", "", "bp-side__row");
  const name = seatLabel(team, seats);
  title.append(element("h4", name));
  if (active) title.append(element("span", kind === "ban" ? "禁用" : "选取", `bp-side__duty is-${kind}`));
  const stats = element("div", "", "bp-side__row");
  if (bp.wins) {
    stats.append(element("span", `胜场 ${bp.wins[team] ?? bp.wins[String(team)] ?? 0}`, "bp-side__wins"));
  }
  stats.append(element("span", `已用 ${bp.total_levels?.[team] || 0}/${bp.level_cap}`, "bp-side__points"));
  head.append(title, stats);
  col.append(head);
  const bans = (bp.actions || []).filter((action) => Number(action.team_id) === team && action.kind === "ban");
  const picks = (bp.actions || []).filter((action) => Number(action.team_id) === team && action.kind === "pick");
  const banRow = element("div", "", "bp-tag-row");
  banRow.setAttribute("aria-label", "已禁用");
  bans.forEach((action) => banRow.append(heroTag(action, details, "ban")));
  const rule = element("div", "", "rule-divider bp-side__rule");
  const pickRow = element("div", "", "bp-tag-row");
  pickRow.setAttribute("aria-label", "已选取");
  picks.forEach((action) => pickRow.append(heroTag(action, details, "pick")));
  col.append(banRow, rule, pickRow);
  return col;
}

function paintCommit(button, bp, hero, allowed) {
  if (!button) return;
  const kind = bp.current_kind === "ban" ? "ban" : "pick";
  const verb = kind === "ban" ? "确认禁用" : "确认选取";
  button.hidden = !allowed;
  button.disabled = !allowed;
  button.textContent = hero && allowed ? `${verb} ${hero.name}` : verb;
}

function heroBoard(bp, details, commit) {
  const captainId = Number(bp.captains?.[bp.current_team_id]);
  const mine = Number(state.room.viewer_player_id) === captainId && bp.phase === "draft";
  const kind = bp.current_kind === "ban" ? "ban" : "pick";
  const autoBanned = new Set((bp.auto_banned_levels || []).map(Number));
  const remaining = Number(bp.level_cap || 0) - Number(bp.total_levels?.[bp.current_team_id] || 0);
  const legalLevels = Array.isArray(bp.legal_levels) ? new Set(bp.legal_levels.map(Number)) : null;
  const actionsFor = (code) => (bp.actions || []).filter((action) => action.hero_code === code);
  const blocked = (hero) => {
    const actions = actionsFor(hero.code);
    if (actions.some((action) => action.kind === "ban") || autoBanned.has(Number(hero.level))) return true;
    const team = Number(bp.current_team_id);
    if (actions.some((action) => action.kind === "pick" && Number(action.team_id) === team)) return true;
    if (kind === "ban" && actions.some((action) => action.kind === "pick")) return true;
    if (!bp.allow_same_hero && actions.some((action) => action.kind === "pick")) return true;
    return false;
  };
  const legal = (hero) => {
    if (blocked(hero)) return false;
    if (legalLevels) return legalLevels.has(Number(hero.level));
    return kind === "ban" || Number(hero.level) <= remaining;
  };
  const board = element("div", "", "bp-matrix");
  const pool = bp.hero_pool_codes?.length ? new Set(bp.hero_pool_codes) : null;
  const heroes = (state.heroes || []).filter((hero) => !pool || pool.has(hero.code)).sort((left, right) => (
    Number(left.level) - Number(right.level) || left.name.localeCompare(right.name, "zh")
  ));
  const groups = [];
  heroes.forEach((hero) => {
    const level = Number(hero.level);
    const last = groups[groups.length - 1];
    if (!last || last.level !== level) groups.push({ level, heroes: [hero] });
    else last.heroes.push(hero);
  });
  const cells = [];
  const markSelected = () => {
    cells.forEach((node) => node.classList.toggle("is-selected", node.dataset.code === selectedHeroCode));
    const hero = heroByCode(selectedHeroCode);
    paintCommit(commit, bp, hero, Boolean(hero && mine && legal(hero)));
    if (hero) {
      details.show(null, hero);
      details.panel.classList.add("has-hero");
    }
  };
  groups.forEach((group) => {
    const closed = collapsedLevels.has(group.level);
    const block = element("section", "", `bp-level${closed ? " is-collapsed" : ""}`);
    const toggle = element("button", `${group.level} 点`, "bp-level__toggle");
    toggle.type = "button";
    const wrap = element("div", "", "bp-level__heroes");
    wrap.hidden = closed;
    toggle.classList.toggle("is-collapsed", closed);
    toggle.setAttribute("aria-expanded", closed ? "false" : "true");
    toggle.addEventListener("click", () => {
      const next = !collapsedLevels.has(group.level);
      if (next) collapsedLevels.add(group.level);
      else collapsedLevels.delete(group.level);
      wrap.hidden = next;
      block.classList.toggle("is-collapsed", next);
      toggle.classList.toggle("is-collapsed", next);
      toggle.setAttribute("aria-expanded", next ? "false" : "true");
    });
    group.heroes.forEach((hero) => {
      const actions = actionsFor(hero.code);
      const picked = actions.some((action) => action.kind === "pick");
      const tone = (actions.some((action) => action.kind === "ban") || autoBanned.has(group.level)) ? "is-banned"
        : (mine && legal(hero)) ? "is-legal"
        : picked ? "is-picked"
        : "is-dim";
      const cell = element("button", hero.name, `bp-cell ${tone}${hero.code === selectedHeroCode ? " is-selected" : ""}`);
      cell.type = "button";
      cell.dataset.code = hero.code;
      cell.addEventListener("click", () => {
        const alreadyOpen = selectedHeroCode === hero.code && details.panel.classList.contains("has-hero");
        if (alreadyOpen) {
          selectedHeroCode = "";
          details.reset(null);
          cells.forEach((node) => node.classList.toggle("is-selected", false));
          paintCommit(commit, bp, null, false);
          return;
        }
        selectedHeroCode = hero.code;
        details.show(cell, hero);
        markSelected();
      });
      cells.push(cell);
      wrap.append(cell);
    });
    block.append(toggle, wrap);
    board.append(block);
  });
  const initial = heroByCode(selectedHeroCode);
  paintCommit(commit, bp, initial, Boolean(initial && mine && legal(initial)));
  return board;
}

function ruleNames(modes) {
  const effects = (modes || []).filter((mode) => mode.code !== "double_down");
  const shown = effects.length ? effects : (modes || []);
  return shown.map((mode) => mode.name).filter(Boolean).join("、") || "无";
}

let ruleDetailOpen = false;

function ruleChip(modes) {
  const wrap = element("div", "", "bp-rule");
  const button = element("button", `规则：${ruleNames(modes)}`, "bp-rule__name");
  button.type = "button";
  const pop = element("div", "", "bp-rule__pop");
  pop.hidden = !ruleDetailOpen;
  const listed = (modes || []).filter((mode) => mode.code !== "double_down" || (modes || []).length === 1);
  (listed.length ? listed : modes || []).forEach((mode) => {
    pop.append(element("strong", mode.name || "规则"));
    pop.append(element("p", mode.description || ""));
  });
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    ruleDetailOpen = pop.hidden;
    pop.hidden = !ruleDetailOpen;
  });
  wrap.append(button, pop);
  return wrap;
}

function renderDraftStage(panel, bp, seats, details) {
  syncBpSelection(bp);
  const reviewing = bp.phase === "review";
  const kind = reviewing ? "pick" : (bp.current_kind === "ban" ? "ban" : "pick");
  const stage = element("div", "", "bp-stage");
  const banner = element("div", "", `bp-phase is-${reviewing ? "assign" : kind}`);
  const captainId = Number(bp.captains?.[bp.current_team_id]);
  if (Number(state.room.viewer_player_id) === captainId) banner.classList.add("is-mine");
  const kindLabel = kind === "ban" ? "禁用" : "选取";
  const who = seatLabel(bp.current_team_id, seats);
  const step = `${Number(bp.step_index || 0) + 1}/${bp.step_count}`;
  const tier = !reviewing && bp.format === "tier" && bp.current_level ? ` ${bp.current_level}点` : "";
  const line = element("p", reviewing ? "禁选结束" : `${who} ${kindLabel}${tier} ${step}`, "bp-phase__line");
  const commit = element("button", reviewing ? "进入分配" : (kind === "ban" ? "确认禁用" : "确认选取"), "bp-commit");
  commit.type = "button";
  commit.hidden = reviewing ? !state.room.viewer_is_host : true;
  commit.disabled = reviewing ? !state.room.viewer_is_host : false;
  commit.addEventListener("click", () => {
    if (commit.disabled) return;
    commit.disabled = true;
    if (reviewing) void openBpAssign();
    else if (selectedHeroCode) void bpChoose(selectedHeroCode);
  });
  banner.append(line);
  if (bp.special_modes?.length) banner.append(ruleChip(bp.special_modes));
  if (!reviewing) {
    const tags = element("div", "", "bp-phase__tags");
    (bp.auto_banned_levels || []).forEach((level) => {
      tags.append(element("span", `Lv ${level} 已禁用`, "bp-phase__ban"));
    });
    if (tags.children.length) banner.append(tags);
  }
  banner.append(commit);
  stage.append(banner);
  const layout = element("div", "", "bp-layout");
  const board = element("div", "", "bp-board");
  board.append(heroBoard(bp, details, reviewing ? null : commit));
  layout.append(sideColumn(1, bp, seats, details), board, sideColumn(2, bp, seats, details));
  const opened = heroByCode(selectedHeroCode);
  if (opened) {
    details.show(null, opened);
    details.panel.classList.add("has-hero");
  }
  stage.append(layout, details.panel);
  panel.append(stage);
}

function renderAssign(panel, bp, seats) {
  const stage = element("div", "", "bp-stage");
  const banner = element("div", "", "bp-phase is-assign");
  banner.append(element("p", "是否交给 AI", "bp-phase__line"));
  if (state.room.viewer_is_host) {
    const start = element("button", "开战", "bp-commit");
    start.type = "button";
    start.addEventListener("click", () => {
      start.disabled = true;
      void startRoomBattle();
    });
    banner.append(start);
  }
  const layout = element("div", "", "bp-assign");
  for (const team of [1, 2]) {
    const card = element("section", "", `bp-side is-team-${team}`);
    card.append(element("span", teamName(team), "bp-side__team"));
    const name = seatLabel(team, seats);
    if (name !== teamName(team)) card.append(element("h4", name));
    const names = (bp.picks?.[team] || []).map((code) => heroName(code));
    card.append(element("p", names.join("、") || "没有选取", "bp-assign__roster"));
    if (bp.wins) {
      card.append(element("div", `胜场 ${bp.wins[team] ?? bp.wins[String(team)] ?? 0}`, "bp-side__wins"));
    }
    card.append(element("div", `${team === 1 ? "红" : "蓝"} ${bp.total_levels?.[team] || 0}/${bp.level_cap}`, "bp-side__points"));
    const label = element("label", "", "switch");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = bp.team_ai?.[team] !== false;
    const viewer = Number(state.room.viewer_player_id);
    const own = (seats || []).some((seat) => seat.player_id === viewer && Number(seat.team_id) === team);
    input.disabled = !(own || state.room.viewer_is_host);
    input.addEventListener("change", () => {
      void setBpTeamAi(team, input.checked);
    });
    const track = element("span", "", "switch__track");
    track.setAttribute("aria-hidden", "true");
    label.append(input, track, element("span", "交给 AI", "switch__text"));
    card.append(label);
    layout.append(card);
  }
  stage.append(banner, layout);
  panel.append(stage);
}

export function renderBpPanel() {
  const panel = $('bp-panel');
  if (!panel) return;
  const room = state.room;
  const battleBanner = typeof document.getElementById === 'function' ? document.getElementById('bp-battle-mode') : null;
  if (battleBanner) {
    const modes = room?.mode === "bp" && room.status === "battle" ? room.bp?.special_modes || [] : [];
    const ruleKey = modes.map((mode) => `${mode.code || ""}:${mode.name || ""}`).join("|");
    battleBanner.classList.toggle("hidden", !modes.length);
    if (battleBanner.dataset.ruleKey !== ruleKey) {
      battleBanner.dataset.ruleKey = ruleKey;
      battleBanner.replaceChildren();
      if (modes.length) battleBanner.append(ruleChip(modes));
    }
  }
  const inBpFlow = room?.mode === "bp" && room.status === "bp";
  document.body?.classList?.toggle("bp-draft", Boolean(inBpFlow));
  panel.classList.toggle("hidden", !inBpFlow);
  if (!inBpFlow) {
    hideHeroHover();
    return;
  }
  const bp = room.bp || {};
  const phaseKey = JSON.stringify([
    room.room_id,
    room.viewer_player_id,
    room.viewer_is_host,
    bp.phase,
    bp.step_index,
    bp.current_kind,
    bp.current_team_id,
    bp.actions,
    bp.picks,
    bp.team_ai,
    bp.legal_levels,
    bp.auto_banned_levels,
    bp.format,
    bp.current_level,
    bp.wins,
    bp.hero_pool_codes,
    bp.special_modes,
    (room.seats || []).map((seat) => [seat.player_id, seat.name, seat.team_id]),
  ]);
  if (panel.dataset.bpPhaseKey === phaseKey) return;
  panel.dataset.bpPhaseKey = phaseKey;
  hideHeroHover();
  panel.replaceChildren();
  const seats = room.seats || [];
  if (bp.phase === "assign") {
    renderAssign(panel, bp, seats);
    return;
  }
  const details = createHeroDetails();
  renderDraftStage(panel, bp, seats, details);
}
