// Auto chess preparation, standings and read-only battle observation.
import { $ } from '../core/dom.js';
import { fetchJson } from '../core/net.js';
import { render } from '../core/render.js';
import { state } from '../core/state.js';
import { applyRoomPayload } from './room-api.js';

let selectedPieceId = '';
let selectedMatchIndex = 0;

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
    const label = `${player.name || `席位 ${player.seat_id}`}${player.is_ai ? ' · AI' : ''} · ${player.health} 血 · ${player.level} 级 · ${player.wins}胜/${player.losses}负${player.eliminated ? ' · 已淘汰' : ''}`;
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
    const name = entry.code ? `${entry.name} · 原生${entry.level}级 · ${entry.cost}金` : '已售出';
    row.append(button(name, 'buy', {slot: entry.slot}, locked || !entry.code || me.gold < entry.cost));
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
    card.append(button(`${piece.name} ★${piece.star} · 原生${piece.level}级 · ${where}${upgrades ? ` · ${upgrades}` : ''}`,
      'select', {pieceId: piece.id}, locked));
    if (piece.pending_upgrades) {
      const choice = node('div', 'autochess-actions');
      choice.append(node('span', '', '升星选能力：'));
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

function renderDeployment(me, locked) {
  const section = node('section', 'autochess-deployment');
  section.append(node('h3', '', `布阵 · 已用等级 ${me.budget_used}/${me.budget}`));
  const occupied = new Map();
  for (const piece of me.fielded || []) {
    for (const [dx, dy] of piece.footprint || [[0, 0]]) {
      occupied.set(`${piece.x + dx},${piece.y + dy}`, piece);
    }
  }
  const board = node('div', 'autochess-grid');
  board.setAttribute('role', 'grid');
  board.setAttribute('aria-label', '己侧四列十行布阵棋盘');
  for (let y = 0; y < 10; y += 1) {
    for (let x = 0; x < 4; x += 1) {
      const piece = occupied.get(`${x},${y}`);
      const cell = button(piece ? piece.name.slice(0, 2) : '·', 'place',
        {pieceId: selectedPieceId, x, y}, locked || !selectedPieceId);
      cell.classList.add('autochess-cell');
      if (piece) cell.classList.add('is-occupied');
      cell.setAttribute('aria-label', `${x + 1}列${y + 1}行${piece ? `，${piece.name}` : '，空格'}`);
      board.append(cell);
    }
  }
  section.append(board);
  section.append(node('p', 'meta-note', '选中武将后点合法空格。右侧开战时会映射到战场右四列。'));
  return section;
}

function renderBattleBoard(battle) {
  const board = node('div', 'autochess-observer-grid');
  board.setAttribute('role', 'grid');
  board.setAttribute('aria-label', '自动战斗观战棋盘');
  const units = new Map();
  for (const unit of battle.units || []) {
    for (const cell of unit.occupied_cells || []) units.set(`${cell.x},${cell.y}`, unit);
  }
  for (let y = 0; y < battle.board.height; y += 1) {
    for (let x = 0; x < battle.board.width; x += 1) {
      const unit = units.get(`${x},${y}`);
      const cell = node('div', `autochess-observer-cell ${unit ? `side-${unit.player_id}` : ''}`,
        unit ? unit.name.slice(0, 2) : '·');
      cell.title = unit ? `${unit.name} · ${Math.round(unit.hp / Math.max(unit.max_hp, .001) * 100)}%血 · ${unit.player_id === 1 ? '左方' : '右方'}` : `${x + 1},${y + 1}`;
      board.append(cell);
    }
  }
  return board;
}

function renderMatches(chess) {
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
  if (match.battle) {
    section.append(node('p', '', `战斗轮 ${Math.min(match.battle.round_number, chess.config.battle_round_limit)}/${chess.config.battle_round_limit} · ${match.battle.winner ? '已结束' : '自动交战中'}`));
    section.append(renderBattleBoard(match.battle));
    const log = node('div', 'autochess-battle-log');
    for (const line of (match.battle.logs || []).slice(-10)) log.append(node('p', '', line));
    section.append(log);
  }
  return section;
}

export function renderAutoChessPanel() {
  const panel = $('autochess-panel');
  if (!panel) return;
  const chess = state.room?.mode === 'autochess' ? state.room.autochess : null;
  panel.classList.toggle('hidden', !chess);
  if (!chess) return;
  panel.replaceChildren();
  const heading = node('div', 'autochess-heading');
  const phase = chess.phase === 'lobby' ? '等待开局' : chess.phase === 'preparation' ? '备战' : chess.phase === 'battle' ? '自动战斗' : '已结束';
  heading.append(node('h2', '', `自走棋 · 第 ${chess.round || 0} 轮 · ${phase}`));
  if (chess.deadline_at) heading.append(node('span', '', `备战截止：${new Date(chess.deadline_at * 1000).toLocaleTimeString()}`));
  panel.append(heading);
  panel.append(renderStandings(chess));
  if (chess.phase === 'lobby') panel.append(node('p', '', '房主选 4 人或 8 人后开局。空席由 AI 补齐。'));
  if (chess.phase === 'finished') {
    const winner = chess.players.find((player) => player.seat_id === chess.champion);
    panel.append(node('h3', '', `冠军：${winner?.name || `席位 ${chess.champion}`}`));
  }
  const me = chess.players.find((player) => player.seat_id === chess.viewer_id);
  if (me && chess.phase === 'preparation' && !me.eliminated) {
    const locked = me.ready;
    panel.append(node('p', '', `${me.gold} 金 · ${me.level} 级 · ${me.health} 血${locked ? ' · 本轮已确认' : ''}`));
    panel.append(renderShop(chess, me, locked));
    panel.append(renderSynergies(me));
    const layout = node('div', 'autochess-layout');
    layout.append(renderPieces(chess, me, locked), renderDeployment(me, locked));
    panel.append(layout);
    panel.append(button(locked ? '等待其他玩家' : '确认备战并开战', 'ready', {}, locked || me.pieces?.some((piece) => piece.pending_upgrades)));
  }
  if (chess.phase !== 'lobby') panel.append(renderMatches(chess));
  const lastRound = chess.history?.at(-1);
  if (lastRound) {
    const summary = node('section', 'autochess-history');
    summary.append(node('h3', '', `第 ${lastRound.round} 轮结算`));
    for (const [seatId, change] of Object.entries(lastRound.players || {})) {
      const name = chess.players.find((player) => player.seat_id === Number(seatId))?.name || `席位 ${seatId}`;
      summary.append(node('p', '', `${name}：+${change.gold_gain}金，-${change.health_loss}血，剩余${change.health}血`));
    }
    panel.append(summary);
  }
}

export function bindAutoChessEvents() {
  $('autochess-panel')?.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-chess-action]');
    if (!target) return;
    const action = target.dataset.chessAction;
    if (action === 'select') {
      selectedPieceId = target.dataset.pieceId;
      renderAutoChessPanel();
      return;
    }
    if (action === 'watch') {
      selectedMatchIndex = Number(target.dataset.index) || 0;
      renderAutoChessPanel();
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
    try {
      target.disabled = true;
      const response = await fetchJson('/api/rooms/autochess/action', {method: 'POST', body: JSON.stringify(payload)});
      applyRoomPayload(response, {preserveScreen: true});
      state.roomError = '';
      render();
    } catch (error) {
      state.roomError = error.error || '自走棋操作失败。';
      render();
    }
  });
}
