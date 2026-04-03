"""
ViewProj Matrix Finder — Kamera hareketi ile doğrulama

Yöntem: İki kamera açısında snapshot al, değişen 4x4 float matrix'i bul.
Gerçek ViewProj matrix kamera hareket ettiğinde DEĞİŞMELİ.

Kullanım:
    sudo python3 discovery/find_viewproj.py
"""

import struct
import time
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.pointer_chain import HeroResolver


def read_matrix_at(mem, addr):
    """64 byte oku, 16 float olarak döndür."""
    data = mem.read(addr, 64)
    if not data or len(data) < 64:
        return None
    return struct.unpack('<16f', data)


def is_plausible_viewproj(m):
    """ViewProj matrix olabilir mi?"""
    if not m:
        return False
    for v in m:
        if math.isnan(v) or math.isinf(v):
            return False
    # Tamamen sıfır değil
    if all(abs(v) < 1e-10 for v in m):
        return False
    # En az 10 non-zero
    nonzero = sum(1 for v in m if abs(v) > 1e-6)
    if nonzero < 10:
        return False
    return True


def matrix_changed(m1, m2, threshold=0.001):
    """İki matrix arasında anlamlı fark var mı?"""
    if not m1 or not m2:
        return False
    changes = 0
    for a, b in zip(m1, m2):
        if abs(a - b) > threshold:
            changes += 1
    return changes >= 4  # En az 4 eleman değişmeli


def test_w2ndc(vp, wx, wy, wz):
    """World → NDC projection test."""
    cx = vp[0]*wx + vp[4]*wy + vp[8]*wz  + vp[12]
    cy = vp[1]*wx + vp[5]*wy + vp[9]*wz  + vp[13]
    cw = vp[3]*wx + vp[7]*wy + vp[11]*wz + vp[15]
    if abs(cw) < 0.001:
        return None, None, cw
    return cx/cw, cy/cw, cw


def main():
    print("=" * 60)
    print("  ViewProj Matrix Finder — Kamera Hareketi Testi")
    print("=" * 60)

    resolver = HeroResolver()
    heroes = resolver.resolve_all()
    mem = resolver.mem
    gp = resolver.global_page

    if not heroes:
        print("[-] Hero bulunamadı!")
        return

    hero_pos = (heroes[0].get('hp', 0), heroes[0])
    print(f"\n[+] GP: 0x{gp:X}")
    print(f"[+] Hero: {heroes[0]['name']} pos will be used for projection test")

    # GP etrafında tüm pointer → matrix adaylarını topla
    print(f"\n[*] GP etrafında matrix pointer'ları taranıyor...")
    candidates = []  # (gp_off, ptr, vp_off, addr)

    for gp_off in range(0x0, 0x1200, 8):
        ptr = mem.read_u64(gp + gp_off)
        if not ptr or ptr < 0x100000000 or ptr > 0x900000000000:
            continue
        for vp_off in range(0x0, 0x200, 4):
            m = read_matrix_at(mem, ptr + vp_off)
            if m and is_plausible_viewproj(m):
                candidates.append((gp_off, ptr, vp_off, ptr + vp_off))

    print(f"[*] {len(candidates)} matrix adayı bulundu")

    # Snapshot 1: Şu anki kamera açısı
    print(f"\n{'='*60}")
    print(f"  ADIM 1: Kamerayı HAREKET ETTİRME — şu anki pozisyonda kal")
    print(f"  ENTER'a bas...")
    print(f"{'='*60}")
    input()

    snap1 = {}
    for gp_off, ptr, vp_off, addr in candidates:
        m = read_matrix_at(mem, addr)
        if m:
            snap1[(gp_off, vp_off)] = m

    print(f"[+] Snapshot 1: {len(snap1)} matrix okundu")

    # Snapshot 2: Kamera hareket ettikten sonra
    print(f"\n{'='*60}")
    print(f"  ADIM 2: Kamerayı FARKLI AÇIYA ÇEVİR (mouse ile döndür)")
    print(f"  Döndürdükten sonra ENTER'a bas...")
    print(f"{'='*60}")
    input()

    snap2 = {}
    for gp_off, ptr, vp_off, addr in candidates:
        m = read_matrix_at(mem, addr)
        if m:
            snap2[(gp_off, vp_off)] = m

    print(f"[+] Snapshot 2: {len(snap2)} matrix okundu")

    # Diff: kamera ile değişen matrix'ler = ViewProj
    print(f"\n{'='*60}")
    print(f"  SONUÇLAR: Kamera ile değişen matrix'ler")
    print(f"{'='*60}")

    hero = heroes[0]
    hx, hy, hz = hero.get('hp', 0), 0, 0
    # Hero position'ı tekrar oku
    hero_addr = hero['addr']
    pos_data = mem.read(hero_addr + 0x98, 12)
    if pos_data:
        hx, hy, hz = struct.unpack('<3f', pos_data)
    print(f"\n[*] Hero position: ({hx:.0f}, {hy:.0f}, {hz:.0f})")

    changed = []
    for key in snap1:
        if key in snap2:
            m1 = snap1[key]
            m2 = snap2[key]
            if matrix_changed(m1, m2):
                gp_off, vp_off = key
                # Projeksiyon testi — snap2 matrix ile hero pozisyonu NDC'ye çevir
                nx, ny, cw = test_w2ndc(m2, hx, hy, hz)
                in_ndc = nx is not None and -2.0 < nx < 2.0 and -2.0 < ny < 2.0
                changed.append((gp_off, vp_off, m2, nx, ny, cw, in_ndc))

    if not changed:
        print("\n[-] Hiçbir matrix kamera ile değişmedi!")
        print("    Kamerayı yeterince döndürdün mü?")
        return

    print(f"\n[+] {len(changed)} matrix kamera ile değişti:\n")

    # NDC testi geçenleri üstte göster
    changed.sort(key=lambda x: (not x[6], abs(x[4] or 99) if x[4] else 99))

    for gp_off, vp_off, m, nx, ny, cw, in_ndc in changed[:15]:
        marker = "✓ NDC OK" if in_ndc else "✗ NDC fail"
        ndc_str = f"ndc=({nx:.2f},{ny:.2f}) cw={cw:.1f}" if nx is not None else "cw≈0"
        print(f"  GP+0x{gp_off:X} → ptr+0x{vp_off:X}  {marker}  {ndc_str}")
        if in_ndc:
            print(f"    vp[0:4]  = {m[0]:.4f}, {m[1]:.4f}, {m[2]:.4f}, {m[3]:.4f}")
            print(f"    vp[4:8]  = {m[4]:.4f}, {m[5]:.4f}, {m[6]:.4f}, {m[7]:.4f}")
            print(f"    vp[8:12] = {m[8]:.4f}, {m[9]:.4f}, {m[10]:.4f}, {m[11]:.4f}")
            print(f"    vp[12:16]= {m[12]:.4f}, {m[13]:.4f}, {m[14]:.4f}, {m[15]:.4f}")

    # En iyi sonuç
    best = [c for c in changed if c[6]]
    if best:
        gp_off, vp_off = best[0][0], best[0][1]
        print(f"\n{'='*60}")
        print(f"  DOĞRU ViewProj: GP+0x{gp_off:X} → ptr+0x{vp_off:X}")
        print(f"  NDC projection: ({best[0][3]:.3f}, {best[0][4]:.3f})")
        print(f"{'='*60}")
    else:
        print(f"\n[!] NDC testi geçen matrix bulunamadı")
        print(f"    Belki row-major/column-major fark var — tüm değişenleri kontrol et")


if __name__ == '__main__':
    main()
