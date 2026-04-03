#!/usr/bin/env python3
"""
Auto Attack v2 — Edge-to-edge range check ile otomatik saldır.

Mantık:
1. Hero ve düşman pozisyonlarını oku
2. Edge-to-edge mesafe hesapla (center_dist - local_radius - enemy_radius)
3. edge_dist <= attack_range + server_tolerance → A + sol tık (attack-move-click)

Kullanım:
    sudo python3 orbwalker/auto_attack.py

Durdurmak: Ctrl+C
"""

import time
import math
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.pointer_chain import HeroResolver


# ============================================================================
# CGEvent — Mouse tıklama
# ============================================================================

import ctypes
import ctypes.util

_cg = ctypes.CDLL(ctypes.util.find_library('CoreGraphics') or
                   '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
_cf = ctypes.CDLL(ctypes.util.find_library('CoreFoundation') or
                   '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')

_cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
_cg.CGEventCreateMouseEvent.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32,
    ctypes.c_double, ctypes.c_double,  # CGPoint inline
    ctypes.c_uint32
]
_cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
_cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
_cg.CGEventPost.restype = None
_cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_cg.CGEventGetLocation.restype = None  # Actually returns CGPoint but we don't need it
_cf.CFRelease.restype = None
_cf.CFRelease.argtypes = [ctypes.c_void_p]

kCGHIDEventTap = 0
kCGEventRightMouseDown = 3
kCGEventRightMouseUp = 4

KEY_A = 0x00  # macOS virtual keycode for 'A'


def right_click_at(x, y):
    """Belirtilen ekran koordinatına sağ tık."""
    down = _cg.CGEventCreateMouseEvent(None, kCGEventRightMouseDown, x, y, 1)
    up = _cg.CGEventCreateMouseEvent(None, kCGEventRightMouseUp, x, y, 1)
    _cg.CGEventPost(kCGHIDEventTap, down)
    time.sleep(0.01)
    _cg.CGEventPost(kCGHIDEventTap, up)
    _cf.CFRelease(down)
    _cf.CFRelease(up)


def attack_click():
    """A tuşu + sağ tık = attack-move-click (en yakın düşmana saldır)."""
    # A tuşuna bas
    a_down = _cg.CGEventCreateKeyboardEvent(None, KEY_A, True)
    _cg.CGEventPost(kCGHIDEventTap, a_down)
    _cf.CFRelease(a_down)
    time.sleep(0.02)

    # Sol tık (attack-move-click)
    from ctypes import c_uint32
    kCGEventLeftMouseDown = 1
    kCGEventLeftMouseUp = 2
    # Ekranın ortasına tıkla (hero zaten orada)
    ldown = _cg.CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, 960.0, 540.0, 0)
    lup = _cg.CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, 960.0, 540.0, 0)
    _cg.CGEventPost(kCGHIDEventTap, ldown)
    time.sleep(0.01)
    _cg.CGEventPost(kCGHIDEventTap, lup)
    _cf.CFRelease(ldown)
    _cf.CFRelease(lup)

    time.sleep(0.02)
    # A tuşunu bırak
    a_up = _cg.CGEventCreateKeyboardEvent(None, KEY_A, False)
    _cg.CGEventPost(kCGHIDEventTap, a_up)
    _cf.CFRelease(a_up)


# ============================================================================
# 2D Mesafe (X-Z düzlemi)
# ============================================================================

def distance_2d(x1, z1, x2, z2):
    """İki nokta arası 2D mesafe (yükseklik Y göz ardı)."""
    dx = x2 - x1
    dz = z2 - z1
    return math.sqrt(dx * dx + dz * dz)


# ============================================================================
# Ana Loop
# ============================================================================

def main():
    print("=" * 60)
    print("  BORAN Auto Attack v2")
    print("  Edge-to-edge range check + server toleransı")
    print("  Formül: edge_dist <= attack_range + 15")
    print("  Durdurmak: Ctrl+C")
    print("=" * 60)

    resolver = HeroResolver()
    heroes = resolver.resolve_all()
    mem = resolver.mem
    gp = resolver.global_page

    if not heroes:
        print("[-] Hero bulunamadı!")
        return

    print(f"[+] {len(heroes)} hero bulundu")
    local = heroes[0]
    print(f"[+] Local: {local['name']} range={local['attack_range']:.0f}")
    print(f"[+] Başlatılıyor...\n")

    # Offset'ler
    OFF_HM = 0x478
    OFF_ARR = 0x8
    OFF_CNT = 0x10
    OFF_POS = 0x98
    OFF_HP = 0x36FC
    OFF_RANGE = 0xF54
    OFF_TEAM = 0x20
    OFF_GAMEPLAY_RADIUS = 0x3A8  # nOverrideCollisionRadius (kuki referans)

    # Varsayılan gameplay radius (~65 unit çoğu champion için)
    DEFAULT_RADIUS = 65.0
    # Server toleransı — LoL server ~15 unit leniency verir
    SERVER_TOLERANCE = 15.0

    attack_cooldown = 0
    tick = 0

    try:
        while True:
            tick += 1
            now = time.time()

            # Hero manager'dan tüm hero'ları oku
            mgr = mem.read_u64(gp + OFF_HM)
            if not mgr or mgr < 0x100000000:
                time.sleep(0.1)
                continue

            arr = mem.read_u64(mgr + OFF_ARR)
            cnt = mem.read_u32(mgr + OFF_CNT)
            if not arr or cnt < 1:
                time.sleep(0.1)
                continue

            # Local hero
            local_ptr = mem.read_u64(arr)
            if not local_ptr or local_ptr < 0x100000000:
                time.sleep(0.1)
                continue

            local_pos = mem.read(local_ptr + OFF_POS, 12)
            if not local_pos:
                time.sleep(0.1)
                continue
            lx, ly, lz = struct.unpack('<3f', local_pos)
            local_hp = mem.read_float(local_ptr + OFF_HP)
            local_range = mem.read_float(local_ptr + OFF_RANGE)
            local_team = mem.read_u32(local_ptr + OFF_TEAM)

            # Local gameplay radius — memory'den oku, geçersizse default
            local_radius = mem.read_float(local_ptr + OFF_GAMEPLAY_RADIUS)
            if not local_radius or local_radius < 20 or local_radius > 200:
                local_radius = DEFAULT_RADIUS

            if local_hp <= 0 or local_range < 50:
                time.sleep(0.1)
                continue

            # Düşman hero'ları tara
            closest_dist = 99999
            closest_center_dist = 99999
            closest_enemy_radius = DEFAULT_RADIUS
            closest_name = ""

            for i in range(1, min(cnt, 12)):
                enemy_ptr = mem.read_u64(arr + i * 8)
                if not enemy_ptr or enemy_ptr < 0x100000000:
                    continue

                enemy_team = mem.read_u32(enemy_ptr + OFF_TEAM)
                if enemy_team == local_team:
                    continue  # Aynı takım, atla

                enemy_hp = mem.read_float(enemy_ptr + OFF_HP)
                if enemy_hp <= 0:
                    continue  # Ölü

                enemy_pos = mem.read(enemy_ptr + OFF_POS, 12)
                if not enemy_pos:
                    continue
                ex, ey, ez = struct.unpack('<3f', enemy_pos)

                # Düşman gameplay radius
                enemy_radius = mem.read_float(enemy_ptr + OFF_GAMEPLAY_RADIUS)
                if not enemy_radius or enemy_radius < 20 or enemy_radius > 200:
                    enemy_radius = DEFAULT_RADIUS

                dist = distance_2d(lx, lz, ex, ez)

                # Edge-to-edge mesafe = center_dist - local_radius - enemy_radius
                edge_dist = dist - local_radius - enemy_radius

                if edge_dist < closest_dist:
                    closest_dist = edge_dist
                    closest_center_dist = dist
                    closest_name = f"hero[{i}]"
                    closest_enemy_radius = enemy_radius

            # Edge-to-edge range check + server toleransı
            # LoL formülü: edge_distance <= attack_range + tolerance
            effective_range = local_range + SERVER_TOLERANCE
            in_range = closest_dist <= effective_range

            if tick % 20 == 0:
                status = (f"✓ {closest_name} edge={closest_dist:.0f} (center={closest_center_dist:.0f} "
                          f"r={local_radius:.0f}+{closest_enemy_radius:.0f})" if in_range
                          else f"  nearest edge={closest_dist:.0f}")
                print(f"[{tick:5d}] HP:{local_hp:.0f} Range:{local_range:.0f}+{SERVER_TOLERANCE:.0f} {status}")

            # Saldır!
            if in_range and now > attack_cooldown:
                print(f"  >>> ATTACK! {closest_name} edge={closest_dist:.0f} < range={effective_range:.0f}")
                attack_click()
                attack_cooldown = now + 0.4  # 0.4sn cooldown (attack speed'e göre ayarlanabilir)

            time.sleep(0.05)  # 20 tick/sn

    except KeyboardInterrupt:
        print("\n[*] Durduruldu.")


if __name__ == '__main__':
    main()
