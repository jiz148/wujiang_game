(function attachHomeUi(global) {
  "use strict";

  function rosterText(match) {
    const byTeam = new Map([[1, []], [2, []]]);
    (match.seats || []).forEach((seat) => {
      if (!seat.occupied) return;
      const roster = (seat.hero_roster || [])
        .map((hero) => `${hero.name}${Number(hero.count || 1) > 1 ? `×${hero.count}` : ""}`)
        .join("、") || "未记录阵容";
      byTeam.get(Number(seat.team_id))?.push(`${seat.name || `席位 ${seat.player_id}`}：${roster}`);
    });
    return `红队 ${byTeam.get(1).join("；") || "—"} / 蓝队 ${byTeam.get(2).join("；") || "—"}`;
  }

  function renderRecentMatches({document, state, loggedIn, onOpenReplay}) {
    const panel = document.getElementById("recent-matches-panel");
    const message = document.getElementById("recent-matches-message");
    const list = document.getElementById("recent-matches-list");
    const refresh = document.getElementById("refresh-recent-matches");
    if (!panel || !message || !list || !refresh) return;
    const visible = loggedIn && state.screen === "draft" && !state.historicalMatchId;
    panel.classList.toggle("hidden", !visible);
    if (!visible) return;
    renderProgression({document, state});
    refresh.disabled = state.recentMatchesBusy;
    refresh.textContent = state.recentMatchesBusy ? "正在刷新..." : "刷新战绩";
    message.textContent = state.recentMatchesError
      || (state.recentMatchesBusy
        ? "正在读取账号战绩..."
        : (state.recentMatches.length ? `最近 ${state.recentMatches.length} 场` : "还没有已完成的账号对局。"));
    list.innerHTML = "";
    state.recentMatches.forEach((match) => {
      const card = document.createElement("article");
      card.className = `recent-match-card ${match.result === "win" ? "is-win" : "is-loss"}`;
      const title = document.createElement("div");
      title.className = "recent-match-title";
      const result = document.createElement("strong");
      result.className = "recent-match-result";
      result.textContent = match.result === "win" ? "胜利" : "落败";
      const time = document.createElement("span");
      time.className = "meta-note";
      time.textContent = new Date(Number(match.finished_at || 0) * 1000)
        .toLocaleString("zh-CN", {hour12: false});
      title.append(result, time);
      const detail = document.createElement("div");
      detail.className = "recent-match-detail";
      detail.textContent = `${match.mode_name || match.mode} · ${match.reason_text || "对局已结束。"} · ${match.duration_seconds || 0} 秒${match.mvp_name ? ` · MVP ${match.mvp_name}` : ""}`;
      const rosters = document.createElement("div");
      rosters.className = "recent-match-rosters";
      rosters.textContent = rosterText(match);
      const actions = document.createElement("div");
      actions.className = "recent-match-actions";
      const matchLabel = document.createElement("span");
      matchLabel.className = "meta-note";
      matchLabel.textContent = `场次 ${match.match_id}`;
      const replay = document.createElement("button");
      replay.type = "button";
      replay.className = "ghost";
      replay.textContent = "查看历史回放";
      replay.disabled = !match.replay_available;
      replay.addEventListener("click", () => onOpenReplay(match.match_id));
      actions.append(matchLabel, replay);
      card.append(title, detail, rosters, actions);
      list.append(card);
    });
  }

  function renderProgression({document, state}) {
    const summary = document.getElementById("mastery-summary");
    const goal = document.getElementById("mastery-next-goal");
    const list = document.getElementById("mastery-hero-list");
    if (!summary || !goal || !list) return;
    list.innerHTML = "";
    goal.innerHTML = "";
    if (state.progressionBusy) {
      summary.textContent = "正在读取熟练度...";
      goal.textContent = "已完成战绩会自动计入，不提供战斗数值加成。";
      return;
    }
    if (state.progressionError) {
      summary.textContent = "熟练度暂不可用";
      goal.textContent = state.progressionError;
      return;
    }
    const progression = state.progression || {total_matches: 0, total_wins: 0, hero_progress: [], next_goal: {}};
    summary.textContent = progression.total_matches
      ? `${progression.total_matches} 场 · ${progression.total_wins} 胜 · 胜率 ${Math.round(Number(progression.win_rate || 0) * 100)}%`
      : "尚无正式对局";
    const goalTitle = document.createElement("strong");
    goalTitle.textContent = progression.next_goal?.kind === "first_match" ? "开始第一段修炼" : "下一目标";
    const goalDetail = document.createElement("span");
    goalDetail.textContent = progression.next_goal?.message || "完成第一场正式对局，开始积累武将熟练度。";
    goal.append(goalTitle, goalDetail);
    (progression.hero_progress || []).slice(0, 4).forEach((hero) => {
      const card = document.createElement("article");
      card.className = "mastery-hero-card";
      const head = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = hero.hero_name || hero.hero_code;
      const level = document.createElement("span");
      level.textContent = `${hero.mastery_level} · ${hero.mastery_points} 点`;
      head.append(name, level);
      const detail = document.createElement("span");
      detail.textContent = `${hero.matches} 场 · ${hero.wins} 胜${hero.next_mastery_level ? ` · 距${hero.next_mastery_level} ${hero.points_to_next_level} 点` : " · 已达大师"}`;
      const progress = document.createElement("progress");
      const currentThreshold = Number(hero.mastery_threshold || 0);
      const nextThreshold = Number(hero.next_mastery_threshold || hero.mastery_points || 1);
      progress.max = Math.max(1, nextThreshold - currentThreshold);
      progress.value = hero.next_mastery_level
        ? Math.max(0, Number(hero.mastery_points || 0) - currentThreshold)
        : progress.max;
      progress.setAttribute("aria-label", `${hero.hero_name || hero.hero_code}熟练度进度`);
      card.append(head, detail, progress);
      list.append(card);
    });
    if (!(progression.hero_progress || []).length) {
      const empty = document.createElement("span");
      empty.className = "meta-note";
      empty.textContent = "完成对局后，这里会按你实际使用的武将显示熟练进度。";
      list.append(empty);
    }
  }

  global.WujiangHomeUi = Object.freeze({renderRecentMatches, renderProgression, rosterText});
}(globalThis));
