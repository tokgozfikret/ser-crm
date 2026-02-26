# -*- coding: utf-8 -*-
"""
SER-CRM © 2026 - Cihaz giriş/çıkış ve tamir takip uygulaması
"""
import os
from flask import Flask, redirect, url_for
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash
from sqlalchemy import text

from extensions import db

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'teknik-servis-crm-gizli-anahtar')
csrf = CSRFProtect(app)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'sqlite:///teknik_servis.db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['LOG_DIR'] = os.path.join(os.path.dirname(__file__), 'logs')

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Bu sayfaya erişmek için giriş yapın.'


@login_manager.user_loader
def load_user(user_id):
    from models import User
    return db.session.get(User, int(user_id))


from routes import auth_bp, secretary_bp, technical_bp, sales_bp, admin_bp, musteri_panel_bp
app.register_blueprint(auth_bp, url_prefix='/')
app.register_blueprint(musteri_panel_bp)
app.register_blueprint(secretary_bp, url_prefix='/sekreterlik')
app.register_blueprint(technical_bp, url_prefix='/teknik-servis')
app.register_blueprint(sales_bp, url_prefix='/satis')
app.register_blueprint(admin_bp, url_prefix='/admin')


@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect('/giris')
    if current_user.role == 'admin':
        return redirect('/admin')
    if current_user.role == 'sekreter':
        return redirect('/sekreterlik')
    if current_user.role == 'satis':
        return redirect('/satis')
    return redirect('/teknik-servis')


@app.template_global()
def url_for_pagination(endpoint, page, **params):
    """URL oluşturur; None ve boş değerleri filtreler."""
    filt = {k: v for k, v in params.items() if v is not None and v != ''}
    return url_for(endpoint, page=page, **filt)


def init_db():
    from models import User
    with app.app_context():
        db.create_all()
        # Yeni sütun ekleme (mevcut veritabanı için)
        for stmt in [
            'ALTER TABLE users ADD COLUMN first_name VARCHAR(100)',
            'ALTER TABLE users ADD COLUMN last_name VARCHAR(100)',
            'ALTER TABLE users ADD COLUMN phone VARCHAR(50)',
            'ALTER TABLE users ADD COLUMN email VARCHAR(120)',
            'ALTER TABLE devices ADD COLUMN musteri_adres VARCHAR(500)',
            'ALTER TABLE devices ADD COLUMN musteri_email VARCHAR(120)',
            'ALTER TABLE devices ADD COLUMN cihaz_marka VARCHAR(200)',
            'ALTER TABLE devices ADD COLUMN cihaz_model VARCHAR(200)',
            'ALTER TABLE devices ADD COLUMN kargolandi_at DATETIME',
            'ALTER TABLE devices ADD COLUMN teknik_servise_gonderildi_at DATETIME',
            'ALTER TABLE devices ADD COLUMN arizalar_tamamlandi_at DATETIME',
            'ALTER TABLE devices ADD COLUMN customer_id INTEGER',
            'ALTER TABLE depo_stok ADD COLUMN marka VARCHAR(100)',
            'ALTER TABLE depo_stok ADD COLUMN model VARCHAR(100)',
            'ALTER TABLE depo_stok ADD COLUMN urun_grubu VARCHAR(100)',
            'ALTER TABLE depo_stok ADD COLUMN stok_numarasi VARCHAR(20)',
            'ALTER TABLE satis_gonderim ADD COLUMN musteri_adres VARCHAR(500)',
            'ALTER TABLE satis_gonderim ADD COLUMN musteri_email VARCHAR(120)',
            'ALTER TABLE satis_gonderim ADD COLUMN customer_id INTEGER',
            'ALTER TABLE satis_gonderim ADD COLUMN tur VARCHAR(20) DEFAULT "satis"',
            'ALTER TABLE musteriler ADD COLUMN lokasyon_proje VARCHAR(200)',
            'ALTER TABLE musteriler ADD COLUMN yetkili_kisi VARCHAR(200)',
            'ALTER TABLE devices ADD COLUMN on_inceleme_sekreterlikte_at DATETIME',
            'ALTER TABLE devices ADD COLUMN on_inceleme_raporu_musteriye_at DATETIME',
            'ALTER TABLE devices ADD COLUMN musteri_onay_durumu VARCHAR(20)',
            'ALTER TABLE devices ADD COLUMN musteri_onay_tarihi DATETIME',
            'ALTER TABLE devices ADD COLUMN teknik_servise_tamir_icin_at DATETIME',
            'ALTER TABLE devices ADD COLUMN on_inceleme_fiyat NUMERIC(12,2)',
            'ALTER TABLE faults ADD COLUMN fiyat NUMERIC(12,2)',
            "ALTER TABLE teklifler ADD COLUMN para_birimi VARCHAR(5) DEFAULT 'TL'",
            "ALTER TABLE teklif_kalemler ADD COLUMN para_birimi VARCHAR(5) DEFAULT 'TL'",
            "ALTER TABLE teklifler ADD COLUMN odeme_suresi VARCHAR(200)",
            "ALTER TABLE teklifler ADD COLUMN teklif_gecerlilik_tarihi DATE",
            "ALTER TABLE teklifler ADD COLUMN teslimat_zamani VARCHAR(500)",
            "ALTER TABLE teklif_kalemler ADD COLUMN garanti_suresi VARCHAR(20)",
            "ALTER TABLE teklif_kalemler ADD COLUMN kdv_durum VARCHAR(20)",
            'ALTER TABLE devices ADD COLUMN kontrol_kisi_id INTEGER',
            'ALTER TABLE devices ADD COLUMN kontrol_onaylandi_at DATETIME',
            'ALTER TABLE devices ADD COLUMN tamir_yapan_id INTEGER',
            "ALTER TABLE devices ADD COLUMN on_inceleme_para_birimi VARCHAR(5) DEFAULT 'TL'",
            "ALTER TABLE faults ADD COLUMN para_birimi VARCHAR(5) DEFAULT 'TL'",
        ]:
            try:
                db.session.execute(text(stmt))
                db.session.commit()
            except Exception:
                db.session.rollback()
        # İndeksler (büyük veri setlerinde sorgu hızını artırmak için)
        for idx_stmt in [
            'CREATE INDEX IF NOT EXISTS idx_devices_musteri_adi ON devices (musteri_adi)',
            'CREATE INDEX IF NOT EXISTS idx_devices_musteri_tel ON devices (musteri_telefon)',
            'CREATE INDEX IF NOT EXISTS idx_devices_durum_created ON devices (durum, created_at)',
            'CREATE INDEX IF NOT EXISTS idx_depo_stok_stok_no ON depo_stok (stok_numarasi)',
            'CREATE INDEX IF NOT EXISTS idx_depo_stok_urun_adi ON depo_stok (urun_adi)',
            'CREATE INDEX IF NOT EXISTS idx_depo_stok_marka ON depo_stok (marka)',
            'CREATE INDEX IF NOT EXISTS idx_depo_stok_model ON depo_stok (model)',
            'CREATE INDEX IF NOT EXISTS idx_depo_stok_urun_grubu ON depo_stok (urun_grubu)',
            'CREATE INDEX IF NOT EXISTS idx_satis_created ON satis_gonderim (created_at)',
            'CREATE INDEX IF NOT EXISTS idx_satis_musteri_adi ON satis_gonderim (musteri_adi)',
        ]:
            try:
                db.session.execute(text(idx_stmt))
                db.session.commit()
            except Exception:
                db.session.rollback()

        # Mevcut depo_stok kayıtlarına stok_numarasi ata (STK-00001, STK-00002, ...)
        try:
            from models import Inventory
            for inv in Inventory.query.filter(
                (Inventory.stok_numarasi == None) | (Inventory.stok_numarasi == '')
            ).all():
                inv.stok_numarasi = f'STK-{inv.id:05d}'
            db.session.commit()
        except Exception:
            db.session.rollback()
        # Varsayılan kullanıcılar
        if User.query.count() == 0:
            u1 = User(username='admin', password_hash=generate_password_hash('admin123'), role='admin', email='admin@example.com')
            u2 = User(username='sekreter', password_hash=generate_password_hash('sekreter123'), role='sekreter', email='sekreter@example.com')
            u3 = User(username='teknik', password_hash=generate_password_hash('teknik123'), role='teknik', email='teknik@example.com')
            u4 = User(username='satis', password_hash=generate_password_hash('satis123'), role='satis', email='satis@example.com')
            db.session.add_all([u1, u2, u3, u4])
            db.session.commit()
            print('Varsayılan kullanıcılar: admin/admin123, sekreter/sekreter123, teknik/teknik123, satis/satis123')
        else:
            # Mevcut veritabanında admin veya satis kullanıcısı yoksa ekle
            created_any = False
            if User.query.filter_by(username='admin').first() is None:
                u_admin = User(username='admin', password_hash=generate_password_hash('admin123'), role='admin', email='admin@example.com')
                db.session.add(u_admin)
                created_any = True
                print('Admin kullanıcısı eklendi: admin/admin123')
            if User.query.filter_by(username='satis').first() is None:
                u_satis = User(username='satis', password_hash=generate_password_hash('satis123'), role='satis', email='satis@example.com')
                db.session.add(u_satis)
                created_any = True
                print('Satış kullanıcısı eklendi: satis/satis123')
            if created_any:
                db.session.commit()
        # E-posta eksik olan admin kullanıcısına placeholder ata (şifremi unuttum için)
        admin_user = User.query.filter_by(username='admin').first()
        if admin_user and not admin_user.email:
            admin_user.email = 'admin@example.com'
            db.session.commit()


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8080, debug=True)
