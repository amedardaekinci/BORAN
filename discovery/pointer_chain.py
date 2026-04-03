"""
Hero Object Pointer Chain Resolver
League of Legends macOS ARM64 — hero object base adreslerini bulur.

Dinamik keşif: ARM64 ADRP instruction scanning ile GlobalPage'i bulur,
ardından HeroManager struct signature ile doğrular. Patch-agnostic.

Pointer Chain:
    base (Mach-O FEEDFACF)
    → base + GP_OFFSET = GlobalPage  (ADRP scan ile bulunur)
    → GP + HM_OFFSET = HeroManager   (struct validation ile bulunur)
    → HeroMgr + 0x8 = hero_array
    → HeroMgr + 0x10 = hero_count
    → hero_array + i*8 = hero[i]

Kullanım:
    sudo python3 discovery/pointer_chain.py
"""

import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.mem import MemReader, VM_PROT_EXECUTE, VM_PROT_READ

# ============================================================================
# Hero object field offset'leri (doğrulama için)
# ============================================================================

OFF_CHAMPION_NAME = 0x3758        # hero + bu = champion name pointer
OFF_HP = 0x3704                   # hero + bu = current HP (float)
OFF_MAX_HP = 0x3708               # hero + bu = max HP (float)
OFF_ATTACK_RANGE = 0xF54          # hero + bu = attack range (float)
OFF_TEAM = 0x3C                   # hero + bu = team ID

KNOWN_ATTACK_RANGES = {
    125, 150, 175, 200, 225, 250, 300, 325,
    350, 425, 450, 475, 500, 525, 550, 575,
    600, 625, 650
}


# ============================================================================
# ARM64 Instruction Decoder
# ============================================================================

class ARM64:
    """ARM64 instruction decoder — ADRP/ADD/LDR çözümleme."""

    @staticmethod
    def is_adrp(insn: int) -> bool:
        return (insn & 0x9F000000) == 0x90000000

    @staticmethod
    def is_add_imm(insn: int) -> bool:
        return (insn & 0x7F800000) == 0x11000000

    @staticmethod
    def is_ldr_imm_unsigned(insn: int) -> bool:
        return (insn & 0x3B400000) == 0x39400000

    @staticmethod
    def decode_adrp(insn: int, pc: int) -> int:
        """ADRP Xd, #imm → target = (PC & ~0xFFF) + (imm << 12)"""
        immhi = (insn >> 5) & 0x7FFFF
        immlo = (insn >> 29) & 0x3
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= (1 << 21)
        return (pc & ~0xFFF) + (imm << 12)

    @staticmethod
    def decode_add_imm(insn: int) -> int:
        imm12 = (insn >> 10) & 0xFFF
        shift = (insn >> 22) & 0x3
        if shift == 1:
            imm12 <<= 12
        return imm12

    @staticmethod
    def decode_ldr_offset(insn: int) -> int:
        imm12 = (insn >> 10) & 0xFFF
        size = (insn >> 30) & 0x3
        return imm12 << size

    @staticmethod
    def get_rd(insn: int) -> int:
        return insn & 0x1F

    @staticmethod
    def get_rn(insn: int) -> int:
        return (insn >> 5) & 0x1F


# ============================================================================
# GlobalPage Dinamik Keşif
# ============================================================================

class GlobalPageFinder:
    """
    ADRP instruction scanning ile GlobalPage'i dinamik olarak bulur.
    Patch-agnostic: hardcoded offset gerektirmez.
    """

    def __init__(self, mem: MemReader, base: int):
        self.mem = mem
        self.base = base
        self.text_regions = []
        self._cache_executable_regions()

    def _cache_executable_regions(self):
        """Executable region'ları cache'le (ADRP tarama için)."""
        total = 0
        for r in self.mem.get_regions():
            if not (r['prot'] & VM_PROT_EXECUTE):
                continue
            if r['size'] > 64 * 1024 * 1024:
                continue
            data = self.mem.read(r['start'], r['size'])
            if data:
                self.text_regions.append((r['start'], data))
                total += len(data)
        print(f"[*] {len(self.text_regions)} executable region cache'lendi "
              f"({total / 1024 / 1024:.1f} MB)")

    def build_adrp_target_map(self) -> dict:
        """
        Tüm ADRP+ADD/LDR instruction pair'lerini tara.
        Döndürür: {target_addr: [xref_pc, ...]}
        """
        targets = {}
        for region_addr, region_data in self.text_regions:
            data_len = len(region_data)
            for i in range(0, data_len - 8, 4):
                insn1 = struct.unpack_from('<I', region_data, i)[0]
                if not ARM64.is_adrp(insn1):
                    continue
                pc = region_addr + i
                page = ARM64.decode_adrp(insn1, pc)
                rd = ARM64.get_rd(insn1)

                insn2 = struct.unpack_from('<I', region_data, i + 4)[0]
                target = None

                if ARM64.is_add_imm(insn2) and ARM64.get_rn(insn2) == rd:
                    target = page + ARM64.decode_add_imm(insn2)
                elif ARM64.is_ldr_imm_unsigned(insn2) and ARM64.get_rn(insn2) == rd:
                    target = page + ARM64.decode_ldr_offset(insn2)

                if target and target > self.base:
                    if target not in targets:
                        targets[target] = []
                    targets[target].append(pc)
        return targets

    def find_global_page(self) -> tuple:
        """
        GlobalPage ve HeroManager offset'ini dinamik olarak bul.
        Döndürür: (global_page_addr, hero_manager_offset)
        """
        print("[*] ADRP target map oluşturuluyor (tüm executable code taranıyor)...")
        adrp_targets = self.build_adrp_target_map()
        print(f"[*] {len(adrp_targets)} unique ADRP+ADD/LDR target bulundu")

        # Data segment'teki en çok referans alan page'leri bul
        page_candidates = {}
        for target, xref_list in adrp_targets.items():
            offset_from_base = target - self.base
            if 0x1000000 < offset_from_base < 0x5000000:
                page = target & ~0xFFF
                if page not in page_candidates:
                    page_candidates[page] = 0
                page_candidates[page] += len(xref_list)

        if not page_candidates:
            raise RuntimeError("GlobalPage adayı bulunamadı! ADRP tarama başarısız.")

        sorted_pages = sorted(page_candidates.items(), key=lambda x: -x[1])
        print(f"\n[*] GlobalPage adayları (top 10):")
        for pg, refs in sorted_pages[:10]:
            print(f"    0x{pg:X} (base+0x{pg - self.base:X}): {refs} refs")

        # Her aday page'de HeroManager struct'ını ara
        for page_addr, refs in sorted_pages[:10]:
            result = self._validate_hero_manager(page_addr)
            if result:
                hm_offset, count, hp = result
                gp_offset = page_addr - self.base
                print(f"\n[+] GlobalPage BULUNDU: 0x{page_addr:X} "
                      f"(base+0x{gp_offset:X})")
                print(f"[+] HeroManager offset: +0x{hm_offset:X} "
                      f"(count={count}, hero0 HP={hp:.0f})")
                return page_addr, hm_offset

        # Fallback: en çok referans alan, 20+ valid pointer içeren page
        print("\n[!] HeroManager validation başarısız, fallback deneniyor...")
        for pg, refs in sorted_pages[:5]:
            valid = sum(
                1 for o in range(0, 0x1000, 8)
                if 0x100000000 < self.mem.read_u64(pg + o) < 0x800000000000
            )
            if valid > 20:
                print(f"[+] Fallback GlobalPage: 0x{pg:X} ({valid} valid ptr)")
                return pg, None

        raise RuntimeError("GlobalPage bulunamadı!")

    def _validate_hero_manager(self, page_addr: int):
        """
        Verilen page'de HeroManager struct'ını ara.
        HeroManager pattern:
            page + offset → mgr_ptr
            mgr_ptr + 0x8 → hero_array (valid ptr)
            mgr_ptr + 0x10 → hero_count (1-12)
            hero_array[0] → hero0 (valid ptr)
            hero0 + 0x3704 → HP (0-50000)

        Döndürür: (offset, count, hp) veya None
        """
        for off in range(0x400, 0x600, 8):
            mgr_ptr = self.mem.read_u64(page_addr + off)
            if not (0x100000000 < mgr_ptr < 0x800000000000):
                continue

            arr_ptr = self.mem.read_u64(mgr_ptr + 0x8)
            count = self.mem.read_u32(mgr_ptr + 0x10)

            if not (0x100000000 < arr_ptr < 0x800000000000):
                continue
            if not (1 <= count <= 12):
                continue

            hero0 = self.mem.read_u64(arr_ptr)
            if not (0x100000000 < hero0 < 0x800000000000):
                continue

            hp = self.mem.read_float(hero0 + OFF_HP)
            if 0 < hp < 50000:
                return off, count, hp

        return None


# ============================================================================
# HeroResolver
# ============================================================================

class HeroResolver:
    """League of Legends hero object'lerini memory'den çözer."""

    def __init__(self, process_name: str = 'LeagueofLegends'):
        self.mem = MemReader.from_process_name(process_name)
        self.base = self.mem.find_base()
        if not self.base:
            raise RuntimeError("Mach-O base bulunamadı!")
        print(f"[+] Base: 0x{self.base:X}")

        # GlobalPage ve HeroManager'ı dinamik bul
        finder = GlobalPageFinder(self.mem, self.base)
        self.global_page, self.hm_offset = finder.find_global_page()

    def resolve_hero_manager(self) -> int:
        """HeroManager pointer'ını çöz."""
        if self.hm_offset is None:
            raise RuntimeError("HeroManager offset bulunamadı!")
        hm = self.mem.read_u64(self.global_page + self.hm_offset)
        if not hm or hm < 0x100000000:
            raise RuntimeError(f"HeroManager geçersiz: 0x{hm:X}")
        print(f"[+] HeroManager: 0x{hm:X}")
        return hm

    def resolve_heroes(self, hero_manager: int) -> list:
        """Hero array'ini çöz, her hero'nun adresini ve bilgilerini döndür."""
        hero_array_ptr = self.mem.read_u64(hero_manager + 0x8)
        hero_count = self.mem.read_u32(hero_manager + 0x10)

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

            # Attack range oku
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
        hm = self.resolve_hero_manager()
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
    print("  (Dinamik ADRP Scanning + HeroManager Validation)")
    print("=" * 60)

    resolver = HeroResolver()
    heroes = resolver.resolve_all()

    if not heroes:
        print("[-] Hiç hero bulunamadı!")
        sys.exit(1)

    print(f"\n[+] {len(heroes)} hero bulundu:\n")
    for h in heroes:
        range_valid = "?" if h['attack_range'] == 0 else (
            "✓" if int(round(h['attack_range'])) in KNOWN_ATTACK_RANGES else "?"
        )
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
