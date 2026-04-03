# Boran — Lessons Learned

## Offset Discovery
- Attack range float değeri C tuşuyla DEĞİŞMEZ — C sadece rendering toggle'ı
- Cross-validation en güvenilir yöntem: birden fazla hero'da aynı offset'te bilinen değer
- Windows (ToirPlus) offset'leri macOS ARM64'te farklı: 0xB48 vs 0xF54
- Offset'ler patch'ler arasında kayabilir, her patch'te yeniden doğrula
