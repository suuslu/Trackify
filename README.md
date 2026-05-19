# Trackify PC App

Trackify, BLE (Bluetooth Low Energy) tabanlı bir yakınlık takip prototipidir. Bu masaüstü uygulaması, `Trackify_ESP32` isimli ESP32 BLE cihazını tarar, RSSI sinyal gücünü okur ve buna göre kullanıcıya cihazın yakınlık durumunu gösterir. Geliştirilmiş sürümde uygulama, ESP32 üzerindeki buzzer ve LED için ayrı bir BLE alert link de kurar.

Bu proje özellikle öğrenci seviyesi bir mühendislik prototipi olarak sade tutulmuştur. Mobil uygulama içermez. Sadece bilgisayar üzerinde çalışan bir Python masaüstü uygulamasıdır.
Repo içinde ayrıca raporda geçen örnek ESP32 BLE firmware dosyası da yer alır.

## Proje Amacı

Amaç, bir eşyanın kullanıcıdan uzaklaşıp uzaklaşmadığını Bluetooth sinyal gücü üzerinden yaklaşık olarak takip etmektir.

Uygulama:

- `Trackify_ESP32` adlı BLE cihazını arar
- Cihaz bulunduğunda RSSI değerini gösterir
- Son RSSI örneklerini filtreleyerek yakınlık tahmini yapar
- Sinyal zayıfladığında kullanıcıyı uyarır
- Cihaz yaklaşık 3 saniye boyunca görünmezse `Lost` durumuna geçirir
- Uygun firmware yüklüyse ESP32 buzzer ve LED tarafını da alarm durumuna göre tetikler

## Kullanılan Teknolojiler

- Python
- Tkinter
- bleak
- ESP32 BLE firmware
- VS Code ile çalıştırılabilir masaüstü yapı

## Dosya Yapısı

- `trackify_app.py`: Ana GUI uygulaması
- `firmware/trackify_esp32_ble/trackify_esp32_ble.ino`: BLE reklam yayını, desktop alert linki ve buzzer/LED kontrolü içeren ESP32 firmware dosyası
- `requirements.txt`: Gerekli Python paketi
- `README.md`: Proje açıklaması ve kullanım rehberi

## Donanim Pinleri

Prototipte dogrulanan pin yerlesimi:

- `Buzzer` -> `D19`
- `Button` -> `D18`
- `LED` -> `D4`

## Uygulama Özellikleri

- Pencere başlığı: `Trackify - Bluetooth Proximity Tracker`
- `Scan for Trackify` butonu ile BLE tarama başlatma
- `Trackify_ESP32` isimli cihazı arama
- Ekranda şu bilgileri gösterme:
  - bağlantı durumu
  - cihaz adı
  - filtrelenmiş RSSI değeri
  - yakınlık seviyesi
- Yakınlık seviyeleri:
  - `Near`
  - `Medium`
  - `Far`
  - `Lost`
- `Far` veya `Lost` durumunda:
  - kırmızı uyarı mesajı gösterme
  - sistem sesi ile uyarı verme
  - uyumlu firmware ile ESP32 buzzer ve LED tarafını tetikleme
- Son birkaç RSSI örneğini hareketli ortalama ile yumuşatma
- Tarama işlemini GUI'yi dondurmadan arka planda çalıştırma
- Arka planda ayrı bir BLE hardware alert link durumu gösterme
- Hata durumları için temel kullanıcı bilgilendirmesi

## RSSI Eşik Mantığı

Yakınlık seviyesi filtrelenmiş RSSI değerine göre aşağıdaki eşiklerle belirlenir:

- `RSSI >= -60` ise `Near`
- `-75 <= RSSI < -60` ise `Medium`
- `RSSI < -75` ise `Far`

Eğer cihaz yaklaşık 3 saniye boyunca algılanmazsa durum `Lost` olarak işaretlenir.

Uygulama, ani RSSI dalgalanmalarını azaltmak için son birkaç BLE örneğinin hareketli ortalamasını kullanır.

Not: RSSI, gerçek fiziksel mesafeyi tam olarak vermez. Ortamda duvar, insan, metal yüzeyler ve diğer kablosuz sinyaller varsa sonuçlar değişebilir. Bu nedenle bu sistem bir yaklaşık yakınlık tahmini yapar.

## Uygulamanın Çalışma Mantığı

1. Kullanıcı `Scan for Trackify` butonuna basar.
2. Uygulama arka planda BLE reklam paketlerini dinlemeye başlar.
3. `Trackify_ESP32` isimli cihaz bulunduğunda en güncel RSSI değeri alınır.
4. Son birkaç RSSI örneği hareketli ortalama ile filtrelenir.
5. Uygulama aynı anda ESP32 ile bir hardware alert link kurmaya çalışır.
6. Filtrelenmiş RSSI değeri eşiklerle karşılaştırılarak `Near`, `Medium` veya `Far` sonucu üretilir.
7. Cihaz 3 saniye boyunca tekrar görülmezse durum `Lost` olur.
8. `Far` veya `Lost` durumunda kullanıcı görsel ve sesli olarak uyarılır.
9. Uygun firmware yüklüyse uygulama `Far` ve `Lost` durumlarını ESP32 tarafına da iletir; böylece buzzer ve LED tepki verebilir.

## Kurulum

Önce proje klasörüne girin:

```bash
cd /Users/su/Desktop/Trackify_PC_App
```

Sanal ortam oluşturmanız önerilir.

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Çalıştırma

### macOS

```bash
python3 trackify_app.py
```

### Windows

```powershell
py trackify_app.py
```

## VS Code ile Çalıştırma

1. Projeyi VS Code içinde açın.
2. Doğru Python interpreter seçin.
3. Terminal açın.
4. Gerekirse sanal ortamı aktif edin.
5. `pip install -r requirements.txt` komutunu çalıştırın.
6. `trackify_app.py` dosyasını açın.
7. Run butonuyla veya terminalden uygulamayı başlatın.

## Kullanım

1. BLE yayın yapan ESP32 cihazınızın adının `Trackify_ESP32` olduğundan emin olun.
2. Uygulamayı açın.
3. `Scan for Trackify` butonuna basın.
4. Cihaz bulunduğunda ekranda bağlantı durumu, cihaz adı, filtrelenmiş RSSI ve yakınlık bilgisi güncellenir.
5. Sinyal zayıflarsa `Warning: Item may be left behind!` mesajı görünür.
6. Cihaz tamamen kaybolursa durum `Lost` olur.
7. `Hardware Alert Link` satırı aktif hale gelirse masaüstü uygulaması ESP32 üzerindeki buzzer/LED tarafını da kontrol edebilir.

## Gereksinimler

`requirements.txt` içeriği:

```txt
bleak>=2.0
```

Ek olarak:

- Python kurulumunda `Tkinter` desteği bulunmalıdır
- Bilgisayarınızda Bluetooth açık olmalıdır
- BLE desteği olan bir adaptör gereklidir

## Hata Durumları

Uygulama aşağıdaki durumlar için temel hata yönetimi içerir:

- `bleak` paketi kurulu değilse
- Bluetooth adaptörü kapalıysa veya erişilemiyorsa
- BLE tarama sırasında hata oluşursa
- `Trackify_ESP32` cihazı bulunamazsa
- Python kurulumunda `Tkinter` yoksa

## Sık Karşılaşılan Sorunlar

### 1. `bleak` modülü bulunamadı

Çözüm:

```bash
pip install -r requirements.txt
```

### 2. Tkinter bulunamadı

Bazı Python kurulumlarında `Tkinter` hazır gelmeyebilir. Bu durumda Tk destekli bir Python sürümü kurmanız gerekir.

Kontrol için:

```bash
python3 -m tkinter
```

Eğer pencere açılmazsa Python kurulumunuzu güncellemeniz gerekir.

### 3. Bluetooth izni verilmedi

Özellikle macOS üzerinde uygulama ilk çalıştığında Bluetooth izni istenebilir. İzin verilmezse tarama yapılamaz.

### 4. Cihaz bulunamıyor

Kontrol edin:

- ESP32 açık mı
- BLE reklam yayını yapıyor mu
- Cihaz adı tam olarak `Trackify_ESP32` mi
- Bilgisayarın Bluetooth özelliği açık mı

## Prototip Notları

Bu uygulama basit ve anlaşılır olacak şekilde hazırlanmıştır. Daha gelişmiş sürümlerde aşağıdaki geliştirmeler yapılabilir:

- adaptif veya daha gelişmiş RSSI filtreleme
- grafiksel sinyal geçmişi
- çoklu cihaz desteği
- özel alarm sesi
- cihaz MAC adresi veya servis UUID ile eşleştirme
- log kaydı

## Özet

Trackify PC App, BLE reklam verilerini kullanarak bir ESP32 cihazının kullanıcıya göre yakınlığını yaklaşık olarak takip eden basit bir masaüstü uygulamasıdır. Son RSSI örneklerini filtreleyerek daha kararlı yakınlık tahmini üretir. Repo içinde masaüstü uygulamasına eşlik eden örnek ESP32 BLE firmware dosyası da bulunur. Eğitim, demo ve prototip amaçlı kullanım için uygundur.
