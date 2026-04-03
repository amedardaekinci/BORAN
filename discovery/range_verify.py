"""
Attack Range Offset Doğrulama — Değer değiştirme testi

C tuşunu sanal olarak basılı tutar, değeri değiştirir, gözlemlersin,
sonra eski değere döner ve C tuşunu bırakır.

Kullanım:
    sudo python3 discovery/range_verify.py
"""

import time
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.pointer_chain import HeroResolver
from input.virtual_key import key_down, key_up, KEYCODES, activate_league

# Test edilecek offset'ler
CANDIDATES = [
    (0x0F54, "OFF_ATTACK_RANGE (kuki referans)"),
    (0x1900, "0x1900 (ikinci 550.0 bulgusu)"),
]

TEST_VALUE = 1200.0   # Çok büyük — halka belirgin şekilde büyümeli
HOLD_SECONDS = 8      # C basılı tutma süresi


def main():
    print("=" * 60)
    print("  Attack Range Offset Doğrulama")
    print("  C tuşu sanal basılı + değer değiştirme")
    print("=" * 60)

    resolver = HeroResolver()
    heroes = resolver.resolve_all()

    if not heroes:
        print("[-] Hero bulunamadı!")
        return

    player = heroes[0]
    hero_addr = player['addr']
    mem = resolver.mem

    print(f"\n[+] {player['name']} @ 0x{hero_addr:X}")

    for offset, name in CANDIDATES:
        addr = hero_addr + offset
        original = mem.read_float(addr)

        print(f"\n{'='*60}")
        print(f"  TEST: {name}")
        print(f"  Offset: 0x{offset:04X}")
        print(f"  Orijinal değer: {original:.1f}")
        print(f"{'='*60}")

        if original < 50 or original > 2000:
            print(f"  [!] Değer makul değil ({original:.1f}), atlıyorum")
            continue

        print(f"\n  Hazır olduğunda ENTER'a bas...")
        print(f"  (Script League'i öne getirecek, C basılı tutacak, değeri değiştirecek)")
        input()

        # 1. League'i öne getir
        print(f"  [*] League öne getiriliyor...")
        activate_league()
        time.sleep(0.3)

        # 2. C tuşunu basılı tut (sanal)
        print(f"  [*] C tuşu basılı tutuluyor...")
        key_down(KEYCODES['c'])
        time.sleep(0.5)  # Halkanın açılmasını bekle

        # 3. Değeri değiştir
        print(f"  [*] Değer {original:.1f} → {TEST_VALUE:.1f}")
        mem.write_float(addr, TEST_VALUE)

        # 4. Bekle — kullanıcı gözlemlesin
        print(f"\n  >>> OYUNA BAK! Mavi halka DEV GİBİ büyüdü mü?")
        for i in range(HOLD_SECONDS, 0, -1):
            # Sürekli yaz (oyun geri yazabilir)
            mem.write_float(addr, TEST_VALUE)
            print(f"  ... {i}sn (değer sürekli yazılıyor: {TEST_VALUE:.0f})", end='\r')
            time.sleep(1)

        # 5. Eski değere dön
        print(f"\n  [*] Değer geri alınıyor: {original:.1f}")
        mem.write_float(addr, original)

        # 6. C tuşunu bırak
        key_up(KEYCODES['c'])
        print(f"  [*] C tuşu bırakıldı")

        time.sleep(0.3)
        restored = mem.read_float(addr)
        print(f"  [+] Şu anki değer: {restored:.1f}")

        print(f"\n  Mavi halka büyüdü mü? (e/h): ", end='')
        answer = input().strip().lower()

        if answer == 'e':
            print(f"\n  ✓✓✓ DOĞRULANDI: {name} (0x{offset:04X}) = DOĞRU OFFSET!")
            print(f"      Bu offset mavi halkanın yarıçapını belirliyor.")
            return
        else:
            print(f"  ✗ {name} — mavi halkayı ETKİLEMİYOR")

    print(f"\n[-] Hiçbir aday doğrulanamadı.")
    print("    Mavi halka farklı bir kaynaktan okunuyor olabilir.")


if __name__ == '__main__':
    main()
