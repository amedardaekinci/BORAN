#!/usr/bin/env python3
"""
BORAN Launcher — Tek komutla range circle aktif et.

1. League process'i bul
2. Metal hook dylib'i inject et
3. Syslog'da başarı mesajı kontrol et

Kullanım:
    sudo python3 loader/boran_launcher.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from loader.inject import find_league, inject_dylib, check_syslog

DYLIB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'metal', 'boran_range_draw.dylib'
)


def main():
    print("=" * 60)
    print("  BORAN Range Circle Launcher")
    print("=" * 60)

    # Dylib var mı kontrol et
    if not os.path.exists(DYLIB_PATH):
        print(f"[-] Dylib bulunamadı: {DYLIB_PATH}")
        print(f"    Önce derle: cd metal && make")
        sys.exit(1)
    print(f"[+] Dylib: {DYLIB_PATH}")

    # League'i bul
    print("[*] League of Legends aranıyor...")
    try:
        pid, task = find_league()
    except RuntimeError as e:
        print(f"[-] {e}")
        sys.exit(1)
    print(f"[+] PID: {pid}")

    # Inject et
    print(f"\n[*] Metal hook dylib inject ediliyor...")
    success, info = inject_dylib(task, DYLIB_PATH)

    if not success:
        print(f"[-] Injection BAŞARISIZ: {info}")
        sys.exit(1)

    print(f"[+] Injection BAŞARILI: {info}")

    # Syslog kontrol
    print(f"\n[*] Dylib log'ları kontrol ediliyor...")
    time.sleep(2)

    logs = check_syslog("[BORAN-MTL]", seconds=10)
    if logs:
        print(f"[+] {len(logs)} log mesajı:")
        for l in logs:
            print(f"    {l}")
    else:
        print("[*] Henüz log yok — birkaç saniye bekle ve kontrol et:")
        print("    log show --last 30s --predicate 'eventMessage contains \"BORAN-MTL\"'")

    print(f"\n{'='*60}")
    print("  Range circle aktif! Oyunda görünmeli.")
    print("  Log takip: log stream --predicate 'eventMessage contains \"BORAN-MTL\"'")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
