"""Local arithmetic expectations used by the original AI scoring probes.

There is no probability tape, alternate battle, or recursive replay here.
The battle probe is a mean-effect scoring model, not a joint outcome solver.
"""
import math
from contextlib import contextmanager
from contextvars import ContextVar

from wujiang.tactical.prediction import direct_scoring, note
_deaths = ContextVar("ai_direct_local_deaths", default=None)


class ExpectedDamage(float):
    """Mean HP loss plus the local kill probability, carried out of a probe."""
    def __new__(cls, amount, kill_probability=0.0):
        value = super().__new__(cls, amount)
        value.kill_probability = kill_probability
        return value


def damage_result(ctx, amount):
    if direct_scoring():
        return ExpectedDamage(amount, 1.0 if not ctx.target.alive else ctx.forecast_kill_probability)
    return amount


@contextmanager
def scoring_probe():
    token = _deaths.set({})
    try:
        yield
    finally:
        _deaths.reset(token)


def damage_chance(ctx, probability):
    ctx.forecast_hit_probability *= max(0.0, min(1.0, probability))
    note("direct_probability_factors")


def attack_change(ctx, change, probability):
    previous = ctx.forecast_attack_changes or ((0.0, 1.0),)
    values = {}
    for value, weight in previous:
        values[value] = values.get(value, 0.0) + weight * (1 - probability)
        changed = value + change
        values[changed] = values.get(changed, 0.0) + weight * probability
    ctx.forecast_attack_changes = tuple(values.items())
    note("direct_attack_expectations")


def damage_amount(battle, ctx, defense):
    if ctx.raw_damage is not None:
        return ctx.raw_damage
    if ctx.forecast_attack_changes is None:
        return battle.damage_rule.calculate_damage(ctx.attack_power, defense)
    rows = tuple((battle.damage_rule.calculate_damage(ctx.attack_power + delta, defense), probability)
                 for delta, probability in ctx.forecast_attack_changes)
    ctx.forecast_damage_rows = rows
    return math.fsum(amount * probability for amount, probability in rows)


def apply_damage_expectation(ctx):
    hp = ctx.target.current_hp
    probability = ctx.forecast_hit_probability * (1 - max(0.0, min(1.0, ctx.complete_block_chance)))
    rows = ctx.forecast_damage_rows
    if rows is not None:
        # Keep the deterministic reductions made after the damage table. The
        # local model shares those reductions; it does not fork later hooks.
        mean = math.fsum(value * weight for value, weight in rows)
        reduction = mean - ctx.raw_damage
        rows = tuple((max(0.0, value - reduction), weight) for value, weight in rows)
    else:
        rows = ((max(0.0, ctx.raw_damage), 1.0),)
    ctx.forecast_kill_probability = probability * math.fsum(weight for amount, weight in rows if hp > 0 and amount >= hp)
    ctx.raw_damage = math.fsum(min(hp, amount) * weight for amount, weight in rows) * probability
    ctx.forecast_hit_probability = probability
    note("direct_damage_expectations")


def record_death_probability(ctx):
    values = _deaths.get()
    if values is None:
        return
    previous = values.get(ctx.target.unit_id, 0.0)
    probability = ctx.forecast_kill_probability
    values[ctx.target.unit_id] = previous + (1 - previous) * probability


def expected_death(unit):
    return 1.0 if not unit.alive else (_deaths.get() or {}).get(unit.unit_id, 0.0)


def finish_damage_expectation(ctx):
    # Guaranteed rebirth/rescue still runs in the original single probe. Its
    # living final recipient must not receive a certain-destruction reward.
    if ctx.target.alive and ctx.forecast_kill_probability >= 1:
        ctx.forecast_kill_probability = 0.0
        if _deaths.get() is not None:
            _deaths.get()[ctx.target.unit_id] = 0.0


def damage_probability(ctx):
    return ctx.forecast_hit_probability if direct_scoring() else 1.0
