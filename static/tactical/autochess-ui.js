// Auto chess preparation, standings and read-only battle observation.
import { $ } from '../core/dom.js';
import { fetchJson } from '../core/net.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { setScreen } from '../core/ui.js';
import { bindHeroHover, hideHeroHover, renderHeroDetails } from './hero-hover.js';
import { applyRoomPayload } from './room-api.js';

let selectedPieceId = '';
let selectedHeroCode = '';
let selectedMatchIndex = 0;
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

function updateCountdown() {
  const clock = $('autochess-countdown');
  const chess = state.room?.mode === 'autochess' ? state.room.autochess : null;
  if (!clock || !chess || chess.phase !== 'preparation') return;
  const seconds = Math.max(0, Math.ceil(Number(chess.deadline_at || 0) - Date.now() / 1000 + serverClockOffset));
  clock.textContent = seconds ? `备战剩余 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : '时间到，正在开战…';
  clock.classList.toggle('is-urgent', seconds <= 15);
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
  section.append(node('h3', '', '武将商店'));
  const row = node('div', 'autochess-shop-row');
  (me.shop || []).forEach((entry) => {
    const card = node('div', 'autochess-shop-card');
    if (entry.code) {
      const hero = chess.hero_previews?.[entry.code];
      const matching = (me.pieces || []).filter((piece) => piece.code === entry.code);
      const selected = matching.find((piece) => piece.id === selectedPieceId);
      const target = selected || matching.sort((left, right) => Number(left.x === null) - Number(right.x === null) || right.star - left.star)[0];
      const name = button(`${entry.name} · ${entry.level}级`, 'inspect', {code: entry.code});
      name.classList.add('autochess-hero-name');
      if (hero) bindHeroHover(name, hero);
      card.append(name, node('small', '', `${entry.cost} 金 · ${hero?.role || ''} / ${hero?.attribute || ''} / ${hero?.race || ''}`));
      card.append(button(target ? `强化 ${target.name} ★${target.star}` : '购买新武将', 'buy',
        {slot: entry.slot, ...(target ? {pieceId: target.id} : {})}, locked || me.gold < entry.cost));
    } else {
      card.append(node('span', 'meta-note', '已售出'));
    }
    row.append(card);
  });
  section.append(row);
  const actions = node('div', 'autochess-actions');
  actions.append(button(`刷新 · ${chess.config.reroll_cost}金`, 'reroll', {}, locked || me.gold < chess.config.reroll_cost));
  const nextLevelCost = chess.config.level_costs[me.level - 1];
  actions.append(button(nextLevelCost ? `升玩家等级 · ${nextLevelCost}金` : '等级已满', 'level_up', {},
    locked || !nextLevelCost || me.gold < nextLevelCost));
  actions.append(button(me.equipment_drawn_this_round ? '本轮已抽装备' :
    (chess.equipment_available ? `抽装备 · ${chess.config.equipment_draw_cost}金` : '装备表不可用'),
    'draw_equipment', {}, locked || !chess.equipment_available || me.equipment_drawn_this_round ||
      me.gold < chess.config.equipment_draw_cost));
  section.append(actions);
  return section;
}

function renderPieces(chess, me, locked) {
  const section = node('section', 'autochess-pieces');
  section.append(node('h3', '', '武将与后备席'));
  const equipment = new Map((chess.equipment_catalog || []).map((item) => [item.code, item]));
  if (!me.pieces?.length) section.append(node('p', '', '从商店买武将，再选中武将点击己侧棋盘放置。'));
  for (const piece of me.pieces || []) {
    const card = node('div', `autochess-piece ${piece.id === selectedPieceId ? 'is-selected' : ''}`);
    const upgrades = Object.entries(piece.upgrades || {}).map(([stat, amount]) => `${({attack:'攻', defense:'守', speed:'速', attack_range:'范', mana:'魔'})[stat] || stat}+${amount}`).join('、');
    const where = piece.x === null ? '后备席' : `已上场 ${piece.x + 1},${piece.y + 1}`;
    const hero = chess.hero_previews?.[piece.code];
    const name = button(`${piece.name} ★${piece.star} · 原生${piece.level}级 · ${where}${upgrades ? ` · ${upgrades}` : ''}`,
      'select', {pieceId: piece.id});
    name.classList.add('autochess-hero-name');
    if (hero) bindHeroHover(name, hero);
    card.append(name);
    if (piece.entry_companion) card.append(node('small', 'autochess-companion', `开场带 ${companionName(piece.entry_companion)}：${piece.entry_companion}`));
    if (piece.pending_upgrades) {
      const choice = node('div', 'autochess-actions');
      choice.append(node('span', '', `强化能力（待选 ${piece.pending_upgrades} 次）：`));
      for (const [stat, label] of [['attack','攻击'], ['defense','防御'], ['speed','速度'],
                                   ['attack_range','范围'], ['mana','魔法']]) {
        choice.append(button(label, 'upgrade', {pieceId: piece.id, stat}, locked));
      }
      card.append(choice);
    }
    const controls = node('div', 'autochess-actions');
    if (piece.x !== null) controls.append(button('撤回', 'place', {pieceId: piece.id}, locked));
    controls.append(button('出售', 'sell', {pieceId: piece.id}, locked));
    card.append(controls);
    const gear = node('div', 'autochess-equipment');
    gear.append(node('small', '', `装备 ${piece.equipment?.length || 0}/${chess.config.equipment_slots}`));
    for (const code of piece.equipment || []) {
      const item = equipment.get(code);
      const line = node('div', 'autochess-equipment-row');
      line.append(node('span', '', `${item?.name || code} · ${item?.attributes || ''} · ${item?.effect || ''}`));
      line.append(button('卸下', 'unequip', {pieceId: piece.id, code}, locked));
      gear.append(line);
    }
    card.append(gear);
    section.append(card);
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

function renderSynergies(me) {
  const section = node('section', 'autochess-synergies');
  section.append(node('h3', '', '场上羁绊'));
  const list = (me.synergies || []).filter((item) => item.count > 0);
  if (!list.length) section.append(node('p', 'meta-note', '布阵后显示职业、属性与种族羁绊。'));
  for (const item of list) {
    const progress = item.next_threshold ? `下级需 ${item.next_threshold} 名` : '最高档';
    section.append(node('p', item.tier ? 'autochess-synergy-active' : 'meta-note',
      `${item.category_name} · ${item.name} ${item.count} 名 · ${item.tier ? `第 ${item.tier} 档：${item.effect}` : progress}`));
  }
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
    const piece = node('div', `piece board-piece is-hero ${cells.length > 1 ? 'is-footprint' : ''} player-${body.side || 1} autochess-board-piece`);
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
    const ring = node('div', 'piece-ring');
    const core = node('div', 'piece-core');
    core.append(node('div', 'piece-name', body.name));
    ring.append(core);
    piece.append(ring);
    if (body.extra) piece.append(node('small', 'autochess-piece-note', body.extra));
    piece.title = body.title || body.name;
    layer.append(piece);
  }
}

function renderDeployment(chess, me, locked) {
  const section = node('section', 'autochess-deployment');
  section.append(node('h3', '', `战场布阵 · 已上场 ${me.fielded?.length || 0} 名`));
  const budget = node('div', 'autochess-capacity');
  budget.append(node('strong', '', `当前 ${me.level} 级：等级预算 ${me.budget_used}/${me.budget}`));
  budget.append(node('span', '', `最多可放 ${me.budget} 名 1 级将；按现有武将等级，最多 ${me.roster_capacity} 名`));
  if (me.level < chess.config.level_budgets.length) {
    const next = chess.config.level_budgets[me.level];
    budget.append(node('span', '', `下一级 ${me.level + 1} 级：预算 ${next}，最多 ${next} 名 1 级将；现有阵容最多 ${me.next_roster_capacity} 名`));
  } else budget.append(node('span', '', '已达最高玩家等级'));
  section.append(budget);
  const occupied = new Map();
  for (const piece of me.fielded || []) {
    for (const [dx, dy] of piece.footprint || [[0, 0]]) {
      occupied.set(`${piece.x + dx},${piece.y + dy}`, piece);
    }
  }
  const chosen = (me.pieces || []).find((piece) => piece.id === selectedPieceId);
  const placementCost = chosen ? me.budget_used - (chosen.x === null ? 0 : chosen.level) + chosen.level : 0;
  const canPlace = (x, y) => Boolean(chosen && placementCost <= me.budget &&
    (chosen.footprint || [[0, 0]]).every(([dx, dy]) => {
      const px = x + dx;
      const py = y + dy;
      const blocker = occupied.get(`${px},${py}`);
      return px >= 0 && px < 4 && py >= 0 && py < 10 && (!blocker || blocker.id === chosen.id);
    }));
  const {stage, board, pieces} = battleStage();
  const cellByKey = new Map();
  board.setAttribute('role', 'grid');
  board.setAttribute('aria-label', '十列十行战场，左侧四列可以布阵');
  for (let y = 0; y < 10; y += 1) {
    for (let x = 0; x < 10; x += 1) {
      const occupant = occupied.get(`${x},${y}`);
      const allowed = x < 4 && canPlace(x, y);
      const cell = x < 4
        ? button('', 'place', {pieceId: selectedPieceId, x, y}, locked || !allowed)
        : node('div');
      cell.classList.add('cell', x < 4 ? 'autochess-friendly-cell' : x >= 6 ? 'autochess-enemy-cell' : 'autochess-middle-cell');
      if (occupant) cell.classList.add('has-unit');
      if (allowed && !locked) cell.classList.add('is-legal');
      cell.style.gridColumn = String(x + 1);
      cell.style.gridRow = String(y + 1);
      cell.setAttribute('aria-label', `${x + 1}列${y + 1}行；${x < 4 ? '己方布阵区' : x >= 6 ? '敌方入场区' : '交战区'}；${occupant?.name || '空格'}${allowed && !locked ? '；可放置' : ''}`);
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
    side: 1, name: piece.name, extra: piece.entry_companion ? `+ ${companionName(piece.entry_companion)}（坐骑）` : `★${piece.star}`,
    title: `${piece.name} ★${piece.star}${piece.entry_companion ? ` · ${piece.entry_companion}` : ''}`,
    cells: (piece.footprint || [[0, 0]]).map(([dx, dy]) => ({x: piece.x + dx, y: piece.y + dy})),
  }));
  const pairing = chess.matches?.find((match) => match.left === me.seat_id || match.right === me.seat_id);
  const opponentId = pairing?.left === me.seat_id ? pairing.right : pairing?.left;
  const opponent = chess.players.find((player) => player.seat_id === opponentId);
  if (opponent) section.append(node('p', 'autochess-opponent', `本轮对手：${opponent.name}${opponent.is_ai ? '（AI）' : ''} · 敌方阵型显示在右侧`));
  const opponentBodies = (opponent?.fielded || []).map((piece) => ({
    side: 2, name: piece.name, extra: piece.entry_companion ? `+ ${companionName(piece.entry_companion)}` : `★${piece.star}`,
    title: `${opponent.name} · ${piece.name}`,
    cells: (piece.footprint || [[0, 0]]).map(([dx, dy]) => ({x: piece.x + dx + 6, y: piece.y + dy})),
  }));
  renderBodies(pieces, [...ownBodies, ...opponentBodies]);
  section.append(stage);
  section.append(node('p', 'meta-note', chosen
    ? `已选 ${chosen.name}，占 ${chosen.footprint?.length || 1} 格。亮起的位置可放置；点击棋盘左侧四列即可布阵。`
    : '先在后备席选中武将，再点击战场左侧四列的亮起位置。右侧是对手入场区。'));
  return section;
}

function renderBattleBoard(battle) {
  const {stage, board, pieces} = battleStage();
  board.setAttribute('role', 'grid');
  board.setAttribute('aria-label', '自动战斗观战棋盘');
  const occupied = new Map();
  const bodies = new Map();
  for (const unit of battle.units || []) {
    if (!unit.position || !unit.alive || unit.banished) continue;
    for (const cell of unit.occupied_cells || []) occupied.set(`${cell.x},${cell.y}`, unit);
    const key = `${unit.player_id}:${unit.position.x},${unit.position.y}`;
    if (!bodies.has(key)) bodies.set(key, {side: unit.player_id, name: '', extra: '', title: '', cells: new Map(), hpRatio: 1});
    const body = bodies.get(key);
    body.name += `${body.name ? ' + ' : ''}${unit.name}`;
    body.title += `${body.title ? ' / ' : ''}${unit.name} ${Math.round(unit.hp / Math.max(unit.max_hp, .001) * 100)}%血`;
    if (unit.is_mount) body.extra = '召唤坐骑';
    body.hpRatio = Math.min(body.hpRatio, unit.hp / Math.max(unit.max_hp, .001));
    for (const cell of unit.occupied_cells || []) body.cells.set(`${cell.x},${cell.y}`, cell);
  }
  for (let y = 0; y < battle.board.height; y += 1) {
    for (let x = 0; x < battle.board.width; x += 1) {
      const unit = occupied.get(`${x},${y}`);
      const cell = node('div', `cell ${x < 4 ? 'autochess-friendly-cell' : x >= 6 ? 'autochess-enemy-cell' : 'autochess-middle-cell'}`);
      if (unit) cell.classList.add('has-unit');
      cell.style.gridColumn = String(x + 1);
      cell.style.gridRow = String(y + 1);
      cell.title = unit ? `${unit.name} · ${Math.round(unit.hp / Math.max(unit.max_hp, .001) * 100)}%血` : `${x + 1},${y + 1}`;
      board.append(cell);
    }
  }
  renderBodies(pieces, [...bodies.values()].map((body) => ({...body, cells: [...body.cells.values()]})));
  return stage;
}

function renderMatchPreview(chess, match) {
  const units = [];
  for (const [side, seatId] of [[1, match.left], [2, match.right]]) {
    const player = chess.players.find((item) => item.seat_id === seatId);
    for (const piece of player?.fielded || []) {
      const x = piece.x + (side === 2 ? 6 : 0);
      units.push({name: piece.name, player_id: side, position: {x, y: piece.y},
        alive: true, banished: false, hp: 1, max_hp: 1,
        occupied_cells: (piece.footprint || [[0, 0]]).map(([dx, dy]) => ({x: x + dx, y: piece.y + dy}))});
    }
  }
  return renderBattleBoard({board: {width: 10, height: 10}, units});
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
    section.append(renderBattleBoard(match.battle));
    const log = node('div', 'autochess-battle-log');
    for (const line of (match.battle.logs || []).slice(-10)) log.append(node('p', '', line));
    section.append(log);
  } else if (includeBoard) {
    section.append(node('p', 'meta-note', chess.phase === 'battle' ? '正在建立战场，阵容已锁定…' : '备战阵型预览'));
    section.append(renderMatchPreview(chess, match));
  }
  return section;
}

export function renderAutoChessPanel() {
  const panel = $('autochess-panel');
  if (!panel) return;
  const chess = state.room?.mode === 'autochess' ? state.room.autochess : null;
  panel.classList.toggle('hidden', !chess);
  if (!chess) {
    hideHeroHover();
    return;
  }
  hideHeroHover();
  panel.replaceChildren();
  if (Number(chess.server_time) && Number(chess.server_time) !== lastServerTimestamp) {
    lastServerTimestamp = Number(chess.server_time);
    serverClockOffset = Date.now() / 1000 - lastServerTimestamp;
  }
  const heading = node('div', 'autochess-heading');
  const phase = chess.phase === 'lobby' ? '等待开局' : chess.phase === 'preparation' ? '备战' : chess.phase === 'battle' ? '自动战斗' : '已结束';
  heading.append(node('h2', '', `自走棋 · 第 ${chess.round || 0} 轮 · ${phase}`));
  if (chess.deadline_at) {
    const clock = node('strong', 'autochess-countdown');
    clock.id = 'autochess-countdown';
    clock.setAttribute('role', 'timer');
    heading.append(clock);
  }
  panel.append(heading);
  updateCountdown();
  const living = chess.players.filter((player) => !player.eliminated);
  const readyCount = living.filter((player) => player.ready).length;
  if (chess.phase === 'preparation') {
    panel.append(node('p', 'autochess-phase-status', `${readyCount}/${living.length} 人已确认 · 全员确认或倒计时结束后自动开战`));
  } else if (chess.phase === 'battle') {
    panel.append(node('p', 'autochess-phase-status', '正在自动交战，战场和战报会持续更新。'));
  }
  if (actionError || state.roomError) panel.append(node('p', 'autochess-feedback is-error', actionError || state.roomError));
  if (messageRound !== chess.round) actionMessage = '';
  if (actionMessage) panel.append(node('p', 'autochess-feedback', actionMessage));
  const layout = node('div', 'autochess-stage-layout');
  const main = node('main', 'autochess-main');
  const dock = node('aside', 'autochess-dock');
  const nav = node('div', 'autochess-actions');
  nav.append(button('房间与邀请', 'lobby'));
  dock.append(nav);
  if (chess.phase === 'lobby') dock.append(node('p', '', '房主选 4 人或 8 人后开局。空席由 AI 补齐。'));
  if (chess.phase === 'finished') {
    const winner = chess.players.find((player) => player.seat_id === chess.champion);
    dock.append(node('h3', '', `冠军：${winner?.name || `席位 ${chess.champion}`}`));
  }
  const me = chess.players.find((player) => player.seat_id === chess.viewer_id);
  if (me && chess.phase === 'preparation' && !me.eliminated) {
    if (selectedPieceId && !(me.pieces || []).some((piece) => piece.id === selectedPieceId)) selectedPieceId = '';
    if (!selectedHeroCode && me.shop?.some((entry) => entry.code)) selectedHeroCode = me.shop.find((entry) => entry.code).code;
    const locked = me.ready;
    dock.append(node('p', 'autochess-economy', `${me.gold} 金 · 玩家 ${me.level} 级 · ${me.health} 血${locked ? ` · 已确认，等待其余 ${living.length - readyCount} 人` : ''}`));
    main.append(renderDeployment(chess, me, locked));
    dock.append(renderShop(chess, me, locked));
    dock.append(renderPieces(chess, me, locked));
    dock.append(renderSelectedHero(chess));
    dock.append(renderSynergies(me));
    const pending = me.pieces?.some((piece) => piece.pending_upgrades);
    if (pending) dock.append(node('p', 'autochess-feedback', '请先为强化武将选择能力值，才能确认备战。'));
    const confirm = button(locked ? '已确认 · 等待开战' : '确认备战', 'ready', {}, locked || pending);
    confirm.classList.add('autochess-confirm');
    dock.append(confirm);
  } else if (chess.phase !== 'lobby') {
    main.append(renderMatches(chess));
  }
  dock.append(renderStandings(chess));
  const lastRound = chess.history?.at(-1);
  if (lastRound) {
    const summary = node('section', 'autochess-history');
    summary.append(node('h3', '', `第 ${lastRound.round} 轮结算`));
    for (const [seatId, change] of Object.entries(lastRound.players || {})) {
      const name = chess.players.find((player) => player.seat_id === Number(seatId))?.name || `席位 ${seatId}`;
      summary.append(node('p', '', `${name}：+${change.gold_gain}金，-${change.health_loss}血，剩余${change.health}血`));
    }
    dock.append(summary);
  }
  layout.append(main, dock);
  panel.append(layout);
}

export function bindAutoChessEvents() {
  window.setInterval(updateCountdown, 250);
  $('autochess-panel')?.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-chess-action]');
    if (!target) return;
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
