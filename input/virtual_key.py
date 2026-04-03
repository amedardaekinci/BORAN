"""
Virtual Key Input — CGEvent ile sanal tuş gönderme
League of Legends'a sanal C tuşu göndererek range indicator toggle.

macOS Quartz Event Services (CoreGraphics) kullanır.
Accessibility izni gerektirir (System Preferences > Privacy > Accessibility).

Kullanım:
    # C tuşuna bas-bırak (range indicator toggle)
    sudo python3 input/virtual_key.py

    # Belirli bir tuşa bas
    sudo python3 input/virtual_key.py --key c

    # Sadece bas (bırakma)
    sudo python3 input/virtual_key.py --key c --action down

Referans: /kuki/src/input.c:122-138
"""

import ctypes
import ctypes.util
import time
import argparse
import subprocess
import sys

# ============================================================================
# CoreGraphics Framework
# ============================================================================

_cg_path = ctypes.util.find_library('CoreGraphics')
if not _cg_path:
    _cg_path = '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics'
CG = ctypes.CDLL(_cg_path)

_cf_path = ctypes.util.find_library('CoreFoundation')
if not _cf_path:
    _cf_path = '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation'
CF = ctypes.CDLL(_cf_path)

# Tipler
CGEventRef = ctypes.c_void_p
CGEventSourceRef = ctypes.c_void_p
CGKeyCode = ctypes.c_uint16
CGEventTapLocation = ctypes.c_uint32
CGEventType = ctypes.c_uint32
CGPoint = ctypes.c_double * 2  # (x, y)

# Event tap lokasyonları
kCGHIDEventTap = 0          # Hardware level
kCGSessionEventTap = 1      # Session level
kCGAnnotatedSessionEventTap = 2

# Event tipleri
kCGEventKeyDown = 10
kCGEventKeyUp = 11
kCGEventLeftMouseDown = 1
kCGEventLeftMouseUp = 2
kCGEventRightMouseDown = 3
kCGEventRightMouseUp = 4
kCGEventMouseMoved = 5

# Fonksiyon imzaları
CG.CGEventCreateKeyboardEvent.restype = CGEventRef
CG.CGEventCreateKeyboardEvent.argtypes = [CGEventSourceRef, CGKeyCode, ctypes.c_bool]

CG.CGEventPost.restype = None
CG.CGEventPost.argtypes = [CGEventTapLocation, CGEventRef]

CF.CFRelease.restype = None
CF.CFRelease.argtypes = [ctypes.c_void_p]

# ============================================================================
# macOS Virtual Key Codes
# https://developer.apple.com/documentation/coregraphics/cgkeycode
# ============================================================================

KEYCODES = {
    'a': 0x00, 's': 0x01, 'd': 0x02, 'f': 0x03, 'h': 0x04,
    'g': 0x05, 'z': 0x06, 'x': 0x07, 'c': 0x08, 'v': 0x09,
    'b': 0x0B, 'q': 0x0C, 'w': 0x0D, 'e': 0x0E, 'r': 0x0F,
    'y': 0x10, 't': 0x11, '1': 0x12, '2': 0x13, '3': 0x14,
    '4': 0x15, '6': 0x16, '5': 0x17, '9': 0x19, '7': 0x1A,
    '8': 0x1C, '0': 0x1D, 'tab': 0x30, 'space': 0x31,
    'backspace': 0x33, 'escape': 0x35, 'enter': 0x24,
    'shift': 0x38, 'ctrl': 0x3B, 'alt': 0x3A, 'cmd': 0x37,
    'f1': 0x7A, 'f2': 0x78, 'f3': 0x63, 'f4': 0x76,
    'f5': 0x60, 'f6': 0x61, 'f7': 0x62, 'f8': 0x64,
}


# ============================================================================
# Input Functions
# ============================================================================

def key_down(keycode: int):
    """Tuşa bas (bırakma)."""
    ev = CG.CGEventCreateKeyboardEvent(None, CGKeyCode(keycode), True)
    if ev:
        CG.CGEventPost(kCGHIDEventTap, ev)
        CF.CFRelease(ev)


def key_up(keycode: int):
    """Tuşu bırak."""
    ev = CG.CGEventCreateKeyboardEvent(None, CGKeyCode(keycode), False)
    if ev:
        CG.CGEventPost(kCGHIDEventTap, ev)
        CF.CFRelease(ev)


def key_tap(keycode: int, hold_ms: int = 20):
    """Tuşa bas-bırak (tap)."""
    key_down(keycode)
    time.sleep(hold_ms / 1000.0)
    key_up(keycode)


def send_c_key():
    """C tuşuna bas-bırak — range indicator toggle."""
    print("[*] C tuşu gönderiliyor (keycode=0x08)...")
    key_tap(KEYCODES['c'])
    print("[+] C tuşu gönderildi!")


def activate_league():
    """League of Legends penceresini öne getir."""
    try:
        subprocess.run(
            ['osascript', '-e',
             'tell application "League of Legends" to activate'],
            capture_output=True, timeout=3
        )
        time.sleep(0.3)
        return True
    except Exception:
        # Alternatif: process adıyla dene
        try:
            subprocess.run(
                ['osascript', '-e',
                 'tell application "LeagueofLegends" to activate'],
                capture_output=True, timeout=3
            )
            time.sleep(0.3)
            return True
        except Exception:
            return False


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='Virtual Key Input for League of Legends')
    parser.add_argument('--key', '-k', default='c',
                        help='Gönderilecek tuş (default: c)')
    parser.add_argument('--action', '-a', choices=['tap', 'down', 'up'],
                        default='tap', help='Tuş aksiyonu (default: tap)')
    parser.add_argument('--hold', type=int, default=20,
                        help='Tap hold süresi ms (default: 20)')
    parser.add_argument('--repeat', '-n', type=int, default=1,
                        help='Tekrar sayısı (default: 1)')
    parser.add_argument('--delay', type=float, default=0.5,
                        help='Tekrarlar arası bekleme sn (default: 0.5)')
    parser.add_argument('--no-activate', action='store_true',
                        help='League penceresini öne getirme')

    args = parser.parse_args()

    key_name = args.key.lower()
    if key_name not in KEYCODES:
        print(f"[-] Bilinmeyen tuş: {key_name}")
        print(f"    Geçerli tuşlar: {', '.join(sorted(KEYCODES.keys()))}")
        sys.exit(1)

    keycode = KEYCODES[key_name]

    print(f"[*] Tuş: '{key_name}' (keycode=0x{keycode:02X})")
    print(f"[*] Aksiyon: {args.action}, Tekrar: {args.repeat}")

    # League'i öne getir
    if not args.no_activate:
        print("[*] League of Legends penceresi öne getiriliyor...")
        if activate_league():
            print("[+] Pencere aktif")
        else:
            print("[!] Pencere bulunamadı — yine de deneniyor")

    time.sleep(0.2)

    for i in range(args.repeat):
        if args.repeat > 1:
            print(f"  [{i+1}/{args.repeat}]", end=' ')

        if args.action == 'tap':
            key_tap(keycode, args.hold)
            print(f"tap 0x{keycode:02X}")
        elif args.action == 'down':
            key_down(keycode)
            print(f"down 0x{keycode:02X}")
        elif args.action == 'up':
            key_up(keycode)
            print(f"up 0x{keycode:02X}")

        if i < args.repeat - 1:
            time.sleep(args.delay)

    print("[+] Tamamlandı!")


if __name__ == '__main__':
    main()
