// 数据库：只读的武将全表，和战绩回放放在同一页里切换。
import { $ } from '../core/dom.js';
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
  const onLibrary = state.homeFlow === "library";
  const showReplays = onLibrary && state.libraryTab === "replays";
  heroesPane?.classList.toggle("hidden", !onLibrary || showReplays);
  $("library-tab-heroes")?.classList.toggle("is-on", !showReplays);
  $("library-tab-replays")?.classList.toggle("is-on", showReplays);
  if (!onLibrary || showReplays) return;

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