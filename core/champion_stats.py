#!/usr/bin/env python3
"""
Champion base stat fetcher — Community Dragon API.

Startup'ta bir kez çağrılır, champion'a özel statik veriler:
  - base_attack_speed
  - windup_percent (mAttackDelayCastOffsetPercent + 0.3)
  - windup_modifier (mAttackDelayCastOffsetPercentAttackSpeedRatio)

Kullanım:
    from core.champion_stats import get_stats_safe
    stats = get_stats_safe("MissFortune")
"""

import json
import ssl
import urllib.request
from functools import lru_cache

CDRAGON_URL = (
    "https://raw.communitydragon.org/latest/game/data/"
    "characters/{name}/{name}.bin.json"
)


def _fetch_champion_json(name: str) -> dict:
    """Community Dragon'dan champion bin.json indir."""
    url = CDRAGON_URL.format(name=name.lower())
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=10, context=ctx) as resp:
        return json.loads(resp.read())


@lru_cache(maxsize=32)
def get_champion_base_stats(name: str) -> dict:
    """
    CDragon'dan base attack stats çek.

    Returns:
        {
            "base_attack_speed": float,
            "windup_percent": float,
            "windup_modifier": float,
        }
    """
    data = _fetch_champion_json(name)
    root_key = f"characters/{name.lower()}/characterrecords/root"

    # CDragon key'leri case-insensitive olabiliyor
    lower_data = {k.lower(): v for k, v in data.items()}

    if root_key not in lower_data:
        raise ValueError(f"Root key '{root_key}' not found for {name}")

    root = lower_data[root_key]
    base_as = root.get("attackSpeed", 0.625)

    basic_attack = root.get("basicAttack", {})
    windup_offset = basic_attack.get("mAttackDelayCastOffsetPercent", 0.0)
    windup_percent = windup_offset + 0.3
    windup_mod = basic_attack.get(
        "mAttackDelayCastOffsetPercentAttackSpeedRatio", 0.0
    )

    return {
        "base_attack_speed": base_as,
        "windup_percent": windup_percent,
        "windup_modifier": windup_mod,
    }


# Offline fallback — yaygın ADC'ler
FALLBACK_STATS = {
    "missfortune": {"base_attack_speed": 0.656, "windup_percent": 0.3, "windup_modifier": 0.0},
    "jinx":        {"base_attack_speed": 0.625, "windup_percent": 0.3, "windup_modifier": 0.0},
    "caitlyn":     {"base_attack_speed": 0.681, "windup_percent": 0.3, "windup_modifier": 0.0},
    "ezreal":      {"base_attack_speed": 0.625, "windup_percent": 0.3, "windup_modifier": 0.0},
    "vayne":       {"base_attack_speed": 0.658, "windup_percent": 0.3, "windup_modifier": 0.0},
    "kaisa":       {"base_attack_speed": 0.644, "windup_percent": 0.3, "windup_modifier": 0.0},
    "lucian":      {"base_attack_speed": 0.638, "windup_percent": 0.3, "windup_modifier": 0.0},
    "ashe":        {"base_attack_speed": 0.658, "windup_percent": 0.3, "windup_modifier": 0.0},
    "tristana":    {"base_attack_speed": 0.656, "windup_percent": 0.3, "windup_modifier": 0.0},
    "jhin":        {"base_attack_speed": 0.625, "windup_percent": 0.3, "windup_modifier": 0.0},
    "draven":      {"base_attack_speed": 0.679, "windup_percent": 0.3, "windup_modifier": 0.0},
    "twitch":      {"base_attack_speed": 0.679, "windup_percent": 0.3, "windup_modifier": 0.0},
    "kogmaw":      {"base_attack_speed": 0.665, "windup_percent": 0.3, "windup_modifier": 0.0},
    "aphelios":    {"base_attack_speed": 0.640, "windup_percent": 0.3, "windup_modifier": 0.0},
    "samira":      {"base_attack_speed": 0.658, "windup_percent": 0.3, "windup_modifier": 0.0},
    "zeri":        {"base_attack_speed": 0.658, "windup_percent": 0.3, "windup_modifier": 0.0},
    "smolder":     {"base_attack_speed": 0.625, "windup_percent": 0.3, "windup_modifier": 0.0},
}

# Universal default
DEFAULT_STATS = {
    "base_attack_speed": 0.625,
    "windup_percent": 0.3,
    "windup_modifier": 0.0,
}


def get_stats_safe(name: str) -> dict:
    """
    CDragon → fallback tablo → universal default.
    Asla exception fırlatmaz.
    """
    try:
        return get_champion_base_stats(name)
    except Exception as e:
        print(f"[!] CDragon fetch failed for {name}: {e}")
        lower = name.lower()
        if lower in FALLBACK_STATS:
            print(f"[*] Using fallback stats for {lower}")
            return FALLBACK_STATS[lower]
        print(f"[*] Using universal default stats")
        return DEFAULT_STATS.copy()
