"""
Memory Snapshot Diff Tool — C-Key Toggle Analizi
Hero object belleğini snapshot alıp, C tuşu toggle öncesi/sonrası
byte-level diff yaparak değişen offset'leri tespit eder.

C tuşu attack range indicator'ı (mavi halka) toggle eder.
Bu tool, rendering flag'ini ve ilişkili memory değişikliklerini bulur.

Kullanım:
    sudo python3 discovery/snapshot_diff.py

Adımlar:
    1. League of Legends Practice Tool'da ol
    2. Script çalıştır
    3. C tuşuna BASMA — script ilk snapshot'ı alır
    4. Script söylediğinde C tuşuna bas
    5. Script ikinci snapshot'ı alır ve diff yapar
"""

import struct
import time
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.mem import MemReader
from discovery.pointer_chain import HeroResolver, OFF_ATTACK_RANGE

# ============================================================================
# Snapshot Diff Engine
# ============================================================================

class SnapshotDiff:
    """İki memory snapshot arasındaki farkları analiz eder."""

    def __init__(self, snap_before: bytes, snap_after: bytes, base_addr: int):
        self.before = snap_before
        self.after = snap_after
        self.base_addr = base_addr
        self.size = min(len(snap_before), len(snap_after))

    def diff_bytes(self) -> list:
        """Byte-level diff: değişen her byte'ın offset'ini döndür."""
        changes = []
        for i in range(self.size):
            if self.before[i] != self.after[i]:
                changes.append({
                    'offset': i,
                    'addr': self.base_addr + i,
                    'before': self.before[i],
                    'after': self.after[i],
                })
        return changes

    def diff_floats(self) -> list:
        """Float-level diff: 4-byte aligned float değişimlerini döndür."""
        changes = []
        for off in range(0, self.size - 3, 4):
            b_val = struct.unpack_from('<f', self.before, off)[0]
            a_val = struct.unpack_from('<f', self.after, off)[0]
            if b_val != a_val:
                changes.append({
                    'offset': off,
                    'addr': self.base_addr + off,
                    'before': b_val,
                    'after': a_val,
                    'delta': a_val - b_val,
                })
        return changes

    def diff_u32(self) -> list:
        """uint32-level diff."""
        changes = []
        for off in range(0, self.size - 3, 4):
            b_val = struct.unpack_from('<I', self.before, off)[0]
            a_val = struct.unpack_from('<I', self.after, off)[0]
            if b_val != a_val:
                changes.append({
                    'offset': off,
                    'addr': self.base_addr + off,
                    'before': b_val,
                    'after': a_val,
                })
        return changes

    def diff_bools(self) -> list:
        """Boolean-like toggle'ları bul (0↔1, 0↔non-zero, true↔false)."""
        toggles = []
        for i in range(self.size):
            b, a = self.before[i], self.after[i]
            if b != a:
                is_toggle = (
                    (b == 0 and a == 1) or
                    (b == 1 and a == 0) or
                    (b == 0 and a != 0) or
                    (b != 0 and a == 0)
                )
                if is_toggle:
                    toggles.append({
                        'offset': i,
                        'addr': self.base_addr + i,
                        'before': b,
                        'after': a,
                        'type': 'bool_toggle',
                    })
        return toggles


# ============================================================================
# Geniş Bölge Tarama (hero object dışındaki alanlar)
# ============================================================================

def scan_wide_regions(mem: MemReader, hero_addr: int, snap_size: int = 0x4000):
    """
    Hero object etrafında ve ilişkili pointer'lardaki bölgeleri tara.
    Döndürülen dict: {bölge_adı: (addr, size)}
    """
    regions = {
        'hero_object': (hero_addr, snap_size),
    }

    # Hero object'in ilk 0x100 byte'ında pointer olabilecek alanları tara
    # Bu pointer'lar rendering struct'larına işaret edebilir
    for off in range(0, 0x100, 8):
        ptr = mem.read_u64(hero_addr + off)
        if ptr and 0x100000000 < ptr < 0x800000000000:
            region_name = f'ptr_at_0x{off:X}'
            regions[region_name] = (ptr, 0x200)

    return regions


# ============================================================================
# Ana Diff Aracı
# ============================================================================

def run_toggle_diff():
    """C-key toggle diff analizi çalıştır."""
    print("=" * 60)
    print("  Attack Range Indicator — C-Key Toggle Diff")
    print("=" * 60)

    # Hero'ları bul
    resolver = HeroResolver()
    heroes = resolver.resolve_all()
    if not heroes:
        print("[-] Hero bulunamadı!")
        return

    player = heroes[0]
    hero_addr = player['addr']
    print(f"\n[+] Local Player: {player['name']} @ 0x{hero_addr:X}")
    print(f"[+] Attack Range: {player['attack_range']:.1f}")

    mem = resolver.mem
    snap_size = 0x4000  # 16KB hero object dump

    # ========================================================================
    # Snapshot 1: C tuşu AÇIK (range indicator görünür)
    # ========================================================================
    print("\n" + "=" * 60)
    print("  ADIM 1: Range indicator AÇIK olmalı (C tuşu ile)")
    print("  Hazır olduğunda ENTER'a bas...")
    print("=" * 60)
    input()

    print("[*] Snapshot 1 alınıyor (C = AÇIK)...")
    snap1_hero = mem.snapshot(hero_addr, snap_size)
    if not snap1_hero:
        print("[-] Snapshot 1 başarısız!")
        return
    print(f"[+] Snapshot 1: {len(snap1_hero)} bytes")

    # Attack range doğrulama
    ar1 = struct.unpack_from('<f', snap1_hero, OFF_ATTACK_RANGE)[0]
    print(f"[+] Attack Range (snap1): {ar1:.1f}")

    # ========================================================================
    # Snapshot 2: C tuşu KAPALI (range indicator gizli)
    # ========================================================================
    print("\n" + "=" * 60)
    print("  ADIM 2: Şimdi C tuşuna bas (range indicator KAPANSIN)")
    print("  Bastıktan sonra ENTER'a bas...")
    print("=" * 60)
    input()

    print("[*] Snapshot 2 alınıyor (C = KAPALI)...")
    snap2_hero = mem.snapshot(hero_addr, snap_size)
    if not snap2_hero:
        print("[-] Snapshot 2 başarısız!")
        return
    print(f"[+] Snapshot 2: {len(snap2_hero)} bytes")

    ar2 = struct.unpack_from('<f', snap2_hero, OFF_ATTACK_RANGE)[0]
    print(f"[+] Attack Range (snap2): {ar2:.1f}")

    # ========================================================================
    # Diff Analizi
    # ========================================================================
    print("\n" + "=" * 60)
    print("  DIFF ANALİZİ")
    print("=" * 60)

    diff = SnapshotDiff(snap1_hero, snap2_hero, hero_addr)

    # 1. Byte-level değişimler
    byte_changes = diff.diff_bytes()
    print(f"\n[*] Byte-level değişim sayısı: {len(byte_changes)}")

    # 2. Boolean toggle'lar (C-key flag muhtemelen burada)
    toggles = diff.diff_bools()
    print(f"[*] Boolean toggle sayısı: {len(toggles)}")
    if toggles:
        print("\n  Boolean Toggle'lar (muhtemel C-key flag):")
        for t in toggles[:20]:  # İlk 20
            print(
                f"    offset=0x{t['offset']:04X}  "
                f"addr=0x{t['addr']:X}  "
                f"{t['before']} → {t['after']}"
            )

    # 3. Float değişimler
    float_changes = diff.diff_floats()
    print(f"\n[*] Float değişim sayısı: {len(float_changes)}")
    if float_changes:
        print("\n  Float Değişimler:")
        for f in float_changes[:20]:
            print(
                f"    offset=0x{f['offset']:04X}  "
                f"addr=0x{f['addr']:X}  "
                f"{f['before']:.4f} → {f['after']:.4f}  "
                f"(delta: {f['delta']:.4f})"
            )

    # 4. Attack range değişti mi?
    print(f"\n[*] Attack Range Karşılaştırma:")
    print(f"    Snap1 (C=AÇIK):  {ar1:.1f}")
    print(f"    Snap2 (C=KAPALI): {ar2:.1f}")
    if ar1 == ar2:
        print("    → Attack range değeri DEĞİŞMEDİ (beklenen davranış)")
        print("    → C tuşu sadece rendering toggle'ı, değer sabit")
    else:
        print(f"    → Attack range DEĞİŞTİ! Delta: {ar2 - ar1:.1f}")

    # ========================================================================
    # Ters Diff (doğrulama: C tuşu geri bas)
    # ========================================================================
    print("\n" + "=" * 60)
    print("  ADIM 3 (opsiyonel): C tuşuna tekrar bas (geri aç)")
    print("  Doğrulama için ENTER'a bas, atlamak için 's' yaz...")
    print("=" * 60)
    skip = input().strip().lower()

    if skip != 's':
        print("[*] Snapshot 3 alınıyor (C = tekrar AÇIK)...")
        snap3_hero = mem.snapshot(hero_addr, snap_size)
        if snap3_hero:
            diff_reverse = SnapshotDiff(snap2_hero, snap3_hero, hero_addr)
            reverse_toggles = diff_reverse.diff_bools()

            # Her iki diff'te de toggle olan offset'ler = kesin C-key flag
            toggle_offsets_1 = {t['offset'] for t in toggles}
            toggle_offsets_r = {t['offset'] for t in reverse_toggles}
            confirmed = toggle_offsets_1 & toggle_offsets_r

            print(f"\n[+] Doğrulanmış C-Key Toggle Offset'ler ({len(confirmed)}):")
            for off in sorted(confirmed):
                val1 = snap1_hero[off]
                val2 = snap2_hero[off]
                val3 = snap3_hero[off]
                print(
                    f"    offset=0x{off:04X}  "
                    f"addr=0x{hero_addr + off:X}  "
                    f"pattern: {val1} → {val2} → {val3}"
                )

    # ========================================================================
    # Sonuç Raporu
    # ========================================================================
    print("\n" + "=" * 60)
    print("  SONUÇ")
    print("=" * 60)
    print(f"  Hero: {player['name']} @ 0x{hero_addr:X}")
    print(f"  Attack Range: {ar1:.1f} (offset 0x{OFF_ATTACK_RANGE:X})")
    print(f"  Toplam byte değişim: {len(byte_changes)}")
    print(f"  Boolean toggle: {len(toggles)}")
    print(f"  Float değişim: {len(float_changes)}")

    # JSON export
    results = {
        'hero': player['name'],
        'hero_addr': f"0x{hero_addr:X}",
        'attack_range': ar1,
        'attack_range_offset': f"0x{OFF_ATTACK_RANGE:X}",
        'byte_changes': len(byte_changes),
        'toggle_candidates': [
            {'offset': f"0x{t['offset']:04X}", 'before': t['before'], 'after': t['after']}
            for t in toggles[:50]
        ],
        'float_changes': [
            {
                'offset': f"0x{f['offset']:04X}",
                'before': round(f['before'], 4),
                'after': round(f['after'], 4),
            }
            for f in float_changes[:50]
        ],
    }

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'offsets', 'toggle_diff_results.json'
    )
    with open(out_path, 'w') as fp:
        json.dump(results, fp, indent=2)
    print(f"\n[+] Sonuçlar kaydedildi: {out_path}")


# ============================================================================
# CLI
# ============================================================================

if __name__ == '__main__':
    run_toggle_diff()
