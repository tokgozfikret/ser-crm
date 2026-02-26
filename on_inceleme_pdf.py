# -*- coding: utf-8 -*-
"""Ön inceleme raporu PDF oluşturma."""

from io import BytesIO
import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle


PARA_BIRIMI_SEMBOLLER = {'TL': '₺', 'USD': '$', 'EUR': '€', 'GBP': '£'}


def _get_font_name():
    """Türkçe karakter desteği için TTF dene; yoksa Helvetica."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        for path in [
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'verdana.ttf'),
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'arial.ttf'),
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        ]:
            if path and os.path.isfile(path):
                name = 'OnIncelemeFont'
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
                name = 'OnIncelemeFontBold'
                pdfmetrics.registerFont(TTFont(name, path))
                return name
    except Exception:
        pass
    return None


def _fmt_fiyat(v, para_birimi: str | None) -> str:
    if v is None:
        return '–'
    try:
        val = float(v)
    except (TypeError, ValueError):
        return str(v)
    code = (para_birimi or 'TL').upper()
    sembol = PARA_BIRIMI_SEMBOLLER.get(code, code)
    return f"{val:,.2f} {sembol}".replace(',', 'X').replace('.', ',').replace('X', '.')


def build_on_inceleme_pdf(musteri_adi: str, cihazlar) -> bytes:
    """
    Ön inceleme raporu için PDF oluşturur.

    Args:
        musteri_adi: Firma / müşteri adı
        cihazlar: Device nesnelerinin listesi

    Returns:
        PDF içeriği (bytes)
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    font_name = _get_font_name()
    font_name_bold = _get_font_name_bold() or font_name
    base = ParagraphStyle(
        name='OnIncelemeBase',
        fontName=font_name,
        fontSize=10,
        leading=12,
    )
    title_style = ParagraphStyle(
        name='OnIncelemeTitle',
        fontName=font_name_bold,
        fontSize=14,
        leading=18,
    )
    section_title = ParagraphStyle(
        name='OnIncelemeSectionTitle',
        fontName=font_name_bold,
        fontSize=11,
        leading=14,
        spaceBefore=8,
        spaceAfter=4,
    )
    small = ParagraphStyle(
        name='OnIncelemeSmall',
        fontName=font_name,
        fontSize=9,
        leading=11,
        textColor='#64748b',
    )

    def p(text: str, style=base):
        if text is None:
            text = '-'
        # Basit HTML kaçışları
        safe = str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return Paragraph(safe, style)

    elements = []

    # Logo (varsa)
    logo_path = None
    try:
        from flask import current_app

        logo_path = os.path.join(current_app.static_folder, 'images', 'ser_logo.png')
    except Exception:
        _root = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(_root, 'static', 'images', 'ser_logo.png')

    if logo_path and os.path.isfile(logo_path):
        try:
            img = Image(logo_path, width=180 * mm, height=40 * mm)
            elements.append(img)
            elements.append(Spacer(1, 6))
        except Exception:
            logo_path = None

    # Başlık
    elements.append(Spacer(1, 4))
    elements.append(p('ÖN İNCELEME RAPORU', title_style))
    elements.append(Spacer(1, 4))
    elements.append(p(musteri_adi or '-', base))
    elements.append(Spacer(1, 6))

    for idx, cihaz in enumerate(cihazlar, start=1):
        marka = (getattr(cihaz, 'cihaz_marka', '') or getattr(cihaz, 'cihaz_bilgisi', '') or '').strip()
        model = (getattr(cihaz, 'cihaz_model', '') or '').strip()
        seri = (getattr(cihaz, 'seri_no', '') or '').strip()

        header_parts = []
        if marka:
            header_parts.append(marka)
        if model:
            header_parts.append(model)
        if seri:
            header_parts.append(f"Seri: {seri}")
        header_text = ' • '.join(header_parts) if header_parts else 'Cihaz'

        if idx > 1:
            elements.append(Spacer(1, 10))

        elements.append(p(f"Cihaz {idx}", small))
        elements.append(p(header_text, section_title))
        elements.append(Spacer(1, 2))
        elements.append(p("Ön inceleme bulguları (arıza / tahmini fiyat):", small))
        elements.append(Spacer(1, 3))

        faults = list(getattr(cihaz, 'faults', []) or [])
        totals: dict[str, float] = {}

        if faults:
            # Arıza satırları için teklif PDF'ine benzer tablo
            header = ['#', 'Arıza açıklaması', 'Para', 'Tahmini fiyat']
            rows = [header]
            for i, f in enumerate(faults, start=1):
                aciklama = (getattr(f, 'aciklama', '') or '')[:120]
                fiyat = getattr(f, 'fiyat', None)
                pb = (getattr(f, 'para_birimi', None) or 'TL').upper()
                para_kodu = pb[:6]
                tutar_text = _fmt_fiyat(fiyat, pb) if fiyat is not None else '–'
                rows.append([
                    str(i),
                    aciklama or '-',
                    para_kodu,
                    tutar_text,
                ])
                if fiyat is not None:
                    try:
                        totals[pb] = totals.get(pb, 0) + float(fiyat)
                    except (TypeError, ValueError):
                        pass

            # Toplamlar
            if totals:
                order = ['TL', 'USD', 'EUR', 'GBP']
                parts = []
                for code in order:
                    if code in totals:
                        parts.append(_fmt_fiyat(totals[code], code))
                for code, val in totals.items():
                    if code not in order:
                        parts.append(_fmt_fiyat(val, code))
                toplam_text = ' + '.join(parts)
            else:
                toplam_text = '–'

            rows.append(['', '', 'Toplam', toplam_text])

            col_widths = [8 * mm, 102 * mm, 18 * mm, 42 * mm]
            tbl = Table(rows, colWidths=col_widths)
            tbl.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f8f8')]),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e8e8e8')),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            elements.append(tbl)
        else:
            # Arıza yoksa, tek satırlık genel tahmini fiyat tablosu
            fiyat = getattr(cihaz, 'on_inceleme_fiyat', None)
            pb = getattr(cihaz, 'on_inceleme_para_birimi', None) or 'TL'
            toplam_text = _fmt_fiyat(fiyat, pb) if fiyat is not None else '–'
            rows = [
                ['Açıklama', 'Tahmini fiyat'],
                ['Genel tahmini fiyat', toplam_text],
            ]
            tbl = Table(rows, colWidths=[110 * mm, 60 * mm])
            tbl.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f8f8')]),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            elements.append(tbl)

    # Teklif PDF'ine benzer: her sayfada altta adres / telefon
    def draw_footer(canvas, doc):
        from reportlab.pdfbase import pdfmetrics
        canvas.saveState()
        page_w, page_h = A4
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

