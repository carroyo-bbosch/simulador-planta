import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import smartsheet
import re
import unicodedata
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import numpy as np

st.set_page_config(page_title="Control de Despachos & Simulador de Carga", layout="wide")
st.markdown("<style>[data-testid='stSidebar']>div:first-child{padding-top:.55rem}[data-testid='stSidebar'] .stVerticalBlock{gap:.35rem}</style>", unsafe_allow_html=True)

# -------------------------------------------------------------
# 0. CONFIGURACIÓN BASE, OEE POR PROCESO Y CALENDARIO CHILE
# -------------------------------------------------------------
SHEET_ID_ESTIMACION_DEF = 5915829825654660
UMBRAL_TERMINADO_KG = 10.0
PROCESOS = ["Ingeniería", "CNCL", "DoblezL", "Soldadura", "Fabricado", "Galvanizado", "Embalaje"]
DOTACION_DEF = {"Ingeniería": 8, "CNCL": 12, "DoblezL": 3, "Soldadura": 12, "Fabricado": 1, "Galvanizado": 1, "Embalaje": 8}
RATIO_DEF = {"Ingeniería": 120.0, "CNCL": 120.0, "DoblezL": 100.0, "Soldadura": 60.0, "Fabricado": 1500.0, "Galvanizado": 2000.0, "Embalaje": 350.0}
OEE_DEF = {"Ingeniería": 90.0, "CNCL": 45.0, "DoblezL": 90.0, "Soldadura": 90.0, "Fabricado": 90.0, "Galvanizado": 90.0, "Embalaje": 90.0}
VERSION_KEY = "v19_umbral_10kg_terminado"

# Feriados nacionales de Chile (no regionales) 2025 - 2028
FERIADOS_CL = {pd.Timestamp(d).normalize() for d in [
    "2025-01-01","2025-04-18","2025-04-19","2025-05-01","2025-05-21","2025-06-20","2025-06-29","2025-07-16","2025-08-15","2025-09-18","2025-09-19","2025-10-12","2025-10-31","2025-11-01","2025-12-08","2025-12-25",
    "2026-01-01","2026-04-03","2026-04-04","2026-05-01","2026-05-21","2026-06-21","2026-06-29","2026-07-16","2026-08-15","2026-09-18","2026-09-19","2026-10-12","2026-10-31","2026-11-01","2026-12-08","2026-12-25",
    "2027-01-01","2027-03-26","2027-03-27","2027-05-01","2027-05-21","2027-06-21","2027-06-28","2027-07-16","2027-08-15","2027-09-17","2027-09-18","2027-09-19","2027-10-11","2027-10-31","2027-11-01","2027-12-08","2027-12-25",
    "2028-01-01","2028-04-14","2028-04-15","2028-05-01","2028-05-21","2028-06-20","2028-06-26","2028-07-16","2028-08-15","2028-09-18","2028-09-19","2028-09-20","2028-10-09","2028-10-27","2028-11-01","2028-12-08","2028-12-25",
]}
# Lun-Mar: 8-18h (-1h colación)=9h | Mié-Jue: 8-17h (-1h)=8h | Vie: 8-15h (-1h)=6h
HRS_SEMANA = {0: 9.0, 1: 9.0, 2: 8.0, 3: 8.0, 4: 6.0, 5: 0.0, 6: 0.0}

def hrs_dia_cl(dt):
    d = pd.Timestamp(dt).normalize()
    return 0.0 if d in FERIADOS_CL else HRS_SEMANA.get(d.weekday(), 0.0)

def info_sem_iso(sem):
    try:
        y, w = map(int, str(sem).split("-"))
        lun = pd.Timestamp.fromisocalendar(y, w, 1)
        hab = [h for i in range(7) if (h := hrs_dia_cl(lun + pd.Timedelta(days=i))) > 0]
        tot = sum(hab)
        return tot, len(hab), (tot / len(hab) if hab else 0.0)
    except Exception:
        return 40.0, 5, 8.0

def sumar_dias_cl(base, n):
    dt = pd.Timestamp(base).normalize()
    if n == 0:
        while hrs_dia_cl(dt) <= 0: dt += pd.Timedelta(days=1)
        return dt
    step, rem = (1 if n > 0 else -1), abs(int(n))
    while rem > 0:
        dt += pd.Timedelta(days=step)
        if hrs_dia_cl(dt) > 0: rem -= 1
    return dt

def contar_dias_cl(f1, f2):
    d1, d2 = pd.Timestamp(f1).normalize(), pd.Timestamp(f2).normalize()
    if d2 == d1: return 0
    if d2 > d1:
        return sum(1 for i in range(1, (d2 - d1).days + 1) if hrs_dia_cl(d1 + pd.Timedelta(days=i)) > 0)
    return -sum(1 for i in range(1, (d1 - d2).days + 1) if hrs_dia_cl(d2 + pd.Timedelta(days=i)) > 0)

# -------------------------------------------------------------
# 1. CONEXIÓN PARALELA A SMARTSHEET Y HELPERS
# -------------------------------------------------------------
def clean_txt(t):
    return re.sub(r'[^a-zA-Z0-9]', '', unicodedata.normalize('NFKD', str(t)).encode('ASCII', 'ignore').decode('utf-8')).lower() if isinstance(t, str) else ""

def _fetch_sheet(token, sid):
    cli = smartsheet.Smartsheet(token)
    h = cli.Sheets.get_sheet(int(sid))
    return pd.DataFrame([[c.value for c in f.cells] for f in h.rows], columns=[col.title.strip() for col in h.columns])

@st.cache_data(ttl=600, show_spinner=False)
def cargar_todas_las_hojas(token, id_back, id_prod, id_est):
    with ThreadPoolExecutor(max_workers=3) as ex:
        f1 = ex.submit(_fetch_sheet, token, id_back)
        f2 = ex.submit(_fetch_sheet, token, id_prod)
        f3 = ex.submit(_fetch_sheet, token, id_est)
        return f1.result(), f2.result(), f3.result()

def buscar_col_kg(cols, clave, prios):
    for t in prios:
        tc = clean_txt(t)
        for c in cols:
            if clean_txt(c) == tc: return c
    cands = [c for c in cols if ("kilo" in c.lower() or "kg" in c.lower()) and clave in clean_txt(c) and "fecha" not in c.lower()]
    if cands: return next((x for x in cands if "por" in x.lower()), cands[0])
    return next((c for c in cols if clave in clean_txt(c) and not any(x in c.lower() for x in ["check","¿","?","listo","ok","estado","pasa","fecha"])), None)

def num_val(v):
    if pd.isna(v) or v is None: return 0.0
    if isinstance(v, (int, float)): return max(0.0, float(v))
    s = str(v).strip()
    if not s or s.lower() in ["none", "nan", "-", "false", "true"]: return 0.0
    if "," in s and "." in s: s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s: s = s.replace(",", ".")
    try: return max(0.0, float(s))
    except Exception: return 0.0

def kg_pendiente_val(v):
    """Retorna 0.0 si el remanente por procesar es <= 10 kg (proceso terminado)."""
    val = num_val(v)
    return val if val > UMBRAL_TERMINADO_KG else 0.0

def pct_val(v):
    if pd.isna(v) or v is None: return 0.0
    try:
        x = float(str(v).replace("%", "").replace(",", ".").strip()) if isinstance(v, str) else float(v)
        return 0.0 if x <= 0 else (x if x <= 1.0 else min(1.0, x / 100.0))
    except Exception:
        return 0.0

fmt_u = lambda kg, f, fmt: f"{float(kg) / f:{fmt}}"

def sems_consecutivas(w_ini, w_fin, min_c=1):
    try:
        y1, wk1 = map(int, str(w_ini).split("-")); y2, wk2 = map(int, str(w_fin).split("-"))
        d1, d2 = pd.Timestamp.fromisocalendar(y1, wk1, 4), pd.Timestamp.fromisocalendar(y2, wk2, 4)
    except Exception:
        d1 = pd.Timestamp.now().normalize(); d2 = d1 + pd.Timedelta(weeks=max(1, min_c))
    if d1 > d2: d2 = d1
    res, cur = [], d1
    while cur <= d2 or len(res) < min_c:
        iy, iw, _ = cur.isocalendar()
        res.append(f"{iy}-{iw:02d}")
        cur += pd.Timedelta(weeks=1)
    return res

# -------------------------------------------------------------
# 2. GENERADOR VECTORIAL PDF 1.4 ESTÁNDAR
# -------------------------------------------------------------
class VectorPDFBuilder:
    def __init__(self):
        self.W, self.H, self.margin = 841.89, 595.28, 22.0
        self.pages, self.cmds, self.y = [], [], self.H - 22.0

    def new_page(self):
        if self.cmds: self.pages.append("\n".join(self.cmds))
        self.cmds, self.y = [], self.H - self.margin

    @staticmethod
    def _esc(txt):
        return str(txt).encode("cp1252", "replace").decode("cp1252").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def _rgb(self, hx):
        h = hx.lstrip("#")
        return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

    def rect(self, x, y_top, w, h, fill=None, stroke=None, lw=0.4):
        yb = y_top - h
        if fill:
            r, g, b = self._rgb(fill); self.cmds.append(f"{r:.3f} {g:.3f} {b:.3f} rg")
        if stroke:
            r, g, b = self._rgb(stroke); self.cmds.append(f"{lw:.2f} w {r:.3f} {g:.3f} {b:.3f} RG")
        op = "B" if (fill and stroke) else ("f" if fill else ("S" if stroke else ""))
        if op: self.cmds.append(f"{x:.2f} {yb:.2f} {w:.2f} {h:.2f} re {op}")

    def text(self, x, y_base, txt, size=8.0, bold=False, color="#0f172a"):
        r, g, b = self._rgb(color)
        self.cmds.append(f"BT {'/F2' if bold else '/F1'} {size:.1f} Tf {r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y_base:.2f} Td ({self._esc(txt)}) Tj ET")

    def cell(self, x, y_top, w, h, txt, align="L", size=7.2, bold=False, color="#0f172a", fill=None, stroke="#cbd5e1"):
        if fill or stroke: self.rect(x, y_top, w, h, fill=fill, stroke=stroke)
        s = str(txt)
        mc = max(1, int((w - 5) / (size * 0.49)))
        if len(s) > mc: s = s[:mc-1] + "."
        tw = len(s) * (size * 0.47)
        tx = max(x + 2.5, x + w - 3.5 - tw) if align == "R" else (max(x + 2.5, x + (w - tw) / 2.0) if align == "C" else x + 3.5)
        self.text(tx, y_top - (h / 2.0) - (size * 0.32), s, size=size, bold=bold, color=color)

    def ensure_space(self, h_req):
        if self.y - h_req < self.margin: self.new_page()

    def build(self):
        if self.cmds: self.pages.append("\n".join(self.cmds))
        if not self.pages: self.pages.append("")
        objs = [
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj", b"",
            b"3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >> endobj",
            b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >> endobj"
        ]
        kids, oid = [], 5
        for ps in self.pages:
            pb = ps.encode("cp1252", "replace")
            pid, cid = oid, oid + 1; oid += 2
            kids.append(f"{pid} 0 R")
            objs.append(f"{pid} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.W:.2f} {self.H:.2f}] /Contents {cid} 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >> endobj".encode("ascii"))
            objs.append(f"{cid} 0 obj << /Length {len(pb)} >> stream\n".encode("ascii") + pb + b"\nendstream endobj")
        objs[1] = f"2 0 obj << /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >> endobj".encode("ascii")
        buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offs = [0]
        for o in objs: offs.append(len(buf)); buf.extend(o + b"\n")
        xref = len(buf)
        buf.extend(f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode("ascii"))
        for off in offs[1:]: buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
        buf.extend(f"trailer << /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii"))
        return bytes(buf)

def crear_pdf_cotizacion(proy, cli, f_ini_req, f_term_des, f_real_cola, dias_ciclo, dias_desv, tot_cot_fmt, oee_cncl, oee_otros, u_lbl, df_est, df_gantt, sems, df_cuellos, df_imp, df_libres):
    pdf = VectorPDFBuilder()
    pdf.new_page()
    M, UW = pdf.margin, pdf.W - 2 * pdf.margin

    pdf.rect(0, pdf.H, pdf.W, 44, fill="#135b79")
    pdf.text(M, pdf.H - 18, f"INFORME DE FACTIBILIDAD Y CARGA DE PLANTA - COTIZACIÓN ({u_lbl.upper()})", size=13, bold=True, color="#ffffff")
    pdf.text(M, pdf.H - 32, f"Emitido: {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  Calendario Chile (40h/sem, sin feriados nac.)  |  OEE CNCL: {oee_cncl:.0f}% / Otros: {oee_otros:.0f}%", size=8.2, color="#e2e8f0")
    pdf.y = pdf.H - 54

    pdf.text(M, pdf.y - 8, f"1. Resumen de Cotización: {proy}   |   Cliente: {cli}", size=10, bold=True, color="#0f172a")
    pdf.y -= 15
    es_factible = (dias_desv <= 0)
    kpis = [
        ("Inicio Requerido (Hacia Atrás)", f_ini_req, "#f8fafc", "#0f172a"),
        ("Fecha Término Deseado", f_term_des, "#f0f9ff", "#135b79"),
        ("Entrega Real c/ Carga Planta", f_real_cola, "#dcfce7" if es_factible else "#fef2f2", "#166534" if es_factible else "#dc2626"),
        ("Ciclo / Total Cotizado", f"{dias_ciclo}d háb. | {tot_cot_fmt} {u_lbl}", "#f8fafc", "#0f172a"),
        ("Factibilidad en Plazo", "FACTIBLE EN FECHA" if es_factible else f"DESVIACIÓN +{dias_desv} DÍAS", "#dcfce7" if es_factible else "#fee2e2", "#166534" if es_factible else "#991b1b"),
    ]
    kw = UW / len(kpis)
    for ik, (kl, kv, kbg, kcol) in enumerate(kpis):
        kx = M + ik * kw
        pdf.cell(kx, pdf.y, kw, 14, kl, align="C", size=7.0, color="#475569", fill=kbg)
        pdf.cell(kx, pdf.y - 14, kw, 16, kv, align="C", size=8.0, bold=True, color=kcol, fill=kbg)
    pdf.y -= 38

    pdf.ensure_space(60)
    pdf.text(M, pdf.y - 8, f"2. Desglose de Carga por Tipología de Estructura ({u_lbl})", size=10, bold=True, color="#0f172a")
    pdf.y -= 14
    headers_e = ["Estructura", "% Ing", "% CNCL", "% Dob", "% Sol", "Cotizado", "Ingeniería", "CNCL", "DoblezL", "Soldadura", "Fabricado", "Galvaniz.", "Embalaje"]
    keys_e = ["Estructura", "% Ing", "% CNCL", "% Dob", "% Sol", "Cotizado", "Ingeniería", "CNCL", "DoblezL", "Soldadura", "Fabricado", "Galvanizado", "Embalaje"]
    w_e = [132, 34, 38, 34, 34, 66, 66, 66, 65, 65, 66, 66, 66]
    cx = M
    for w, h in zip(w_e, headers_e):
        pdf.cell(cx, pdf.y, w, 17, h, align="L" if w > 100 else "C", size=7.2, bold=True, color="#ffffff", fill="#135b79", stroke="#0e3d54")
        cx += w
    pdf.y -= 17

    for ir, r in df_est.iterrows():
        pdf.ensure_space(16)
        is_tot = str(r.get("Estructura", "")).startswith("TOTAL")
        bg = "#f1f5f9" if is_tot else ("#ffffff" if ir % 2 == 0 else "#fafbfc")
        cx = M
        for ic, (w, key_col) in enumerate(zip(w_e, keys_e)):
            pdf.cell(cx, pdf.y, w, 15, str(r.get(key_col, "-")), align="L" if ic == 0 else ("C" if 1 <= ic <= 4 else "R"), size=7.2, bold=is_tot, fill=bg)
            cx += w
        pdf.y -= 15

    pdf.y -= 10
    pdf.ensure_space(80)
    pdf.text(M, pdf.y - 8, f"3. Carta Gantt / Cuadro de Carga Semanal Cotizada ({u_lbl})", size=10, bold=True, color="#0f172a")
    pdf.y -= 14
    sems_show = sems[:12]
    w_base = [84, 54, 54, 34, 56]
    w_sem = (UW - sum(w_base)) / max(1, len(sems_show))
    w_g = w_base + [w_sem] * len(sems_show)
    h_g = ["Procesos", "Inicio Req.", "Término Obj.", "Días", f"Total ({u_lbl})"] + sems_show
    cx = M
    for ic, (w, h) in enumerate(zip(w_g, h_g)):
        pdf.cell(cx, pdf.y, w, 17, h, align="L" if ic == 0 else "C", size=7.0, bold=True, color="#ffffff", fill="#135b79", stroke="#0e3d54")
        cx += w
    pdf.y -= 17

    for ig, r in df_gantt.iterrows():
        pdf.ensure_space(16)
        bg = "#ffffff" if ig % 2 == 0 else "#fafbfc"
        ov_set = r.get("_ov_sems", set())
        cx = M
        for ic, (w, col) in enumerate(zip(w_g, h_g)):
            val_s = str(r.get(col, "-"))
            is_ov = (ic >= 5 and col in ov_set and val_s != "-")
            pdf.cell(cx, pdf.y, w, 15, val_s, align="L" if ic == 0 else ("C" if ic in [1, 2] else "R"), size=7.0, bold=(ic in [0, 4] or is_ov), color="#dc2626" if is_ov else ("#cbd5e1" if val_s == "-" else "#0f172a"), fill="#fee2e2" if is_ov else bg)
            cx += w
        pdf.y -= 15

    pdf.y -= 10
    pdf.ensure_space(50)
    pdf.text(M, pdf.y - 8, "4. Diagnóstico de Sobrecapacidad y Ventanas Libres por Estación", size=10, bold=True, color="#0f172a")
    pdf.y -= 14

    if df_cuellos.empty:
        pdf.cell(M, pdf.y, UW, 16, "OK - Ningún proceso supera su capacidad semanal en la ventana cotizada.", align="L", size=8.0, bold=True, color="#166534", fill="#dcfce7")
        pdf.y -= 20
    else:
        w_c = [110, 75, 120, 120, 120, 120, UW - 665]
        h_c = ["Proceso Crítico", "Semana", f"Planta ({u_lbl})", f"Cotiz. ({u_lbl})", f"Total ({u_lbl})", f"Cap. ({u_lbl})", "Ocupación"]
        cx = M
        for ic, (w, h) in enumerate(zip(w_c, h_c)):
            pdf.cell(cx, pdf.y, w, 16, h, align="L" if ic == 0 else "C", size=7.2, bold=True, color="#ffffff", fill="#991b1b", stroke="#7f1d1d")
            cx += w
        pdf.y -= 16
        for _, rc in df_cuellos.head(6).iterrows():
            pdf.ensure_space(15)
            cx = M
            for ic, (w, v) in enumerate(zip(w_c, [rc.get("Proceso","-"), rc.get("Semana","-"), rc.get(f"Planta ({u_lbl})","-"), rc.get(f"Cotiz. ({u_lbl})","-"), rc.get(f"Total ({u_lbl})","-"), rc.get(f"Cap. ({u_lbl})","-"), rc.get("Ocupación","-")])):
                pdf.cell(cx, pdf.y, w, 14.5, str(v), align="L" if ic == 0 else ("C" if ic in [1, 6] else "R"), size=7.2, bold=(ic in [0, 6]), color="#991b1b" if ic == 6 else "#0f172a", fill="#fef2f2" if ic == 6 else "#ffffff")
                cx += w
            pdf.y -= 14.5
        pdf.y -= 8

    if not df_libres.empty:
        pdf.ensure_space(55)
        pdf.text(M, pdf.y - 7, "Disponibilidad y Rangos de Fechas sin Carga por Estación:", size=8.5, bold=True, color="#334155")
        pdf.y -= 13
        w_l = [95, 115, 85, 220, 180, UW - 695]
        h_l = ["Proceso", "Estado", "Fin Últ. Carga", "Ventanas Intermedias Libres", "Disponible Continuo Desde", f"Cap. Sem ({u_lbl})"]
        cx = M
        for ic, (w, h) in enumerate(zip(w_l, h_l)):
            pdf.cell(cx, pdf.y, w, 15, h, align="L" if ic in [0, 3, 4] else "C", size=7.0, bold=True, color="#ffffff", fill="#334155", stroke="#1e293b")
            cx += w
        pdf.y -= 15
        for il, rl in df_libres.iterrows():
            pdf.ensure_space(14)
            bg = "#ffffff" if il % 2 == 0 else "#fafbfc"
            cx = M
            for ic, (w, col_l) in enumerate(zip(w_l, h_l)):
                key_l = f"Cap. Semanal ({u_lbl})" if ic == 5 else col_l
                pdf.cell(cx, pdf.y, w, 14, str(rl.get(key_l, "-")), align="L" if ic in [0, 3, 4] else ("R" if ic == 5 else "C"), size=7.0, bold=(ic == 0), fill=bg)
                cx += w
            pdf.y -= 14

    return pdf.build()

# -------------------------------------------------------------
# 3. RENDERIZADORES HTML COMPACTOS + DIAGRAMA DE FLUJO SVG
# -------------------------------------------------------------
CSS_BASE = """
body{margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#fff}
.box{overflow:auto;border-radius:8px;border:1px solid #cbd5e1;box-shadow:0 2px 5px rgba(0,0,0,.04)}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:11px}
th,td{white-space:nowrap;padding:5px;border-bottom:1px solid #e2e8f0;border-right:1px solid #e2e8f0}
thead th{background:#135b79;color:#fff;position:sticky;top:0;z-index:6;border-bottom:2px solid #0e3d54;border-right:1px solid #1e6f91;font-weight:600;line-height:1.15}
tr.row:hover td{background:#f1f5f9!important}
.l{text-align:left}.r{text-align:right}.c{text-align:center}.b{font-weight:600;color:#0f172a}
.b-red{background:#fee2e2;color:#991b1b;padding:2px 6px;border-radius:10px;font-weight:700;font-size:10px}
.b-grn{background:#dcfce7;color:#166534;padding:2px 6px;border-radius:10px;font-weight:700;font-size:10px}
.b-blu{background:#e0f2fe;color:#075985;padding:2px 6px;border-radius:10px;font-weight:700;font-size:10px}
.ov{background:#fef2f2;color:#dc2626;font-weight:700;padding:1px 4px;border-radius:3px}
.dim{color:#cbd5e1}.cut{border-right:2px solid #94a3b8!important}.ws{width:64px;min-width:64px}
"""
html_box = lambda css, tbl, h: f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{CSS_BASE}.box{{max-height:{h}px}}{css}</style></head><body><div class='box'>{tbl}</div></body></html>"

def render_tabla_despachos(df_m, tot, f_u, fmt, u_lbl):
    css = ".c0{position:sticky;left:0;width:96px;min-width:96px;max-width:96px;z-index:3;overflow:hidden;text-overflow:ellipsis}.c1{position:sticky;left:96px;width:78px;min-width:78px;max-width:78px;z-index:3;overflow:hidden;text-overflow:ellipsis}.c2{position:sticky;left:174px;width:110px;min-width:110px;max-width:110px;z-index:3}thead .c0,thead .c1,thead .c2{z-index:10;background:#135b79}tfoot td{position:sticky;bottom:0;background:#f1f5f9;font-weight:700;color:#0f172a;border-top:2px solid #64748b;z-index:6}tfoot .c0,tfoot .c1,tfoot .c2{z-index:9;background:#f1f5f9}.wn{width:62px;min-width:62px}.wd{width:72px;min-width:72px}"
    rows = []
    for i, r in df_m.iterrows():
        bg = "#fff" if i % 2 == 0 else "#fafbfc"
        diag, tip = str(r["Diagnostico"]), r.get("Ruta_Detalle", "")
        b_cls = "b-red" if "Retraso" in diag else ("b-blu" if "Adelanto" in diag else "b-grn")
        badge = f"<span class='{b_cls}'>{diag}</span>"
        ci = "color:#dc2626;font-weight:600" if r["Kg por cargar (Ing)"] > UMBRAL_TERMINADO_KG else "color:#475569"
        cf = "color:#dc2626;font-weight:700" if r["Desviacion_Dias"] > 0 else ("color:#0369a1;font-weight:700" if "Adelanto" in diag else "color:#15803d;font-weight:700")
        rows.append(
            f"<tr class='row' style='background:{bg}'><td title='{tip}' class='l b c0' style='background:{bg}'>{r['Identificador de fila']}</td><td title='{tip}' class='l c1' style='background:{bg}'>{r['Subproyecto']}</td><td class='c c2 cut' style='background:{bg}'>{badge}</td>"
            f"<td class='r wn'>{fmt_u(r['Kg Orden Proceder'],f_u,fmt)}</td><td class='r wn' style='{ci}'>{fmt_u(r['Kg por cargar (Ing)'],f_u,fmt)}</td>"
            f"<td class='r wn'>{fmt_u(r['Kg por procesar CNCL'],f_u,fmt)}</td><td class='r wn'>{fmt_u(r['Kg por procesar DoblezL'],f_u,fmt)}</td>"
            f"<td class='r wn'>{fmt_u(r['Kg por procesar Soldadura'],f_u,fmt)}</td><td class='r wn'>{fmt_u(r['Kg por procesar Fabricado'],f_u,fmt)}</td>"
            f"<td class='r wn'>{fmt_u(r['Kg por galvanizar'],f_u,fmt)}</td><td class='r wn'>{fmt_u(r['Kg por Embalar'],f_u,fmt)}</td>"
            f"<td class='c wd'>{r['Fecha Programada']}</td><td class='c wd' style='{cf}'>{r['Fecha Estimada Finalizar']}</td></tr>"
        )
    tbl = f"""<table><thead><tr><th class='l c0'>ID Fila</th><th class='l c1'>Subproy.</th><th class='c c2 cut'>Estado / Desv.</th>
        <th class='r wn'>{u_lbl} Orden<br>Proceder</th><th class='r wn'>{u_lbl} cargar<br>(Ing)</th><th class='r wn'>{u_lbl}<br>CNCL</th><th class='r wn'>{u_lbl}<br>DoblezL</th>
        <th class='r wn'>{u_lbl}<br>Soldad.</th><th class='r wn'>{u_lbl}<br>Fabric.</th><th class='r wn'>{u_lbl}<br>Galvan.</th><th class='r wn'>{u_lbl}<br>Embalar</th>
        <th class='c wd'>Fecha<br>Prog.</th><th class='c wd'>Fecha Est.<br>Finalizar</th></tr></thead><tbody>{''.join(rows)}</tbody><tfoot><tr>
        <td class='l c0'>TOTAL</td><td class='l c1'>{len(df_m)} lotes</td><td class='c c2 cut'>-</td>
        {''.join(f"<td class='r'>{fmt_u(tot[k],f_u,fmt)}</td>" for k in ['orden','ing','cncl','doblez','sold','fab','galv','emb'])}<td class='c'>-</td><td class='c'>-</td></tr></tfoot></table>"""
    return html_box(css, tbl, 510)

def render_cuadro_semanal(piv, info_p, sems, cap_din, f_u, fmt, u_lbl, h=350, h_ini_lbl="Inicio<br>Est.", h_fin_lbl="Término<br>Est."):
    css = ".p0{position:sticky;left:0;width:110px;min-width:110px;max-width:110px;z-index:3}.p1{position:sticky;left:110px;width:76px;min-width:76px;max-width:76px;z-index:3}.p2{position:sticky;left:186px;width:76px;min-width:76px;max-width:76px;z-index:3}.p3{position:sticky;left:262px;width:48px;min-width:48px;max-width:48px;z-index:3}.p4{position:sticky;left:310px;width:85px;min-width:85px;max-width:85px;z-index:3}thead .p0,thead .p1,thead .p2,thead .p3,thead .p4{z-index:10;background:#135b79}"
    rows = []
    for i, pk in enumerate(PROCESOS):
        bg = "#fff" if i % 2 == 0 else "#fafbfc"
        inf = info_p.get(pk, {})
        tkg = float(inf.get("total_kg", 0.0))
        tds = [f"<td class='r ws'><span class='{'ov' if float(inf.get('check_ov',{}).get(s,piv.get((pk,s),0)))>cap_din.get(pk,{}).get(s,9e9)>0 else ''}'>{fmt_u(piv[(pk,s)],f_u,fmt)}</span></td>" if piv.get((pk,s),0)>0 else "<td class='r ws dim'>-</td>" for s in sems]
        rows.append(f"<tr class='row' style='background:{bg}'><td class='l b p0' style='background:{bg}'>{i+1}. {pk}</td><td class='c p1' style='background:{bg}'>{inf.get('ini','-')}</td><td class='c p2' style='background:{bg}'>{inf.get('fin','-')}</td><td class='r p3' style='background:{bg}'>{int(inf.get('dias',0))}</td><td class='r b p4 cut' style='background:{bg}'>{fmt_u(tkg,f_u,fmt) if tkg>0 else '-'}</td>{''.join(tds)}</tr>")
    th_sems = ''.join(f"<th class='c ws'>{s}</th>" for s in sems)
    tbl = f"<table><thead><tr><th class='l p0'>Procesos</th><th class='c p1'>{h_ini_lbl}</th><th class='c p2'>{h_fin_lbl}</th><th class='c p3'>Cant.<br>Días</th><th class='r p4 cut'>Total<br>({u_lbl})</th>{th_sems}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    return html_box(css, tbl, h)

def render_horas_dotacion(sems, dot_sem, hrs_info):
    css = ".h0{position:sticky;left:0;width:230px;min-width:230px;max-width:230px;z-index:4;background:#fff}thead .h0{background:#334155;z-index:8}"
    r1 = [f"<td class='r ws'>{hrs_info[s][2]:.2f} h</td>" for s in sems]
    r2 = [f"<td class='r ws b'>{dot_sem[s]*hrs_info[s][2]:,.1f} h</td>" for s in sems]
    r3 = [f"<td class='r ws b' style='color:#135b79;background:#f0f9ff'>{dot_sem[s]*hrs_info[s][0]:,.0f} h</td>" for s in sems]
    th_sems = ''.join(f"<th class='c ws' style='background:#334155'>{s}</th>" for s in sems)
    tbl = f"<table><thead><tr><th class='l h0 cut'>Desglose Horas Hábiles (Chile)</th>{th_sems}</tr></thead><tbody><tr><td class='l h0 cut'>⏱️ Horas Diarias por Dotación (Prom/Op)</td>{''.join(r1)}</tr><tr><td class='l h0 cut b' style='background:#fafbfc'>📊 Total Horas Diarias por Dotación</td>{''.join(r2)}</tr><tr><td class='l h0 cut b' style='background:#f8fafc;color:#135b79'>🗓️ Total Horas Semana por Dotación</td>{''.join(r3)}</tr></tbody></table>"
    return html_box(css, tbl, 155)

def render_carga_proceso(df_p, sems, cap_sem, f_u, fmt, u_lbl):
    css = """
    .d0{position:sticky;left:0;width:90px;min-width:90px;max-width:90px;z-index:3;overflow:hidden;text-overflow:ellipsis}
    .d1{position:sticky;left:90px;width:78px;min-width:78px;max-width:78px;z-index:3;overflow:hidden;text-overflow:ellipsis}
    .d2{position:sticky;left:168px;width:68px;min-width:68px;max-width:68px;z-index:3}
    .d3{position:sticky;left:236px;width:72px;min-width:72px;max-width:72px;z-index:3}
    .d4{position:sticky;left:308px;width:78px;min-width:78px;max-width:78px;z-index:3}
    .d5{position:sticky;left:386px;width:42px;min-width:42px;max-width:42px;z-index:3}
    .d6{position:sticky;left:428px;width:72px;min-width:72px;max-width:72px;z-index:3}
    .r1 th{top:0;background:#f8fafc;color:#475569;font-weight:700;z-index:7}
    .r2 th{top:27px;background:#f1f5f9;color:#0f172a;font-weight:700;z-index:7}
    .r3 th{top:54px;background:#135b79;color:#fff;z-index:7}
    .r1 .fix,.r2 .d0,.r2 .d1,.r2 .d2,.r2 .d3,.r2 .d4,.r2 .d5,.r2 .d6,.r3 .d0,.r3 .d1,.r3 .d2,.r3 .d3,.r3 .d4,.r3 .d5,.r3 .d6{z-index:11}
    .r1 .fix{left:0;width:500px;min-width:500px}
    .r2 .d0,.r2 .d1,.r2 .d2,.r2 .d3,.r2 .d4,.r2 .d5,.r2 .d6{background:#f1f5f9}
    .r3 .d0,.r3 .d1,.r3 .d2,.r3 .d3,.r3 .d4,.r3 .d5,.r3 .d6{background:#135b79}
    """
    tot_d = int(df_p["Cant_Dias"].sum()) if not df_p.empty else 0
    tot_k = float(df_p["Kilos_Procesar"].sum()) if not df_p.empty else 0.0
    tot_s = {s: float(df_p[s].sum()) if (not df_p.empty and s in df_p.columns) else 0.0 for s in sems}
    th_caps = "".join(f"<th class='r ws'>{fmt_u(cap_sem.get(s,0),f_u,fmt)}</th>" for s in sems)
    th_tots = [f"<th class='r ws'><span class='{'ov' if tot_s[s]>cap_sem.get(s,0)>0 else ''}'>{fmt_u(tot_s[s],f_u,fmt) if tot_s[s]>0 else '-'}</span></th>" for s in sems]
    rows = []
    for i, r in df_p.iterrows():
        bg, tip = ("#fff" if i % 2 == 0 else "#fafbfc"), r.get("Ruta_Tooltip", "")
        tds = [f"<td class='r ws'><span class='{'ov' if float(r.get(s,0))>cap_sem.get(s,0)>0 else ''}'>{fmt_u(r[s],f_u,fmt)}</span></td>" if float(r.get(s,0))>0 else "<td class='r ws dim'>-</td>" for s in sems]
        rows.append(
            f"<tr class='row' style='background:{bg}'>"
            f"<td title='{tip}' class='l b d0' style='background:{bg}'>{r['Identificador de fila']}</td>"
            f"<td title='{tip}' class='l d1' style='background:{bg}'>{r['Subproyecto']}</td>"
            f"<td class='c d2' style='background:{bg}'>{r.get('Fecha_Despacho','-')}</td>"
            f"<td class='c d3' style='background:{bg};color:#334155'>{r.get('Fecha_Obj_Proc','-')}</td>"
            f"<td class='c d4' style='background:{bg}'>{r.get('Fecha_Est_Display','-')}</td>"
            f"<td class='r d5' style='background:{bg}'>{int(r['Cant_Dias'])}</td>"
            f"<td class='r b d6 cut' style='background:{bg}'>{fmt_u(r['Kilos_Procesar'],f_u,fmt)}</td>{''.join(tds)}</tr>"
        )
    th_sems = ''.join(f"<th class='c ws'>{s}</th>" for s in sems)
    tbl = f"""<table><thead>
        <tr class='r1'><th colspan='7' class='r fix cut'>Capacidad Semanal ({u_lbl}):</th>{th_caps}</tr>
        <tr class='r2'><th class='l d0'>TOTAL PEND.</th><th class='l d1'>{len(df_p)} lotes</th><th class='c d2'>-</th><th class='c d3'>-</th><th class='c d4'>-</th><th class='r d5'>{tot_d}</th><th class='r d6 cut'>{fmt_u(tot_k,f_u,fmt)}</th>{''.join(th_tots)}</tr>
        <tr class='r3'><th class='l d0'>ID Fila</th><th class='l d1'>Subproy.</th><th class='c d2'>Fecha<br>Desp.</th><th class='c d3'>Fecha Obj.<br>Proceso</th><th class='c d4'>Fecha Est.<br>Entrega</th><th class='c d5'>Cant.<br>Días</th><th class='r d6 cut'>{u_lbl} por<br>Procesar</th>{th_sems}</tr>
    </thead><tbody>{''.join(rows)}</tbody></table>"""
    return html_box(css, tbl, 530)

def render_diagrama_flujo_svg(cola_info, proc_activo):
    nodes = [
        ("Ingeniería",  "Ingeniería",  15,  18),
        ("CNCL",        "CNCL",        185, 18),
        ("DoblezL",     "Doblez L",    345, 118),
        ("Soldadura",   "Soldadura",   445, 218),
        ("Fabricado",   "Fabricado",   565, 18),
        ("Galvanizado", "Galvanizado", 740, 18),
        ("Embalaje",    "Embalado",    915, 18),
    ]
    W_BOX, H_BOX = 136, 62
    arrows = [
        "M 151 49 L 179 49",
        "M 321 49 L 633 49 L 633 12",
        "M 253 80 L 253 149 L 339 149",
        "M 481 149 L 530 149 L 530 62 L 559 62",
        "M 413 180 L 413 249 L 439 249",
        "M 581 249 L 633 249 L 633 86",
        "M 701 49 L 734 49",
        "M 876 49 L 909 49",
    ]
    paths_svg = "".join(f'<path d="{d}" fill="none" stroke="#1e3a5f" stroke-width="1.6" marker-end="url(#arrow)"/>' for d in arrows)
    boxes_svg = []
    for key, label, x, y in nodes:
        ton_val, lotes_cnt = cola_info.get(key, (0.0, 0))
        is_sel = (key == proc_activo)
        stroke_col = "#0284c7" if is_sel else "#1e293b"
        stroke_w = "2.6" if is_sel else "1.3"
        hdr_fill = "#0284c7" if is_sel else "#135b79"
        body_fill = "#e0f2fe" if is_sel else "#f8fafc"
        boxes_svg.append(f"""
        <g>
            <rect x="{x}" y="{y}" width="{W_BOX}" height="{H_BOX}" rx="6" fill="{body_fill}" stroke="{stroke_col}" stroke-width="{stroke_w}"/>
            <rect x="{x}" y="{y}" width="{W_BOX}" height="22" rx="5" fill="{hdr_fill}"/>
            <text x="{x + W_BOX/2}" y="{y + 15}" text-anchor="middle" fill="#ffffff" font-size="11.5" font-weight="700">{label}</text>
            <text x="{x + W_BOX/2}" y="{y + 40}" text-anchor="middle" fill="#0f172a" font-size="12.5" font-weight="800">{ton_val:,.2f} Ton</text>
            <text x="{x + W_BOX/2}" y="{y + 55}" text-anchor="middle" fill="#475569" font-size="10">{lotes_cnt} lotes en cola</text>
        </g>
        """)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{{margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#fff}}.wrap{{width:100%;overflow-x:auto;border:1px solid #cbd5e1;border-radius:8px;background:#ffffff;padding:8px 0}}</style></head><body><div class="wrap"><svg viewBox="0 0 1068 296" width="100%" style="min-width:780px;max-height:295px;display:block"><defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#1e3a5f"/></marker></defs>{paths_svg}{''.join(boxes_svg)}</svg></div></body></html>"""

# -------------------------------------------------------------
# 4. MOTOR DE PROGRAMACIÓN ASAP CON TOPES 50% / 90% Y PRIORIZACIÓN EDD
# -------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def dist_semanal_cl(df_g):
    if df_g.empty:
        return pd.DataFrame(columns=["Semana", "Proceso", "Identificador de fila", "Estructura", "Kilos"])
    rows = []
    for _, r in df_g.iterrows():
        if r["Kilos"] <= 0: continue
        cur, end_n = pd.Timestamp(r["Inicio"]).normalize(), pd.Timestamp(r["Fin"]).normalize()
        hrs_w, tot_h = {}, 0.0
        while cur <= end_n:
            h = hrs_dia_cl(cur)
            if h > 0:
                iso = f"{cur.isocalendar()[0]}-{cur.isocalendar()[1]:02d}"
                hrs_w[iso] = hrs_w.get(iso, 0.0) + h
                tot_h += h
            cur += pd.Timedelta(days=1)
        if tot_h == 0:
            iso = f"{pd.Timestamp(r['Inicio']).isocalendar()[0]}-{pd.Timestamp(r['Inicio']).isocalendar()[1]:02d}"
            hrs_w, tot_h = {iso: 1.0}, 1.0
        for wk, hw in hrs_w.items():
            rows.append({"Semana": wk, "Proceso": r["Proceso"], "Identificador de fila": r.get("Identificador de fila", ""), "Estructura": r.get("Estructura", ""), "Kilos": (hw / tot_h) * r["Kilos"]})
    return pd.DataFrame(rows)

def ciclo_relativo_lote(kgs, cap_d):
    d = {p: kgs.get(p, 0.0) / cap_d[p] for p in PROCESOS}
    s_r, e_r = {}, {}
    s_r["Ingeniería"] = 0.0; e_r["Ingeniería"] = d["Ingeniería"]
    s_r["CNCL"] = 0.50 * d["Ingeniería"]; e_r["CNCL"] = s_r["CNCL"] + d["CNCL"]
    s_r["DoblezL"] = s_r["CNCL"] + 0.30 * d["CNCL"]; e_r["DoblezL"] = s_r["DoblezL"] + d["DoblezL"]
    s_r["Soldadura"] = max(s_r["DoblezL"] + 0.25 * d["DoblezL"], s_r["CNCL"] + 0.50 * d["CNCL"]) if d["DoblezL"] > 0 else s_r["CNCL"] + 0.30 * d["CNCL"]
    e_r["Soldadura"] = s_r["Soldadura"] + d["Soldadura"]
    s_r["Fabricado"] = (s_r["Soldadura"] + 0.50 * d["Soldadura"]) if d["Soldadura"] > 0 else ((s_r["DoblezL"] + 0.50 * d["DoblezL"]) if d["DoblezL"] > 0 else s_r["CNCL"] + 0.50 * d["CNCL"])
    e_r["Fabricado"] = max(s_r["Fabricado"] + d["Fabricado"], e_r["Ingeniería"], e_r["CNCL"], e_r["DoblezL"], e_r["Soldadura"])
    if d["Fabricado"] > 0: s_r["Fabricado"] = max(s_r["Fabricado"], e_r["Fabricado"] - d["Fabricado"])
    s_r["Galvanizado"] = max(s_r["Fabricado"] + 0.50 * d["Fabricado"], e_r["Soldadura"]) if d["Fabricado"] > 0 else e_r["Soldadura"]
    e_r["Galvanizado"] = max(s_r["Galvanizado"] + d["Galvanizado"], e_r["Fabricado"])
    if d["Galvanizado"] > 0: s_r["Galvanizado"] = max(s_r["Galvanizado"], e_r["Galvanizado"] - d["Galvanizado"])
    s_r["Embalaje"] = max(s_r["Galvanizado"] + 0.30 * d["Galvanizado"], e_r["Fabricado"]) if d["Galvanizado"] > 0 else e_r["Fabricado"]
    e_r["Embalaje"] = max(s_r["Embalaje"] + d["Embalaje"], e_r["Galvanizado"])
    if d["Embalaje"] > 0: s_r["Embalaje"] = max(s_r["Embalaje"], e_r["Embalaje"] - d["Embalaje"])
    return s_r, e_r, e_r["Embalaje"]

def programar_cola(kgs, cap_d, hoy_ts, cur_dict, min_off=0.0):
    d = {p: kgs.get(p, 0.0) / cap_d[p] for p in PROCESOS}
    s_a, e_a = {}, {}

    # 1. Ingeniería
    s_a["Ingeniería"] = max(min_off, cur_dict["Ingeniería"]) if d["Ingeniería"] > 0 else min_off
    e_a["Ingeniería"] = s_a["Ingeniería"] + d["Ingeniería"]
    if d["Ingeniería"] > 0:
        cur_dict["Ingeniería"] = e_a["Ingeniería"]

    # 2. CNCL (Ingeniería <= 50% avance CNCL)
    if d["CNCL"] > 0:
        req_s = s_a["Ingeniería"] + 0.50 * d["Ingeniería"]
        req_c50 = e_a["Ingeniería"] - 0.50 * d["CNCL"]
        s_a["CNCL"] = max(req_s, req_c50, cur_dict["CNCL"])
        e_a["CNCL"] = max(s_a["CNCL"] + d["CNCL"], e_a["Ingeniería"])
        s_a["CNCL"] = e_a["CNCL"] - d["CNCL"]
        cur_dict["CNCL"] = e_a["CNCL"]
    else:
        s_a["CNCL"] = e_a["Ingeniería"]
        e_a["CNCL"] = s_a["CNCL"]

    # 3. DoblezL
    if d["DoblezL"] > 0:
        req_dob = s_a["CNCL"] + 0.30 * d["CNCL"] if d["CNCL"] > 0 else e_a["Ingeniería"]
        s_a["DoblezL"] = max(req_dob, e_a["CNCL"] - 0.90 * d["DoblezL"], cur_dict["DoblezL"])
        e_a["DoblezL"] = max(s_a["DoblezL"] + d["DoblezL"], e_a["CNCL"])
        s_a["DoblezL"] = e_a["DoblezL"] - d["DoblezL"]
        cur_dict["DoblezL"] = e_a["DoblezL"]
    else:
        s_a["DoblezL"] = s_a["CNCL"] + 0.30 * d["CNCL"] if d["CNCL"] > 0 else e_a["Ingeniería"]
        e_a["DoblezL"] = s_a["DoblezL"]

    # 4. Soldadura
    if d["Soldadura"] > 0:
        if d["DoblezL"] > 0:
            req_sol = max(s_a["DoblezL"] + 0.25 * d["DoblezL"], e_a["DoblezL"] - 0.90 * d["Soldadura"])
            prev_e = e_a["DoblezL"]
        elif d["CNCL"] > 0:
            req_sol = max(s_a["CNCL"] + 0.50 * d["CNCL"], e_a["CNCL"] - 0.90 * d["Soldadura"])
            prev_e = e_a["CNCL"]
        else:
            req_sol = e_a["Ingeniería"]
            prev_e = e_a["Ingeniería"]
        s_a["Soldadura"] = max(req_sol, cur_dict["Soldadura"])
        e_a["Soldadura"] = max(s_a["Soldadura"] + d["Soldadura"], prev_e)
        s_a["Soldadura"] = e_a["Soldadura"] - d["Soldadura"]
        cur_dict["Soldadura"] = e_a["Soldadura"]
    else:
        s_a["Soldadura"] = e_a["DoblezL"] if d["DoblezL"] > 0 else (s_a["CNCL"] + 0.30 * d["CNCL"] if d["CNCL"] > 0 else e_a["Ingeniería"])
        e_a["Soldadura"] = s_a["Soldadura"]

    # 5. Fabricado (CNCL, DoblezL y Soldadura <= 90% avance Fabricado)
    max_up_fab = max(e_a["Ingeniería"], e_a["CNCL"], e_a["DoblezL"], e_a["Soldadura"])
    if d["Soldadura"] > 0:
        req_fab_s = s_a["Soldadura"] + 0.50 * d["Soldadura"]
    elif d["DoblezL"] > 0:
        req_fab_s = s_a["DoblezL"] + 0.50 * d["DoblezL"]
    elif d["CNCL"] > 0:
        req_fab_s = s_a["CNCL"] + 0.50 * d["CNCL"]
    else:
        req_fab_s = e_a["Ingeniería"]

    if d["Fabricado"] > 0:
        s_a["Fabricado"] = max(req_fab_s, max_up_fab - 0.90 * d["Fabricado"], cur_dict["Fabricado"])
        e_a["Fabricado"] = max(s_a["Fabricado"] + d["Fabricado"], max_up_fab)
        s_a["Fabricado"] = e_a["Fabricado"] - d["Fabricado"]
        cur_dict["Fabricado"] = e_a["Fabricado"]
    else:
        s_a["Fabricado"] = max_up_fab
        e_a["Fabricado"] = max_up_fab

    # 6. Galvanizado (Fabricado <= 90% avance Galvanizado)
    if d["Galvanizado"] > 0:
        req_gal_s = s_a["Fabricado"] + 0.50 * d["Fabricado"] if d["Fabricado"] > 0 else e_a["Fabricado"]
        s_a["Galvanizado"] = max(req_gal_s, e_a["Fabricado"] - 0.90 * d["Galvanizado"], cur_dict["Galvanizado"])
        e_a["Galvanizado"] = max(s_a["Galvanizado"] + d["Galvanizado"], e_a["Fabricado"])
        s_a["Galvanizado"] = e_a["Galvanizado"] - d["Galvanizado"]
        cur_dict["Galvanizado"] = e_a["Galvanizado"]
    else:
        s_a["Galvanizado"] = e_a["Fabricado"]
        e_a["Galvanizado"] = e_a["Fabricado"]

    # 7. Embalaje (Galvanizado <= 90% avance Embalaje)
    if d["Embalaje"] > 0:
        req_emb_s = s_a["Galvanizado"] + 0.30 * d["Galvanizado"] if d["Galvanizado"] > 0 else e_a["Galvanizado"]
        s_a["Embalaje"] = max(req_emb_s, e_a["Galvanizado"] - 0.90 * d["Embalaje"], cur_dict["Embalaje"])
        e_a["Embalaje"] = max(s_a["Embalaje"] + d["Embalaje"], e_a["Galvanizado"])
        s_a["Embalaje"] = e_a["Embalaje"] - d["Embalaje"]
        cur_dict["Embalaje"] = e_a["Embalaje"]
    else:
        s_a["Embalaje"] = e_a["Galvanizado"]
        e_a["Embalaje"] = e_a["Galvanizado"]

    dias_tot = int(np.ceil(max(0.0, e_a["Embalaje"])))
    return s_a, e_a, dias_tot, (sumar_dias_cl(hoy_ts, dias_tot) if dias_tot > 0 else hoy_ts)

def ciclo_relativo_lote(kgs, cap_d):
    zero_cur = {p: 0.0 for p in PROCESOS}
    s_r, e_r, _, _ = programar_cola(kgs, cap_d, pd.Timestamp.now().normalize(), zero_cur, min_off=0.0)
    return s_r, e_r, e_r["Embalaje"]

def calcular_rangos_sin_carga(df_g, hoy_ts, cap_sem_dict, f_u, fmt_s, u_lbl):
    rows_free = []
    for p in PROCESOS:
        sub = df_g[(df_g["Proceso"] == p) & (df_g["Kilos"] > UMBRAL_TERMINADO_KG) & (df_g["Fin"] >= hoy_ts)] if not df_g.empty else pd.DataFrame()
        cap_s_val = fmt_u(cap_sem_dict.get(p, 0.0), f_u, fmt_s)
        if sub.empty:
            ini_libre = sumar_dias_cl(hoy_ts, 0)
            rows_free.append({
                "Proceso": p, "Estado": "🟢 Sin Carga", "Fin Últ. Carga": "-",
                "Ventanas Intermedias Libres": "Disponible de inmediato",
                "Disponible Continuo Desde": f"Desde {ini_libre.strftime('%d/%m/%Y')} en adelante",
                f"Cap. Semanal ({u_lbl})": cap_s_val
            })
            continue

        intervals = sorted([(max(hoy_ts, pd.Timestamp(r["Inicio"]).normalize()), pd.Timestamp(r["Fin"]).normalize()) for _, r in sub.iterrows()])
        merged = [intervals[0]]
        for st_dt, en_dt in intervals[1:]:
            prev_st, prev_en = merged[-1]
            if st_dt <= sumar_dias_cl(prev_en, 1):
                merged[-1] = (prev_st, max(prev_en, en_dt))
            else:
                merged.append((st_dt, en_dt))

        gaps = []
        first_st = merged[0][0]
        g_ini0 = sumar_dias_cl(hoy_ts, 0)
        g_fin0 = sumar_dias_cl(first_st, -1)
        if g_ini0 <= g_fin0:
            dg0 = contar_dias_cl(sumar_dias_cl(g_ini0, -1), g_fin0)
            if dg0 > 0:
                gaps.append(f"{g_ini0.strftime('%d/%m')} al {g_fin0.strftime('%d/%m/%Y')} ({dg0}d)")

        for im in range(len(merged) - 1):
            gi = sumar_dias_cl(merged[im][1], 1)
            gf = sumar_dias_cl(merged[im + 1][0], -1)
            if gi <= gf:
                dg = contar_dias_cl(sumar_dias_cl(gi, -1), gf)
                if dg > 0:
                    gaps.append(f"{gi.strftime('%d/%m')} al {gf.strftime('%d/%m/%Y')} ({dg}d)")

        last_end = merged[-1][1]
        libre_desde = sumar_dias_cl(last_end, 1)
        rows_free.append({
            "Proceso": p, "Estado": "🟡 Con Carga", "Fin Últ. Carga": last_end.strftime("%d/%m/%Y"),
            "Ventanas Intermedias Libres": " | ".join(gaps) if gaps else "Sin huecos intermedios",
            "Disponible Continuo Desde": f"Desde {libre_desde.strftime('%d/%m/%Y')} en adelante",
            f"Cap. Semanal ({u_lbl})": cap_s_val
        })
    return pd.DataFrame(rows_free)

@st.cache_data(ttl=300, show_spinner=False)
def ejecutar_edd_cartera(lotes_tuple, cap_dia_tuple, hoy_str):
    cap_dia_kg = dict(cap_dia_tuple)
    hoy = pd.Timestamp(hoy_str).normalize()
    cursor = {p: 0.0 for p in PROCESOS}
    filas_res, gantt_all = [], []

    for idx_edd, it in enumerate(lotes_tuple, 1):
        # Todo proceso con <= 10 kg por procesar se considera terminado (0 kg)
        raw_kgs = dict(it["kgs"])
        kgs = {p: (float(raw_kgs.get(p, 0.0)) if float(raw_kgs.get(p, 0.0)) > UMBRAL_TERMINADO_KG else 0.0) for p in PROCESOS}
        activos_reales = [p for p in PROCESOS if kgs[p] > 0.0]
        n_proc_real = len(activos_reales)
        ruta_real = " → ".join(activos_reales) if activos_reales else "Terminado (≤10 kg)"

        f_prog = pd.Timestamp(it["f_prog"])
        f_emb = pd.Timestamp(it["f_emb"]) if it["f_emb"] else pd.NaT
        tip = f"Prioridad EDD #{idx_edd} | Procesos ({n_proc_real}): {ruta_real}"

        if kgs["Embalaje"] == 0.0 and pd.notna(f_emb):
            f_fin, desv, dias_tot = f_emb, max(0, (f_emb - f_prog).days), 0
            if desv > 0:
                diag = f"🔴 Retraso (+{desv}d)"
            else:
                adel = contar_dias_cl(f_fin, f_prog)
                diag = f"🔵 Adelanto (-{adel}d)" if adel > 0 else "🟢 Embalado (OK)"
            for p in PROCESOS:
                gantt_all.append({"Identificador de fila": it["id"], "Subproyecto": it["subp"], "Proyecto_Display": it["proy"], "Fecha_Prog_Str": f_prog.strftime("%d/%m/%Y"), "Fecha_DT": f_prog, "Proceso": p, "Inicio": f_fin, "Fin": f_fin, "Fecha_Limite": f_prog, "Duracion": 0, "Kilos": kgs[p], "Fuera_Plazo": f_fin > f_prog, "Dias_Desv_Proc": desv, "Diagnostico": diag, "Ruta_Tooltip": tip})
        else:
            s_a, e_a, dias_tot, _ = programar_cola(kgs, cap_dia_kg, hoy, cursor, min_off=0.0)
            _, e_rel, t_emb_e = ciclo_relativo_lote(kgs, cap_dia_kg)
            if dias_tot == 0:
                f_fin, desv, diag = hoy, 0, "🟢 Completado"
            else:
                f_fin = sumar_dias_cl(hoy, dias_tot)
                if f_fin < f_prog:
                    adelanto_d = contar_dias_cl(f_fin, f_prog)
                    desv = 0
                    diag = f"🔵 Adelanto (-{adelanto_d}d)" if adelanto_d > 0 else "🟢 En Fecha (0d)"
                elif f_fin == f_prog:
                    desv = 0
                    diag = "🟢 En Fecha (0d)"
                else:
                    desv = (f_fin - f_prog).days
                    diag = f"🔴 Retraso (+{desv}d)"

            for p in PROCESOS:
                dur_p = max(1, int(np.ceil(e_a[p] - s_a[p]))) if kgs[p] > 0.0 else 0
                st_dt = sumar_dias_cl(hoy, int(np.floor(s_a[p])))
                en_dt = sumar_dias_cl(hoy, max(1, int(np.ceil(e_a[p])))) if kgs[p] > 0.0 else st_dt
                f_lim = sumar_dias_cl(f_prog, -max(0, int(np.ceil(t_emb_e - e_rel[p]))))
                fp_b = bool(kgs[p] > 0.0 and en_dt > f_lim)
                gantt_all.append({"Identificador de fila": it["id"], "Subproyecto": it["subp"], "Proyecto_Display": it["proy"], "Fecha_Prog_Str": f_prog.strftime("%d/%m/%Y"), "Fecha_DT": f_prog, "Proceso": p, "Inicio": st_dt, "Fin": en_dt, "Fecha_Limite": f_lim, "Duracion": dur_p, "Kilos": kgs[p], "Fuera_Plazo": fp_b, "Dias_Desv_Proc": max(1, (en_dt - f_lim).days) if fp_b else 0, "Diagnostico": diag, "Ruta_Tooltip": tip})

        filas_res.append({
            "Identificador de fila": it["id"], "Subproyecto": it["subp"], "Proyecto_Display": it["proy"],
            "Kg Orden Proceder": it["kg_ord"], "Kg por cargar (Ing)": kgs["Ingeniería"], "Kg por procesar CNCL": kgs["CNCL"],
            "Kg por procesar DoblezL": kgs["DoblezL"], "Kg por procesar Soldadura": kgs["Soldadura"],
            "Kg por procesar Fabricado": kgs["Fabricado"], "Kg por galvanizar": kgs["Galvanizado"], "Kg por Embalar": kgs["Embalaje"],
            "Kilos Programados": it["kg_prog"], "Cant_Procesos": n_proc_real, "Ruta_Detalle": tip,
            "Fecha Programada": f_prog.strftime("%d/%m/%Y"), "Fecha Estimada Finalizar": f_fin.strftime("%d/%m/%Y"),
            "Fecha_DT": f_prog, "Fecha_Fin_DT": f_fin, "Total_Dias": dias_tot, "Desviacion_Dias": desv, "Diagnostico": diag
        })

    return pd.DataFrame(filas_res), pd.DataFrame(gantt_all), cursor

# -------------------------------------------------------------
# 5. CONTROLES BARRA LATERAL (PRODUCTIVIDAD & OEE INDIVIDUAL)
# -------------------------------------------------------------
st.sidebar.header("⚙️ Configuración Planta")
unidad_medida = st.sidebar.radio("📏 Unidad:", ["Kilos (kg)", "Toneladas (t)"], index=0, horizontal=True)
es_ton = (unidad_medida == "Toneladas (t)")
f_u, fmt_s, u_lbl, u_corto = (1000.0, ",.2f", "Ton", "t") if es_ton else (1.0, ",.0f", "Kg", "kg")
st.sidebar.caption("🕒 **Jornada Chile:** Lun-Mar 9h, Mié-Jue 8h, Vie 6h (-1h colación) | Sin feriados nac.")
st.sidebar.caption(f"🎯 **Cierre Proceso:** ≤ {UMBRAL_TERMINADO_KG:.0f} kg se considera terminado (0 kg).")

st.title("📋 Control Integral de Despachos & Simulador de Carga")
if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()
    st.session_state.pop("dotacion_version_key", None)
    st.rerun()

try:
    with st.spinner("Sincronizando información de planta..."):
        sheet_id_est = st.secrets.get("SHEET_ID_ESTIMACION", SHEET_ID_ESTIMACION_DEF)
        df_back_raw, df_prod_raw, df_tip_raw = cargar_todas_las_hojas(
            st.secrets["SMARTSHEET_ACCESS_TOKEN"],
            st.secrets["SHEET_ID_BACKLOG"],
            st.secrets["SHEET_ID_PRODUCTIVIDAD"],
            sheet_id_est
        )

    cols_b = df_back_raw.columns.tolist()
    c_id = next((c for c in cols_b if "identificador" in c.lower() and "fila" in c.lower()), next((c for c in cols_b if "identificador" in c.lower() or "despacho" in c.lower()), cols_b[0]))
    c_sub = next((c for c in cols_b if "sub-proyecto" in c.lower() or "subproyecto" in c.lower()), None)
    c_proy = next((c for c in cols_b if c.lower().strip() in ["proyecto","proyectos","cod proyecto","código proyecto"]), next((c for c in cols_b if "proyecto" in c.lower() and "sub" not in c.lower()), None))
    c_nom = next((c for c in cols_b if any(k in c.lower() for k in ["nombre proyecto","nombre del proyecto","nom_proyecto","obra","cliente"])), None)
    c_fprog = next((c for c in cols_b if "programad" in c.lower() and "fecha" in c.lower()), next((c for c in cols_b if "fecha" in c.lower()), None))
    c_dreal = next((c for c in cols_b if "despacho" in c.lower() and ("real" in c.lower() or "fecha" in c.lower())), None)
    c_ord = next((c for c in cols_b if "orden" in c.lower() and "proceder" in c.lower()), None)
    c_femb = next((c for c in cols_b if "fecha" in c.lower() and "embalaje" in c.lower()), None)

    c_kg_prog = buscar_col_kg(cols_b, "programad", ["Kilos Programados", "Kilos programados", "Kg Programados"])
    c_kg_ing = buscar_col_kg(cols_b, "cargar", ["Kilos por Cargar_", "Kilos por Cargar"])
    c_kg_cncl = buscar_col_kg(cols_b, "cncl", ["Kilos por CNCL", "Kilos por CNCL_"])
    c_kg_dob = buscar_col_kg(cols_b, "doblez", ["Kilos por DoblezL", "Kilos por Doblez"])
    c_kg_sol = buscar_col_kg(cols_b, "soldadura", ["Kilos por Soldadura"])
    c_kg_fab = buscar_col_kg(cols_b, "fabricar", ["Kilos por Fabricar", "Kilos por Fabricado"])
    c_kg_gal = buscar_col_kg(cols_b, "galvanizar", ["Kilos por Galvanizar", "Kilos por Galvanizado"])
    c_kg_emb = buscar_col_kg(cols_b, "embalar", ["Kilos por Embalar", "Kilos por Embalaje"])

    # Productividad Smartsheet + OEE por proceso (CNCL 45%, resto 90%)
    cp_proc = next((c for c in df_prod_raw.columns if "proceso" in c.lower() and "simulador" in c.lower()), df_prod_raw.columns[0])
    cp_kghh = next((c for c in df_prod_raw.columns if "kg" in c.lower() and "hh" in c.lower()), df_prod_raw.columns[1])
    ratios_sis = {}
    for p in PROCESOS:
        pc = clean_txt(p)
        for _, r in df_prod_raw.iterrows():
            v, sc = pd.to_numeric(r[cp_kghh], errors='coerce'), clean_txt(str(r[cp_proc]))
            if pd.notna(v) and v > 0 and (sc == pc or (pc in sc and not (p == "Soldadura" and "armado" in sc))):
                ratios_sis[p] = float(v)
                if sc == pc: break

    with st.sidebar.expander("⚖️ Productividad (Kg/HH) & OEE (%)", expanded=False):
        st.caption("CNCL inicia en 45% OEE y los demás procesos en 90% (editables):")
        if "df_prod_oee" not in st.session_state:
            st.session_state.df_prod_oee = pd.DataFrame([
                {"Proceso": p, "Kg/HH Sist.": ratios_sis.get(p, RATIO_DEF[p]), "Kg/HH Sim.": ratios_sis.get(p, RATIO_DEF[p]), "OEE (%)": OEE_DEF[p]}
                for p in PROCESOS
            ])
        else:
            for p in PROCESOS:
                st.session_state.df_prod_oee.loc[st.session_state.df_prod_oee["Proceso"] == p, "Kg/HH Sist."] = ratios_sis.get(p, RATIO_DEF[p])
        ed_prod = st.data_editor(
            st.session_state.df_prod_oee,
            column_config={
                "Proceso": st.column_config.TextColumn("Proceso", disabled=True),
                "Kg/HH Sist.": st.column_config.NumberColumn("Sist.", disabled=True, format="%.1f"),
                "Kg/HH Sim.": st.column_config.NumberColumn("Sim.", min_value=1.0, max_value=10000.0, step=5.0, format="%.1f"),
                "OEE (%)": st.column_config.NumberColumn("OEE %", min_value=5.0, max_value=100.0, step=5.0, format="%.0f")
            },
            hide_index=True, key="ed_sb_ratios_oee"
        )
        st.session_state.df_prod_oee = ed_prod
        ratios_act = dict(zip(ed_prod["Proceso"], ed_prod["Kg/HH Sim."]))
        oee_act = {p: float(v) / 100.0 for p, v in zip(ed_prod["Proceso"], ed_prod["OEE (%)"])}
        oee_cncl_pct = float(ed_prod.loc[ed_prod["Proceso"] == "CNCL", "OEE (%)"].values[0])
        oee_otros_pct = float(ed_prod.loc[ed_prod["Proceso"] != "CNCL", "OEE (%)"].mean())

    val_f = df_back_raw[c_fprog].notna() & (~df_back_raw[c_fprog].astype(str).str.strip().str.lower().isin(["", "none", "nan"]))
    df_act = df_back_raw[val_f & (df_back_raw[c_dreal].isna() | df_back_raw[c_dreal].astype(str).str.strip().str.lower().isin(["", "none", "nan"]))].copy() if c_dreal else df_back_raw[val_f].copy()
    df_act["Fecha_DT"] = pd.to_datetime(df_act[c_fprog], errors='coerce', dayfirst=True)
    df_act = df_act.dropna(subset=["Fecha_DT"]).sort_values("Fecha_DT")
    df_act["Mes_Entrega"] = df_act["Fecha_DT"].dt.strftime("%Y-%m")
    df_act["Kg_Prog_Num"] = df_act[c_kg_prog].apply(num_val) if c_kg_prog else 0.0
    df_act["Fecha_Emb_DT"] = pd.to_datetime(df_act[c_femb], errors='coerce', dayfirst=True) if c_femb else pd.NaT
    df_act["Proy_Cod"] = df_act[c_proy].fillna("").astype(str).str.strip() if c_proy else df_act[c_sub].apply(lambda s: "-".join(str(s).split("-")[:2]))
    df_act["Proy_Disp"] = (df_act["Proy_Cod"] + " - " + df_act[c_nom].fillna("").astype(str).str.strip()) if c_nom else df_act["Proy_Cod"]

    st.sidebar.markdown("---")
    st.sidebar.subheader("📅 Horizonte & Semanas ISO")
    meses_disp = sorted(df_act["Mes_Entrega"].unique().tolist())
    hoy_dt = datetime.now()
    mes_act_str, sem_actual_iso = hoy_dt.strftime("%Y-%m"), f"{hoy_dt.isocalendar()[0]}-{hoy_dt.isocalendar()[1]:02d}"
    idx_def = meses_disp.index(mes_act_str) if mes_act_str in meses_disp else next((i for i, m in enumerate(meses_disp) if m >= mes_act_str), 0)
    mes_sel = st.sidebar.selectbox("Despachos desde:", options=meses_disp, index=idx_def)
    df_hor = df_act[df_act["Mes_Entrega"] >= mes_sel].copy()
    try:
        dt_i = pd.to_datetime(f"{mes_sel}-01")
        sem_ini_f = f"{dt_i.isocalendar()[0]}-{dt_i.isocalendar()[1]:02d}"
    except Exception:
        sem_ini_f = sem_actual_iso
    st.sidebar.caption(f"📍 **Sem. Actual:** `{sem_actual_iso}` | ▶️ **Inicio Filtro:** `{sem_ini_f}`")

    # -------------------------------------------------------------
    # 6. CAPACIDADES CON OEE POR PROCESO Y SECUENCIACIÓN ASAP + EDD
    # -------------------------------------------------------------
    dot_efec = {}
    for p in PROCESOS:
        if "matriz_dotacion_semanal" in st.session_state and st.session_state.get("dotacion_version_key") == VERSION_KEY:
            fp = st.session_state.matriz_dotacion_semanal[st.session_state.matriz_dotacion_semanal["Proceso"] == p]
            cs = [c for c in st.session_state.matriz_dotacion_semanal.columns if c != "Proceso"]
            prom = pd.to_numeric(fp[cs].iloc[0], errors='coerce').mean() if (not fp.empty and cs) else np.nan
            dot_efec[p] = float(prom) if pd.notna(prom) and prom > 0 else float(DOTACION_DEF[p])
        else:
            dot_efec[p] = float(DOTACION_DEF[p])

    cap_dia_kg = {p: max(100.0, dot_efec[p] * 8.0 * oee_act[p] * ratios_act.get(p, RATIO_DEF[p])) for p in PROCESOS}

    lotes_prep = []
    for _, lt in df_hor.iterrows():
        # Aplica umbral <= 10 kg como proceso terminado (0 kg)
        kgs = {p: kg_pendiente_val(lt.get(col)) if col else 0.0 for p, col in [("Ingeniería",c_kg_ing),("CNCL",c_kg_cncl),("DoblezL",c_kg_dob),("Soldadura",c_kg_sol),("Fabricado",c_kg_fab),("Galvanizado",c_kg_gal),("Embalaje",c_kg_emb)]}
        activos = [p for p in PROCESOS if kgs[p] > 0.0]
        lotes_prep.append({
            "id": str(lt[c_id]), "subp": str(lt[c_sub]),
            "f_prog": lt["Fecha_DT"].strftime("%Y-%m-%d"),
            "f_emb": lt["Fecha_Emb_DT"].strftime("%Y-%m-%d") if pd.notna(lt["Fecha_Emb_DT"]) else "",
            "proy": str(lt["Proy_Disp"]), "kg_prog": float(lt["Kg_Prog_Num"]),
            "kg_ord": num_val(lt.get(c_ord)) if c_ord else float(lt["Kg_Prog_Num"]),
            "kgs": tuple(kgs.items()), "n_proc": len(activos),
            "ruta": " → ".join(activos) if activos else "Terminado (≤10 kg)"
        })

    lotes_prep.sort(key=lambda x: (x["f_prog"], -x["n_proc"], x["subp"]))
    hoy = pd.Timestamp.now().normalize()
    df_res, df_gantt, cursor = ejecutar_edd_cartera(tuple(lotes_prep), tuple(cap_dia_kg.items()), hoy.strftime("%Y-%m-%d"))

    # -------------------------------------------------------------
    # 7. FILTROS GLOBALES EN BARRA LATERAL
    # -------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("🎯 Filtros Globales de Cartera")
    cs1, cs2 = st.sidebar.columns(2)
    f_anio = cs1.multiselect("Año:", sorted(df_res["Fecha_DT"].dt.year.dropna().unique().astype(int).tolist()), placeholder="Todos", key="sb_anio")
    p1 = df_res if not f_anio else df_res[df_res["Fecha_DT"].dt.year.isin(f_anio)]
    f_mes = cs2.multiselect("Mes:", sorted(p1["Fecha_DT"].dt.strftime("%Y-%m").dropna().unique().tolist()), placeholder="Todos", key="sb_mes")
    p2 = p1 if not f_mes else p1[p1["Fecha_DT"].dt.strftime("%Y-%m").isin(f_mes)]

    cs3, cs4 = st.sidebar.columns(2)
    f_fec = cs3.multiselect("Fecha Prog:", [pd.Timestamp(d).strftime("%d/%m/%Y") for d in sorted(p2["Fecha_DT"].dropna().unique())], placeholder="Todas", key="sb_fecha")
    p3 = p2 if not f_fec else p2[p2["Fecha_DT"].dt.strftime("%d/%m/%Y").isin(f_fec)]
    f_desp = cs4.multiselect("ID Despacho:", sorted([d for d in p3["Identificador de fila"].unique() if d.strip()]), placeholder="Todos", key="sb_desp")
    p4 = p3 if not f_desp else p3[p3["Identificador de fila"].isin(f_desp)]

    f_proy = st.sidebar.multiselect("🏗️ Nombre / Proyecto:", sorted([p for p in p4["Proyecto_Display"].unique() if p.strip()]), placeholder="Todos los proyectos", key="sb_proy")
    p5 = p4 if not f_proy else p4[p4["Proyecto_Display"].isin(f_proy)]
    f_subp = st.sidebar.multiselect("📂 Subproyecto:", sorted([s for s in p5["Subproyecto"].unique() if s.strip()]), placeholder="Todos los subproyectos", key="sb_subp")
    df_mostrar = (p5 if not f_subp else p5[p5["Subproyecto"].isin(f_subp)]).copy()

    # Semanas y capacidades compartidas
    df_sem_all = dist_semanal_cl(df_gantt)
    if not df_sem_all.empty:
        df_sem_all = df_sem_all[df_sem_all["Semana"] >= sem_actual_iso].copy()
        piv_planta_all = df_sem_all.groupby(["Proceso", "Semana"])["Kilos"].sum().to_dict()
        max_sem_all = max(sem_actual_iso, df_sem_all["Semana"].max())
    else:
        piv_planta_all, max_sem_all = {}, sem_actual_iso

    semanas_cols = sems_consecutivas(sem_actual_iso, max_sem_all, min_c=10)
    hrs_sem_info = {s: info_sem_iso(s) for s in semanas_cols}

    if st.session_state.get("dotacion_version_key") != VERSION_KEY:
        st.session_state.dotacion_version_key = VERSION_KEY
        st.session_state.matriz_dotacion_semanal = pd.DataFrame({"Proceso": PROCESOS, **{s: [DOTACION_DEF[p] for p in PROCESOS] for s in semanas_cols}})
    else:
        for s in semanas_cols:
            if s not in st.session_state.matriz_dotacion_semanal.columns:
                st.session_state.matriz_dotacion_semanal[s] = [DOTACION_DEF[p] for p in PROCESOS]

    cap_din_sem = {
        p: {
            s: (float(st.session_state.matriz_dotacion_semanal.loc[st.session_state.matriz_dotacion_semanal["Proceso"] == p, s].values[0])
                if s in st.session_state.matriz_dotacion_semanal.columns else DOTACION_DEF[p])
               * hrs_sem_info[s][0] * oee_act[p] * ratios_act.get(p, RATIO_DEF[p])
            for s in semanas_cols
        }
        for p in PROCESOS
    }

    # -------------------------------------------------------------
    # 8. PESTAÑAS PRINCIPALES (3 PESTAÑAS)
    # -------------------------------------------------------------
    tab_ctrl, tab_flujo, tab_sim = st.tabs([
        "📋 Control de Despachos & Carga Semanal",
        "🔀 Flujo de Planta & Cola por Proceso",
        "🧮 Simulador de Cotizaciones"
    ])

    # =============================================================
    # PESTAÑA 1: CONTROL DE DESPACHOS & CARGA SEMANAL
    # =============================================================
    with tab_ctrl:
        c1, c2, c3, c4, c5 = st.columns([1.2, 1.2, 1.2, 1.2, 1.2])
        if c5.checkbox("🔴 Solo con retraso", key="f_atr_global"):
            df_mostrar = df_mostrar[df_mostrar["Desviacion_Dias"] > 0]
        df_mostrar = df_mostrar.sort_values(by=["Fecha_DT", "Cant_Procesos", "Subproyecto"], ascending=[True, False, True])

        tot_l, atr_l = len(df_mostrar), len(df_mostrar[df_mostrar["Desviacion_Dias"] > 0])
        adel_l = len(df_mostrar[df_mostrar["Diagnostico"].str.contains("Adelanto", na=False)])
        max_r = df_mostrar["Desviacion_Dias"].max() if not df_mostrar.empty else 0
        c1.metric("Lotes en Vista", tot_l)
        c2.metric("En Fecha / Adelantados", tot_l - atr_l, delta=f"{adel_l} adelantados" if adel_l > 0 else None, delta_color="off")
        c3.metric("Lotes Desviados", atr_l, delta=f"-{atr_l}" if atr_l > 0 else "0", delta_color="inverse")
        c4.metric("Máximo Retraso", f"+{max_r} días" if max_r > 0 else "0 días", delta_color="inverse")

        lotes_vis = df_mostrar["Identificador de fila"].unique()
        df_gf = df_gantt[df_gantt["Identificador de fila"].isin(lotes_vis)].copy()
        df_st = dist_semanal_cl(df_gf)
        if not df_st.empty:
            df_st = df_st[df_st["Semana"] >= sem_actual_iso].copy()
            piv_d = df_st.groupby(["Proceso", "Semana"])["Kilos"].sum().to_dict()
        else:
            piv_d = {}

        st.markdown("---")
        st.markdown(f"### 📋 Programa de Despachos & Estado de Carga por Estación ({unidad_medida})")
        st.caption(f"Calculado desde hoy en cola continua ASAP (priorizada por Fecha Programada más cercana). Todo proceso con ≤ {UMBRAL_TERMINADO_KG:.0f} kg se considera terminado (0 kg).")
        tot_d1 = {k: df_mostrar[col].sum() for k, col in [("orden","Kg Orden Proceder"),("ing","Kg por cargar (Ing)"),("cncl","Kg por procesar CNCL"),("doblez","Kg por procesar DoblezL"),("sold","Kg por procesar Soldadura"),("fab","Kg por procesar Fabricado"),("galv","Kg por galvanizar"),("emb","Kg por Embalar")]}
        components.html(render_tabla_despachos(df_mostrar, tot_d1, f_u, fmt_s, u_lbl), height=520, scrolling=False)

        st.markdown("---")
        st.markdown(f"### 📊 Cuadro de Carga Semanal por Proceso ({unidad_medida})")
        info_p = {}
        for p in PROCESOS:
            sp = df_gf[(df_gf["Proceso"] == p) & (df_gf["Kilos"] > UMBRAL_TERMINADO_KG)] if not df_gf.empty else pd.DataFrame()
            kgt = float(sp["Kilos"].sum()) if not sp.empty else 0.0
            info_p[p] = {"ini": pd.to_datetime(sp["Inicio"]).min().strftime("%d/%m/%Y"), "fin": pd.to_datetime(sp["Fin"]).max().strftime("%d/%m/%Y"), "dias": int(sp["Duracion"].values[0]) if len(lotes_vis) == 1 else int(np.ceil(kgt / cap_dia_kg[p])), "total_kg": kgt} if (not sp.empty and kgt > 0) else {"ini": "-", "fin": "-", "dias": 0, "total_kg": 0.0}
        components.html(render_cuadro_semanal(piv_d, info_p, semanas_cols, cap_din_sem, f_u, fmt_s, u_lbl), height=360, scrolling=False)

        st.markdown("---")
        st.markdown(f"### ⚡ Carga Semanal por Despacho & Capacidad de Planta ({unidad_medida})")
        ct1, _ = st.columns([2.5, 4.5])
        proc_sel = ct1.selectbox("🎯 Selecciona el Proceso a Analizar:", ["Programa Despacho"] + PROCESOS, index=0, key="sel_proc_uni")

        with st.expander("👷 Dotación Semanal de Operarios por Proceso (Editable) & Horas Hábiles", expanded=True):
            df_dv = st.session_state.matriz_dotacion_semanal[["Proceso"] + semanas_cols].copy() if proc_sel == "Programa Despacho" else st.session_state.matriz_dotacion_semanal[st.session_state.matriz_dotacion_semanal["Proceso"] == proc_sel][["Proceso"] + semanas_cols].copy()
            ed_dot = st.data_editor(df_dv, column_config={"Proceso": st.column_config.TextColumn("Proceso", disabled=True), **{s: st.column_config.NumberColumn(s, min_value=0, max_value=100, step=1) for s in semanas_cols}}, hide_index=True, use_container_width=True, key=f"ed_dot_{proc_sel}")
            if not ed_dot.reset_index(drop=True).equals(df_dv.reset_index(drop=True)):
                for _, re_row in ed_dot.iterrows():
                    for s in semanas_cols: st.session_state.matriz_dotacion_semanal.loc[st.session_state.matriz_dotacion_semanal["Proceso"] == re_row["Proceso"], s] = re_row[s]
                st.rerun()
            components.html(render_horas_dotacion(semanas_cols, {s: float(pd.to_numeric(ed_dot[s], errors='coerce').sum()) for s in semanas_cols}, hrs_sem_info), height=155, scrolling=False)

        cap_v = {s: cap_din_sem["Embalaje"].get(s, 1000.0) for s in semanas_cols} if proc_sel == "Programa Despacho" else {s: cap_din_sem[proc_sel].get(s, 1000.0) for s in semanas_cols}
        mapa_kg = {"Ingeniería": "Kg por cargar (Ing)", "CNCL": "Kg por procesar CNCL", "DoblezL": "Kg por procesar DoblezL", "Soldadura": "Kg por procesar Soldadura", "Fabricado": "Kg por procesar Fabricado", "Galvanizado": "Kg por galvanizar", "Embalaje": "Kg por Embalar"}
        lotes_t2 = []
        for _, lt in df_mostrar.iterrows():
            id_f, f_dt = lt["Identificador de fila"], lt["Fecha_DT"]
            if proc_sel == "Programa Despacho":
                kg_p = num_val(lt.get("Kilos Programados", 0.0))
                dias_p = int(lt.get("Total_Dias", 0)) or (int(np.ceil(kg_p / cap_dia_kg["Embalaje"])) if kg_p > 0 else 0)
                sem_l = f"{f_dt.isocalendar()[0]}-{f_dt.isocalendar()[1]:02d}"
                s_dict = {s: (kg_p if s == sem_l else 0.0) for s in semanas_cols}
                desv = int(lt.get("Desviacion_Dias", 0))
                fp, f_est_s, tip_a = (desv > 0), lt["Fecha Estimada Finalizar"], f"Desviado +{desv}d"
                f_obj_s = lt["Fecha Programada"]
            else:
                kg_p = kg_pendiente_val(lt.get(mapa_kg.get(proc_sel, ""), 0.0))
                dias_p = int(np.ceil(kg_p / cap_dia_kg[proc_sel])) if kg_p > 0 else 0
                fl = df_st[(df_st["Identificador de fila"] == id_f) & (df_st["Proceso"] == proc_sel)] if not df_st.empty else pd.DataFrame()
                s_dict = fl.groupby("Semana")["Kilos"].sum().to_dict() if not fl.empty else {}
                rp = df_gf[(df_gf["Identificador de fila"] == id_f) & (df_gf["Proceso"] == proc_sel)] if not df_gf.empty else pd.DataFrame()
                if not rp.empty:
                    f_est_s = pd.Timestamp(rp["Fin"].values[0]).strftime("%d/%m/%Y")
                    f_obj_s = pd.Timestamp(rp["Fecha_Limite"].values[0]).strftime("%d/%m/%Y")
                    fp = bool(rp["Fuera_Plazo"].values[0])
                    tip_a = f"Desviado +{int(rp['Dias_Desv_Proc'].values[0])}d (Fecha Objetivo: {f_obj_s})"
                else:
                    f_est_s, f_obj_s, fp, tip_a = "-", "-", False, ""
            if kg_p > UMBRAL_TERMINADO_KG:
                lotes_t2.append({
                    "Identificador de fila": id_f, "Subproyecto": lt["Subproyecto"], "Fecha_DT": f_dt, "Cant_Procesos": int(lt["Cant_Procesos"]),
                    "Ruta_Tooltip": lt["Ruta_Detalle"], "Fecha_Despacho": lt["Fecha Programada"], "Fecha_Obj_Proc": f_obj_s,
                    "Fecha_Est_Display": f"{f_est_s} <span style='color:#dc2626;cursor:help' title='{tip_a}'>⚠️</span>" if fp else f_est_s,
                    "Cant_Dias": dias_p, "Kilos_Procesar": kg_p, **{s: s_dict.get(s, 0.0) for s in semanas_cols}
                })

        df_t2 = pd.DataFrame(lotes_t2)
        if df_t2.empty:
            st.info(f"🎉 No hay lotes pendientes en **{proc_sel}** (todos los procesos están terminados con ≤ {fmt_u(UMBRAL_TERMINADO_KG, f_u, fmt_s)} {u_corto}).")
        else:
            df_t2 = df_t2.sort_values(by=["Fecha_DT", "Cant_Procesos", "Subproyecto"], ascending=[True, False, True])
            components.html(render_carga_proceso(df_t2, semanas_cols, cap_v, f_u, fmt_s, u_lbl), height=540, scrolling=False)
        st.markdown("---")
        st.download_button("📥 Descargar Plan de Despachos Completo (CSV)", data=df_mostrar.to_csv(index=False).encode('utf-8'), file_name=f"plan_despachos_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")

    # =============================================================
    # PESTAÑA 2: FLUJO DE PLANTA & COLA POR PROCESO
    # =============================================================
    with tab_flujo:
        st.markdown("### 🔀 Flujo Operativo de Planta & Toneladas en Cola por Estación")
        st.caption(f"Haz clic en cualquiera de las cajas de proceso para auditar los proyectos en cola (> {UMBRAL_TERMINADO_KG:.0f} kg) ordenados por fecha de entrega ascendente.")

        if "flujo_proc_sel" not in st.session_state:
            st.session_state.flujo_proc_sel = "CNCL"

        df_gf_flujo = df_gantt[df_gantt["Identificador de fila"].isin(lotes_vis)].copy()
        cola_info = {}
        for p in PROCESOS:
            sub_p = df_gf_flujo[(df_gf_flujo["Proceso"] == p) & (df_gf_flujo["Kilos"] > UMBRAL_TERMINADO_KG)] if not df_gf_flujo.empty else pd.DataFrame()
            ton_p = float(sub_p["Kilos"].sum()) / 1000.0 if not sub_p.empty else 0.0
            cnt_p = len(sub_p)
            cola_info[p] = (ton_p, cnt_p)

        svg_flujo = render_diagrama_flujo_svg(cola_info, st.session_state.flujo_proc_sel)
        components.html(svg_flujo, height=305, scrolling=False)

        st.markdown("**Selecciona una caja de proceso:**")
        cols_btn = st.columns(7)
        nombres_caja = [
            ("Ingeniería", "1. Ingeniería"),
            ("CNCL", "2. CNCL"),
            ("DoblezL", "3. Doblez L"),
            ("Soldadura", "4. Soldadura"),
            ("Fabricado", "5. Fabricado"),
            ("Galvanizado", "6. Galvanizado"),
            ("Embalaje", "7. Embalado"),
        ]
        for idx_b, (p_key, p_lbl) in enumerate(nombres_caja):
            ton_v, cnt_v = cola_info[p_key]
            is_act = (st.session_state.flujo_proc_sel == p_key)
            if cols_btn[idx_b].button(
                f"{p_lbl}\n{ton_v:,.2f} Ton ({cnt_v})",
                key=f"btn_flujo_{p_key}",
                type="primary" if is_act else "secondary",
                use_container_width=True
            ):
                st.session_state.flujo_proc_sel = p_key
                st.rerun()

        proc_f_sel = st.session_state.flujo_proc_sel
        lbl_f_sel = dict(nombres_caja).get(proc_f_sel, proc_f_sel)
        ton_tot_sel, cnt_tot_sel = cola_info[proc_f_sel]

        st.markdown("---")
        st.markdown(f"#### 📦 Proyectos en Cola — **{lbl_f_sel}** (`{ton_tot_sel:,.2f} Ton` | `{cnt_tot_sel}` lotes ordenados por Fecha de Entrega)")

        df_cola_proc = df_gf_flujo[(df_gf_flujo["Proceso"] == proc_f_sel) & (df_gf_flujo["Kilos"] > UMBRAL_TERMINADO_KG)].copy() if not df_gf_flujo.empty else pd.DataFrame()
        if df_cola_proc.empty:
            st.success(f"✅ No hay proyectos pendientes en cola para **{lbl_f_sel}** (todos los lotes tienen ≤ {UMBRAL_TERMINADO_KG:.0f} kg).")
        else:
            df_cola_proc = df_cola_proc.sort_values(by=["Fecha_DT", "Subproyecto", "Identificador de fila"], ascending=[True, True, True]).reset_index(drop=True)
            df_tabla_cola = pd.DataFrame({
                "Orden": [f"#{i+1}" for i in range(len(df_cola_proc))],
                "ID Fila": df_cola_proc["Identificador de fila"],
                "Proyecto": df_cola_proc["Proyecto_Display"],
                "Subproyecto": df_cola_proc["Subproyecto"],
                "Toneladas en Cola (t)": df_cola_proc["Kilos"].map(lambda x: f"{x/1000.0:,.2f}"),
                "Kilos en Cola (kg)": df_cola_proc["Kilos"].map(lambda x: f"{x:,.0f}"),
                "Días Est. Proceso": df_cola_proc["Duracion"].astype(int),
                "Inicio Est. Proceso": pd.to_datetime(df_cola_proc["Inicio"]).dt.strftime("%d/%m/%Y"),
                "Término Est. Proceso": pd.to_datetime(df_cola_proc["Fin"]).dt.strftime("%d/%m/%Y"),
                "Fecha Obj. Proceso": pd.to_datetime(df_cola_proc["Fecha_Limite"]).dt.strftime("%d/%m/%Y"),
                "Fecha Entrega Prog.": df_cola_proc["Fecha_Prog_Str"],
                "Estado / Desviación": df_cola_proc["Diagnostico"],
            })
            st.dataframe(df_tabla_cola, hide_index=True, use_container_width=True, height=440)
            st.download_button(
                f"📥 Descargar Cola de {lbl_f_sel} (CSV)",
                data=df_tabla_cola.to_csv(index=False).encode("utf-8"),
                file_name=f"cola_{clean_txt(lbl_f_sel)}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                key="dl_csv_flujo"
            )

    # =============================================================
    # PESTAÑA 3: SIMULADOR DE COTIZACIONES (CÁLCULO HACIA ATRÁS)
    # =============================================================
    with tab_sim:
        mapa_tip = {}
        if not df_tip_raw.empty:
            ct = df_tip_raw.columns.tolist()
            c_tip = next((c for c in ct if "tipolog" in clean_txt(c)), ct[0])
            c_pc = next((c for c in ct if "%" in str(c) and ("cncl" in clean_txt(c) or "cnc" in clean_txt(c))), None)
            c_pd = next((c for c in ct if "%" in str(c) and "doblez" in clean_txt(c)), None)
            c_ps = next((c for c in ct if "%" in str(c) and "soldadura" in clean_txt(c)), None)
            for _, rt in df_tip_raw.iterrows():
                nt = str(rt.get(c_tip, "")).strip()
                if not nt or nt.lower() in ["none", "nan"]:
                    continue
                vc = pct_val(rt.get(c_pc)) if c_pc else 0.0
                vd = pct_val(rt.get(c_pd)) if c_pd else 0.0
                vs = pct_val(rt.get(c_ps)) if c_ps else 0.0
                if nt not in mapa_tip:
                    mapa_tip[nt] = {"Ingeniería": 1.0, "CNCL": vc, "DoblezL": vd, "Soldadura": vs, "Fabricado": 1.0, "Galvanizado": 1.0, "Embalaje": 1.0}
                else:
                    mapa_tip[nt]["CNCL"] = max(mapa_tip[nt]["CNCL"], vc)
                    mapa_tip[nt]["DoblezL"] = max(mapa_tip[nt]["DoblezL"], vd)
                    mapa_tip[nt]["Soldadura"] = max(mapa_tip[nt]["Soldadura"], vs)
        else:
            st.warning("⚠️ No se pudieron leer filas de la hoja de Tipologías. Usando catálogo base.")
            mapa_tip = {"T ANCLAJE": {"Ingeniería": 1.0, "CNCL": 0.65, "DoblezL": 0.25, "Soldadura": 0.45, "Fabricado": 1.0, "Galvanizado": 1.0, "Embalaje": 1.0}, "T SUSPENSION": {"Ingeniería": 1.0, "CNCL": 0.70, "DoblezL": 0.30, "Soldadura": 0.35, "Fabricado": 1.0, "Galvanizado": 1.0, "Embalaje": 1.0}}

        l_tips = sorted(list(mapa_tip.keys()))
        ch1, ch2 = st.columns([3.5, 1.5])
        with ch1:
            st.markdown(f"### 🧮 Simulador de Factibilidad & Carga para Cotizaciones ({unidad_medida})")
            st.caption("Ingresa la Fecha Término Deseado por el cliente (por defecto +60 días hábiles) para calcular fechas hacia atrás y evaluar disponibilidad de planta.")
        pdf_slot = ch2.empty()

        fecha_def_60d = sumar_dias_cl(hoy, 60).date()
        cs1, cs2, cs3 = st.columns([2.2, 2.0, 1.5])
        nom_cot = cs1.text_input("🏗️ Nombre Proyecto Cotizado:", value="Proyecto Cotización Nueva", key="sim_nom")
        cli_cot = cs2.text_input("🏢 Cliente:", value="Cliente Cotización", key="sim_cli")
        f_term_c = pd.Timestamp(cs3.date_input("🎯 Fecha Término Deseado:", value=fecha_def_60d, min_value=hoy.date(), key="sim_fterm")).normalize()

        st.markdown(f"#### 1️⃣ Estructuras Cotizadas (Agrega filas y {unidad_medida})")
        dt1 = "T ANCLAJE" if "T ANCLAJE" in l_tips else l_tips[0]
        dt2 = "T SUSPENSION" if "T SUSPENSION" in l_tips else (l_tips[1] if len(l_tips) > 1 else l_tips[0])
        if "df_cot_items" not in st.session_state:
            st.session_state.df_cot_items = pd.DataFrame([{"Estructura (G_Tipología)": dt1, "Cantidad": 25000.0}, {"Estructura (G_Tipología)": dt2, "Cantidad": 40000.0}])

        df_iv = st.session_state.df_cot_items.copy()
        df_iv["Peso_Vista"] = df_iv["Cantidad"] / f_u
        ed_cot = st.data_editor(
            df_iv[["Estructura (G_Tipología)", "Peso_Vista"]],
            column_config={"Estructura (G_Tipología)": st.column_config.SelectboxColumn("Estructura (G_Tipología)", options=l_tips, required=True, width="large"), "Peso_Vista": st.column_config.NumberColumn(f"Peso Cotizado ({u_lbl})", min_value=0.0, step=10.0 if es_ton else 500.0, format="%.2f" if es_ton else "%.0f", required=True)},
            num_rows="dynamic", use_container_width=True, key="ed_cot_est"
        )
        df_sv = ed_cot.copy()
        df_sv["Cantidad"] = pd.to_numeric(df_sv["Peso_Vista"], errors="coerce").fillna(0.0) * f_u
        st.session_state.df_cot_items = df_sv[["Estructura (G_Tipología)", "Cantidad"]]

        calc_rows = []
        for idx_s, rs in df_sv.iterrows():
            ts, kg_c = str(rs.get("Estructura (G_Tipología)", "")).strip(), float(rs.get("Cantidad", 0.0))
            if not ts or ts == "None" or kg_c <= 0: continue
            pc = mapa_tip.get(ts, {"Ingeniería": 1.0, "CNCL": 0.0, "DoblezL": 0.0, "Soldadura": 0.0, "Fabricado": 1.0, "Galvanizado": 1.0, "Embalaje": 1.0})
            calc_rows.append({"Etiqueta": f"{idx_s+1}. {ts}", "Tipologia": ts, "p_cncl": pc["CNCL"]*100, "p_dob": pc["DoblezL"]*100, "p_sol": pc["Soldadura"]*100, "Kg_Cotizado": kg_c, **{p: kg_c * pc.get(p, 1.0) for p in PROCESOS}})

        if not calc_rows:
            st.info("Ingresa al menos una estructura con peso > 0 para simular la factibilidad.")
        else:
            df_ce = pd.DataFrame(calc_rows)
            df_sh = pd.DataFrame({
                "Estructura": df_ce["Etiqueta"],
                "% Ing.": "100%",
                "% CNCL": df_ce["p_cncl"].map(lambda x: f"{x:.0f}%"),
                "% DoblezL": df_ce["p_dob"].map(lambda x: f"{x:.0f}%"),
                "% Soldad.": df_ce["p_sol"].map(lambda x: f"{x:.0f}%"),
                f"Cotizado ({u_lbl})": df_ce["Kg_Cotizado"].map(lambda x: fmt_u(x, f_u, fmt_s)),
                **{f"{p} ({u_lbl})": df_ce[p].map(lambda x: fmt_u(x, f_u, fmt_s)) for p in PROCESOS}
            })
            tot_r = pd.DataFrame([{
                "Estructura": "TOTAL COTIZACIÓN", "% Ing.": "100%", "% CNCL": "-", "% DoblezL": "-", "% Soldad.": "-",
                f"Cotizado ({u_lbl})": fmt_u(df_ce["Kg_Cotizado"].sum(), f_u, fmt_s),
                **{f"{p} ({u_lbl})": fmt_u(df_ce[p].sum(), f_u, fmt_s) for p in PROCESOS}
            }])
            st.dataframe(pd.concat([df_sh, tot_r], ignore_index=True), hide_index=True, use_container_width=True)

            target_off = float(contar_dias_cl(hoy, f_term_c))
            rel_cur = {p: 0.0 for p in PROCESOS}
            rel_items = []
            for _, re_c in df_ce.iterrows():
                kgs_i = {p: float(re_c[p]) for p in PROCESOS}
                sr, er, _ = ciclo_relativo_lote(kgs_i, cap_dia_kg)
                shift_it = max([rel_cur[p] - sr[p] for p in PROCESOS if kgs_i[p] > 0] or [0.0])
                s_i = {p: sr[p] + shift_it for p in PROCESOS}
                e_i = {p: er[p] + shift_it for p in PROCESOS}
                for p in PROCESOS:
                    if kgs_i[p] > 0:
                        rel_cur[p] = e_i[p]
                rel_items.append((re_c, kgs_i, s_i, e_i))

            total_span_dias = max(e_i["Embalaje"] for _, _, _, e_i in rel_items)
            backward_shift = target_off - total_span_dias

            sim_gantt = []
            for re_c, kgs_i, s_i, e_i in rel_items:
                for p in PROCESOS:
                    sb = s_i[p] + backward_shift
                    eb = e_i[p] + backward_shift
                    dur_p = max(1, int(np.ceil(eb - sb))) if kgs_i[p] > 0 else 0
                    st_dt = sumar_dias_cl(hoy, int(np.floor(sb)))
                    en_dt = sumar_dias_cl(hoy, int(np.ceil(eb))) if kgs_i[p] > 0 else st_dt
                    sim_gantt.append({
                        "Estructura": re_c["Etiqueta"], "Proceso": p,
                        "Inicio": st_dt, "Fin": en_dt, "Duracion": dur_p, "Kilos": kgs_i[p]
                    })

            df_sg = pd.DataFrame(sim_gantt)
            f_ini_req_dt = pd.to_datetime(df_sg[df_sg["Kilos"] > 0]["Inicio"]).min() if not df_sg[df_sg["Kilos"] > 0].empty else hoy
            dias_ciclo_cot = int(np.ceil(total_span_dias))

            start_real_off = max(0.0, backward_shift)
            cur_real = {p: max(start_real_off, cursor[p]) for p in PROCESOS}
            cur_vac = {p: start_real_off for p in PROCESOS}
            max_fin_r, max_fin_v = f_ini_req_dt, f_ini_req_dt
            for _, re_c in df_ce.iterrows():
                kgs_i = {p: float(re_c[p]) for p in PROCESOS}
                _, _, _, ffr = programar_cola(kgs_i, cap_dia_kg, hoy, cur_real, min_off=start_real_off)
                _, _, _, ffv = programar_cola(kgs_i, cap_dia_kg, hoy, cur_vac, min_off=start_real_off)
                if ffr > max_fin_r: max_fin_r = ffr
                if ffv > max_fin_v: max_fin_v = ffv

            dias_desv_meta = max(0, contar_dias_cl(f_term_c, max_fin_r))
            if backward_shift < 0:
                dias_desv_meta = max(dias_desv_meta, abs(int(np.floor(backward_shift))))

            df_ssem = dist_semanal_cl(df_sg)
            all_sems_union = sorted(set(semanas_cols) | (set(df_ssem["Semana"].unique()) if not df_ssem.empty else set()))
            hrs_sim = {s: info_sem_iso(s) for s in all_sems_union}
            cap_sim = {
                p: {
                    s: (float(st.session_state.matriz_dotacion_semanal.loc[st.session_state.matriz_dotacion_semanal["Proceso"] == p, s].values[0])
                        if s in st.session_state.matriz_dotacion_semanal.columns else DOTACION_DEF[p])
                       * hrs_sim[s][0] * oee_act[p] * ratios_act.get(p, RATIO_DEF[p])
                    for s in all_sems_union
                }
                for p in PROCESOS
            }

            st.markdown("---")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("🚀 Inicio Requerido (Hacia Atrás)", f_ini_req_dt.strftime("%d/%m/%Y"), delta="⚠️ Fecha pasada" if backward_shift < 0 else "En plazo", delta_color="inverse" if backward_shift < 0 else "off")
            k2.metric("🎯 Fecha Término Deseado", f_term_c.strftime("%d/%m/%Y"), delta=f"Ciclo: {dias_ciclo_cot} d. hábiles", delta_color="off")
            k3.metric("🏭 Entrega Real c/ Carga Actual", max_fin_r.strftime("%d/%m/%Y"), delta="✅ Factible en plazo" if dias_desv_meta == 0 else f"🔴 +{dias_desv_meta} d. háb. desviación", delta_color="normal" if dias_desv_meta == 0 else "inverse")
            k4.metric("⚖️ Total Cotizado", f"{fmt_u(df_ce['Kg_Cotizado'].sum(), f_u, fmt_s)} {u_corto}")

            st.markdown("---")
            st.markdown(f"#### 2️⃣ Carta Gantt / Cuadro de Carga Semanal Cotizada ({unidad_medida})")
            cgf, _ = st.columns([2.5, 4.5])
            est_f = cgf.selectbox("🔍 Filtrar Gantt por Estructura Cotizada:", ["Todas las Estructuras (Proyecto Completo)"] + df_ce["Etiqueta"].tolist(), index=0, key="sim_f_est")

            df_gv = df_sg if est_f.startswith("Todas") else df_sg[df_sg["Estructura"] == est_f]
            df_sv_sem = df_ssem if est_f.startswith("Todas") else (df_ssem[df_ssem["Estructura"] == est_f] if not df_ssem.empty else pd.DataFrame())
            piv_sv = df_sv_sem.groupby(["Proceso", "Semana"])["Kilos"].sum().to_dict() if not df_sv_sem.empty else {}
            piv_st = df_ssem.groupby(["Proceso", "Semana"])["Kilos"].sum().to_dict() if not df_ssem.empty else {}

            df_gv_act = df_gv[df_gv["Kilos"] > 0]
            if not df_gv_act.empty:
                dt_min_g = pd.to_datetime(df_gv_act["Inicio"]).min()
                dt_max_g = pd.to_datetime(df_gv_act["Fin"]).max()
                w_min_g = f"{dt_min_g.isocalendar()[0]}-{dt_min_g.isocalendar()[1]:02d}"
                w_max_g = f"{dt_max_g.isocalendar()[0]}-{dt_max_g.isocalendar()[1]:02d}"
                sems_gantt_cot = sems_consecutivas(w_min_g, w_max_g, min_c=1)
            else:
                sems_gantt_cot = [sem_actual_iso]

            info_ps = {}
            for p in PROCESOS:
                sp = df_gv[(df_gv["Proceso"] == p) & (df_gv["Kilos"] > 0)]
                kgt = float(sp["Kilos"].sum()) if not sp.empty else 0.0
                chk = {s: piv_planta_all.get((p, s), 0.0) + piv_st.get((p, s), 0.0) for s in sems_gantt_cot}
                info_ps[p] = {"ini": pd.to_datetime(sp["Inicio"]).min().strftime("%d/%m/%Y"), "fin": pd.to_datetime(sp["Fin"]).max().strftime("%d/%m/%Y"), "dias": int(np.ceil(kgt / cap_dia_kg[p])), "total_kg": kgt, "check_ov": chk} if (not sp.empty and kgt > 0) else {"ini": "-", "fin": "-", "dias": 0, "total_kg": 0.0, "check_ov": chk}

            components.html(render_cuadro_semanal(piv_sv, info_ps, sems_gantt_cot, cap_sim, f_u, fmt_s, u_lbl, h=350, h_ini_lbl="Inicio<br>Req.", h_fin_lbl="Término<br>Obj."), height=360, scrolling=False)

            st.markdown("---")
            st.markdown(f"#### 3️⃣ Disponibilidad de Planta: Rangos de Fechas sin Carga por Proceso ({unidad_medida})")
            st.caption("Identifica los tramos de fechas libres en cada estación según la cola ASAP actual para ubicar estructuras sin generar retrasos:")
            cap_sem_ref = {p: cap_sim[p].get(sem_actual_iso, cap_dia_kg[p] * 5.0) for p in PROCESOS}
            df_libres = calcular_rangos_sin_carga(df_gantt, hoy, cap_sem_ref, f_u, fmt_s, u_lbl)
            st.dataframe(df_libres, hide_index=True, use_container_width=True)

            st.markdown("---")
            cd1, cd2 = st.columns(2)
            cuellos = []
            for p in PROCESOS:
                for s in sems_gantt_cot:
                    kc = piv_st.get((p, s), 0.0)
                    if kc <= 0: continue
                    ka, cs = piv_planta_all.get((p, s), 0.0), cap_sim[p].get(s, 0.0)
                    if cs > 0 and (ka + kc) > cs:
                        cuellos.append({"Proceso": p, "Semana": s, f"Planta ({u_lbl})": fmt_u(ka, f_u, fmt_s), f"Cotiz. ({u_lbl})": fmt_u(kc, f_u, fmt_s), f"Total ({u_lbl})": fmt_u(ka+kc, f_u, fmt_s), f"Cap. ({u_lbl})": fmt_u(cs, f_u, fmt_s), "Ocupación": f"{((ka+kc)/cs)*100:.0f}%"})
            df_cuellos = pd.DataFrame(cuellos)

            with cd1:
                st.markdown("#### 🚨 Procesos con Sobrecapacidad y Semanas Críticas")
                if df_cuellos.empty: st.success("✅ Ningún proceso supera su capacidad semanal en la ventana cotizada.")
                else: st.dataframe(df_cuellos, hide_index=True, use_container_width=True)

            p_crit = set(df_cuellos["Proceso"].unique()) if not df_cuellos.empty else {p for p in PROCESOS if cursor[p] > max(0.0, backward_shift) and df_ce[p].sum() > 0}
            imp_rows = []
            if not df_gantt.empty and p_crit:
                dgc = df_gantt[(df_gantt["Proceso"].isin(p_crit)) & (df_gantt["Kilos"] > UMBRAL_TERMINADO_KG) & (df_gantt["Fin"] >= max(hoy, f_ini_req_dt))]
                if not dgc.empty:
                    grp = dgc.groupby(["Proyecto_Display", "Subproyecto", "Identificador de fila", "Fecha_Prog_Str"], as_index=False).agg(Kilos_Oc=("Kilos", "sum"), Procs=("Proceso", lambda x: ", ".join(sorted(set(x)))), Fin_E=("Fin", "max")).sort_values("Kilos_Oc", ascending=False)
                    for _, rp in grp.head(12).iterrows():
                        imp_rows.append({"Proyecto": rp["Proyecto_Display"], "Subproyecto": rp["Subproyecto"], "Procesos Críticos": rp["Procs"], f"Carga ({u_lbl})": fmt_u(rp["Kilos_Oc"], f_u, fmt_s), "Fin_Est": pd.Timestamp(rp["Fin_E"]).strftime("%d/%m/%Y"), "F. Prog": rp["Fecha_Prog_Str"]})
            df_imp = pd.DataFrame(imp_rows)

            with cd2:
                st.markdown("#### 🏗️ Proyectos Activos que Ocupan la Ventana Cotizada")
                if df_imp.empty: st.success("✅ Ningún proyecto activo bloquea la ventana cotizada.")
                else: st.dataframe(df_imp, hide_index=True, use_container_width=True)

            df_est_pdf = pd.DataFrame({
                "Estructura": df_ce["Etiqueta"].tolist() + ["TOTAL COTIZACIÓN"],
                "% Ing": ["100%"] * len(df_ce) + ["100%"],
                "% CNCL": [f"{x:.0f}%" for x in df_ce["p_cncl"]] + ["-"],
                "% Dob": [f"{x:.0f}%" for x in df_ce["p_dob"]] + ["-"],
                "% Sol": [f"{x:.0f}%" for x in df_ce["p_sol"]] + ["-"],
                "Cotizado": [fmt_u(x, f_u, fmt_s) for x in df_ce["Kg_Cotizado"]] + [fmt_u(df_ce["Kg_Cotizado"].sum(), f_u, fmt_s)],
                **{p: [fmt_u(x, f_u, fmt_s) for x in df_ce[p]] + [fmt_u(df_ce[p].sum(), f_u, fmt_s)] for p in PROCESOS}
            })
            df_g_pdf = pd.DataFrame([
                {
                    "Procesos": f"{i+1}. {p}",
                    "Inicio Req.": info_ps[p]["ini"],
                    "Término Obj.": info_ps[p]["fin"],
                    "Días": info_ps[p]["dias"],
                    f"Total ({u_lbl})": fmt_u(info_ps[p]["total_kg"], f_u, fmt_s) if info_ps[p]["total_kg"] > 0 else "-",
                    "_ov_sems": {s for s in sems_gantt_cot[:12] if info_ps[p]["check_ov"].get(s, 0) > cap_sim[p].get(s, 9e9) > 0},
                    **{s: fmt_u(piv_st[(p, s)], f_u, fmt_s) if piv_st.get((p, s), 0) > 0 else "-" for s in sems_gantt_cot[:12]}
                }
                for i, p in enumerate(PROCESOS)
            ])
            pdf_bytes = crear_pdf_cotizacion(
                proy=nom_cot, cli=cli_cot,
                f_ini_req=f_ini_req_dt.strftime("%d/%m/%Y"), f_term_des=f_term_c.strftime("%d/%m/%Y"), f_real_cola=max_fin_r.strftime("%d/%m/%Y"),
                dias_ciclo=dias_ciclo_cot, dias_desv=dias_desv_meta,
                tot_cot_fmt=fmt_u(df_ce["Kg_Cotizado"].sum(), f_u, fmt_s),
                oee_cncl=oee_cncl_pct, oee_otros=oee_otros_pct, u_lbl=u_lbl,
                df_est=df_est_pdf, df_gantt=df_g_pdf, sems=sems_gantt_cot, df_cuellos=df_cuellos, df_imp=df_imp, df_libres=df_libres
            )
            pdf_slot.download_button("📄 Descargar Informe PDF", data=pdf_bytes, file_name=f"Informe_Cotizacion_{clean_txt(nom_cot)}_{datetime.now().strftime('%Y%m%d')}.pdf", mime="application/pdf", use_container_width=True, key="dl_pdf_cot")

except Exception as e:
    st.error(f"Error procesando los datos: {e}")
