from flask import Flask, request, send_file, jsonify
import io, math, datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, HRFlowable, PageBreak)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import Frame, PageTemplate, BaseDocTemplate
from reportlab.pdfgen import canvas as pdfcanvas

app = Flask(__name__, static_folder='.', static_url_path='')

# ── COLORES ────────────────────────────────────────────────────
AZUL       = colors.HexColor('#0D1B2A')
AZUL2      = colors.HexColor('#162436')
ORO        = colors.HexColor('#C9A84C')
GRIS_CLARO = colors.HexColor('#F5F5F5')
GRIS_MED   = colors.HexColor('#CCCCCC')
VERDE_CLARO= colors.HexColor('#EAF3DE')
BLANCO     = colors.white
NEGRO      = colors.black

W, H = A4

FACTORES_MERCADO = {
    'deprimida':  1.09,
    'normal':     1.20,
    'consolidada':1.30,
    'premium':    1.45,
}

DESCUENTO_LABELS = {
    10: '10% – Venta en plazo normal',
    15: '15–20% – Venta rápida',
    25: '25% – Liquidación urgente',
}

def fmt_u(n): return f"USD {int(round(n)):,}".replace(",",".")
def fmt_p(n): return f"$ {int(round(n)):,}".replace(",",".")
def fmt_m2(n): return f"USD {int(round(n))}/m²"

def estilos():
    s = {}
    B = ParagraphStyle
    s['tit']   = B('tit',  fontName='Helvetica-Bold',  fontSize=18, textColor=BLANCO,  alignment=TA_CENTER, leading=22)
    s['subtit']= B('sub',  fontName='Helvetica',       fontSize=10, textColor=ORO,     alignment=TA_CENTER, leading=13)
    s['sec']   = B('sec',  fontName='Helvetica-Bold',  fontSize=11, textColor=AZUL,    spaceAfter=3, leading=14)
    s['body']  = B('body', fontName='Helvetica',       fontSize=9,  textColor=NEGRO,   alignment=TA_JUSTIFY, leading=13, spaceAfter=3)
    s['lbl']   = B('lbl',  fontName='Helvetica-Bold',  fontSize=8.5,textColor=AZUL,    leading=12)
    s['val']   = B('val',  fontName='Helvetica',       fontSize=8.5,textColor=NEGRO,   leading=12)
    s['ch']    = B('ch',   fontName='Helvetica-Bold',  fontSize=8.5,textColor=BLANCO,  alignment=TA_CENTER, leading=12)
    s['cv']    = B('cv',   fontName='Helvetica',       fontSize=8.5,textColor=NEGRO,   alignment=TA_CENTER, leading=12)
    s['cvb']   = B('cvb',  fontName='Helvetica-Bold',  fontSize=8.5,textColor=AZUL,    alignment=TA_CENTER, leading=12)
    s['num']   = B('num',  fontName='Helvetica-Bold',  fontSize=16, textColor=AZUL,    alignment=TA_CENTER, leading=20)
    s['pie']   = B('pie',  fontName='Helvetica-Oblique',fontSize=7, textColor=colors.HexColor('#666666'), alignment=TA_CENTER, leading=9)
    s['fnm']   = B('fnm',  fontName='Helvetica-Bold',  fontSize=8.5,textColor=AZUL,    alignment=TA_CENTER, leading=11)
    s['fdt']   = B('fdt',  fontName='Helvetica',       fontSize=8,  textColor=NEGRO,   alignment=TA_CENTER, leading=10)
    s['alerta']= B('alr',  fontName='Helvetica-Bold',  fontSize=9,  textColor=colors.HexColor('#7B3F00'), alignment=TA_JUSTIFY, leading=13)
    return s

def header_footer(c, doc):
    c.saveState()
    c.setFillColor(AZUL)
    c.rect(0, H-58, W, 58, fill=1, stroke=0)
    c.setFillColor(ORO)
    c.rect(0, H-61, W, 3, fill=1, stroke=0)
    c.setFillColor(BLANCO)
    c.setFont('Helvetica-Bold', 13)
    c.drawString(2*cm, H-30, 'GRUPO RODI INMOBILIARIA')
    c.setFont('Helvetica', 8.5)
    c.setFillColor(ORO)
    c.drawString(2*cm, H-43, 'Soluciones Inteligentes para el Mercado')
    c.setFillColor(BLANCO)
    c.setFont('Helvetica', 7.5)
    c.drawRightString(W-2*cm, H-28, 'Corredor Público Inmobiliario')
    c.drawRightString(W-2*cm, H-38, 'Matrícula Prof. N.° 7524')
    c.drawRightString(W-2*cm, H-48, 'Rodi Matías Nicolás')
    c.setFillColor(AZUL)
    c.rect(0, 0, W, 26, fill=1, stroke=0)
    c.setFillColor(ORO)
    c.rect(0, 26, W, 2, fill=1, stroke=0)
    c.setFillColor(BLANCO)
    fecha = datetime.datetime.now().strftime('%d de %B de %Y')
    c.setFont('Helvetica', 6.5)
    c.drawCentredString(W/2, 14, 'Grupo Rodi Inmobiliaria  |  Córdoba, Argentina  |  Mat. Prof. N.° 7524')
    c.drawCentredString(W/2, 6,  f'Informe emitido el {fecha}  –  Página {doc.page}  |  Vigencia: 90 días')
    c.restoreState()

def sep(color=None, t=1.2):
    return HRFlowable(width='100%', thickness=t, color=color or ORO, spaceAfter=6, spaceBefore=3)

def tbl_ficha(rows, s, col1=5*cm, col2=11*cm):
    t = Table([[Paragraph(r[0], s['lbl']), Paragraph(str(r[1]), s['val'])] for r in rows],
              colWidths=[col1, col2])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), GRIS_CLARO),
        ('ROWPADDING', (0,0), (-1,-1), 5),
        ('GRID',       (0,0), (-1,-1), 0.3, GRIS_MED),
        ('VALIGN',     (0,0), (-1,-1), 'TOP'),
    ]))
    return t

def generar_pdf(d):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=3.3*cm, bottomMargin=1.8*cm)
    s = estilos()
    story = []
    fecha_hoy = datetime.datetime.now().strftime('%d/%m/%Y')

    # ── DATOS CALCULADOS ──────────────────────────────────────
    sup         = d['superficie']
    val_m2      = d['valorM2']
    tc_of       = d['tcOficial']
    tc_bl       = d['tcBlue']
    val_tierra  = d.get('valTierra', 0)
    zona        = d.get('zona', 'deprimida')
    factor      = FACTORES_MERCADO.get(zona, 1.09)
    costo_demo  = d.get('costoDemo', 80)
    descuento   = d.get('descuento', 15)
    fot         = d.get('fot', 2.5)
    fos         = d.get('fos', 70) / 100
    alt_max     = d.get('altMax', 15)
    pisos       = max(1, int(alt_max / 3.5))

    # Método 1
    m1_total    = sup * val_m2

    # Método 2
    val_cat_usd = val_tierra / tc_bl if tc_bl > 0 and val_tierra > 0 else 0
    m2_total    = val_cat_usd * factor if val_cat_usd > 0 else m1_total

    # Método 3 – Residual
    sup_const   = sup * fot
    sup_vendible= sup_const * 0.85
    precio_venta= 1000
    ingreso     = sup_vendible * precio_venta
    costo_const_m2 = 867
    costo_const = sup_const * costo_const_m2
    costo_demol_total = costo_demo * (d.get('valMejoras', 0) / 30000000 * 374 if d.get('valMejoras', 0) > 0 else 374)
    honorarios  = costo_const * 0.10
    gastos_com  = ingreso * 0.04
    utilidad    = ingreso * 0.18
    m3_total    = ingreso - costo_const - costo_demol_total - honorarios - gastos_com - utilidad

    # Valor adoptado
    if val_cat_usd > 0:
        valor_adoptado = round((m1_total + m2_total) / 2)
    else:
        valor_adoptado = round(m1_total)

    precio_min  = round(valor_adoptado * (1 - (descuento + 5) / 100))
    precio_max  = round(valor_adoptado * (1 - descuento / 100))

    # ── PORTADA ───────────────────────────────────────────────
    story.append(Spacer(1, 0.4*cm))
    tit_data = [
        [Paragraph('INFORME DE TASACIÓN DE INMUEBLE', s['tit'])],
        [Paragraph(f"{d.get('domicilio','–')}", s['subtit'])],
        [Paragraph(f"Nomenclatura: {d.get('nomenclatura','–')}  |  Fecha: {fecha_hoy}", s['subtit'])],
    ]
    tbl_tit = Table(tit_data, colWidths=[16.5*cm])
    tbl_tit.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), AZUL),
        ('ROWPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 12),
    ]))
    story.append(tbl_tit)
    story.append(Spacer(1, 0.4*cm))

    # Advertencia
    aviso = Table([[Paragraph(
        '⚠  ACLARACIÓN: El tasador no pudo ingresar físicamente al inmueble. '
        'La tasación se basa en datos catastrales oficiales, relevamiento externo '
        'y análisis comparativo de publicaciones de mercado vigentes.',
        s['alerta'])]], colWidths=[16.5*cm])
    aviso.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFF3CD')),
        ('BOX',        (0,0), (-1,-1), 1, ORO),
        ('ROWPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(aviso)
    story.append(Spacer(1, 0.4*cm))

    # ── 1. IDENTIFICACIÓN ─────────────────────────────────────
    story.append(Paragraph('1. IDENTIFICACIÓN DEL INMUEBLE', s['sec']))
    story.append(sep())
    ficha_rows = [
        ['Titular registral',       d.get('titular', '–')],
        ['CUIT / CUIL',             d.get('cuit', '–')],
        ['Domicilio del bien',      d.get('domicilio', '–')],
        ['N.° cuenta Rentas Cba.',  d.get('nroCuenta', '–')],
        ['Nomenclatura catastral',  d.get('nomenclatura', '–')],
        ['Tipo de bien',            d.get('tipoBien', '–').replace('baldio','Terreno baldío').replace('edificado','Terreno con construcciones')],
        ['Zonificación',            f"Perfil {d.get('perfil','–')} – {d.get('apu','–')}"],
        ['Carácter urbanístico',    d.get('caracter', '–').capitalize()],
    ]
    story.append(tbl_ficha(ficha_rows, s))
    story.append(Spacer(1, 0.3*cm))

    # ── 2. CARACTERÍSTICAS ────────────────────────────────────
    story.append(Paragraph('2. CARACTERÍSTICAS FÍSICAS Y NORMATIVAS', s['sec']))
    story.append(sep())
    story.append(Paragraph('2.1  Datos físicos', s['lbl']))
    story.append(Spacer(1, 0.15*cm))
    story.append(tbl_ficha([
        ['Superficie total (oficial)',         f'{sup} m²'],
        ['Construcciones existentes',          f"Mejoras fiscales: {fmt_p(d.get('valMejoras',0))}"],
    ], s))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph('2.2  Datos normativos', s['lbl']))
    story.append(Spacer(1, 0.15*cm))
    story.append(tbl_ficha([
        ['FOS',          f"{d.get('fos',70)}%  →  Ocupación máxima en planta: {round(sup * fos)} m²"],
        ['FOT',          f"{fot}  →  Superficie total construible: {round(sup * fot)} m²"],
        ['Altura máxima',f"{alt_max} m (aprox. {pisos} pisos)"],
    ], s))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph('2.3  Valuación fiscal – Catastro Córdoba', s['lbl']))
    story.append(Spacer(1, 0.15*cm))
    story.append(tbl_ficha([
        ['Valuación fiscal tierra',  fmt_p(val_tierra)],
        ['Valuación fiscal mejoras', fmt_p(d.get('valMejoras', 0))],
        ['Valuación fiscal total',   fmt_p(val_tierra + d.get('valMejoras', 0))],
        ['Zona de mercado',          f"{zona.capitalize()} – Factor de mercado: {factor}"],
    ], s))
    story.append(Spacer(1, 0.3*cm))

    # ── 3. ENTORNO ────────────────────────────────────────────
    story.append(Paragraph('3. ANÁLISIS DE ENTORNO Y UBICACIÓN', s['sec']))
    story.append(sep())
    story.append(Paragraph(
        f"El inmueble se ubica en {d.get('domicilio','–')}, en zona de carácter "
        f"{d.get('caracter','mixto')} con perfil normativo {d.get('perfil','D')}. "
        f"El entorno presenta tejido urbano {'consolidado' if zona in ['consolidada','premium'] else 'en desarrollo'} "
        f"con {'alta' if zona in ['consolidada','premium'] else 'moderada'} demanda de mercado.",
        s['body']))
    obs = d.get('observaciones', '')
    if obs:
        story.append(Spacer(1, 0.15*cm))
        story.append(Paragraph(f"Observaciones del tasador: {obs}", s['body']))
    story.append(Spacer(1, 0.3*cm))

    # ── 4. METODOLOGÍA ────────────────────────────────────────
    story.append(Paragraph('4. METODOLOGÍA DE VALUACIÓN – TRES MÉTODOS', s['sec']))
    story.append(sep())
    story.append(Paragraph(
        'Se aplican tres métodos de valuación complementarios. '
        'El valor final adoptado surge de la ponderación de los métodos '
        'que arrojan resultados verificables con la información disponible.',
        s['body']))
    story.append(Spacer(1, 0.3*cm))

    # MÉTODO 1
    story.append(Paragraph('MÉTODO 1 – Comparativo de Mercado', s['sec']))
    story.append(sep(color=GRIS_MED, t=0.8))

    # Comparables
    comps = d.get('comparables', [])
    if comps:
        comp_rows = [['N.°', 'Ubicación', 'Sup.', 'Precio', 'USD/m²', 'Fuente']]
        for i, c in enumerate(comps, 1):
            comp_rows.append([
                str(i),
                c.get('ubicacion','–'),
                f"{c.get('sup',0)} m²" if c.get('sup') else '–',
                fmt_u(c.get('precio', 0)),
                fmt_m2(c.get('usdm2', 0)) if c.get('usdm2') else '–',
                c.get('fuente', '–'),
            ])
        comp_rows.append(['SUJETO', d.get('domicilio','–'), f'{sup} m²',
                          fmt_u(m1_total), fmt_m2(val_m2), 'Valor adoptado'])
        rows_c = []
        for i, row in enumerate(comp_rows):
            if i == 0:
                rows_c.append([Paragraph(x, s['ch']) for x in row])
            elif row[0] == 'SUJETO':
                rows_c.append([Paragraph(x, s['cvb']) for x in row])
            else:
                rows_c.append([Paragraph(x, s['cv']) for x in row])
        tbl_c = Table(rows_c, colWidths=[1.2*cm, 4*cm, 1.8*cm, 2.3*cm, 1.8*cm, 5.4*cm])
        tbl_c.setStyle(TableStyle([
            ('BACKGROUND',  (0,0),  (-1,0),  AZUL),
            ('BACKGROUND',  (0,-1), (-1,-1), VERDE_CLARO),
            ('ROWPADDING',  (0,0),  (-1,-1), 5),
            ('GRID',        (0,0),  (-1,-1), 0.3, GRIS_MED),
            ('VALIGN',      (0,0),  (-1,-1), 'TOP'),
        ]))
        story.append(tbl_c)
    else:
        story.append(Paragraph(f'Valor unitario adoptado: {fmt_m2(val_m2)} × {sup} m²', s['body']))

    res_m1 = Table([[Paragraph('RESULTADO MÉTODO 1', s['ch']), Paragraph(fmt_u(m1_total), s['cvb'])]],
                   colWidths=[10*cm, 6.5*cm])
    res_m1.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), AZUL),
        ('BACKGROUND', (0,0), (1,0), VERDE_CLARO),
        ('ROWPADDING', (0,0), (-1,-1), 7),
        ('BOX',        (0,0), (-1,-1), 0.5, ORO),
    ]))
    story.append(Spacer(1, 0.2*cm))
    story.append(res_m1)
    story.append(Spacer(1, 0.4*cm))

    # MÉTODO 2
    if val_cat_usd > 0:
        story.append(Paragraph('MÉTODO 2 – Intrínseco (Base Catastral)', s['sec']))
        story.append(sep(color=GRIS_MED, t=0.8))
        story.append(Paragraph(
            f'Se aplica un factor de mercado de {factor} sobre la valuación fiscal de la tierra '
            f'({zona} – catastro representa el {round(100/factor)}% del valor de mercado). '
            f'Este factor es justificado por las condiciones específicas del inmueble y su entorno.',
            s['body']))
        calc_m2 = [
            ['Concepto', 'Cálculo', 'Resultado'],
            ['Valuación fiscal tierra', '–', fmt_p(val_tierra)],
            [f'Conversión a dólares (TC Blue {fmt_p(tc_bl)})', f'{fmt_p(val_tierra)} ÷ {fmt_p(tc_bl)}', fmt_u(val_cat_usd)],
            [f'Factor de mercado ({factor})', f'× {factor} ({zona})', '–'],
            ['Valor estimado – Método 2', '–', fmt_u(m2_total)],
        ]
        rows_m2 = []
        for i, row in enumerate(calc_m2):
            if i == 0: rows_m2.append([Paragraph(x, s['ch']) for x in row])
            elif i == len(calc_m2)-1: rows_m2.append([Paragraph(x, s['cvb']) for x in row])
            else: rows_m2.append([Paragraph(x, s['cv']) for x in row])
        tbl_m2 = Table(rows_m2, colWidths=[7*cm, 5.5*cm, 4*cm])
        tbl_m2.setStyle(TableStyle([
            ('BACKGROUND',  (0,0),  (-1,0),  AZUL),
            ('BACKGROUND',  (0,-1), (-1,-1), VERDE_CLARO),
            ('ROWPADDING',  (0,0),  (-1,-1), 6),
            ('GRID',        (0,0),  (-1,-1), 0.3, GRIS_MED),
        ]))
        story.append(Spacer(1, 0.15*cm))
        story.append(tbl_m2)
        res_m2 = Table([[Paragraph('RESULTADO MÉTODO 2', s['ch']), Paragraph(fmt_u(m2_total), s['cvb'])]],
                       colWidths=[10*cm, 6.5*cm])
        res_m2.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (0,0), AZUL),
            ('BACKGROUND', (0,0), (1,0), VERDE_CLARO),
            ('ROWPADDING', (0,0), (-1,-1), 7),
            ('BOX',        (0,0), (-1,-1), 0.5, ORO),
        ]))
        story.append(Spacer(1, 0.2*cm))
        story.append(res_m2)
        story.append(Spacer(1, 0.4*cm))

    # MÉTODO 3
    story.append(Paragraph('MÉTODO 3 – Valor Residual (Desarrollo Inmobiliario)', s['sec']))
    story.append(sep(color=GRIS_MED, t=0.8))
    calc_m3 = [
        ['Concepto', 'Cálculo', 'USD'],
        ['(+) Ingreso bruto', f'{round(sup_vendible)} m² × USD {precio_venta}/m²', fmt_u(ingreso)],
        ['(−) Costo construcción', f'{round(sup_const)} m² × USD {costo_const_m2}/m²', f'− {fmt_u(costo_const)}'],
        ['(−) Demolición (FACTOR CLAVE)', f'{round(costo_demol_total)} m² × USD {costo_demo}/m²', f'− {fmt_u(costo_demol_total)}'],
        ['(−) Honorarios (10%)', '–', f'− {fmt_u(honorarios)}'],
        ['(−) Comercialización (4%)', '–', f'− {fmt_u(gastos_com)}'],
        ['(−) Utilidad desarrollador (18%)', '–', f'− {fmt_u(utilidad)}'],
        ['VALOR RESIDUAL', '–', fmt_u(m3_total) if m3_total > 0 else 'NEGATIVO'],
    ]
    rows_m3 = []
    for i, row in enumerate(calc_m3):
        if i == 0: rows_m3.append([Paragraph(x, s['ch']) for x in row])
        elif i == len(calc_m3)-1: rows_m3.append([Paragraph(x, s['cvb']) for x in row])
        else: rows_m3.append([Paragraph(x, s['cv']) for x in row])
    tbl_m3 = Table(rows_m3, colWidths=[6.5*cm, 5.5*cm, 4.5*cm])
    tbl_m3.setStyle(TableStyle([
        ('BACKGROUND',  (0,0),  (-1,0),  AZUL),
        ('BACKGROUND',  (0,-1), (-1,-1), colors.HexColor('#FDECEA') if m3_total <= 0 else VERDE_CLARO),
        ('ROWPADDING',  (0,0),  (-1,-1), 6),
        ('GRID',        (0,0),  (-1,-1), 0.3, GRIS_MED),
        ('BACKGROUND',  (0,3),  (-1,3),  colors.HexColor('#FFF3CD')),
    ]))
    story.append(tbl_m3)
    if m3_total <= 0:
        story.append(Spacer(1, 0.15*cm))
        story.append(Paragraph(
            'El Método Residual arroja resultado negativo bajo las premisas actuales, '
            'lo que confirma que el costo de demolición y las restricciones normativas '
            'limitan significativamente el atractivo del terreno para desarrolladores. '
            'Este resultado es coherente con el valor conservador adoptado y lo refuerza.',
            s['body']))
    story.append(Spacer(1, 0.4*cm))

    # ── 5. RESUMEN ────────────────────────────────────────────
    story.append(Paragraph('5. CUADRO RESUMEN Y VALOR ADOPTADO', s['sec']))
    story.append(sep())
    metodos = [
        ['Método', 'Valor USD', 'Ponderación', 'Observaciones'],
        ['1 – Comparativo', fmt_u(m1_total), 'Adoptado', f'{fmt_m2(val_m2)} × {sup} m²'],
    ]
    if val_cat_usd > 0:
        metodos.append(['2 – Intrínseco catastral', fmt_u(m2_total), 'Adoptado', f'Factor {factor} sobre val. fiscal'])
    metodos.append(['3 – Valor residual', fmt_u(m3_total) if m3_total > 0 else 'Negativo', 'Referencia', 'FOT/altura limitan desarrollo'])
    metodos.append(['VALOR ADOPTADO', fmt_u(valor_adoptado), '✓', 'Promedio M1 + M2' if val_cat_usd > 0 else 'Método 1'])
    rows_res = []
    for i, row in enumerate(metodos):
        if i == 0: rows_res.append([Paragraph(x, s['ch']) for x in row])
        elif row[0] == 'VALOR ADOPTADO': rows_res.append([Paragraph(x, s['cvb']) for x in row])
        else: rows_res.append([Paragraph(x, s['cv']) for x in row])
    tbl_res = Table(rows_res, colWidths=[4.5*cm, 3*cm, 2.5*cm, 6.5*cm])
    tbl_res.setStyle(TableStyle([
        ('BACKGROUND',  (0,0),  (-1,0),  AZUL),
        ('BACKGROUND',  (0,-1), (-1,-1), VERDE_CLARO),
        ('ROWPADDING',  (0,0),  (-1,-1), 6),
        ('GRID',        (0,0),  (-1,-1), 0.3, GRIS_MED),
    ]))
    story.append(tbl_res)
    story.append(Spacer(1, 0.4*cm))

    # CAJA VALOR FINAL
    val_box = [
        [Paragraph('VALOR DE TASACIÓN ADOPTADO', s['ch'])],
        [Paragraph(fmt_u(valor_adoptado), s['num'])],
        [Paragraph(
            f"TC Oficial ($ {tc_of:,.0f}): {fmt_p(valor_adoptado * tc_of)}    |    "
            f"TC Blue ($ {tc_bl:,.0f}): {fmt_p(valor_adoptado * tc_bl)}",
            s['subtit'])],
        [Paragraph('Vigencia del informe: 90 días corridos desde la fecha de emisión', s['pie'])],
    ]
    tbl_val = Table(val_box, colWidths=[16.5*cm])
    tbl_val.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,0),  AZUL),
        ('BACKGROUND',   (0,1), (-1,-1), GRIS_CLARO),
        ('ROWPADDING',   (0,0), (-1,-1), 9),
        ('BOX',          (0,0), (-1,-1), 2, ORO),
    ]))
    story.append(tbl_val)
    story.append(Spacer(1, 0.4*cm))

    # ── 6. PRECIO DE COMERCIALIZACIÓN ────────────────────────
    story.append(Paragraph('6. PRECIO DE COMERCIALIZACIÓN SUGERIDO', s['sec']))
    story.append(sep())
    fact_rows = [
        ['Factor', 'Fundamento', '%'],
        ['Zona deprimida / mercado lento', 'Baja rotación de inmuebles en el corredor al momento de la tasación.', '5–7%'],
        ['Venta en plazo reducido', 'El comitente requiere comercialización rápida, resignando parte del valor.', '5–7%'],
        ['Demolición a cargo del adquirente (FACTOR CLAVE)',
         f'Las construcciones existentes deben demolerse previo a cualquier desarrollo. '
         f'Costo estimado: USD {costo_demo}/m². Principal condicionante del valor, '
         f'limita el universo de compradores a desarrolladores e inversores.', '3–6%'],
        ['No inspección interior', 'Incertidumbre adicional para el adquirente.', '2–3%'],
        ['DESCUENTO TOTAL', '–', f'{descuento}–{descuento+5}%'],
    ]
    rows_f = []
    for i, row in enumerate(fact_rows):
        if i == 0: rows_f.append([Paragraph(x, s['ch']) for x in row])
        elif i == len(fact_rows)-1: rows_f.append([Paragraph(x, s['cvb']) for x in row])
        else: rows_f.append([Paragraph(x, s['cv']) for x in row])
    tbl_f = Table(rows_f, colWidths=[4.5*cm, 9.5*cm, 2.5*cm])
    tbl_f.setStyle(TableStyle([
        ('BACKGROUND',  (0,0),  (-1,0),  AZUL),
        ('BACKGROUND',  (0,-1), (-1,-1), VERDE_CLARO),
        ('BACKGROUND',  (0,3),  (-1,3),  colors.HexColor('#FFF3CD')),
        ('ROWPADDING',  (0,0),  (-1,-1), 6),
        ('GRID',        (0,0),  (-1,-1), 0.3, GRIS_MED),
        ('VALIGN',      (0,0),  (-1,-1), 'TOP'),
    ]))
    story.append(tbl_f)
    story.append(Spacer(1, 0.3*cm))

    precio_box = [
        [Paragraph('PRECIO DE COMERCIALIZACIÓN SUGERIDO (VENTA RÁPIDA)', s['ch'])],
        [Paragraph(f'{fmt_u(precio_min)} – {fmt_u(precio_max)}', s['num'])],
        [Paragraph(
            f'Descuento del {descuento}% al {descuento+5}% sobre el valor de tasación de {fmt_u(valor_adoptado)}. '
            f'Otorga flexibilidad negociadora y posiciona el inmueble competitivamente en el mercado.',
            s['subtit'])],
    ]
    tbl_p = Table(precio_box, colWidths=[16.5*cm])
    tbl_p.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,0),  AZUL),
        ('BACKGROUND',   (0,1), (-1,-1), GRIS_CLARO),
        ('ROWPADDING',   (0,0), (-1,-1), 9),
        ('BOX',          (0,0), (-1,-1), 2, ORO),
    ]))
    story.append(tbl_p)
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        'Nota: el precio de comercialización sugerido no forma parte del valor de tasación oficial '
        'y se incluye únicamente como orientación para la estrategia de venta del comitente.',
        s['pie']))
    story.append(Spacer(1, 0.4*cm))

    # ── 7. FIRMA ──────────────────────────────────────────────
    story.append(Paragraph('7. FIRMA Y CERTIFICACIÓN', s['sec']))
    story.append(sep())
    story.append(Spacer(1, 0.8*cm))
    firma = [
        [Paragraph('_______________________________', s['fnm'])],
        [Paragraph('RODI MATÍAS NICOLÁS', s['fnm'])],
        [Paragraph('Corredor Público Inmobiliario', s['fdt'])],
        [Paragraph('Matrícula Provincial N.° 7524', s['fdt'])],
        [Paragraph('Grupo Rodi Inmobiliaria – Córdoba, Argentina', s['fdt'])],
        [Paragraph(f'Fecha: {fecha_hoy}', s['fdt'])],
    ]
    tbl_firma = Table(firma, colWidths=[16.5*cm])
    tbl_firma.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('ROWPADDING',(0,0),(-1,-1),3)]))
    story.append(tbl_firma)
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=GRIS_MED, spaceAfter=6))
    story.append(Paragraph(
        'Informe elaborado por Corredor Público Inmobiliario matriculado conforme Ley N.° 9445 '
        'de la Provincia de Córdoba. Información confidencial de uso exclusivo del solicitante.',
        s['pie']))

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    buf.seek(0)
    return buf

# ── RUTAS ──────────────────────────────────────────────────────
@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/generar', methods=['POST'])
def generar():
    datos = request.get_json()
    if not datos:
        return jsonify({'error': 'No se recibieron datos'}), 400
    try:
        pdf_buf = generar_pdf(datos)
        titular = datos.get('titular', 'tasacion').replace(' ', '_')
        filename = f"Tasacion_{titular}.pdf"
        return send_file(pdf_buf, mimetype='application/pdf',
                        as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5001)
