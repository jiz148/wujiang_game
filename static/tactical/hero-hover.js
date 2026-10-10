let hoverCard = null;
let activeAnchor = null;

function detail(label, value) {
  const row = document.createElement('div');
  row.className = 'hero-hover__row';
  const caption = document.createElement('span');
  caption.className = 'hero-hover__label';
  caption.textContent = label;
  const body = document.createElement('span');
  body.textContent = value || '无';
  row.append(caption, body);
  return row;
}

export function hideHeroHover(anchor = null) {
  if (anchor && activeAnchor !== anchor) return;
  hoverCard?.remove();
  hoverCard = null;
  activeAnchor = null;
}

function heroDetailNodes(hero) {
  const title = document.createElement('strong');
  title.className = 'hero-hover__title';
  title.textContent = `${hero.name} · Lv ${hero.level}`;
  return [
    title,
    detail('定位', [hero.role, hero.attribute, hero.race].filter(Boolean).join(' / ')),
    detail('数值', `攻 ${hero.stats?.attack ?? 0} · 守 ${hero.stats?.defense ?? 0} · 速 ${hero.stats?.speed ?? 0} · 范 ${hero.stats?.attack_range ?? 0} · 魔 ${hero.stats?.mana ?? 0}`),
    detail('技能', hero.raw_skill_text),
    detail('特性', hero.raw_trait_text),
    ...(hero.weather_effect_text ? [detail('天气效果', hero.weather_effect_text)] : []),
    ...(hero.entry_companion ? [detail('开场召唤', hero.entry_companion)] : []),
    ...((hero.synergies || []).map((item) => detail(
      `${item.category_name}羁绊 · ${item.name}`,
      `3名：${item.first}；6名：${item.second}`,
    ))),
  ];
}

export function renderHeroDetails(container, hero) {
  container.replaceChildren(...heroDetailNodes(hero));
}

export function showHeroHover(hero, anchor) {
  if (!hero || !anchor || !anchor.isConnected) return;
  hideHeroHover();
  activeAnchor = anchor;
  const card = document.createElement('aside');
  card.className = 'hero-hover';
  card.setAttribute('role', 'tooltip');
  card.append(...heroDetailNodes(hero));
  document.body.append(card);
  hoverCard = card;
  const anchorRect = anchor.getBoundingClientRect();
  const cardRect = card.getBoundingClientRect();
  const gap = 12;
  const margin = 8;
  const right = anchorRect.right + gap;
  const left = anchorRect.left - cardRect.width - gap;
  const fitsRight = right + cardRect.width <= window.innerWidth - margin;
  const fitsLeft = left >= margin;
  const x = fitsRight ? right : fitsLeft ? left
    : Math.max(margin, Math.min(anchorRect.left, window.innerWidth - cardRect.width - margin));
  let y = Math.max(margin, Math.min(anchorRect.top, window.innerHeight - cardRect.height - margin));
  if (!fitsRight && !fitsLeft) {
    const below = anchorRect.bottom + gap;
    const above = anchorRect.top - cardRect.height - gap;
    if (below + cardRect.height <= window.innerHeight - margin) y = below;
    else if (above >= margin) y = above;
  }
  card.style.left = `${x}px`;
  card.style.top = `${y}px`;
}

export function bindHeroHover(anchor, hero) {
  anchor.addEventListener('mouseenter', () => showHeroHover(hero, anchor));
  anchor.addEventListener('mouseleave', () => hideHeroHover(anchor));
  anchor.addEventListener('focus', () => showHeroHover(hero, anchor));
  anchor.addEventListener('blur', () => hideHeroHover(anchor));
}
