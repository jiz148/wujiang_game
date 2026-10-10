import { $ } from '../core/dom.js';
import { state } from '../core/state.js';
import { bpAssign, bpChoose, setBpCaptain } from './room-api.js';
import { hideHeroHover, renderHeroDetails } from './hero-hover.js';

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

function createHeroDetails() {
  const panel = element('aside', '', 'bp-hero-details');
  panel.dataset.bpHeroDetails = 'true';
  panel.setAttribute('aria-label', '武将信息');
  panel.append(element('h4', '武将信息'));
  const body = element('div', '', 'bp-hero-details__body');
  panel.append(body);
  let activeAnchor = null;
  const reset = (anchor = null) => {
    if (anchor && activeAnchor !== anchor) return;
    activeAnchor = null;
    body.replaceChildren(element('p', '将鼠标移到武将名字上，或用键盘聚焦查看资料。'));
  };
  const show = (anchor, hero) => {
    if (!hero) return reset(anchor);
    activeAnchor = anchor;
    renderHeroDetails(body, hero);
  };
  const bind = (anchor, heroOrGetter) => {
    const reveal = () => show(anchor, typeof heroOrGetter === 'function' ? heroOrGetter() : heroOrGetter);
    anchor.addEventListener('mouseenter', reveal);
    anchor.addEventListener('focus', reveal);
    anchor.addEventListener('mouseleave', () => reset(anchor));
    anchor.addEventListener('blur', () => reset(anchor));
  };
  reset();
  return { panel, bind, reset, show };
}

function addCaptainControls(panel, bp, seats) {
  panel.append(element('h3', '赛前设置'));
  panel.append(element('p', `${bp.team_size}v${bp.team_size} · 每队总等级不超过 ${bp.level_cap}。每局先随机禁用两个等级的全部武将；两队都指定真人队长、所有真人准备后进入禁选。`));
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

function addAutomaticBans(panel, bp, details) {
  const levels = (bp.auto_banned_levels || []).map(Number);
  if (!levels.length) return;
  const section = element('section', '', 'bp-auto-bans');
  section.append(element('h4', `系统禁用等级：${levels.map((level) => `Lv ${level}`).join('、')}`));
  section.append(element('p', '这两个等级的全部武将本局均不可禁用或选取。'));
  levels.forEach((level) => {
    const heroes = (state.heroes || []).filter((hero) => Number(hero.level) === level)
      .sort((left, right) => left.name.localeCompare(right.name, 'zh'));
    const group = document.createElement('details');
    group.className = 'bp-auto-bans__group';
    group.append(element('summary', `Lv ${level} · ${heroes.length} 名武将`));
    const names = element('div', '', 'bp-auto-bans__names');
    heroes.forEach((hero) => {
      const name = element('span', hero.name, 'bp-chip');
      name.tabIndex = 0;
      name.dataset.bpAutoBanHero = hero.code;
      details.bind(name, hero);
      names.append(name);
    });
    group.append(names);
    section.append(group);
  });
  panel.append(section);
}

function addDraftControls(panel, bp, details) {
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
    const autoBanned = new Set((bp.auto_banned_levels || []).map(Number));
    const choices = (state.heroes || []).filter((hero) => !used.has(hero.code) && !autoBanned.has(Number(hero.level)))
      .sort((left, right) => left.name.localeCompare(right.name, 'zh'));
    const remaining = Number(bp.level_cap || 0) - Number(bp.total_levels?.[bp.current_team_id] || 0);
    const legalLevels = Array.isArray(bp.legal_levels) ? new Set(bp.legal_levels.map(Number)) : null;
    const isLegal = (hero) => legalLevels
      ? legalLevels.has(Number(hero.level))
      : kind === '禁用' || Number(hero.level) <= remaining;
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
    const picker = document.createElement('details');
    picker.className = 'bp-hero-picker';
    picker.dataset.bpHeroChoice = 'true';
    const summary = element('summary', '', 'bp-hero-picker__summary');
    summary.setAttribute('aria-label', `${kind}武将`);
    const menu = element('div', '', 'bp-hero-picker__menu');
    menu.setAttribute('role', 'listbox');
    picker.append(summary, menu);
    let selectedCode = '';
    details.bind(summary, () => choices.find((hero) => hero.code === selectedCode));
    summary.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowDown') return;
      picker.open = true;
      const items = Array.from(menu.children);
      (items.find((item) => item.dataset.bpHeroOption === selectedCode) || items[0])?.focus();
      event.preventDefault();
    });
    menu.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        picker.open = false;
        summary.focus();
        event.preventDefault();
        return;
      }
      const items = Array.from(menu.children);
      const current = items.indexOf(document.activeElement);
      const next = event.key === 'ArrowDown' ? Math.min(current + 1, items.length - 1)
        : event.key === 'ArrowUp' ? Math.max(current - 1, 0)
          : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : -1;
      if (next < 0 || !items.length) return;
      items[next].focus();
      event.preventDefault();
    });
    const button = element('button', `确认${kind}`, 'primary');
    button.type = 'button';
    button.dataset.bpConfirm = 'true';
    const refreshChoices = () => {
      const visible = choices.filter((hero) => levelSelect.value === 'all' || Number(hero.level) === Number(levelSelect.value));
      const legal = visible.filter(isLegal);
      if (!legal.some((hero) => hero.code === selectedCode)) selectedCode = legal[0]?.code || '';
      summary.textContent = selectedCode
        ? `${heroName(selectedCode)} · Lv ${choices.find((hero) => hero.code === selectedCode)?.level}`
        : '无可选武将';
      menu.replaceChildren();
      visible.forEach((hero) => {
        const unavailable = !isLegal(hero);
        const reason = kind === '选取' && Number(hero.level) > remaining ? '超出等级上限' : '无法补齐阵容';
        const item = element('button', `${hero.name} · Lv ${hero.level}${unavailable ? `（${reason}）` : ''}`,
          `bp-hero-picker__option${unavailable ? ' is-unavailable' : ''}`);
        item.type = 'button';
        item.dataset.bpHeroOption = hero.code;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(selectedCode === hero.code));
        if (unavailable) item.setAttribute('aria-disabled', 'true');
        details.bind(item, hero);
        item.addEventListener('click', () => {
          if (unavailable) return;
          selectedCode = hero.code;
          refreshChoices();
          picker.open = false;
          summary.focus();
          details.show(summary, hero);
        });
        menu.append(item);
      });
      button.disabled = !selectedCode;
    };
    levelSelect.addEventListener('change', () => {
      draftLevelFilter = levelSelect.value;
      refreshChoices();
      picker.open = false;
      details.reset();
    });
    refreshChoices();
    button.addEventListener('click', () => { if (selectedCode) void bpChoose(selectedCode); });
    row.append(levelSelect, picker, button);
    panel.append(row);
  }
}

function addAssignmentControls(panel, bp, seats, details) {
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
        name.dataset.bpAssignedHero = code;
        details.bind(name, hero);
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
    const content = element('div', '', 'bp-content');
    const main = element('div', '', 'bp-content__main');
    const details = createHeroDetails();
    addAutomaticBans(main, bp, details);
    if (bp.phase === 'draft') addDraftControls(main, bp, details);
    else addAssignmentControls(main, bp, seats, details);
    const history = element('div', '', 'bp-history');
    history.append(element('h4', '禁选记录'));
    (bp.actions || []).forEach((action) => {
      const chip = element('span',
        `${teamName(action.team_id)}${action.kind === 'ban' ? '禁' : '选'} ${action.hero_name} Lv ${action.hero_level}`, 'bp-chip');
      const hero = (state.heroes || []).find((item) => item.code === action.hero_code);
      if (hero) {
        chip.tabIndex = 0;
        chip.dataset.bpHistoryHero = action.hero_code;
        details.bind(chip, hero);
      }
      history.append(chip);
    });
    main.append(history);
    content.append(main, details.panel);
    panel.append(content);
  } else {
    panel.append(element('p', `本局选将等级　红队 ${bp.total_levels?.[1] || 0}/${bp.level_cap}　·　蓝队 ${bp.total_levels?.[2] || 0}/${bp.level_cap}`));
  }
}
