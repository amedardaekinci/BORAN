"""
Camera / ViewProj Matrix Offset Finder
GlobalPage etrafında ViewProjection matrix pointer'ını bulur.

ViewProj matrix özellikleri:
- 4x4 float (64 byte)
- Değerler genelde -10 ile +10 arası
- Hiçbir satır/sütun tamamen sıfır değil
- Determinant != 0

Kullanım:
    sudo python3 discovery/find_camera.py
"""

import struct
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.pointer_chain import HeroResolver

# Kuki'nin bilinen camera offset'i (referans)
KUKI_CAMERA_OFF = 0xDC8
KUKI_VIEWPROJ_OFF = 0xEC


def is_valid_viewproj(data_16f):
    """ViewProj matrix gibi görünüyor mu?"""
    # Hiçbir değer NaN/Inf olmamalı
    for v in data_16f:
        if math.isnan(v) or math.isinf(v):
            return False

    # Tamamen sıfır olmamalı
    if all(abs(v) < 1e-10 for v in data_16f):
        return False

    # Değerler makul aralıkta olmalı (çoğu -50 ile +50 arası)
    extreme = sum(1 for v in data_16f if abs(v) > 100)
    if extreme > 4:
        return False

    # En az 8 non-zero değer olmalı
    nonzero = sum(1 for v in data_16f if abs(v) > 1e-6)
    if nonzero < 8:
        return False

    # Perspektif matrixi: son satır genelde (0, 0, ~1, 0) veya benzeri
    # vp[3], vp[7], vp[11], vp[15] kontrol
    # Tipik perspektif: vp[15] küçük (0-2 arası) veya vp[11] ~ -1 veya +1
    has_perspective = (abs(data_16f[11]) > 0.5 and abs(data_16f[11]) < 2.0)
    if not has_perspective:
        return False

    return True


def main():
    print("=" * 60)
    print("  Camera / ViewProj Matrix Offset Finder")
    print("=" * 60)

    resolver = HeroResolver()
    heroes = resolver.resolve_all()
    mem = resolver.mem
    gp = resolver.global_page

    print(f"\n[+] GlobalPage: 0x{gp:X}")

    # Yöntem 1: GP + offset → pointer → pointer + 0xEC → matrix
    print(f"\n{'='*60}")
    print(f"  YÖNTEM 1: GP+offset → camera_ptr → cam+0xEC → ViewProj")
    print(f"  (Kuki pattern: GP+0xDC8 → cam → cam+0xEC)")
    print(f"{'='*60}")

    found = []
    # GP'den 0x800 - 0x1200 arası tara (camera pointer genelde buralarda)
    for off in range(0x800, 0x1200, 8):
        ptr = mem.read_u64(gp + off)
        if not ptr or ptr < 0x100000000 or ptr > 0x900000000000:
            continue

        # Bu pointer'dan 0xEC offset'te ViewProj var mı?
        for vp_off in [0xEC, 0xF0, 0xE8, 0x100, 0x110, 0x80, 0x90, 0xA0]:
            data = mem.read(ptr + vp_off, 64)
            if not data or len(data) < 64:
                continue

            floats = struct.unpack('<16f', data)
            if is_valid_viewproj(floats):
                found.append({
                    'gp_off': off,
                    'vp_off': vp_off,
                    'ptr': ptr,
                    'matrix': floats,
                })
                print(f"\n  ✓ GP+0x{off:X} → 0x{ptr:X} → +0x{vp_off:X} = ViewProj!")
                print(f"    vp[0:4]  = {floats[0]:.4f}, {floats[1]:.4f}, {floats[2]:.4f}, {floats[3]:.4f}")
                print(f"    vp[4:8]  = {floats[4]:.4f}, {floats[5]:.4f}, {floats[6]:.4f}, {floats[7]:.4f}")
                print(f"    vp[8:12] = {floats[8]:.4f}, {floats[9]:.4f}, {floats[10]:.4f}, {floats[11]:.4f}")
                print(f"    vp[12:16]= {floats[12]:.4f}, {floats[13]:.4f}, {floats[14]:.4f}, {floats[15]:.4f}")

    if not found:
        print("\n  Yöntem 1: Bulunamadı")

    # Yöntem 2: Geniş tarama — GP etrafında doğrudan matrix arama
    print(f"\n{'='*60}")
    print(f"  YÖNTEM 2: GP etrafında tüm pointer → tüm offset'ler")
    print(f"  (Daha geniş arama: GP+0x0 ~ GP+0x2000)")
    print(f"{'='*60}")

    found2 = []
    for off in range(0x0, 0x2000, 8):
        ptr = mem.read_u64(gp + off)
        if not ptr or ptr < 0x100000000 or ptr > 0x900000000000:
            continue

        # Pointer'ın ilk 0x200 byte'ında matrix ara
        for vp_off in range(0x0, 0x200, 4):
            data = mem.read(ptr + vp_off, 64)
            if not data or len(data) < 64:
                continue

            floats = struct.unpack('<16f', data)
            if is_valid_viewproj(floats):
                # Duplicate kontrolü
                dup = any(f['gp_off'] == off and f['vp_off'] == vp_off for f in found)
                if not dup:
                    found2.append({
                        'gp_off': off,
                        'vp_off': vp_off,
                        'ptr': ptr,
                        'matrix': floats,
                    })

    if found2:
        print(f"\n  {len(found2)} ek aday bulundu:")
        for f in found2[:10]:  # İlk 10
            m = f['matrix']
            print(f"\n  GP+0x{f['gp_off']:X} → 0x{f['ptr']:X} → +0x{f['vp_off']:X}")
            print(f"    vp[0:4]  = {m[0]:.4f}, {m[1]:.4f}, {m[2]:.4f}, {m[3]:.4f}")
            print(f"    vp[11]={m[11]:.4f} vp[15]={m[15]:.4f}")
    else:
        print("\n  Yöntem 2: Ek aday bulunamadı")

    # Sonuç
    all_found = found + found2
    print(f"\n{'='*60}")
    print(f"  SONUÇ: {len(all_found)} ViewProj matrix adayı")
    print(f"{'='*60}")

    if all_found:
        best = all_found[0]
        print(f"\n  EN İYİ ADAY:")
        print(f"    Camera offset: GP + 0x{best['gp_off']:X}")
        print(f"    ViewProj offset: camera + 0x{best['vp_off']:X}")
        print(f"    Camera ptr: 0x{best['ptr']:X}")
        print(f"    Kuki referans: GP + 0x{KUKI_CAMERA_OFF:X} (fark: {best['gp_off'] - KUKI_CAMERA_OFF:+d})")


if __name__ == '__main__':
    main()
