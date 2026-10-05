import { $ } from '../core/dom.js';
import { state } from '../core/state.js';
import { bpAssign, bpChoose, setBpCaptain } from './room-api.js';
import { bindHeroHover, hideHeroHover, showHeroHover } from './hero-hover.js';

let draftLevelFilter = 'all';
let draftFilterRoomId = '';

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

function addCaptainControls(panel, bp, seats) {
  panel.append(element('h3', '赛前设置'));
  panel.append(element('p', `${bp.team_size}v${bp.team_size} · 每队总等级不超过 ${bp.level_cap}。两队都指定真人队长，所有真人准备后自动进入禁选。`));
  for (const team of [1, 2]) {
    const row = element('div', '', 'bp-row');
    row.append(element('strong', `${teamName(team)}队长`));
    const candidates = seats.filter((seat) => seat.team_id === team && seat.is_human);
    if (state.room.viewer_is_host) {
      const select = document.createElement('select');
      select.className = 'select';
      select.append(option('', '请选择'));
      candidates.forEach((seat) => select.append(option(seat.player_id, seat.name || `席位 ${seat.player_id}`)));
      select.value = String(bp.captains?.[team] || '');
      select.addEventListener('change', () => {
        select.blur();
        if (select.value) void setBpCaptain(team, Number(select.value));
      });
      row.append(select);
    } else {
      const captain = seats.find((seat) => seat.player_id === Number(bp.captains?.[team]));
      row.append(element('span', captain?.name || '待指定'));
    }
    panel.append(row);
  }
  if (!state.room.configuration_ready && state.room.start_blocker) {
    panel.append(element('p', state.room.start_blocker, 'bp-error'));
  }
}

function addDraftControls(panel, bp) {
  const step = Number(bp.step_index || 0);
  const kind = bp.current_kind === 'ban' ? '禁用' : '选取';
  panel.append(element('h3', `禁选 ${step + 1}/${bp.step_count} · ${teamName(bp.current_team_id)}${kind}武将`));
  const captainId = Number(bp.captains?.[bp.current_team_id]);
  const captain = (state.room.seats || []).find((seat) => seat.player_id === captainId);
  panel.append(element('p', `当前由 ${captain?.name || teamName(bp.current_team_id) + '队长'} 操作。首选队：${teamName(bp.first_pick_team)}。`));
  if (Number(state.room.viewer_player_id) === captainId) {
    if (draftFilterRoomId !== state.room.room_id) {
      draftFilterRoomId = state.room.room_id;
      draftLevelFilter = 'all';
    }
    const used = new Set((bp.actions || []).map((action) => action.hero_code));
    const choices = (state.heroes || []).filter((hero) => !used.has(hero.code))
      .sort((left, right) => left.name.localeCompare(right.name, 'zh'));
    const remaining = Number(bp.level_cap || 0) - Number(bp.total_levels?.[bp.current_team_id] || 0);
    const row = element('div', '', 'bp-row');
    const levelSelect = document.createElement('select');
    levelSelect.className = 'select';
    levelSelect.dataset.bpLevelFilter = 'true';
    levelSelect.setAttribute('aria-label', '按武将等级筛选');
    levelSelect.append(option('all', '全部等级'));
    const levels = [...new Set(choices.map((hero) => Number(hero.level)))].sort((left, right) => left - right);
    levels.forEach((level) => levelSelect.append(option(level, `Lv ${level}`)));
    if (draftLevelFilter !== 'all' && !levels.includes(Number(draftLevelFilter))) draftLevelFilter = 'all';
    levelSelect.value = draftLevelFilter;
    const select = document.createElement('select');
    select.className = 'select';
    select.dataset.bpHeroChoice = 'true';
    select.setAttribute('aria-label', `${kind}武将`);
    const button = element('button', `确认${kind}`, 'primary');
    button.type = 'button';
    const refreshChoices = () => {
      const visible = choices.filter((hero) => levelSelect.value === 'all' || Number(hero.level) === Number(levelSelect.value));
      const legal = visible.filter((hero) => kind === '禁用' || Number(hero.level) <= remaining);
      select.replaceChildren();
      visible.forEach((hero) => {
        const item = option(hero.code, `${hero.name} · Lv ${hero.level}`);
        item.disabled = kind === '选取' && Number(hero.level) > remaining;
        select.append(item);
      });
      select.value = legal.length ? legal[0].code : '';
      button.disabled = legal.length === 0;
    };
    levelSelect.addEventListener('change', () => {
      draftLevelFilter = levelSelect.value;
      refreshChoices();
      hideHeroHover();
    });
    select.addEventListener('change', () => {
      const hero = choices.find((item) => item.code === select.value);
      if (hero) showHeroHover(hero, select);
    });
    select.addEventListener('mouseenter', () => {
      const hero = choices.find((item) => item.code === select.value);
      if (hero) showHeroHover(hero, select);
    });
    select.addEventListener('focus', () => {
      const hero = choices.find((item) => item.code === select.value);
      if (hero) showHeroHover(hero, select);
    });
    select.addEventListener('mouseleave', () => hideHeroHover(select));
    select.addEventListener('blur', () => hideHeroHover(select));
    refreshChoices();
    button.addEventListener('click', () => { if (select.value) void bpChoose(select.value); });
    row.append(levelSelect, select, button);
    panel.append(row);
  }
}

function addAssignmentControls(panel, bp, seats) {
  panel.append(element('h3', '分配控制权'));
  panel.append(element('p', '认领本队武将，或交给AI；队长可代队员分配。全部分配完成后由房主开战。'));
  for (const team of [1, 2]) {
    panel.append(element('h4', teamName(team)));
    const captain = Number(bp.captains?.[team]);
    const viewer = Number(state.room.viewer_player_id);
    const teammates = seats.filter((seat) => seat.team_id === team && seat.is_human);
    for (const code of bp.picks?.[team] || []) {
      const row = element('div', '', 'bp-row');
      const name = element('strong', heroName(code));
      const hero = (state.heroes || []).find((item) => item.code === code);
      if (hero) {
        name.tabIndex = 0;
        bindHeroHover(name, hero);
      }
      row.append(name);
      const assigned = bp.assignments?.[code];
      const canEdit = teammates.some((seat) => seat.player_id === viewer)
        && (viewer === captain || assigned == null || assigned === 'ai' || Number(assigned) === viewer);
      if (canEdit) {
        const select = document.createElement('select');
        select.className = 'select';
        select.append(option('', '待分配'), option('ai', '交给AI'));
        (viewer === captain ? teammates : teammates.filter((seat) => seat.player_id === viewer))
          .forEach((seat) => select.append(option(seat.player_id, seat.name || `席位 ${seat.player_id}`)));
        select.value = assigned == null ? '' : String(assigned);
        select.addEventListener('change', () => {
          select.blur();
          if (select.value) void bpAssign(code, select.value);
        });
        row.append(select);
      } else {
        const owner = seats.find((seat) => seat.player_id === Number(assigned));
        row.append(element('span', assigned === 'ai' ? 'AI' : owner?.name || '待分配'));
      }
      panel.append(row);
    }
  }
}

export function renderBpPanel() {
  const panel = $('bp-panel');
  if (!panel) return;
  const room = state.room;
  const visible = room?.mode === 'bp' && (room.status === 'lobby' || room.status === 'bp' || room.status === 'finished');
  panel.classList.toggle('hidden', !visible);
  if (!visible) {
    hideHeroHover();
    return;
  }
  const phaseKey = JSON.stringify([room.room_id, room.status, room.viewer_player_id, room.viewer_is_host, room.configuration_ready, room.start_blocker, room.bp, room.seats]);
  if (panel.dataset.bpPhaseKey === phaseKey) return;
  panel.dataset.bpPhaseKey = phaseKey;
  hideHeroHover();
  panel.replaceChildren();
  const bp = room.bp || {};
  const seats = room.seats || [];
  if (bp.show_win_count && bp.wins) {
    panel.append(element('div', `胜场　红队 ${bp.wins[1] || 0} : ${bp.wins[2] || 0} 蓝队`, 'bp-score'));
  }
  if (room.status === 'lobby') addCaptainControls(panel, bp, seats);
  else if (room.status === 'bp') {
    panel.append(element('p', `等级预算　红队 ${bp.total_levels?.[1] || 0}/${bp.level_cap}　·　蓝队 ${bp.total_levels?.[2] || 0}/${bp.level_cap}`));
    if (bp.phase === 'draft') addDraftControls(panel, bp);
    else addAssignmentControls(panel, bp, seats);
    const history = element('div', '', 'bp-history');
    history.append(element('h4', '禁选记录'));
    (bp.actions || []).forEach((action) => history.append(element('span',
      `${teamName(action.team_id)}${action.kind === 'ban' ? '禁' : '选'} ${action.hero_name} Lv ${action.hero_level}`, 'bp-chip')));
    panel.append(history);
  } else {
    panel.append(element('p', `本局选将等级　红队 ${bp.total_levels?.[1] || 0}/${bp.level_cap}　·　蓝队 ${bp.total_levels?.[2] || 0}/${bp.level_cap}`));
  }
}
