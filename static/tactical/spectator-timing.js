// Cadence for receiving committed frames and showing their short-lived cues.
export function roomPollDelay(room, hidden = false, backoff = 0) {
  const chess = room?.mode === 'autochess' ? room.autochess : null;
  const activeChess = ['preparation', 'battle'].includes(chess?.phase);
  if (hidden) return activeChess ? 1500 : 5000;
  if (backoff) return backoff;
  if (chess?.phase === 'battle') {
    const speed = Math.max(0.5, Number(room.chess_battle_speed) || 1);
    return Math.max(200, Math.min(400, 800 / speed));
  }
  return room?.status === 'battle' || activeChess ? 800 : 1500;
}

export function chessEffectTiming(speed, reduced = false) {
  const scale = Math.max(1, Number(speed) || 1);
  return {
    duration: Math.max(100, (reduced ? 180 : 850) / scale),
    spacing: reduced ? 0 : 180 / scale,
    targetGap: reduced ? 0 : 45 / scale,
    linger: Math.max(20, 80 / scale),
  };
}
