// 数据库：只读的武将全表，和战绩回放放在同一页里切换。
import { $ } from '../core/dom.js';
import { confirmDialog } from '../core/dialog.js';
import { fetchJson } from '../core/net.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';

const STAT_LABELS = [
  ["attack", "攻击"],
  ["defense", "防御"],
  ["speed", "速度"],
  ["attack_range", "范围"],
  ["mana", "魔力"],
];

function heroMatches(hero, query) {
  if (!query) return true;
  const haystack = [hero.name, hero.role, hero.attribute, hero.race, hero.raw_skill_text, hero.raw_trait_text]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function renderHeroDetail(hero) {
  const detail = $("library-hero-detail");
  if (!detail) return;
  detail.replaceChildren();
  if (!hero) {
    const empty = document.createElement("p");
    empty.className = "meta-note";
    empty.textContent = "从左侧选一名武将。";
    detail.append(empty);
    return;
  }
  const title = document.createElement("h3");
  title.textContent = hero.name;
  const meta = document.createElement("p");
  meta.className = "library-hero-meta";
  meta.textContent = `Lv ${hero.level} · ${hero.role || "—"} · ${hero.attribute || "—"} · ${hero.race || "—"}`;
  const stats = document.createElement("div");
  stats.className = "library-stats";
  STAT_LABELS.forEach(([key, label]) => {
    const cell = document.createElement("span");
    cell.textContent = `${label} ${hero.stats?.[key] ?? "—"}`;
    stats.append(cell);
  });
  detail.append(title, meta, stats);
  [
    ["技能", hero.raw_skill_text],
    ["特性", hero.raw_trait_text],
    ["天气", hero.weather_effect_text],
  ].forEach(([label, text]) => {
    if (!text) return;
    const block = document.createElement("section");
    const heading = document.createElement("h4");
    heading.textContent = label;
    const body = document.createElement("p");
    body.textContent = text;
    block.append(heading, body);
    detail.append(block);
  });
}

export function renderLibrary() {
  const heroesPane = $("library-heroes");
  const customPane = $("library-custom");
  const onLibrary = state.homeFlow === "library";
  const tab = state.libraryTab || "heroes";
  const showReplays = onLibrary && tab === "replays";
  const showCustom = onLibrary && tab === "custom";
  const showHeroes = onLibrary && tab === "heroes";
  heroesPane?.classList.toggle("hidden", !showHeroes);
  customPane?.classList.toggle("hidden", !showCustom);
  $("library-tab-heroes")?.classList.toggle("is-on", showHeroes);
  $("library-tab-custom")?.classList.toggle("is-on", showCustom);
  $("library-tab-replays")?.classList.toggle("is-on", showReplays);
  if (showCustom) {
    renderCustomLibrary();
    return;
  }
  if (!showHeroes) return;

  const query = String(state.libraryHeroQuery || "").trim().toLowerCase();
  const level = Number(state.libraryLevel || 0);
  const sortKey = state.librarySort || "level";
  const direction = state.librarySortDir === "desc" ? -1 : 1;
  const search = $("library-hero-search");
  if (search && document.activeElement !== search) search.value = state.libraryHeroQuery || "";
  const levelSelect = $("library-level");
  if (levelSelect && document.activeElement !== levelSelect) levelSelect.value = String(level || 0);
  const sortSelect = $("library-sort");
  if (sortSelect && document.activeElement !== sortSelect) sortSelect.value = sortKey;
  const dirButton = $("library-sort-dir");
  if (dirButton) dirButton.textContent = direction < 0 ? "倒序" : "正序";
  const heroes = [...(state.heroes || [])]
    .filter((hero) => heroMatches(hero, query) && (!level || Number(hero.level) === level))
    .sort((left, right) => {
      const delta = sortKey === "name"
        ? String(left.name).localeCompare(right.name, "zh")
        : sortKey === "level"
          ? Number(left.level) - Number(right.level)
          : Number(left.stats?.[sortKey] || 0) - Number(right.stats?.[sortKey] || 0);
      if (delta) return delta * direction;
      return String(left.name).localeCompare(right.name, "zh");
    });
  const list = $("library-hero-list");
  if (!list) return;
  list.replaceChildren();
  if (!state.libraryHeroCode || !heroes.some((hero) => hero.code === state.libraryHeroCode)) {
    state.libraryHeroCode = heroes[0]?.code || "";
  }
  heroes.forEach((hero) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `library-hero${hero.code === state.libraryHeroCode ? " is-on" : ""}`;
    button.textContent = `${hero.name}  ${hero.level}`;
    button.addEventListener("click", () => {
      state.libraryHeroCode = hero.code;
      renderLibrary();
    });
    list.append(button);
  });
  if (!heroes.length) {
    const empty = document.createElement("p");
    empty.className = "meta-note";
    empty.textContent = "没有符合的武将。";
    list.append(empty);
  }
  renderHeroDetail(heroes.find((hero) => hero.code === state.libraryHeroCode));
}

const CUSTOM_STAT_LABELS = [
  ["attack", "攻击"],
  ["defense", "防御"],
  ["speed", "速度"],
  ["attack_range", "范围"],
  ["mana", "魔力"],
];

function customMatches(hero, query) {
  if (!query) return true;
  const parts = [
    hero.name,
    hero.role,
    hero.attribute,
    hero.race,
    hero.intro,
    ...(hero.tags || []),
    ...(hero.skills || []).map((item) => `${item.name || ""} ${item.description || ""}`),
    ...(hero.traits || []).map((item) => `${item.name || ""} ${item.description || ""}`),
  ];
  return parts.filter(Boolean).join(" ").toLowerCase().includes(query);
}

async function loadCustomHeroes() {
  if (state.customHeroesBusy) return;
  state.customHeroesBusy = true;
  try {
    const payload = await fetchJson("/api/custom-heroes");
    state.customHeroes = Array.isArray(payload.heroes) ? payload.heroes : [];
    state.customHeroesError = "";
    state.customHeroesLoaded = true;
  } catch (error) {
    state.customHeroesError = typeof error?.error === "string" ? error.error : "没能读到自建武将。";
    state.customHeroesLoaded = true;
  } finally {
    state.customHeroesBusy = false;
    render();
  }
}

function renderCustomDetail(hero) {
  const detail = $("library-custom-detail");
  if (!detail) return;
  detail.replaceChildren();
  if (!hero) {
    const empty = document.createElement("p");
    empty.className = "meta-note";
    empty.textContent = state.customHeroesError || (state.customHeroesBusy ? "正在读取自建武将..." : "从左侧选一名武将。");
    detail.append(empty);
    return;
  }
  const title = document.createElement("h3");
  title.textContent = hero.name || "未命名";
  const tag = document.createElement("p");
  tag.className = "library-hero-meta";
  tag.textContent = "自建";
  const meta = document.createElement("p");
  meta.className = "library-hero-meta";
  meta.textContent = `Lv ${hero.level} · ${hero.role || "—"} · ${hero.attribute || "—"} · ${hero.race || "—"}`;
  const stats = document.createElement("div");
  stats.className = "library-stats";
  CUSTOM_STAT_LABELS.forEach(([key, label]) => {
    const cell = document.createElement("span");
    cell.textContent = `${label} ${hero.stats?.[key] ?? "—"}`;
    stats.append(cell);
  });
  detail.append(title, tag, meta, stats);
  if (hero.intro) {
    const block = document.createElement("section");
    const heading = document.createElement("h4");
    heading.textContent = "介绍";
    const body = document.createElement("p");
    body.textContent = hero.intro;
    block.append(heading, body);
    detail.append(block);
  }
  [
    ["技能", hero.skills],
    ["特性", hero.traits],
  ].forEach(([label, parts]) => {
    const block = document.createElement("section");
    const heading = document.createElement("h4");
    heading.textContent = label;
    block.append(heading);
    if (!parts?.length) {
      const empty = document.createElement("p");
      empty.textContent = "无";
      block.append(empty);
    } else {
      parts.forEach((part) => {
        const name = document.createElement("p");
        const strong = document.createElement("strong");
        strong.textContent = part.name || "未命名";
        name.append(strong);
        block.append(name);
        if (part.description) {
          const body = document.createElement("p");
          body.textContent = part.description;
          block.append(body);
        }
      });
    }
    detail.append(block);
  });
  if (state.customDeleteError) {
    const error = document.createElement("p");
    error.className = "meta-note";
    error.textContent = state.customDeleteError;
    detail.append(error);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "danger library-custom-delete";
  remove.textContent = "删除";
  remove.addEventListener("click", () => {
    void deleteCustomHero(hero);
  });
  detail.append(remove);
}

function renderCustomLibrary() {
  if (!state.customHeroesLoaded && !state.customHeroesBusy) void loadCustomHeroes();
  const query = String(state.customHeroQuery || "").trim().toLowerCase();
  const level = Number(state.customLevel || 0);
  const sortKey = state.customSort || "created";
  const direction = state.customSortDir === "asc" ? 1 : -1;
  const search = $("library-custom-search");
  if (search && document.activeElement !== search) search.value = state.customHeroQuery || "";
  const levelSelect = $("library-custom-level");
  if (levelSelect && document.activeElement !== levelSelect) levelSelect.value = String(level || 0);
  const sortSelect = $("library-custom-sort");
  if (sortSelect && document.activeElement !== sortSelect) sortSelect.value = sortKey;
  const dirButton = $("library-custom-sort-dir");
  if (dirButton) dirButton.textContent = direction < 0 ? "倒序" : "正序";
  const heroes = [...(state.customHeroes || [])]
    .filter((hero) => customMatches(hero, query) && (!level || Number(hero.level) === level))
    .sort((left, right) => {
      const delta = sortKey === "name"
        ? String(left.name || "").localeCompare(right.name || "", "zh")
        : sortKey === "created"
          ? Number(left.created_at || 0) - Number(right.created_at || 0)
          : sortKey === "level"
            ? Number(left.level) - Number(right.level)
            : Number(left.stats?.[sortKey] || 0) - Number(right.stats?.[sortKey] || 0);
      if (delta) return delta * direction;
      return String(left.name || "").localeCompare(right.name || "", "zh");
    });
  const list = $("library-custom-list");
  if (!list) return;
  list.replaceChildren();
  if (!state.customHeroId || !heroes.some((hero) => hero.id === state.customHeroId)) {
    state.customHeroId = heroes[0]?.id || "";
  }
  heroes.forEach((hero) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `library-hero${hero.id === state.customHeroId ? " is-on" : ""}`;
    button.textContent = `${hero.name || "未命名"}  ${hero.level}  自建`;
    button.addEventListener("click", () => {
      state.customHeroId = hero.id;
      renderLibrary();
    });
    list.append(button);
  });
  if (!heroes.length) {
    const empty = document.createElement("p");
    empty.className = "meta-note";
    empty.textContent = state.customHeroesError
      || (state.customHeroesBusy ? "正在读取自建武将..." : "还没有存下来的武将。");
    list.append(empty);
  }
  renderCustomDetail(heroes.find((hero) => hero.id === state.customHeroId));
}

async function deleteCustomHero(hero) {
  const confirmed = await confirmDialog({
    title: "删除自建武将",
    body: `「${hero.name || "未命名"}」会从自建列表里去掉。`,
    confirmLabel: "删除",
    tone: "danger",
  });
  if (!confirmed) return;
  state.customDeleteError = "";
  try {
    const payload = await fetchJson("/api/custom-heroes/delete", {
      method: "POST",
      body: JSON.stringify({id: hero.id}),
    });
    state.customHeroes = Array.isArray(payload.heroes) ? payload.heroes : [];
    state.customHeroesLoaded = true;
    if (state.customHeroId === hero.id) state.customHeroId = "";
  } catch (error) {
    state.customDeleteError = typeof error?.error === "string" ? error.error : "没能删掉这名武将。";
  }
  render();
}