# Kişisel Organizer (Kronometre + Görev/Takvim)

Python + Flet ile yazılmış, Android'e paketlenebilen kişisel bir uygulama.

## Özellikler
- **Kronometre:** Başlat/Durdur/Sıfırla, tur (lap) kaydı
- **Görevler:** Görev ekle, tarih seç, tamamlandı olarak işaretle, sil

## Bilgisayarında Çalıştırma (Test için)

```bash
pip install -r requirements.txt
python main.py
```

Bu komut uygulamayı masaüstünde bir pencere olarak açar — kod aynı, sadece
test için hızlı bir yöntem.

## Android APK Oluşturma

Flet'in kendi CLI aracıyla APK üretebilirsin:

```bash
pip install flet[all]
flet build apk
```

Bu komut `build/` klasörü altında kurulabilir bir `.apk` dosyası oluşturur.
İlk build biraz zaman alabilir (Flutter araçlarını indirir).

Detaylı platform gereksinimleri (Android SDK vs.) için:
https://flet.dev/docs/publish/android

## Sıradaki Adımlar (İstersen ileride ekleriz)
- Bildirim / hatırlatıcı desteği
- Görevleri cihazda kalıcı olarak saklama (şu an sadece uygulama açıkken bellekte duruyor, kapatınca siliniyor)
- Takvim görünümünde günlere göre gruplama

## Dosya Yapısı
```
mobilapp/
├── main.py           # Tüm uygulama mantığı
├── requirements.txt  # Bağımlılıklar
└── README.md
```
