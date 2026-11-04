from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_AI_SCORING = "direct"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.platform.security import SecurityConfig, normalize_origin  # noqa: E402


def parse_args() -> argparse.Namespace:
    from wujiang.platform.http.server import normalize_public_base_url
    parser = argparse.ArgumentParser(description="Run the Wujiang online test server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind. Default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on. Default: 8000")
    parser.add_argument("--ai-scoring", choices=("direct", "legacy"), default=DEFAULT_AI_SCORING,
                        help="AI scoring policy; legacy is retained for manual logic comparison.")
    parser.add_argument(
        "--public-base-url",
        default=os.environ.get("WUJIANG_PUBLIC_BASE_URL", ""),
        help="Public homepage URL to share with friends, e.g. http://203.0.113.10:8000 or https://game.example.com",
    )
    parser.add_argument(
        "--environment", choices=("development", "test", "production"), default="",
        help="Security profile. Production requires HTTPS and disables query-string auth tokens.",
    )
    parser.add_argument(
        "--require-https", action="store_true",
        help="Reject requests not received through TLS or a trusted HTTPS proxy.",
    )
    parser.add_argument(
        "--trusted-proxy-network", action="append", default=[],
        help="Trusted proxy IP/CIDR allowed to supply X-Forwarded-*; repeat as needed.",
    )
    parser.add_argument(
        "--allowed-origin", action="append", default=[],
        help="Additional exact browser Origin allowed for POST; repeat as needed.",
    )
    parser.add_argument(
        "--max-json-body-bytes", type=int, default=0,
        help="Maximum POST JSON body size; default 262144 bytes.",
    )
    args = parser.parse_args()
    try:
        args.public_base_url = normalize_public_base_url(args.public_base_url)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        defaults = SecurityConfig.from_environment()
        environment = args.environment or defaults.environment
        normalized_origins = tuple(
            normalize_origin(value) for value in (args.allowed_origin or defaults.allowed_origins)
        )
        if any(not value for value in normalized_origins):
            raise ValueError("--allowed-origin must be an exact http(s) origin without path, query, or credentials.")
        args.security_config = SecurityConfig(
            environment=environment,
            require_https=bool(args.require_https or defaults.require_https or environment == "production"),
            trusted_proxy_networks=tuple(args.trusted_proxy_network or defaults.trusted_proxy_networks),
            allowed_origins=normalized_origins,
            max_json_body_bytes=int(args.max_json_body_bytes or defaults.max_json_body_bytes),
            allow_query_auth_tokens=environment != "production" and defaults.allow_query_auth_tokens,
        ).validated()
    except ValueError as exc:
        parser.error(str(exc))
    return args


def server_ai_choice(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--ai-scoring", choices=("direct", "legacy"), default=DEFAULT_AI_SCORING)
    return parser.parse_known_args(argv)[0].ai_scoring


def configure_server_ai(scoring=None):
    """Pin the chosen measured profile before engine imports.

    Only the server entry calls this. Offline expectation experiments keep
    their own policy/configuration; inherited experiment flags cannot silently
    put the interactive server back on a slower path.
    """
    scoring = DEFAULT_AI_SCORING if scoring is None else scoring
    if scoring not in {"direct", "legacy"}:
        raise ValueError("Unknown server AI scoring: " + scoring)
    os.environ.update({
        "WUJIANG_AI_SERVER_PROFILE": "expected-direct-measured-fast" if scoring == "direct" else "legacy-measured-fast",
        "WUJIANG_AI_SCORING_RULES": "expected-visible-direct-v2" if scoring == "direct" else "legacy",
        "WUJIANG_AI_EXPECTATION_METHOD": "direct",
        "WUJIANG_AI_DIRECT_CHECKPOINT_REUSE": "1",
        "WUJIANG_AI_DIRECT_EMPTY_HOOKS": "1",
        "WUJIANG_AI_PREDICTION_NATIVE": "1",
        "WUJIANG_AI_WORKERS": "4",
        "WUJIANG_AI_ATTACK_IDS": "1",
        "WUJIANG_AI_MOVE_BATCHES": "1",
        "WUJIANG_AI_SMALL_BASELINES": "1",
        "WUJIANG_AI_BASELINE_BATCHES": "1",
        "WUJIANG_AI_PARALLEL_BASELINES": "1",
        "WUJIANG_AI_RNG_INDEPENDENT_ROWS": "1",
        "WUJIANG_AI_ADAPTIVE_ROWS": "1",
        "WUJIANG_AI_NATIVE_ROW_KEYS": "1",
        "WUJIANG_AI_ROLLBACK_PLANS": "1",
        "WUJIANG_AI_NATIVE_ROLLBACK": "1",
        "WUJIANG_AI_TACTICAL_QUERIES": "0",
        "WUJIANG_AI_NATIVE_COMPONENT_SCAN": "0",
        "WUJIANG_AI_NATIVE_CHECKPOINT": "0",
        "WUJIANG_AI_NATIVE_FLAT_COPY": "0",
        "WUJIANG_AI_PARALLEL_BOARDS": "0",
    })


def server_ai_build_info():
    """Report the loaded backend, without querying a battle or consuming IDs."""
    from wujiang.platform.ai_build import build_info
    from wujiang.tactical.engine import core
    from wujiang.tactical.rooms import ai_acceleration
    from wujiang.tactical.rooms import ai_parallel
    ai_build = build_info()
    loaded = core._NATIVE_ROLLBACK_COPY is not None
    ai_build["features"] = {
        **ai_build["features"],
        "attack_ids": core._AI_TARGET_IDS_ENABLED,
        "move_batches": ai_acceleration._MOVE_BATCHES_ENABLED,
        "small_baselines": ai_acceleration._SMALL_BASELINES_ENABLED,
        "baseline_batches": ai_acceleration._BASELINE_BATCHES_ENABLED,
        "parallel_baselines": ai_acceleration._PARALLEL_BASELINES_ENABLED,
        "rng_independent_rows": ai_acceleration._RNG_INDEPENDENT_ROWS_ENABLED,
        "adaptive_rows": ai_acceleration._ADAPTIVE_ROWS_ENABLED,
        "tactical_queries": ai_acceleration._TACTICAL_QUERIES_ENABLED,
        "native_row_keys": ai_acceleration._NATIVE_ROW_KEYS_ENABLED,
        "native_component_scan": core._NATIVE_COMPONENT_SCAN_ENABLED,
        "native_component_scan_loaded": core._NATIVE_COMPONENT_SCAN is not None,
        "native_component_scan_active": (core._NATIVE_COMPONENT_SCAN is not None
                                         and core.Unit.innate_traits_suppressed is core._native_innate_traits_suppressed),
        "native_row_keys_loaded": ai_acceleration.load_rng_mask() is not None,
        "native_row_keys_active": (ai_acceleration._NATIVE_ROW_KEYS_ENABLED
                                   and ai_acceleration._RNG_INDEPENDENT_ROWS_ENABLED
                                   and ai_acceleration.load_rng_mask() is not None),
        "parallel_boards": ai_parallel._BOARDS_ENABLED,
        "parallel_workers_available": not ai_acceleration._worker_disabled,
        "fast_chess_parallel_baselines": (ai_acceleration._BASELINE_BATCHES_ENABLED
                                           and ai_acceleration._PARALLEL_BASELINES_ENABLED),
        "native_checkpoint_requested": core._NATIVE_CHECKPOINT_ENABLED,
        "native_flat_copy_requested": core._NATIVE_FLAT_COPY_ENABLED,
        "native_flat_copy_loaded": core._NATIVE_FLAT_COPY is not None,
        "native_flat_copy_active": (core._NATIVE_FLAT_COPY is not None and loaded
                                    and core._NATIVE_FLAT_COPY_ENABLED and core._ROLLBACK_NATIVE_ENABLED
                                    and core._ROLLBACK_TYPE_PLANS_ENABLED and not core._NATIVE_CHECKPOINT_ENABLED),
        "native_checkpoint_loaded": core._NATIVE_CHECKPOINT is not None,
        "native_checkpoint_active": (core._NATIVE_CHECKPOINT is not None
                                     and loaded and core._NATIVE_CHECKPOINT_ENABLED and core._ROLLBACK_NATIVE_ENABLED),
        "rollback_plans": core._ROLLBACK_TYPE_PLANS_ENABLED,
        "native_rollback_requested": core._ROLLBACK_NATIVE_ENABLED,
        "native_rollback_loaded": loaded,
        "native_rollback_active": loaded and core._ROLLBACK_NATIVE_ENABLED and core._ROLLBACK_TYPE_PLANS_ENABLED,
    }
    return ai_build


if __name__ == "__main__":
    # The Windows launcher reaches this entry too. Configure before importing
    # the composition root so policy ContextVars and spawned workers agree.
    configure_server_ai(server_ai_choice())
    from wujiang.platform.http.server import run_server
    args = parse_args()
    ai_build = server_ai_build_info()
    print(f"AI computation build: {ai_build['id']}  config={ai_build['features']}", flush=True)
    run_server(
        host=args.host,
        port=args.port,
        public_base_url=args.public_base_url,
        security_config=args.security_config,
    )
