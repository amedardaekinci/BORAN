#!/usr/bin/env python3
"""
Auto Attack v3 — Edge-to-edge range + gerçek attack speed/windup.

Mantık:
1. Startup: CDragon'dan base AS + windup data çek
2. Runtime: Live Client API'den güncel attack speed (1Hz background thread)
3. Edge-to-edge mesafe + attack cooldown (1/AS) + windup timing

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
from core.champion_stats import get_stats_safe
from core.live_client import LiveClientPoller


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
# Attack Speed & Windup Hesaplama
# ============================================================================

def calc_windup_time(base_as, windup, windup_mod, current_as):
    """
    Auto-attack windup süresi (saniye).
    Windup sırasında hareket → AA cancel olur.

    Formül (LoL wiki / VakScript referans):
    base_windup = (1/base_as) * windup
    if windup_mod: actual = base_windup + (current_full - base_windup) * windup_mod
    else: actual = (1/current_as) * windup
    """
    base_windup = (1.0 / base_as) * windup
    current_full = (1.0 / current_as) * windup
    if windup_mod:
        return base_windup + (current_full - base_windup) * windup_mod
    return current_full


def calc_attack_cooldown(current_as):
    """İki AA arası bekleme = 1 / attack_speed."""
    return 1.0 / current_as


# ============================================================================
# Ana Loop
# ============================================================================

def main():
    print("=" * 60)
    print("  BORAN Auto Attack v3")
    print("  Edge-to-edge range + real attack speed/windup")
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
    local_name = local['name']
    print(f"[+] Local: {local_name} range={local['attack_range']:.0f}")

    # Champion base stats — CDragon API
    print(f"[*] {local_name} base stats çekiliyor (CDragon)...")
    base_stats = get_stats_safe(local_name)
    base_as = base_stats["base_attack_speed"]
    windup_pct = base_stats["windup_percent"]
    windup_mod = base_stats["windup_modifier"]
    print(f"[+] base_AS={base_as:.3f}  windup={windup_pct:.3f}  windup_mod={windup_mod:.3f}")

    # Live Client API poller — background thread
    poller = LiveClientPoller(poll_interval=1.0)
    poller.start()
    print("[*] Live Client API poller başlatıldı (1Hz)")
    time.sleep(1.5)
    if poller.is_valid:
        print(f"[+] Güncel attack speed: {poller.attack_speed:.3f}")
    else:
        print(f"[!] Live Client API yanıt yok, base_as={base_as:.3f} kullanılacak")

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

    can_attack_time = 0.0
    can_move_time = 0.0
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
            lx, _, lz = struct.unpack('<3f', local_pos)
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
                ex, _, ez = struct.unpack('<3f', enemy_pos)

                # Düşman gameplay radius
                enemy_radius = mem.read_float(enemy_ptr + OFF_GAMEPLAY_RADIUS)
                if not enemy_radius or enemy_radius < 20 or enemy_radius > 200:
                    enemy_radius = DEFAULT_RADIUS

                dist = distance_2d(lx, lz, ex, ez)

                # Edge-to-edge mesafe = center_dist - local_radius - enemy_radius
                edge_dist = dist - local_radius - enemy_radius

                if edge_dist < closest_dist:
                    closest_dist = edge_dist
                    closest_name = f"hero[{i}]"

            # Edge-to-edge range check + server toleransı
            effective_range = local_range + SERVER_TOLERANCE
            in_range = closest_dist <= effective_range

            # Güncel attack speed — API varsa gerçek, yoksa base
            c_as = poller.attack_speed if poller.is_valid else base_as
            c_as = max(0.2, min(c_as, 2.5))  # AS cap

            if tick % 20 == 0:
                attack_cd = calc_attack_cooldown(c_as)
                windup_t = calc_windup_time(base_as, windup_pct, windup_mod, c_as)
                api_tag = "API" if poller.is_valid else "BASE"
                status = (f"✓ {closest_name} edge={closest_dist:.0f}"
                          if in_range else f"  nearest edge={closest_dist:.0f}")
                print(f"[{tick:5d}] HP:{local_hp:.0f} AS={c_as:.2f}({api_tag}) "
                      f"cd={attack_cd:.2f}s wu={windup_t:.2f}s {status}")

            # Saldır!
            if in_range and now > can_attack_time:
                attack_cd = calc_attack_cooldown(c_as)
                windup_t = calc_windup_time(base_as, windup_pct, windup_mod, c_as)
                print(f"  >>> ATTACK! {closest_name} edge={closest_dist:.0f} "
                      f"AS={c_as:.2f} cd={attack_cd:.3f}s windup={windup_t:.3f}s")
                attack_click()
                can_attack_time = now + attack_cd
                can_move_time = now + windup_t

            # Windup bitti, hareket edebilirsin (kite window)
            if can_move_time > 0 and now > can_move_time and now < can_attack_time:
                right_click_at(960.0, 540.0)  # Kite — ekran ortasına sağ tık
                can_move_time = 0  # Tek seferde bir kez

            time.sleep(0.05)  # 20 tick/sn

    except KeyboardInterrupt:
        poller.stop()
        print("\n[*] Durduruldu.")


if __name__ == '__main__':
    main()
