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
adlarıyla görünür. Her hesabın model tercihi ve geçmişi ayrı profilde tutulur.

Claude Code'dan çıkınca Flex'e dönersin. Flex kendi yapılandırma klasörünü kullanır;
normal `claude` komutu mevcut normal kurulumunu açmaya devam eder.
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

Her hesabın ayrı bağlantı portu, Claude yapılandırması ve oturum klasörü vardır.
Başka hesap açmak çalışan hesabın bağlantısını kapatmaz veya hesabını değiştirmez.
Normal `~/.claude` ayarlarına ve Desktop'ın ortak profil listesine yazılmaz.
Kimlik bilgileri repo dışında, Flex'in özel klasöründe kalır. Bu bir sanal makine
değil; ayrı uygulama profilleridir ve seçtiğin proje klasörü üzerinde çalışır.

Desktop bilgisayarda algılanırsa **d** seçeneği görünür. Kurulu uygulama ayrı
Flex veri diziniyle açılır; normal Desktop oturumun kapatılmaz. Kısayol oluşturulmaz.

## İzinler

**s → Permissions** altında **Full access**, **Auto** ve **Ask** bulunur.
Full access, `bypassPermissions` modudur; işlem onaylarını sormadan çalışır.
Auto bazı işlemler için yine onay isteyebilir. Bu tercih kullanıcıya özeldir;
yeni kurulumlara zorunlu tam erişim uygulanmaz. Değişiklikten sonra yeni oturum
aç. Tercih yalnızca Flex profillerine uygulanır; normal Claude izinleri değişmez.

[English README](../README.md) · [Mimari](architecture.md)
