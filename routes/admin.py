# -*- coding: utf-8 -*-
import os
import json
from datetime import datetime, timedelta, time
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from sqlalchemy import func, or_, and_
from sqlalchemy.sql.expression import distinct

from extensions import db
from logging_utils import log_event
from models import User, Device, Sale, Inventory, MailConfig, Customer, Fault, Teklif


admin_bp = Blueprint('admin', __name__)


def _parse_admin_date_range():
    """Admin paneli için tarih aralığı parse et."""
    start_str = request.args.get('tarih_baslangic', '').strip() or None
    end_str = request.args.get('tarih_bitis', '').strip() or None
    start_dt = end_dt = None
    if start_str:
        try:
            start_dt = datetime.combine(datetime.strptime(start_str, '%Y-%m-%d').date(), time.min)
        except ValueError:
            start_dt = None
    if end_str:
        try:
            end_dt = datetime.combine(datetime.strptime(end_str, '%Y-%m-%d').date(), time.max)
        except ValueError:
            end_dt = None
    return start_str, end_str, start_dt, end_dt


def admin_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Bu sayfaya erişim yetkiniz yok.', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)

    return inner


@admin_bp.route('/')
@login_required
@admin_required
def index():
    # Tarih filtresi
    tarih_baslangic, tarih_bitis, start_dt, end_dt = _parse_admin_date_range()
    if start_dt and end_dt and start_dt > end_dt:
        flash('Başlangıç tarihi, bitiş tarihinden sonra olamaz.', 'error')
        start_dt = end_dt = None

    # Kullanıcı istatistikleri (tarih filtresi uygulanmaz)
    user_count = User.query.count()
    role_counts_raw = db.session.query(User.role, func.count(User.id)).group_by(User.role).all()
    role_counts = {role: count for role, count in role_counts_raw}

    # Cihaz istatistikleri (tarih filtresi: Device.created_at)
    device_q = Device.query
    if start_dt:
        device_q = device_q.filter(Device.created_at >= start_dt)
    if end_dt:
        device_q = device_q.filter(Device.created_at <= end_dt)

    device_count = device_q.count()
    device_status_raw = device_q.with_entities(Device.durum, func.count(Device.id)).group_by(Device.durum).all()
    device_status = {durum: count for durum, count in device_status_raw}
    waiting_devices = device_status.get('geldi', 0)
    technical_devices = device_status.get('teknik_serviste', 0)
    ready_to_ship_devices = device_status.get('arizalar_tamamlandi', 0)
    shipped_devices = device_status.get('kargolandı', 0)
    open_devices = device_count - shipped_devices

    # Teknik servis için arıza durumu istatistikleri
    fault_status_counts = {
        'hic_girilmemis': 0,
        'girildi_yapilmamis': 0,
        'tamam_teslim_bekliyor': 0,
        'sekreterlikte': 0,
    }
    tech_devices_q = device_q.filter(Device.durum.in_(['teknik_serviste', 'arizalar_tamamlandi']))
    tech_devices = tech_devices_q.all()
    for dev in tech_devices:
        if dev.durum == 'arizalar_tamamlandi':
            key = 'sekreterlikte'
        else:
            total_faults = dev.faults.count()
            if total_faults == 0:
                key = 'hic_girilmemis'
            else:
                open_faults = dev.faults.filter_by(yapildi=False).count()
                if open_faults > 0:
                    key = 'girildi_yapilmamis'
                else:
                    key = 'tamam_teslim_bekliyor'
        fault_status_counts[key] = fault_status_counts.get(key, 0) + 1

    # Satış / gönderim istatistikleri (tarih filtresi: Sale.created_at)
    sale_q = Sale.query
    if start_dt:
        sale_q = sale_q.filter(Sale.created_at >= start_dt)
    if end_dt:
        sale_q = sale_q.filter(Sale.created_at <= end_dt)

    sale_count = sale_q.count()
    total_quantity = sale_q.with_entities(func.coalesce(func.sum(Sale.miktar), 0)).scalar() or 0
    sales_customer_count = (
        sale_q.with_entities(Sale.musteri_adi, Sale.musteri_telefon, Sale.musteri_email)
        .distinct()
        .count()
    )
    if start_dt or end_dt:
        recent_sales_count = sale_count
        recent_sales_quantity = int(total_quantity or 0)
    else:
        now = datetime.now()
        last_30_days = now - timedelta(days=30)
        recent_sales_q = sale_q.filter(Sale.created_at >= last_30_days)
        recent_sales_count = recent_sales_q.count()
        recent_sales_quantity = (
            recent_sales_q.with_entities(func.coalesce(func.sum(Sale.miktar), 0)).scalar() or 0
        )
    # Satış türü dağılımı (satis / kiralama)
    sale_type_raw = sale_q.with_entities(Sale.tur, func.count(Sale.id)).group_by(Sale.tur).all()
    sale_type_counts = {tur: count for tur, count in sale_type_raw}

    # Satış istatistikleri – en çok gönderilen markalar / gruplar / müşteriler
    stats_query = sale_q.join(Inventory)
    top_brands_rows = stats_query.with_entities(
        Inventory.marka.label('marka'),
        func.sum(Sale.miktar).label('toplam'),
    ).filter(
        Inventory.marka.isnot(None),
        Inventory.marka != '',
    ).group_by(Inventory.marka).order_by(func.sum(Sale.miktar).desc()).limit(10).all()
    top_brands = [
        {'marka': row.marka, 'toplam': int(row.toplam or 0)} for row in top_brands_rows
    ]

    top_groups_rows = stats_query.with_entities(
        Inventory.urun_grubu.label('urun_grubu'),
        func.sum(Sale.miktar).label('toplam'),
    ).filter(
        Inventory.urun_grubu.isnot(None),
        Inventory.urun_grubu != '',
    ).group_by(Inventory.urun_grubu).order_by(func.sum(Sale.miktar).desc()).limit(10).all()
    top_groups = [
        {'urun_grubu': row.urun_grubu, 'toplam': int(row.toplam or 0)} for row in top_groups_rows
    ]

    top_models_rows = stats_query.with_entities(
        Inventory.model.label('model'),
        func.sum(Sale.miktar).label('toplam'),
    ).filter(
        Inventory.model.isnot(None),
        Inventory.model != '',
    ).group_by(Inventory.model).order_by(func.sum(Sale.miktar).desc()).limit(10).all()
    top_models = [
        {'model': str(row.model or ''), 'toplam': int(row.toplam or 0)} for row in top_models_rows
    ]

    top_customers_rows = stats_query.with_entities(
        Sale.musteri_adi.label('musteri_adi'),
        Sale.musteri_telefon.label('musteri_telefon'),
        func.count(Sale.id).label('gonderim_sayisi'),
    ).group_by(
        Sale.musteri_adi,
        Sale.musteri_telefon,
    ).order_by(func.count(Sale.id).desc()).limit(10).all()
    top_customers = [
        {
            'musteri_adi': row.musteri_adi,
            'musteri_telefon': row.musteri_telefon or '',
            'gonderim_sayisi': int(row.gonderim_sayisi or 0),
        }
        for row in top_customers_rows
    ]

    # En çok satış yapılan ilk 10 gün
    top_days_rows = sale_q.with_entities(
        func.date(Sale.created_at).label('tarih'),
        func.sum(Sale.miktar).label('toplam'),
    ).group_by(func.date(Sale.created_at)).order_by(
        func.sum(Sale.miktar).desc()
    ).limit(10).all()
    top_days = []
    for row in top_days_rows:
        tarih_str = ''
        if row.tarih:
            try:
                dt = datetime.strptime(str(row.tarih), '%Y-%m-%d')
                tarih_str = dt.strftime('%d.%m.%Y')
            except (ValueError, TypeError):
                tarih_str = str(row.tarih)
        top_days.append({'gun': tarih_str, 'toplam': int(row.toplam or 0)})

    # En çok satış yapılan ilk 10 ay (YYYY-MM formatında grupla)
    AY_ADLARI = ('', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
                 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık')
    top_months_rows = sale_q.with_entities(
        func.strftime('%Y', Sale.created_at).label('yil'),
        func.strftime('%m', Sale.created_at).label('ay'),
        func.sum(Sale.miktar).label('toplam'),
    ).filter(
        Sale.created_at.isnot(None),
    ).group_by(
        func.strftime('%Y', Sale.created_at),
        func.strftime('%m', Sale.created_at),
    ).order_by(func.sum(Sale.miktar).desc()).limit(10).all()
    top_months = []
    for row in top_months_rows:
        ay_adi = AY_ADLARI[int(row.ay or 1)] if row.ay else ''
        ay_str = f'{ay_adi} {row.yil or ""}'.strip()
        top_months.append({'ay': ay_str, 'toplam': int(row.toplam or 0)})

    # Aylara göre satış (kronolojik sıra – son 24 ay)
    sales_by_month_rows = sale_q.with_entities(
        func.strftime('%Y', Sale.created_at).label('yil'),
        func.strftime('%m', Sale.created_at).label('ay'),
        func.sum(Sale.miktar).label('toplam'),
    ).filter(
        Sale.created_at.isnot(None),
    ).group_by(
        func.strftime('%Y', Sale.created_at),
        func.strftime('%m', Sale.created_at),
    ).order_by(
        func.strftime('%Y', Sale.created_at).desc(),
        func.strftime('%m', Sale.created_at).desc(),
    ).limit(24).all()
    sales_by_month = []
    for row in reversed(sales_by_month_rows):
        ay_adi = AY_ADLARI[int(row.ay or 1)] if row.ay else ''
        ay_str = f'{ay_adi} {row.yil or ""}'.strip()
        sales_by_month.append({'ay': ay_str, 'toplam': int(row.toplam or 0)})

    sale_stats = {
        'top_brands': top_brands,
        'top_groups': top_groups,
        'top_models': top_models,
        'top_customers': top_customers,
        'top_days': top_days,
        'top_months': top_months,
        'sales_by_month': sales_by_month,
    }

    # Teklif istatistikleri (tarih filtresi: teklif_tarihi)
    teklif_q = Teklif.query
    if start_dt:
        teklif_q = teklif_q.filter(Teklif.teklif_tarihi >= start_dt.date())
    if end_dt:
        teklif_q = teklif_q.filter(Teklif.teklif_tarihi <= end_dt.date())

    teklif_top_days_rows = (
        teklif_q.with_entities(
            Teklif.teklif_tarihi.label('tarih'),
            func.count(Teklif.teklif_no).label('toplam'),
        )
        .filter(Teklif.teklif_tarihi.isnot(None))
        .group_by(Teklif.teklif_tarihi)
        .order_by(func.count(Teklif.teklif_no).desc())
        .limit(10)
        .all()
    )
    teklif_top_days = []
    for row in teklif_top_days_rows:
        tarih_str = row.tarih.strftime('%d.%m.%Y') if row.tarih else '-'
        teklif_top_days.append({'gun': tarih_str, 'toplam': int(row.toplam or 0)})

    teklif_top_months_rows = (
        teklif_q.filter(Teklif.teklif_tarihi.isnot(None))
        .with_entities(
            func.strftime('%Y', Teklif.teklif_tarihi).label('yil'),
            func.strftime('%m', Teklif.teklif_tarihi).label('ay'),
            func.count(Teklif.teklif_no).label('toplam'),
        )
        .group_by(
            func.strftime('%Y', Teklif.teklif_tarihi),
            func.strftime('%m', Teklif.teklif_tarihi),
        )
        .order_by(func.count(Teklif.teklif_no).desc())
        .limit(10)
        .all()
    )
    teklif_top_months = []
    for row in teklif_top_months_rows:
        ay_adi = AY_ADLARI[int(row.ay or 1)] if row.ay else ''
        ay_str = f'{ay_adi} {int(row.yil or 0)}'.strip()
        teklif_top_months.append({'ay': ay_str, 'toplam': int(row.toplam or 0)})

    teklif_top_firmalar_rows = (
        teklif_q.with_entities(
            Teklif.firma_adi.label('firma_adi'),
            func.count(Teklif.teklif_no).label('toplam'),
        )
        .filter(Teklif.firma_adi.isnot(None), Teklif.firma_adi != '')
        .group_by(Teklif.firma_adi)
        .order_by(func.count(Teklif.teklif_no).desc())
        .limit(10)
        .all()
    )
    teklif_top_firmalar = [{'firma_adi': row.firma_adi, 'toplam': int(row.toplam or 0)} for row in teklif_top_firmalar_rows]

    teklif_stats = {
        'teklif_top_days': teklif_top_days,
        'teklif_top_months': teklif_top_months,
        'teklif_top_firmalar': teklif_top_firmalar,
    }

    # Depo stok istatistikleri
    stock_item_count = Inventory.query.count()
    stock_total_quantity = (
        db.session.query(func.coalesce(func.sum(Inventory.miktar), 0)).scalar() or 0
    )
    # Ürün grubuna göre stok dağılımı (ilk 10)
    stock_by_group_raw = (
        db.session.query(Inventory.urun_grubu, func.coalesce(func.sum(Inventory.miktar), 0))
        .filter(Inventory.urun_grubu.isnot(None), Inventory.urun_grubu != '')
        .group_by(Inventory.urun_grubu)
        .order_by(func.sum(Inventory.miktar).desc())
        .limit(10)
        .all()
    )
    stock_by_group = {grp: int(qty or 0) for grp, qty in stock_by_group_raw}

    # Depoda en çok stoğu bulunan markalar (ilk 10)
    stock_by_marka_rows = (
        db.session.query(Inventory.marka, func.coalesce(func.sum(Inventory.miktar), 0).label('toplam'))
        .filter(Inventory.marka.isnot(None), Inventory.marka != '')
        .group_by(Inventory.marka)
        .order_by(func.sum(Inventory.miktar).desc())
        .limit(10)
        .all()
    )
    stock_by_marka = [
        {'marka': str(row.marka or ''), 'toplam': int(row.toplam or 0)} for row in stock_by_marka_rows
    ]

    # Depoda en çok stoğu bulunan modeller (ilk 10)
    stock_by_model_rows = (
        db.session.query(Inventory.model, func.coalesce(func.sum(Inventory.miktar), 0).label('toplam'))
        .filter(Inventory.model.isnot(None))
        .group_by(Inventory.model)
        .order_by(func.sum(Inventory.miktar).desc())
        .limit(10)
        .all()
    )
    stock_by_model = [
        {'model': str(row.model or '').strip(), 'toplam': int(row.toplam or 0)}
        for row in stock_by_model_rows
        if row.model and str(row.model or '').strip()
    ]

    # Teknik servis – tamir istatistikleri
    AY_ADLARI_TS = ('', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
                    'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık')
    tamir_device_q = Device.query.join(Fault).filter(Device.id == Fault.device_id)
    if start_dt:
        tamir_device_q = tamir_device_q.filter(Device.created_at >= start_dt)
    if end_dt:
        tamir_device_q = tamir_device_q.filter(Device.created_at <= end_dt)
    tamir_by_marka_rows = (
        tamir_device_q.with_entities(
            func.coalesce(Device.cihaz_marka, Device.cihaz_bilgisi).label('marka'),
            func.count(distinct(Device.id)).label('toplam'),
        )
        .filter(
            or_(
                Device.cihaz_marka.isnot(None), Device.cihaz_bilgisi.isnot(None),
            ),
        )
        .group_by(func.coalesce(Device.cihaz_marka, Device.cihaz_bilgisi))
        .order_by(func.count(distinct(Device.id)).desc())
        .limit(10)
        .all()
    )
    tamir_by_marka = [{'marka': str(row.marka or '').strip(), 'toplam': int(row.toplam or 0)} for row in tamir_by_marka_rows if row.marka and str(row.marka or '').strip()]

    tamir_by_model_rows = (
        tamir_device_q.with_entities(
            Device.cihaz_model.label('model'),
            func.count(distinct(Device.id)).label('toplam'),
        )
        .filter(Device.cihaz_model.isnot(None), Device.cihaz_model != '')
        .group_by(Device.cihaz_model)
        .order_by(func.count(distinct(Device.id)).desc())
        .limit(10)
        .all()
    )
    tamir_by_model = [{'model': str(row.model or '').strip(), 'toplam': int(row.toplam or 0)} for row in tamir_by_model_rows if row.model and str(row.model or '').strip()]

    tamir_month_base = Device.query.join(Fault).filter(Device.id == Fault.device_id)
    if start_dt:
        tamir_month_base = tamir_month_base.filter(Device.arizalar_tamamlandi_at >= start_dt)
    if end_dt:
        tamir_month_base = tamir_month_base.filter(Device.arizalar_tamamlandi_at <= end_dt)
    tamir_by_month_rows = (
        tamir_month_base.with_entities(
            func.strftime('%Y', Device.arizalar_tamamlandi_at).label('yil'),
            func.strftime('%m', Device.arizalar_tamamlandi_at).label('ay'),
            func.count(distinct(Device.id)).label('toplam'),
        )
        .filter(Device.arizalar_tamamlandi_at.isnot(None))
        .group_by(
            func.strftime('%Y', Device.arizalar_tamamlandi_at),
            func.strftime('%m', Device.arizalar_tamamlandi_at),
        )
        .order_by(
            func.strftime('%Y', Device.arizalar_tamamlandi_at).desc(),
            func.strftime('%m', Device.arizalar_tamamlandi_at).desc(),
        )
        .limit(24)
        .all()
    )
    tamir_by_month = []
    for row in reversed(tamir_by_month_rows):
        ay_adi = AY_ADLARI_TS[int(row.ay or 1)] if row.ay else ''
        ay_str = f'{ay_adi} {row.yil or ""}'.strip()
        tamir_by_month.append({'ay': ay_str, 'toplam': int(row.toplam or 0)})

    tamir_stats = {
        'tamir_by_marka': tamir_by_marka,
        'tamir_by_model': tamir_by_model,
        'tamir_by_month': tamir_by_month,
    }

    # Son 10 kullanıcı
    last_users = User.query.order_by(User.id.desc()).limit(10).all()

    # Log dosyalarının son 100 satırı
    auth_log_lines = []
    secretary_log_lines = []
    technical_log_lines = []
    sales_log_lines = []
    admin_log_lines = []
    log_dir = current_app.config.get('LOG_DIR')
    if log_dir:
        def _tail(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return f.readlines()[-100:]
            except FileNotFoundError:
                return []

        auth_log_lines = _tail(os.path.join(log_dir, 'auth_log.txt'))
        secretary_log_lines = _tail(os.path.join(log_dir, 'secretary_log.txt'))
        technical_log_lines = _tail(os.path.join(log_dir, 'technical_log.txt'))
        sales_log_lines = _tail(os.path.join(log_dir, 'sales_log.txt'))
        admin_log_lines = _tail(os.path.join(log_dir, 'admin_log.txt'))

    return render_template(
        'admin/index.html',
        user_count=user_count,
        device_count=device_count,
        open_devices=open_devices,
        sale_count=sale_count,
        last_users=last_users,
        device_status=device_status,
        auth_log_lines=auth_log_lines,
        secretary_log_lines=secretary_log_lines,
        technical_log_lines=technical_log_lines,
        sales_log_lines=sales_log_lines,
        admin_log_lines=admin_log_lines,
        role_counts=role_counts,
        waiting_devices=waiting_devices,
        technical_devices=technical_devices,
        ready_to_ship_devices=ready_to_ship_devices,
        shipped_devices=shipped_devices,
        total_quantity=total_quantity,
        sales_customer_count=sales_customer_count,
        recent_sales_count=recent_sales_count,
        recent_sales_quantity=recent_sales_quantity,
        stock_item_count=stock_item_count,
        stock_total_quantity=stock_total_quantity,
        stock_by_group=stock_by_group,
        stock_by_marka=stock_by_marka,
        stock_by_model=stock_by_model,
        sale_type_counts=sale_type_counts,
        fault_status_counts=fault_status_counts,
        sale_stats=sale_stats,
        teklif_stats=teklif_stats,
        tamir_stats=tamir_stats,
        tarih_baslangic=tarih_baslangic or '',
        tarih_bitis=tarih_bitis or '',
        date_filter_active=bool(start_dt or end_dt),
    )


@admin_bp.route('/musteriler')
@login_required
@admin_required
def musteriler():
    """Müşteri yönetimi – müşteri listesi."""
    q = (request.args.get('q') or '').strip()
    page = request.args.get('page', 1, type=int)
    sorgu = Customer.query.order_by(Customer.ad.asc())
    if q:
        arama = f'%{q}%'
        sorgu = sorgu.filter(
            or_(
                Customer.ad.ilike(arama),
                Customer.yetkili_kisi.ilike(arama),
                Customer.telefon.ilike(arama),
                Customer.email.ilike(arama),
                Customer.adres.ilike(arama),
                Customer.lokasyon_proje.ilike(arama),
            )
        )
    pagination = sorgu.paginate(page=page, per_page=25, error_out=False)
    # Her müşteri için cihaz ve satış sayıları
    for m in pagination.items:
        m.device_count = Device.query.filter(
            or_(
                Device.customer_id == m.id,
                and_(
                    Device.musteri_adi == m.ad,
                    (Device.musteri_telefon == m.telefon) if m.telefon else Device.musteri_telefon.is_(None),
                ),
            )
        ).count()
        m.sale_count = Sale.query.filter(
            or_(
                Sale.customer_id == m.id,
                and_(
                    Sale.musteri_adi == m.ad,
                    (Sale.musteri_telefon == m.telefon) if m.telefon else Sale.musteri_telefon.is_(None),
                ),
            )
        ).count()
    return render_template(
        'admin/musteriler.html',
        pagination=pagination,
        arama=q,
    )


@admin_bp.route('/musteriler/<int:id>/duzenle', methods=['GET', 'POST'])
@login_required
@admin_required
def musteri_duzenle(id):
    """Müşteri bilgilerini düzenle – e-posta, telefon, adres vb."""
    musteri = Customer.query.get_or_404(id)
    if request.method == 'POST':
        ad_yeni = (request.form.get('ad') or '').strip()
        if not ad_yeni:
            flash('Firma adı zorunludur.', 'error')
            return render_template('admin/musteri_duzenle.html', musteri=musteri)
        musteri.ad = ad_yeni
        musteri.yetkili_kisi = (request.form.get('yetkili_kisi') or '').strip() or None
        musteri.telefon = (request.form.get('telefon') or '').strip() or None
        musteri.email = (request.form.get('email') or '').strip() or None
        musteri.adres = (request.form.get('adres') or '').strip() or None
        musteri.lokasyon_proje = (request.form.get('lokasyon_proje') or '').strip() or None
        musteri.notlar = (request.form.get('notlar') or '').strip() or None
        for c in Device.query.filter(Device.customer_id == musteri.id).all():
            c.musteri_adi = musteri.ad
            c.musteri_telefon = musteri.telefon
            c.musteri_adres = musteri.adres
            c.musteri_email = musteri.email
        for s in Sale.query.filter(Sale.customer_id == musteri.id).all():
            s.musteri_adi = musteri.ad
            s.musteri_telefon = musteri.telefon
            s.musteri_adres = musteri.adres
            s.musteri_email = musteri.email
        db.session.commit()
        log_event('admin', 'customer_update', extra=f'customer_id={musteri.id}')
        flash('Firma bilgileri güncellendi.', 'success')
        return redirect(url_for('admin.musteri_detay', id=musteri.id))
    return render_template('admin/musteri_duzenle.html', musteri=musteri)


@admin_bp.route('/musteriler/<int:id>')
@login_required
@admin_required
def musteri_detay(id):
    """Müşteri detayı – tamir cihazları ve satış/kiralama kayıtları."""
    musteri = Customer.query.get_or_404(id)
    # Tamir için gönderilen cihazlar (customer_id veya musteri_adi+telefon eşleşmesi)
    tel_match = (Device.musteri_telefon == musteri.telefon) if musteri.telefon else Device.musteri_telefon.is_(None)
    cihazlar = Device.query.filter(
        or_(
            Device.customer_id == musteri.id,
            and_(Device.musteri_adi == musteri.ad, tel_match),
        )
    ).order_by(Device.created_at.desc()).all()
    tel_match_sale = (Sale.musteri_telefon == musteri.telefon) if musteri.telefon else Sale.musteri_telefon.is_(None)
    satislar = Sale.query.filter(
        or_(
            Sale.customer_id == musteri.id,
            and_(Sale.musteri_adi == musteri.ad, tel_match_sale),
        )
    ).order_by(Sale.created_at.desc()).all()
    return render_template(
        'admin/musteri_detay.html',
        musteri=musteri,
        cihazlar=cihazlar,
        satislar=satislar,
    )


@admin_bp.route('/mail-ayarlari', methods=['GET', 'POST'])
@login_required
@admin_required
def mail_settings():
    cfg = MailConfig.query.first()
    if not cfg:
        cfg = MailConfig(smtp_port=587, use_tls=True)
        db.session.add(cfg)
        db.session.commit()
    if request.method == 'POST':
        cfg.smtp_host = (request.form.get('smtp_host') or '').strip() or None
        try:
            cfg.smtp_port = int(request.form.get('smtp_port') or 587)
        except ValueError:
            cfg.smtp_port = 587
        cfg.smtp_username = (request.form.get('smtp_username') or '').strip() or None
        pw = (request.form.get('smtp_password') or '').strip()
        if pw:
            cfg.smtp_password = pw
        cfg.from_email = (request.form.get('from_email') or '').strip() or None
        cfg.use_tls = request.form.get('use_tls') == '1'
        db.session.commit()
        flash('Mail ayarları kaydedildi.', 'success')
        return redirect(url_for('admin.mail_settings'))
    return render_template('admin/mail_settings.html', cfg=cfg)


@admin_bp.route('/kullanicilar')
@login_required
@admin_required
def users():
    users = User.query.order_by(User.id.asc()).all()

    # Log dosyalarının son 100 satırı (kullanıcı yönetimi sekmesi için)
    auth_log_lines = []
    secretary_log_lines = []
    technical_log_lines = []
    sales_log_lines = []
    admin_log_lines = []
    log_dir = current_app.config.get('LOG_DIR')
    if log_dir:
        def _tail(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return f.readlines()[-100:]
            except FileNotFoundError:
                return []

        auth_log_lines = _tail(os.path.join(log_dir, 'auth_log.txt'))
        secretary_log_lines = _tail(os.path.join(log_dir, 'secretary_log.txt'))
        technical_log_lines = _tail(os.path.join(log_dir, 'technical_log.txt'))
        sales_log_lines = _tail(os.path.join(log_dir, 'sales_log.txt'))
        admin_log_lines = _tail(os.path.join(log_dir, 'admin_log.txt'))

    return render_template(
        'admin/users.html',
        users=users,
        auth_log_lines=auth_log_lines,
        secretary_log_lines=secretary_log_lines,
        technical_log_lines=technical_log_lines,
        sales_log_lines=sales_log_lines,
        admin_log_lines=admin_log_lines,
    )


@admin_bp.route('/kullanicilar/<int:user_id>/sil', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    # Kendi hesabını silmeye çalışıyorsa engelle
    if user.id == current_user.id:
        flash('Kendi hesabınızı silemezsiniz.', 'error')
        return redirect(url_for('admin.users'))

    # Sistemde en az bir admin kalmasını sağla
    if user.role == 'admin':
        other_admins = User.query.filter(User.role == 'admin', User.id != user.id).count()
        if other_admins == 0:
            flash('Sistemde en az bir adet admin kullanıcısı kalmalıdır.', 'error')
            return redirect(url_for('admin.users'))

    log_event('admin', 'user_delete', extra=f'deleted_user={user.username}')
    db.session.delete(user)
    db.session.commit()
    flash('Kullanıcı silindi.', 'success')
    return redirect(url_for('admin.users'))

