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
  const skillsPane = $("library-skills");
  const traitsPane = $("library-traits");
  const onLibrary = state.homeFlow === "library";
  const tab = state.libraryTab || "heroes";
  const showReplays = onLibrary && tab === "replays";
  const showCustom = onLibrary && tab === "custom";
  const showSkills = onLibrary && tab === "skills";
  const showTraits = onLibrary && tab === "traits";
  const showHeroes = onLibrary && tab === "heroes";
  heroesPane?.classList.toggle("hidden", !showHeroes);
  customPane?.classList.toggle("hidden", !showCustom);
  skillsPane?.classList.toggle("hidden", !showSkills);
  traitsPane?.classList.toggle("hidden", !showTraits);
  $("library-tab-heroes")?.classList.toggle("is-on", showHeroes);
  $("library-tab-skills")?.classList.toggle("is-on", showSkills);
  $("library-tab-traits")?.classList.toggle("is-on", showTraits);
  $("library-tab-custom")?.classList.toggle("is-on", showCustom);
  $("library-tab-replays")?.classList.toggle("is-on", showReplays);
  if (showTraits) {
    renderTraitLibrary();
    return;
  }
  if (showSkills) {
    renderSkillLibrary();
    return;
  }
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

async function loadSkillLibrary() {
  if (state.librarySkillsBusy) return;
  state.librarySkillsBusy = true;
  try {
    const payload = await fetchJson("/api/skills");
    state.librarySkills = Array.isArray(payload.skills) ? payload.skills : [];
    state.librarySkillsError = "";
    state.librarySkillsLoaded = true;
  } catch (error) {
    state.librarySkillsError = typeof error?.error === "string" ? error.error : "没能读到技能。";
    state.librarySkillsLoaded = true;
  } finally {
    state.librarySkillsBusy = false;
    render();
  }
}

function matchesLibrarySkillKind(skill, kind) {
  if (!kind || kind === "all") return true;
  const tokens = String(skill?.search || "").split(/\s+/).filter(Boolean);
  if (kind === "ultimate") return Boolean(skill.ultimate);
  if (kind === "passive") return tokens.includes("被动");
  if (kind === "active") return !tokens.includes("被动");
  if (kind === "mana") return tokens.includes("费魔");
  if (kind === "free") return tokens.includes("不费魔");
  return true;
}

function renderSkillDetail(skill) {
  const detail = $("library-skill-detail");
  if (!detail) return;
  detail.replaceChildren();
  if (!skill) {
    const empty = document.createElement("p");
    empty.className = "meta-note";
    empty.textContent = state.librarySkillsError || (state.librarySkillsBusy ? "正在读取技能..." : "从左侧选一个技能。");
    detail.append(empty);
    return;
  }
  const title = document.createElement("h3");
  title.textContent = skill.name || "未命名";
  const meta = document.createElement("p");
  meta.className = "library-hero-meta";
  const tokens = String(skill.search || "").split(/\s+/).filter(Boolean);
  const marks = [
    skill.ultimate ? "大招" : "技能",
    tokens.includes("被动") ? "被动" : "主动",
    tokens.includes("费魔") ? "费魔" : "不费魔",
  ];
  meta.textContent = marks.join(" · ");
  const from = document.createElement("p");
  from.className = "library-hero-meta";
  from.textContent = (skill.heroes || []).join("、") || "没有来源武将";
  detail.append(title, meta, from);
  const body = document.createElement("p");
  body.textContent = skill.description || "没有介绍";
  detail.append(body);
}

function renderSkillLibrary() {
  if (!state.librarySkillsLoaded && !state.librarySkillsBusy) void loadSkillLibrary();
  const query = String(state.librarySkillQuery || "").trim().toLowerCase();
  const kind = state.librarySkillKind || "all";
  const sortKey = state.librarySkillSort || "name";
  const search = $("library-skill-search");
  if (search && document.activeElement !== search) search.value = state.librarySkillQuery || "";
  const kindSelect = $("library-skill-kind");
  if (kindSelect && document.activeElement !== kindSelect) kindSelect.value = kind;
  const sortSelect = $("library-skill-sort");
  if (sortSelect && document.activeElement !== sortSelect) sortSelect.value = sortKey;
  const skills = [...(state.librarySkills || [])]
    .filter((skill) => {
      if (!matchesLibrarySkillKind(skill, kind)) return false;
      if (!query) return true;
      const haystack = [skill.name, skill.description, skill.search, ...(skill.heroes || [])].filter(Boolean).join(" ").toLowerCase();
      return haystack.includes(query);
    })
    .sort((left, right) => String(left.name || "").localeCompare(right.name || "", "zh"));
  const list = $("library-skill-list");
  if (!list) return;
  list.replaceChildren();
  if (!state.librarySkillId || !skills.some((skill) => skill.id === state.librarySkillId)) {
    state.librarySkillId = skills[0]?.id || "";
  }
  skills.forEach((skill) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `library-hero${skill.id === state.librarySkillId ? " is-on" : ""}`;
    button.textContent = skill.name || "未命名";
    button.addEventListener("click", () => {
      state.librarySkillId = skill.id;
      renderLibrary();
    });
    list.append(button);
  });
  if (!skills.length) {
    const empty = document.createElement("p");
    empty.className = "meta-note";
    empty.textContent = state.librarySkillsError
      || (state.librarySkillsBusy ? "正在读取技能..." : "没有符合的技能。");
    list.append(empty);
  }
  renderSkillDetail(skills.find((skill) => skill.id === state.librarySkillId));
}

function traitIsStrong(trait) {
  if (trait?.category === "strong") return true;
  if (trait?.category === "normal") return false;
  return trait?.name === "魔免" || trait?.name === "物免";
}

async function loadTraitLibrary() {
  if (state.libraryTraitsBusy) return;
  state.libraryTraitsBusy = true;
  try {
    const payload = await fetchJson("/api/traits");
    state.libraryTraits = Array.isArray(payload.traits) ? payload.traits : [];
    state.libraryTraitsError = "";
    state.libraryTraitsLoaded = true;
  } catch (error) {
    state.libraryTraitsError = typeof error?.error === "string" ? error.error : "没能读到特性。";
    state.libraryTraitsLoaded = true;
  } finally {
    state.libraryTraitsBusy = false;
    render();
  }
}

function stampTraitCategory(name, category) {
  for (const trait of state.libraryTraits || []) {
    if (trait.name === name) trait.category = category;
  }
  for (const trait of state.room?.forge?.catalog?.traits || []) {
    if (trait.name === name) trait.category = category;
  }
}

async function saveTraitCategory(trait, category) {
  state.libraryTraitError = "";
  try {
    const payload = await fetchJson("/api/traits/category", {
      method: "POST",
      body: JSON.stringify({name: trait.name, category}),
    });
    if (Array.isArray(payload.traits)) state.libraryTraits = payload.traits;
    else stampTraitCategory(trait.name, category);
    stampTraitCategory(trait.name, category);
    state.libraryTraitsLoaded = true;
  } catch (error) {
    state.libraryTraitError = typeof error?.error === "string" ? error.error : "没能改这个特性的类别。";
  }
  render();
}

function renderTraitDetail(trait) {
  const detail = $("library-trait-detail");
  if (!detail) return;
  detail.replaceChildren();
  if (!trait) {
    const empty = document.createElement("p");
    empty.className = "meta-note";
    empty.textContent = state.libraryTraitsError || (state.libraryTraitsBusy ? "正在读取特性..." : "从左侧选一个特性。");
    detail.append(empty);
    return;
  }
  const title = document.createElement("h3");
  title.textContent = trait.name || "未命名";
  const meta = document.createElement("p");
  meta.className = "library-hero-meta";
  meta.textContent = traitIsStrong(trait) ? "强力" : "一般";
  const from = document.createElement("p");
  from.className = "library-hero-meta";
  from.textContent = (trait.heroes || []).join("、") || "没有来源武将";
  const body = document.createElement("p");
  body.textContent = trait.description || "没有介绍";
  detail.append(title, meta, from, body);
  if (state.libraryTraitError) {
    const error = document.createElement("p");
    error.className = "meta-note";
    error.textContent = state.libraryTraitError;
    detail.append(error);
  }
  const field = document.createElement("label");
  field.className = "field library-trait-category";
  const label = document.createElement("span");
  label.className = "field__label";
  label.textContent = "类别";
  const select = document.createElement("select");
  select.className = "select";
  for (const [value, text] of [["normal", "一般"], ["strong", "强力"]]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    select.append(option);
  }
  select.value = traitIsStrong(trait) ? "strong" : "normal";
  select.addEventListener("change", () => {
    void saveTraitCategory(trait, select.value);
  });
  field.append(label, select);
  detail.append(field);
}

function renderTraitLibrary() {
  if (!state.libraryTraitsLoaded && !state.libraryTraitsBusy) void loadTraitLibrary();
  const query = String(state.libraryTraitQuery || "").trim().toLowerCase();
  const kind = state.libraryTraitKind || "all";
  const search = $("library-trait-search");
  if (search && document.activeElement !== search) search.value = state.libraryTraitQuery || "";
  const kindSelect = $("library-trait-kind");
  if (kindSelect && document.activeElement !== kindSelect) kindSelect.value = kind;
  const traits = [...(state.libraryTraits || [])]
    .filter((trait) => {
      if (kind === "strong" && !traitIsStrong(trait)) return false;
      if (kind === "normal" && traitIsStrong(trait)) return false;
      if (!query) return true;
      const haystack = [trait.name, trait.description, ...(trait.heroes || [])].filter(Boolean).join(" ").toLowerCase();
      return haystack.includes(query);
    })
    .sort((left, right) => String(left.name || "").localeCompare(right.name || "", "zh"));
  const list = $("library-trait-list");
  if (!list) return;
  list.replaceChildren();
  if (!state.libraryTraitId || !traits.some((trait) => trait.id === state.libraryTraitId)) {
    state.libraryTraitId = traits[0]?.id || "";
  }
  traits.forEach((trait) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `library-hero${trait.id === state.libraryTraitId ? " is-on" : ""}`;
    button.textContent = traitIsStrong(trait) ? `${trait.name || "未命名"}  强力` : (trait.name || "未命名");
    button.addEventListener("click", () => {
      state.libraryTraitId = trait.id;
      renderLibrary();
    });
    list.append(button);
  });
  if (!traits.length) {
    const empty = document.createElement("p");
    empty.className = "meta-note";
    empty.textContent = state.libraryTraitsError
      || (state.libraryTraitsBusy ? "正在读取特性..." : "没有符合的特性。");
    list.append(empty);
  }
  renderTraitDetail(traits.find((trait) => trait.id === state.libraryTraitId));
}