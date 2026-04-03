# Boran — Attack Range Offset Discovery

## Phase 1: Core Infrastructure ✅
- [x] `core/mem.py` — Mach API Memory Reader
- [x] `discovery/pointer_chain.py` — Hero Object Resolver

## Phase 2: Snapshot Diff Tool ✅
- [x] `discovery/snapshot_diff.py` — C-Key Toggle Diff

## Phase 3: Range Scanner ✅
- [x] `discovery/range_scanner.py` — Known Value + Cross-Validation Scanner

## Phase 4: Doğrulama
- [ ] Practice Tool'da çalıştır ve offset'i doğrula
- [ ] `offsets/known_offsets.json` güncelle
- [ ] Kuki 0xF54 ile karşılaştır

## Kullanım Sırası
1. `sudo python3 discovery/pointer_chain.py` — Hero'ları bul
2. `sudo python3 discovery/range_scanner.py` — Attack range offset'i tara
3. `sudo python3 discovery/snapshot_diff.py` — C-key toggle diff analizi
