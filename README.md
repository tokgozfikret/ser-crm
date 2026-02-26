# SER-CRM

SER-CRM, cihaz kabul/tamir takibi, satış-stok yönetimi, teklif üretimi ve müşteri paneli süreçlerini tek uygulamada birleştiren Flask tabanlı bir CRM sistemidir.

## Ozellikler

- Rol bazli paneller
- Cihaz yasam dongusu: kabul -> on inceleme -> musteri onayi -> tamir -> kargolama
- Musteri paneli: e-posta + telefon ile cihaz ve on inceleme takibi, onay/red islemleri
- Satis modulu: depo stok, musteriye gonderim, satis/kiralama kayitlari
- Teklif modulu: coklu kalemli teklif olusturma, PDF alma, e-posta ile gonderme
- Panel bazli loglama
- SQLite veritabani ile hizli kurulum

## Teknolojiler

- Python + Flask
- Flask-SQLAlchemy
- Flask-Login
- Flask-WTF (CSRF korumasi)
- ReportLab (PDF uretimi)

## Kurulum

Proje klasorunde:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```




## Panel URL'leri

- Giris: `/giris`
- Admin paneli: `/admin`
- Sekreter paneli: `/sekreterlik`
- Teknik servis paneli: `/teknik-servis`
- Satis paneli: `/satis`
- Musteri paneli: `/musteri-panel`

## Cihaz sureci (durumlar)

Uygulamada kullanilan temel cihaz durumlari:

- `geldi`
- `teknik_serviste` (on inceleme)
- `on_inceleme_sekreterlikte`
- `musteri_onay_bekliyor`
- `musteri_onayladi` / `musteri_onaylamadi`
- `teknik_serviste_tamir`
- `arizalar_tamamlandi`
- `kargolandi`

## Notlar

- Mail gonderimi icin SMTP ayarlari admin panelindeki "Mail Ayarlari" ekranindan yapilandirilir.
- PDF ozellikleri (`on inceleme raporu`, `teklif PDF`) icin `reportlab` kurulmus olmalidir (`requirements.txt` icinde vardir).
- Varsayilan `SECRET_KEY` ve `DATABASE_URL` degerleri gelistirme amaclidir; uretimde ortam degiskenleriyle override edilmelidir.
