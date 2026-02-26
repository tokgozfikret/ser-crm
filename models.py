# -*- coding: utf-8 -*-
"""Veritabanı modelleri."""
from datetime import datetime
from flask_login import UserMixin
from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # sekreter, teknik, satis
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))

    def __repr__(self):
        return f'<User {self.username}>'


class Device(db.Model):
    __tablename__ = 'devices'
    __table_args__ = (
        db.Index('idx_devices_musteri_adi', 'musteri_adi'),
        db.Index('idx_devices_musteri_tel', 'musteri_telefon'),
        db.Index('idx_devices_durum_created', 'durum', 'created_at'),
    )
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('musteriler.id'), nullable=True)
    musteri_adi = db.Column(db.String(200), nullable=False)
    musteri_telefon = db.Column(db.String(50))
    musteri_adres = db.Column(db.String(500))
    musteri_email = db.Column(db.String(120))
    cihaz_marka = db.Column(db.String(200))
    cihaz_model = db.Column(db.String(200))
    cihaz_bilgisi = db.Column(db.String(500), nullable=False)
    seri_no = db.Column(db.String(100))
    aciklama = db.Column(db.String(1000))
    durum = db.Column(db.String(30), nullable=False, default='geldi')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    teknik_servise_gonderildi_at = db.Column(db.DateTime)
    on_inceleme_sekreterlikte_at = db.Column(db.DateTime)  # Teknik servisten ön inceleme sonrası dönüş
    on_inceleme_raporu_musteriye_at = db.Column(db.DateTime)  # Rapor müşteriye gönderildi
    musteri_onay_durumu = db.Column(db.String(20))  # bekliyor, onaylandi, onaylanmadi
    musteri_onay_tarihi = db.Column(db.DateTime)
    teknik_servise_tamir_icin_at = db.Column(db.DateTime)  # Müşteri onayı sonrası tamir için 2. kez gönderim
    on_inceleme_fiyat = db.Column(db.Numeric(12, 2))  # Ön inceleme raporu tahmini fiyat (müşteriye gönderilmeden önce)
    on_inceleme_para_birimi = db.Column(db.String(5), default='TL', nullable=False)  # TL, USD, EUR, GBP
    arizalar_tamamlandi_at = db.Column(db.DateTime)
    kargolandi_at = db.Column(db.DateTime)
    kontrol_kisi_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # Tamir sonrası kontrol edecek teknik personel
    kontrol_onaylandi_at = db.Column(db.DateTime, nullable=True)  # Kontrol kişisinin onayı
    tamir_yapan_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # Arızaları yapıldı işaretleyen (sadece bu kişi sekreterliğe bildirebilir)
    faults = db.relationship('Fault', backref='device', lazy='dynamic', cascade='all, delete-orphan')
    kontrol_kisi = db.relationship('User', foreign_keys=[kontrol_kisi_id], backref='kontrol_cihazlari')
    tamir_yapan = db.relationship('User', foreign_keys=[tamir_yapan_id], backref='tamir_cihazlari')

    @property
    def on_inceleme_para_birimi_sembol(self):
        return {'TL': '₺', 'USD': '$', 'EUR': '€', 'GBP': '£'}.get(self.on_inceleme_para_birimi or 'TL', '₺')

    @property
    def on_inceleme_toplamlari(self):
        """Ön inceleme fiyatlarını para birimine göre gruplanmış olarak döndürür: {'TL': 1000.0, 'USD': 50.0} gibi."""
        totals = {}
        arizalar = list(self.faults)
        if arizalar:
            for f in arizalar:
                if f.fiyat is None:
                    continue
                code = ((getattr(f, 'para_birimi', None) or 'TL')).upper()
                totals[code] = totals.get(code, 0) + float(f.fiyat)
        elif self.on_inceleme_fiyat is not None:
            code = (self.on_inceleme_para_birimi or 'TL').upper()
            totals[code] = float(self.on_inceleme_fiyat)
        return totals

    @property
    def on_inceleme_toplam_str(self):
        """Ön inceleme toplamlarını '100,00 ₺ + 50,00 $' formatında döndürür."""
        totals = self.on_inceleme_toplamlari
        if not totals:
            return None
        symbols = {'TL': '₺', 'USD': '$', 'EUR': '€', 'GBP': '£'}
        order = ['TL', 'USD', 'EUR', 'GBP']
        parts = []
        for code in order:
            if code in totals:
                val = float(totals[code])
                parts.append(f"{val:,.2f} {symbols.get(code, code)}")
        for code, val in totals.items():
            if code not in order:
                parts.append(f"{float(val):,.2f} {code}")
        return ' + '.join(parts)

    DURUMLAR = {
        'geldi': 'Cihaz geldi',
        'teknik_serviste': 'Teknik Serviste (Ön İnceleme)',
        'on_inceleme_sekreterlikte': 'Ön inceleme sekreterlikte',
        'musteri_onay_bekliyor': 'Firma onayı bekliyor',
        'musteri_onayladi': 'Firma onayladı',
        'musteri_onaylamadi': 'Firma onaylamadı',
        'teknik_serviste_tamir': 'Teknik serviste (Tamir)',
        'arizalar_tamamlandi': 'Arızalar tamamlandı',
        'kargolandı': 'Kargolandı',
    }


class Customer(db.Model):
    """Panellerde ortak kullanılacak müşteri tablosu."""
    __tablename__ = 'musteriler'
    # Müşteri no – tüm sistemde kullanılacak primary key
    id = db.Column(db.Integer, primary_key=True)  # müşteri_no
    ad = db.Column(db.String(200), nullable=False)
    yetkili_kisi = db.Column(db.String(200))  # Yetkili kişi / iletişim kişisi
    telefon = db.Column(db.String(50))
    adres = db.Column(db.String(500))
    email = db.Column(db.String(120))
    lokasyon_proje = db.Column(db.String(200))
    notlar = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    devices = db.relationship('Device', backref='customer', lazy='dynamic')


class Fault(db.Model):
    __tablename__ = 'faults'
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=False)
    aciklama = db.Column(db.String(500), nullable=False)
    yapildi = db.Column(db.Boolean, default=False)
    fiyat = db.Column(db.Numeric(12, 2))  # Ön inceleme tahmini fiyat (arıza bazlı)
    para_birimi = db.Column(db.String(5), default='TL')  # TL, USD, EUR, GBP
    created_at = db.Column(db.DateTime, default=datetime.now)

    @property
    def para_birimi_sembol(self):
        return {'TL': '₺', 'USD': '$', 'EUR': '€', 'GBP': '£'}.get(self.para_birimi or 'TL', '₺')


class UrunGrubu(db.Model):
    """Sadece ürün grubu listesine eklenen gruplar (stok kaydı olmadan)."""
    __tablename__ = 'urun_gruplari'
    id = db.Column(db.Integer, primary_key=True)
    ad = db.Column(db.String(100), unique=True, nullable=False)


class Inventory(db.Model):
    """Depo stok (Satış paneli)."""
    __tablename__ = 'depo_stok'
    __table_args__ = (
        db.Index('idx_depo_stok_stok_no', 'stok_numarasi'),
        db.Index('idx_depo_stok_urun_adi', 'urun_adi'),
        db.Index('idx_depo_stok_marka', 'marka'),
        db.Index('idx_depo_stok_model', 'model'),
        db.Index('idx_depo_stok_urun_grubu', 'urun_grubu'),
    )
    id = db.Column(db.Integer, primary_key=True)
    stok_numarasi = db.Column(db.String(20), unique=True, nullable=True)  # STK-00001 formatında, kullanıcıya gösterilir
    urun_adi = db.Column(db.String(200), nullable=False)
    urun_grubu = db.Column(db.String(100))
    marka = db.Column(db.String(100))
    model = db.Column(db.String(100))
    miktar = db.Column(db.Integer, nullable=False, default=0)
    birim = db.Column(db.String(20), nullable=False, default='adet')
    aciklama = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    sales = db.relationship('Sale', backref='inventory', lazy='dynamic', cascade='all, delete-orphan')


class Sale(db.Model):
    """Müşteriye gönderim kaydı (Satış paneli)."""
    __tablename__ = 'satis_gonderim'
    __table_args__ = (
        db.Index('idx_satis_created', 'created_at'),
        db.Index('idx_satis_musteri_adi', 'musteri_adi'),
    )
    id = db.Column(db.Integer, primary_key=True)
    inventory_id = db.Column(db.Integer, db.ForeignKey('depo_stok.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('musteriler.id'), nullable=True)
    miktar = db.Column(db.Integer, nullable=False)
    tur = db.Column(db.String(20), nullable=False, default='satis')  # satis / kiralama
    musteri_adi = db.Column(db.String(200), nullable=False)
    musteri_adres = db.Column(db.String(500))
    musteri_telefon = db.Column(db.String(50))
    musteri_email = db.Column(db.String(120))
    aciklama = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.now)
    sale_serials = db.relationship('SaleSerial', backref='sale', lazy='select', cascade='all, delete-orphan')
    customer = db.relationship('Customer', backref='sales', lazy='joined')


class PasswordResetToken(db.Model):
    """Şifre sıfırlama token'ı (geçici, süre sınırlı)."""
    __tablename__ = 'password_reset_tokens'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


class MailConfig(db.Model):
    """SMTP mail sunucu ayarları (admin tarafından yapılandırılır)."""
    __tablename__ = 'mail_config'
    id = db.Column(db.Integer, primary_key=True)
    smtp_host = db.Column(db.String(200))
    smtp_port = db.Column(db.Integer, default=587)
    smtp_username = db.Column(db.String(200))
    smtp_password = db.Column(db.String(255))
    from_email = db.Column(db.String(200))
    use_tls = db.Column(db.Boolean, default=True)


class Teklif(db.Model):
    """Satış paneli – teklif kayıtları."""
    __tablename__ = 'teklifler'
    __table_args__ = (db.Index('idx_teklif_tarih', 'teklif_tarihi'), db.Index('idx_teklif_firma', 'firma_adi'))
    teklif_no = db.Column(db.String(20), primary_key=True)  # TKL-00001 formatı
    customer_id = db.Column(db.Integer, db.ForeignKey('musteriler.id'), nullable=True)
    firma_adi = db.Column(db.String(200), nullable=False)
    yetkilisi = db.Column(db.String(200))
    telefon = db.Column(db.String(50))
    email = db.Column(db.String(120))
    adres = db.Column(db.String(500))
    teklif_tarihi = db.Column(db.Date, nullable=False)
    referans = db.Column(db.String(200))
    hazirlayan_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    aciklama = db.Column(db.String(1000))
    para_birimi = db.Column(db.String(5), nullable=False, default='TL')  # TL, USD, EUR, GBP
    odeme_suresi = db.Column(db.String(200))  # Ödeme süresi (genel)
    teklif_gecerlilik_tarihi = db.Column(db.Date)  # Teklif geçerlilik tarihi
    teslimat_zamani = db.Column(db.String(500))  # Teslimat zamanı
    # Eski tek ürün alanları (geriye dönük uyumluluk; yeni kayıtlar kalemlerde tutulur)
    urun_marka = db.Column(db.String(200))
    urun_model = db.Column(db.String(200))
    adet = db.Column(db.Integer, nullable=False, default=1)
    birim_fiyat = db.Column(db.Numeric(12, 2))
    toplam_fiyat = db.Column(db.Numeric(12, 2))
    created_at = db.Column(db.DateTime, default=datetime.now)
    customer = db.relationship('Customer', backref='teklifler', lazy='joined')
    hazirlayan = db.relationship('User', backref='hazirladigi_teklifler', lazy='joined', foreign_keys=[hazirlayan_user_id])
    kalemler = db.relationship('TeklifKalem', backref='teklif', lazy='select', cascade='all, delete-orphan', order_by='TeklifKalem.sira, TeklifKalem.id')

    @property
    def toplam_genel(self):
        """Kalemlerin toplam fiyatı; kalem yoksa eski tek ürün toplamı."""
        kalem_list = list(self.kalemler) if self.kalemler else []
        if kalem_list:
            return sum((float(k.toplam_fiyat or 0) for k in kalem_list))
        return float(self.toplam_fiyat or 0) if self.toplam_fiyat is not None else None

    @property
    def para_birimi_sembol(self):
        """Para birimi sembolü (kalemsiz eski teklifler için)."""
        return {'TL': '₺', 'USD': '$', 'EUR': '€', 'GBP': '£'}.get(self.para_birimi or 'TL', '₺')

    @property
    def toplam_ozet_metin(self):
        """Liste/gösterim için toplam metni. Kalemler farklı para birimlerindeyse alt toplamları gösterir."""
        kalem_list = list(self.kalemler) if self.kalemler else []
        if not kalem_list:
            t = float(self.toplam_fiyat or 0) if self.toplam_fiyat is not None else None
            if t is not None:
                return f"{t:.2f} {self.para_birimi_sembol}"
            return "-"
        by_curr = {}
        for k in kalem_list:
            curr = k.para_birimi or 'TL'
            by_curr[curr] = by_curr.get(curr, 0) + float(k.toplam_fiyat or 0)
        semboller = {'TL': '₺', 'USD': '$', 'EUR': '€', 'GBP': '£'}
        parts = [f"{v:.2f} {semboller.get(c, c)}" for c, v in sorted(by_curr.items())]
        return " + ".join(parts)

    @property
    def toplam_sayisal(self):
        """Tüm kalemlerin toplam tutarlarının sayısal toplamı (para birimi dönüşümü yapılmadan)."""
        kalem_list = list(self.kalemler) if self.kalemler else []
        if kalem_list:
            return sum(float(k.toplam_fiyat or 0) for k in kalem_list)
        if self.toplam_fiyat is not None:
            return float(self.toplam_fiyat)
        return None


class TeklifKalem(db.Model):
    """Teklif kalemleri – birden çok ürün satırı."""
    __tablename__ = 'teklif_kalemler'
    id = db.Column(db.Integer, primary_key=True)
    teklif_no = db.Column(db.String(20), db.ForeignKey('teklifler.teklif_no', ondelete='CASCADE'), nullable=False)
    sira = db.Column(db.Integer, default=0)
    urun_marka = db.Column(db.String(200))
    urun_model = db.Column(db.String(200))
    adet = db.Column(db.Integer, nullable=False, default=1)
    para_birimi = db.Column(db.String(5), nullable=False, default='TL')  # TL, USD, EUR, GBP
    garanti_suresi = db.Column(db.String(20))  # 6_ay, 1_yil, 2_yil
    kdv_durum = db.Column(db.String(20))  # dahil, dahil_degil
    birim_fiyat = db.Column(db.Numeric(12, 2))
    toplam_fiyat = db.Column(db.Numeric(12, 2))

    @property
    def para_birimi_sembol(self):
        return {'TL': '₺', 'USD': '$', 'EUR': '€', 'GBP': '£'}.get(self.para_birimi or 'TL', '₺')

    @property
    def garanti_suresi_metin(self):
        return {'6_ay': '6 ay', '1_yil': '1 yıl', '2_yil': '2 yıl'}.get(self.garanti_suresi or '', self.garanti_suresi or '-')

    @property
    def kdv_durum_metin(self):
        return {'dahil': 'KDV dahildir', 'dahil_degil': 'KDV dahil değildir'}.get(self.kdv_durum or '', self.kdv_durum or '-')


class SaleSerial(db.Model):
    """Satış kalemine ait seri numarası (adet bazlı)."""
    __tablename__ = 'satis_seri'
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('satis_gonderim.id'), nullable=False)
    seri_no = db.Column(db.String(100), nullable=False)
