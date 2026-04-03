"""
Hero Object Memory Dumper — Diagnostik Araç
Hero object'in ham memory'sini tarayarak doğru offset'leri bulur.

Kullanım:
    sudo python3 discovery/hero_dumper.py
"""

import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.pointer_chain import HeroResolver, OFF_ATTACK_RANGE

# Miss Fortune level 1 base stats (yaklaşık)
MF_BASE_HP = 640.0       # ±50
MF_BASE_MANA = 300.0     # ±50
MF_BASE_AD = 52.0        # ±10
MF_BASE_ARMOR = 28.0     # ±10
MF_BASE_MR = 30.0        # ±10
MF_BASE_MS = 325.0       # ±10
MF_ATTACK_RANGE = 550.0


def dump_float_scan(mem, hero_addr, scan_size=0x5000):
    """Hero object'te bilinen stat değerlerini tara."""
    data = mem.snapshot(hero_addr, scan_size)
    if not data:
        print("[-] Memory okunamadı!")
        return

    print(f"\n[*] Hero @ 0x{hero_addr:X} — {len(data)} bytes okundu")

    # 1. Attack Range (550.0) — zaten biliniyor, doğrulama
    print(f"\n{'='*60}")
    print(f"  ATTACK RANGE (550.0) Tarama")
    print(f"{'='*60}")
    target_550 = struct.pack('<f', 550.0)
    for off in range(0, len(data) - 3, 4):
        if data[off:off+4] == target_550:
            print(f"  ✓ 550.0 @ offset 0x{off:04X}")

    # 2. HP aralığı (500-700 MF level 1)
    print(f"\n{'='*60}")
    print(f"  HP ADAYLARI (500-700 float)")
    print(f"{'='*60}")
    for off in range(0, len(data) - 3, 4):
        val = struct.unpack_from('<f', data, off)[0]
        if 500.0 < val < 700.0:
            # Yanındaki değer de HP-benzeri mi? (MaxHP = HP yanında)
            if off + 4 < len(data):
                next_val = struct.unpack_from('<f', data, off + 4)[0]
                marker = " ← HP+MaxHP?" if 500.0 < next_val < 700.0 else ""
            else:
                marker = ""
            print(f"  0x{off:04X}: {val:.1f}{marker}")

    # 3. Mana aralığı (250-350 MF level 1)
    print(f"\n{'='*60}")
    print(f"  MANA ADAYLARI (250-400 float)")
    print(f"{'='*60}")
    count = 0
    for off in range(0, len(data) - 3, 4):
        val = struct.unpack_from('<f', data, off)[0]
        if 250.0 < val < 400.0:
            count += 1
            if count <= 30:
                print(f"  0x{off:04X}: {val:.1f}")
    if count > 30:
        print(f"  ... ve {count - 30} tane daha")

    # 4. AD (50-60)
    print(f"\n{'='*60}")
    print(f"  AD ADAYLARI (50-60 float)")
    print(f"{'='*60}")
    count = 0
    for off in range(0, len(data) - 3, 4):
        val = struct.unpack_from('<f', data, off)[0]
        if 50.0 < val < 60.0:
            count += 1
            if count <= 20:
                print(f"  0x{off:04X}: {val:.1f}")
    if count > 20:
        print(f"  ... ve {count - 20} tane daha")

    # 5. MoveSpeed (325.0 MF)
    print(f"\n{'='*60}")
    print(f"  MOVESPEED (325.0) Tarama")
    print(f"{'='*60}")
    target_325 = struct.pack('<f', 325.0)
    for off in range(0, len(data) - 3, 4):
        if data[off:off+4] == target_325:
            print(f"  ✓ 325.0 @ offset 0x{off:04X}")

    # 6. Armor (28.0 ±5)
    print(f"\n{'='*60}")
    print(f"  ARMOR ADAYLARI (23-33 float)")
    print(f"{'='*60}")
    count = 0
    for off in range(0, len(data) - 3, 4):
        val = struct.unpack_from('<f', data, off)[0]
        if 23.0 < val < 33.0:
            count += 1
            if count <= 20:
                print(f"  0x{off:04X}: {val:.1f}")
    if count > 20:
        print(f"  ... ve {count - 20} tane daha")

    # 7. Champion name string "MissFortune" arama
    print(f"\n{'='*60}")
    print(f"  CHAMPION NAME STRING Tarama")
    print(f"{'='*60}")
    # Direkt string arama
    for name in [b'MissFortune', b'missFortune', b'Miss', b'Fortune']:
        idx = data.find(name)
        while idx >= 0:
            print(f"  '{name.decode()}' bulundu @ offset 0x{idx:04X}")
            idx = data.find(name, idx + 1)

    # Pointer-through string arama (name bir pointer olabilir)
    print(f"\n  Pointer-through name tarama (0x3700-0x3800):")
    for off in range(0x3700, min(0x3800, len(data) - 7), 8):
        ptr = struct.unpack_from('<Q', data, off)[0]
        if 0x100000000 < ptr < 0x800000000000:
            s = mem.read_string(ptr, 32)
            if s and len(s) > 2 and s.isprintable():
                print(f"  0x{off:04X} → 0x{ptr:X} → \"{s}\"")

    # Geniş pointer-string tarama (her 8 byte'ta pointer + string dene)
    print(f"\n  Geniş pointer-string tarama (name içerenler):")
    for off in range(0, min(len(data) - 7, 0x4000), 8):
        ptr = struct.unpack_from('<Q', data, off)[0]
        if 0x100000000 < ptr < 0x800000000000:
            s = mem.read_string(ptr, 32)
            if s and ('Fortune' in s or 'Miss' in s or 'miss' in s):
                print(f"  0x{off:04X} → 0x{ptr:X} → \"{s}\"")

    # 8. 0x3700 civarı raw hex dump (HP bölgesi)
    print(f"\n{'='*60}")
    print(f"  RAW HEX DUMP: 0x3700-0x3720")
    print(f"{'='*60}")
    for off in range(0x3700, min(0x3720, len(data) - 3), 4):
        raw = data[off:off+4]
        as_float = struct.unpack_from('<f', raw, 0)[0]
        as_u32 = struct.unpack_from('<I', raw, 0)[0]
        print(f"  0x{off:04X}: {raw.hex()}  float={as_float:.4f}  u32={as_u32}")

    # 9. Team değeri tarama (100, 200 = order/chaos)
    print(f"\n{'='*60}")
    print(f"  TEAM ADAYLARI (100 veya 200 u32)")
    print(f"{'='*60}")
    for off in range(0, min(len(data) - 3, 0x100), 4):
        val = struct.unpack_from('<I', data, off)[0]
        if val in (100, 200):
            print(f"  0x{off:04X}: {val} ({'ORDER' if val == 100 else 'CHAOS'})")


if __name__ == '__main__':
    print("=" * 60)
    print("  Hero Object Memory Dumper — Diagnostik")
    print("=" * 60)

    resolver = HeroResolver()
    heroes = resolver.resolve_all()

    if not heroes:
        print("[-] Hero bulunamadı!")
        sys.exit(1)

    # İlk hero'yu dump et
    hero = heroes[0]
    print(f"\n[+] Hero[0] @ 0x{hero['addr']:X}")
    dump_float_scan(resolver.mem, hero['addr'])
