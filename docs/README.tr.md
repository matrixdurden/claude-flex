# Claude Flex

**Kendi modellerin. Kendi hesapların. Claude Code.**

```bash
uv tool install git+https://github.com/matrixdurden/claude-flex.git
claude-flex
```

Claude Code ve Python 3.11+ gerekir. Desktop zorunlu değildir. `claude-flex`
doğrudan minimalist terminal arayüzünü açar; günlük kullanımda alt komut yoktur.
Gerekli yerel gateway ilk kullanımda otomatik hazırlanır.

## Modeller

Modeli seçip **Enter** ile bilgisayarındaki gerçek Claude Code'u başlatırsın.
Claude Code'dan çıkınca Flex'e dönersin. Proje klasörü değişmez.

**a** ile model veya sağlayıcı ekle: Claude aboneliği, ChatGPT/Codex, OpenRouter
ücretsiz modelleri, Ollama, LM Studio ve uyumlu API'ler. API anahtarları maskeli
alanlardan alınır. Modelin güvenilir araç çağrısı desteği olmalıdır.

## Hesaplar

**Tab** ile Hesaplar sekmesine geç. Claude Pro/Max ve ChatGPT Plus/Pro hesapları
aynı şekilde eklenir: **a → sağlayıcı → giriş yöntemi → giriş yap**.
Etiket veya model formu sorulmaz. Hesap adı otomatik verilir; giriş tamamlanınca
ilgili modeller Modeller sekmesine otomatik eklenir. Claude için tarayıcı veya
giriş bağlantısı, ChatGPT için bunlara ek olarak cihaz kodu ve mevcut OpenCode
girişi seçenekleri bulunur.

- **Space:** hesabı havuza al / çıkar.
- **Enter:** limit ayrıntıları, tek hesap seçimi, giriş yenileme, ağırlık/öncelik,
  hesabı kaldırma.
- **r:** limitleri yenile.
- **s:** havuz stratejisi ve isteğe bağlı Desktop bağlantısı.
- **Esc:** geri; **q:** çık.

Yüzdeler kalan kullanımdır. ChatGPT için Codex kotası, Claude için abonelik
kullanım pencereleri gösterilir. Bilinmeyen veri **—**, eski veri **\*** ile
gösterilir. Hesap havuzları sağlayıcıya göre ayrıdır: Claude hesabını seçmek
ChatGPT hesaplarını kapatmaz.

Terminal TUI ana deneyimdir. Tarayıcı yönetim paneli veya arka planda bir web
sunucusu yoktur. Kimlik bilgileri repo dışında yerel kullanıcı dizininde kalır.

Detaylar ve geliştirme: [English README](../README.md).
