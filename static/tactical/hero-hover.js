let hoverCard = null;
let activeAnchor = null;

function detail(label, value, extraClass = '') {
  const row = document.createElement('div');
  row.className = `hero-hover__row${extraClass ? ` ${extraClass}` : ''}`;
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

function statRow(hero) {
  const row = document.createElement('div');
  row.className = 'hero-hover__row';
  const caption = document.createElement('span');
  caption.className = 'hero-hover__label';
  caption.textContent = '数值';
  const body = document.createElement('span');
  const stats = hero.stats || {};
  const upgrades = hero.upgrades || {};
  [['attack', '攻'], ['defense', '守'], ['speed', '速'], ['attack_range', '范'], ['mana', '魔']].forEach(([key, label], index) => {
    if (index) body.append(' · ');
    body.append(`${label}${stats[key] ?? 0}`);
    const plus = Number(upgrades[key] || 0);
    if (plus > 0) {
      const mark = document.createElement('b');
      mark.className = 'hero-hover__plus';
      mark.textContent = `+${plus}`;
      body.append(mark);
    }
  });
  row.append(caption, body);
  return row;
}

function heroDetailNodes(hero) {
  const title = document.createElement('strong');
  title.className = 'hero-hover__title';
  title.textContent = `${hero.name} · Lv ${hero.level}`;
  const synergies = (hero.active_synergies || []).filter(Boolean);
  return [
    title,
    detail('定位', [hero.role, hero.attribute, hero.race].filter(Boolean).join(' / ')),
    statRow(hero),
    ...(synergies.length ? [detail('羁绊', synergies.join('；'), 'is-synergy')] : []),
    detail('技能', hero.raw_skill_text),
    detail('特性', hero.raw_trait_text),
    ...(hero.weather_effect_text ? [detail('天气效果', hero.weather_effect_text)] : []),
    ...(hero.entry_companion ? [detail('开场召唤', hero.entry_companion)] : []),
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
  if (Number(hero.star) > 0) {
    const badge = document.createElement('span');
    badge.className = 'hero-hover__star';
    badge.textContent = `★${hero.star}`;
    card.append(badge);
  }
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
