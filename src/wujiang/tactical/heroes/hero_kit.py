"""Object-owned assembly hooks for source-row heroes.

The Excel source keeps identity and stats. A kit owns only hero-specific
components, so adding a hero does not grow the central fragment factories.
"""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any, Mapping

from wujiang.tactical.engine.core import Skill, Stats, Trait
from wujiang.tactical.heroes.ai_policy import HeroAIPolicy


class HeroKit:
    code: str = ""
    ai_policy: HeroAIPolicy | None = None

    def special_skill(self, fragment: Mapping[str, Any]) -> Skill | None:
        return None

    def special_trait(self, fragment: Mapping[str, Any]) -> Trait | None:
        return None

    def extra_skills(self) -> list[Skill]:
        return []

    def augment_skills(self, skills: list[Skill]) -> list[Skill]:
        return skills

    def extra_traits(self) -> list[Trait]:
        return []

    def unit_attributes(self) -> dict[str, Any]:
        return {}

    def base_stats(self, source_stats: Stats) -> Stats:
        return source_stats


class FragmentMappedHeroKit(HeroKit):
    """Exact source-fragment mappings for heroes without custom assembly logic."""

    def __init__(
        self,
        code: str,
        *,
        skills: Mapping[str, Callable[[], Skill]] | None = None,
        skill_fragments: Mapping[str, Callable[[], Skill]] | None = None,
        traits: Mapping[str, Callable[[], Trait]] | None = None,
        extra_skill_factories: tuple[Callable[[], Skill], ...] = (),
        extra_trait_factories: tuple[Callable[[], Trait], ...] = (),
        attributes: Mapping[str, Any] | None = None,
        ai_policy: HeroAIPolicy | None = None,
    ) -> None:
        self.code = code
        self.ai_policy = ai_policy
        self._skill_factories = MappingProxyType(dict(skills or {}))
        self._skill_fragment_factories = MappingProxyType(dict(skill_fragments or {}))
        self._trait_factories = MappingProxyType(dict(traits or {}))
        self._extra_skill_factories = tuple(extra_skill_factories)
        self._extra_trait_factories = tuple(extra_trait_factories)
        self._attributes = MappingProxyType(dict(attributes or {}))

    def special_skill(self, fragment: Mapping[str, Any]) -> Skill | None:
        factory = self._skill_fragment_factories.get(fragment.get("fragment"))
        if factory is None:
            factory = self._skill_factories.get(fragment.get("name"))
        return factory() if factory is not None else None

    def special_trait(self, fragment: Mapping[str, Any]) -> Trait | None:
        factory = self._trait_factories.get(fragment.get("fragment"))
        return factory() if factory is not None else None

    def extra_skills(self) -> list[Skill]:
        return [factory() for factory in self._extra_skill_factories]

    def extra_traits(self) -> list[Trait]:
        return [factory() for factory in self._extra_trait_factories]

    def unit_attributes(self) -> dict[str, Any]:
        return dict(self._attributes)


_HERO_KITS: dict[str, HeroKit] = {}


def register_hero_kit(kit: HeroKit) -> HeroKit:
    if not kit.code or kit.code in _HERO_KITS:
        raise ValueError(f"duplicate or missing hero kit code: {kit.code!r}")
    _HERO_KITS[kit.code] = kit
    return kit


def hero_kit_for(code: str) -> HeroKit | None:
    return _HERO_KITS.get(code)
