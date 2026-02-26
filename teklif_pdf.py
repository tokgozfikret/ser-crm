# -*- coding: utf-8 -*-
"""Teklif PDF oluşturma."""
import os
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image


def _get_font_name():
    """Türkçe karakter desteği için TTF dene; yoksa Helvetica."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import os
        for path in [
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'verdana.ttf'),
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'arial.ttf'),
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        ]:
            if path and os.path.isfile(path):
                name = 'TeklifFont'
                pdfmetrics.registerFont(TTFont(name, path))
                return name
    except Exception:
        pass
    return 'Helvetica'


def _get_font_name_bold():
    """Bold TTF dene; yoksa None (regular kullanılır)."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        for path in [
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'verdanab.ttf'),
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'arialbd.ttf'),
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        ]:
            if path and os.path.isfile(path):
                name = 'TeklifFontBold'
                pdfmetrics.registerFont(TTFont(name, path))
                return name
    except Exception:
        pass
    return None


def build_teklif_pdf(teklif):
    """
    Teklif için PDF oluşturur.
    Returns: bytes
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=15*mm, leftMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm
    )
    font_name = _get_font_name()
    font_name_bold = _get_font_name_bold() or font_name
    styles = getSampleStyleSheet()
    custom = ParagraphStyle(
        name='Custom',
        fontName=font_name,
        fontSize=10,
        leading=12,
    )
    custom_title = ParagraphStyle(
        name='CustomTitle',
        fontName=font_name,
        fontSize=14,
        leading=18,
    )
    custom_small = ParagraphStyle(
        name='CustomSmall',
        fontName=font_name,
        fontSize=9,
        leading=11,
    )
    custom_footer = ParagraphStyle(
        name='CustomFooter',
        fontName=font_name,
        fontSize=8,
        leading=10,
        alignment=1,  # TA_CENTER = 1
    )

    def p(text, style=custom):
        if not text:
            text = '-'
        return Paragraph(str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), style)

    elements = []

    # Şirket logosu (static/images/ser_logo.png varsa)
    logo_path = None
    try:
        from flask import current_app
        logo_path = os.path.join(current_app.static_folder, 'images', 'ser_logo.png')
    except Exception:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logo_path = os.path.join(_root, 'static', 'images', 'ser_logo.png')
    if logo_path and os.path.isfile(logo_path):
        try:
            # A4 kullanılabilir genişlik 180mm (kenar boşlukları 15mm). Logo oranını koruyarak sığdır.
            img = Image(logo_path, width=180*mm, height=50*mm)
            elements.append(img)
            elements.append(Spacer(1, 8))
        except Exception:
            logo_path = None

    # Başlık
    elements.append(p('TEKLİF', custom_title))
    elements.append(Spacer(1, 6))
    elements.append(p('%s - %s' % (teklif.teklif_no, teklif.firma_adi or ''), custom))
    elements.append(Spacer(1, 4))
    tarih = teklif.teklif_tarihi.strftime('%d.%m.%Y') if teklif.teklif_tarihi else '-'
    elements.append(p('Teklif tarihi: %s' % tarih, custom_small))
    elements.append(Spacer(1, 10))

    # Firma bilgileri
    gecerlilik = teklif.teklif_gecerlilik_tarihi.strftime('%d.%m.%Y') if getattr(teklif, 'teklif_gecerlilik_tarihi', None) else '-'
    info_data = [
        ['Teklif no', teklif.teklif_no or '-'],
        ['Firma', (teklif.firma_adi or '-')[:80]],
        ['Yetkili', (teklif.yetkilisi or '-')[:60]],
        ['Telefon', (teklif.telefon or '-')[:40]],
        ['E-posta', (teklif.email or '-')[:60]],
        ['Adres', (teklif.adres or '-')[:100]],
        ['Ödeme süresi', (getattr(teklif, 'odeme_suresi', None) or '-')[:60]],
        ['Teklif geçerlilik', gecerlilik],
        ['Teslimat zamanı', (getattr(teklif, 'teslimat_zamani', None) or '-')[:80]],
        ['Kur bilgisi', 'T.C. Merkez Bankası döviz satış kuru esas alınır.'],
    ]
    info_table = Table(info_data, colWidths=[42*mm, 133*mm])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f0f0')),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#333333')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 12))

    # Ürünler tablosu
    kalem_list = list(teklif.kalemler) if teklif.kalemler else []
    if kalem_list:
        header = ['#', 'Marka', 'Model', 'Adet', 'Garanti', 'KDV', 'Para', 'Birim fiyat', 'Toplam']
        rows = [header]
        for i, k in enumerate(kalem_list, 1):
            bf = '%.2f %s' % (float(k.birim_fiyat or 0), k.para_birimi_sembol) if k.birim_fiyat is not None else '-'
            tf = '%.2f %s' % (float(k.toplam_fiyat or 0), k.para_birimi_sembol) if k.toplam_fiyat is not None else '-'
            garanti_metin = getattr(k, 'garanti_suresi_metin', None) or (getattr(k, 'garanti_suresi', None) or '-')
            kdv_metin = getattr(k, 'kdv_durum_metin', None) or (getattr(k, 'kdv_durum', None) or '-')
            if isinstance(garanti_metin, str) and len(garanti_metin) > 14:
                garanti_metin = garanti_metin[:14]
            if isinstance(kdv_metin, str) and len(kdv_metin) > 16:
                kdv_metin = kdv_metin[:16]
            rows.append([
                str(i),
                (k.urun_marka or '-')[:18],
                (k.urun_model or '-')[:18],
                str(k.adet),
                garanti_metin,
                kdv_metin,
                (k.para_birimi or 'TL')[:6],
                bf,
                tf,
            ])
        # Genel toplam satırı: Toplam sütununun altında
        rows.append(['', '', '', '', '', '', '', 'Genel toplam', (teklif.toplam_ozet_metin or '-')[:28]])
        # Sütun sırası: #, Marka, Model, Adet, Garanti, KDV, Para, Birim fiyat, Toplam (toplam 180mm)
        col_widths = [6*mm, 18*mm, 18*mm, 11*mm, 22*mm, 32*mm, 10*mm, 28*mm, 30*mm]
        tbl = Table(rows, colWidths=col_widths)
        tbl.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (5, -1), 'LEFT'),
            ('ALIGN', (6, 0), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f8f8')]),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e8e8e8')),
            ('FONTSIZE', (7, -1), (-1, -1), 9),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(tbl)
    else:
        # Eski tek ürün
        elements.append(p('Marka: %s' % (teklif.urun_marka or '-'), custom))
        elements.append(p('Model: %s' % (teklif.urun_model or '-'), custom))
        elements.append(p('Adet: %s' % teklif.adet, custom))
        bf = '%.2f %s' % (float(teklif.birim_fiyat or 0), teklif.para_birimi_sembol) if teklif.birim_fiyat is not None else '-'
        tf = '%.2f %s' % (float(teklif.toplam_fiyat or 0), teklif.para_birimi_sembol) if teklif.toplam_fiyat is not None else '-'
        elements.append(p('Birim fiyat: %s' % bf, custom))
        elements.append(p('Toplam: %s' % tf, custom))

    if getattr(teklif, 'aciklama', None):
        elements.append(Spacer(1, 10))
        elements.append(p('Açıklama: %s' % (teklif.aciklama or '')[:500], custom_small))

    # Her sayfada: altta turuncu çizgi + adres/telefon
    def draw_footer(canvas, doc):
        from reportlab.pdfbase import pdfmetrics
        canvas.saveState()
        page_w, page_h = A4

        # Footer çizgi + adres
        left = 15 * mm
        right = page_w - (15 * mm)
        line_y = 38
        canvas.setStrokeColor(colors.HexColor('#F97316'))
        canvas.setLineWidth(1)
        canvas.line(left, line_y, right, line_y)
        canvas.setFillColor(colors.black)
        footer_font = font_name_bold
        footer_size = 9
        canvas.setFont(footer_font, footer_size)
        addr_text = "Büyükesat Mah. Çayhane Sk. Kozlar Apt. No:20/1 Gaziosmanpaşa Çankaya/ANKARA"
        addr_w = pdfmetrics.stringWidth(addr_text, footer_font, footer_size)
        canvas.drawString((page_w - addr_w) / 2, 26, addr_text)
        tel_text = "Tel:  +90 (312) 446 45 73"
        tel_w = pdfmetrics.stringWidth(tel_text, footer_font, footer_size)
        canvas.drawString((page_w - tel_w) / 2, 18, tel_text)
        canvas.restoreState()

    doc.build(elements, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
