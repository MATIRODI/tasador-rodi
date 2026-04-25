from flask import Flask, request, send_file, jsonify
import io, math, datetime, requests, json, time, re
from bs4 import BeautifulSoup
import anthropic
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, HRFlowable)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

app = Flask(__name__, static_folder='.', static_url_path='')

# ── COLORES ────────────────────────────────────────────────────
AZUL       = colors.HexColor('#0D1B2A')
ORO        = colors.HexColor('#C9A84C')
GRIS_CLARO = colors.HexColor('#F5F5F5')
GRIS_MED   = colors.HexColor('#CCCCCC')
VERDE_CLARO= colors.HexColor('#EAF3DE')
BLANCO     = colors.white
NEGRO      = colors.black
W, H       = A4

FACTORES_MERCADO = {
    'deprimida':   1.09,
    'normal':      1.20,
    'consolidada': 1.30,
    'premium':     1.45,
}

COSTOS_DEMOLICION = {
    'ninguna':   0,
    'antigua':   80,
    'moderna':   120,
    'hormigon':  180,
}

def fmt_u(n): return f"USD {int(round(n)):,}".replace(",",".")
def fmt_p(n): return f"$ {int(round(n)):,}".replace(",",".")
def fmt_m2(n): return f"USD {int(round(n))}/m²"


# ══════════════════════════════════════════════════════════════
# TIPOS DE CAMBIO AUTOMÁTICOS — bluelytics.com.ar
# ══════════════════════════════════════════════════════════════
_tc_cache = {
    'data': None,
    'timestamp': 0,
}
TC_CACHE_TTL = 600  # 10 minutos

def obtener_tipos_cambio():
    """
    Consulta bluelytics.com.ar y retorna oficial y blue (venta).
    Cachea el resultado por 10 minutos.
    Fallback: valores hardcodeados si la API falla.
    """
    global _tc_cache
    now = time.time()

    if _tc_cache['data'] and (now - _tc_cache['timestamp']) < TC_CACHE_TTL:
        return _tc_cache['data']

    try:
        resp = requests.get(
            'https://api.bluelytics.com.ar/v2/latest',
            timeout=6,
            headers={'User-Agent': 'GrupoRodiTasador/1.0'}
        )
        resp.raise_for_status()
        raw = resp.json()

        data = {
            'oficial': round(raw['oficial']['value_sell']),
            'blue':    round(raw['blue']['value_sell']),
            'fuente':  'bluelytics.com.ar',
            'ultima_actualizacion': raw.get('last_update', ''),
            'ok': True,
        }

        _tc_cache['data'] = data
        _tc_cache['timestamp'] = now
        print(f"TC actualizado — Oficial: {data['oficial']} | Blue: {data['blue']}")
        return data

    except Exception as e:
        print(f"Error obteniendo TC: {e}")
        fallback = {
            'oficial': 1400,
            'blue':    1410,
            'fuente':  'fallback manual',
            'ultima_actualizacion': '',
            'ok': False,
        }
        # Usar caché vieja si existe, aunque esté vencida
        if _tc_cache['data']:
            _tc_cache['data']['ok'] = False
            return _tc_cache['data']
        return fallback


# ══════════════════════════════════════════════════════════════
# NORMATIVA AUTOMÁTICA
# ══════════════════════════════════════════════════════════════
def obtener_normativa(direccion):
    try:
        headers = {'User-Agent': 'GrupoRodiTasador/1.0'}
        geo_url = "https://nominatim.openstreetmap.org/search"
        geo_params = {
            'q': f"{direccion}, Córdoba, Argentina",
            'format': 'json',
            'limit': 1
        }
        geo_resp = requests.get(geo_url, params=geo_params, headers=headers, timeout=8)
        geo_data = geo_resp.json()

        if not geo_data:
            return None

        lat = float(geo_data[0]['lat'])
        lon = float(geo_data[0]['lon'])

        gis_url = "https://mapas.cordoba.gob.ar/server/rest/services/normativa/MapServer/identify"
        gis_params = {
            'f': 'json',
            'geometry': f"{lon},{lat}",
            'geometryType': 'esriGeometryPoint',
            'sr': '4326',
            'layers': 'all',
            'tolerance': '5',
            'mapExtent': f"{lon-0.01},{lat-0.01},{lon+0.01},{lat+0.01}",
            'imageDisplay': '800,600,96',
            'returnGeometry': 'false',
        }
        gis_resp = requests.get(gis_url, params=gis_params, headers=headers, timeout=10)
        gis_data = gis_resp.json()

        resultado = {'lat': lat, 'lon': lon}

        if 'results' in gis_data:
            for item in gis_data['results']:
                attrs = item.get('attributes', {})
                for k, v in attrs.items():
                    k_lower = k.lower()
                    if 'fot' in k_lower and v and str(v) not in ['Null','null','']:
                        resultado['fot'] = float(str(v).replace(',','.'))
                    if 'fos' in k_lower and v and str(v) not in ['Null','null','']:
                        resultado['fos'] = float(str(v).replace(',','.').replace('%',''))
                    if 'perfil' in k_lower and v and str(v) not in ['Null','null','']:
                        resultado['perfil'] = str(v)
                    if 'altura' in k_lower and v and str(v) not in ['Null','null','']:
                        try:
                            resultado['altMax'] = float(str(v).replace(',','.'))
                        except:
                            pass
                    if 'caracter' in k_lower and v and str(v) not in ['Null','null','']:
                        resultado['caracter'] = str(v)
                    if 'apu' in k_lower and v and str(v) not in ['Null','null','']:
                        resultado['apu'] = str(v)

        return resultado if len(resultado) > 2 else {'lat': lat, 'lon': lon}

    except Exception as e:
        print(f"Error normativa: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# COMPARABLES AUTOMÁTICOS
# ══════════════════════════════════════════════════════════════
def scraping_zonaprop(barrio, tipo='terreno'):
    try:
        barrio_slug = barrio.lower().replace(' ', '-').replace('°','').replace('º','')
        url = f"https://www.zonaprop.com.ar/terrenos-venta-{barrio_slug}-cordoba.html"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'es-AR,es;q=0.9',
        }
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        comparables = []
        cards = soup.find_all('div', {'data-id': True}) or \
                soup.find_all('div', class_=re.compile(r'posting|card|property', re.I))

        for card in cards[:8]:
            try:
                texto = card.get_text(' ', strip=True)
                precio_match = re.search(r'USD?\s*[\$]?\s*([\d\.]+)', texto, re.I)
                sup_match    = re.search(r'(\d+)\s*m[²2]', texto)
                dir_match    = re.search(r'([A-ZÁÉÍÓÚ][a-záéíóú\s]+\d+)', texto)

                if precio_match and sup_match:
                    precio = float(precio_match.group(1).replace('.',''))
                    sup    = float(sup_match.group(1))
                    if precio > 10000 and sup > 50:
                        usdm2 = round(precio / sup)
                        comparables.append({
                            'ubicacion': dir_match.group(1) if dir_match else f'Zona {barrio}',
                            'fuente': 'Zonaprop (automático)',
                            'sup': sup,
                            'precio': precio,
                            'usdm2': usdm2,
                            'obs': 'Relevado automáticamente',
                        })
            except:
                continue

        return comparables[:5]

    except Exception as e:
        print(f"Error scraping Zonaprop: {e}")
        return []


def comparables_via_claude(barrio, superficie_ref, tipo_bien):
    try:
        client = anthropic.Anthropic()
        prompt = f"""Buscá en internet publicaciones actuales (2025-2026) de terrenos en venta 
en el barrio {barrio}, Córdoba Capital, Argentina. 
Necesito entre 4 y 6 comparables con precio real publicado en USD.
Tipo de bien: {tipo_bien}
Superficie de referencia: {superficie_ref} m²

Respondé ÚNICAMENTE con un JSON válido, sin texto adicional, sin markdown, sin backticks.
Formato exacto:
[
  {{
    "ubicacion": "Calle y barrio",
    "fuente": "Zonaprop/Argenprop/etc + fecha",
    "sup": 500,
    "precio": 200000,
    "usdm2": 400,
    "obs": "observación breve"
  }}
]

Solo incluí comparables con precio USD confirmado. Si no encontrás suficientes en ese barrio exacto, 
expandí a barrios linderos de Córdoba Capital."""

        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )

        texto = ""
        for block in response.content:
            if block.type == "text":
                texto += block.text

        texto = texto.strip()
        texto = re.sub(r'```json|```', '', texto).strip()
        json_match = re.search(r'\[.*\]', texto, re.DOTALL)
        if json_match:
            comparables = json.loads(json_match.group())
            resultado = []
            for c in comparables:
                if c.get('precio', 0) > 0 and c.get('ubicacion'):
                    if not c.get('usdm2') and c.get('sup', 0) > 0:
                        c['usdm2'] = round(c['precio'] / c['sup'])
                    resultado.append(c)
            return resultado[:6]
        return []

    except Exception as e:
        print(f"Error Claude comparables: {e}")
        return []


def obtener_comparables(barrio, superficie_ref, tipo_bien):
    print(f"Buscando comparables para {barrio}...")
    comparables = scraping_zonaprop(barrio, tipo_bien)
    print(f"Scraping trajo {len(comparables)} comparables")

    if len(comparables) < 3:
        print("Complementando con Claude API...")
        claude_comps = comparables_via_claude(barrio, superficie_ref, tipo_bien)
        print(f"Claude trajo {len(claude_comps)} comparables")
        ubicaciones_existentes = {c['ubicacion'].lower() for c in comparables}
        for c in claude_comps:
            if c['ubicacion'].lower() not in ubicaciones_existentes:
                comparables.append(c)
                ubicaciones_existentes.add(c['ubicacion'].lower())

    return comparables[:6]


def sugerir_valor_m2(comparables, superficie, zona, tiene_demolicion):
    if not comparables:
        return None
    precios_m2 = [c['usdm2'] for c in comparables if c.get('usdm2', 0) > 0]
    if not precios_m2:
        return None

    precios_sorted = sorted(precios_m2)
    n = len(precios_sorted)
    if n >= 4:
        q1 = precios_sorted[n//4]
        q3 = precios_sorted[3*n//4]
        iqr = q3 - q1
        precios_filtrados = [p for p in precios_m2 if q1 - 1.5*iqr <= p <= q3 + 1.5*iqr]
    else:
        precios_filtrados = precios_m2

    if not precios_filtrados:
        precios_filtrados = precios_m2

    promedio = sum(precios_filtrados) / len(precios_filtrados)
    minimo   = min(precios_filtrados)
    maximo   = max(precios_filtrados)

    factor_zona = {'deprimida': 0.85, 'normal': 0.92, 'consolidada': 1.0, 'premium': 1.10}.get(zona, 0.92)
    factor_demo = 0.90 if tiene_demolicion else 1.0
    factor_sup  = 0.95 if superficie > 800 else 1.0

    valor_sugerido = round(promedio * factor_zona * factor_demo * factor_sup)
    rango_min = round(minimo * factor_zona * factor_demo * factor_sup)
    rango_max = round(maximo * factor_zona * factor_demo * factor_sup)

    return {
        'sugerido': valor_sugerido,
        'rango_min': rango_min,
        'rango_max': rango_max,
        'promedio_mercado': round(promedio),
        'total_comparables': len(precios_filtrados),
        'factores': {
            'zona': factor_zona,
            'demolicion': factor_demo,
            'superficie': factor_sup,
        }
    }


# ══════════════════════════════════════════════════════════════
# GENERADOR DE PDF
# ══════════════════════════════════════════════════════════════
def estilos():
    s = {}
    B = ParagraphStyle
    s['tit']   = B('tit',  fontName='Helvetica-Bold',   fontSize=18, textColor=BLANCO,  alignment=TA_CENTER, leading=22)
    s['subtit']= B('sub',  fontName='Helvetica',        fontSize=10, textColor=ORO,     alignment=TA_CENTER, leading=13)
    s['sec']   = B('sec',  fontName='Helvetica-Bold',   fontSize=11, textColor=AZUL,    spaceAfter=3, leading=14)
    s['body']  = B('body', fontName='Helvetica',        fontSize=9,  textColor=NEGRO,   alignment=TA_JUSTIFY, leading=13, spaceAfter=3)
    s['lbl']   = B('lbl',  fontName='Helvetica-Bold',   fontSize=8.5,textColor=AZUL,    leading=12)
    s['val']   = B('val',  fontName='Helvetica',        fontSize=8.5,textColor=NEGRO,   leading=12)
    s['ch']    = B('ch',   fontName='Helvetica-Bold',   fontSize=8.5,textColor=BLANCO,  alignment=TA_CENTER, leading=12)
    s['cv']    = B('cv',   fontName='Helvetica',        fontSize=8.5,textColor=NEGRO,   alignment=TA_CENTER, leading=12)
    s['cvb']   = B('cvb',  fontName='Helvetica-Bold',   fontSize=8.5,textColor=AZUL,    alignment=TA_CENTER, leading=12)
    s['num']   = B('num',  fontName='Helvetica-Bold',   fontSize=16, textColor=AZUL,    alignment=TA_CENTER, leading=20)
    s['pie']   = B('pie',  fontName='Helvetica-Oblique',fontSize=7,  textColor=colors.HexColor('#666666'), alignment=TA_CENTER, leading=9)
    s['fnm']   = B('fnm',  fontName='Helvetica-Bold',   fontSize=8.5,textColor=AZUL,    alignment=TA_CENTER, leading=11)
    s['fdt']   = B('fdt',  fontName='Helvetica',        fontSize=8,  textColor=NEGRO,   alignment=TA_CENTER, leading=10)
    s['alerta']= B('alr',  fontName='Helvetica-Bold',   fontSize=9,  textColor=colors.HexColor('#7B3F00'), alignment=TA_JUSTIFY, leading=13)
    s['tc']    = B('tc',   fontName='Helvetica',        fontSize=7.5,textColor=colors.HexColor('#444444'), alignment=TA_CENTER, leading=10)
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
    fecha = datetime.datetime.now().strftime('%d/%m/%Y')
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

    # ── TIPOS DE CAMBIO ───────────────────────────────────────
    # Prioridad: los que vienen del formulario (usuario puede corregir)
    # Si el frontend los mandó como 0 o vacíos, usar los automáticos
    tc_of_form = float(d.get('tcOficial', 0) or 0)
    tc_bl_form = float(d.get('tcBlue', 0) or 0)

    if tc_of_form > 100 and tc_bl_form > 100:
        tc_of = tc_of_form
        tc_bl = tc_bl_form
        tc_fuente = 'ingresado manualmente'
    else:
        tc_auto = obtener_tipos_cambio()
        tc_of = tc_auto['oficial']
        tc_bl = tc_auto['blue']
        tc_fuente = tc_auto.get('fuente', 'automático')

    # ── CÁLCULOS ──────────────────────────────────────────────
    sup          = float(d.get('superficie', 0))
    val_m2       = float(d.get('valorM2', 0))
    val_tierra   = float(d.get('valTierra', 0))
    val_mejoras  = float(d.get('valMejoras', 0))
    zona         = d.get('zona', 'deprimida')
    factor       = FACTORES_MERCADO.get(zona, 1.09)
    tipo_demo    = d.get('tipoDemo', 'ninguna')
    costo_demo   = COSTOS_DEMOLICION.get(tipo_demo, 0)
    sup_demo     = float(d.get('supDemo', 0))
    descuento    = float(d.get('descuento', 15))
    fot          = float(d.get('fot', 2.5))
    fos_pct      = float(d.get('fos', 70))
    fos          = fos_pct / 100
    alt_max      = float(d.get('altMax', 15))
    pisos        = max(1, int(alt_max / 3.5))
    comps        = d.get('comparables', [])

    m1_total     = sup * val_m2
    val_cat_usd  = val_tierra / tc_bl if tc_bl > 0 and val_tierra > 0 else 0
    m2_total     = val_cat_usd * factor if val_cat_usd > 0 else m1_total

    sup_const    = sup * fot
    sup_vendible = sup_const * 0.85
    precio_venta = 1000
    ingreso      = sup_vendible * precio_venta
    costo_const  = sup_const * 867
    costo_demol  = costo_demo * sup_demo
    honorarios   = costo_const * 0.10
    gastos_com   = ingreso * 0.04
    utilidad     = ingreso * 0.18
    m3_total     = ingreso - costo_const - costo_demol - honorarios - gastos_com - utilidad

    valor_adoptado = round((m1_total + m2_total) / 2) if val_cat_usd > 0 else round(m1_total)
    precio_min   = round(valor_adoptado * (1 - (descuento + 5) / 100))
    precio_max   = round(valor_adoptado * (1 - descuento / 100))

    # ── PORTADA ───────────────────────────────────────────────
    story.append(Spacer(1, 0.4*cm))
    tit = Table([
        [Paragraph('INFORME DE TASACIÓN DE INMUEBLE', s['tit'])],
        [Paragraph(d.get('domicilio','–'), s['subtit'])],
        [Paragraph(f"Nomenclatura: {d.get('nomenclatura','–')}  |  Fecha: {fecha_hoy}", s['subtit'])],
    ], colWidths=[16.5*cm])
    tit.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), AZUL),
        ('ROWPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 12),
    ]))
    story.append(tit)
    story.append(Spacer(1, 0.3*cm))

    # ── AVISO TC AUTOMÁTICO ───────────────────────────────────
    hora_tc = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    tc_info = Table([[Paragraph(
        f'💱  Tipos de cambio aplicados ({tc_fuente}) — '
        f'TC Oficial: {fmt_p(tc_of)}  |  TC Blue: {fmt_p(tc_bl)}  '
        f'— Consultado: {hora_tc} hs',
        s['tc'])]], colWidths=[16.5*cm])
    tc_info.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#EAF3DE')),
        ('BOX',        (0,0), (-1,-1), 0.8, colors.HexColor('#3B6D11')),
        ('ROWPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(tc_info)
    story.append(Spacer(1, 0.25*cm))

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
    story.append(tbl_ficha([
        ['Titular registral',      d.get('titular','–')],
        ['CUIT / CUIL',            d.get('cuit','–')],
        ['Domicilio del bien',     d.get('domicilio','–')],
        ['N.° cuenta Rentas',      d.get('nroCuenta','–')],
        ['Nomenclatura catastral', d.get('nomenclatura','–')],
        ['Tipo de bien',           d.get('tipoBien','–')],
        ['Zonificación',           f"Perfil {d.get('perfil','–')} – {d.get('apu','–')}"],
    ], s))
    story.append(Spacer(1, 0.3*cm))

    # ── 2. CARACTERÍSTICAS ────────────────────────────────────
    story.append(Paragraph('2. CARACTERÍSTICAS FÍSICAS Y NORMATIVAS', s['sec']))
    story.append(sep())
    story.append(tbl_ficha([
        ['Superficie total',        f'{sup} m²'],
        ['FOS',                     f'{fos_pct}%  →  ocupación máx. en planta: {round(sup*fos)} m²'],
        ['FOT',                     f'{fot}  →  superficie total construible: {round(sup*fot)} m²'],
        ['Altura máxima',           f'{alt_max} m (aprox. {pisos} pisos)'],
        ['Valuación fiscal tierra', fmt_p(val_tierra)],
        ['Valuación fiscal mejoras',fmt_p(val_mejoras)],
        ['Zona de mercado',         f"{zona.capitalize()} – Factor: {factor}"],
        ['Demolición',              f"{tipo_demo.capitalize()} – USD {costo_demo}/m² × {sup_demo} m² = {fmt_u(costo_demol)}" if costo_demol > 0 else "No aplica"],
        ['TC Oficial aplicado',     f"{fmt_p(tc_of)}  ({tc_fuente})"],
        ['TC Blue aplicado',        f"{fmt_p(tc_bl)}  ({tc_fuente})"],
    ], s))
    story.append(Spacer(1, 0.3*cm))

    # ── 3. METODOLOGÍA ────────────────────────────────────────
    story.append(Paragraph('3. METODOLOGÍA DE VALUACIÓN – TRES MÉTODOS', s['sec']))
    story.append(sep())
    story.append(Paragraph(
        'Se aplican tres métodos de valuación complementarios. '
        'El valor final adoptado surge del promedio de los métodos con datos verificables.',
        s['body']))
    story.append(Spacer(1, 0.2*cm))

    # M1
    story.append(Paragraph('MÉTODO 1 – Comparativo de Mercado', s['sec']))
    story.append(sep(color=GRIS_MED, t=0.8))
    if comps:
        comp_rows = [['N.°','Ubicación','Sup.','Precio','USD/m²','Fuente']]
        for i, c in enumerate(comps, 1):
            sup_c = c.get('sup', 0)
            prec  = c.get('precio', 0)
            usdm2 = c.get('usdm2', round(prec/sup_c) if sup_c > 0 else 0)
            comp_rows.append([
                str(i), c.get('ubicacion','–'),
                f"{sup_c} m²" if sup_c else '–',
                fmt_u(prec), fmt_m2(usdm2) if usdm2 else '–',
                c.get('fuente','–'),
            ])
        comp_rows.append(['SUJ.', d.get('domicilio','–'), f'{sup} m²',
                          fmt_u(m1_total), fmt_m2(val_m2), 'Valor adoptado'])
        rows_c = []
        for i, row in enumerate(comp_rows):
            if i == 0: rows_c.append([Paragraph(x, s['ch']) for x in row])
            elif row[0] == 'SUJ.': rows_c.append([Paragraph(x, s['cvb']) for x in row])
            else: rows_c.append([Paragraph(x, s['cv']) for x in row])
        tbl_c = Table(rows_c, colWidths=[1.1*cm, 3.8*cm, 1.6*cm, 2.2*cm, 1.8*cm, 6*cm])
        tbl_c.setStyle(TableStyle([
            ('BACKGROUND', (0,0),  (-1,0),  AZUL),
            ('BACKGROUND', (0,-1), (-1,-1), VERDE_CLARO),
            ('ROWPADDING', (0,0),  (-1,-1), 5),
            ('GRID',       (0,0),  (-1,-1), 0.3, GRIS_MED),
            ('VALIGN',     (0,0),  (-1,-1), 'TOP'),
        ]))
        story.append(tbl_c)
    story.append(Spacer(1, 0.15*cm))

    res1 = Table([[Paragraph('RESULTADO MÉTODO 1', s['ch']), Paragraph(fmt_u(m1_total), s['cvb'])]],
                 colWidths=[10*cm, 6.5*cm])
    res1.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), AZUL),
        ('BACKGROUND', (0,0), (1,0), VERDE_CLARO),
        ('ROWPADDING', (0,0), (-1,-1), 7),
        ('BOX',        (0,0), (-1,-1), 0.5, ORO),
    ]))
    story.append(res1)
    story.append(Spacer(1, 0.4*cm))

    # M2
    if val_cat_usd > 0:
        story.append(Paragraph('MÉTODO 2 – Intrínseco (Base Catastral)', s['sec']))
        story.append(sep(color=GRIS_MED, t=0.8))
        story.append(Paragraph(
            f'Factor de mercado {factor} aplicado (zona {zona} – catastro representa el '
            f'{round(100/factor)}% del valor real). Justificado por condicionantes del inmueble y entorno.',
            s['body']))
        calc_m2 = [
            ['Concepto','Cálculo','Resultado'],
            ['Valuación fiscal tierra','–', fmt_p(val_tierra)],
            [f'Conversión USD (TC Blue {fmt_p(tc_bl)})', f'÷ {fmt_p(tc_bl)}', fmt_u(val_cat_usd)],
            [f'Factor mercado × {factor}','–','–'],
            ['Valor estimado – Método 2','–', fmt_u(m2_total)],
        ]
        rows_m2 = []
        for i, row in enumerate(calc_m2):
            if i == 0: rows_m2.append([Paragraph(x, s['ch']) for x in row])
            elif i == len(calc_m2)-1: rows_m2.append([Paragraph(x, s['cvb']) for x in row])
            else: rows_m2.append([Paragraph(x, s['cv']) for x in row])
        tbl_m2 = Table(rows_m2, colWidths=[7*cm, 5.5*cm, 4*cm])
        tbl_m2.setStyle(TableStyle([
            ('BACKGROUND', (0,0),  (-1,0),  AZUL),
            ('BACKGROUND', (0,-1), (-1,-1), VERDE_CLARO),
            ('ROWPADDING', (0,0),  (-1,-1), 6),
            ('GRID',       (0,0),  (-1,-1), 0.3, GRIS_MED),
        ]))
        story.append(tbl_m2)
        res2 = Table([[Paragraph('RESULTADO MÉTODO 2', s['ch']), Paragraph(fmt_u(m2_total), s['cvb'])]],
                     colWidths=[10*cm, 6.5*cm])
        res2.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (0,0), AZUL),
            ('BACKGROUND', (0,0), (1,0), VERDE_CLARO),
            ('ROWPADDING', (0,0), (-1,-1), 7),
            ('BOX',        (0,0), (-1,-1), 0.5, ORO),
        ]))
        story.append(Spacer(1, 0.2*cm))
        story.append(res2)
        story.append(Spacer(1, 0.4*cm))

    # M3
    story.append(Paragraph('MÉTODO 3 – Valor Residual (Desarrollo Inmobiliario)', s['sec']))
    story.append(sep(color=GRIS_MED, t=0.8))
    calc_m3 = [
        ['Concepto','Cálculo','USD'],
        ['(+) Ingreso bruto',            f'{round(sup_vendible)} m² × USD {precio_venta}', fmt_u(ingreso)],
        ['(−) Costo construcción',       f'{round(sup_const)} m² × USD 867',               f'− {fmt_u(costo_const)}'],
        ['(−) Demolición (FACTOR CLAVE)',f'{sup_demo} m² × USD {costo_demo}',               f'− {fmt_u(costo_demol)}' if costo_demol > 0 else 'No aplica'],
        ['(−) Honorarios 10%',           '–',                                                f'− {fmt_u(honorarios)}'],
        ['(−) Comercialización 4%',      '–',                                                f'− {fmt_u(gastos_com)}'],
        ['(−) Utilidad 18%',             '–',                                                f'− {fmt_u(utilidad)}'],
        ['VALOR RESIDUAL',               '–',                                                fmt_u(m3_total) if m3_total > 0 else 'NEGATIVO'],
    ]
    rows_m3 = []
    for i, row in enumerate(calc_m3):
        if i == 0: rows_m3.append([Paragraph(x, s['ch']) for x in row])
        elif i == len(calc_m3)-1: rows_m3.append([Paragraph(x, s['cvb']) for x in row])
        else: rows_m3.append([Paragraph(x, s['cv']) for x in row])
    tbl_m3 = Table(rows_m3, colWidths=[6.5*cm, 5.5*cm, 4.5*cm])
    tbl_m3.setStyle(TableStyle([
        ('BACKGROUND', (0,0),  (-1,0),  AZUL),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#FDECEA') if m3_total <= 0 else VERDE_CLARO),
        ('BACKGROUND', (0,3),  (-1,3),  colors.HexColor('#FFF3CD')),
        ('ROWPADDING', (0,0),  (-1,-1), 6),
        ('GRID',       (0,0),  (-1,-1), 0.3, GRIS_MED),
    ]))
    story.append(tbl_m3)
    if m3_total <= 0:
        story.append(Spacer(1, 0.1*cm))
        story.append(Paragraph(
            'Resultado negativo: el costo de demolición y las restricciones normativas '
            'limitan el atractivo para desarrolladores, reforzando el criterio conservador adoptado.',
            s['body']))
    story.append(Spacer(1, 0.4*cm))

    # ── 4. RESUMEN ────────────────────────────────────────────
    story.append(Paragraph('4. CUADRO RESUMEN Y VALOR ADOPTADO', s['sec']))
    story.append(sep())
    metodos = [['Método','Valor USD','Ponderación','Observaciones'],
               ['1 – Comparativo', fmt_u(m1_total), 'Adoptado', fmt_m2(val_m2)]]
    if val_cat_usd > 0:
        metodos.append(['2 – Intrínseco', fmt_u(m2_total), 'Adoptado', f'Factor {factor}'])
    metodos.append(['3 – Residual', fmt_u(m3_total) if m3_total > 0 else 'Negativo', 'Referencia', '–'])
    metodos.append(['VALOR ADOPTADO', fmt_u(valor_adoptado), '✓', 'Promedio M1+M2'])
    rows_res = []
    for i, row in enumerate(metodos):
        if i == 0: rows_res.append([Paragraph(x, s['ch']) for x in row])
        elif row[0] == 'VALOR ADOPTADO': rows_res.append([Paragraph(x, s['cvb']) for x in row])
        else: rows_res.append([Paragraph(x, s['cv']) for x in row])
    tbl_res = Table(rows_res, colWidths=[4.5*cm, 3*cm, 2.5*cm, 6.5*cm])
    tbl_res.setStyle(TableStyle([
        ('BACKGROUND', (0,0),  (-1,0),  AZUL),
        ('BACKGROUND', (0,-1), (-1,-1), VERDE_CLARO),
        ('ROWPADDING', (0,0),  (-1,-1), 6),
        ('GRID',       (0,0),  (-1,-1), 0.3, GRIS_MED),
    ]))
    story.append(tbl_res)
    story.append(Spacer(1, 0.3*cm))

    val_box = [
        [Paragraph('VALOR DE TASACIÓN ADOPTADO', s['ch'])],
        [Paragraph(fmt_u(valor_adoptado), s['num'])],
        [Paragraph(f"TC Oficial: {fmt_p(valor_adoptado*tc_of)}    |    TC Blue: {fmt_p(valor_adoptado*tc_bl)}", s['subtit'])],
        [Paragraph(f'Tipos de cambio al {hora_tc} hs — Fuente: {tc_fuente}', s['pie'])],
        [Paragraph('Vigencia: 90 días corridos desde la fecha de emisión', s['pie'])],
    ]
    tbl_val = Table(val_box, colWidths=[16.5*cm])
    tbl_val.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0),  AZUL),
        ('BACKGROUND', (0,1), (-1,-1), GRIS_CLARO),
        ('ROWPADDING', (0,0), (-1,-1), 9),
        ('BOX',        (0,0), (-1,-1), 2, ORO),
    ]))
    story.append(tbl_val)
    story.append(Spacer(1, 0.4*cm))

    # ── 5. PRECIO COMERCIALIZACIÓN ────────────────────────────
    story.append(Paragraph('5. PRECIO DE COMERCIALIZACIÓN SUGERIDO', s['sec']))
    story.append(sep())
    fact_rows = [
        ['Factor','Fundamento','%'],
        ['Zona de mercado',           f"Zona {zona} – baja rotación de inmuebles.", '5–7%'],
        ['Venta en plazo reducido',   'Comitente requiere comercialización rápida.', '5–7%'],
        ['Demolición (FACTOR CLAVE)', f"Construcciones existentes ({tipo_demo}). Costo estimado: {fmt_u(costo_demol)}. Principal condicionante del valor." if costo_demol > 0 else 'No aplica en este caso.', '3–6%' if costo_demol > 0 else '–'],
        ['No inspección interior',    'Incertidumbre adicional para el adquirente.', '2–3%'],
        ['DESCUENTO TOTAL',           '–', f'{int(descuento)}–{int(descuento+5)}%'],
    ]
    rows_f = []
    for i, row in enumerate(fact_rows):
        if i == 0: rows_f.append([Paragraph(x, s['ch']) for x in row])
        elif i == len(fact_rows)-1: rows_f.append([Paragraph(x, s['cvb']) for x in row])
        else: rows_f.append([Paragraph(x, s['cv']) for x in row])
    tbl_f = Table(rows_f, colWidths=[4.5*cm, 9.5*cm, 2.5*cm])
    tbl_f.setStyle(TableStyle([
        ('BACKGROUND', (0,0),  (-1,0),  AZUL),
        ('BACKGROUND', (0,-1), (-1,-1), VERDE_CLARO),
        ('BACKGROUND', (0,3),  (-1,3),  colors.HexColor('#FFF3CD')),
        ('ROWPADDING', (0,0),  (-1,-1), 6),
        ('GRID',       (0,0),  (-1,-1), 0.3, GRIS_MED),
        ('VALIGN',     (0,0),  (-1,-1), 'TOP'),
    ]))
    story.append(tbl_f)
    story.append(Spacer(1, 0.3*cm))

    precio_box = [
        [Paragraph('PRECIO DE COMERCIALIZACIÓN SUGERIDO', s['ch'])],
        [Paragraph(f'{fmt_u(precio_min)} – {fmt_u(precio_max)}', s['num'])],
        [Paragraph(f'Descuento {int(descuento)}–{int(descuento+5)}% sobre valor de tasación {fmt_u(valor_adoptado)}.', s['subtit'])],
    ]
    tbl_p = Table(precio_box, colWidths=[16.5*cm])
    tbl_p.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0),  AZUL),
        ('BACKGROUND', (0,1), (-1,-1), GRIS_CLARO),
        ('ROWPADDING', (0,0), (-1,-1), 9),
        ('BOX',        (0,0), (-1,-1), 2, ORO),
    ]))
    story.append(tbl_p)
    story.append(Spacer(1, 0.4*cm))

    # ── 6. FIRMA ──────────────────────────────────────────────
    story.append(Paragraph('6. FIRMA Y CERTIFICACIÓN', s['sec']))
    story.append(sep())
    story.append(Spacer(1, 0.8*cm))
    firma = [
        [Paragraph('_______________________________', s['fnm'])],
        [Paragraph('RODI MATÍAS NICOLÁS', s['fnm'])],
        [Paragraph('Corredor Público Inmobiliario  –  Matrícula N.° 7524', s['fdt'])],
        [Paragraph('Grupo Rodi Inmobiliaria  –  Córdoba, Argentina', s['fdt'])],
        [Paragraph(f'Fecha: {fecha_hoy}', s['fdt'])],
    ]
    tbl_firma = Table(firma, colWidths=[16.5*cm])
    tbl_firma.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('ROWPADDING',(0,0),(-1,-1),3)]))
    story.append(tbl_firma)
    story.append(Spacer(1, 0.4*cm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=GRIS_MED, spaceAfter=6))
    story.append(Paragraph(
        'Informe elaborado conforme Ley N.° 9445 de la Provincia de Córdoba. '
        'Información confidencial de uso exclusivo del solicitante.',
        s['pie']))

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════
# RUTAS API
# ══════════════════════════════════════════════════════════════
@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/api/tipos-cambio', methods=['GET'])
def api_tipos_cambio():
    """Retorna TC oficial y blue en tiempo real desde bluelytics.com.ar"""
    data = obtener_tipos_cambio()
    return jsonify(data)

@app.route('/api/normativa', methods=['POST'])
def api_normativa():
    data = request.get_json()
    direccion = data.get('direccion', '')
    if not direccion:
        return jsonify({'error': 'Dirección requerida'}), 400
    resultado = obtener_normativa(direccion)
    if resultado:
        return jsonify({'ok': True, 'data': resultado})
    return jsonify({'ok': False, 'mensaje': 'No se pudo obtener la normativa automáticamente'})

@app.route('/api/comparables', methods=['POST'])
def api_comparables():
    data       = request.get_json()
    barrio     = data.get('barrio', '')
    superficie = float(data.get('superficie', 500))
    tipo_bien  = data.get('tipoBien', 'terreno')
    if not barrio:
        return jsonify({'error': 'Barrio requerido'}), 400
    comparables = obtener_comparables(barrio, superficie, tipo_bien)
    sugerencia  = sugerir_valor_m2(
        comparables, superficie,
        data.get('zona', 'deprimida'),
        data.get('tipoDemo', 'ninguna') != 'ninguna'
    )
    return jsonify({'ok': True, 'comparables': comparables, 'sugerencia': sugerencia})

@app.route('/api/sugerir-valor', methods=['POST'])
def api_sugerir_valor():
    data       = request.get_json()
    comps      = data.get('comparables', [])
    superficie = float(data.get('superficie', 500))
    zona       = data.get('zona', 'deprimida')
    demo       = data.get('tipoDemo', 'ninguna') != 'ninguna'
    sugerencia = sugerir_valor_m2(comps, superficie, zona, demo)
    if sugerencia:
        return jsonify({'ok': True, 'sugerencia': sugerencia})
    return jsonify({'ok': False, 'mensaje': 'Insuficientes comparables para calcular sugerencia'})

@app.route('/generar', methods=['POST'])
def generar():
    datos = request.get_json()
    if not datos:
        return jsonify({'error': 'No se recibieron datos'}), 400
    try:
        pdf_buf  = generar_pdf(datos)
        titular  = datos.get('titular', 'tasacion').replace(' ', '_')
        filename = f"Tasacion_{titular}.pdf"
        return send_file(pdf_buf, mimetype='application/pdf',
                        as_attachment=True, download_name=filename)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5001)
