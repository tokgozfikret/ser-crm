# -*- coding: utf-8 -*-
import json
from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, Response
from flask_login import login_required, current_user
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import joinedload, selectinload
from models import Inventory, Sale, SaleSerial, UrunGrubu, Customer, Teklif, TeklifKalem, User, Device
from extensions import db
from logging_utils import log_event
from mail_utils import send_email_with_attachment
try:
    from teklif_pdf import build_teklif_pdf
except ModuleNotFoundError:
    build_teklif_pdf = None  # reportlab yüklü değilse: pip install reportlab

sales_bp = Blueprint('sales', __name__)


def sales_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if current_user.role == 'admin':
            return f(*args, **kwargs)
        if current_user.role != 'satis':
            flash('Bu sayfaya erişim yetkiniz yok.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return inner


TAB_VALUES = ('envanter', 'musteri_satis', 'teklif', 'istatistik')

# Stok ekle/düzenle formunda dropdown için varsayılan ürün grupları + veritabanındaki mevcut gruplar
URUN_GRUBU_DEFAULT = ('Telsiz', 'Aksesuar', 'Yedek parça', 'Diğer')


def _get_or_create_customer(ad, telefon=None, adres=None, email=None, lokasyon_proje=None, yetkili_kisi=None):
    """Satış tarafı için müşteri bul/oluştur."""
    ad = (ad or '').strip()
    telefon = (telefon or '').strip() or None
    adres = (adres or '').strip() or None
    email = (email or '').strip() or None
    yetkili_kisi = (yetkili_kisi or '').strip() or None
    if not ad:
        return None
    q = Customer.query.filter(Customer.ad == ad)
    if telefon:
        q = q.filter(Customer.telefon == telefon)
    musteri = q.first()
    if not musteri:
        musteri = Customer(ad=ad, yetkili_kisi=yetkili_kisi, telefon=telefon, adres=adres, email=email, lokasyon_proje=lokasyon_proje)
        db.session.add(musteri)
        db.session.flush()
    else:
        updated = False
        if email and not musteri.email:
            musteri.email = email
            updated = True
        if adres and not musteri.adres:
            musteri.adres = adres
            updated = True
        if yetkili_kisi and not musteri.yetkili_kisi:
            musteri.yetkili_kisi = yetkili_kisi
            updated = True
        if lokasyon_proje and not getattr(musteri, 'lokasyon_proje', None):
            musteri.lokasyon_proje = lokasyon_proje
            updated = True
        if updated:
            db.session.flush()
    return musteri


def _next_teklif_no():
    """Sonraki teklif numarasını üret (TKL-00001 formatı)."""
    last = Teklif.query.order_by(Teklif.created_at.desc()).first()
    if not last:
        num = 1
    else:
        try:
            num = int(last.teklif_no.split('-')[-1]) + 1
        except (ValueError, IndexError):
            num = 1
    return f'TKL-{num:05d}'


def get_urun_grubu_list():
    """Varsayılanlar + stoktan gelen gruplar + sadece listeye eklenen gruplar (tekilleştirilmiş, sıralı)."""
    from_inventory = [
        r[0] for r in db.session.query(Inventory.urun_grubu).filter(
            Inventory.urun_grubu.isnot(None), Inventory.urun_grubu != ''
        ).distinct().all()
    ]
    from_list = [r.ad for r in UrunGrubu.query.order_by(UrunGrubu.ad).all()]
    combined = list(URUN_GRUBU_DEFAULT) + [g for g in from_inventory if g not in URUN_GRUBU_DEFAULT]
    for g in from_list:
        if g not in combined:
            combined.append(g)
    return sorted(combined, key=lambda x: x.lower())

@sales_bp.route('/')
@login_required
@sales_required
def index():
    """Satış paneli ana sayfa – tab: envanter (depo stok) veya musteri_satis (gönderim geçmişi)."""
    tab = request.args.get('tab', 'envanter')
    if tab not in TAB_VALUES:
        tab = 'envanter'
    page = request.args.get('page', 1, type=int)
    per_page = 25
    arama = request.args.get('q', '').strip()

    # Ortak tarih filtre parse fonksiyonu
    from datetime import datetime, time
    def _parse_date_range(prefix):
        start_str = request.args.get(f'{prefix}_baslangic', '').strip() or None
        end_str = request.args.get(f'{prefix}_bitis', '').strip() or None
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

    if tab == 'musteri_satis':
        # Filtreler: tarih aralığı + müşteri adı
        tarih_baslangic, tarih_bitis, start_dt, end_dt = _parse_date_range('tarih')
        musteri_ara = request.args.get('musteri_ara', '').strip() or None

        sorgu = Sale.query.options(joinedload(Sale.inventory))
        # Başlangıç > Bitiş ise uyar ve filtreleri uygulama.
        if start_dt and end_dt and start_dt > end_dt:
            flash('Başlangıç tarihi, bitiş tarihinden sonra olamaz.', 'error')
        else:
            if start_dt:
                sorgu = sorgu.filter(Sale.created_at >= start_dt)
            if end_dt:
                sorgu = sorgu.filter(Sale.created_at <= end_dt)
        if musteri_ara:
            pattern = f'%{musteri_ara}%'
            sorgu = sorgu.filter(Sale.musteri_adi.ilike(pattern))

        # Varsayılan sıralama: en yeni gönderim en üstte (tarih azalan)
        all_sales = sorgu.order_by(Sale.created_at.desc(), Sale.id.desc()).all()

        # Aynı müşteri + aynı dakikadaki (yaklaşık) gönderimleri tek satırda grupla.
        from collections import OrderedDict
        grouped = OrderedDict()
        for s in all_sales:
            minute_ts = s.created_at.replace(second=0, microsecond=0)
            key = (s.musteri_adi, s.musteri_telefon, s.musteri_email, s.musteri_adres, minute_ts)
            if key not in grouped:
                grouped[key] = s

        grouped_list = list(grouped.values())
        total = len(grouped_list)
        start = (page - 1) * per_page
        end = start + per_page
        page_items = grouped_list[start:end]

        class SimplePagination:
            def __init__(self, items, page, per_page, total):
                self.items = items
                self.page = page
                self.per_page = per_page
                self.total = total
                self.pages = (total + per_page - 1) // per_page or 1
                self.has_prev = page > 1
                self.has_next = page < self.pages
                self.prev_num = page - 1
                self.next_num = page + 1

            def iter_pages(self, left_edge=2, left_current=2, right_current=2, right_edge=2):
                pages_end = self.pages + 1
                if pages_end <= 1:
                    return
                left_end = min(1 + left_edge, pages_end)
                for p in range(1, left_end):
                    yield p
                mid_start = max(left_end, self.page - left_current)
                mid_end = min(self.page + right_current + 1, pages_end)
                if mid_start > left_end:
                    yield None
                for p in range(mid_start, mid_end):
                    yield p
                right_start = max(mid_end, pages_end - right_edge)
                if right_start > mid_end:
                    yield None
                for p in range(right_start, pages_end):
                    yield p

        sale_pagination = SimplePagination(page_items, page, per_page, total)
        return render_template(
            'sales/index.html',
            aktif_tab=tab,
            sale_pagination=sale_pagination,
            arama=arama,
            pagination=None,
            urun_grubu_list=[],
            marka_list=[],
            model_list=[],
            marka_models={},
            secili_urun_grubu=None,
            secili_marka=None,
            secili_model=None,
            tarih_baslangic=tarih_baslangic or '',
            tarih_bitis=tarih_bitis or '',
            musteri_ara=musteri_ara or '',
        )

    if tab == 'teklif':
        # Teklif geçmişi – filtreler: tarih aralığı, firma/teklif no arama
        tkl_tarih_baslangic, tkl_tarih_bitis, tkl_start_dt, tkl_end_dt = _parse_date_range('tkl_tarih')
        tkl_ara = request.args.get('tkl_q', '').strip() or None
        sorgu = Teklif.query.options(
            joinedload(Teklif.customer),
            joinedload(Teklif.hazirlayan),
            selectinload(Teklif.kalemler),
        )
        if tkl_start_dt:
            sorgu = sorgu.filter(Teklif.teklif_tarihi >= tkl_start_dt.date())
        if tkl_end_dt:
            sorgu = sorgu.filter(Teklif.teklif_tarihi <= tkl_end_dt.date())
        if tkl_ara:
            pat = f'%{tkl_ara}%'
            sorgu = sorgu.filter(or_(
                Teklif.teklif_no.ilike(pat),
                Teklif.firma_adi.ilike(pat),
                Teklif.referans.ilike(pat),
            ))
        sorgu = sorgu.order_by(Teklif.teklif_tarihi.desc(), Teklif.created_at.desc())
        teklif_pagination = sorgu.paginate(page=page, per_page=per_page, error_out=False)
        return render_template(
            'sales/index.html',
            aktif_tab=tab,
            teklif_pagination=teklif_pagination,
            tkl_tarih_baslangic=tkl_tarih_baslangic or '',
            tkl_tarih_bitis=tkl_tarih_bitis or '',
            tkl_ara=tkl_ara or '',
            arama='',
            pagination=None,
            sale_pagination=None,
            urun_grubu_list=[],
            marka_list=[],
            model_list=[],
            marka_models={},
            secili_urun_grubu=None,
            secili_marka=None,
            secili_model=None,
        )

    if tab == 'istatistik':
        # İstatistikler için tarih filtresi
        ist_tarih_baslangic, ist_tarih_bitis, ist_start_dt, ist_end_dt = _parse_date_range('ist')
        stats_query = Sale.query.join(Inventory)
        if ist_start_dt:
            stats_query = stats_query.filter(Sale.created_at >= ist_start_dt)
        if ist_end_dt:
            stats_query = stats_query.filter(Sale.created_at <= ist_end_dt)

        # Toplamlar
        total_shipments = stats_query.count()
        total_quantity = stats_query.with_entities(db.func.coalesce(db.func.sum(Sale.miktar), 0)).scalar() or 0
        total_customers = stats_query.with_entities(Sale.musteri_adi, Sale.musteri_telefon, Sale.musteri_email).distinct().count()
        total_sales = stats_query.filter(Sale.tur == 'satis').count()
        total_rentals = stats_query.filter(Sale.tur == 'kiralama').count()

        # Günlük zaman serisi (sonuçlar: tarih, toplam_qty, satis_qty, kiralama_qty)
        date_agg = stats_query.with_entities(
            db.func.date(Sale.created_at).label('g'),
            db.func.sum(Sale.miktar).label('toplam'),
            db.func.sum(db.case((Sale.tur == 'satis', Sale.miktar), else_=0)).label('satis'),
            db.func.sum(db.case((Sale.tur == 'kiralama', Sale.miktar), else_=0)).label('kiralama'),
        ).group_by('g').order_by('g').all()
        time_series = [{
            'tarih': row.g,
            'toplam': int(row.toplam or 0),
            'satis': int(row.satis or 0),
            'kiralama': int(row.kiralama or 0),
        } for row in date_agg]

        # En çok gönderilen ürünler (ilk 10)
        top_products = stats_query.with_entities(
            Inventory.urun_adi.label('urun_adi'),
            db.func.sum(Sale.miktar).label('toplam')
        ).group_by(Inventory.id, Inventory.urun_adi).order_by(db.desc('toplam')).limit(10).all()
        top_products_data = [{
            'urun_adi': row.urun_adi,
            'toplam': int(row.toplam or 0),
        } for row in top_products]

        # En çok gönderilen ürün grupları (ilk 10)
        top_groups = stats_query.with_entities(
            Inventory.urun_grubu.label('urun_grubu'),
            db.func.sum(Sale.miktar).label('toplam')
        ).filter(Inventory.urun_grubu.isnot(None), Inventory.urun_grubu != '').group_by(
            Inventory.urun_grubu
        ).order_by(db.desc('toplam')).limit(10).all()
        top_groups_data = [{
            'urun_grubu': row.urun_grubu,
            'toplam': int(row.toplam or 0),
        } for row in top_groups]

        # Satış türü dağılımı (satis / kiralama) – admin ile aynı
        sale_type_raw = stats_query.with_entities(Sale.tur, func.count(Sale.id)).group_by(Sale.tur).all()
        sale_type_counts = {tur: count for tur, count in sale_type_raw}

        # Admin ile aynı satış istatistikleri: top_brands, top_groups, top_models, top_customers, top_days
        top_brands_rows = stats_query.with_entities(
            Inventory.marka.label('marka'),
            func.sum(Sale.miktar).label('toplam'),
        ).filter(
            Inventory.marka.isnot(None),
            Inventory.marka != '',
        ).group_by(Inventory.marka).order_by(func.sum(Sale.miktar).desc()).limit(10).all()
        admin_top_brands = [{'marka': row.marka, 'toplam': int(row.toplam or 0)} for row in top_brands_rows]

        admin_top_groups_rows = stats_query.with_entities(
            Inventory.urun_grubu.label('urun_grubu'),
            func.sum(Sale.miktar).label('toplam'),
        ).filter(
            Inventory.urun_grubu.isnot(None),
            Inventory.urun_grubu != '',
        ).group_by(Inventory.urun_grubu).order_by(func.sum(Sale.miktar).desc()).limit(10).all()
        admin_top_groups = [{'urun_grubu': row.urun_grubu, 'toplam': int(row.toplam or 0)} for row in admin_top_groups_rows]

        admin_top_models_rows = stats_query.with_entities(
            Inventory.model.label('model'),
            func.sum(Sale.miktar).label('toplam'),
        ).filter(
            Inventory.model.isnot(None),
            Inventory.model != '',
        ).group_by(Inventory.model).order_by(func.sum(Sale.miktar).desc()).limit(10).all()
        admin_top_models = [{'model': str(row.model or ''), 'toplam': int(row.toplam or 0)} for row in admin_top_models_rows]

        admin_top_customers_rows = stats_query.with_entities(
            Sale.musteri_adi.label('musteri_adi'),
            Sale.musteri_telefon.label('musteri_telefon'),
            func.count(Sale.id).label('gonderim_sayisi'),
        ).group_by(Sale.musteri_adi, Sale.musteri_telefon).order_by(func.count(Sale.id).desc()).limit(10).all()
        admin_top_customers = [{
            'musteri_adi': row.musteri_adi,
            'musteri_telefon': row.musteri_telefon or '',
            'gonderim_sayisi': int(row.gonderim_sayisi or 0),
        } for row in admin_top_customers_rows]

        admin_top_days_rows = stats_query.with_entities(
            func.date(Sale.created_at).label('tarih'),
            func.sum(Sale.miktar).label('toplam'),
        ).group_by(func.date(Sale.created_at)).order_by(func.sum(Sale.miktar).desc()).limit(10).all()
        admin_top_days = []
        for row in admin_top_days_rows:
            tarih_str = ''
            if row.tarih:
                try:
                    dt = datetime.strptime(str(row.tarih), '%Y-%m-%d')
                    tarih_str = dt.strftime('%d.%m.%Y')
                except (ValueError, TypeError):
                    tarih_str = str(row.tarih)
            admin_top_days.append({'gun': tarih_str, 'toplam': int(row.toplam or 0)})

        AY_ADLARI = ('', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
                     'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık')
        admin_top_months_rows = stats_query.with_entities(
            func.strftime('%Y', Sale.created_at).label('yil'),
            func.strftime('%m', Sale.created_at).label('ay'),
            func.sum(Sale.miktar).label('toplam'),
        ).filter(Sale.created_at.isnot(None)).group_by(
            func.strftime('%Y', Sale.created_at),
            func.strftime('%m', Sale.created_at),
        ).order_by(func.sum(Sale.miktar).desc()).limit(10).all()
        admin_top_months = []
        for row in admin_top_months_rows:
            ay_adi = AY_ADLARI[int(row.ay or 1)] if row.ay else ''
            ay_str = f'{ay_adi} {row.yil or ""}'.strip()
            admin_top_months.append({'ay': ay_str, 'toplam': int(row.toplam or 0)})

        # Aylara göre satış (kronolojik sıra – son 24 ay)
        admin_sales_by_month_rows = stats_query.with_entities(
            func.strftime('%Y', Sale.created_at).label('yil'),
            func.strftime('%m', Sale.created_at).label('ay'),
            func.sum(Sale.miktar).label('toplam'),
        ).filter(Sale.created_at.isnot(None)).group_by(
            func.strftime('%Y', Sale.created_at),
            func.strftime('%m', Sale.created_at),
        ).order_by(
            func.strftime('%Y', Sale.created_at).desc(),
            func.strftime('%m', Sale.created_at).desc(),
        ).limit(24).all()
        admin_sales_by_month = []
        for row in reversed(admin_sales_by_month_rows):
            ay_adi = AY_ADLARI[int(row.ay or 1)] if row.ay else ''
            ay_str = f'{ay_adi} {row.yil or ""}'.strip()
            admin_sales_by_month.append({'ay': ay_str, 'toplam': int(row.toplam or 0)})

        sale_stats = {
            'top_brands': admin_top_brands,
            'top_groups': admin_top_groups,
            'top_models': admin_top_models,
            'top_customers': admin_top_customers,
            'top_days': admin_top_days,
            'top_months': admin_top_months,
            'sales_by_month': admin_sales_by_month,
        }

        # Teklif istatistikleri (istatistik sekmesi tarih filtresi)
        teklif_q = Teklif.query
        if ist_start_dt:
            teklif_q = teklif_q.filter(Teklif.teklif_tarihi >= ist_start_dt.date())
        if ist_end_dt:
            teklif_q = teklif_q.filter(Teklif.teklif_tarihi <= ist_end_dt.date())

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

        # Depo stok grafikleri – admin ile aynı (tarih filtresi yok, güncel stok)
        stock_by_group_raw = (
            db.session.query(Inventory.urun_grubu, func.coalesce(func.sum(Inventory.miktar), 0))
            .filter(Inventory.urun_grubu.isnot(None), Inventory.urun_grubu != '')
            .group_by(Inventory.urun_grubu)
            .order_by(func.sum(Inventory.miktar).desc())
            .limit(10)
            .all()
        )
        stock_by_group = {grp: int(qty or 0) for grp, qty in stock_by_group_raw}

        stock_by_marka_rows = (
            db.session.query(Inventory.marka, func.coalesce(func.sum(Inventory.miktar), 0).label('toplam'))
            .filter(Inventory.marka.isnot(None), Inventory.marka != '')
            .group_by(Inventory.marka)
            .order_by(func.sum(Inventory.miktar).desc())
            .limit(10)
            .all()
        )
        stock_by_marka = [{'marka': str(row.marka or ''), 'toplam': int(row.toplam or 0)} for row in stock_by_marka_rows]

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

        # En çok gönderim yapılan müşteriler (ilk 10)
        top_customers = stats_query.with_entities(
            Sale.musteri_adi.label('musteri_adi'),
            Sale.musteri_telefon.label('musteri_telefon'),
            Sale.musteri_email.label('musteri_email'),
            db.func.count(Sale.id).label('gonderim_sayisi'),
            db.func.sum(Sale.miktar).label('toplam_miktar'),
            db.func.max(Sale.created_at).label('son_tarih'),
        ).group_by(
            Sale.musteri_adi, Sale.musteri_telefon, Sale.musteri_email
        ).order_by(db.desc('gonderim_sayisi')).limit(10).all()
        top_customers_data = [{
            'musteri_adi': row.musteri_adi,
            'musteri_telefon': row.musteri_telefon or '',
            'musteri_email': row.musteri_email or '',
            'gonderim_sayisi': int(row.gonderim_sayisi or 0),
            'toplam_miktar': int(row.toplam_miktar or 0),
            'son_tarih': row.son_tarih.strftime('%Y-%m-%d') if row.son_tarih else '',
        } for row in top_customers]

        stats = {
            'total_shipments': total_shipments,
            'total_quantity': int(total_quantity or 0),
            'total_customers': int(total_customers or 0),
            'total_sales': total_sales,
            'total_rentals': total_rentals,
            'time_series': time_series,
            'top_products': top_products_data,
            'top_groups': top_groups_data,
            'top_customers': top_customers_data,
        }
        stats_json = json.dumps(stats, ensure_ascii=False)
        return render_template(
            'sales/index.html',
            aktif_tab=tab,
            stats=stats,
            stats_json=stats_json,
            ist_tarih_baslangic=ist_tarih_baslangic or '',
            ist_tarih_bitis=ist_tarih_bitis or '',
            arama='',
            pagination=None,
            sale_pagination=None,
            urun_grubu_list=[],
            marka_list=[],
            model_list=[],
            marka_models={},
            secili_urun_grubu=None,
            secili_marka=None,
            secili_model=None,
            sale_type_counts=sale_type_counts,
            sale_stats=sale_stats,
            teklif_stats=teklif_stats,
            stock_by_group=stock_by_group,
            stock_by_marka=stock_by_marka,
            stock_by_model=stock_by_model,
        )

    # tab == 'envanter' – arama + marka/model dropdown filtreleri
    secili_urun_grubu = request.args.get('urun_grubu', '').strip() or None
    secili_marka = request.args.get('marka', '').strip() or None
    secili_model = request.args.get('model', '').strip() or None
    # Arama kutusu boşsa tüm listeye dön (q parametresiz, sayfa 1); marka/model seçimleri korunur
    if not arama and request.args.get('q') is not None:
        return redirect(url_for(
            'sales.index',
            tab='envanter',
            page=1,
            urun_grubu=secili_urun_grubu or '',
            marka=secili_marka or '',
            model=secili_model or ''
        ))

    urun_grubu_list = get_urun_grubu_list()
    # Marka listesi; markaya göre modeller (marka_models[marka] = [model, ...])
    marka_list = db.session.query(Inventory.marka).filter(
        Inventory.marka.isnot(None), Inventory.marka != ''
    ).distinct().order_by(Inventory.marka).all()
    marka_list = [m[0] for m in marka_list]
    marka_models = {}
    for marka, model in db.session.query(Inventory.marka, Inventory.model).filter(
        Inventory.marka.isnot(None), Inventory.marka != '',
        Inventory.model.isnot(None), Inventory.model != ''
    ).distinct().order_by(Inventory.marka, Inventory.model).all():
        marka_models.setdefault(marka, []).append(model)
    for marka in marka_models:
        marka_models[marka] = sorted(marka_models[marka])
    # Model listesi sadece seçili markaya göre
    model_list = marka_models.get(secili_marka, []) if secili_marka else []
    sorgu = Inventory.query.order_by(Inventory.stok_numarasi.desc())
    if secili_urun_grubu:
        sorgu = sorgu.filter(Inventory.urun_grubu == secili_urun_grubu)
    if secili_marka:
        sorgu = sorgu.filter(Inventory.marka == secili_marka)
    if secili_model and secili_marka:
        sorgu = sorgu.filter(Inventory.model == secili_model)
    if arama:
        arama_pattern = f'%{arama}%'
        sorgu = sorgu.filter(or_(
            Inventory.urun_adi.ilike(arama_pattern),
            Inventory.marka.ilike(arama_pattern),
            Inventory.model.ilike(arama_pattern),
        ))
    pagination = sorgu.paginate(page=page, per_page=per_page, error_out=False)
    return render_template(
        'sales/index.html',
        aktif_tab=tab,
        pagination=pagination,
        sale_pagination=None,
        arama=arama,
        urun_grubu_list=urun_grubu_list,
        marka_list=marka_list,
        model_list=model_list,
        marka_models=marka_models,
        secili_urun_grubu=secili_urun_grubu,
        secili_marka=secili_marka,
        secili_model=secili_model,
    )


def get_next_stok_numarasi():
    """Yeni stok kaydı için benzersiz stok numarası üretir (STK-00001, STK-00002, ...)."""
    import re
    rows = db.session.query(Inventory.stok_numarasi).filter(
        Inventory.stok_numarasi.isnot(None),
        Inventory.stok_numarasi != ''
    ).all()
    max_n = 0
    for (sn,) in rows:
        if sn and sn.startswith('STK-'):
            m = re.match(r'STK-(\d+)', sn)
            if m:
                try:
                    max_n = max(max_n, int(m.group(1)))
                except ValueError:
                    pass
    return f'STK-{max_n + 1:05d}'


def _urun_grubu_from_form():
    """Formdan ürün grubu değerini al.

    Not: "Yeni ürün grubu gir" seçeneği modal üzerinden listeye eklenip select'e gerçek değer olarak yazılır.
    Bu nedenle formda '__yeni__' gelirse geçerli bir ürün grubu seçilmemiş sayılır.
    """
    val = request.form.get('urun_grubu', '').strip()
    if val == '__yeni__':
        return None
    return val or None


@sales_bp.route('/urun-grubu-ekle', methods=['POST'])
@login_required
@sales_required
def urun_grubu_ekle():
    """Sadece ürün grubu listesine yeni grup ekler (stok kaydı oluşturmaz). AJAX veya normal form."""
    ad = request.form.get('ad', '').strip()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not ad:
        if is_ajax:
            return jsonify({'success': False, 'error': 'Ürün grubu adı boş olamaz.'}), 400
        flash('Ürün grubu adı boş olamaz.', 'error')
        return redirect(url_for('sales.stok_ekle'))
    if UrunGrubu.query.filter_by(ad=ad).first():
        if is_ajax:
            return jsonify({'success': False, 'error': f'"{ad}" zaten listede.'}), 400
        flash(f'"{ad}" zaten ürün grubu listesinde.', 'info')
        return redirect(url_for('sales.stok_ekle'))
    db.session.add(UrunGrubu(ad=ad))
    db.session.commit()
    if is_ajax:
        return jsonify({'success': True, 'ad': ad})
    flash(f'"{ad}" ürün grubu listesine eklendi.', 'success')
    return redirect(url_for('sales.stok_ekle'))


@sales_bp.route('/stok-ekle', methods=['GET', 'POST'])
@login_required
@sales_required
def stok_ekle():
    """Yeni ürün / stok girişi."""
    urun_grubu_list = get_urun_grubu_list()
    son_eklenenler = Inventory.query.order_by(Inventory.created_at.desc()).limit(10).all()
    if request.method == 'POST':
        urun_adi = request.form.get('urun_adi', '').strip()
        marka = request.form.get('marka', '').strip() or None
        model = request.form.get('model', '').strip() or None
        urun_grubu = _urun_grubu_from_form()
        miktar = request.form.get('miktar', type=int)
        birim = request.form.get('birim', 'adet').strip() or 'adet'
        aciklama = request.form.get('aciklama', '').strip() or None
        if not urun_adi:
            flash('Ürün adı zorunludur.', 'error')
            return render_template('sales/stok_ekle.html', urun_grubu_list=urun_grubu_list, son_eklenenler=son_eklenenler)
        if not urun_grubu:
            flash('Ürün grubu seçiniz.', 'error')
            return render_template('sales/stok_ekle.html', urun_grubu_list=urun_grubu_list, son_eklenenler=son_eklenenler)
        if miktar is None or miktar < 0:
            miktar = 0
        stok_no = get_next_stok_numarasi()
        inv = Inventory(
            stok_numarasi=stok_no,
            urun_adi=urun_adi,
            urun_grubu=urun_grubu,
            marka=marka,
            model=model,
            miktar=miktar,
            birim=birim,
            aciklama=aciklama,
        )
        db.session.add(inv)
        db.session.commit()
        log_event('sales', 'stock_add', extra=f'inventory_id={inv.id} stok_no={stok_no}')
        flash(f'"{urun_adi}" stoka eklendi. Stok no: {stok_no}', 'success')
        # Aynı sayfada ardışık stok ekleyebilmek için stok ekle sayfasında kal.
        return redirect(url_for('sales.stok_ekle'))
    return render_template('sales/stok_ekle.html', urun_grubu_list=urun_grubu_list, son_eklenenler=son_eklenenler)


@sales_bp.route('/stok/<int:id>/duzenle', methods=['GET', 'POST'])
@login_required
@sales_required
def stok_duzenle(id):
    """Stok kalemi düzenle (ürün adı, ürün grubu, marka, model, miktar, birim)."""
    inv = Inventory.query.get_or_404(id)
    urun_grubu_list = get_urun_grubu_list()
    if inv.urun_grubu and inv.urun_grubu not in urun_grubu_list:
        urun_grubu_list = sorted(urun_grubu_list + [inv.urun_grubu], key=lambda x: x.lower())
    if request.method == 'POST':
        urun_adi = request.form.get('urun_adi', '').strip()
        marka = request.form.get('marka', '').strip() or None
        model = request.form.get('model', '').strip() or None
        urun_grubu = _urun_grubu_from_form()
        miktar = request.form.get('miktar', type=int)
        birim = request.form.get('birim', 'adet').strip() or 'adet'
        aciklama = request.form.get('aciklama', '').strip() or None
        if not urun_adi:
            flash('Ürün adı zorunludur.', 'error')
            return render_template('sales/stok_duzenle.html', inv=inv, urun_grubu_list=urun_grubu_list)
        if miktar is None or miktar < 0:
            miktar = 0
        inv.urun_adi = urun_adi
        inv.urun_grubu = urun_grubu
        inv.marka = marka
        inv.model = model
        inv.miktar = miktar
        inv.birim = birim
        inv.aciklama = aciklama
        db.session.commit()
        log_event('sales', 'stock_update', extra=f'inventory_id={inv.id}')
        flash('Stok güncellendi.', 'success')
        return redirect(url_for('sales.index'))
    return render_template('sales/stok_duzenle.html', inv=inv, urun_grubu_list=urun_grubu_list)


@sales_bp.route('/stok/<int:id>/sil', methods=['POST'])
@login_required
@sales_required
def stok_sil(id):
    """Depo stok kalemini sil (ilişkili satış kayıtları cascade ile silinir)."""
    inv = Inventory.query.get_or_404(id)
    marka = inv.marka or '—'
    model = inv.model or '—'
    miktar = inv.miktar
    urun_adi = inv.urun_adi
    db.session.delete(inv)
    db.session.commit()
    log_event('sales', 'stock_delete', extra=f'inventory_id={id}')
    flash(f'"{urun_adi}" (marka: {marka}, model: {model}, miktar: {miktar}) stoktan silindi.', 'success')
    page = request.form.get('return_page') or request.args.get('page', 1, type=int)
    arama = request.form.get('return_q') or request.args.get('q', '')
    secili_urun_grubu = request.form.get('return_urun_grubu') or request.args.get('urun_grubu', '')
    secili_marka = request.form.get('return_marka') or request.args.get('marka', '')
    secili_model = request.form.get('return_model') or request.args.get('model', '')
    return redirect(url_for('sales.index', tab='envanter', page=page, q=arama, urun_grubu=secili_urun_grubu, marka=secili_marka, model=secili_model))


@sales_bp.route('/musteriler')
@login_required
@sales_required
def musteriler():
    """Firma yönetimi – müşteri listesi ve düzenleme."""
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
    for m in pagination.items:
        tel_match = (Sale.musteri_telefon == m.telefon) if m.telefon else Sale.musteri_telefon.is_(None)
        m.sale_count = Sale.query.filter(
            or_(
                Sale.customer_id == m.id,
                and_(Sale.musteri_adi == m.ad, tel_match),
            )
        ).count()
    return render_template(
        'sales/musteriler.html',
        pagination=pagination,
        arama=q,
    )


@sales_bp.route('/musteriler/<int:id>/duzenle', methods=['GET', 'POST'])
@login_required
@sales_required
def musteri_duzenle(id):
    """Müşteri bilgilerini düzenle – e-posta, telefon, adres."""
    musteri = Customer.query.get_or_404(id)
    if request.method == 'POST':
        ad_yeni = (request.form.get('ad') or '').strip()
        if not ad_yeni:
            flash('Firma adı zorunludur.', 'error')
            return render_template('sales/musteri_duzenle.html', musteri=musteri)
        musteri.ad = ad_yeni
        musteri.yetkili_kisi = (request.form.get('yetkili_kisi') or '').strip() or None
        musteri.telefon = (request.form.get('telefon') or '').strip() or None
        musteri.email = (request.form.get('email') or '').strip() or None
        musteri.adres = (request.form.get('adres') or '').strip() or None
        musteri.lokasyon_proje = (request.form.get('lokasyon_proje') or '').strip() or None
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
        log_event('sales', 'customer_update', extra=f'customer_id={musteri.id}')
        flash('Firma bilgileri güncellendi.', 'success')
        return redirect(url_for('sales.musteriler', q=request.form.get('return_q', ''), page=request.form.get('return_page', 1)))
    return render_template('sales/musteri_duzenle.html', musteri=musteri)


@sales_bp.route('/musteriye-gonder', methods=['GET', 'POST'])
@login_required
@sales_required
def musteriye_gonder():
    """Müşteriye ürün gönder – stoktan düş."""
    if request.method == 'POST':
        # Bir müşteri için birden fazla ürün gönderimini destekle.
        inventory_ids_raw = request.form.getlist('inventory_id')
        miktar_list_raw = request.form.getlist('miktar')
        seri_numaralari_raw = request.form.getlist('seri_numaralari')
        customer_id_raw = request.form.get('customer_id', '').strip() or None
        islem_turu = request.form.get('islem_turu', 'satis')
        if islem_turu not in ('satis', 'kiralama'):
            islem_turu = 'satis'
        musteri_adi = request.form.get('musteri_adi', '').strip()
        musteri_adres = request.form.get('musteri_adres', '').strip() or None
        musteri_telefon = request.form.get('musteri_telefon', '').strip() or None
        musteri_email = request.form.get('musteri_email', '').strip() or None
        musteri_lokasyon = request.form.get('musteri_lokasyon', '').strip() or None
        aciklama = request.form.get('aciklama', '').strip() or None
        if not musteri_adi:
            flash('Firma adı zorunludur.', 'error')
            return redirect(url_for('sales.musteriye_gonder'))
        musteri = None
        if customer_id_raw:
            try:
                musteri = Customer.query.get(int(customer_id_raw))
            except (TypeError, ValueError):
                musteri = None
        if not musteri:
            musteri = _get_or_create_customer(musteri_adi, musteri_telefon, musteri_adres, musteri_email, musteri_lokasyon)

        # Formdan gelen satırları normalize et (geçersiz / boş olanları atla).
        items = []
        for i, (inv_id_raw, miktar_raw) in enumerate(zip(inventory_ids_raw, miktar_list_raw)):
            inv_id_raw = (inv_id_raw or '').strip()
            miktar_raw = (miktar_raw or '').strip()
            if not inv_id_raw:
                continue
            try:
                inv_id = int(inv_id_raw)
            except ValueError:
                continue
            try:
                qty = int(miktar_raw)
            except ValueError:
                continue
            if inv_id and qty > 0:
                seri_text = (seri_numaralari_raw[i] if i < len(seri_numaralari_raw) else '').strip()
                seri_list = [s.strip() for s in seri_text.splitlines() if s.strip()]
                items.append((inv_id, qty, seri_list))

        if not items:
            flash('En az bir ürün ve miktar seçmelisiniz.', 'error')
            return redirect(url_for('sales.musteriye_gonder'))

        # Her ürün için toplam istenen miktarı hesapla.
        toplam_istek = {}
        for inv_id, qty, _ in items:
            toplam_istek[inv_id] = toplam_istek.get(inv_id, 0) + qty

        # İlgili stok kayıtlarını tek seferde çek.
        inv_list = Inventory.query.filter(Inventory.id.in_(toplam_istek.keys())).all()
        inv_map = {inv.id: inv for inv in inv_list}

        # Stok ve ürün kontrolleri.
        for inv_id, total_qty in toplam_istek.items():
            inv = inv_map.get(inv_id)
            if not inv:
                flash('Seçilen ürünlerden biri bulunamadı.', 'error')
                return redirect(url_for('sales.musteriye_gonder'))
            if inv.miktar < total_qty:
                flash(
                    f'"{inv.urun_adi}" için yetersiz stok. '
                    f'İstenen toplam: {total_qty} {inv.birim}, mevcut: {inv.miktar} {inv.birim}.',
                    'error'
                )
                return redirect(url_for('sales.musteriye_gonder'))

        # Tüm kontroller geçti, artık satış ve stok güncellemesini tek transaction içinde yap.
        total_lines = 0
        for inv_id, qty, seri_list in items:
            inv = inv_map[inv_id]
            inv.miktar -= qty
            sale = Sale(
                inventory_id=inv.id,
                miktar=qty,
                tur=islem_turu,
                customer_id=musteri.id if musteri else None,
                musteri_adi=musteri_adi,
                musteri_adres=musteri_adres,
                musteri_telefon=musteri_telefon,
                musteri_email=musteri_email,
                aciklama=aciklama
            )
            db.session.add(sale)
            db.session.flush()
            for seri_no in seri_list:
                db.session.add(SaleSerial(sale_id=sale.id, seri_no=seri_no))
            total_lines += 1

        db.session.commit()
        log_event('sales', 'shipment_create', extra=f'lines={total_lines} customer="{musteri_adi}"')
        flash('Seçilen ürünler firmaya gönderildi ve stoktan düşüldü.', 'success')
        return redirect(url_for('sales.index'))
    # GET: Formu göster.

    # Eski gönderim kayıtlarından Customer tablosunu geriye dönük doldur (customer_id boş olanlar).
    missing_sales = Sale.query.filter(Sale.customer_id.is_(None)).all()
    if missing_sales:
        for s in missing_sales:
            cust = _get_or_create_customer(s.musteri_adi, s.musteri_telefon, s.musteri_adres, s.musteri_email, None)
            if cust:
                s.customer_id = cust.id
        db.session.commit()

    # Tüm stok kayıtlarını (miktar 0 olsa bile) listede gösterebilmek için filtreyi kaldır.
    stoklar = Inventory.query.order_by(Inventory.stok_numarasi.desc()).all()
    stoklar_json = json.dumps([{
        'id': inv.id,
        'stok_numarasi': inv.stok_numarasi or '',
        'ad': inv.urun_adi,
        'urun_grubu': inv.urun_grubu or '',
        'marka': inv.marka or '',
        'model': inv.model or '',
        'miktar': inv.miktar,
        'birim': inv.birim
    } for inv in stoklar], ensure_ascii=False)
    # Müşteri seçimi için tüm müşteriler.
    customers = Customer.query.order_by(Customer.ad.asc()).all()
    customers_json = json.dumps([{
        'id': c.id,
        'ad': c.ad,
        'telefon': c.telefon or '',
        'adres': c.adres or '',
        'email': c.email or '',
        'lokasyon_proje': getattr(c, 'lokasyon_proje', '') or '',
    } for c in customers], ensure_ascii=False)
    return render_template('sales/musteriye_gonder.html', stoklar=stoklar, stoklar_json=stoklar_json,
                           customers=customers, customers_json=customers_json)


@sales_bp.route('/gonderim/<int:id>')
@login_required
@sales_required
def gonderim_detay(id):
    """Bir müşteriye yapılan tek gönderim oturumundaki tüm ürünleri göster."""
    base_sale = Sale.query.get_or_404(id)

    from datetime import timedelta

    minute_ts = base_sale.created_at.replace(second=0, microsecond=0)
    next_minute = minute_ts + timedelta(minutes=1)

    # Aynı müşteri + aynı dakikadaki (yaklaşık) tüm satırları al.
    sales = Sale.query.options(
        joinedload(Sale.inventory),
        joinedload(Sale.sale_serials),
        joinedload(Sale.customer),
    ).filter(
        Sale.musteri_adi == base_sale.musteri_adi,
        Sale.musteri_telefon == base_sale.musteri_telefon,
        Sale.musteri_email == base_sale.musteri_email,
        Sale.musteri_adres == base_sale.musteri_adres,
        Sale.created_at >= minute_ts,
        Sale.created_at < next_minute,
    ).order_by(Sale.id.asc()).all()

    toplam_adet = sum(s.miktar or 0 for s in sales)

    page = request.args.get('page', 1, type=int)
    return render_template('sales/gonderim_detay.html',
                           sales=sales,
                           base_sale=base_sale,
                           toplam_adet=toplam_adet,
                           return_page=page)


@sales_bp.route('/gonderim/<int:id>/sil', methods=['POST'])
@login_required
@sales_required
def gonderim_sil(id):
    """Gönderim kaydını sil; miktarı stoka geri ekle. Henüz fiziksel gönderim yapılmadıysa iptal için."""
    sale = Sale.query.get_or_404(id)
    inv = sale.inventory
    inv.miktar += sale.miktar
    db.session.delete(sale)
    db.session.commit()
    log_event('sales', 'shipment_delete', extra=f'sale_id={id} inventory_id={inv.id}')
    flash(f'Gönderim kaydı iptal edildi. {sale.miktar} {inv.birim} "{inv.urun_adi}" stoka geri eklendi.', 'success')
    page = request.form.get('return_page') or request.args.get('page', 1, type=int)
    return redirect(url_for('sales.index', tab='musteri_satis', page=page))


@sales_bp.route('/gonderim-gecmisi')
@login_required
@sales_required
def gonderim_gecmisi():
    """Müşteriye gönderim geçmişi – ana sayfadaki Müşteri satış tabına yönlendir."""
    page = request.args.get('page', 1, type=int)
    return redirect(url_for('sales.index', tab='musteri_satis', page=page))


@sales_bp.route('/teklif-ekle', methods=['GET', 'POST'])
@login_required
@sales_required
def teklif_ekle():
    """Yeni teklif oluştur."""
    satis_personelleri = User.query.filter(User.role == 'satis').order_by(User.username).all()
    if request.method == 'POST':
        firma_adi = (request.form.get('firma_adi') or '').strip()
        if not firma_adi:
            flash('Teklif verilen firma adı zorunludur.', 'error')
            n = _next_teklif_no()
            return render_template('sales/teklif_ekle.html', satis_personelleri=satis_personelleri, next_teklif_no=n)
        yetkilisi = (request.form.get('yetkilisi') or '').strip() or None
        telefon = (request.form.get('telefon') or '').strip() or None
        email = (request.form.get('email') or '').strip() or None
        adres = (request.form.get('adres') or '').strip() or None
        teklif_tarihi_str = (request.form.get('teklif_tarihi') or '').strip()
        if not teklif_tarihi_str:
            flash('Teklif tarihi zorunludur.', 'error')
            return render_template('sales/teklif_ekle.html', satis_personelleri=satis_personelleri, next_teklif_no=_next_teklif_no())
        try:
            teklif_tarihi = datetime.strptime(teklif_tarihi_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Geçersiz teklif tarihi.', 'error')
            return render_template('sales/teklif_ekle.html', satis_personelleri=satis_personelleri, next_teklif_no=_next_teklif_no())
        referans = (request.form.get('referans') or '').strip() or None
        hazirlayan_id = request.form.get('hazirlayan_user_id', type=int) or None
        aciklama = (request.form.get('aciklama') or '').strip() or None
        odeme_suresi = (request.form.get('odeme_suresi') or '').strip() or None
        teslimat_zamani = (request.form.get('teslimat_zamani') or '').strip() or None
        teklif_gecerlilik_str = (request.form.get('teklif_gecerlilik_tarihi') or '').strip()
        teklif_gecerlilik_tarihi = None
        if teklif_gecerlilik_str:
            try:
                teklif_gecerlilik_tarihi = datetime.strptime(teklif_gecerlilik_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        # Çoklu ürün kalemleri
        marka_list = request.form.getlist('kalem_marka')
        model_list = request.form.getlist('kalem_model')
        adet_list = request.form.getlist('kalem_adet')
        para_birimi_list = request.form.getlist('kalem_para_birimi')
        garanti_list = request.form.getlist('kalem_garanti_suresi')
        kdv_list = request.form.getlist('kalem_kdv_durum')
        birim_list = request.form.getlist('kalem_birim_fiyat')
        toplam_list = request.form.getlist('kalem_toplam_fiyat')
        n = max(len(marka_list), len(model_list), len(adet_list), len(para_birimi_list), len(garanti_list), len(kdv_list), len(birim_list), len(toplam_list))
        kalemler = []
        for i in range(n):
            marka = (marka_list[i] if i < len(marka_list) else '').strip() or None
            model = (model_list[i] if i < len(model_list) else '').strip() or None
            if not marka and not model:
                continue
            try:
                adet = int(adet_list[i] if i < len(adet_list) else 1)
                if adet < 1:
                    adet = 1
            except (ValueError, TypeError):
                adet = 1
            birim_fiyat = None
            try:
                bf = (birim_list[i] if i < len(birim_list) else '').strip().replace(',', '.')
                if bf:
                    birim_fiyat = float(bf)
            except (ValueError, TypeError):
                pass
            toplam_fiyat = None
            try:
                tf = (toplam_list[i] if i < len(toplam_list) else '').strip().replace(',', '.')
                if tf:
                    toplam_fiyat = float(tf)
            except (ValueError, TypeError):
                pass
            if birim_fiyat is not None and toplam_fiyat is None:
                toplam_fiyat = float(birim_fiyat) * adet
            elif toplam_fiyat is not None and birim_fiyat is None and adet:
                birim_fiyat = float(toplam_fiyat) / adet
            pb = (para_birimi_list[i] if i < len(para_birimi_list) else 'TL').strip().upper()
            if pb not in ('TL', 'USD', 'EUR', 'GBP'):
                pb = 'TL'
            garanti = (garanti_list[i] if i < len(garanti_list) else '').strip() or None
            if garanti and garanti not in ('6_ay', '1_yil', '2_yil'):
                garanti = None
            kdv = (kdv_list[i] if i < len(kdv_list) else '').strip() or None
            if kdv and kdv not in ('dahil', 'dahil_degil'):
                kdv = None
            kalemler.append({
                'urun_marka': marka,
                'urun_model': model,
                'adet': adet,
                'para_birimi': pb,
                'garanti_suresi': garanti,
                'kdv_durum': kdv,
                'birim_fiyat': birim_fiyat,
                'toplam_fiyat': toplam_fiyat,
            })
        if not kalemler:
            flash('En az bir ürün kalemi girin (marka veya model).', 'error')
            return render_template('sales/teklif_ekle.html', satis_personelleri=satis_personelleri, next_teklif_no=_next_teklif_no())

        musteri = _get_or_create_customer(firma_adi, telefon, adres, email, yetkili_kisi=yetkilisi)
        teklif_no = _next_teklif_no()
        t = Teklif(
            teklif_no=teklif_no,
            customer_id=musteri.id if musteri else None,
            firma_adi=firma_adi,
            yetkilisi=yetkilisi,
            telefon=telefon,
            email=email,
            adres=adres,
            teklif_tarihi=teklif_tarihi,
            referans=referans,
            hazirlayan_user_id=hazirlayan_id,
            aciklama=aciklama,
            odeme_suresi=odeme_suresi,
            teklif_gecerlilik_tarihi=teklif_gecerlilik_tarihi,
            teslimat_zamani=teslimat_zamani,
        )
        db.session.add(t)
        db.session.flush()
        for idx, k in enumerate(kalemler):
            kalem = TeklifKalem(
                teklif_no=teklif_no,
                sira=idx,
                urun_marka=k['urun_marka'],
                urun_model=k['urun_model'],
                adet=k['adet'],
                para_birimi=k['para_birimi'],
                garanti_suresi=k['garanti_suresi'],
                kdv_durum=k['kdv_durum'],
                birim_fiyat=k['birim_fiyat'],
                toplam_fiyat=k['toplam_fiyat'],
            )
            db.session.add(kalem)
        db.session.commit()
        log_event('sales', 'teklif_eklendi', extra=f'teklif_no={teklif_no}')
        flash(f'Teklif {teklif_no} oluşturuldu.', 'success')
        return redirect(url_for('sales.teklif_detay', teklif_no=teklif_no))
    next_no = _next_teklif_no()
    return render_template('sales/teklif_ekle.html', satis_personelleri=satis_personelleri, next_teklif_no=next_no)


@sales_bp.route('/teklif/<teklif_no>')
@login_required
@sales_required
def teklif_detay(teklif_no):
    """Teklif detayı."""
    t = Teklif.query.options(
        joinedload(Teklif.customer),
        joinedload(Teklif.hazirlayan),
        selectinload(Teklif.kalemler),
    ).filter_by(teklif_no=teklif_no).first_or_404()
    return render_template('sales/teklif_detay.html', teklif=t)


@sales_bp.route('/teklif/<teklif_no>/pdf')
@login_required
@sales_required
def teklif_pdf(teklif_no):
    """Teklifi PDF olarak indir."""
    if build_teklif_pdf is None:
        flash('PDF oluşturmak için reportlab kurulmalı: pip install reportlab', 'error')
        return redirect(url_for('sales.teklif_detay', teklif_no=teklif_no))
    t = Teklif.query.options(
        selectinload(Teklif.kalemler),
    ).filter_by(teklif_no=teklif_no).first_or_404()
    pdf_bytes = build_teklif_pdf(t)
    filename = 'Teklif-%s.pdf' % t.teklif_no
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': 'attachment; filename="%s"' % filename}
    )


@sales_bp.route('/teklif/<teklif_no>/e-posta-gonder', methods=['POST'])
@login_required
@sales_required
def teklif_eposta_gonder(teklif_no):
    """Teklifi firma e-posta adresine PDF ekli gönder."""
    if build_teklif_pdf is None:
        flash('PDF oluşturmak için reportlab kurulmalı: pip install reportlab', 'error')
        return redirect(url_for('sales.teklif_detay', teklif_no=teklif_no))
    t = Teklif.query.options(
        selectinload(Teklif.kalemler),
    ).filter_by(teklif_no=teklif_no).first_or_404()
    to_email = (request.form.get('email') or '').strip()
    if not to_email:
        flash('E-posta adresi girin.', 'error')
        return redirect(url_for('sales.teklif_detay', teklif_no=teklif_no))
    pdf_bytes = build_teklif_pdf(t)
    filename = 'Teklif-%s.pdf' % t.teklif_no
    subject = 'Teklif %s - %s' % (t.teklif_no, t.firma_adi or '')
    body_html = (
        '<p>Sayın yetkili,</p>'
        '<p>Ekte <strong>%s</strong> numaralı teklifimizi bulabilirsiniz.</p>'
        '<p>İyi günler dileriz.</p>'
    ) % t.teklif_no
    ok, err = send_email_with_attachment(to_email, subject, body_html, pdf_bytes, filename)
    if ok:
        flash('Teklif e-posta ile gönderildi: %s' % to_email, 'success')
        log_event('sales', 'teklif_email_gonderildi', extra='teklif_no=%s to=%s' % (t.teklif_no, to_email))
    else:
        flash('E-posta gönderilemedi: %s' % err, 'error')
    return redirect(url_for('sales.teklif_detay', teklif_no=teklif_no))
