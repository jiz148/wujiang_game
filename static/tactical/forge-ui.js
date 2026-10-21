import { $ } from "../core/dom.js";
import { fetchJson } from "../core/net.js";
import { render } from "../core/render.js";
import { state } from "../core/state.js";
import { applyRoomPayload, reportRoomError } from "./room-api.js";

const STATS = [
  ["attack", "攻"],
  ["defense", "守"],
  ["speed", "速"],
  ["attack_range", "范"],
  ["mana", "魔"],
];

let query = "";
let tab = "skill";
let composing = false;
let forgeOverlayOpen = false;

function teamName(team) {
  return team === 1 ? "红方" : "蓝方";
}

function viewerTeam() {
  const viewer = Number(state.room?.viewer_player_id);
  const seat = (state.room?.seats || []).find((item) => item.player_id === viewer);
  return Number(seat?.team_id) === 2 ? 2 : 1;
}

function canEdit(team) {
  const viewer = Number(state.room?.viewer_player_id);
  const seat = (state.room?.seats || []).find((item) => item.player_id === viewer);
  return Boolean(seat?.is_human && (Number(seat.team_id) === team || state.room?.viewer_is_host));
}

async function postForge(action, fields) {
  try {
    const payload = await fetchJson("/api/rooms/forge/action", {
      method: "POST",
      body: JSON.stringify({
        room_id: state.room?.room_id,
        player_token: state.playerToken,
        action,
        ...fields,
      }),
    });
    applyRoomPayload(payload, {preserveScreen: true});
    render();
  } catch (error) {
    if (error?.state) applyRoomPayload(error.state, {preserveScreen: true});
    reportRoomError(typeof error?.error === "string" ? error.error : "没能保存这次修改。");
    render();
  }
}

function catalogIndex(catalog) {
  return new Map([...(catalog.skills || []), ...(catalog.traits || [])].map((item) => [item.id, item]));
}


function pointBudget(build) {
  const forge = state.room?.forge || {};
  const team = build?.team;
  const base = Number(forge.point_cap ?? 15);
  const extra = Number(forge.bonus?.[String(team)] ?? forge.bonus?.[team] ?? 0);
  return base + Math.max(0, extra);
}

function spent(build, catalog) {
  const byId = catalogIndex(catalog);
  let total = 0;
  for (const [key] of STATS) total += (Number(build[key]) - 3) * 3;
  for (const id of build.skills || []) total += Number(byId.get(id)?.cost || 1);
  for (const id of build.traits || []) total += Number(byId.get(id)?.cost || 1);
  return total;
}

function matches(item, text) {
  if (!text) return true;
  const hay = `${item.name} ${item.description || ""} ${item.search || ""} ${(item.heroes || []).join(" ")}`.toLowerCase();
  return hay.includes(text);
}

function tipNode() {
  let tip = document.getElementById("forge-tip");
  if (!tip) {
    tip = document.createElement("aside");
    tip.id = "forge-tip";
    tip.className = "forge-tip";
    tip.setAttribute("role", "tooltip");
    document.body.append(tip);
  }
  return tip;
}

function hideForgeTip() {
  tipNode().classList.remove("is-on");
}

function placeForgeTip(event) {
  const tip = tipNode();
  const margin = 8;
  const gap = 14;
  const rect = tip.getBoundingClientRect();
  let x = event.clientX + gap;
  let y = event.clientY + gap;
  if (x + rect.width > window.innerWidth - margin) x = event.clientX - rect.width - gap;
  if (y + rect.height > window.innerHeight - margin) y = event.clientY - rect.height - gap;
  tip.style.left = `${Math.max(margin, x)}px`;
  tip.style.top = `${Math.max(margin, y)}px`;
}

function showForgeTip(item, event) {
  if (!item) return;
  const tip = tipNode();
  tip.replaceChildren();
  const title = document.createElement("strong");
  title.textContent = item.name || "";
  const body = document.createElement("p");
  body.textContent = item.description || "没有介绍";
  tip.append(title, body);
  const from = (item.heroes || []).filter(Boolean);
  if (from.length) {
    const source = document.createElement("p");
    source.className = "forge-tip__from";
    source.textContent = from.join("、");
    tip.append(source);
  }
  tip.classList.add("is-on");
  placeForgeTip(event);
}

function bindForgeTip(node, item) {
  node.addEventListener("pointerenter", (event) => showForgeTip(item, event));
  node.addEventListener("pointermove", (event) => {
    if (tipNode().classList.contains("is-on")) placeForgeTip(event);
  });
  node.addEventListener("pointerleave", hideForgeTip);
}

function cardFace(build, catalog) {
  const face = document.createElement("article");
  face.className = "forge-card";
  const head = document.createElement("header");
  head.className = "forge-card__head";
  const name = document.createElement("input");
  name.className = "forge-name";
  name.value = build.name || "";
  name.maxLength = 12;
  name.setAttribute("aria-label", "名称");
  name.addEventListener("change", () => {
    build.name = name.value.trim() || "未命名";
    void postForge("build", {team_id: build.team, build});
  });
  head.append(name);
  const meta = document.createElement("div");
  meta.className = "forge-meta";
  for (const [key, label, options] of [["role", "职业", catalog.roles], ["race", "种族", catalog.races], ["attribute", "属性", catalog.attributes]]) {
    const select = document.createElement("select");
    select.className = "forge-select";
    select.setAttribute("aria-label", label);
    for (const optionName of options || []) {
      const option = document.createElement("option");
      option.value = optionName;
      option.textContent = optionName;
      select.append(option);
    }
    select.value = build[key] || options?.[0] || "";
    select.addEventListener("change", () => {
      build[key] = select.value;
      void postForge("build", {team_id: build.team, build});
    });
    meta.append(select);
  }
  const intro = document.createElement("textarea");
  intro.className = "forge-intro";
  intro.value = build.intro || "";
  intro.placeholder = "一句介绍";
  intro.maxLength = 240;
  intro.rows = 2;
  const fitIntro = () => {
    intro.style.height = "auto";
    intro.style.height = `${Math.min(intro.scrollHeight, 420)}px`;
  };
  intro.addEventListener("input", fitIntro);
  intro.addEventListener("change", () => {
    build.intro = intro.value.trim();
    void postForge("build", {team_id: build.team, build});
  });
  queueMicrotask(fitIntro);
  const stats = document.createElement("div");
  stats.className = "forge-stats";
  const cap = Number(state.room?.forge?.stat_cap ?? 6);
  const remaining = pointBudget(build) - spent(build, catalog);
  for (const [key, label] of STATS) {
    const row = document.createElement("div");
    row.className = "forge-stat";
    const down = document.createElement("button");
    down.type = "button";
    down.textContent = "–";
    down.disabled = Number(build[key]) <= 0;
    down.addEventListener("click", () => {
      build[key] = Math.max(0, Number(build[key]) - 1);
      void postForge("build", {team_id: build.team, build});
    });
    const up = document.createElement("button");
    up.type = "button";
    up.textContent = "+";
    up.disabled = Number(build[key]) >= cap || remaining < 3;
    up.addEventListener("click", () => {
      build[key] = Math.min(cap, Number(build[key]) + 1);
      void postForge("build", {team_id: build.team, build});
    });
    const value = document.createElement("strong");
    value.textContent = String(build[key]);
    row.append(document.createElement("span"), down, value, up);
    row.firstChild.textContent = label;
    stats.append(row);
  }
  face.append(head, meta, intro, stats);
  return face;
}

function chosenItems(build, catalog) {
  const byId = catalogIndex(catalog);
  return [...(build.skills || []), ...(build.traits || [])].map((id) => byId.get(id)).filter(Boolean);
}


function fillOwned(host, build, items, removable) {
  const groups = [
    ["skills", "技能", items.filter((item) => item.kind !== "trait")],
    ["traits", "特性", items.filter((item) => item.kind === "trait")],
  ];
  for (const [kind, title, list] of groups) {
    if (!list.length) continue;
    const group = document.createElement("section");
    group.className = `forge-owned forge-owned--${kind}`;
    const heading = document.createElement("h4");
    heading.textContent = title;
    const row = document.createElement("div");
    row.className = "forge-owned__list";
    for (const item of list) {
      const chip = document.createElement("span");
      chip.className = removable ? "forge-chip" : "forge-chip is-static";
      const name = document.createElement("span");
      name.textContent = item.ultimate ? `${item.name} · 大` : (item.name || item.id);
      chip.append(name);
      bindForgeTip(chip, item);
      if (removable) {
        const close = document.createElement("button");
        close.type = "button";
        close.className = "forge-chip__x";
        close.textContent = "×";
        close.setAttribute("aria-label", `移除${item.name}`);
        close.addEventListener("click", () => {
          hideForgeTip();
          const key = item.kind === "trait" ? "traits" : "skills";
          build[key] = (build[key] || []).filter((part) => part !== item.id);
          void postForge("build", {team_id: build.team, build});
        });
        chip.append(close);
      }
      row.append(chip);
    }
    group.append(heading, row);
    host.append(group);
  }
}

function picker(build, catalog) {
  const pane = document.createElement("section");
  pane.className = "forge-picker";
  const picks = document.createElement("div");
  picks.className = "forge-picks";
  fillOwned(picks, build, chosenItems(build, catalog), true);
  const search = document.createElement("input");
  search.className = "forge-search";
  search.placeholder = "技能、特性、武将";
  search.value = query;
  search.setAttribute("aria-label", "检索");
  const tabs = document.createElement("div");
  tabs.className = "forge-tabs";
  for (const [id, label] of [["skill", "技能"], ["ultimate", "大招"], ["trait", "特性"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = tab === id ? "is-on" : "";
    button.textContent = label;
    button.addEventListener("click", () => {
      tab = id;
      renderForgePanel();
    });
    tabs.append(button);
  }
  const list = document.createElement("div");
  list.className = "forge-results";
  const paintResults = () => {
    hideForgeTip();
    const pool = tab === "trait" ? catalog.traits : catalog.skills;
    const rows = (pool || []).filter((item) => (tab !== "ultimate" || item.ultimate) && (tab !== "skill" || !item.ultimate) && matches(item, query)).slice(0, 80);
    const chosen = new Set([...(build.skills || []), ...(build.traits || [])]);
    const budget = pointBudget(build);
    const used = spent(build, catalog);
    list.replaceChildren();
    for (const item of rows) {
      const row = document.createElement("button");
      row.type = "button";
      const blocked = !chosen.has(item.id) && used + Number(item.cost) > budget;
      row.className = `forge-result${chosen.has(item.id) ? " is-on" : ""}${blocked ? " is-disabled" : ""}`;
      const title = document.createElement("strong");
      title.textContent = item.name;
      const from = document.createElement("em");
      from.textContent = (item.heroes || []).slice(0, 2).join("、");
      const cost = document.createElement("b");
      cost.textContent = String(item.cost);
      row.append(title, from, cost);
      bindForgeTip(row, item);
      row.addEventListener("click", () => {
        if (blocked) return;
        hideForgeTip();
        const key = item.kind === "trait" ? "traits" : "skills";
        const next = new Set(build[key] || []);
        if (next.has(item.id)) next.delete(item.id);
        else next.add(item.id);
        build[key] = [...next];
        void postForge("build", {team_id: build.team, build});
      });
      list.append(row);
    }
  };
  const applyQuery = () => {
    query = search.value.trim().toLowerCase();
    paintResults();
  };
  search.addEventListener("compositionstart", () => {
    composing = true;
  });
  search.addEventListener("compositionend", () => {
    composing = false;
    applyQuery();
  });
  search.addEventListener("input", (event) => {
    if (composing || event.isComposing) return;
    applyQuery();
  });
  paintResults();
  pane.append(picks, search, tabs, list);
  return pane;
}

function reveal(forge) {
  const stage = document.createElement("div");
  stage.className = "forge-reveal";
  const catalog = forge.catalog || {skills: [], traits: []};
  const byId = catalogIndex(catalog);
  for (const team of [1, 2]) {
    const build = forge.builds?.[String(team)] || forge.builds?.[team] || {};
    const card = document.createElement("section");
    card.className = `forge-reveal__card is-team-${team}`;
    const title = document.createElement("h3");
    title.textContent = `${teamName(team)} · ${build.name || "未命名"}`;
    const meta = document.createElement("p");
    meta.className = "forge-reveal__meta";
    meta.textContent = [build.role, build.race, build.attribute].filter(Boolean).join(" · ");
    const intro = document.createElement("p");
    intro.className = "forge-reveal__intro";
    intro.textContent = String(build.intro || "").trim() || "没有介绍";
    const line = document.createElement("p");
    line.className = "forge-reveal__stats";
    line.textContent = STATS.map(([key, label]) => `${label} ${build[key] ?? 3}`).join("  ");
    const parts = document.createElement("div");
    parts.className = "forge-picks";
    const picked = [...(build.skills || []), ...(build.traits || [])].map((id) => byId.get(id) || {
      id,
      name: id,
      kind: String(id).startsWith("t:") ? "trait" : "skill",
    });
    fillOwned(parts, build, picked, false);
    const label = document.createElement("label");
    label.className = "switch forge-reveal__ai";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(forge.team_ai?.[String(team)] ?? forge.team_ai?.[team] ?? true);
    input.disabled = !canEdit(team);
    input.addEventListener("change", () => {
      void postForge("team-ai", {team_id: team, enabled: input.checked});
    });
    const track = document.createElement("span");
    track.className = "switch__track";
    label.append(input, track, document.createTextNode("交给 AI"));
    card.append(title, meta, intro, line, parts, label);
    stage.append(card);
  }
  if (state.room?.viewer_is_host) {
    const start = document.createElement("button");
    start.type = "button";
    start.className = "primary forge-start";
    start.textContent = "开战";
    start.addEventListener("click", () => {
      start.disabled = true;
      void import("./room-api.js").then(({startRoomBattle}) => startRoomBattle());
    });
    stage.append(start);
  }
  return stage;
}


let forgeRollDialog = null;

function closeForgeRoll() {
  forgeRollDialog?.remove();
  forgeRollDialog = null;
}

function takeRandom(list) {
  if (!list.length) return null;
  return list[Math.floor(Math.random() * list.length)];
}

function rollForgeBuild(build, catalog, points, reset) {
  const cap = Number(state.room?.forge?.stat_cap ?? 6);
  const next = {
    ...build,
    skills: reset ? [] : [...(build.skills || [])],
    traits: reset ? [] : [...(build.traits || [])],
  };
  if (reset) {
    for (const [key] of STATS) next[key] = 0;
  }
  if (catalog.races?.length) next.race = takeRandom(catalog.races);
  if (catalog.attributes?.length) next.attribute = takeRandom(catalog.attributes);
  if (catalog.roles?.length) next.role = takeRandom(catalog.roles);
  let left = reset
    ? pointBudget(build) - spent(next, catalog)
    : Math.max(0, Math.floor(Number(points) || 0));
  const owned = new Set([...(next.skills || []), ...(next.traits || [])]);

  const takeStat = () => {
    const keys = STATS.map(([key]) => key).filter((key) => Number(next[key]) < cap);
    if (!keys.length || left < 3) return false;
    const key = takeRandom(keys);
    next[key] = Number(next[key]) + 1;
    left -= 3;
    return true;
  };

  const takePart = (kind) => {
    const pool = (kind === "traits" ? catalog.traits : catalog.skills) || [];
    const affordable = pool.filter((item) => item && !owned.has(item.id) && Number(item.cost) <= left);
    if (!affordable.length) return false;
    const ultimates = affordable.filter((item) => item.ultimate);
    const choices = kind === "skills" && ultimates.length && Math.random() < 0.22 ? ultimates : affordable;
    const item = takeRandom(choices);
    owned.add(item.id);
    next[kind].push(item.id);
    left -= Number(item.cost);
    return true;
  };

  const steps = [takeStat, () => takePart("skills"), () => takePart("traits")];
  let guard = 0;
  while (left > 0 && guard < 120) {
    guard += 1;
    const roll = Math.random();
    const first = roll < 0.34 ? takeStat : roll < 0.72 ? () => takePart("skills") : () => takePart("traits");
    if (first()) continue;
    const order = [...steps].sort(() => Math.random() - 0.5);
    if (!order.some((step) => step())) break;
  }
  return next;
}

function openForgeRoll(build, catalog, remaining) {
  hideForgeTip();
  closeForgeRoll();
  const max = Math.max(0, Math.floor(Number(remaining) || 0));
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay forge-roll";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  const card = document.createElement("div");
  card.className = "modal-card modal-card--narrow";
  const head = document.createElement("div");
  head.className = "modal-card__head";
  const title = document.createElement("h2");
  title.className = "modal-card__title";
  title.id = "forge-roll-title";
  title.textContent = "\u968f\u673a\u751f\u6210";
  overlay.setAttribute("aria-labelledby", "forge-roll-title");
  head.append(title);
  const body = document.createElement("div");
  body.className = "modal-card__body";
  const resetLabel = document.createElement("label");
  resetLabel.className = "switch forge-roll__reset";
  const resetInput = document.createElement("input");
  resetInput.type = "checkbox";
  const resetTrack = document.createElement("span");
  resetTrack.className = "switch__track";
  resetLabel.append(resetInput, resetTrack, document.createTextNode("完全重新随机"));
  const field = document.createElement("label");
  field.className = "field";
  field.htmlFor = "forge-roll-points";
  const label = document.createElement("span");
  label.className = "field__label";
  label.textContent = "\u4f7f\u7528\u70b9\u6570";
  const input = document.createElement("input");
  input.id = "forge-roll-points";
  input.className = "input";
  input.type = "number";
  input.min = "1";
  input.max = String(max);
  input.step = "1";
  input.value = String(max);
  const hint = document.createElement("span");
  hint.className = "forge-roll__hint";
  hint.textContent = "\u6700\u591a " + max + " \u70b9";
  field.append(label, input, hint);
  body.append(resetLabel, field);
  const actions = document.createElement("div");
  actions.className = "modal-card__actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "ghost";
  cancel.textContent = "\u53d6\u6d88";
  cancel.addEventListener("click", closeForgeRoll);
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "primary";
  confirm.textContent = "\u786e\u5b9a";
  const readPoints = () => {
    const value = Math.floor(Number(input.value));
    if (!Number.isFinite(value)) return 0;
    return value;
  };
  const sync = () => {
    field.classList.toggle("hidden", resetInput.checked);
    const value = readPoints();
    confirm.disabled = !resetInput.checked && (value < 1 || value > max);
  };
  resetInput.addEventListener("change", sync);
  input.addEventListener("input", () => {
    if (input.value !== "" && readPoints() > max) input.value = String(max);
    sync();
  });
  confirm.addEventListener("click", () => {
    const value = readPoints();
    if (!resetInput.checked && (value < 1 || value > max)) return;
    const rolled = rollForgeBuild(build, catalog, value, resetInput.checked);
    closeForgeRoll();
    void postForge("build", {team_id: build.team, build: rolled});
  });
  actions.append(cancel, confirm);
  sync();
  card.append(head, body, actions);
  overlay.append(card);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeForgeRoll();
  });
  document.body.append(overlay);
  forgeRollDialog = overlay;
  input.focus();
  input.select();
}

function showForgeToast(text) {
  document.querySelectorAll(".forge-saved-banner").forEach((node) => node.remove());
  const toast = document.createElement("div");
  toast.className = "forge-saved-banner";
  toast.setAttribute("role", "status");
  toast.textContent = text;
  document.body.append(toast);
  window.setTimeout(() => toast.remove(), 2600);
}

function releaseForgeOverlay() {
  document.querySelectorAll(".forge-more-menu").forEach((node) => node.remove());
  document.getElementById("forge-save-dialog")?.remove();
  forgeOverlayOpen = false;
}

function openForgeSave(build) {
  document.querySelectorAll(".forge-more-menu").forEach((node) => node.remove());
  if (document.getElementById("forge-save-dialog")) return;
  forgeOverlayOpen = true;
  hideForgeTip();
  const overlay = document.createElement("div");
  overlay.id = "forge-save-dialog";
  overlay.className = "modal-overlay forge-roll";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  const card = document.createElement("div");
  card.className = "modal-card modal-card--narrow";
  const head = document.createElement("div");
  head.className = "modal-card__head";
  const title = document.createElement("h2");
  title.className = "modal-card__title";
  title.id = "forge-save-title";
  title.textContent = "加入数据库";
  overlay.setAttribute("aria-labelledby", "forge-save-title");
  head.append(title);
  const body = document.createElement("div");
  body.className = "modal-card__body";
  const who = document.createElement("p");
  who.textContent = build.name || "未命名";
  const field = document.createElement("label");
  field.className = "field";
  const label = document.createElement("span");
  label.className = "field__label";
  label.textContent = "等级";
  const input = document.createElement("input");
  input.className = "input";
  input.type = "number";
  input.min = "1";
  input.max = "10";
  input.step = "1";
  input.value = "1";
  const hint = document.createElement("span");
  hint.className = "forge-save__hint";
  hint.textContent = "等级就是这名武将在数据库里的点数，从 1 到 10。存下来后带上「自建」，不会进入其他模式的武将池。";
  field.append(label, input, hint);
  const error = document.createElement("p");
  error.className = "meta-note";
  body.append(who, field, error);
  const actions = document.createElement("div");
  actions.className = "modal-card__actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "ghost";
  cancel.textContent = "取消";
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "primary";
  confirm.textContent = "加入数据库";
  const close = () => {
    overlay.remove();
    forgeOverlayOpen = false;
  };
  cancel.addEventListener("click", close);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  confirm.addEventListener("click", () => {
    const level = Math.floor(Number(input.value));
    if (!Number.isFinite(level) || level < 1 || level > 10) {
      error.textContent = "等级要是 1 到 10 的整数。";
      return;
    }
    confirm.disabled = true;
    error.textContent = "";
    void fetchJson("/api/custom-heroes", {
      method: "POST",
      body: JSON.stringify({
        name: build.name,
        race: build.race,
        attribute: build.attribute,
        role: build.role,
        intro: build.intro,
        attack: build.attack,
        defense: build.defense,
        speed: build.speed,
        attack_range: build.attack_range,
        mana: build.mana,
        skills: build.skills || [],
        traits: build.traits || [],
        level,
      }),
    }).then((payload) => {
      state.customHeroes = Array.isArray(payload.heroes) ? payload.heroes : state.customHeroes;
      state.customHeroesLoaded = true;
      state.customHeroesError = "";
      close();
      showForgeToast("添加成功");
      render();
    }).catch((err) => {
      confirm.disabled = false;
      error.textContent = typeof err?.error === "string" ? err.error : "没能存进数据库。";
    });
  });
  actions.append(cancel, confirm);
  card.append(head, body, actions);
  overlay.append(card);
  document.body.append(overlay);
  input.focus();
  input.select();
}

function forgeMoreControl(build, canUse) {
  const wrap = document.createElement("div");
  wrap.className = "forge-more";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost forge-more__button";
  button.textContent = "···";
  button.setAttribute("aria-label", "更多");
  button.disabled = !canUse;
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    const existing = document.querySelector(".forge-more-menu");
    if (existing) {
      existing.remove();
      forgeOverlayOpen = false;
      return;
    }
    const menu = document.createElement("div");
    menu.className = "forge-more-menu";
    const item = document.createElement("button");
    item.type = "button";
    item.textContent = "加入数据库";
    item.addEventListener("click", (click) => {
      click.stopPropagation();
      openForgeSave(build);
    });
    menu.append(item);
    const rect = button.getBoundingClientRect();
    menu.style.top = `${rect.bottom + 6}px`;
    menu.style.left = `${rect.left}px`;
    document.body.append(menu);
    forgeOverlayOpen = true;
    const closer = (click) => {
      if (click.target === button || menu.contains(click.target)) return;
      menu.remove();
      document.removeEventListener("click", closer);
      if (!document.getElementById("forge-save-dialog")) forgeOverlayOpen = false;
    };
    window.setTimeout(() => document.addEventListener("click", closer), 0);
  });
  wrap.append(button);
  return wrap;
}


function tipOf(entry, catalog, kind) {
  if (typeof entry === "string") {
    const pool = kind === "trait" ? catalog?.traits : catalog?.skills;
    return (pool || []).find((item) => item.name === entry) || {name: entry, description: "没有介绍"};
  }
  if (entry?.description) return entry;
  const name = entry?.name || "";
  const pool = kind === "trait" ? catalog?.traits : catalog?.skills;
  return (pool || []).find((item) => item.name === name) || {name, description: entry?.description || "没有介绍"};
}

function rivalTags(entries, catalog, kind) {
  const tags = document.createElement("div");
  tags.className = "forge-rival__tags";
  for (const entry of entries || []) {
    const tip = tipOf(entry, catalog, kind);
    const chip = document.createElement("span");
    chip.className = "forge-chip is-static";
    chip.textContent = tip.name || "未命名";
    bindForgeTip(chip, tip);
    tags.append(chip);
  }
  if (!tags.childElementCount) {
    const empty = document.createElement("span");
    empty.className = "forge-rival__empty";
    empty.textContent = "无";
    tags.append(empty);
  }
  return tags;
}

function rivalStrip(forge) {
  const heroes = Array.isArray(forge?.rivals) ? forge.rivals : [];
  if (forge?.play !== "coop" || !heroes.length) return null;
  const box = document.createElement("aside");
  box.className = "forge-rivals";
  const heading = document.createElement("h3");
  heading.className = "forge-rivals__title";
  heading.textContent = "本层对手";
  const list = document.createElement("div");
  list.className = "forge-rivals__list";
  const labels = [["attack", "攻"], ["defense", "守"], ["speed", "速"], ["attack_range", "范"], ["mana", "魔"]];
  for (const hero of heroes) {
    const card = document.createElement("article");
    card.className = "forge-rival";
    const head = document.createElement("div");
    head.className = "forge-rival__head";
    const title = document.createElement("div");
    title.className = "forge-rival__title";
    const name = document.createElement("strong");
    name.textContent = hero.name || "武将";
    const level = document.createElement("span");
    level.textContent = `Lv${hero.level ?? "?"}`;
    title.append(name, level);
    const identity = document.createElement("span");
    identity.className = "forge-rival__identity";
    identity.textContent = [hero.role, hero.attribute, hero.race].filter(Boolean).join(" · ");
    head.append(title, identity);
    const stats = document.createElement("div");
    stats.className = "forge-rival__stats";
    for (const [key, label] of labels) {
      const chip = document.createElement("span");
      chip.textContent = `${label} ${hero.stats?.[key] ?? "-"}`;
      stats.append(chip);
    }
    const skillLine = document.createElement("div");
    skillLine.className = "forge-rival__line";
    const skillLabel = document.createElement("span");
    skillLabel.textContent = "技能";
    skillLine.append(skillLabel, rivalTags(hero.skills, forge.catalog, "skill"));
    const traitLine = document.createElement("div");
    traitLine.className = "forge-rival__line";
    const traitLabel = document.createElement("span");
    traitLabel.textContent = "特性";
    traitLine.append(traitLabel, rivalTags(hero.traits, forge.catalog, "trait"));
    card.append(head, stats, skillLine, traitLine);
    list.append(card);
  }
  box.append(heading, list);
  return box;
}

function stageBanner(forge) {
  if (forge?.play !== "coop") return null;
  const names = {1: "第一层", 2: "第二层", 3: "第三层", 4: "第四层"};
  const banner = document.createElement("div");
  banner.className = `forge-stage-banner${Number(forge.stage) === 4 ? " is-secret" : ""}`;
  banner.textContent = names[Number(forge.stage)] || "第一层";
  return banner;
}

function crownIcon(crowned) {
  const holder = document.createElement("div");
  holder.className = `forge-crown forge-crown--${crowned ? "ruby" : "gold"}`;
  holder.innerHTML = crowned ? rubyCrownSvg() : goldCrownSvg();
  return holder;
}

let crownSerial = 0;

function goldCrownSvg() {
  const id = `gold${crownSerial += 1}`;
  return `<svg viewBox="0 0 180 148" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <linearGradient id="${id}-body" x1="0" y1="0" x2=".35" y2="1">
        <stop offset="0%" stop-color="#fff8dc"/>
        <stop offset="22%" stop-color="#ffe08a"/>
        <stop offset="55%" stop-color="#e0a428"/>
        <stop offset="100%" stop-color="#8a5610"/>
      </linearGradient>
      <linearGradient id="${id}-band" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#fff4c8"/>
        <stop offset="40%" stop-color="#f0c14b"/>
        <stop offset="100%" stop-color="#9a6414"/>
      </linearGradient>
      <radialGradient id="${id}-orb" cx="32%" cy="28%" r="72%">
        <stop offset="0%" stop-color="#fffef8"/>
        <stop offset="42%" stop-color="#ffe08a"/>
        <stop offset="100%" stop-color="#b8860b"/>
      </radialGradient>
      <radialGradient id="${id}-gem" cx="35%" cy="30%" r="70%">
        <stop offset="0%" stop-color="#ffffff"/>
        <stop offset="40%" stop-color="#dff6ff"/>
        <stop offset="100%" stop-color="#6eb6dc"/>
      </radialGradient>
    </defs>
    <ellipse cx="90" cy="132" rx="68" ry="8" fill="rgba(90,50,8,.28)"/>
    <path fill="url(#${id}-body)" stroke="#7a5210" stroke-width="1.7" stroke-linejoin="round" d="M14 114 L30 62 L36 46 L52 74 L74 98 L90 24 L106 98 L128 74 L144 46 L150 62 L166 114 Z"/>
    <path fill="rgba(120,72,12,.32)" d="M90 24 L106 98 L128 74 L144 46 L150 62 L166 114 L90 114 Z"/>
    <path fill="none" stroke="#fff6d2" stroke-width="2.2" stroke-linecap="round" opacity=".7" d="M34 68 L48 82 M90 34 L90 54 M146 68 L132 82"/>
    <rect x="14" y="106" width="152" height="24" rx="6" fill="url(#${id}-band)" stroke="#7a5210" stroke-width="1.5"/>
    <path fill="none" stroke="#fff8e0" stroke-width="1.4" opacity=".8" d="M22 112 H158"/>
    <circle cx="90" cy="118" r="8" fill="url(#${id}-gem)" stroke="#f0d48a" stroke-width="1.3"/>
    <circle cx="56" cy="118" r="5" fill="url(#${id}-orb)" stroke="#a97816"/>
    <circle cx="124" cy="118" r="5" fill="url(#${id}-orb)" stroke="#a97816"/>
    <circle cx="90" cy="26" r="8" fill="url(#${id}-orb)" stroke="#fff6d0" stroke-width="1.2"/>
    <circle cx="36" cy="48" r="5" fill="url(#${id}-orb)" stroke="#fff6d0"/>
    <circle cx="144" cy="48" r="5" fill="url(#${id}-orb)" stroke="#fff6d0"/>
  </svg>`;
}

function rubyCrownSvg() {
  const id = `ruby${crownSerial += 1}`;
  return `<svg viewBox="0 0 240 196" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <linearGradient id="${id}-velvet" x1="0" y1="0" x2=".4" y2="1">
        <stop offset="0%" stop-color="#ff7d88"/>
        <stop offset="28%" stop-color="#d01830"/>
        <stop offset="70%" stop-color="#8d0d1c"/>
        <stop offset="100%" stop-color="#4c0610"/>
      </linearGradient>
      <linearGradient id="${id}-gold" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#fff6d4"/>
        <stop offset="45%" stop-color="#f0c45a"/>
        <stop offset="100%" stop-color="#a87416"/>
      </linearGradient>
      <radialGradient id="${id}-ruby" cx="32%" cy="28%" r="75%">
        <stop offset="0%" stop-color="#ffe4e8"/>
        <stop offset="35%" stop-color="#ff4d62"/>
        <stop offset="100%" stop-color="#8a1020"/>
      </radialGradient>
      <radialGradient id="${id}-emerald" cx="32%" cy="30%" r="72%">
        <stop offset="0%" stop-color="#f4fff4"/>
        <stop offset="40%" stop-color="#3dce73"/>
        <stop offset="100%" stop-color="#0d7a3a"/>
      </radialGradient>
      <radialGradient id="${id}-sapphire" cx="32%" cy="30%" r="72%">
        <stop offset="0%" stop-color="#f4f8ff"/>
        <stop offset="40%" stop-color="#4aa0f0"/>
        <stop offset="100%" stop-color="#184a9a"/>
      </radialGradient>
    </defs>
    <ellipse cx="120" cy="178" rx="92" ry="9" fill="rgba(60,0,8,.35)"/>
    <path fill="url(#${id}-velvet)" stroke="url(#${id}-gold)" stroke-width="3" stroke-linejoin="round" d="M18 150 L38 78 L46 58 L66 96 L90 118 L112 42 L120 24 L128 42 L150 118 L174 96 L194 58 L202 78 L222 150 Z"/>
    <path fill="rgba(40,0,8,.38)" d="M120 24 L128 42 L150 118 L174 96 L194 58 L202 78 L222 150 L120 150 Z"/>
    <path fill="none" stroke="url(#${id}-gold)" stroke-width="3.2" stroke-linecap="round" d="M50 104 Q84 62 120 50"/>
    <path fill="none" stroke="url(#${id}-gold)" stroke-width="3.2" stroke-linecap="round" d="M190 104 Q156 62 120 50"/>
    <path fill="none" stroke="#fff1c4" stroke-width="1.4" opacity=".75" d="M58 108 Q86 78 120 70 Q154 78 182 108"/>
    <path fill="none" stroke="url(#${id}-gold)" stroke-width="2" d="M72 124 Q96 104 120 116 Q144 104 168 124"/>
    <rect x="18" y="142" width="204" height="30" rx="7" fill="url(#${id}-gold)" stroke="#7a5410" stroke-width="1.4"/>
    <rect x="24" y="148" width="192" height="18" rx="4" fill="#7a1020"/>
    <path fill="#f0c45a" d="M40 157 l4 -6 l4 6 l-4 6 z M64 157 l4 -6 l4 6 l-4 6 z M176 157 l4 -6 l4 6 l-4 6 z M200 157 l4 -6 l4 6 l-4 6 z"/>
    <g transform="translate(120 157)">
      <polygon points="0,-14 -10,0 0,0" fill="#f7fdff"/>
      <polygon points="0,-14 10,0 0,0" fill="#c8ecff"/>
      <polygon points="0,16 -10,0 0,0" fill="#7eb8dc"/>
      <polygon points="0,16 10,0 0,0" fill="#e7f7ff"/>
      <polygon points="0,-3 -3,1 0,6 3,1" fill="#ffffff" opacity=".9"/>
    </g>
    <g transform="translate(46 64) scale(.62)">
      <polygon points="0,-14 -10,0 0,0" fill="#f7fdff"/>
      <polygon points="0,-14 10,0 0,0" fill="#c8ecff"/>
      <polygon points="0,16 -10,0 0,0" fill="#7eb8dc"/>
      <polygon points="0,16 10,0 0,0" fill="#e7f7ff"/>
    </g>
    <g transform="translate(194 64) scale(.62)">
      <polygon points="0,-14 -10,0 0,0" fill="#f7fdff"/>
      <polygon points="0,-14 10,0 0,0" fill="#c8ecff"/>
      <polygon points="0,16 -10,0 0,0" fill="#7eb8dc"/>
      <polygon points="0,16 10,0 0,0" fill="#e7f7ff"/>
    </g>
    <g transform="translate(120 22) scale(.7)">
      <polygon points="0,-14 -10,0 0,0" fill="#f7fdff"/>
      <polygon points="0,-14 10,0 0,0" fill="#c8ecff"/>
      <polygon points="0,16 -10,0 0,0" fill="#7eb8dc"/>
      <polygon points="0,16 10,0 0,0" fill="#e7f7ff"/>
    </g>
    <circle cx="78" cy="157" r="6" fill="url(#${id}-emerald)" stroke="#f0c45a" stroke-width="1"/>
    <circle cx="162" cy="157" r="6" fill="url(#${id}-sapphire)" stroke="#f0c45a" stroke-width="1"/>
    <circle cx="96" cy="100" r="5" fill="url(#${id}-ruby)" stroke="#f0c45a"/>
    <circle cx="144" cy="100" r="5" fill="url(#${id}-ruby)" stroke="#f0c45a"/>
    <circle cx="70" cy="112" r="3.2" fill="#fff8ee" stroke="#e6d3a2"/>
    <circle cx="170" cy="112" r="3.2" fill="#fff8ee" stroke="#e6d3a2"/>
    <circle cx="86" cy="92" r="2.6" fill="#fff8ee"/>
    <circle cx="154" cy="92" r="2.6" fill="#fff8ee"/>
    <path fill="url(#${id}-gold)" stroke="#fff6d4" stroke-width=".6" d="M120 8 l3 8 h8 l-6 5 2 8 -7 -5 -7 5 2 -8 -6 -5 h8 z"/>
  </svg>`;
}

function heroFigure(build, crowned) {
  const figure = document.createElement("figure");
  figure.className = "forge-victor";
  const caption = document.createElement("figcaption");
  caption.textContent = build?.name || "无名";
  const meta = document.createElement("span");
  meta.textContent = [build?.role, build?.attribute, build?.race].filter(Boolean).join(" · ");
  figure.append(crownIcon(Boolean(crowned)), caption, meta);
  return figure;
}

function resumeButton() {
  const go = document.createElement("button");
  go.type = "button";
  go.className = "primary forge-resume-go";
  go.textContent = "开始新一轮";
  go.addEventListener("click", () => {
    go.disabled = true;
    void postForge("ack-run", {}).finally(() => {
      if (go.isConnected) go.disabled = false;
    });
  });
  return go;
}

function mountResumeButton(stage) {
  const card = stage.querySelector(".forge-ceremony__card") || stage;
  if (card.querySelector(".forge-resume-go")) return;
  card.append(resumeButton());
}

function ceremony(forge, resume) {
  const saved = resume ? forge.ceremony_view : null;
  const crowned = saved ? Boolean(saved.hidden_clear) : Boolean(forge.hidden_clear);
  const builds = saved?.builds || forge.builds || {};
  const stage = document.createElement("section");
  stage.className = `forge-ceremony${crowned ? " is-crowned" : ""}`;
  for (let index = 0; index < 22; index += 1) {
    const bit = document.createElement("i");
    bit.className = "forge-confetti";
    bit.style.left = `${(index * 37) % 100}%`;
    bit.style.animationDelay = `${(index % 8) * 0.18}s`;
    stage.append(bit);
  }
  const card = document.createElement("div");
  card.className = "forge-ceremony__card";
  const title = document.createElement("h2");
  title.textContent = crowned ? "隐藏试炼已通关" : "三层全过";
  const line = document.createElement("p");
  line.textContent = crowned ? "真正的对手也被你们打倒了。" : "这一趟试炼到此结束。";
  const lineup = document.createElement("div");
  lineup.className = "forge-ceremony__lineup";
  lineup.append(heroFigure(builds["1"] || builds[1], crowned), heroFigure(builds["2"] || builds[2], crowned));
  card.append(title, line, lineup);
  if (resume) card.append(resumeButton());
  else if (state.room?.viewer_is_host) {
    const again = document.createElement("button");
    again.type = "button";
    again.className = "primary";
    again.textContent = "再来一趟";
    again.addEventListener("click", () => {
      again.disabled = true;
      void postForge("coop-again", {});
    });
    card.append(again);
  }
  stage.append(card);
  return stage;
}

function omenScreen() {
  const stage = document.createElement("section");
  stage.className = "forge-omen";
  const calm = document.createElement("div");
  calm.className = "forge-omen__calm";
  const done = document.createElement("h2");
  done.textContent = "三层全过";
  const quiet = document.createElement("p");
  quiet.textContent = "试炼到此结束。";
  calm.append(done, quiet);
  const twist = document.createElement("div");
  twist.className = "forge-omen__twist";
  const title = document.createElement("h2");
  title.textContent = "隐藏事件";
  const line = document.createElement("p");
  line.textContent = "真正的对手还在后面。";
  const go = document.createElement("button");
  go.type = "button";
  go.className = "primary";
  go.textContent = "迎战";
  go.disabled = true;
  window.setTimeout(() => {
    go.disabled = false;
  }, 2800);
  go.addEventListener("click", () => {
    go.disabled = true;
    void postForge("ack-omen", {});
  });
  twist.append(title, line, go);
  stage.append(calm, twist);
  return stage;
}

export function renderForgePanel() {
  const panel = $("forge-panel");
  if (!panel) return;
  const room = state.room;
  const active = room?.mode === "skill_random" && room?.status === "forge";
  panel.classList.toggle("hidden", !active);
  document.body?.classList?.toggle("forge-mode", Boolean(active));
  if (!active) {
    hideForgeTip();
    closeForgeRoll();
    releaseForgeOverlay();
    return;
  }
  const editing = panel.contains(document.activeElement)
    && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName);
  if (editing || forgeOverlayOpen) return;
  const forge = room.forge || {};
  if (forge.phase === "resume") {
    const existing = panel.querySelector(".forge-ceremony");
    if (existing) {
      mountResumeButton(existing);
      panel.dataset.stamp = `resume:${forge.run || 1}`;
      return;
    }
  }
  const stamp = `${forge.phase}:${forge.hidden_clear ? 1 : 0}:${forge.run || 1}`;
  if ((forge.phase === "ceremony" || forge.phase === "omen" || forge.phase === "resume") && panel.dataset.stamp === stamp && panel.childElementCount) return;
  const team = viewerTeam();
  panel.dataset.stamp = "";
  panel.replaceChildren();
  if (forge.phase === "ceremony") {
    panel.dataset.stamp = stamp;
    panel.append(ceremony(forge));
    return;
  }
  if (forge.phase === "omen") {
    panel.dataset.stamp = stamp;
    panel.append(omenScreen());
    return;
  }
  if (forge.phase === "resume") {
    panel.dataset.stamp = stamp;
    panel.append(ceremony(forge, true));
    return;
  }
  const rivals = rivalStrip(forge);
  const banner = stageBanner(forge);
  if (banner) panel.append(banner);
  const layout = document.createElement("div");
  layout.className = forge.play === "coop" ? "forge-layout" : "forge-layout is-solo";
  const main = document.createElement("div");
  main.className = "forge-main";
  if (forge.phase === "reveal") {
    if (rivals) layout.append(rivals);
    main.append(reveal(forge));
    layout.append(main);
    panel.append(layout);
    return;
  }
  const build = {...(forge.builds?.[String(team)] || forge.builds?.[team] || {}), team};
  const catalog = forge.catalog || {skills: [], traits: [], races: [], attributes: []};
  const bar = document.createElement("header");
  bar.className = "forge-bar";
  const points = document.createElement("div");
  points.className = "forge-points";
  const budget = pointBudget(build);
  const remain = document.createElement("b");
  remain.textContent = String(budget - spent(build, catalog));
  const prefix = document.createElement("span");
  prefix.textContent = "剩余";
  const suffix = document.createElement("span");
  suffix.textContent = "点";
  points.append(prefix, remain, suffix);
  const roundNote = forge.round_note?.[String(team)] ?? forge.round_note?.[team];
  const roundAmount = Number(roundNote?.amount || 0);
  if (roundNote && roundAmount > 0) {
    const note = document.createElement("span");
    note.className = "forge-points__note";
    note.textContent = roundNote.kind === "promote"
      ? `（晋级奖励${roundAmount}点）`
      : `（已补偿${roundAmount}点）`;
    points.append(note);
  }
  const ready = document.createElement("button");
  ready.type = "button";
  const mine = Boolean(forge.ready?.[String(team)] ?? forge.ready?.[team]);
  ready.className = `forge-ready ${mine ? "is-ready" : "is-waiting"}`;
  ready.textContent = mine ? "已准备" : "未准备";
  ready.disabled = !canEdit(team);
  ready.addEventListener("click", () => {
    void postForge("ready", {team_id: team, ready: !mine});
  });
  const actions = document.createElement("div");
  actions.className = "forge-actions";
  const roll = document.createElement("button");
  roll.type = "button";
  roll.className = "ghost forge-roll-open";
  roll.textContent = "随机生成";
  const remainingPoints = pointBudget(build) - spent(build, catalog);
  roll.disabled = !canEdit(team);
  roll.addEventListener("click", () => openForgeRoll(build, catalog, remainingPoints));
  actions.append(forgeMoreControl(build, canEdit(team)), roll, ready);
  bar.append(points, actions);
  const body = document.createElement("div");
  body.className = "forge-body";
  if (canEdit(team)) body.append(cardFace(build, catalog), picker(build, catalog));
  if (rivals) layout.append(rivals);
  main.append(bar, body);
  layout.append(main);
  panel.append(layout);
}

export function renderForgeLobbyConfig() {
  const host = $("forge-lobby-config");
  if (!host) return;
  host.classList.add("hidden");
}
