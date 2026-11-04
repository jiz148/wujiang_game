"""Source identity and configuration for AI diagnostics; no game imports."""
from functools import lru_cache
import hashlib
import os
from pathlib import Path
from wujiang.platform.ai_native import native_status


AI_SOURCE_FILES = (
    "src/wujiang/tactical/prediction_contracts.json",
    "tools/build_ai_query_contracts.py",
    "src/wujiang/tactical/engine/core.py",
    "src/wujiang/tactical/engine/geometry.py",
    "src/wujiang/tactical/rooms/ai.py",
    "src/wujiang/tactical/prediction_factors.py",
    "src/wujiang/tactical/rooms/ai_acceleration.py",
    "src/wujiang/tactical/rooms/ai_parallel.py",
    "src/wujiang/tactical/rooms/ai_router.py",
    "src/wujiang/tactical/rooms/multiplayer.py",
    "src/wujiang/tactical/rooms/autochess.py",
    "src/wujiang/tactical/heroes/common.py",
    "src/wujiang/tactical/heroes/next_five.py",
    "static/tactical/autochess-ui.js",
    "src/wujiang/tools/ai_equivalence.py",
    "src/wujiang/tools/ai_validation.py",
    "src/wujiang/tools/ai_diagnostics.py",
    "src/wujiang/tools/ai_control_diagnostics.py",
    "src/wujiang/tools/ai_scenarios.py",
    "src/wujiang/platform/id_sequence.py",
    "src/wujiang/platform/ai_scoring_policy.py",
    "src/wujiang/tools/ai_baseline.py",
    "src/wujiang/platform/ai_build.py",
    "src/wujiang/platform/ai_native.py",
    "src/wujiang/tactical/engine/native/rollback_copy.c",
    "tools/build_ai_native.py",
    "tests/test_behavior.py",
    "tests/test_ai_prediction.py",
    "tests/test_ai_direct_expectation.py",
    "tools/validate_full_ai.py",
    "tools/diagnose_full_ai.py",
    "tools/diagnose_control_ai.py",
    "run.py",
    "start_windows_server.bat",
    "start_windows_server_legacy.bat",
    "scripts/start_windows_server.ps1",
    "src/wujiang/platform/ai_prediction_native.py",
    "src/wujiang/tactical/engine/native/prediction_prepare.c",
    "tools/build_ai_prediction_native.py",
)


@lru_cache(maxsize=1)
def _identity():
    root = Path(__file__).resolve().parents[3]
    digest, missing = hashlib.sha256(), []
    names = set(AI_SOURCE_FILES)
    names.update(path.relative_to(root).as_posix() for path in (root / "src/wujiang/tactical").rglob("*.py"))
    for name in sorted(names):
        digest.update(name.encode("utf-8") + b"\0")
        try:
            digest.update((root / name).read_bytes())
        except OSError:
            missing.append(name)
    return digest.hexdigest()[:16], tuple(missing)


def build_info():
    from wujiang.platform.ai_scoring_policy import rules_enabled
    identity, missing = _identity()
    method = os.environ.get("WUJIANG_AI_EXPECTATION_METHOD", "direct")
    direct = rules_enabled() and method == "direct"
    prediction_artifact = {"available": False, "reason": "not used by legacy server scoring"}
    if rules_enabled() and not direct:
        from wujiang.platform.ai_prediction_native import native_status as prediction_native_status
        prediction_artifact = dict(prediction_native_status())
    requested = os.environ.get("WUJIANG_AI_WORKERS", "4")
    try:
        worker_limit = int(requested)
    except ValueError:
        worker_limit = 4
    worker_limit = max(0, min(worker_limit, 4, (os.cpu_count() or 1) - 1))
    info = {"id": identity, "missing_sources": list(missing), "features": {
        "server_profile": os.environ.get("WUJIANG_AI_SERVER_PROFILE", "custom"),
        "scoring_rules": ("expected-visible-direct-v2" if direct else "expected-visible-v1") if rules_enabled() else "legacy",
        "prediction_method": method if rules_enabled() else "unused",
        "prediction_whole_battle_probability_replay": rules_enabled() and not direct,
        "prediction_direct_model": "local_probability_weighted_contributions_original_probes" if direct else "unused",
        "direct_position_cache": "whole_baseline_only_no_per_row_serialization" if direct else "unused",
        "expected_random_scoring": rules_enabled(),
        "enemy_stealth_ignored_for_scoring": rules_enabled(),
        "enemy_clone_identity_hidden_for_scoring": rules_enabled(),
        "prediction_rollback": "alias_preserving_graph" if rules_enabled() else "legacy",
        "prediction_distribution_cache_bytes": 16 * 1024 * 1024 if rules_enabled() else 0,
        "prediction_graph_snapshot_cache": (("identity_checked_16_entries_8MiB_including_pickle_memos"
            if os.environ.get("WUJIANG_AI_PREPARATION_BATCH", "1") != "0" else "identity_checked_4_entries_8MiB_accounting_budget")
            if rules_enabled() else "disabled"),
        "prediction_preparation_batch": rules_enabled() and os.environ.get("WUJIANG_AI_PREPARATION_BATCH", "1") != "0",
        "prediction_structural_batch": rules_enabled() and os.environ.get("WUJIANG_AI_STRUCTURAL_BATCH", "1") != "0",
        "prediction_code_contracts": "source_hash_and_bytecode_pinned_build_artifact" if rules_enabled() else "unused",
        "prediction_shared_checkpoints": ("source_proven_self_restoring_queries"
            if rules_enabled() and os.environ.get("WUJIANG_AI_STRUCTURAL_BATCH", "1") != "0" else "original"),
        "prediction_settled_projection": ("dependency_groups_local_recipients_and_shared_unaffected_units"
            if rules_enabled() and os.environ.get("WUJIANG_AI_STRUCTURAL_BATCH", "1") != "0" else "original"),
        "prediction_complete_state_keys": ("strict_serialization_reuse_with_full_graph_rng_protocol_alias_checks"
            if rules_enabled() and os.environ.get("WUJIANG_AI_PREPARATION_BATCH", "1") != "0" else "original"),
        "prediction_static_runtime_discovery": ("source_and_type_version_certified_empty_callbacks"
            if rules_enabled() and os.environ.get("WUJIANG_AI_PREPARATION_BATCH", "1") != "0" else "original"),
        "prediction_inline_boundaries": (("source_pinned_composition_and_plain_metadata_filters"
            if os.environ.get("WUJIANG_AI_STRUCTURAL_BATCH", "1") != "0" else "source_pinned_pure_composition_only")
            if rules_enabled() and os.environ.get("WUJIANG_AI_PREPARATION_BATCH", "1") != "0" else "original"),
        "prediction_preparation_native_requested": (rules_enabled()
            and os.environ.get("WUJIANG_AI_PREDICTION_NATIVE", "1") != "0"),
        "prediction_preparation_artifact": prediction_artifact,
        "prediction_argument_copy": "existing_native_with_python_fallback" if rules_enabled() else "legacy",
        "prediction_distribution_keys": "first_probability_fork_or_explicit_heavy_query" if rules_enabled() else "legacy",
        "prediction_queued_loss": "certified_recipient_factors_with_joint_fallback" if rules_enabled() else "legacy_per_target",
        "prediction_recipient_factors": rules_enabled() and os.environ.get("WUJIANG_AI_EXPECTATION_FACTORS", "1") != "0",
        "prediction_settled_area": ("certified_dependency_groups_with_joint_fallback"
            if rules_enabled() and os.environ.get("WUJIANG_AI_EXPECTATION_FACTORS", "1") != "0" else "original"),
        "prediction_defense_exposure": ("exact_distribution_convolution"
            if rules_enabled() and os.environ.get("WUJIANG_AI_EXPECTATION_FACTORS", "1") != "0" else "original"),
        "prediction_workers": "disabled_until_probability_context_transfer" if rules_enabled() else "legacy",
        "attack_ids": os.environ.get("WUJIANG_AI_ATTACK_IDS", "1") != "0",
        "move_batches": os.environ.get("WUJIANG_AI_MOVE_BATCHES", "1") != "0",
        "small_baselines": os.environ.get("WUJIANG_AI_SMALL_BASELINES", "1") != "0",
        "baseline_batches": os.environ.get("WUJIANG_AI_BASELINE_BATCHES", "1") != "0",
        "parallel_baselines": os.environ.get("WUJIANG_AI_PARALLEL_BASELINES", "1") != "0",
        "rng_independent_rows": os.environ.get("WUJIANG_AI_RNG_INDEPENDENT_ROWS", "0") != "0",
        "adaptive_rows": os.environ.get("WUJIANG_AI_ADAPTIVE_ROWS", "1") != "0",
        "tactical_queries": os.environ.get("WUJIANG_AI_TACTICAL_QUERIES", "0") != "0",
        "native_row_keys": os.environ.get("WUJIANG_AI_NATIVE_ROW_KEYS", "0") != "0",
        "native_component_scan": os.environ.get("WUJIANG_AI_NATIVE_COMPONENT_SCAN", "0") != "0",
        "parallel_boards": os.environ.get("WUJIANG_AI_PARALLEL_BOARDS", "0") != "0",
        "native_checkpoint_requested": os.environ.get("WUJIANG_AI_NATIVE_CHECKPOINT", "0") != "0",
        "native_flat_copy_requested": os.environ.get("WUJIANG_AI_NATIVE_FLAT_COPY", "0") != "0",
        "rollback_plans": os.environ.get("WUJIANG_AI_ROLLBACK_PLANS", "1") != "0",
        "native_rollback_requested": os.environ.get("WUJIANG_AI_NATIVE_ROLLBACK", "0") != "0",
        "native_rollback_artifact": dict(native_status()),
        "requested_workers": requested, "worker_limit": worker_limit,
        "fast_chess_worker_precompute": False,
        "fast_chess_parallel_baselines": (not rules_enabled() and os.environ.get("WUJIANG_AI_PARALLEL_BASELINES", "1") != "0"
                                           and os.environ.get("WUJIANG_AI_BASELINE_BATCHES", "1") != "0"),
        "board_commit_order": "original_serial",
    }}
    if direct:
        from wujiang.platform.ai_prediction_native import native_status as query_native_status
        info["features"].update({
            "prediction_rollback": "identity_validated_reusable_probes_with_original_fallback_and_one_enemy_view",
            "prediction_distribution_cache_bytes": 0, "prediction_graph_snapshot_cache": "one_enemy_view_plus_16_probe_snapshots_8MiB",
            "prediction_preparation_batch": False, "prediction_structural_batch": False,
            "prediction_code_contracts": "unused", "prediction_shared_checkpoints": "complete_identity_checked_probe_snapshots",
            "prediction_settled_projection": "unused", "prediction_complete_state_keys": "unused",
            "prediction_static_runtime_discovery": "unused", "prediction_inline_boundaries": "unused",
            "prediction_preparation_native_requested": False,
            "prediction_preparation_artifact": {"available": False, "reason": "no probability preparation in direct scoring"},
            "direct_query_native": query_native_status(),
            "direct_checkpoint_reuse": os.environ.get("WUJIANG_AI_DIRECT_CHECKPOINT_REUSE", "1") != "0",
            "direct_checkpoint_discovery": "reuse_empty_proof_only_after_complete_graph_and_original_code_match",
            "direct_placement_batches": os.environ.get("WUJIANG_AI_DIRECT_PLACEMENT_BATCHES", "1") != "0",
            "direct_checkpoint_cache_bytes": 8 * 1024 * 1024,
            "direct_component_dispatch": os.environ.get("WUJIANG_AI_DIRECT_EMPTY_HOOKS", "1") != "0",
            "prediction_argument_copy": "original_scoring_path", "prediction_distribution_keys": "unused",
            "prediction_queued_loss": "original_scalar_probe", "prediction_recipient_factors": False,
            "prediction_settled_area": "original_queue_once", "prediction_defense_exposure": "original_heuristic",
            "prediction_workers": "disabled_until_direct_view_context_transfer",
        })
    return info
