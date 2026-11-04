"""Publish detached spectator frames at committed action boundaries.

Battle preparation and viewer-only economy data still use the normal room
serializer. During combat only the changed board and new hero previews need
replacement. Readers receive their own copy and never inspect an AI trial.
"""
import copy
import time


def current_frame(room, payload):
    public = payload.get("room", {}) if payload else {}
    frame = public.get("autochess", {})
    chess = room.autochess
    return (not getattr(room, "_chess_frame_publish_failed", False)
            and chess is not None and chess.phase == "battle" and frame.get("phase") == "battle"
            and frame.get("round") == chess.round_number
            and public.get("version") == room.version)


def publish_match(room, chess, match):
    cache = getattr(room, "_chess_state_cache", {})
    viewers = [(key, payload) for key, payload in cache.items() if current_frame(room, payload)]
    if not viewers or room.autochess is not chess or chess.phase != "battle":
        return
    from wujiang.platform.id_sequence import isolated_ids
    from wujiang.tactical.rooms.autochess import battle_observer_snapshot, hero_catalog, _hero_preview

    index = next(index for index, item in enumerate(chess.matches) if item is match)
    battle = match.runner.battle if match.runner else None
    # Preview construction may instantiate a hero. Presentation must never
    # consume the ID sequence used by the next real action.
    with isolated_ids():
        board = copy.deepcopy({"index": index, "left": match.left, "right": match.right,
                               "mirror": match.mirror, "result": match.result,
                               "battle": battle_observer_snapshot(battle) if battle is not None else None})
        known = set.intersection(*(set(payload["room"]["autochess"]["hero_previews"])
                                   for _, payload in viewers))
        codes = {str(getattr(unit, "hero_code", "") or "") for unit in battle.all_units()} if battle else set()
        catalog = hero_catalog()
        previews = {code: _hero_preview(catalog[code]) for code in codes - known if code in catalog}
    timestamp, published = time.time(), time.monotonic()
    for key, payload in viewers:
        previous = payload["room"]["autochess"]
        matches = list(previous["matches"])
        if len(matches) != len(chess.matches):
            continue
        matches[index] = board
        frame = {**previous, "matches": matches, "version": chess.version, "server_time": timestamp}
        if previews:
            frame["hero_previews"] = {**previous["hero_previews"], **previews}
        # Replace whole branches. Previously returned frames remain detached,
        # including frames another request is currently encoding as JSON.
        cache[key] = {**payload, "room": {**payload["room"], "autochess": frame}}
        room._chess_state_cache_at[key] = published
