# Claude Flex

**Hesabını Flex'te seç. Modelini Claude Code'da seç.**

```bash
uv tool install git+https://github.com/matrixdurden/claude-flex.git
claude-flex
```

- **a → Claude / ChatGPT → giriş yöntemi → giriş yap.**
- Hesabı seç; **Enter** terminalde Claude Code'u, **d** Desktop'ı açar.
- Modeli Claude Code'un kendi **`/model`** menüsünden veya Desktop menüsünden seç.

Flex'te yalnızca hesap seçimi, değişimi ve limitler bulunur. Ayrı bir model
listesi veya model seçme ekranı yoktur. GPT modelleri Claude Code'da kendi
adlarıyla görünür; Sonnet gibi gösterilmez. Aynı sağlayıcıdaki hesaplar arasında
geçiş yaparken Claude Code'da seçtiğin model korunur.

Claude Code'dan çıkınca Flex'e dönersin. Seçilen hesap normal `claude` komutunda
da kullanılabilir.
Claude Code 2.1.242+ ve Python 3.11+ gerekir; Desktop zorunlu değildir.

## Tuşlar

- **↑↓:** hesap seç.
- **Enter:** seçili hesapla terminal Claude Code'u aç.
- **d:** seçili hesapla Claude Desktop'ı aç.
- **a:** hesap ekle.
- **Space:** hesabı havuza al/çıkar.
- **r:** limitleri ve Claude Code'un model listesini yenile.
- **s:** ayrıntılar, giriş yenileme, gelişmiş ayarlar.
- **Esc:** geri; **q:** çık.

Hesaplar otomatik adlandırılır. Şifreyi yalnızca sağlayıcının kendi giriş
ekranında girersin. Claude ve ChatGPT havuzları ayrı tutulur.

Gösterilen yüzdeler kalan kullanımdır. ChatGPT hesaplarında Codex limitleri,
Claude hesaplarında sağlayıcının kullanım pencereleri gösterilir. Bilinmeyen
değer **—**, eski veri **\*** ile işaretlenir. 429 kullanım limiti hatasında
kullanılabilir başka bir hesabı seçebilir veya yenilenme zamanını bekleyebilirsin.

Hesap seçimi, normal Claude Code'un kullanıcı ayarlarına gateway bağlantısını
ve gerçek model adlarını içeren `/model` listesini yazar. Önceki ayarların
yedeği Flex'in özel kullanıcı klasöründe tutulur; izinler ve ilgisiz tercihler
korunur. Kimlik bilgileri repo dışında kalır.

Desktop profili açılıştan önce otomatik hazırlanır. Uygulama eski profille zaten
açıksa yalnızca **Restart Desktop.** mesajı görünür; tamamen kapatıp açman yeterli.

[English README](../README.md) · [Mimari](architecture.md)
