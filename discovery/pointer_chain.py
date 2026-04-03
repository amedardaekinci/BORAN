"""
Hero Object Pointer Chain Resolver
League of Legends macOS ARM64 — hero object base adreslerini bulur.

Pointer Chain (Patch 26.S6):
    base (Mach-O FEEDFACF)
    → base + 0x2300000 = GlobalPage
    → GP + 0x458 = HeroManager
    → HeroMgr + 0x8 = hero_array
    → HeroMgr + 0x10 = hero_count
    → hero_array + i*8 = hero[i]

Kullanım:
    sudo python3 -c "
    from discovery.pointer_chain import HeroResolver
    r = HeroResolver()
    heroes = r.resolve_all()
    for h in heroes:
        print(f'  {h[\"name\"]} @ 0x{h[\"addr\"]:X}')
    "
"""

import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.mem import MemReader

# ============================================================================
# Bilinen Offset'ler (Patch 26.S6, macOS ARM64)
# ============================================================================

# Pointer chain offset'leri
OFF_GLOBAL_PAGE = 0x2300000       # base + bu = GlobalPage pointer
OFF_HERO_MANAGER = 0x458          # GlobalPage + bu = HeroManager
OFF_HERO_ARRAY = 0x8              # HeroManager + bu = hero pointer array
OFF_HERO_COUNT = 0x10             # HeroManager + bu = hero count

# Hero object field offset'leri (doğrulama için)
OFF_CHAMPION_NAME = 0x3758        # hero + bu = champion name pointer
OFF_HP = 0x3704                   # hero + bu = current HP (float)
OFF_MAX_HP = 0x3708               # hero + bu = max HP (float)
OFF_ATTACK_RANGE = 0xF54          # hero + bu = attack range (float) — doğrulanacak
OFF_TEAM = 0x3C                   # hero + bu = team ID

# Bilinen attack range değerleri (tüm LoL championlar)
KNOWN_ATTACK_RANGES = {
    125, 150, 175, 200, 225, 325, 425, 475,
    500, 525, 550, 575, 600, 625, 650
}


class HeroResolver:
    """League of Legends hero object'lerini memory'den çözer."""

    def __init__(self, process_name: str = 'LeagueofLegends'):
        self.mem = MemReader.from_process_name(process_name)
        self.base = self.mem.find_base()
        if not self.base:
            raise RuntimeError("Mach-O base bulunamadı!")
        print(f"[+] Base: 0x{self.base:X}")

    def resolve_global_page(self) -> int:
        """GlobalPage pointer'ını çöz."""
        gp_addr = self.base + OFF_GLOBAL_PAGE
        gp = self.mem.read_u64(gp_addr)
        if not gp or gp < 0x100000000:
            raise RuntimeError(
                f"GlobalPage geçersiz: 0x{gp:X} (addr: 0x{gp_addr:X})"
            )
        print(f"[+] GlobalPage: 0x{gp:X}")
        return gp

    def resolve_hero_manager(self, global_page: int) -> int:
        """HeroManager pointer'ını çöz."""
        hm = self.mem.read_u64(global_page + OFF_HERO_MANAGER)
        if not hm or hm < 0x100000000:
            raise RuntimeError(f"HeroManager geçersiz: 0x{hm:X}")
        print(f"[+] HeroManager: 0x{hm:X}")
        return hm

    def resolve_heroes(self, hero_manager: int) -> list:
        """Hero array'ini çöz, her hero'nun adresini ve bilgilerini döndür."""
        hero_array_ptr = self.mem.read_u64(hero_manager + OFF_HERO_ARRAY)
        hero_count = self.mem.read_u32(hero_manager + OFF_HERO_COUNT)

        if not hero_array_ptr or hero_count < 1 or hero_count > 12:
            raise RuntimeError(
                f"Hero array geçersiz: ptr=0x{hero_array_ptr:X}, count={hero_count}"
            )

        print(f"[+] Hero Array: 0x{hero_array_ptr:X}, Count: {hero_count}")

        heroes = []
        for i in range(hero_count):
            hero_ptr = self.mem.read_u64(hero_array_ptr + i * 8)
            if not hero_ptr or hero_ptr < 0x100000000:
                continue

            # Champion adını oku
            name_ptr = self.mem.read_u64(hero_ptr + OFF_CHAMPION_NAME)
            name = ""
            if name_ptr and name_ptr > 0x100000000:
                name = self.mem.read_string(name_ptr, 32)

            # HP oku
            hp = self.mem.read_float(hero_ptr + OFF_HP)
            max_hp = self.mem.read_float(hero_ptr + OFF_MAX_HP)

            # Attack range oku (bilinen offset ile)
            atk_range = self.mem.read_float(hero_ptr + OFF_ATTACK_RANGE)

            # Team oku
            team = self.mem.read_u32(hero_ptr + OFF_TEAM)

            heroes.append({
                'index': i,
                'addr': hero_ptr,
                'name': name,
                'hp': hp,
                'max_hp': max_hp,
                'attack_range': atk_range,
                'team': team,
            })

        return heroes

    def resolve_all(self) -> list:
        """Tam pointer chain'i çöz ve tüm hero'ları döndür."""
        gp = self.resolve_global_page()
        hm = self.resolve_hero_manager(gp)
        heroes = self.resolve_heroes(hm)
        return heroes

    def get_local_player(self) -> dict:
        """İlk hero'yu (genelde local player) döndür."""
        heroes = self.resolve_all()
        if not heroes:
            raise RuntimeError("Hiç hero bulunamadı!")
        return heroes[0]

    def dump_hero(self, hero_addr: int, size: int = 0x4000) -> bytes:
        """Hero object'in ham memory dump'ını al."""
        data = self.mem.snapshot(hero_addr, size)
        if not data:
            raise RuntimeError(f"Hero dump başarısız: 0x{hero_addr:X}")
        return data


# ============================================================================
# CLI
# ============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  Hero Object Pointer Chain Resolver")
    print("=" * 60)

    resolver = HeroResolver()
    heroes = resolver.resolve_all()

    if not heroes:
        print("[-] Hiç hero bulunamadı!")
        sys.exit(1)

    print(f"\n[+] {len(heroes)} hero bulundu:\n")
    for h in heroes:
        range_valid = "✓" if int(round(h['attack_range'])) in KNOWN_ATTACK_RANGES else "?"
        print(
            f"  [{h['index']}] {h['name']:20s} "
            f"@ 0x{h['addr']:X}  "
            f"HP: {h['hp']:.0f}/{h['max_hp']:.0f}  "
            f"Range: {h['attack_range']:.1f} {range_valid}  "
            f"Team: {h['team']}"
        )

    # Attack range doğrulama
    print(f"\n[*] Attack Range Offset Doğrulama (0x{OFF_ATTACK_RANGE:X}):")
    for h in heroes:
        r = int(round(h['attack_range']))
        status = "✓ VALID" if r in KNOWN_ATTACK_RANGES else "✗ INVALID"
        print(f"  {h['name']}: {h['attack_range']:.1f} → {status}")
