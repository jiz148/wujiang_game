// Auto chess preparation, standings and read-only battle observation.
import { $ } from '../core/dom.js';
import { fetchJson } from '../core/net.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { setScreen } from '../core/ui.js';
import { bindHeroHover, hideHeroHover, renderHeroDetails } from './hero-hover.js';
import { applyRoomPayload, setRoomChessBattleSpeed } from './room-api.js';

let selectedPieceId = '';
let selectedHeroCode = '';
let selectedMatchIndex = 0;
let watchSeatId = 0;
let actionMessage = '';
let actionError = '';
let messageRound = 0;
let serverClockOffset = 0;
let lastServerTimestamp = 0;

function selectedHero(chess) {
  const me = chess.players.find((player) => player.seat_id === chess.viewer_id);
  const code = (me?.pieces || []).find((piece) => piece.id === selectedPieceId)?.code || selectedHeroCode;
  return chess.hero_previews?.[code] || null;
}

function companionName(description) {
  return String(description || '').match(/召唤出自己的([^，。]+)/)?.[1] || '召唤物';
}

function matchingSynergies(hero, synergies) {
  if (!hero) return [];
  return (synergies || []).filter((item) => Number(item.tier) > 0 && (
    (item.category === 'role' && item.name === hero.role)
    || (item.category === 'attribute' && item.name === hero.attribute)
    || (item.category === 'race' && item.name === hero.race)
  )).map((item) => item.effect).filter(Boolean);
}

function withPieceGrowth(hero, piece, synergies) {
  if (!hero) return null;
  const lines = piece?.synergyLines || matchingSynergies(hero, synergies);
  return {...hero, upgrades: piece?.upgrades || {}, active_synergies: lines, star: Number(piece?.star || 0)};
}

function updateCountdown() {
  const clock = $('autochess-countdown');
  const chess = state.room?.mode === 'autochess' ? state.room.autochess : null;
  if (!clock || !chess || (chess.phase !== 'preparation' && chess.phase !== 'battle') || !Number(chess.deadline_at)) return;
  const seconds = Math.max(0, Math.ceil(Number(chess.deadline_at || 0) - Date.now() / 1000 + serverClockOffset));
  const value = clock.querySelector('.chess-clock__seconds');
  if (value) value.textContent = String(seconds);
  const ring = clock.querySelector('.chess-clock__ring');
  const total = Math.max(1, Number(chess.config?.preparation_seconds || 90));
  if (ring) {
    const length = 2 * Math.PI * 32;
    ring.style.strokeDasharray = String(length);
    ring.style.strokeDashoffset = String(length * (1 - Math.min(1, seconds / total)));
  }
  clock.classList.toggle('is-urgent', seconds <= 15);
}

function statPill(kind, amount) {
  const pill = node('span', `chess-pill chess-stat is-${kind}`);
  pill.title = kind === 'gold' ? '金币' : '血量';
  pill.innerHTML = kind === 'gold'
    ? `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 4.4v7.2M6.2 6.2c.4-.6 1-.9 1.8-.9 1.2 0 2 .6 2 1.5S9.2 8.2 8 8.2 6 8.8 6 9.7s.9 1.6 2 1.6c.8 0 1.4-.3 1.8-.9" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round"/></svg><span>${amount}</span>`
    : `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 13.4S2.4 9.7 2.4 6.2A2.8 2.8 0 0 1 8 4.9a2.8 2.8 0 0 1 5.6 1.3c0 3.5-5.6 7.2-5.6 7.2z" fill="currentColor"/></svg><span>${amount}</span>`;
  return pill;
}

function node(tag, className = '', text = '') {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== '') element.textContent = String(text);
  return element;
}

function button(label, action, values = {}, disabled = false) {
  const element = node('button', 'ghost', label);
  element.type = 'button';
  element.dataset.chessAction = action;
  for (const [key, value] of Object.entries(values)) element.dataset[key] = String(value);
  element.disabled = disabled;
  return element;
}

const SHOP_ICON = {
  refresh: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.4-3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M13 2.6V6H9.6" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  gear: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8 13 4.2v4.1c0 3-2.1 4.7-5 5.7-2.9-1-5-2.7-5-5.7V4.2L8 1.8z" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>',
  level: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 12.6V3.4M4.4 6.6 8 3l3.6 3.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};
const VIEW_ICON = {
  card: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2.5" width="5" height="4.5" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="9" y="2.5" width="5" height="4.5" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="2" y="9" width="12" height="4.5" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>',
  tag: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8.2 8 2.8h5.2V8L8.2 13.8 2.5 8.2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><circle cx="10.6" cy="5.4" r="0.8" fill="currentColor"/></svg>',
};

const PIECE_ICON = {
  recall: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.2 3.4 2.8 6.8l3.4 3.4M3.2 6.8h6.6a3 3 0 0 1 0 6H8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  sell: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 5.1v5.8M6.3 6.5c.3-.5.9-.8 1.7-.8 1 0 1.7.5 1.7 1.2S9 8.2 8 8.2 6.3 8.7 6.3 9.5s.8 1.3 1.7 1.3c.7 0 1.3-.3 1.6-.7" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round"/></svg>',
};

function upgradeButton(glyph, tone, title, values, disabled) {
  const element = button(glyph, 'upgrade', values, disabled);
  element.className = `chess-upgrade-btn ${tone}`;
  element.setAttribute('aria-label', title);
  element.addEventListener('mouseenter', () => showActionTip(element, title));
  element.addEventListener('mouseleave', hideActionTip);
  element.addEventListener('focus', () => showActionTip(element, title));
  element.addEventListener('blur', hideActionTip);
  return element;
}

function pieceIconButton(action, icon, tone, values, disabled, label) {
  const element = button('', action, values, disabled);
  element.className = `chess-icon-btn ${tone}`;
  element.innerHTML = icon;
  element.setAttribute('aria-label', label);
  element.addEventListener('mouseenter', () => showActionTip(element, label));
  element.addEventListener('mouseleave', hideActionTip);
  element.addEventListener('focus', () => showActionTip(element, label));
  element.addEventListener('blur', hideActionTip);
  return element;
}

function shopButton(label, action, icon, tone, disabled) {
  const element = button('', action, {}, disabled);
  element.className = `chess-shop-btn ${tone}`;
  element.innerHTML = `${icon}<span>${label}</span>`;
  return element;
}

let synergyTip = null;
let actionTip = null;

function hideActionTip() {
  actionTip?.remove();
  actionTip = null;
}

function showActionTip(anchor, text) {
  if (!anchor?.isConnected) return;
  hideActionTip();
  const card = node('div', 'chess-action-tip', text);
  document.body.append(card);
  actionTip = card;
  const rect = anchor.getBoundingClientRect();
  card.style.left = `${rect.left + rect.width / 2}px`;
  card.style.top = `${rect.top - 6}px`;
}

function renderStatus(chess, me) {
  const box = node('div', 'chess-status');
  const preparing = chess.phase === 'preparation';
  const fighting = chess.phase === 'battle';
  const clock = node('div', `chess-clock${preparing ? ' is-prep' : ''}${fighting ? ' is-battle' : ''}${chess.phase === 'finished' ? ' is-done' : ''}`);
  clock.id = 'autochess-countdown';
  clock.setAttribute('role', 'timer');
  const hasDeadline = (preparing || fighting) && Boolean(Number(chess.deadline_at));
  clock.innerHTML = `<svg viewBox="0 0 72 72" aria-hidden="true"><circle class="chess-clock__track" cx="36" cy="36" r="32"></circle><circle class="chess-clock__ring" cx="36" cy="36" r="32"></circle></svg><div class="chess-clock__label">${hasDeadline ? '<strong class="chess-clock__seconds"></strong>' : (preparing || fighting) ? '<strong class="chess-clock__seconds">∞</strong>' : ''}</div>`;
  const facts = node('div', 'chess-status__facts');
  const rowA = node('div', 'chess-status__row');
  const rowB = node('div', 'chess-status__row');
  const phase = node('span', `chess-phase${preparing ? ' is-prep' : ''}${fighting ? ' is-battle' : ''}${chess.phase === 'finished' ? ' is-done' : ''}`,
    preparing ? '准备阶段' : fighting ? '交战阶段' : chess.phase === 'finished' ? '结束' : '等待');
  rowA.append(phase);
  if (me && chess.phase !== 'lobby') {
    rowA.append(statPill('gold', me.gold));
    rowA.append(statPill('health', me.health));
    const field = node('span', 'chess-status__field');
    field.innerHTML = `当前上场 <b>${me.budget_used}</b><span>/</span><b>${me.budget}</b>`;
    rowB.append(field);
  }
  if (chess.phase === 'preparation' || chess.phase === 'battle') rowB.append(battleSpeedControl());
  facts.append(rowA);
  if (rowB.childElementCount) facts.append(rowB);
  box.append(clock, facts);
  return box;
}

function battleSpeedControl() {
  const label = node('label', 'chess-speed');
  label.append(node('span', '', '速度'));
  const select = document.createElement('select');
  select.className = 'chess-speed__select';
  const current = Number(state.room?.chess_battle_speed ?? 1);
  for (const value of [0.5, 1, 2, 4, 6, 8, 16]) {
    const option = document.createElement('option');
    option.value = String(value);
    option.textContent = `${value}x`;
    if (value === current) option.selected = true;
    select.append(option);
  }
  select.disabled = !state.room?.viewer_is_host;
  select.addEventListener('change', () => {
    const value = select.value;
    select.blur();
    void setRoomChessBattleSpeed(value);
  });
  label.append(select);
  return label;
}

function chessOpponentLabel(chess, seatId) {
  const matches = chess.matches || [];
  const real = matches.find((match) => !match.mirror && (match.left === seatId || match.right === seatId));
  const mirrored = matches.find((match) => match.mirror && (match.left === seatId || match.right === seatId));
  const match = real || mirrored;
  if (!match) return '';
  const otherId = match.left === seatId ? match.right : match.left;
  const other = (chess.players || []).find((player) => player.seat_id === otherId);
  const name = other?.name || `席位 ${otherId}`;
  return match.mirror && !real ? `${name} · 镜像` : name;
}

function renderStandings(chess) {
  const section = node('section', 'autochess-standings');
  section.append(node('h3', '', '积分与血量'));
  const list = node('div', 'autochess-standing-list');
  chess.players.forEach((player) => {
    const label = `${player.name || `席位 ${player.seat_id}`}${player.is_ai ? ' · AI' : ''} · ${player.health} 血 · ${player.level} 级 · ${player.wins}胜/${player.losses}负${player.eliminated ? ' · 已淘汰' : player.ready ? ' · 已确认' : ''}`;
    list.append(node('div', player.seat_id === chess.viewer_id ? 'autochess-self' : '', label));
  });
  section.append(list);
  return section;
}

function renderShop(chess, me, locked) {
  const section = node('section', 'autochess-shop');
  const list = node('div', 'chess-shop-list');
  (me.shop || []).forEach((entry) => {
    if (!entry.code) {
      list.append(node('div', 'chess-block is-empty', '已售出'));
      return;
    }
    const hero = chess.hero_previews?.[entry.code];
    const matching = (me.pieces || []).filter((piece) => piece.code === entry.code);
    const selected = matching.find((piece) => piece.id === selectedPieceId);
    const target = selected || matching.sort((left, right) => Number(left.x === null) - Number(right.x === null) || right.star - left.star)[0];
    const benchFull = !target && (me.pieces || []).filter((piece) => piece.x === null).length >= (chess.config.bench_capacity || 8);
    const item = button('', 'buy', {slot: entry.slot, ...(target ? {pieceId: target.id} : {})}, locked || me.gold < entry.cost || benchFull);
    item.className = 'chess-block';
    item.append(node('strong', '', entry.name));
    item.append(node('small', '', `${entry.level}级 · ${entry.cost}金 · ${hero?.role || '—'} / ${hero?.attribute || '—'} / ${hero?.race || '—'}`));
    if (hero) bindHeroHover(item, withPieceGrowth(hero, target, me.synergies));
    list.append(item);
  });
  section.append(list);
  const actions = node('div', 'chess-shop-actions');
  const reroll = shopButton(`刷新 ${chess.config.reroll_cost}金`, 'reroll', SHOP_ICON.refresh, 'is-reroll', locked || me.gold < chess.config.reroll_cost);
  const draw = shopButton(me.equipment_drawn_this_round ? '已抽装备' :
    (chess.equipment_available ? `抽装备 ${chess.config.equipment_draw_cost}金` : '无装备'),
    'draw_equipment', SHOP_ICON.gear, 'is-gear', locked || !chess.equipment_available || me.equipment_drawn_this_round ||
      me.gold < chess.config.equipment_draw_cost);
  actions.append(reroll, draw);
  const nextLevelCost = chess.config.level_costs[me.level - 1];
  const level = shopButton(nextLevelCost ? `升级 ${nextLevelCost}金` : '等级已满', 'level_up', SHOP_ICON.level, 'is-level',
    locked || !nextLevelCost || me.gold < nextLevelCost);
  section.append(actions, node('div', 'chess-shop-split'), level);
  return section;
}

function renderPieces(chess, me, locked) {
  const section = node('section', 'autochess-pieces');
  const head = node('div', 'chess-piece-head');
  head.append(node('h3', '', '武将与后备席'));
  const toggle = button('', 'piece-view', {mode: pieceView === 'tag' ? 'card' : 'tag'});
  toggle.className = 'chess-piece-mode';
  toggle.innerHTML = pieceView === 'tag' ? VIEW_ICON.card : VIEW_ICON.tag;
  toggle.setAttribute('aria-label', pieceView === 'tag' ? '切换为卡片' : '切换为标签');
  head.append(toggle);
  section.append(head);
  const equipment = new Map((chess.equipment_catalog || []).map((item) => [item.code, item]));
  if (!me.pieces?.length) section.append(node('p', '', '从商店买武将，再选中武将点击己侧棋盘放置。'));
  if (pieceView === 'tag') {
    const tags = node('div', 'chess-piece-tags');
    for (const piece of me.pieces || []) {
      const tag = button('', 'select', {pieceId: piece.id});
      tag.className = `chess-piece-tag${piece.x !== null ? ' is-fielded' : ''}${piece.id === selectedPieceId ? ' is-on' : ''}`;
      tag.append(node('span', '', piece.name), node('em', 'chess-cost-tag', String(piece.level ?? '')));
      const hero = chess.hero_previews?.[piece.code];
      if (hero) bindHeroHover(tag, withPieceGrowth(hero, piece, me.synergies));
      tags.append(tag);
    }
    section.append(tags);
  }
  for (const piece of me.pieces || []) {
    if (pieceView === 'tag' && !piece.pending_upgrades) continue;
    const pieceLocked = locked || (chess.phase === 'battle' && piece.x !== null);
    const card = node('div', `autochess-piece${piece.x !== null ? ' is-fielded' : ''} ${piece.id === selectedPieceId ? 'is-selected' : ''}`);
    const hero = chess.hero_previews?.[piece.code];
    if (pieceView !== 'tag') {
      const name = button('', 'select', {pieceId: piece.id});
      name.className = 'autochess-hero-name';
      name.append(node('strong', '', `${piece.name} ★${piece.star}`), node('em', 'chess-cost-tag', String(piece.level ?? '')));
      if (hero) bindHeroHover(name, withPieceGrowth(hero, piece, me.synergies));
      card.append(name);
      if (piece.entry_companion) card.append(node('small', 'autochess-companion', `开场带 ${companionName(piece.entry_companion)}：${piece.entry_companion}`));
    }
    if (piece.pending_upgrades) {
      const choice = node('div', 'chess-upgrade');
      choice.append(node('span', 'chess-upgrade__hint', `${piece.name} 强化（待选 ${piece.pending_upgrades} 次）`));
      const picks = node('div', 'chess-upgrade__picks');
      const lockedUpgrade = chess.phase === 'battle' || pieceLocked;
      for (const [stat, glyph, tone, title] of [
        ['attack', '攻', 'is-attack', '攻击 +1'],
        ['defense', '防', 'is-defense', '防御 +1'],
        ['speed', '速', 'is-speed', '速度 +1'],
        ['attack_range', '范', 'is-range', '范围 +1'],
        ['mana', '魔', 'is-mana', '魔法 +1'],
      ]) {
        picks.append(upgradeButton(glyph, tone, title, {pieceId: piece.id, stat}, lockedUpgrade));
      }
      choice.append(picks);
      card.append(choice);
    }
    if (pieceView !== 'tag') {
      const row = node('div', 'chess-piece-row');
      const gear = node('div', 'chess-piece-gear');
      for (const code of piece.equipment || []) {
        const item = equipment.get(code);
        const chip = node('span', 'chess-gear-chip', item?.name || code);
        chip.title = [item?.attributes, item?.effect].filter(Boolean).join(' · ');
        const remove = button('卸下', 'unequip', {pieceId: piece.id, code}, pieceLocked);
        gear.append(chip, remove);
      }
      const controls = node('div', 'chess-piece-actions');
      if (piece.x !== null) controls.append(pieceIconButton('place', PIECE_ICON.recall, 'is-recall', {pieceId: piece.id}, pieceLocked, '撤回'));
      controls.append(pieceIconButton('sell', PIECE_ICON.sell, 'is-sell', {pieceId: piece.id}, pieceLocked, '出售'));
      row.append(gear, controls);
      card.append(row);
    }
    if (card.childElementCount) section.append(card);
  }
  if (me.equipment_inventory?.length) {
    const inventory = node('div', 'autochess-actions');
    inventory.append(node('span', '', '装备背包：'));
    const selected = (me.pieces || []).find((piece) => piece.id === selectedPieceId);
    for (const code of me.equipment_inventory) {
      const item = equipment.get(code);
      const gear = button(`${item?.name || code} · ${item?.attributes || ''} · ${item?.effect || ''} → 所选武将`,
        'equip', {code}, locked || !selected || (selected.equipment?.length || 0) >= chess.config.equipment_slots);
      inventory.append(gear);
    }
    section.append(inventory);
  }
  return section;
}

function renderSelectedHero(chess) {
  const section = node('section', 'autochess-hero-details');
  section.append(node('h3', '', '武将档案与羁绊'));
  const hero = selectedHero(chess);
  if (hero) {
    const details = node('div', 'autochess-hero-details-body');
    renderHeroDetails(details, hero);
    section.append(details);
  } else {
    section.append(node('p', 'meta-note', '点商店武将名字查看资料；鼠标悬停或键盘聚焦名字可快速查看。'));
  }
  return section;
}

function synergyFace(item) {
  const count = Number(item.count || 0);
  if (count <= 3) return {mark: 'I', shown: count, need: 3, active: count >= 3};
  return {mark: 'II', shown: Math.min(3, count - 3), need: 3, active: true};
}

function hideSynergyTip() {
  synergyTip?.remove();
  synergyTip = null;
}

function showSynergyTip(anchor, item) {
  if (!anchor?.isConnected) return;
  hideSynergyTip();
  hideHeroHover();
  const face = synergyFace(item);
  const card = node('aside', 'chess-synergy-tip');
  card.append(node('strong', '', `${item.name}${face.mark}`));
  card.append(node('p', '', `I · 3名：${item.first || ''}`));
  card.append(node('p', '', `II · 6名：${item.second || ''}`));
  card.append(node('p', face.active ? 'is-on' : '', face.active ? '已激活' : '尚未激活'));
  document.body.append(card);
  synergyTip = card;
  const anchorRect = anchor.getBoundingClientRect();
  const cardRect = card.getBoundingClientRect();
  const gap = 10;
  const margin = 8;
  const left = anchorRect.left - cardRect.width - gap;
  const x = left >= margin ? left : Math.min(anchorRect.right + gap, window.innerWidth - cardRect.width - margin);
  const y = Math.max(margin, Math.min(anchorRect.top, window.innerHeight - cardRect.height - margin));
  card.style.left = `${x}px`;
  card.style.top = `${y}px`;
}

function renderSynergies(me) {
  const section = node('section', 'autochess-synergies chess-synergy-block');
  section.append(node('h3', '', '场上羁绊'));
  const list = (me.synergies || []).filter((item) => item.count > 0);
  if (!list.length) {
    section.append(node('p', 'meta-note', '布阵后显示羁绊。'));
    return section;
  }
  const row = node('div', 'chess-synergy-list');
  for (const item of list) {
    const face = synergyFace(item);
    const chip = node('button', `chess-synergy${face.active ? ' is-on' : ''}`);
    chip.type = 'button';
    chip.append(node('span', '', `${item.name}${face.mark}`));
    const count = node('b', '', `${face.shown}/${face.need}`);
    chip.append(count);
    chip.addEventListener('mouseenter', () => showSynergyTip(chip, item));
    chip.addEventListener('mouseleave', hideSynergyTip);
    chip.addEventListener('focus', () => showSynergyTip(chip, item));
    chip.addEventListener('blur', hideSynergyTip);
    row.append(chip);
  }
  section.append(row);
  return section;
}

function battleStage() {
  const stage = node('div', 'autochess-board-stage board-stage');
  const world = node('div', 'autochess-board-world board-world');
  const board = node('div', 'autochess-battle-board board');
  const pieces = node('div', 'autochess-board-pieces board-pieces');
  world.append(board, pieces);
  stage.append(world);
  return {stage, board, pieces};
}

function renderBodies(layer, bodies) {
  for (const body of bodies) {
    const cells = body.cells || [];
    if (!cells.length) continue;
    const xs = cells.map((cell) => cell.x);
    const ys = cells.map((cell) => cell.y);
    const minX = Math.min(...xs);
    const minY = Math.min(...ys);
    const tone = Number(body.side || 1) === 1 ? 'is-friendly' : 'is-hostile';
    const piece = node('div', `piece board-piece is-hero ${cells.length > 1 ? 'is-footprint' : ''} player-${body.side || 1} ${tone} autochess-board-piece`);
    piece.style.gridColumn = `${minX + 1} / span ${Math.max(...xs) - minX + 1}`;
    piece.style.gridRow = `${minY + 1} / span ${Math.max(...ys) - minY + 1}`;
    piece.style.setProperty('--hp-angle', `${Math.max(0, Math.min(1, body.hpRatio ?? 1)) * 360}deg`);
    const shape = node('div', 'piece-footprint-cells autochess-body-cells');
    shape.style.gridTemplateColumns = `repeat(${Math.max(...xs) - minX + 1}, minmax(0, 1fr))`;
    shape.style.gridTemplateRows = `repeat(${Math.max(...ys) - minY + 1}, minmax(0, 1fr))`;
    for (const cell of cells) {
      const marker = node('span', 'piece-footprint-cell autochess-body-cell');
      marker.style.gridColumn = String(cell.x - minX + 1);
      marker.style.gridRow = String(cell.y - minY + 1);
      shape.append(marker);
    }
    if (cells.length > 1) piece.append(shape);
    if (body.pad) piece.classList.add('is-mount-pad');
    if (body.rider) {
      piece.classList.add('is-rider');
      piece.style.zIndex = '6';
    }
    if (!body.hideRing) {
      const ring = node('div', 'piece-ring');
      ring.append(node('div', 'piece-name', body.name));
      piece.append(ring);
    }
    if (Number(body.star) > 0) piece.append(node('small', 'autochess-piece-star', `★${body.star}`));
    if (body.extra) piece.append(node('small', 'autochess-piece-note', body.extra));
    if (body.hero) {
      piece.classList.add('is-hoverable');
      bindHeroHover(piece, body.hero);
    }
    if (body.pieceId) {
      piece.dataset.chessAction = 'select';
      piece.dataset.pieceId = body.pieceId;
      piece.classList.add('is-selectable');
      if (body.pieceId === selectedPieceId) piece.classList.add('is-picked');
    }
    piece.title = '';
    layer.append(piece);
  }
}

function applyChessGrid(board, width, height) {
  const cell = 46;
  const gap = 2;
  const columns = `repeat(${width}, ${cell}px)`;
  const rows = `repeat(${height}, ${cell}px)`;
  const boxWidth = width * cell + Math.max(0, width - 1) * gap;
  const boxHeight = height * cell + Math.max(0, height - 1) * gap;
  const world = board.parentElement;
  const pieces = world?.querySelector('.autochess-board-pieces');
  for (const item of [board, pieces]) {
    if (!item) continue;
    item.style.gridTemplateColumns = columns;
    item.style.gridTemplateRows = rows;
    item.style.gap = `${gap}px`;
    item.style.width = `${boxWidth}px`;
    item.style.height = `${boxHeight}px`;
    item.style.aspectRatio = 'auto';
    item.style.maxWidth = 'none';
    item.style.maxHeight = 'none';
  }
  if (world) {
    world.style.width = `${boxWidth}px`;
    world.style.height = `${boxHeight}px`;
    world.style.aspectRatio = 'auto';
    world.style.maxWidth = 'none';
    world.style.maxHeight = 'none';
  }
}

function boardSize(chess) {
  return {
    width: Number(chess?.config?.board_width || 10),
    height: Number(chess?.config?.board_height || 10),
    deploy: Number(chess?.config?.deployment_width || 4),
  };
}

function renderDeployment(chess, me, locked) {
  const section = node('section', 'autochess-deployment');
  const occupied = new Map();
  for (const piece of me.fielded || []) {
    for (const [dx, dy] of piece.footprint || [[0, 0]]) {
      occupied.set(`${piece.x + dx},${piece.y + dy}`, piece);
    }
  }
  const size = boardSize(chess);
  const enemyStart = size.width - size.deploy;
  const visualX = (logicalX) => size.width - 1 - logicalX;
  const chosen = (me.pieces || []).find((piece) => piece.id === selectedPieceId);
  const placementCost = chosen ? me.budget_used - (chosen.x === null ? 0 : chosen.level) + chosen.level : 0;
  const canPlace = (x, y) => Boolean(chosen && placementCost <= me.budget &&
    (chosen.footprint || [[0, 0]]).every(([dx, dy]) => {
      const px = x + dx;
      const py = y + dy;
      const blocker = occupied.get(`${px},${py}`);
      return px >= 0 && px < size.deploy && py >= 0 && py < size.height && (!blocker || blocker.id === chosen.id);
    }));
  const {stage, board, pieces} = battleStage();
  board.style.gridTemplateColumns = '';
  board.style.gridTemplateRows = '';
  applyChessGrid(board, size.width, size.height);
  const cellByKey = new Map();
  board.setAttribute('role', 'grid');
  board.setAttribute('aria-label', '自走棋布阵');
  for (let y = 0; y < size.height; y += 1) {
    for (let x = 0; x < size.width; x += 1) {
      const occupant = occupied.get(`${x},${y}`);
      const allowed = x < size.deploy && canPlace(x, y);
      const cell = x < size.deploy
        ? button('', 'place', {pieceId: selectedPieceId, x, y}, locked || !allowed)
        : node('div');
      cell.classList.add('cell', x < size.deploy ? 'autochess-friendly-cell' : x >= enemyStart ? 'autochess-enemy-cell' : 'autochess-middle-cell');
      if (occupant) cell.classList.add('has-unit');
      if (allowed && !locked) cell.classList.add('is-legal');
      cell.style.gridColumn = String(visualX(x) + 1);
      cell.style.gridRow = String(y + 1);
      cell.setAttribute('aria-label', `${x + 1}列${y + 1}行；${x < size.deploy ? '己方布阵区' : x >= enemyStart ? '敌方入场区' : '交战区'}；${occupant?.name || '空格'}${allowed && !locked ? '；可放置' : ''}`);
      cellByKey.set(`${x},${y}`, cell);
      if (allowed && !locked) {
        const preview = () => (chosen.footprint || [[0, 0]]).forEach(([dx, dy]) =>
          cellByKey.get(`${x + dx},${y + dy}`)?.classList.add('is-preview'));
        const clear = () => board.querySelectorAll('.cell.is-preview').forEach((item) => item.classList.remove('is-preview'));
        cell.addEventListener('mouseenter', preview);
        cell.addEventListener('focus', preview);
        cell.addEventListener('mouseleave', clear);
        cell.addEventListener('blur', clear);
      }
      board.append(cell);
    }
  }
  const ownBodies = (me.fielded || []).map((piece) => ({
    side: 1, pieceId: piece.id, name: piece.name, star: piece.star,
    extra: piece.entry_companion ? `+ ${companionName(piece.entry_companion)}（坐骑）` : '',
    hero: withPieceGrowth(chess.hero_previews?.[piece.code], piece, me.synergies),
    cells: (piece.footprint || [[0, 0]]).map(([dx, dy]) => ({x: visualX(piece.x + dx), y: piece.y + dy})),
  }));
  renderBodies(pieces, ownBodies);
  section.append(stage);
  return section;
}

function renderBattleBoard(battle, previews, viewerSide = 1) {
  const {stage, board, pieces} = battleStage();
  const width = Number(battle.board?.width || 10);
  const height = Number(battle.board?.height || 10);
  const focusSide = Number(viewerSide) === 2 ? 2 : 1;
  const visualX = (x) => focusSide === 1 ? width - 1 - x : x;
  board.style.gridTemplateColumns = '';
  board.style.gridTemplateRows = '';
  applyChessGrid(board, width, height);
  board.setAttribute('role', 'grid');
  board.setAttribute('aria-label', '自动战斗观战棋盘');
  const occupied = new Map();
  const present = (battle.units || []).filter((unit) => unit.position && unit.alive && !unit.banished);
  const riddenMounts = new Set(present.map((unit) => unit.mounted_on_unit_id).filter(Boolean));
  const bodies = present.map((unit) => {
    for (const cell of unit.occupied_cells || []) occupied.set(`${cell.x},${cell.y}`, unit);
    const ridden = Boolean(unit.is_mount && riddenMounts.has(unit.id));
    return {
      side: unit.player_id,
      name: unit.name,
      extra: unit.is_mount ? '召唤坐骑' : '',
      heroCode: unit.hero_code,
      star: Number(unit.chess_star || 0),
      upgrades: unit.chess_upgrades || {},
      synergyLines: unit.synergy_lines || [],
      hideRing: ridden,
      pad: ridden,
      rider: Boolean(unit.mounted_on_unit_id),
      cells: new Map((unit.occupied_cells || []).map((cell) => [`${cell.x},${cell.y}`, cell])),
      hpRatio: unit.hp / Math.max(unit.max_hp, 0.001),
    };
  });
  bodies.sort((left, right) => Number(Boolean(right.pad)) - Number(Boolean(left.pad)));
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const unit = occupied.get(`${x},${y}`);
      const shown = visualX(x);
      const cell = node('div', `cell ${shown >= Math.floor(width / 2) ? 'autochess-friendly-cell' : 'autochess-enemy-cell'}`);
      if (unit) cell.classList.add('has-unit');
      cell.dataset.x = String(shown);
      cell.dataset.y = String(y);
      cell.style.gridColumn = String(shown + 1);
      cell.style.gridRow = String(y + 1);
      const marks = (battle.field_effects || []).filter((effect) => (effect.cells || []).some((item) => item.x === x && item.y === y));
      if (marks.length) {
        cell.classList.add('has-field-effect');
        const stack = node('div', 'cell-effects');
        marks.forEach((effect) => stack.append(node('span', 'cell-effect-tag', effect.board_marker || String(effect.name || '').slice(0, 2))));
        cell.append(stack);
      }
      cell.title = '';
      board.append(cell);
    }
  }
  renderBodies(pieces, bodies.map((body) => ({
    ...body,
    side: body.side === focusSide ? 1 : 2,
    hero: withPieceGrowth(previews?.[body.heroCode], {upgrades: body.upgrades, synergyLines: body.synergyLines, star: body.star}),
    cells: [...body.cells.values()].map((cell) => ({...cell, x: visualX(cell.x)})),
  })));
  return stage;
}

function renderMatchPreview(chess, match) {
  const units = [];
  const width = Number(chess.config?.board_width || 10);
  const height = Number(chess.config?.board_height || 10);
  const shift = width - Number(chess.config?.deployment_width || 4);
  for (const [side, seatId] of [[1, match.left], [2, match.right]]) {
    const player = chess.players.find((item) => item.seat_id === seatId);
    for (const piece of player?.fielded || []) {
      const x = piece.x + (side === 2 ? shift : 0);
      units.push({name: piece.name, hero_code: piece.code, player_id: side, position: {x, y: piece.y},
        alive: true, banished: false, hp: 1, max_hp: 1,
        occupied_cells: (piece.footprint || [[0, 0]]).map(([dx, dy]) => ({x: x + dx, y: piece.y + dy}))});
    }
  }
  return renderBattleBoard({board: {width, height}, units}, chess.hero_previews);
}

function renderMatches(chess, {includeBoard = true} = {}) {
  const section = node('section', 'autochess-matches');
  section.append(node('h3', '', '本轮配对与观战'));
  if (!chess.matches?.length) return section;
  const buttons = node('div', 'autochess-actions');
  chess.matches.forEach((match) => {
    const left = chess.players.find((player) => player.seat_id === match.left);
    const right = chess.players.find((player) => player.seat_id === match.right);
    buttons.append(button(`${left?.name || match.left} 对 ${right?.name || match.right}${match.mirror ? '（镜像）' : ''}`,
      'watch', {index: match.index}));
  });
  section.append(buttons);
  const match = chess.matches[selectedMatchIndex] || chess.matches[0];
  if (match.result) section.append(node('p', '', `胜方：${match.result.winner === 1 ? '左方' : '右方'} · 扣血 ${match.result.damage} · 判定 ${match.result.criterion}`));
  if (match.battle && includeBoard) {
    section.append(node('p', '', `战斗轮 ${Math.min(match.battle.round_number, chess.config.battle_round_limit)}/${chess.config.battle_round_limit} · ${match.battle.winner ? '已结束' : '自动交战中'}`));
    section.append(renderBattleBoard(match.battle, chess.hero_previews));
    const log = node('div', 'autochess-battle-log');
    for (const line of (match.battle.logs || []).slice(-10)) log.append(node('p', '', line));
    section.append(log);
  } else if (includeBoard) {
    section.append(node('p', 'meta-note', chess.phase === 'battle' ? '正在建立战场，阵容已锁定…' : '备战阵型预览'));
    section.append(renderMatchPreview(chess, match));
  }
  return section;
}

function renderChessRecord(chess, me) {
  const wrap = node('div', 'chess-record');
  const battles = chess.matches || [];
  if (!battles.length) {
    wrap.append(node('p', 'meta-note', '这一轮还没有战斗。'));
    return wrap;
  }
  const keyOf = (match) => `${match.left}:${match.right}:${match.index ?? 0}`;
  const mine = me ? battles.find((match) => match.left === me.seat_id || match.right === me.seat_id) : null;
  const selected = battles.find((match) => keyOf(match) === logMatchKey) || mine || battles[0];
  logMatchKey = keyOf(selected);
  const select = document.createElement('select');
  select.className = 'chess-log__select';
  for (const match of battles) {
    const option = document.createElement('option');
    option.value = keyOf(match);
    const left = chess.players.find((player) => player.seat_id === match.left);
    const right = chess.players.find((player) => player.seat_id === match.right);
    option.textContent = `${left?.name || `席位 ${match.left}`} 对 ${right?.name || `席位 ${match.right}`}${match.mirror ? ' · 镜像' : ''}`;
    if (option.value === logMatchKey) option.selected = true;
    select.append(option);
  }
  select.addEventListener('change', () => {
    logMatchKey = select.value;
    select.blur();
    renderAutoChessPanel();
  });
  wrap.append(select);
  const lines = node('div', 'logs');
  const entries = [...(selected.battle?.logs || [])].reverse();
  if (!entries.length) {
    lines.append(node('div', 'log', chess.phase === 'battle' ? '这场战斗还没有写出记录。' : '交战开始后，这里显示这一轮的战斗记录。'));
  } else {
    entries.forEach((line) => lines.append(node('div', 'log', line)));
  }
  wrap.append(lines);
  return wrap;
}

export function renderAutoChessPanel() {
  const panel = $('autochess-panel');
  if (!panel) return;
  const chess = state.room?.mode === 'autochess' ? state.room.autochess : null;
  const focused = document.activeElement;
  if (chess && focused && panel.contains(focused) && (focused.classList.contains('chess-speed__select') || focused.classList.contains('chess-log__select'))) return;
  panel.classList.toggle('hidden', !chess);
  if (!chess) {
    hideHeroHover();
    return;
  }
  hideHeroHover();
  hideSynergyTip();
  hideActionTip();
  panel.replaceChildren();
  if (Number(chess.server_time) && Number(chess.server_time) !== lastServerTimestamp) {
    lastServerTimestamp = Number(chess.server_time);
    serverClockOffset = Date.now() / 1000 - lastServerTimestamp;
  }
  const living = chess.players.filter((player) => !player.eliminated);
  const readyCount = living.filter((player) => player.ready).length;
  const me = chess.players.find((player) => player.seat_id === chess.viewer_id);
  if (!watchSeatId) watchSeatId = me?.seat_id || chess.players[0]?.seat_id || 0;
  const focus = chess.players.find((player) => player.seat_id === watchSeatId) || me;
  const shell = node('div', 'chess-shell');
  shell.append(renderStatus(chess, me));
  const stageHost = node('div', 'chess-stage');
  if (me && chess.phase === 'preparation' && focus && !focus.eliminated) {
    if (selectedPieceId && !(me.pieces || []).some((piece) => piece.id === selectedPieceId)) selectedPieceId = '';
    const locked = Boolean(me.ready) || focus.seat_id !== me.seat_id;
    stageHost.append(renderDeployment(chess, focus, locked));
  } else if (chess.phase !== 'lobby') {
    const match = (chess.matches || []).find((item) => item.left === focus?.seat_id || item.right === focus?.seat_id) || chess.matches?.[0];
    if (match?.battle) stageHost.append(renderBattleBoard(match.battle, chess.hero_previews, match.right === focus?.seat_id ? 2 : 1));
    else if (match) stageHost.append(renderMatchPreview(chess, match));
  }
  shell.append(stageHost);
  const left = node('aside', `chess-dock chess-dock--left${leftDockOpen ? '' : ' is-collapsed'}`);
  left.append(button('商店', 'toggle-dock', {side: 'left'}));
  const leftBody = node('div', 'chess-dock__body');
  if (me && !me.eliminated) leftBody.append(renderShop(chess, me, chess.phase === 'preparation' && me.ready));
  else leftBody.append(node('p', 'meta-note', '淘汰后不能再购买。'));
  left.append(leftBody);
  shell.append(left);
  const right = node('aside', `chess-dock chess-dock--right${rightDockOpen ? '' : ' is-collapsed'}`);
  const tabs = node('div', 'chess-side-tabs');
  const packTab = button('背包', 'dock', {tab: 'pack'});
  const foeTab = button('对手', 'dock', {tab: 'players'});
  const logTab = button('记录', 'dock', {tab: 'log'});
  for (const [tab, name] of [[packTab, 'pack'], [foeTab, 'players'], [logTab, 'log']]) {
    tab.className = 'chess-side-tab';
    if (rightDockOpen && chessDock === name) tab.classList.add('is-on');
  }
  tabs.append(packTab, foeTab, logTab);
  const rightBody = node('div', 'chess-dock__body chess-dock__body--split');
  const scroll = node('div', 'chess-dock__scroll');
  if (chessDock === 'players') {
    const page = node('div', 'chess-players');
    if (chess.phase === 'preparation') page.append(node('p', 'chess-ready-note', `已确认 ${readyCount}/${living.length}`));
    chess.players.forEach((player, index) => {
      const item = button('', 'watch-seat', {seatId: player.seat_id});
      item.className = `chess-player${player.seat_id === focus?.seat_id ? ' is-on' : ''}${player.eliminated ? ' is-out' : ''}`;
      const mark = node('i', `chess-player__mark is-${index % 2 === 0 ? 'red' : 'blue'}`);
      item.append(mark, node('span', 'chess-player__name', player.name || `席位 ${player.seat_id}`));
      const tail = node('span', 'chess-player__tail');
      if (chess.phase === 'battle') {
        const match = (chess.matches || []).find((entry) => entry.left === player.seat_id || entry.right === player.seat_id);
        if (match?.result) item.classList.add('is-done');
        const foe = chessOpponentLabel(chess, player.seat_id);
        if (foe) tail.append(node('em', 'chess-player__foe', foe));
      }
      tail.append(statPill('health', player.health));
      if (player.gold != null) tail.append(statPill('gold', player.gold));
      item.append(tail);
      if (chess.phase === 'preparation' && player.ready) item.append(node('em', 'chess-player__ready', '已确认'));
      page.append(item);
    });
    scroll.append(page);
  } else if (chessDock === 'log') {
    scroll.append(renderChessRecord(chess, me));
  } else if (me && !me.eliminated) {
    scroll.append(renderPieces(chess, me, chess.phase === 'preparation' && me.ready));
    scroll.append(renderSynergies(me));
  } else if (chess.phase === 'finished') {
    const winner = chess.players.find((player) => player.seat_id === chess.champion);
    scroll.append(node('p', '', `冠军：${winner?.name || chess.champion}`));
    scroll.append(renderStandings(chess));
  } else {
    scroll.append(renderStandings(chess));
  }
  rightBody.append(scroll);
  if (me && !me.eliminated && chess.phase === 'preparation') {
    const pending = me.pieces?.some((piece) => piece.pending_upgrades);
    const confirm = button(me.ready ? '已确认' : '确认备战', 'ready', {}, me.ready || pending);
    confirm.classList.add('autochess-confirm');
    const foot = node('div', 'chess-dock__foot');
    foot.append(confirm);
    rightBody.append(foot);
  }
  right.append(tabs, rightBody);
  shell.append(right);
  panel.append(shell);
  updateCountdown();
  paintChessCamera();
  const watched = (chess.matches || []).find((item) => item.left === focus?.seat_id || item.right === focus?.seat_id) || chess.matches?.[0];
  const stage = panel.querySelector('.autochess-board-stage');
  if (chess.phase === 'battle' && stage && watched?.battle) {
    playChessEffects(stage, watched.battle, `${state.room?.room_id || ''}:${watched.index}`, watched.right === focus?.seat_id ? 2 : 1);
  } else {
    chessVfxKey = '';
    document.getElementById('chess-battle-vfx')?.replaceChildren();
  }
}

let chessDock = 'pack';
let logMatchKey = '';
let leftDockOpen = true;
let rightDockOpen = true;
let pieceView = 'card';

let chessVfxKey = '';
let chessVfxSeen = 0;

function playChessEffects(stage, battle, key, viewerSide) {
  const screen = document.getElementById('autochess-screen');
  if (!screen) return;
  let layer = document.getElementById('chess-battle-vfx');
  if (!layer) {
    layer = document.createElement('div');
    layer.id = 'chess-battle-vfx';
    layer.className = 'battle-vfx';
    screen.append(layer);
  }
  const events = battle.visual_events || [];
  const newest = events.reduce((max, event) => Math.max(max, Number(event.id || 0)), 0);
  if (key !== chessVfxKey || newest < chessVfxSeen) {
    chessVfxKey = key;
    chessVfxSeen = newest;
    return;
  }
  const fresh = events.filter((event) => Number(event.id || 0) > chessVfxSeen);
  chessVfxSeen = Math.max(chessVfxSeen, newest);
  if (!fresh.length) return;
  const width = Number(battle.board?.width || 10);
  const flip = Number(viewerSide) === 1;
  const visualX = (x) => flip ? width - 1 - x : x;
  const host = screen.getBoundingClientRect();
  fresh.slice(-8).forEach((event) => {
    const cells = (event.target_cells && event.target_cells.length) ? event.target_cells : (event.source_cell ? [event.source_cell] : []);
    cells.forEach((cell) => {
      const node = stage.querySelector(`.cell[data-x="${visualX(cell.x)}"][data-y="${cell.y}"]`);
      if (!node || typeof node.getBoundingClientRect !== 'function') return;
      const rect = node.getBoundingClientRect();
      const burst = document.createElement('div');
      burst.className = `battle-vfx-burst theme-${event.kind === 'defense' ? 'barrier' : 'attack'}`;
      burst.style.left = `${rect.left - host.left + rect.width / 2}px`;
      burst.style.top = `${rect.top - host.top + rect.height / 2}px`;
      layer.append(burst);
      window.setTimeout(() => burst.remove(), 900);
    });
  });
}

function paintChessCamera() {
  const world = document.querySelector('.autochess-board-world');
  if (!world) return;
  const zoom = Math.max(0.45, Math.min(1.85, Number(state.boardZoom || 1)));
  world.style.transform = `translate3d(${Number(state.boardPanX || 0)}px, ${Number(state.boardPanY || 0)}px, 0) scale(${zoom})`;
}

export function bindAutoChessEvents() {
  let drag = null;
  let dragUntil = 0;
  window.setInterval(updateCountdown, 250);
  const panel = $('autochess-panel');
  panel?.addEventListener('pointerdown', (event) => {
    const stage = event.target.closest?.('.autochess-board-stage');
    if (!stage || event.button !== 0) return;
    if (event.target.closest?.('input, select, textarea')) return;
    drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      panX: Number(state.boardPanX || 0),
      panY: Number(state.boardPanY || 0),
      moved: false,
    };
  });
  panel?.addEventListener('pointermove', (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < 6) return;
    drag.moved = true;
    state.boardPanX = drag.panX + dx;
    state.boardPanY = drag.panY + dy;
    paintChessCamera();
  });
  const endDrag = (event) => {
    if (!drag || (event && drag.pointerId !== event.pointerId)) return;
    if (drag.moved) dragUntil = Date.now() + 180;
    drag = null;
  };
  panel?.addEventListener('pointerup', endDrag);
  panel?.addEventListener('pointercancel', endDrag);
  document.getElementById('autochess-screen')?.addEventListener('wheel', (event) => {
    if (!event.target?.closest?.('.autochess-board-stage')) return;
    event.preventDefault();
    const delta = Number(event.deltaY || 0);
    if (Math.abs(delta) < 0.5) return;
    state.boardZoom = Math.max(0.45, Math.min(1.85, Number(state.boardZoom || 1) + (delta < 0 ? 0.12 : -0.12)));
    paintChessCamera();
  }, { passive: false });
  panel?.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-chess-action]');
    if (!target || Date.now() < dragUntil) return;
    const action = target.dataset.chessAction;
    if (action === 'select') {
      selectedPieceId = target.dataset.pieceId;
      selectedHeroCode = '';
      actionMessage = '';
      actionError = '';
      renderAutoChessPanel();
      return;
    }
    if (action === 'inspect') {
      selectedPieceId = '';
      selectedHeroCode = target.dataset.code;
      actionMessage = '';
      actionError = '';
      renderAutoChessPanel();
      return;
    }
    if (action === 'watch') {
      selectedMatchIndex = Number(target.dataset.index) || 0;
      renderAutoChessPanel();
      return;
    }
    if (action === 'watch-seat') {
      watchSeatId = Number(target.dataset.seatId) || 0;
      chessDock = 'players';
      renderAutoChessPanel();
      return;
    }
    if (action === 'dock') {
      const tab = target.dataset.tab === 'players' || target.dataset.tab === 'log' ? target.dataset.tab : 'pack';
      if (rightDockOpen && chessDock === tab) rightDockOpen = false;
      else {
        chessDock = tab;
        rightDockOpen = true;
      }
      renderAutoChessPanel();
      return;
    }
    if (action === 'piece-view') {
      pieceView = target.dataset.mode === 'tag' ? 'tag' : 'card';
      renderAutoChessPanel();
      return;
    }
    if (action === 'toggle-dock') {
      leftDockOpen = !leftDockOpen;
      renderAutoChessPanel();
      return;
    }
    if (action === 'lobby') {
      setScreen('draft');
      return;
    }
    const payload = {room_id: state.room?.room_id, player_token: state.playerToken, action};
    if (target.dataset.slot !== undefined) payload.slot = Number(target.dataset.slot);
    if (target.dataset.pieceId) payload.piece_id = target.dataset.pieceId;
    else if (['place', 'equip'].includes(action)) payload.piece_id = selectedPieceId;
    if (target.dataset.x !== undefined) payload.x = Number(target.dataset.x);
    if (target.dataset.y !== undefined) payload.y = Number(target.dataset.y);
    if (target.dataset.stat) payload.stat = target.dataset.stat;
    if (target.dataset.code) payload.code = target.dataset.code;
    const previousPieces = action === 'buy'
      ? [...(state.room?.autochess?.players?.find((player) => player.seat_id === state.room.autochess.viewer_id)?.pieces || [])]
      : [];
    const purchaseCode = action === 'buy'
      ? state.room?.autochess?.players?.find((player) => player.seat_id === state.room.autochess.viewer_id)?.shop?.[payload.slot]?.code
      : '';
    try {
      target.disabled = true;
      target.textContent = '处理中…';
      const response = await fetchJson('/api/rooms/autochess/action', {method: 'POST', body: JSON.stringify(payload), timeoutMs: 15000});
      applyRoomPayload(response, {preserveScreen: true});
      if (action === 'place' && target.dataset.x !== undefined) selectedPieceId = '';
      if (action === 'buy') {
        const own = state.room?.autochess?.players?.find((player) => player.seat_id === state.room.autochess.viewer_id);
        const earlierIds = new Set(previousPieces.map((piece) => piece.id));
        const upgraded = own?.pieces?.find((piece) => piece.id === payload.piece_id)
          || own?.pieces?.find((piece) => piece.code === purchaseCode && !earlierIds.has(piece.id));
        if (upgraded) {
          selectedPieceId = upgraded.id;
          selectedHeroCode = '';
        }
      }
      state.roomError = '';
      actionError = '';
      messageRound = state.room?.autochess?.round || 0;
      actionMessage = action === 'ready' ? '备战已确认；其他玩家确认或倒计时结束后自动开战。'
        : ({buy: payload.piece_id ? '强化成功，请在武将卡片中选择一项能力 +1。' : '购买成功，已选中武将，请点击战场左侧布阵。', place: '布阵已更新。', level_up: '玩家等级已提升。',
            reroll: '商店已刷新。', draw_equipment: '装备已抽取，请在背包中查看。'}[action] || '操作已完成。');
      render();
    } catch (error) {
      actionError = error?.error || (error instanceof TypeError ? '网络连接失败，请检查连接后重试。' : error?.message)
        || '操作未完成，请稍后重试。';
      actionMessage = '';
      render();
    }
  });
}
