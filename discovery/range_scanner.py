"""
Attack Range Offset Scanner
Hero object memory'sinde bilinen attack range değerlerini tarar.
Cross-validation ile birden fazla hero'da doğrular.

Yöntem 1: Known Value Scan
    - Miss Fortune = 550.0, Caitlyn = 650.0, Vayne = 550.0, vb.
    - Hero object dump'ında bu float değerleri ara
    - Birden fazla hero'da aynı offset'te geçerli değer = doğrulanmış

Yöntem 2: Cross-Validation
    - Tüm hero'larda aynı offset'teki float'u oku
    - Hepsi bilinen attack range setinde mi kontrol et

Kullanım:
    sudo python3 discovery/range_scanner.py
"""

import struct
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.mem import MemReader
from discovery.pointer_chain import HeroResolver

# ============================================================================
# Bilinen Değerler
# ============================================================================

# Tüm League of Legends champion attack range değerleri
KNOWN_ATTACK_RANGES = {
    125, 150, 175, 200, 225, 250, 300, 325,
    350, 425, 450, 475, 500, 525, 550, 575,
    600, 625, 650
}

# Champion → attack range mapping (doğrulama için)
CHAMPION_RANGES = {
    'MissFortune': 550.0,
    'Caitlyn': 650.0,
    'Vayne': 550.0,
    'Ashe': 600.0,
    'Jinx': 525.0,
    'Ezreal': 550.0,
    'Tristana': 525.0,  # level 1, büyür
    'Jhin': 550.0,
    'KaiSa': 525.0,
    'Lucian': 500.0,
    'Draven': 550.0,
    'Aphelios': 550.0,
    'Xayah': 525.0,
    'Sivir': 500.0,
    'Twitch': 550.0,
    'KogMaw': 500.0,
    'Varus': 575.0,
    'Kalista': 525.0,
    'Samira': 500.0,
    'Zeri': 525.0,
    'Nilah': 225.0,
    'Garen': 175.0,
    'Darius': 175.0,
    'Yasuo': 175.0,
    'Yone': 175.0,
    'Riven': 125.0,
    'Fiora': 150.0,
    'Camille': 125.0,
    'Irelia': 200.0,
    'Jax': 125.0,
    'Tryndamere': 175.0,
    'Aatrox': 175.0,
    'Sett': 125.0,
    'Mordekaiser': 175.0,
    'Urgot': 350.0,
    'Jayce': 500.0,  # ranged form
    'Teemo': 500.0,
    'Quinn': 525.0,
    'Kayle': 175.0,  # melee pre-6
    'Annie': 625.0,
    'Lux': 550.0,
    'Ahri': 550.0,
    'Syndra': 550.0,
    'Orianna': 525.0,
    'Viktor': 525.0,
    'Xerath': 525.0,
    'Ziggs': 550.0,
    'Velkoz': 525.0,
    'Thresh': 450.0,
}

# Miss Fortune attack range'inin float hex representation'ı
# 550.0 = 0x44098000
MF_RANGE_HEX = struct.pack('<f', 550.0)


# ============================================================================
# Scanner
# ============================================================================

class RangeScanner:
    """Attack range offset'ini memory taraması ile keşfeder."""

    def __init__(self, resolver: HeroResolver):
        self.resolver = resolver
        self.mem = resolver.mem
        self.results = []

    def scan_known_value(self, hero_addr: int, hero_name: str,
                         expected_range: float, scan_size: int = 0x4000) -> list:
        """
        Hero object'te bilinen attack range float değerini tara.
        Döndürür: [(offset, value), ...]
        """
        data = self.mem.snapshot(hero_addr, scan_size)
        if not data:
            print(f"[-] {hero_name}: memory okunamadı!")
            return []

        target_bytes = struct.pack('<f', expected_range)
        matches = []

        for off in range(0, len(data) - 3, 4):
            if data[off:off+4] == target_bytes:
                matches.append((off, expected_range))

        print(f"[*] {hero_name}: {expected_range:.0f} değeri {len(matches)} yerde bulundu")
        for off, val in matches:
            print(f"    offset=0x{off:04X}  addr=0x{hero_addr + off:X}")

        return matches

    def cross_validate(self, heroes: list, scan_range: tuple = (0xE00, 0x1100),
                       scan_size: int = 0x4000) -> list:
        """
        Birden fazla hero'da cross-validation ile attack range offset'ini bul.
        Kuki dumper'dan adapte edilmiş algoritma.
        """
        print(f"\n[*] Cross-Validation: {len(heroes)} hero, "
              f"scan range 0x{scan_range[0]:X}-0x{scan_range[1]:X}")

        # Her hero'nun memory dump'ını al
        hero_dumps = []
        for h in heroes:
            dump = self.mem.snapshot(h['addr'], scan_size)
            if dump and len(dump) >= scan_range[1]:
                hero_dumps.append((h, dump))
                print(f"  [{h['name']}] dump OK ({len(dump)} bytes)")
            else:
                print(f"  [{h['name']}] dump BAŞARISIZ, atlanıyor")

        if len(hero_dumps) < 1:
            print("[-] Yeterli hero dump yok!")
            return []

        # Her 4-byte aligned offset'i kontrol et
        validated = []
        for off in range(scan_range[0], min(scan_range[1], len(hero_dumps[0][1]) - 3), 4):
            # İlk hero'nun değerini oku
            val0 = struct.unpack_from('<f', hero_dumps[0][1], off)[0]

            # Bilinen attack range setinde mi?
            if int(round(val0)) not in KNOWN_ATTACK_RANGES:
                continue

            # Diğer hero'larda da geçerli mi?
            all_valid = True
            vals = [val0]
            for h, dump in hero_dumps[1:]:
                val_n = struct.unpack_from('<f', dump, off)[0]
                if int(round(val_n)) not in KNOWN_ATTACK_RANGES:
                    all_valid = False
                    break
                vals.append(val_n)

            if all_valid:
                # Champion adı biliniyor ise expected range ile karşılaştır
                name_match = True
                for i, (h, _) in enumerate(hero_dumps):
                    if h['name'] in CHAMPION_RANGES:
                        expected = CHAMPION_RANGES[h['name']]
                        if abs(vals[i] - expected) > 1.0:
                            name_match = False
                            break

                confidence = 'HIGH' if name_match and len(hero_dumps) >= 2 else 'MEDIUM'

                validated.append({
                    'offset': off,
                    'values': vals,
                    'heroes': [h['name'] for h, _ in hero_dumps],
                    'confidence': confidence,
                    'name_match': name_match,
                })
                print(
                    f"  ✓ offset=0x{off:04X}  "
                    f"values={[f'{v:.0f}' for v in vals]}  "
                    f"confidence={confidence}"
                )

        return validated

    def scan_float_range(self, hero_addr: int, scan_range: tuple = (0xE00, 0x1100),
                         scan_size: int = 0x4000) -> list:
        """
        Hero object'te belirli aralıktaki tüm float'ları listele.
        Attack range'e benzeyen değerleri işaretle.
        """
        data = self.mem.snapshot(hero_addr, scan_size)
        if not data:
            return []

        floats = []
        for off in range(scan_range[0], min(scan_range[1], len(data) - 3), 4):
            val = struct.unpack_from('<f', data, off)[0]
            is_range = int(round(val)) in KNOWN_ATTACK_RANGES
            if 50.0 < val < 1000.0:  # makul stat değerleri
                floats.append({
                    'offset': off,
                    'value': val,
                    'is_known_range': is_range,
                    'hex': data[off:off+4].hex(),
                })

        return floats


# ============================================================================
# Ana Tarama
# ============================================================================

def run_range_scan():
    """Tam attack range offset taraması çalıştır."""
    print("=" * 60)
    print("  Attack Range Offset Scanner")
    print("=" * 60)

    resolver = HeroResolver()
    heroes = resolver.resolve_all()

    if not heroes:
        print("[-] Hero bulunamadı!")
        return

    scanner = RangeScanner(resolver)
    player = heroes[0]

    # ========================================================================
    # Yöntem 1: Known Value Scan
    # ========================================================================
    print("\n" + "-" * 60)
    print("  YÖNTEM 1: Known Value Scan")
    print("-" * 60)

    expected = CHAMPION_RANGES.get(player['name'])
    if expected:
        print(f"[+] {player['name']} bilinen range: {expected:.0f}")
        matches = scanner.scan_known_value(
            player['addr'], player['name'], expected
        )
    else:
        print(f"[!] {player['name']} bilinen range tablosunda yok")
        print("[*] Tüm bilinen range değerlerini tarıyorum...")
        matches = []
        for rng in sorted(KNOWN_ATTACK_RANGES):
            m = scanner.scan_known_value(
                player['addr'], player['name'], float(rng)
            )
            matches.extend(m)

    # ========================================================================
    # Yöntem 2: Cross-Validation
    # ========================================================================
    print("\n" + "-" * 60)
    print("  YÖNTEM 2: Cross-Validation")
    print("-" * 60)

    validated = scanner.cross_validate(heroes)

    # ========================================================================
    # Yöntem 3: Float Range Dump (0xE00-0x1100 arası)
    # ========================================================================
    print("\n" + "-" * 60)
    print("  YÖNTEM 3: Float Dump (stat bölgesi)")
    print("-" * 60)

    stat_floats = scanner.scan_float_range(player['addr'])
    range_candidates = [f for f in stat_floats if f['is_known_range']]
    print(f"[*] {len(stat_floats)} float değer bulundu (50-1000 arası)")
    print(f"[*] {len(range_candidates)} tanesi bilinen attack range değeri:")
    for f in range_candidates:
        print(
            f"    offset=0x{f['offset']:04X}  "
            f"value={f['value']:.1f}  "
            f"hex={f['hex']}"
        )

    # ========================================================================
    # Sonuç
    # ========================================================================
    print("\n" + "=" * 60)
    print("  SONUÇ")
    print("=" * 60)

    # En iyi aday: cross-validation HIGH confidence
    best = None
    for v in validated:
        if v['confidence'] == 'HIGH':
            best = v
            break
    if not best and validated:
        best = validated[0]

    if best:
        print(f"\n  ✓ ATTACK RANGE OFFSET: 0x{best['offset']:X}")
        print(f"    Değerler: {best['values']}")
        print(f"    Hero'lar: {best['heroes']}")
        print(f"    Güven: {best['confidence']}")
        print(f"    İsim eşleşmesi: {best['name_match']}")

        # Kuki referans ile karşılaştır
        KUKI_OFFSET = 0xF54
        if best['offset'] == KUKI_OFFSET:
            print(f"\n  ✓✓ Kuki referans offset (0x{KUKI_OFFSET:X}) ile EŞLEŞME!")
        else:
            print(f"\n  ✗ Kuki referans offset 0x{KUKI_OFFSET:X} ile eşleşmiyor!")
            print(f"    Fark: {best['offset'] - KUKI_OFFSET} byte")
    else:
        print("\n  ✗ Cross-validation ile offset bulunamadı")
        print("  Known value scan sonuçlarını kontrol edin")

    # JSON export
    results = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'heroes': [
            {
                'name': h['name'],
                'addr': f"0x{h['addr']:X}",
                'attack_range': h['attack_range'],
            }
            for h in heroes
        ],
        'known_value_matches': [
            {'offset': f"0x{off:04X}", 'value': val}
            for off, val in (matches if matches else [])
        ],
        'cross_validated': [
            {
                'offset': f"0x{v['offset']:04X}",
                'values': v['values'],
                'heroes': v['heroes'],
                'confidence': v['confidence'],
            }
            for v in validated
        ],
        'best_offset': f"0x{best['offset']:X}" if best else None,
        'kuki_reference': '0xF54',
        'match_kuki': best['offset'] == 0xF54 if best else False,
    }

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'offsets', 'range_scan_results.json'
    )
    with open(out_path, 'w') as fp:
        json.dump(results, fp, indent=2)
    print(f"\n[+] Sonuçlar kaydedildi: {out_path}")

    # known_offsets.json güncelle
    offsets_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'offsets', 'known_offsets.json'
    )
    offsets = {}
    if os.path.exists(offsets_path):
        with open(offsets_path) as fp:
            offsets = json.load(fp)

    if best:
        offsets['AttackRange'] = {
            'offset': f"0x{best['offset']:X}",
            'offset_int': best['offset'],
            'type': 'float',
            'verified': best['confidence'] == 'HIGH',
            'values_seen': best['values'],
            'heroes_tested': best['heroes'],
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(offsets_path, 'w') as fp:
            json.dump(offsets, fp, indent=2)
        print(f"[+] Offset kaydedildi: {offsets_path}")


# ============================================================================
# CLI
# ============================================================================

if __name__ == '__main__':
    run_range_scan()
