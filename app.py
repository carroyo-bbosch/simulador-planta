import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import smartsheet
import re
import unicodedata
from datetime import datetime
import numpy as np

try:
    from fpdf import FPDF
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False

st.set_page_config(page_title="Control de Despachos & Simulador de Carga", layout="wide")
st.markdown("<style>[data-testid='stSidebar']>div:first-child{padding-top:.6rem}[data-testid='stSidebar'] .stVerticalBlock{gap:.38rem}</style>", unsafe_allow_html=True)

# -------------------------------------------------------------
# 0. CONFIGURACIÓN BASE Y CALENDARIO LABORAL CHILE
# -------------------------------------------------------------
PROCESOS = ["Ingeniería", "CNCL", "DoblezL", "Soldadura", "Fabricado", "Galvanizado", "Embalaje"]
DOTACION_DEF = {"Ingeniería": 8, "CNCL": 12, "DoblezL": 3, "Soldadura": 12, "Fabricado": 1, "Galvanizado": 1, "Embalaje": 8}
RATIO_DEF = {"Ingeniería": 120.0, "CNCL": 120.0, "DoblezL": 100.0, "Soldadura": 60.0, "Fabricado": 1500.0, "Galvanizado": 2000.0, "Embalaje": 350.0}
VERSION_KEY = "v12_light_oee40_sim"

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
    return 0 if d2 <= d1 else sum(1 for i in range(1, (d2 - d1).days + 1) if hrs_dia_cl(d1 + pd.Timedelta(days=i)) > 0)

# -------------------------------------------------------------
# 1. CONEXIÓN SMARTSHEET Y HELPERS
# -------------------------------------------------------------
def clean_txt(t):
    return re.sub(r'[^a-zA-Z0-9]', '', unicodedata.normalize('NFKD', str(t)).encode('ASCII', 'ignore').decode('utf-8')).lower() if isinstance(t, str) else ""

@st.cache_data(ttl=300)
def cargar_hoja(sid):
    cli = smartsheet.Smartsheet(st.secrets["SMARTSHEET_ACCESS_TOKEN"])
    h = cli.Sheets.get_sheet(int(sid))
    return pd.DataFrame([[c.value for c in f.cells] for f in h.rows], columns=[col.title.strip() for col in h.columns])

@st.cache_data(ttl=300)
def cargar_hoja_tipologias():
    cli = smartsheet.Smartsheet(st.secrets["SMARTSHEET_ACCESS_TOKEN"])
    sid = next((int(st.secrets[k]) for k in ["SHEET_ID_ESTIMACION", "SHEET_ID_TIPOLOGIAS", "SHEET_ID_SIMULADOR"] if k in st.secrets), None)
    if not sid:
        obj = clean_txt("Estimación Producción Programa Despacho")
        for s in cli.Sheets.list_sheets(include_all=True).data:
            if obj in clean_txt(s.name):
                sid = s.id
                break
    if not sid: return pd.DataFrame()
    h = cli.Sheets.get_sheet(int(sid))
    return pd.DataFrame([[c.value for c in f.cells] for f in h.rows], columns=[col.title.strip() for col in h.columns])

def buscar_col_kg(cols, clave, prios):
    for t in prios:
        tc = clean_txt(t)
        for c in cols:
            if clean_txt(c) == tc: return c
    cands = [c for c in cols if ("kilo" in c.lower() or "kg" in c.lower()) and clave in c.lower() and "fecha" not in c.lower()]
    if cands: return next((x for x in cands if "por" in x.lower()), cands[0])
    return next((c for c in cols if clave in c.lower() and not any(x in c.lower() for x in ["check","¿","?","listo","ok","estado","pasa","fecha"])), None)

def num_val(v):
    if pd.isna(v) or v is None: return 0.0
    if isinstance(v, (int, float)): return max(0.0, float(v))
    s = str(v).strip()
    if not s or s.lower() in ["none", "nan", "-", "false", "true"]: return 0.0
    if "," in s and "." in s: s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s: s = s.replace(",", ".")
    try: return max(0.0, float(s))
    except Exception: return 0.0

def pct_val(v):
    if pd.isna(v) or v is None: return 0.0
    try:
        x = float(str(v).replace("%", "").replace(",", ".").strip()) if isinstance(v, str) else float(v)
        return 0.0 if x <= 0 else (x if x <= 1.0 else min(1.0, x / 100.0))
    except Exception:
        return 0.0

def fmt_u(kg, f, fmt):
    return f"{float(kg) / f:{fmt}}"

def sems_consecutivas(w_ini, w_fin, min_c=8):
    try:
        y1, wk1 = map(int, str(w_ini).split("-")); y2, wk2 = map(int, str(w_fin).split("-"))
        d1, d2 = pd.Timestamp.fromisocalendar(y1, wk1, 4), pd.Timestamp.fromisocalendar(y2, wk2, 4)
    except Exception:
        d1 = pd.Timestamp.now().normalize(); d2 = d1 + pd.Timedelta(weeks=min_c)
    if d1 > d2: d2 = d1
    res, cur = [], d1
    while cur <= d2 or len(res) < min_c:
        iy, iw, _ = cur.isocalendar()
        res.append(f"{iy}-{iw:02d}")
        cur += pd.Timedelta(weeks=1)
    return res

# -------------------------------------------------------------
# 2. GENERADOR DE INFORME PDF DE COTIZACIÓN
# -------------------------------------------------------------
def crear_pdf_cotizacion(proy, cli, f_ini, f_fin, dias_h, dias_atr, oee, u_lbl, df_est, df_gantt, sems, df_cuellos, df_imp):
    if HAS_FPDF:
        pdf = FPDF(orientation="L", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=12)
        pdf.add_page()
        pdf.set_fill_color(19, 91, 121)
        pdf.rect(0, 0, 297, 20, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_xy(10, 4)
        pdf.cell(0, 6, "INFORME DE FACTIBILIDAD Y CARGA DE PLANTA - COTIZACION", ln=True)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_x(10)
        pdf.cell(0, 5, f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')} | Calendario Chile (40h/sem, sin feriados) | OEE: {oee:.0f}%", ln=True)

        pdf.ln(6); pdf.set_text_color(15, 23, 42); pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, f"1. Resumen Cotizacion: {proy} | Cliente: {cli}", ln=True)
        pdf.set_font("Helvetica", "", 9)
        for w, txt, ln in [(68, f"Inicio Simulado: {f_ini}", 0), (74, f"Entrega Estimada: {f_fin}", 0), (68, f"Plazo: {dias_h} dias habiles", 0), (67, f"Impacto Carga: +{dias_atr} dias", 1)]:
            pdf.cell(w, 6, txt, border=1, ln=ln)

        pdf.ln(3); pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, f"2. Desglose por Tipologia de Estructura ({u_lbl})", ln=True)
        we = [54, 27, 27, 27, 27, 27, 29, 29, 30]
        he = ["Tipologia", "Cotizado", "Ingenieria", "CNCL", "DoblezL", "Soldadura", "Fabricado", "Galvaniz.", "Embalaje"]
        pdf.set_fill_color(19, 91, 121); pdf.set_text_color(255, 255, 255); pdf.set_font("Helvetica", "B", 8)
        for w, h in zip(we, he): pdf.cell(w, 6, h, border=1, align="L" if w > 50 else "C", fill=True)
        pdf.ln(); pdf.set_text_color(15, 23, 42); pdf.set_font("Helvetica", "", 8)
        for _, r in df_est.iterrows():
            tot_f = str(r["Tipologia"]).startswith("TOTAL")
            if tot_f: pdf.set_font("Helvetica", "B", 8); pdf.set_fill_color(241, 245, 249)
            for ic, (w, col) in enumerate(zip(we, ["Tipologia","Cotizado","Ingeniería","CNCL","DoblezL","Soldadura","Fabricado","Galvanizado","Embalaje"])):
                pdf.cell(w, 5.5, str(r[col])[:30], border=1, align="L" if ic == 0 else "R", fill=tot_f)
            pdf.ln()
            if tot_f: pdf.set_font("Helvetica", "", 8)

        pdf.ln(3); pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, f"3. Cronograma Estimado por Proceso ({u_lbl})", ln=True)
        ss = sems[:9]; wg = [34, 22, 22, 15, 24] + [17.5] * len(ss)
        pdf.set_fill_color(19, 91, 121); pdf.set_text_color(255, 255, 255); pdf.set_font("Helvetica", "B", 7.5)
        for w, h in zip(wg, ["Proceso", "Inicio Est.", "Termino Est.", "Dias", f"Total ({u_lbl})"] + ss):
            pdf.cell(w, 6, h, border=1, align="C", fill=True)
        pdf.ln(); pdf.set_text_color(15, 23, 42); pdf.set_font("Helvetica", "", 7.5)
        for _, r in df_gantt.iterrows():
            vals = [r["Proceso"], r["Inicio"], r["Termino"], str(r["Dias"]), r["Total"]] + [str(r.get(s, "-")) for s in ss]
            for ic, (w, v) in enumerate(zip(wg, vals)):
                pdf.cell(w, 5.5, v, border=1, align="L" if ic == 0 else ("C" if ic in [1, 2] else "R"))
            pdf.ln()

        pdf.ln(3); pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "4. Diagnostico de Sobrecapacidad y Proyectos que Impactan Plazo", ln=True)
        pdf.set_font("Helvetica", "", 8.5)
        if df_cuellos.empty:
            pdf.cell(0, 5, "- Sin sobrecapacidad critica en las semanas proyectadas.", ln=True)
        else:
            for _, rc in df_cuellos.head(6).iterrows():
                pdf.cell(0, 5, f"- [Sobrecarga] {rc['Proceso']} Sem {rc['Semana']}: Total {rc['Total']} vs Cap {rc['Capacidad']} ({rc['Ocupacion']})", ln=True)
        if not df_imp.empty:
            pdf.ln(1); pdf.set_font("Helvetica", "B", 8.5)
            pdf.cell(0, 5, "Proyectos activos en cola que desplazan esta cotizacion:", ln=True)
            pdf.set_font("Helvetica", "", 8.5)
            for _, rp in df_imp.head(6).iterrows():
                pdf.cell(0, 5, f"- {rp['Proyecto']} ({rp['Subproyecto']}): {rp['Carga']} {u_lbl} en procesos criticos (Fin Est: {rp['Fin_Est']})", ln=True)
        raw_out = pdf.output(dest="S")
        return raw_out.encode("latin-1") if isinstance(raw_out, str) else bytes(raw_out)

    # Fallback PDF nativo mínimo
    bs, nl = chr(92), chr(10)
    esc = lambda t: t.encode("latin-1", "replace").decode("latin-1").replace(bs, bs*2).replace("(", bs+"(").replace(")", bs+")")
    lns = [f"INFORME COTIZACION: {proy} ({cli})", f"Inicio: {f_ini} | Entrega Est: {f_fin} | Plazo: {dias_h}d (+{dias_atr}d carga) | OEE: {oee:.0f}%"] + [
        f"{r['Tipologia']}: Cot={r['Cotizado']} CNCL={r['CNCL']} Dob={r['DoblezL']} Sol={r['Soldadura']} Fab/Galv/Emb={r['Fabricado']}" for _, r in df_est.iterrows()
    ]
    st_cmd = f"BT{nl}/F1 9 Tf{nl}40 800 Td{nl}14 TL{nl}" + nl.join(f"({esc(x)}) Tj T*" for x in lns[:50]) + f"{nl}ET"
    objs = ["1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj", "2 0 obj << /Type /Pages /Kids [4 0 R] /Count 1 >> endobj",
            "3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
            "4 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 5 0 R /Resources << /Font << /F1 3 0 R >> >> >> endobj",
            f"5 0 obj << /Length {len(st_cmd)} >> stream{nl}{st_cmd}{nl}endstream endobj"]
    out, offs = f"%PDF-1.4{nl}", [0]
    for o in objs: offs.append(len(out)); out += o + nl
    xref = len(out)
    out += f"xref{nl}0 6{nl}0000000000 65535 f {nl}" + "".join(f"{x:010d} 00000 n {nl}" for x in offs[1:]) + f"trailer << /Size 6 /Root 1 0 R >>{nl}startxref{nl}{xref}{nl}%%EOF"
    return out.encode("latin-1", "replace")

# -------------------------------------------------------------
# 3. RENDERIZADORES HTML COMPACTOS
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
.ov{background:#fef2f2;color:#dc2626;font-weight:700;padding:1px 4px;border-radius:3px}
.dim{color:#cbd5e1}.cut{border-right:2px solid #94a3b8!important}.ws{width:64px;min-width:64px}
"""
html_box = lambda css, tbl, h: f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{CSS_BASE}.box{{max-height:{h}px}}{css}</style></head><body><div class='box'>{tbl}</div></body></html>"

def render_tabla_despachos(df_m, tot, f_u, fmt, u_lbl):
    css = ".c0{position:sticky;left:0;width:96px;min-width:96px;max-width:96px;z-index:3;overflow:hidden;text-overflow:ellipsis}.c1{position:sticky;left:96px;width:78px;min-width:78px;max-width:78px;z-index:3;overflow:hidden;text-overflow:ellipsis}.c2{position:sticky;left:174px;width:106px;min-width:106px;max-width:106px;z-index:3}thead .c0,thead .c1,thead .c2{z-index:10;background:#135b79}tfoot td{position:sticky;bottom:0;background:#f1f5f9;font-weight:700;color:#0f172a;border-top:2px solid #64748b;z-index:6}tfoot .c0,tfoot .c1,tfoot .c2{z-index:9;background:#f1f5f9}.wn{width:62px;min-width:62px}.wd{width:72px;min-width:72px}"
    rows = []
    for i, r in df_m.iterrows():
        bg = "#fff" if i % 2 == 0 else "#fafbfc"
        diag, tip = str(r["Diagnostico"]), r.get("Ruta_Detalle", "")
        badge = f"<span class='{'b-red' if 'Retraso' in diag else 'b-grn'}'>{diag}</span>"
        ci = "color:#dc2626;font-weight:600" if r["Kg por cargar (Ing)"] > 0 else "color:#475569"
        cf = "color:#dc2626;font-weight:700" if r["Desviacion_Dias"] > 0 else "color:#15803d;font-weight:700"
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

def render_cuadro_semanal(piv, info_p, sems, cap_din, f_u, fmt, u_lbl, h=350):
    css = ".p0{position:sticky;left:0;width:110px;min-width:110px;max-width:110px;z-index:3}.p1{position:sticky;left:110px;width:76px;min-width:76px;max-width:76px;z-index:3}.p2{position:sticky;left:186px;width:76px;min-width:76px;max-width:76px;z-index:3}.p3{position:sticky;left:262px;width:48px;min-width:48px;max-width:48px;z-index:3}.p4{position:sticky;left:310px;width:85px;min-width:85px;max-width:85px;z-index:3}thead .p0,thead .p1,thead .p2,thead .p3,thead .p4{z-index:10;background:#135b79}"
    rows = []
    for i, pk in enumerate(PROCESOS):
        bg = "#fff" if i % 2 == 0 else "#fafbfc"
        inf = info_p.get(pk, {})
        tkg = float(inf.get("total_kg", 0.0))
        tds = [f"<td class='r ws'><span class='{'ov' if float(inf.get('check_ov',{}).get(s,piv.get((pk,s),0)))>cap_din.get(pk,{}).get(s,9e9)>0 else ''}'>{fmt_u(piv[(pk,s)],f_u,fmt)}</span></td>" if piv.get((pk,s),0)>0 else "<td class='r ws dim'>-</td>" for s in sems]
        rows.append(f"<tr class='row' style='background:{bg}'><td class='l b p0' style='background:{bg}'>{i+1}. {pk}</td><td class='c p1' style='background:{bg}'>{inf.get('ini','-')}</td><td class='c p2' style='background:{bg}'>{inf.get('fin','-')}</td><td class='r p3' style='background:{bg}'>{int(inf.get('dias',0))}</td><td class='r b p4 cut' style='background:{bg}'>{fmt_u(tkg,f_u,fmt) if tkg>0 else '-'}</td>{''.join(tds)}</tr>")
    th_sems = ''.join(f"<th class='c ws'>{s}</th>" for s in sems)
    tbl = f"<table><thead><tr><th class='l p0'>Procesos</th><th class='c p1'>Inicio<br>Est.</th><th class='c p2'>Término<br>Est.</th><th class='c p3'>Cant.<br>Días</th><th class='r p4 cut'>Total<br>({u_lbl})</th>{th_sems}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
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
    css = ".d0{position:sticky;left:0;width:96px;min-width:96px;max-width:96px;z-index:3}.d1{position:sticky;left:96px;width:82px;min-width:82px;max-width:82px;z-index:3}.d2{position:sticky;left:178px;width:72px;min-width:72px;max-width:72px;z-index:3}.d3{position:sticky;left:250px;width:82px;min-width:82px;max-width:82px;z-index:3}.d4{position:sticky;left:332px;width:44px;min-width:44px;max-width:44px;z-index:3}.d5{position:sticky;left:376px;width:74px;min-width:74px;max-width:74px;z-index:3}.r1 th{top:0;background:#f8fafc;color:#475569;font-weight:700;z-index:7}.r2 th{top:27px;background:#f1f5f9;color:#0f172a;font-weight:700;z-index:7}.r3 th{top:54px;background:#135b79;color:#fff;z-index:7}.r1 .fix,.r2 .d0,.r2 .d1,.r2 .d2,.r2 .d3,.r2 .d4,.r2 .d5,.r3 .d0,.r3 .d1,.r3 .d2,.r3 .d3,.r3 .d4,.r3 .d5{z-index:11}.r1 .fix{left:0;width:450px;min-width:450px}.r2 .d0,.r2 .d1,.r2 .d2,.r2 .d3,.r2 .d4,.r2 .d5{background:#f1f5f9}.r3 .d0,.r3 .d1,.r3 .d2,.r3 .d3,.r3 .d4,.r3 .d5{background:#135b79}"
    tot_d = int(df_p["Cant_Dias"].sum()) if not df_p.empty else 0
    tot_k = float(df_p["Kilos_Procesar"].sum()) if not df_p.empty else 0.0
    tot_s = {s: float(df_p[s].sum()) if (not df_p.empty and s in df_p.columns) else 0.0 for s in sems}
    th_caps = "".join(f"<th class='r ws'>{fmt_u(cap_sem.get(s,0),f_u,fmt)}</th>" for s in sems)
    th_tots = [f"<th class='r ws'><span class='{'ov' if tot_s[s]>cap_sem.get(s,0)>0 else ''}'>{fmt_u(tot_s[s],f_u,fmt) if tot_s[s]>0 else '-'}</span></th>" for s in sems]
    rows = []
    for i, r in df_p.iterrows():
        bg, tip = ("#fff" if i % 2 == 0 else "#fafbfc"), r.get("Ruta_Tooltip", "")
        tds = [f"<td class='r ws'><span class='{'ov' if float(r.get(s,0))>cap_sem.get(s,0)>0 else ''}'>{fmt_u(r[s],f_u,fmt)}</span></td>" if float(r.get(s,0))>0 else "<td class='r ws dim'>-</td>" for s in sems]
        rows.append(f"<tr class='row' style='background:{bg}'><td title='{tip}' class='l b d0' style='background:{bg}'>{r['Identificador de fila']}</td><td title='{tip}' class='l d1' style='background:{bg}'>{r['Subproyecto']}</td><td class='c d2' style='background:{bg}'>{r.get('Fecha_Despacho','-')}</td><td class='c d3' style='background:{bg}'>{r.get('Fecha_Est_Display','-')}</td><td class='r d4' style='background:{bg}'>{int(r['Cant_Dias'])}</td><td class='r b d5 cut' style='background:{bg}'>{fmt_u(r['Kilos_Procesar'],f_u,fmt)}</td>{''.join(tds)}</tr>")
    th_sems = ''.join(f"<th class='c ws'>{s}</th>" for s in sems)
    tbl = f"<table><thead><tr class='r1'><th colspan='6' class='r fix cut'>Capacidad Semanal ({u_lbl}):</th>{th_caps}</tr><tr class='r2'><th class='l d0'>TOTAL PEND.</th><th class='l d1'>{len(df_p)} lotes</th><th class='c d2'>-</th><th class='c d3'>-</th><th class='r d4'>{tot_d}</th><th class='r d5 cut'>{fmt_u(tot_k,f_u,fmt)}</th>{''.join(th_tots)}</tr><tr class='r3'><th class='l d0'>ID Fila</th><th class='l d1'>Subproy.</th><th class='c d2'>Fecha<br>Desp.</th><th class='c d3'>Fecha Est.<br>Entrega</th><th class='c d4'>Cant.<br>Días</th><th class='r d5 cut'>{u_lbl} por<br>Procesar</th>{th_sems}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    return html_box(css, tbl, 530)

# -------------------------------------------------------------
# 4. MOTOR DE PROGRAMACIÓN EN COLA Y DISTRIBUCIÓN SEMANAL
# -------------------------------------------------------------
def dist_semanal_cl(df_g):
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

def programar_cola(kgs, cap_d, hoy_ts, cur_dict, min_off=0.0):
    d = {p: kgs.get(p, 0.0) / cap_d[p] for p in PROCESOS}
    t_ing_e = d["Ingeniería"]
    t_cncl_s = 0.50 * d["Ingeniería"]; t_cncl_e = t_cncl_s + d["CNCL"]
    t_dob_s = t_cncl_s + 0.30 * d["CNCL"]; t_dob_e = t_dob_s + d["DoblezL"]
    t_sol_s = max(t_dob_s + 0.25 * d["DoblezL"], t_cncl_s + 0.50 * d["CNCL"]) if d["DoblezL"] > 0 else t_cncl_s + 0.30 * d["CNCL"]; t_sol_e = t_sol_s + d["Soldadura"]
    t_fab_s = (t_sol_s + 0.50 * d["Soldadura"]) if d["Soldadura"] > 0 else ((t_dob_s + 0.50 * d["DoblezL"]) if d["DoblezL"] > 0 else t_cncl_s + 0.50 * d["CNCL"])
    t_fab_e = max(t_fab_s + d["Fabricado"], t_ing_e, t_cncl_e, t_dob_e, t_sol_e)
    t_gal_s = max(t_fab_s + 0.50 * d["Fabricado"], t_sol_e) if d["Fabricado"] > 0 else t_sol_e; t_gal_e = max(t_gal_s + d["Galvanizado"], t_fab_e)
    t_emb_s = max(t_gal_s + 0.30 * d["Galvanizado"], t_fab_e) if d["Galvanizado"] > 0 else t_gal_e; t_emb_e = max(t_emb_s + d["Embalaje"], t_gal_e)
    e_rel = {"Ingeniería": t_ing_e, "CNCL": t_cncl_e, "DoblezL": t_dob_e, "Soldadura": t_sol_e, "Fabricado": t_fab_e, "Galvanizado": t_gal_e, "Embalaje": t_emb_e}

    s_a, e_a = {}, {}
    for p in PROCESOS:
        if p == "Ingeniería": req = min_off
        elif p == "CNCL": req = s_a["Ingeniería"] + 0.50 * d["Ingeniería"]
        elif p == "DoblezL": req = s_a["CNCL"] + 0.30 * d["CNCL"]
        elif p == "Soldadura": req = max(s_a["DoblezL"] + 0.25 * d["DoblezL"], s_a["CNCL"] + 0.50 * d["CNCL"]) if d["DoblezL"] > 0 else s_a["CNCL"] + 0.30 * d["CNCL"]
        elif p == "Fabricado": req = (s_a["Soldadura"] + 0.50 * d["Soldadura"]) if d["Soldadura"] > 0 else ((s_a["DoblezL"] + 0.50 * d["DoblezL"]) if d["DoblezL"] > 0 else s_a["CNCL"] + 0.50 * d["CNCL"])
        elif p == "Galvanizado": req = max(s_a["Fabricado"] + 0.50 * d["Fabricado"], e_a["Soldadura"]) if d["Fabricado"] > 0 else e_a["Soldadura"]
        else: req = max(s_a["Galvanizado"] + 0.30 * d["Galvanizado"], e_a["Fabricado"]) if d["Galvanizado"] > 0 else e_a["Fabricado"]

        s_a[p] = max(req, cur_dict[p]) if d[p] > 0 else req
        e_a[p] = s_a[p] + d[p]
        if p == "Fabricado": e_a[p] = max(e_a[p], e_a["Ingeniería"], e_a["CNCL"], e_a["DoblezL"], e_a["Soldadura"])
        elif p == "Galvanizado": e_a[p] = max(e_a[p], e_a["Fabricado"])
        elif p == "Embalaje": e_a[p] = max(e_a[p], e_a["Galvanizado"])
        if d[p] > 0:
            if p in ["Fabricado", "Galvanizado", "Embalaje"]: s_a[p] = max(s_a[p], e_a[p] - d[p])
            cur_dict[p] = e_a[p]

    dias_tot = int(np.ceil(max(0.0, e_a["Embalaje"])))
    return s_a, e_a, e_rel, t_emb_e, dias_tot, (sumar_dias_cl(hoy_ts, dias_tot) if dias_tot > 0 else hoy_ts)

# -------------------------------------------------------------
# 5. CONTROLES BARRA LATERAL (OEE POR DEFECTO EN 40%)
# -------------------------------------------------------------
st.sidebar.header("⚙️ Configuración Planta")
unidad_medida = st.sidebar.radio("📏 Unidad:", ["Kilos (kg)", "Toneladas (t)"], index=0, horizontal=True)
es_ton = (unidad_medida == "Toneladas (t)")
f_u, fmt_s, u_lbl, u_corto = (1000.0, ",.2f", "Ton", "t") if es_ton else (1.0, ",.0f", "Kg", "kg")

oee_pct = st.sidebar.slider("Eficiencia Operativa / OEE (%)", 10, 100, 40)
eficiencia = oee_pct / 100.0
st.sidebar.caption("🕒 **Jornada Chile:** Lun-Mar 9h, Mié-Jue 8h, Vie 6h (-1h colación) | Sin feriados nac.")

st.title("📋 Control Integral de Despachos & Simulador de Carga")
if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()
    st.session_state.pop("dotacion_version_key", None)
    st.rerun()

try:
    with st.spinner("Sincronizando información de planta..."):
        df_back_raw = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
        df_prod_raw = cargar_hoja(st.secrets["SHEET_ID_PRODUCTIVIDAD"])
        df_tip_raw = cargar_hoja_tipologias()

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

    # Productividad Smartsheet
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

    with st.sidebar.expander("⚖️ Productividad (Kg/HH)", expanded=False):
        if "df_prod_ratios" not in st.session_state:
            st.session_state.df_prod_ratios = pd.DataFrame([{"Proceso": p, "Kg/HH Sistema": ratios_sis.get(p, RATIO_DEF[p]), "Kg/HH Simulado": ratios_sis.get(p, RATIO_DEF[p])} for p in PROCESOS])
        else:
            for p in PROCESOS: st.session_state.df_prod_ratios.loc[st.session_state.df_prod_ratios["Proceso"] == p, "Kg/HH Sistema"] = ratios_sis.get(p, RATIO_DEF[p])
        ed_prod = st.data_editor(st.session_state.df_prod_ratios, column_config={"Proceso": st.column_config.TextColumn("Proceso", disabled=True), "Kg/HH Sistema": st.column_config.NumberColumn("Sistema", disabled=True, format="%.1f"), "Kg/HH Simulado": st.column_config.NumberColumn("Simulado", min_value=1.0, max_value=10000.0, step=5.0, format="%.1f")}, hide_index=True, key="ed_sb_ratios")
        st.session_state.df_prod_ratios = ed_prod
        ratios_act = dict(zip(ed_prod["Proceso"], ed_prod["Kg/HH Simulado"]))

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
    # 6. CAPACIDADES Y SECUENCIACIÓN EDD DE CARTERA
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

    cap_dia_kg = {p: max(100.0, dot_efec[p] * 8.0 * eficiencia * ratios_act.get(p, RATIO_DEF[p])) for p in PROCESOS}

    lotes_prep = []
    for _, lt in df_hor.iterrows():
        kgs = {p: num_val(lt.get(col)) if col else 0.0 for p, col in [("Ingeniería",c_kg_ing),("CNCL",c_kg_cncl),("DoblezL",c_kg_dob),("Soldadura",c_kg_sol),("Fabricado",c_kg_fab),("Galvanizado",c_kg_gal),("Embalaje",c_kg_emb)]}
        activos = [p for p in PROCESOS if kgs[p] > 0]
        lotes_prep.append({"id": str(lt[c_id]), "subp": str(lt[c_sub]), "f_prog": lt["Fecha_DT"], "f_emb": lt["Fecha_Emb_DT"], "proy": str(lt["Proy_Disp"]), "kg_prog": float(lt["Kg_Prog_Num"]), "kg_ord": num_val(lt.get(c_ord)) if c_ord else float(lt["Kg_Prog_Num"]), "kgs": kgs, "n_proc": len(activos), "ruta": " → ".join(activos) if activos else "Completado / Embalado"})

    lotes_prep.sort(key=lambda x: (x["f_prog"], -x["n_proc"], x["subp"]))
    hoy = pd.Timestamp.now().normalize()
    cursor = {p: 0.0 for p in PROCESOS}
    filas_res, gantt_all = [], []

    for idx_edd, it in enumerate(lotes_prep, 1):
        kgs, tip = it["kgs"], f"EDD #{idx_edd} | Procesos ({it['n_proc']}): {it['ruta']}"
        if kgs["Embalaje"] < 10.0 and pd.notna(it["f_emb"]):
            f_fin, desv, dias_tot = it["f_emb"], max(0, (it["f_emb"] - it["f_prog"]).days), 0
            diag = f"🔴 Retraso (+{desv}d)" if desv > 0 else "🟢 Embalado (OK)"
            for p in PROCESOS:
                gantt_all.append({"Identificador de fila": it["id"], "Subproyecto": it["subp"], "Proyecto_Display": it["proy"], "Fecha_Prog_Str": it["f_prog"].strftime("%d/%m/%Y"), "Proceso": p, "Inicio": f_fin, "Fin": f_fin, "Fecha_Limite": it["f_prog"], "Duracion": 0, "Kilos": kgs[p], "Fuera_Plazo": f_fin > it["f_prog"], "Dias_Desv_Proc": desv, "Ruta_Tooltip": tip})
        else:
            ideal = max(0.0, float(contar_dias_cl(hoy, it["f_prog"])) - sum(kgs[p] / cap_dia_kg[p] for p in ["Fabricado","Galvanizado","Embalaje"]))
            s_a, e_a, e_rel, t_emb_e, dias_tot, _ = programar_cola(kgs, cap_dia_kg, hoy, cursor, min_off=ideal)
            if dias_tot == 0:
                f_fin, desv, diag = it["f_prog"], 0, "🟢 Completado"
            else:
                f_fin = sumar_dias_cl(hoy, dias_tot)
                if f_fin <= it["f_prog"]:
                    holg = contar_dias_cl(f_fin, it["f_prog"])
                    f_fin, desv, diag = it["f_prog"], 0, f"🟢 En Fecha ({holg}d)"
                else:
                    desv = (f_fin - it["f_prog"]).days
                    diag = f"🔴 Retraso (+{desv}d)"
            for p in PROCESOS:
                dur_p = max(1, int(np.ceil(e_a[p] - s_a[p]))) if kgs[p] > 0 else 0
                st_dt, en_dt = sumar_dias_cl(hoy, int(np.floor(s_a[p]))), (sumar_dias_cl(hoy, max(1, int(np.ceil(e_a[p])))) if kgs[p] > 0 else sumar_dias_cl(hoy, int(np.floor(s_a[p]))))
                f_lim = sumar_dias_cl(it["f_prog"], -max(0, int(np.ceil(t_emb_e - e_rel[p]))))
                fp_b = bool(kgs[p] > 0 and en_dt > f_lim)
                gantt_all.append({"Identificador de fila": it["id"], "Subproyecto": it["subp"], "Proyecto_Display": it["proy"], "Fecha_Prog_Str": it["f_prog"].strftime("%d/%m/%Y"), "Proceso": p, "Inicio": st_dt, "Fin": en_dt, "Fecha_Limite": f_lim, "Duracion": dur_p, "Kilos": kgs[p], "Fuera_Plazo": fp_b, "Dias_Desv_Proc": max(1, (en_dt - f_lim).days) if fp_b else 0, "Ruta_Tooltip": tip})

        filas_res.append({
            "Identificador de fila": it["id"], "Subproyecto": it["subp"], "Proyecto_Display": it["proy"],
            "Kg Orden Proceder": it["kg_ord"], "Kg por cargar (Ing)": kgs["Ingeniería"], "Kg por procesar CNCL": kgs["CNCL"],
            "Kg por procesar DoblezL": kgs["DoblezL"], "Kg por procesar Soldadura": kgs["Soldadura"],
            "Kg por procesar Fabricado": kgs["Fabricado"], "Kg por galvanizar": kgs["Galvanizado"], "Kg por Embalar": kgs["Embalaje"],
            "Kilos Programados": it["kg_prog"], "Cant_Procesos": it["n_proc"], "Ruta_Detalle": tip,
            "Fecha Programada": it["f_prog"].strftime("%d/%m/%Y"), "Fecha Estimada Finalizar": f_fin.strftime("%d/%m/%Y"),
            "Fecha_DT": it["f_prog"], "Fecha_Fin_DT": f_fin, "Total_Dias": dias_tot, "Desviacion_Dias": desv, "Diagnostico": diag
        })

    df_res, df_gantt = pd.DataFrame(filas_res), pd.DataFrame(gantt_all)

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

    cap_din_sem = {p: {s: (float(st.session_state.matriz_dotacion_semanal.loc[st.session_state.matriz_dotacion_semanal["Proceso"] == p, s].values[0]) if s in st.session_state.matriz_dotacion_semanal.columns else DOTACION_DEF[p]) * hrs_sem_info[s][0] * eficiencia * ratios_act.get(p, RATIO_DEF[p]) for s in semanas_cols} for p in PROCESOS}

    # -------------------------------------------------------------
    # 8. PESTAÑAS PRINCIPALES
    # -------------------------------------------------------------
    tab_ctrl, tab_sim = st.tabs(["📋 Control de Despachos & Carga Semanal", "🧮 Simulador de Cotizaciones"])

    with tab_ctrl:
        c1, c2, c3, c4, c5 = st.columns([1.2, 1.2, 1.2, 1.2, 1.2])
        if c5.checkbox("🔴 Solo con retraso", key="f_atr_global"):
            df_mostrar = df_mostrar[df_mostrar["Desviacion_Dias"] > 0]
        df_mostrar = df_mostrar.sort_values(by=["Fecha_DT", "Cant_Procesos", "Subproyecto"], ascending=[True, False, True])

        tot_l, atr_l = len(df_mostrar), len(df_mostrar[df_mostrar["Desviacion_Dias"] > 0])
        max_r = df_mostrar["Desviacion_Dias"].max() if not df_mostrar.empty else 0
        c1.metric("Lotes en Vista", tot_l); c2.metric("Lotes en Fecha", tot_l - atr_l)
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
        tot_d1 = {k: df_mostrar[col].sum() for k, col in [("orden","Kg Orden Proceder"),("ing","Kg por cargar (Ing)"),("cncl","Kg por procesar CNCL"),("doblez","Kg por procesar DoblezL"),("sold","Kg por procesar Soldadura"),("fab","Kg por procesar Fabricado"),("galv","Kg por galvanizar"),("emb","Kg por Embalar")]}
        components.html(render_tabla_despachos(df_mostrar, tot_d1, f_u, fmt_s, u_lbl), height=520, scrolling=False)

        st.markdown("---")
        st.markdown(f"### 📊 Cuadro de Carga Semanal por Proceso ({unidad_medida})")
        info_p = {}
        for p in PROCESOS:
            sp = df_gf[(df_gf["Proceso"] == p) & (df_gf["Kilos"] > 0)] if not df_gf.empty else pd.DataFrame()
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
            else:
                kg_p = num_val(lt.get(mapa_kg.get(proc_sel, ""), 0.0))
                dias_p = int(np.ceil(kg_p / cap_dia_kg[proc_sel])) if kg_p > 0 else 0
                fl = df_st[(df_st["Identificador de fila"] == id_f) & (df_st["Proceso"] == proc_sel)] if not df_st.empty else pd.DataFrame()
                s_dict = fl.groupby("Semana")["Kilos"].sum().to_dict() if not fl.empty else {}
                rp = df_gf[(df_gf["Identificador de fila"] == id_f) & (df_gf["Proceso"] == proc_sel)] if not df_gf.empty else pd.DataFrame()
                if not rp.empty:
                    f_est_s, fp = pd.Timestamp(rp["Fin"].values[0]).strftime("%d/%m/%Y"), bool(rp["Fuera_Plazo"].values[0])
                    tip_a = f"Desviado +{int(rp['Dias_Desv_Proc'].values[0])}d (Límite: {pd.Timestamp(rp['Fecha_Limite'].values[0]).strftime('%d/%m/%Y')})"
                else:
                    f_est_s, fp, tip_a = "-", False, ""
            if kg_p > 50.0:
                lotes_t2.append({"Identificador de fila": id_f, "Subproyecto": lt["Subproyecto"], "Fecha_DT": f_dt, "Cant_Procesos": int(lt["Cant_Procesos"]), "Ruta_Tooltip": lt["Ruta_Detalle"], "Fecha_Despacho": lt["Fecha Programada"], "Fecha_Est_Display": f"{f_est_s} <span style='color:#dc2626;cursor:help' title='{tip_a}'>⚠️</span>" if fp else f_est_s, "Cant_Dias": dias_p, "Kilos_Procesar": kg_p, **{s: s_dict.get(s, 0.0) for s in semanas_cols}})

        df_t2 = pd.DataFrame(lotes_t2)
        if df_t2.empty:
            st.info(f"🎉 No hay lotes pendientes en **{proc_sel}** (Carga ≤ {fmt_u(50.0, f_u, fmt_s)} {u_corto}).")
        else:
            df_t2 = df_t2.sort_values(by=["Fecha_DT", "Cant_Procesos", "Subproyecto"], ascending=[True, False, True])
            components.html(render_carga_proceso(df_t2, semanas_cols, cap_v, f_u, fmt_s, u_lbl), height=540, scrolling=False)
        st.markdown("---")
        st.download_button("📥 Descargar Plan de Despachos Completo (CSV)", data=df_mostrar.to_csv(index=False).encode('utf-8'), file_name=f"plan_despachos_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")

    # =============================================================
    # PESTAÑA 2: SIMULADOR DE COTIZACIONES
    # =============================================================
    with tab_sim:
        mapa_tip = {}
        if not df_tip_raw.empty:
            ct = df_tip_raw.columns.tolist()
            c_tip = next((c for c in ct if "g_tipolog" in c.lower() or "tipolog" in c.lower()), ct[0])
            c_pi = next((c for c in ct if "%" in c and "ingenier" in c.lower()), None)
            c_pc = next((c for c in ct if "%" in c and "cncl" in c.lower()), None)
            c_pd = next((c for c in ct if "%" in c and "doblez" in c.lower()), None)
            c_ps = next((c for c in ct if "%" in c and "soldadura" in c.lower()), None)
            for _, rt in df_tip_raw.iterrows():
                nt = str(rt.get(c_tip, "")).strip()
                if nt and nt.lower() not in ["none", "nan"] and nt not in mapa_tip:
                    mapa_tip[nt] = {"Ingeniería": pct_val(rt.get(c_pi)) if c_pi else 0.0, "CNCL": pct_val(rt.get(c_pc)) if c_pc else 0.0, "DoblezL": pct_val(rt.get(c_pd)) if c_pd else 0.0, "Soldadura": pct_val(rt.get(c_ps)) if c_ps else 0.0, "Fabricado": 1.0, "Galvanizado": 1.0, "Embalaje": 1.0}
        else:
            st.warning("⚠️ No se encontró la hoja **'Estimación Producción Programa Despacho'**. Usando tipologías base.")
            mapa_tip = {"T ANCLAJE": {"Ingeniería": 0.0, "CNCL": 0.65, "DoblezL": 0.25, "Soldadura": 0.45, "Fabricado": 1.0, "Galvanizado": 1.0, "Embalaje": 1.0}, "T SUSPENSION": {"Ingeniería": 0.0, "CNCL": 0.70, "DoblezL": 0.30, "Soldadura": 0.35, "Fabricado": 1.0, "Galvanizado": 1.0, "Embalaje": 1.0}}

        l_tips = sorted(list(mapa_tip.keys()))
        ch1, ch2 = st.columns([3.5, 1.5])
        with ch1:
            st.markdown(f"### 🧮 Simulador de Carga para Cotizaciones ({unidad_medida})")
            st.caption("Ingresa estructuras cotizadas y su peso para proyectar fecha de entrega, carga semanal por estación y cuellos de botella.")
        pdf_slot = ch2.empty()

        cs1, cs2, cs3 = st.columns([2.2, 2.0, 1.5])
        nom_cot = cs1.text_input("🏗️ Nombre Proyecto Cotizado:", value="Proyecto Cotización Nueva", key="sim_nom")
        cli_cot = cs2.text_input("🏢 Cliente:", value="Cliente Cotización", key="sim_cli")
        f_ini_c = pd.Timestamp(cs3.date_input("📅 Fecha Inicio Deseada:", value=hoy.date(), min_value=hoy.date(), key="sim_fini")).normalize()

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
            pc = mapa_tip.get(ts, {"Ingeniería": 0.0, "CNCL": 0.0, "DoblezL": 0.0, "Soldadura": 0.0, "Fabricado": 1.0, "Galvanizado": 1.0, "Embalaje": 1.0})
            calc_rows.append({"Etiqueta": f"{idx_s+1}. {ts}", "Tipologia": ts, "p_cncl": pc["CNCL"]*100, "p_dob": pc["DoblezL"]*100, "p_sol": pc["Soldadura"]*100, "Kg_Cotizado": kg_c, **{p: kg_c * pc.get(p, 1.0) for p in PROCESOS}})

        if not calc_rows:
            st.info("Ingresa al menos una estructura con peso > 0 para simular la entrega.")
        else:
            df_ce = pd.DataFrame(calc_rows)
            df_sh = pd.DataFrame({
                "Estructura": df_ce["Etiqueta"], "% CNCL": df_ce["p_cncl"].map(lambda x: f"{x:.0f}%"), "% DoblezL": df_ce["p_dob"].map(lambda x: f"{x:.0f}%"), "% Soldad.": df_ce["p_sol"].map(lambda x: f"{x:.0f}%"),
                f"Cotizado ({u_lbl})": df_ce["Kg_Cotizado"].map(lambda x: fmt_u(x, f_u, fmt_s)),
                **{f"{p} ({u_lbl})": df_ce[p].map(lambda x: fmt_u(x, f_u, fmt_s)) for p in PROCESOS}
            })
            tot_r = pd.DataFrame([{"Estructura": "TOTAL COTIZACIÓN", "% CNCL": "-", "% DoblezL": "-", "% Soldad.": "-", f"Cotizado ({u_lbl})": fmt_u(df_ce["Kg_Cotizado"].sum(), f_u, fmt_s), **{f"{p} ({u_lbl})": fmt_u(df_ce[p].sum(), f_u, fmt_s) for p in PROCESOS}}])
            st.dataframe(pd.concat([df_sh, tot_r], ignore_index=True), hide_index=True, use_container_width=True)

            off_ini = float(contar_dias_cl(hoy, f_ini_c))
            cur_real, cur_vac = {p: max(off_ini, cursor[p]) for p in PROCESOS}, {p: off_ini for p in PROCESOS}
            sim_gantt, max_fin_r, max_fin_v = [], f_ini_c, f_ini_c

            for _, re_c in df_ce.iterrows():
                kgs_i = {p: float(re_c[p]) for p in PROCESOS}
                sr, er, _, _, _, ffr = programar_cola(kgs_i, cap_dia_kg, hoy, cur_real, min_off=off_ini)
                _, _, _, _, _, ffv = programar_cola(kgs_i, cap_dia_kg, hoy, cur_vac, min_off=off_ini)
                if ffr > max_fin_r: max_fin_r = ffr
                if ffv > max_fin_v: max_fin_v = ffv
                for p in PROCESOS:
                    dur_p = max(1, int(np.ceil(er[p] - sr[p]))) if kgs_i[p] > 0 else 0
                    sim_gantt.append({"Estructura": re_c["Etiqueta"], "Proceso": p, "Inicio": sumar_dias_cl(hoy, int(np.floor(sr[p]))), "Fin": sumar_dias_cl(hoy, max(1, int(np.ceil(er[p])))) if kgs_i[p] > 0 else sumar_dias_cl(hoy, int(np.floor(sr[p]))), "Duracion": dur_p, "Kilos": kgs_i[p]})

            df_sg = pd.DataFrame(sim_gantt)
            dias_plazo = contar_dias_cl(f_ini_c, max_fin_r)
            dias_atr_carga = contar_dias_cl(max_fin_v, max_fin_r)

            df_ssem = dist_semanal_cl(df_sg)
            if not df_ssem.empty:
                df_ssem = df_ssem[df_ssem["Semana"] >= sem_actual_iso].copy()
                max_s_sim = max(max_sem_all, df_ssem["Semana"].max())
            else:
                max_s_sim = max_sem_all

            sems_sim = sems_consecutivas(sem_actual_iso, max_s_sim, min_c=10)
            hrs_sim = {s: info_sem_iso(s) for s in sems_sim}
            cap_sim = {p: {s: (float(st.session_state.matriz_dotacion_semanal.loc[st.session_state.matriz_dotacion_semanal["Proceso"] == p, s].values[0]) if s in st.session_state.matriz_dotacion_semanal.columns else DOTACION_DEF[p]) * hrs_sim[s][0] * eficiencia * ratios_act.get(p, RATIO_DEF[p]) for s in sems_sim} for p in PROCESOS}

            st.markdown("---")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("📅 Fecha Estimada Entrega", max_fin_r.strftime("%d/%m/%Y"))
            k2.metric("⏱️ Plazo Total Cotización", f"{dias_plazo} días hábiles")
            k3.metric("⚖️ Total Cotizado", f"{fmt_u(df_ce['Kg_Cotizado'].sum(), f_u, fmt_s)} {u_corto}")
            k4.metric("⏳ Impacto Carga Actual", f"+{dias_atr_carga} días hábiles", delta=f"Planta vacía: {max_fin_v.strftime('%d/%m/%Y')}", delta_color="off")

            st.markdown("---")
            st.markdown(f"#### 2️⃣ Carta Gantt / Cuadro de Carga Semanal Cotizada ({unidad_medida})")
            cgf, _ = st.columns([2.5, 4.5])
            est_f = cgf.selectbox("🔍 Filtrar Gantt por Estructura Cotizada:", ["Todas las Estructuras (Proyecto Completo)"] + df_ce["Etiqueta"].tolist(), index=0, key="sim_f_est")

            df_gv = df_sg if est_f.startswith("Todas") else df_sg[df_sg["Estructura"] == est_f]
            df_sv_sem = df_ssem if est_f.startswith("Todas") else (df_ssem[df_ssem["Estructura"] == est_f] if not df_ssem.empty else pd.DataFrame())
            piv_sv = df_sv_sem.groupby(["Proceso", "Semana"])["Kilos"].sum().to_dict() if not df_sv_sem.empty else {}
            piv_st = df_ssem.groupby(["Proceso", "Semana"])["Kilos"].sum().to_dict() if not df_ssem.empty else {}

            info_ps = {}
            for p in PROCESOS:
                sp = df_gv[(df_gv["Proceso"] == p) & (df_gv["Kilos"] > 0)]
                kgt = float(sp["Kilos"].sum()) if not sp.empty else 0.0
                chk = {s: piv_planta_all.get((p, s), 0.0) + piv_st.get((p, s), 0.0) for s in sems_sim}
                info_ps[p] = {"ini": pd.to_datetime(sp["Inicio"]).min().strftime("%d/%m/%Y"), "fin": pd.to_datetime(sp["Fin"]).max().strftime("%d/%m/%Y"), "dias": int(np.ceil(kgt / cap_dia_kg[p])), "total_kg": kgt, "check_ov": chk} if (not sp.empty and kgt > 0) else {"ini": "-", "fin": "-", "dias": 0, "total_kg": 0.0, "check_ov": chk}

            components.html(render_cuadro_semanal(piv_sv, info_ps, sems_sim, cap_sim, f_u, fmt_s, u_lbl, h=350), height=360, scrolling=False)

            st.markdown("---")
            cd1, cd2 = st.columns(2)
            cuellos = []
            for p in PROCESOS:
                for s in sems_sim:
                    kc = piv_st.get((p, s), 0.0)
                    if kc <= 0: continue
                    ka, cs = piv_planta_all.get((p, s), 0.0), cap_sim[p].get(s, 0.0)
                    if cs > 0 and (ka + kc) > cs:
                        cuellos.append({"Proceso": p, "Semana": s, f"Planta ({u_lbl})": fmt_u(ka, f_u, fmt_s), f"Cotiz. ({u_lbl})": fmt_u(kc, f_u, fmt_s), f"Total ({u_lbl})": fmt_u(ka+kc, f_u, fmt_s), f"Cap. ({u_lbl})": fmt_u(cs, f_u, fmt_s), "Ocupación": f"{((ka+kc)/cs)*100:.0f}%", "Total": fmt_u(ka+kc, f_u, fmt_s), "Capacidad": fmt_u(cs, f_u, fmt_s), "Ocupacion": f"{((ka+kc)/cs)*100:.0f}%"})
            df_cuellos = pd.DataFrame(cuellos)

            with cd1:
                st.markdown("#### 🚨 Procesos con Sobrecapacidad y Semanas Críticas")
                if df_cuellos.empty: st.success("✅ Ningún proceso supera su capacidad semanal.")
                else: st.dataframe(df_cuellos[["Proceso", "Semana", f"Planta ({u_lbl})", f"Cotiz. ({u_lbl})", f"Total ({u_lbl})", f"Cap. ({u_lbl})", "Ocupación"]], hide_index=True, use_container_width=True)

            p_crit = set(df_cuellos["Proceso"].unique()) if not df_cuellos.empty else {p for p in PROCESOS if cursor[p] > off_ini and df_ce[p].sum() > 0}
            imp_rows = []
            if not df_gantt.empty and p_crit:
                dgc = df_gantt[(df_gantt["Proceso"].isin(p_crit)) & (df_gantt["Kilos"] > 0) & (df_gantt["Fin"] >= f_ini_c)]
                if not dgc.empty:
                    grp = dgc.groupby(["Proyecto_Display", "Subproyecto", "Identificador de fila", "Fecha_Prog_Str"], as_index=False).agg(Kilos_Oc=("Kilos", "sum"), Procs=("Proceso", lambda x: ", ".join(sorted(set(x)))), Fin_E=("Fin", "max")).sort_values("Kilos_Oc", ascending=False)
                    for _, rp in grp.head(12).iterrows():
                        imp_rows.append({"Proyecto": rp["Proyecto_Display"], "Subproyecto": rp["Subproyecto"], "ID": rp["Identificador de fila"], "Procesos Críticos": rp["Procs"], f"Carga ({u_lbl})": fmt_u(rp["Kilos_Oc"], f_u, fmt_s), "Carga": fmt_u(rp["Kilos_Oc"], f_u, fmt_s), "Fin_Est": pd.Timestamp(rp["Fin_E"]).strftime("%d/%m/%Y"), "F. Prog": rp["Fecha_Prog_Str"]})
            df_imp = pd.DataFrame(imp_rows)

            with cd2:
                st.markdown("#### 🏗️ Proyectos Activos que Provocan el Retraso")
                if df_imp.empty: st.success("✅ Ningún proyecto activo bloquea la ruta crítica.")
                else: st.dataframe(df_imp[["Proyecto", "Subproyecto", "Procesos Críticos", f"Carga ({u_lbl})", "Fin_Est", "F. Prog"]], hide_index=True, use_container_width=True)

            # Generar y mostrar PDF en la cabecera superior
            df_est_pdf = pd.DataFrame({"Tipologia": df_ce["Tipologia"].tolist() + ["TOTAL COTIZACION"], "Cotizado": [fmt_u(x,f_u,fmt_s) for x in df_ce["Kg_Cotizado"]] + [fmt_u(df_ce["Kg_Cotizado"].sum(),f_u,fmt_s)], **{p: [fmt_u(x,f_u,fmt_s) for x in df_ce[p]] + [fmt_u(df_ce[p].sum(),f_u,fmt_s)] for p in PROCESOS}})
            df_g_pdf = pd.DataFrame([{"Proceso": p, "Inicio": info_ps[p]["ini"], "Termino": info_ps[p]["fin"], "Dias": info_ps[p]["dias"], "Total": fmt_u(info_ps[p]["total_kg"],f_u,fmt_s) if info_ps[p]["total_kg"]>0 else "-", **{s: fmt_u(piv_st[(p,s)],f_u,fmt_s) if piv_st.get((p,s),0)>0 else "-" for s in sems_sim[:9]}} for p in PROCESOS])
            pdf_bytes = crear_pdf_cotizacion(nom_cot, cli_cot, f_ini_c.strftime("%d/%m/%Y"), max_fin_r.strftime("%d/%m/%Y"), dias_plazo, dias_atr_carga, oee_pct, u_lbl, df_est_pdf, df_g_pdf, sems_sim, df_cuellos, df_imp)
            pdf_slot.download_button("📄 Descargar Informe PDF", data=pdf_bytes, file_name=f"Informe_Cotizacion_{clean_txt(nom_cot)}_{datetime.now().strftime('%Y%m%d')}.pdf", mime="application/pdf", use_container_width=True, key="dl_pdf_cot")

except Exception as e:
    st.error(f"Error procesando los datos: {e}")
