import { $ } from '../core/dom.js';
import { state } from '../core/state.js';
import { bpAssign, bpChoose, setBpCaptain } from './room-api.js';

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
  panel.append(element('p', `${bp.team_size}v${bp.team_size} · 两队都指定真人队长，所有真人准备后自动进入禁选。`));
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
        if (select.value) void setBpCaptain(team, Number(select.value));
      });
      row.append(select);
    } else {
      const captain = seats.find((seat) => seat.player_id === Number(bp.captains?.[team]));
      row.append(element('span', captain?.name || '待指定'));
    }
    panel.append(row);
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
    const used = new Set((bp.actions || []).map((action) => action.hero_code));
    const choices = (state.heroes || []).filter((hero) => !used.has(hero.code))
      .sort((left, right) => left.name.localeCompare(right.name, 'zh'));
    const row = element('div', '', 'bp-row');
    const select = document.createElement('select');
    select.className = 'select';
    choices.forEach((hero) => select.append(option(hero.code, hero.name)));
    const button = element('button', `确认${kind}`, 'primary');
    button.type = 'button';
    button.disabled = choices.length === 0;
    button.addEventListener('click', () => { if (select.value) void bpChoose(select.value); });
    row.append(select, button);
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
      row.append(element('strong', heroName(code)));
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
  const visible = room?.mode === 'bp' && (room.status === 'lobby' || room.status === 'bp');
  panel.classList.toggle('hidden', !visible);
  if (!visible) return;
  if (panel.contains(document.activeElement) && document.activeElement?.tagName === 'SELECT') return;
  panel.replaceChildren();
  const bp = room.bp || {};
  const seats = room.seats || [];
  if (room.status === 'lobby') addCaptainControls(panel, bp, seats);
  else {
    if (bp.phase === 'draft') addDraftControls(panel, bp);
    else addAssignmentControls(panel, bp, seats);
    const history = element('div', '', 'bp-history');
    history.append(element('h4', '禁选记录'));
    (bp.actions || []).forEach((action) => history.append(element('span',
      `${teamName(action.team_id)}${action.kind === 'ban' ? '禁' : '选'} ${action.hero_name}`, 'bp-chip')));
    panel.append(history);
  }
}
