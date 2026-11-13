import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import smartsheet
import re
import unicodedata
from datetime import datetime
import numpy as np

st.set_page_config(page_title="Control de Despachos & Carga Semanal", layout="wide")
st.markdown("<style>[data-testid='stSidebar']>div:first-child{padding-top:.7rem}[data-testid='stSidebar'] .stVerticalBlock{gap:.4rem}</style>", unsafe_allow_html=True)

# -------------------------------------------------------------
# 0. CONFIGURACIÓN BASE Y CALENDARIO CHILE
# -------------------------------------------------------------
PROCESOS_LINEA = ["Ingeniería", "CNCL", "DoblezL", "Soldadura", "Fabricado", "Galvanizado", "Embalaje"]
DOTACION_BASE_DEFAULT = {"Ingeniería": 8, "CNCL": 12, "DoblezL": 3, "Soldadura": 12, "Fabricado": 1, "Galvanizado": 1, "Embalaje": 8}
RATIO_FALLBACK_DEFAULT = {"Ingeniería": 120.0, "CNCL": 120.0, "DoblezL": 100.0, "Soldadura": 60.0, "Fabricado": 1500.0, "Galvanizado": 2000.0, "Embalaje": 350.0}
DOTACION_VERSION = "v10_optimized_chile"

FERIADOS_SET = {pd.Timestamp(d).normalize() for d in [
    "2025-01-01","2025-04-18","2025-04-19","2025-05-01","2025-05-21","2025-06-20","2025-06-29","2025-07-16","2025-08-15","2025-09-18","2025-09-19","2025-10-12","2025-10-31","2025-11-01","2025-12-08","2025-12-25",
    "2026-01-01","2026-04-03","2026-04-04","2026-05-01","2026-05-21","2026-06-21","2026-06-29","2026-07-16","2026-08-15","2026-09-18","2026-09-19","2026-10-12","2026-10-31","2026-11-01","2026-12-08","2026-12-25",
    "2027-01-01","2027-03-26","2027-03-27","2027-05-01","2027-05-21","2027-06-21","2027-06-28","2027-07-16","2027-08-15","2027-09-17","2027-09-18","2027-09-19","2027-10-11","2027-10-31","2027-11-01","2027-12-08","2027-12-25",
    "2028-01-01","2028-04-14","2028-04-15","2028-05-01","2028-05-21","2028-06-20","2028-06-26","2028-07-16","2028-08-15","2028-09-18","2028-09-19","2028-09-20","2028-10-09","2028-10-27","2028-11-01","2028-12-08","2028-12-25",
]}

# Lun-Mar: 8-18h (-1h) = 9h | Mié-Jue: 8-17h (-1h) = 8h | Vie: 8-15h (-1h) = 6h
HORAS_DIA_SEMANA = {0: 9.0, 1: 9.0, 2: 8.0, 3: 8.0, 4: 6.0, 5: 0.0, 6: 0.0}

def horas_laborales_fecha(dt):
    d = pd.Timestamp(dt).normalize()
    return 0.0 if d in FERIADOS_SET else HORAS_DIA_SEMANA.get(d.weekday(), 0.0)

def info_horas_semana_iso(sem_str):
    try:
        y, w = map(int, str(sem_str).split("-"))
        lunes = pd.Timestamp.fromisocalendar(y, w, 1)
    except Exception:
        return 40.0, 5, 8.0
    habiles = [h for i in range(7) if (h := horas_laborales_fecha(lunes + pd.Timedelta(days=i))) > 0]
    tot = sum(habiles)
    return tot, len(habiles), (tot / len(habiles) if habiles else 0.0)

def sumar_dias_laborales_chile(fecha_base, n_dias):
    dt = pd.Timestamp(fecha_base).normalize()
    if n_dias == 0:
        while horas_laborales_fecha(dt) <= 0: dt += pd.Timedelta(days=1)
        return dt
    step, rem = (1 if n_dias > 0 else -1), abs(int(n_dias))
    while rem > 0:
        dt += pd.Timedelta(days=step)
        if horas_laborales_fecha(dt) > 0: rem -= 1
    return dt

def contar_dias_laborales_chile(f_ini, f_fin):
    d1, d2 = pd.Timestamp(f_ini).normalize(), pd.Timestamp(f_fin).normalize()
    return 0 if d2 <= d1 else sum(1 for i in range(1, (d2 - d1).days + 1) if horas_laborales_fecha(d1 + pd.Timedelta(days=i)) > 0)

# -------------------------------------------------------------
# 1. SMARTSHEET Y UTILIDADES
# -------------------------------------------------------------
@st.cache_data(ttl=300)
def cargar_hoja(sheet_id):
    cliente = smartsheet.Smartsheet(st.secrets["SMARTSHEET_ACCESS_TOKEN"])
    hoja = cliente.Sheets.get_sheet(int(sheet_id))
    return pd.DataFrame([[c.value for c in f.cells] for f in hoja.rows], columns=[col.title.strip() for col in hoja.columns])

def limpiar_texto(txt):
    if not isinstance(txt, str): return ""
    return re.sub(r'[^a-zA-Z0-9]', '', unicodedata.normalize('NFKD', txt).encode('ASCII', 'ignore').decode('utf-8')).lower()

def buscar_columna_kilos(cols, clave, prioritarios):
    for t in prioritarios:
        tc = limpiar_texto(t)
        for c in cols:
            if limpiar_texto(c) == tc: return c
    cands = [c for c in cols if ("kilo" in c.lower() or "kg" in c.lower()) and clave in c.lower() and "fecha" not in c.lower()]
    if cands: return next((x for x in cands if "por" in x.lower()), cands[0])
    return next((c for c in cols if clave in c.lower() and not any(x in c.lower() for x in ["check","¿","?","listo","ok","estado","pasa","fecha"])), None)

def limpiar_numero(val):
    if pd.isna(val) or val is None: return 0.0
    if isinstance(val, (int, float)): return max(0.0, float(val))
    s = str(val).strip()
    if not s or s.lower() in ["none", "nan", "-", "false", "true"]: return 0.0
    if "," in s and "." in s: s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s: s = s.replace(",", ".")
    try: return max(0.0, float(s))
    except Exception: return 0.0

def fmt_u(val_kg, factor, fmt):
    return f"{float(val_kg) / factor:{fmt}}"

def generar_semanas_consecutivas(min_wk, max_wk, min_count=8):
    try:
        y1, w1 = map(int, str(min_wk).split("-"))
        y2, w2 = map(int, str(max_wk).split("-"))
        dt1, dt2 = pd.Timestamp.fromisocalendar(y1, w1, 4), pd.Timestamp.fromisocalendar(y2, w2, 4)
    except Exception:
        dt1 = pd.Timestamp.now().normalize()
        dt2 = dt1 + pd.Timedelta(weeks=min_count)
    if dt1 > dt2: dt2 = dt1
    weeks, cur = [], dt1
    while cur <= dt2 or len(weeks) < min_count:
        iso_y, iso_w, _ = cur.isocalendar()
        weeks.append(f"{iso_y}-{iso_w:02d}")
        cur += pd.Timedelta(weeks=1)
    return weeks

# -------------------------------------------------------------
# 2. RENDERIZADORES HTML COMPACTOS (CSS MODULAR)
# -------------------------------------------------------------
BASE_CSS = """
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
.dim{color:#cbd5e1}.cut{border-right:2px solid #94a3b8!important}
"""

def wrap_html(css_extra, body_html, max_h=520):
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{BASE_CSS}.box{{max-height:{max_h}px}}{css_extra}</style></head><body><div class='box'>{body_html}</div></body></html>"

def renderizar_tabla_html(df_m, tot, f_u=1.0, fmt=",.0f", u_lbl="Kg"):
    css = """
    .c0{position:sticky;left:0;width:96px;min-width:96px;max-width:96px;z-index:3;overflow:hidden;text-overflow:ellipsis}
    .c1{position:sticky;left:96px;width:78px;min-width:78px;max-width:78px;z-index:3;overflow:hidden;text-overflow:ellipsis}
    .c2{position:sticky;left:174px;width:106px;min-width:106px;max-width:106px;z-index:3}
    thead .c0,thead .c1,thead .c2{z-index:10;background:#135b79}
    tfoot td{position:sticky;bottom:0;background:#f1f5f9;font-weight:700;color:#0f172a;border-top:2px solid #64748b;z-index:6}
    tfoot .c0,tfoot .c1,tfoot .c2{z-index:9;background:#f1f5f9}
    .w-num{width:62px;min-width:62px}.w-dt{width:72px;min-width:72px}
    """
    rows = []
    for idx, r in df_m.iterrows():
        bg = "#fff" if idx % 2 == 0 else "#fafbfc"
        diag = str(r["Diagnostico"])
        badge = f"<span class='{'b-red' if 'Retraso' in diag else 'b-grn'}'>{diag}</span>"
        c_ing = "color:#dc2626;font-weight:600" if r["Kg por cargar (Ing)"] > 0 else "color:#475569"
        c_fin = "color:#dc2626;font-weight:700" if r["Desviacion_Dias"] > 0 else "color:#15803d;font-weight:700"
        tip = r.get("Ruta_Detalle", "")
        rows.append(
            f"<tr class='row' style='background:{bg}'>"
            f"<td title='{tip}' class='l b c0' style='background:{bg}'>{r['Identificador de fila']}</td>"
            f"<td title='{tip}' class='l c1' style='background:{bg}'>{r['Subproyecto']}</td>"
            f"<td class='c c2 cut' style='background:{bg}'>{badge}</td>"
            f"<td class='r w-num'>{fmt_u(r['Kg Orden Proceder'],f_u,fmt)}</td>"
            f"<td class='r w-num' style='{c_ing}'>{fmt_u(r['Kg por cargar (Ing)'],f_u,fmt)}</td>"
            f"<td class='r w-num'>{fmt_u(r['Kg por procesar CNCL'],f_u,fmt)}</td>"
            f"<td class='r w-num'>{fmt_u(r['Kg por procesar DoblezL'],f_u,fmt)}</td>"
            f"<td class='r w-num'>{fmt_u(r['Kg por procesar Soldadura'],f_u,fmt)}</td>"
            f"<td class='r w-num'>{fmt_u(r['Kg por procesar Fabricado'],f_u,fmt)}</td>"
            f"<td class='r w-num'>{fmt_u(r['Kg por galvanizar'],f_u,fmt)}</td>"
            f"<td class='r w-num'>{fmt_u(r['Kg por Embalar'],f_u,fmt)}</td>"
            f"<td class='c w-dt'>{r['Fecha Programada']}</td>"
            f"<td class='c w-dt' style='{c_fin}'>{r['Fecha Estimada Finalizar']}</td></tr>"
        )
    tbl = f"""<table><thead><tr>
        <th class='l c0'>ID Fila</th><th class='l c1'>Subproy.</th><th class='c c2 cut'>Estado / Desv.</th>
        <th class='r w-num'>{u_lbl} Orden<br>Proceder</th><th class='r w-num'>{u_lbl} cargar<br>(Ing)</th>
        <th class='r w-num'>{u_lbl}<br>CNCL</th><th class='r w-num'>{u_lbl}<br>DoblezL</th>
        <th class='r w-num'>{u_lbl}<br>Soldad.</th><th class='r w-num'>{u_lbl}<br>Fabric.</th>
        <th class='r w-num'>{u_lbl}<br>Galvan.</th><th class='r w-num'>{u_lbl}<br>Embalar</th>
        <th class='c w-dt'>Fecha<br>Prog.</th><th class='c w-dt'>Fecha Est.<br>Finalizar</th>
    </tr></thead><tbody>{''.join(rows)}</tbody><tfoot><tr>
        <td class='l c0'>TOTAL</td><td class='l c1'>{len(df_m)} lotes</td><td class='c c2 cut'>-</td>
        <td class='r'>{fmt_u(tot['orden'],f_u,fmt)}</td><td class='r'>{fmt_u(tot['ing'],f_u,fmt)}</td>
        <td class='r'>{fmt_u(tot['cncl'],f_u,fmt)}</td><td class='r'>{fmt_u(tot['doblez'],f_u,fmt)}</td>
        <td class='r'>{fmt_u(tot['sold'],f_u,fmt)}</td><td class='r'>{fmt_u(tot['fab'],f_u,fmt)}</td>
        <td class='r'>{fmt_u(tot['galv'],f_u,fmt)}</td><td class='r'>{fmt_u(tot['emb'],f_u,fmt)}</td>
        <td class='c'>-</td><td class='c'>-</td>
    </tr></tfoot></table>"""
    return wrap_html(css, tbl, 520)

def renderizar_cuadro_semanal_html(piv, info_p, sems, cap_din, f_u=1.0, fmt=",.0f", u_lbl="Kg"):
    css = """
    .p0{position:sticky;left:0;width:110px;min-width:110px;max-width:110px;z-index:3}
    .p1{position:sticky;left:110px;width:76px;min-width:76px;max-width:76px;z-index:3}
    .p2{position:sticky;left:186px;width:76px;min-width:76px;max-width:76px;z-index:3}
    .p3{position:sticky;left:262px;width:48px;min-width:48px;max-width:48px;z-index:3}
    .p4{position:sticky;left:310px;width:85px;min-width:85px;max-width:85px;z-index:3}
    thead .p0,thead .p1,thead .p2,thead .p3,thead .p4{z-index:10;background:#135b79}
    .ws{width:64px;min-width:64px}
    """
    labels = [("Ingeniería","1. Ingeniería"),("CNCL","2. CNCL"),("DoblezL","3. DoblezL"),("Soldadura","4. Soldadura"),("Fabricado","5. Fabricado"),("Galvanizado","6. Galvanizado"),("Embalaje","7. Embalaje")]
    rows = []
    for idx, (pk, pn) in enumerate(labels):
        bg = "#fff" if idx % 2 == 0 else "#fafbfc"
        inf = info_p.get(pk, {})
        tot_kg = float(inf.get("total_kg", 0.0))
        tds = []
        for s in sems:
            v = float(piv.get((pk, s), 0.0))
            cap = cap_din.get(pk, {}).get(s, 9e9)
            if v > 0:
                vs = fmt_u(v, f_u, fmt)
                tds.append(f"<td class='r ws'><span class='{'ov' if v > cap > 0 else ''}'>{vs}</span></td>")
            else:
                tds.append("<td class='r ws dim'>-</td>")
        rows.append(
            f"<tr class='row' style='background:{bg}'>"
            f"<td class='l b p0' style='background:{bg}'>{pn}</td>"
            f"<td class='c p1' style='background:{bg}'>{inf.get('inicio_est','-')}</td>"
            f"<td class='c p2' style='background:{bg}'>{inf.get('termino_est','-')}</td>"
            f"<td class='r p3' style='background:{bg}'>{int(inf.get('dias',0))}</td>"
            f"<td class='r b p4 cut' style='background:{bg}'>{fmt_u(tot_kg,f_u,fmt) if tot_kg>0 else '-'}</td>"
            f"{''.join(tds)}</tr>"
        )
    ths = "".join(f"<th class='c ws'>{s}</th>" for s in sems)
    tbl = f"""<table><thead><tr>
        <th class='l p0'>Procesos</th><th class='c p1'>Inicio<br>Est.</th><th class='c p2'>Término<br>Est.</th>
        <th class='c p3'>Cant.<br>Días</th><th class='r p4 cut'>Total<br>({u_lbl})</th>{ths}
    </tr></thead><tbody>{''.join(rows)}</tbody></table>"""
    return wrap_html(css, tbl, 350)

def render_tabla_horas_dotacion_html(sems, dot_sem, hrs_info):
    css = ".h0{position:sticky;left:0;width:230px;min-width:230px;max-width:230px;z-index:4;background:#fff}.ws{width:64px;min-width:64px}thead .h0{background:#334155;z-index:8}"
    r1, r2, r3 = [], [], []
    for s in sems:
        op = float(dot_sem.get(s, 0.0))
        tot_h, _, prom_d = hrs_info.get(s, (40.0, 5, 8.0))
        r1.append(f"<td class='r ws'>{prom_d:.2f} h</td>")
        r2.append(f"<td class='r ws b'>{op * prom_d:,.1f} h</td>")
        r3.append(f"<td class='r ws b' style='color:#135b79;background:#f0f9ff'>{op * tot_h:,.0f} h</td>")
    ths = "".join(f"<th class='c ws' style='background:#334155'>{s}</th>" for s in sems)
    tbl = f"""<table><thead><tr><th class='l h0 cut'>Desglose Horas Hábiles (Chile)</th>{ths}</tr></thead><tbody>
        <tr><td class='l h0 cut'>⏱️ Horas Diarias por Dotación (Prom/Op)</td>{''.join(r1)}</tr>
        <tr><td class='l h0 cut b' style='background:#fafbfc'>📊 Total Horas Diarias por Dotación</td>{''.join(r2)}</tr>
        <tr><td class='l h0 cut b' style='background:#f8fafc;color:#135b79'>🗓️ Total Horas Semana por Dotación</td>{''.join(r3)}</tr>
    </tbody></table>"""
    return wrap_html(css, tbl, 160)

def render_tabla_carga_proceso_html(df_p, sems, cap_sem, f_u=1.0, fmt=",.0f", u_lbl="Kg"):
    css = """
    .d0{position:sticky;left:0;width:96px;min-width:96px;max-width:96px;z-index:3}
    .d1{position:sticky;left:96px;width:82px;min-width:82px;max-width:82px;z-index:3}
    .d2{position:sticky;left:178px;width:72px;min-width:72px;max-width:72px;z-index:3}
    .d3{position:sticky;left:250px;width:82px;min-width:82px;max-width:82px;z-index:3}
    .d4{position:sticky;left:332px;width:44px;min-width:44px;max-width:44px;z-index:3}
    .d5{position:sticky;left:376px;width:74px;min-width:74px;max-width:74px;z-index:3}
    .r1 th{top:0;background:#f8fafc;color:#475569;font-weight:700;z-index:7}
    .r2 th{top:27px;background:#f1f5f9;color:#0f172a;font-weight:700;z-index:7}
    .r3 th{top:54px;background:#135b79;color:#fff;z-index:7}
    .r1 .fix,.r2 .d0,.r2 .d1,.r2 .d2,.r2 .d3,.r2 .d4,.r2 .d5,.r3 .d0,.r3 .d1,.r3 .d2,.r3 .d3,.r3 .d4,.r3 .d5{z-index:11}
    .r1 .fix{left:0;width:450px;min-width:450px}
    .r2 .d0,.r2 .d1,.r2 .d2,.r2 .d3,.r2 .d4,.r2 .d5{background:#f1f5f9}
    .r3 .d0,.r3 .d1,.r3 .d2,.r3 .d3,.r3 .d4,.r3 .d5{background:#135b79}
    .ws{width:64px;min-width:64px}
    """
    tot_d = int(df_p["Cant_Dias"].sum()) if not df_p.empty else 0
    tot_k = float(df_p["Kilos_Procesar"].sum()) if not df_p.empty else 0.0
    tot_s = {s: float(df_p[s].sum()) if (not df_p.empty and s in df_p.columns) else 0.0 for s in sems}

    th_caps = "".join(f"<th class='r ws'>{fmt_u(cap_sem.get(s,0),f_u,fmt)}</th>" for s in sems)
    th_tots = [f"<th class='r ws'><span class='{'ov' if tot_s[s]>cap_sem.get(s,0)>0 else ''}'>{fmt_u(tot_s[s],f_u,fmt) if tot_s[s]>0 else '-'}</span></th>" for s in sems]
    th_sems = "".join(f"<th class='c ws'>{s}</th>" for s in sems)

    rows = []
    for idx, r in df_p.iterrows():
        bg = "#fff" if idx % 2 == 0 else "#fafbfc"
        tip = r.get("Ruta_Tooltip", "")
        tds = [f"<td class='r ws'><span class='{'ov' if float(r.get(s,0))>cap_sem.get(s,0)>0 else ''}'>{fmt_u(r[s],f_u,fmt)}</span></td>" if float(r.get(s,0))>0 else "<td class='r ws dim'>-</td>" for s in sems]
        rows.append(
            f"<tr class='row' style='background:{bg}'>"
            f"<td title='{tip}' class='l b d0' style='background:{bg}'>{r['Identificador de fila']}</td>"
            f"<td title='{tip}' class='l d1' style='background:{bg}'>{r['Subproyecto']}</td>"
            f"<td class='c d2' style='background:{bg}'>{r.get('Fecha_Despacho','-')}</td>"
            f"<td class='c d3' style='background:{bg}'>{r.get('Fecha_Est_Display','-')}</td>"
            f"<td class='r d4' style='background:{bg}'>{int(r['Cant_Dias'])}</td>"
            f"<td class='r b d5 cut' style='background:{bg}'>{fmt_u(r['Kilos_Procesar'],f_u,fmt)}</td>{''.join(tds)}</tr>"
        )
    tbl = f"""<table><thead>
        <tr class='r1'><th colspan='6' class='r fix cut'>Capacidad Semanal ({u_lbl}):</th>{th_caps}</tr>
        <tr class='r2'><th class='l d0'>TOTAL PEND.</th><th class='l d1'>{len(df_p)} lotes</th><th class='c d2'>-</th><th class='c d3'>-</th><th class='r d4'>{tot_d}</th><th class='r d5 cut'>{fmt_u(tot_k,f_u,fmt)}</th>{''.join(th_tots)}</tr>
        <tr class='r3'><th class='l d0'>ID Fila</th><th class='l d1'>Subproy.</th><th class='c d2'>Fecha<br>Desp.</th><th class='c d3'>Fecha Est.<br>Entrega</th><th class='c d4'>Cant.<br>Días</th><th class='r d5 cut'>{u_lbl} por<br>Procesar</th>{th_sems}</tr>
    </thead><tbody>{''.join(rows)}</tbody></table>"""
    return wrap_html(css, tbl, 540)

# -------------------------------------------------------------
# 3. CONTROLES Y FILTROS GLOBALES EN BARRA LATERAL
# -------------------------------------------------------------
st.sidebar.header("⚙️ Configuración Planta")
unidad_medida = st.sidebar.radio("📏 Unidad:", ["Kilos (kg)", "Toneladas (t)"], index=0, horizontal=True)
factor_u, fmt_u_str, lbl_u, corto_u = (1000.0, ",.2f", "Ton", "t") if unidad_medida == "Toneladas (t)" else (1.0, ",.0f", "Kg", "kg")
eficiencia = st.sidebar.slider("Eficiencia Operativa / OEE (%)", 50, 100, 85) / 100.0
st.sidebar.caption("🕒 **Jornada Chile:** Lun-Mar 9h, Mié-Jue 8h, Vie 6h (-1h colación) | Sin feriados nac.")

st.title("📋 Control Integral de Despachos & Capacidad de Planta")
if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()
    st.session_state.pop("dotacion_version_key", None)
    st.rerun()

try:
    with st.spinner("Sincronizando información de planta..."):
        df_backlog_raw = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
        df_prod_raw = cargar_hoja(st.secrets["SHEET_ID_PRODUCTIVIDAD"])

    cols_b = df_backlog_raw.columns.tolist()
    col_id_fila = next((c for c in cols_b if "identificador" in c.lower() and "fila" in c.lower()), next((c for c in cols_b if "identificador" in c.lower() or "despacho" in c.lower()), cols_b[0]))
    col_sub_b = next((c for c in cols_b if "sub-proyecto" in c.lower() or "subproyecto" in c.lower()), None)
    col_proy = next((c for c in cols_b if c.lower().strip() in ["proyecto","proyectos","cod proyecto","código proyecto"]), next((c for c in cols_b if "proyecto" in c.lower() and "sub" not in c.lower()), None))
    col_nom_proy = next((c for c in cols_b if any(k in c.lower() for k in ["nombre proyecto","nombre del proyecto","nom_proyecto","obra","cliente"])), None)
    col_f_prog = next((c for c in cols_b if "programad" in c.lower() and "fecha" in c.lower()), next((c for c in cols_b if "fecha" in c.lower()), None))
    col_desp_real = next((c for c in cols_b if "despacho" in c.lower() and ("real" in c.lower() or "fecha" in c.lower())), None)
    col_orden = next((c for c in cols_b if "orden" in c.lower() and "proceder" in c.lower()), None)
    col_f_emb = next((c for c in cols_b if "fecha" in c.lower() and "embalaje" in c.lower()), None)

    col_kg_prog = buscar_columna_kilos(cols_b, "programad", ["Kilos Programados", "Kilos programados", "Kg Programados"])
    col_kg_ing = buscar_columna_kilos(cols_b, "cargar", ["Kilos por Cargar_", "Kilos por Cargar"])
    col_kg_cncl = buscar_columna_kilos(cols_b, "cncl", ["Kilos por CNCL", "Kilos por CNCL_"])
    col_kg_doblez = buscar_columna_kilos(cols_b, "doblez", ["Kilos por DoblezL", "Kilos por Doblez"])
    col_kg_sold = buscar_columna_kilos(cols_b, "soldadura", ["Kilos por Soldadura"])
    col_kg_fab = buscar_columna_kilos(cols_b, "fabricar", ["Kilos por Fabricar", "Kilos por Fabricado"])
    col_kg_galv = buscar_columna_kilos(cols_b, "galvanizar", ["Kilos por Galvanizar", "Kilos por Galvanizado"])
    col_kg_emb = buscar_columna_kilos(cols_b, "embalar", ["Kilos por Embalar", "Kilos por Embalaje"])

    # Productividad Smartsheet (coincidencia exacta primero)
    col_p_proc = next((c for c in df_prod_raw.columns if "proceso" in c.lower() and "simulador" in c.lower()), df_prod_raw.columns[0])
    col_p_kghh = next((c for c in df_prod_raw.columns if "kg" in c.lower() and "hh" in c.lower()), df_prod_raw.columns[1])
    ratios_sis = {}
    for p_of in PROCESOS_LINEA:
        pc = limpiar_texto(p_of)
        for _, r in df_prod_raw.iterrows():
            val = pd.to_numeric(r[col_p_kghh], errors='coerce')
            sc = limpiar_texto(str(r[col_p_proc]))
            if pd.notna(val) and val > 0 and (sc == pc or (pc in sc and not (p_of == "Soldadura" and "armado" in sc))):
                ratios_sis[p_of] = float(val)
                if sc == pc: break

    with st.sidebar.expander("⚖️ Productividad (Kg/HH)", expanded=False):
        if "df_prod_ratios" not in st.session_state:
            st.session_state.df_prod_ratios = pd.DataFrame([{"Proceso": p, "Kg/HH Sistema": ratios_sis.get(p, RATIO_FALLBACK_DEFAULT[p]), "Kg/HH Simulado": ratios_sis.get(p, RATIO_FALLBACK_DEFAULT[p])} for p in PROCESOS_LINEA])
        else:
            for p in PROCESOS_LINEA:
                st.session_state.df_prod_ratios.loc[st.session_state.df_prod_ratios["Proceso"] == p, "Kg/HH Sistema"] = ratios_sis.get(p, RATIO_FALLBACK_DEFAULT[p])
        ed_prod = st.data_editor(
            st.session_state.df_prod_ratios,
            column_config={"Proceso": st.column_config.TextColumn("Proceso", disabled=True), "Kg/HH Sistema": st.column_config.NumberColumn("Sistema", disabled=True, format="%.1f"), "Kg/HH Simulado": st.column_config.NumberColumn("Simulado", min_value=1.0, max_value=10000.0, step=5.0, format="%.1f")},
            hide_index=True, key="ed_sb_ratios"
        )
        st.session_state.df_prod_ratios = ed_prod
        ratios_act = dict(zip(ed_prod["Proceso"], ed_prod["Kg/HH Simulado"]))

    val_f = df_backlog_raw[col_f_prog].notna() & (~df_backlog_raw[col_f_prog].astype(str).str.strip().str.lower().isin(["", "none", "nan"]))
    df_act = df_backlog_raw[val_f & (df_backlog_raw[col_desp_real].isna() | df_backlog_raw[col_desp_real].astype(str).str.strip().str.lower().isin(["", "none", "nan"]))].copy() if col_desp_real else df_backlog_raw[val_f].copy()

    df_act["Fecha_DT"] = pd.to_datetime(df_act[col_f_prog], errors='coerce', dayfirst=True)
    df_act = df_act.dropna(subset=["Fecha_DT"]).sort_values("Fecha_DT")
    df_act["Mes_Entrega"] = df_act["Fecha_DT"].dt.strftime("%Y-%m")
    df_act["Kg_Prog_Num"] = df_act[col_kg_prog].apply(limpiar_numero) if col_kg_prog else 0.0
    df_act["Fecha_Emb_DT"] = pd.to_datetime(df_act[col_f_emb], errors='coerce', dayfirst=True) if col_f_emb else pd.NaT
    df_act["Proy_Cod"] = df_act[col_proy].fillna("").astype(str).str.strip() if col_proy else df_act[col_sub_b].apply(lambda s: "-".join(str(s).split("-")[:2]))
    df_act["Proy_Disp"] = (df_act["Proy_Cod"] + " - " + df_act[col_nom_proy].fillna("").astype(str).str.strip()) if col_nom_proy else df_act["Proy_Cod"]

    st.sidebar.markdown("---")
    st.sidebar.subheader("📅 Horizonte & Semanas ISO")
    meses_disp = sorted(df_act["Mes_Entrega"].unique().tolist())
    hoy_dt = datetime.now()
    mes_act_str = hoy_dt.strftime("%Y-%m")
    sem_actual_iso = f"{hoy_dt.isocalendar()[0]}-{hoy_dt.isocalendar()[1]:02d}"
    idx_def = meses_disp.index(mes_act_str) if mes_act_str in meses_disp else next((i for i, m in enumerate(meses_disp) if m >= mes_act_str), 0)
    mes_sel = st.sidebar.selectbox("Despachos desde:", options=meses_disp, index=idx_def)
    df_hor = df_act[df_act["Mes_Entrega"] >= mes_sel].copy()

    try:
        dt_ini_f = pd.to_datetime(f"{mes_sel}-01")
        sem_ini_f_iso = f"{dt_ini_f.isocalendar()[0]}-{dt_ini_f.isocalendar()[1]:02d}"
    except Exception:
        sem_ini_f_iso = sem_actual_iso
    st.sidebar.caption(f"📍 **Sem. Actual:** `{sem_actual_iso}` | ▶️ **Inicio Filtro:** `{sem_ini_f_iso}`")

    # -------------------------------------------------------------
    # 4. CAPACIDADES Y SECUENCIACIÓN EDD
    # -------------------------------------------------------------
    dot_efec = {}
    for p in PROCESOS_LINEA:
        if "matriz_dotacion_semanal" in st.session_state and st.session_state.get("dotacion_version_key") == DOTACION_VERSION:
            df_m = st.session_state.matriz_dotacion_semanal
            fp = df_m[df_m["Proceso"] == p]
            c_sems = [c for c in df_m.columns if c != "Proceso"]
            prom = pd.to_numeric(fp[c_sems].iloc[0], errors='coerce').mean() if (not fp.empty and c_sems) else np.nan
            dot_efec[p] = float(prom) if pd.notna(prom) and prom > 0 else float(DOTACION_BASE_DEFAULT[p])
        else:
            dot_efec[p] = float(DOTACION_BASE_DEFAULT[p])

    cap_dia_kg = {p: max(100.0, dot_efec[p] * 8.0 * eficiencia * ratios_act.get(p, RATIO_FALLBACK_DEFAULT[p])) for p in PROCESOS_LINEA}

    lotes_prep = []
    for _, lt in df_hor.iterrows():
        kgs = {p: limpiar_numero(lt.get(col)) if col else 0.0 for p, col in [("Ingeniería",col_kg_ing),("CNCL",col_kg_cncl),("DoblezL",col_kg_doblez),("Soldadura",col_kg_sold),("Fabricado",col_kg_fab),("Galvanizado",col_kg_galv),("Embalaje",col_kg_emb)]}
        activos = [p for p in PROCESOS_LINEA if kgs[p] > 0]
        lotes_prep.append({
            "id": str(lt[col_id_fila]), "subp": str(lt[col_sub_b]), "f_prog": lt["Fecha_DT"], "f_emb": lt["Fecha_Emb_DT"],
            "proy": str(lt["Proy_Disp"]), "kg_prog": float(lt["Kg_Prog_Num"]),
            "kg_ord": limpiar_numero(lt.get(col_orden)) if col_orden else float(lt["Kg_Prog_Num"]),
            "kgs": kgs, "n_proc": len(activos), "ruta": " → ".join(activos) if activos else "Completado / Embalado"
        })

    lotes_prep.sort(key=lambda x: (x["f_prog"], -x["n_proc"], x["subp"]))
    hoy = pd.Timestamp.now().normalize()
    cursor = {p: 0.0 for p in PROCESOS_LINEA}
    filas_res, gantt_all = [], []

    for idx_edd, it in enumerate(lotes_prep, 1):
        kgs, tip = it["kgs"], f"EDD #{idx_edd} | Procesos ({it['n_proc']}): {it['ruta']}"
        if kgs["Embalaje"] < 10.0 and pd.notna(it["f_emb"]):
            f_fin = it["f_emb"]
            desv = max(0, (f_fin - it["f_prog"]).days)
            diag = f"🔴 Retraso (+{desv}d)" if desv > 0 else "🟢 Embalado (OK)"
            dias_tot = 0
            for p in PROCESOS_LINEA:
                gantt_all.append({"Identificador de fila": it["id"], "Subproyecto": it["subp"], "Proceso": p, "Inicio": f_fin, "Fin": f_fin, "Fecha_Limite": it["f_prog"], "Duracion": 0, "Kilos": kgs[p], "Fuera_Plazo": f_fin > it["f_prog"], "Dias_Desv_Proc": desv, "Ruta_Tooltip": tip})
        else:
            d = {p: kgs[p] / cap_dia_kg[p] for p in PROCESOS_LINEA}
            t_ing_e = d["Ingeniería"]
            t_cncl_s = 0.50 * d["Ingeniería"]; t_cncl_e = t_cncl_s + d["CNCL"]
            t_dob_s = t_cncl_s + 0.30 * d["CNCL"]; t_dob_e = t_dob_s + d["DoblezL"]
            t_sol_s = max(t_dob_s + 0.25 * d["DoblezL"], t_cncl_s + 0.50 * d["CNCL"]) if d["DoblezL"] > 0 else t_cncl_s + 0.30 * d["CNCL"]; t_sol_e = t_sol_s + d["Soldadura"]
            t_fab_s = (t_sol_s + 0.50 * d["Soldadura"]) if d["Soldadura"] > 0 else ((t_dob_s + 0.50 * d["DoblezL"]) if d["DoblezL"] > 0 else t_cncl_s + 0.50 * d["CNCL"])
            t_fab_e = max(t_fab_s + d["Fabricado"], t_ing_e, t_cncl_e, t_dob_e, t_sol_e)
            t_gal_s = max(t_fab_s + 0.50 * d["Fabricado"], t_sol_e) if d["Fabricado"] > 0 else t_sol_e; t_gal_e = max(t_gal_s + d["Galvanizado"], t_fab_e)
            t_emb_s = max(t_gal_s + 0.30 * d["Galvanizado"], t_fab_e) if d["Galvanizado"] > 0 else t_gal_e; t_emb_e = max(t_emb_s + d["Embalaje"], t_gal_e)
            e_rel = {"Ingeniería": t_ing_e, "CNCL": t_cncl_e, "DoblezL": t_dob_e, "Soldadura": t_sol_e, "Fabricado": t_fab_e, "Galvanizado": t_gal_e, "Embalaje": t_emb_e}

            ideal = max(0.0, float(contar_dias_laborales_chile(hoy, it["f_prog"])) - t_emb_e)
            s_a, e_a = {}, {}
            for p in PROCESOS_LINEA:
                if p == "Ingeniería": req = ideal
                elif p == "CNCL": req = s_a["Ingeniería"] + 0.50 * d["Ingeniería"]
                elif p == "DoblezL": req = s_a["CNCL"] + 0.30 * d["CNCL"]
                elif p == "Soldadura": req = max(s_a["DoblezL"] + 0.25 * d["DoblezL"], s_a["CNCL"] + 0.50 * d["CNCL"]) if d["DoblezL"] > 0 else s_a["CNCL"] + 0.30 * d["CNCL"]
                elif p == "Fabricado": req = (s_a["Soldadura"] + 0.50 * d["Soldadura"]) if d["Soldadura"] > 0 else ((s_a["DoblezL"] + 0.50 * d["DoblezL"]) if d["DoblezL"] > 0 else s_a["CNCL"] + 0.50 * d["CNCL"])
                elif p == "Galvanizado": req = max(s_a["Fabricado"] + 0.50 * d["Fabricado"], e_a["Soldadura"]) if d["Fabricado"] > 0 else e_a["Soldadura"]
                else: req = max(s_a["Galvanizado"] + 0.30 * d["Galvanizado"], e_a["Fabricado"]) if d["Galvanizado"] > 0 else e_a["Fabricado"]

                s_a[p] = max(req, cursor[p]) if d[p] > 0 else req
                e_a[p] = s_a[p] + d[p]
                if p == "Fabricado": e_a[p] = max(e_a[p], e_a["Ingeniería"], e_a["CNCL"], e_a["DoblezL"], e_a["Soldadura"])
                elif p == "Galvanizado": e_a[p] = max(e_a[p], e_a["Fabricado"])
                elif p == "Embalaje": e_a[p] = max(e_a[p], e_a["Galvanizado"])
                if d[p] > 0:
                    if p in ["Fabricado", "Galvanizado", "Embalaje"]: s_a[p] = max(s_a[p], e_a[p] - d[p])
                    cursor[p] = e_a[p]

            dias_tot = int(np.ceil(max(0.0, e_a["Embalaje"])))
            if dias_tot == 0:
                f_fin, desv, diag = it["f_prog"], 0, "🟢 Completado"
            else:
                f_fin = sumar_dias_laborales_chile(hoy, dias_tot)
                if f_fin <= it["f_prog"]:
                    holg = contar_dias_laborales_chile(f_fin, it["f_prog"])
                    f_fin, desv, diag = it["f_prog"], 0, f"🟢 En Fecha ({holg}d)"
                else:
                    desv = (f_fin - it["f_prog"]).days
                    diag = f"🔴 Retraso (+{desv}d)"

            for p in PROCESOS_LINEA:
                dur_p = max(1, int(np.ceil(e_a[p] - s_a[p]))) if kgs[p] > 0 else 0
                st_dt = sumar_dias_laborales_chile(hoy, int(np.floor(s_a[p])))
                en_dt = sumar_dias_laborales_chile(hoy, max(1, int(np.ceil(e_a[p])))) if kgs[p] > 0 else st_dt
                f_lim = sumar_dias_laborales_chile(it["f_prog"], -max(0, int(np.ceil(t_emb_e - e_rel[p]))))
                fp_bool = bool(kgs[p] > 0 and en_dt > f_lim)
                gantt_all.append({"Identificador de fila": it["id"], "Subproyecto": it["subp"], "Proceso": p, "Inicio": st_dt, "Fin": en_dt, "Fecha_Limite": f_lim, "Duracion": dur_p, "Kilos": kgs[p], "Fuera_Plazo": fp_bool, "Dias_Desv_Proc": max(1, (en_dt - f_lim).days) if fp_bool else 0, "Ruta_Tooltip": tip})

        filas_res.append({
            "Identificador de fila": it["id"], "Subproyecto": it["subp"], "Proyecto_Display": it["proy"],
            "Kg Orden Proceder": it["kg_ord"], "Kg por cargar (Ing)": kgs["Ingeniería"], "Kg por procesar CNCL": kgs["CNCL"],
            "Kg por procesar DoblezL": kgs["DoblezL"], "Kg por procesar Soldadura": kgs["Soldadura"],
            "Kg por procesar Fabricado": kgs["Fabricado"], "Kg por galvanizar": kgs["Galvanizado"], "Kg por Embalar": kgs["Embalaje"],
            "Kilos Programados": it["kg_prog"], "Cant_Procesos": it["n_proc"], "Ruta_Detalle": tip,
            "Fecha Programada": it["f_prog"].strftime("%d/%m/%Y"), "Fecha Estimada Finalizar": f_fin.strftime("%d/%m/%Y"),
            "Fecha_DT": it["f_prog"], "Fecha_Fin_DT": f_fin, "Total_Dias": dias_tot, "Desviacion_Dias": desv, "Diagnostico": diag
        })

    df_res = pd.DataFrame(filas_res)
    df_gantt = pd.DataFrame(gantt_all)

    # -------------------------------------------------------------
    # 5. FILTROS GLOBALES EN BARRA LATERAL
    # -------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("🎯 Filtros Globales de Cartera")
    c_s1, c_s2 = st.sidebar.columns(2)
    f_anio = c_s1.multiselect("Año:", sorted(df_res["Fecha_DT"].dt.year.dropna().unique().astype(int).tolist()), placeholder="Todos", key="sb_anio")
    p1 = df_res if not f_anio else df_res[df_res["Fecha_DT"].dt.year.isin(f_anio)]
    f_mes = c_s2.multiselect("Mes:", sorted(p1["Fecha_DT"].dt.strftime("%Y-%m").dropna().unique().tolist()), placeholder="Todos", key="sb_mes")
    p2 = p1 if not f_mes else p1[p1["Fecha_DT"].dt.strftime("%Y-%m").isin(f_mes)]

    c_s3, c_s4 = st.sidebar.columns(2)
    f_fec = c_s3.multiselect("Fecha Prog:", [pd.Timestamp(d).strftime("%d/%m/%Y") for d in sorted(p2["Fecha_DT"].dropna().unique())], placeholder="Todas", key="sb_fecha")
    p3 = p2 if not f_fec else p2[p2["Fecha_DT"].dt.strftime("%d/%m/%Y").isin(f_fec)]
    f_desp = c_s4.multiselect("ID Despacho:", sorted([d for d in p3["Identificador de fila"].unique() if d.strip()]), placeholder="Todos", key="sb_desp")
    p4 = p3 if not f_desp else p3[p3["Identificador de fila"].isin(f_desp)]

    f_proy = st.sidebar.multiselect("🏗️ Nombre / Proyecto:", sorted([p for p in p4["Proyecto_Display"].unique() if p.strip()]), placeholder="Todos los proyectos", key="sb_proy")
    p5 = p4 if not f_proy else p4[p4["Proyecto_Display"].isin(f_proy)]
    f_subp = st.sidebar.multiselect("📂 Subproyecto:", sorted([s for s in p5["Subproyecto"].unique() if s.strip()]), placeholder="Todos los subproyectos", key="sb_subp")
    df_mostrar = (p5 if not f_subp else p5[p5["Subproyecto"].isin(f_subp)]).copy()

    # -------------------------------------------------------------
    # 6. MÉTRICAS Y TABLAS EN CASCADA (DESDE SEMANA ACTUAL)
    # -------------------------------------------------------------
    c1, c2, c3, c4, c5 = st.columns([1.2, 1.2, 1.2, 1.2, 1.2])
    if c5.checkbox("🔴 Solo con retraso", key="f_atr_global"):
        df_mostrar = df_mostrar[df_mostrar["Desviacion_Dias"] > 0]
    df_mostrar = df_mostrar.sort_values(by=["Fecha_DT", "Cant_Procesos", "Subproyecto"], ascending=[True, False, True])

    tot_l = len(df_mostrar)
    atr_l = len(df_mostrar[df_mostrar["Desviacion_Dias"] > 0])
    max_r = df_mostrar["Desviacion_Dias"].max() if not df_mostrar.empty else 0
    c1.metric("Lotes en Vista", tot_l)
    c2.metric("Lotes en Fecha", tot_l - atr_l)
    c3.metric("Lotes Desviados", atr_l, delta=f"-{atr_l}" if atr_l > 0 else "0", delta_color="inverse")
    c4.metric("Máximo Retraso", f"+{max_r} días" if max_r > 0 else "0 días", delta_color="inverse")

    lotes_vis = df_mostrar["Identificador de fila"].unique()
    df_g_filt = df_gantt[df_gantt["Identificador de fila"].isin(lotes_vis)].copy()

    def dist_semanal_chile(df_g):
        rows = []
        for _, r in df_g.iterrows():
            if r["Kilos"] <= 0: continue
            cur, end_n = pd.Timestamp(r["Inicio"]).normalize(), pd.Timestamp(r["Fin"]).normalize()
            hrs_w, tot_h = {}, 0.0
            while cur <= end_n:
                h = horas_laborales_fecha(cur)
                if h > 0:
                    iso = f"{cur.isocalendar()[0]}-{cur.isocalendar()[1]:02d}"
                    hrs_w[iso] = hrs_w.get(iso, 0.0) + h
                    tot_h += h
                cur += pd.Timedelta(days=1)
            if tot_h == 0:
                iso = f"{pd.Timestamp(r['Inicio']).isocalendar()[0]}-{pd.Timestamp(r['Inicio']).isocalendar()[1]:02d}"
                hrs_w, tot_h = {iso: 1.0}, 1.0
            for wk, hw in hrs_w.items():
                rows.append({"Semana": wk, "Proceso": r["Proceso"], "Identificador de fila": r["Identificador de fila"], "Kilos": (hw / tot_h) * r["Kilos"]})
        return pd.DataFrame(rows)

    df_sem_tot = dist_semanal_chile(df_g_filt)
    if not df_sem_tot.empty:
        df_sem_tot = df_sem_tot[df_sem_tot["Semana"] >= sem_actual_iso].copy()
        piv_dict = df_sem_tot.groupby(["Proceso", "Semana"])["Kilos"].sum().to_dict()
        max_sem = max(sem_actual_iso, df_sem_tot["Semana"].max())
    else:
        piv_dict, max_sem = {}, sem_actual_iso

    semanas_cols = generar_semanas_consecutivas(sem_actual_iso, max_sem, min_count=8)
    hrs_sem_info = {s: info_horas_semana_iso(s) for s in semanas_cols}

    if st.session_state.get("dotacion_version_key") != DOTACION_VERSION:
        st.session_state.dotacion_version_key = DOTACION_VERSION
        st.session_state.matriz_dotacion_semanal = pd.DataFrame({"Proceso": PROCESOS_LINEA, **{s: [DOTACION_BASE_DEFAULT[p] for p in PROCESOS_LINEA] for s in semanas_cols}})
    else:
        for s in semanas_cols:
            if s not in st.session_state.matriz_dotacion_semanal.columns:
                st.session_state.matriz_dotacion_semanal[s] = [DOTACION_BASE_DEFAULT[p] for p in PROCESOS_LINEA]

    cap_din_sem = {p: {} for p in PROCESOS_LINEA}
    for p in PROCESOS_LINEA:
        rp = ratios_act.get(p, RATIO_FALLBACK_DEFAULT[p])
        fo = st.session_state.matriz_dotacion_semanal[st.session_state.matriz_dotacion_semanal["Proceso"] == p]
        for s in semanas_cols:
            op = float(fo[s].values[0]) if (not fo.empty and s in fo.columns) else DOTACION_BASE_DEFAULT[p]
            cap_din_sem[p][s] = op * hrs_sem_info[s][0] * eficiencia * rp

    # BLOQUE 1: PROGRAMA DE DESPACHOS
    st.markdown("---")
    st.markdown(f"### 📋 Programa de Despachos & Estado de Carga por Estación ({unidad_medida})")
    tot_d1 = {k: df_mostrar[col].sum() for k, col in [("orden","Kg Orden Proceder"),("ing","Kg por cargar (Ing)"),("cncl","Kg por procesar CNCL"),("doblez","Kg por procesar DoblezL"),("sold","Kg por procesar Soldadura"),("fab","Kg por procesar Fabricado"),("galv","Kg por galvanizar"),("emb","Kg por Embalar")]}
    components.html(renderizar_tabla_html(df_mostrar, tot_d1, factor_u, fmt_u_str, lbl_u), height=530, scrolling=False)

    # BLOQUE 2: CUADRO DE CARGA SEMANAL POR PROCESO
    st.markdown("---")
    st.markdown(f"### 📊 Cuadro de Carga Semanal por Proceso ({unidad_medida})")
    info_p_cuadro = {}
    for p in PROCESOS_LINEA:
        sp = df_g_filt[(df_g_filt["Proceso"] == p) & (df_g_filt["Kilos"] > 0)] if not df_g_filt.empty else pd.DataFrame()
        kgt = float(sp["Kilos"].sum()) if not sp.empty else 0.0
        if not sp.empty and kgt > 0:
            ini_s = pd.to_datetime(sp["Inicio"]).min().strftime("%d/%m/%Y")
            fin_s = pd.to_datetime(sp["Fin"]).max().strftime("%d/%m/%Y")
            dp = int(sp["Duracion"].values[0]) if len(lotes_vis) == 1 else int(np.ceil(kgt / cap_dia_kg[p]))
        else:
            ini_s, fin_s, dp = "-", "-", 0
        info_p_cuadro[p] = {"inicio_est": ini_s, "termino_est": fin_s, "dias": dp, "total_kg": kgt}

    components.html(renderizar_cuadro_semanal_html(piv_dict, info_p_cuadro, semanas_cols, cap_din_sem, factor_u, fmt_u_str, lbl_u), height=360, scrolling=False)

    # BLOQUE 3: CARGA SEMANAL POR DESPACHO & CAPACIDAD DE PLANTA
    st.markdown("---")
    st.markdown(f"### ⚡ Carga Semanal por Despacho & Capacidad de Planta ({unidad_medida})")
    c_t1, _ = st.columns([2.5, 4.5])
    proc_sel = c_t1.selectbox("🎯 Selecciona el Proceso a Analizar:", ["Programa Despacho"] + PROCESOS_LINEA, index=0, key="sel_proc_uni")

    with st.expander("👷 Dotación Semanal de Operarios por Proceso (Editable) & Horas Hábiles", expanded=True):
        df_dot_v = st.session_state.matriz_dotacion_semanal[["Proceso"] + semanas_cols].copy() if proc_sel == "Programa Despacho" else st.session_state.matriz_dotacion_semanal[st.session_state.matriz_dotacion_semanal["Proceso"] == proc_sel][["Proceso"] + semanas_cols].copy()
        ed_dot = st.data_editor(
            df_dot_v,
            column_config={"Proceso": st.column_config.TextColumn("Proceso", disabled=True), **{s: st.column_config.NumberColumn(s, min_value=0, max_value=100, step=1) for s in semanas_cols}},
            hide_index=True, use_container_width=True, key=f"ed_dot_{proc_sel}"
        )
        if not ed_dot.reset_index(drop=True).equals(df_dot_v.reset_index(drop=True)):
            for _, r_e in ed_dot.iterrows():
                for s in semanas_cols:
                    st.session_state.matriz_dotacion_semanal.loc[st.session_state.matriz_dotacion_semanal["Proceso"] == r_e["Proceso"], s] = r_e[s]
            st.rerun()

        dot_vista_sem = {s: float(pd.to_numeric(ed_dot[s], errors='coerce').sum()) for s in semanas_cols}
        components.html(render_tabla_horas_dotacion_html(semanas_cols, dot_vista_sem, hrs_sem_info), height=155, scrolling=False)

    cap_vista = {s: cap_din_sem["Embalaje"].get(s, 1000.0) for s in semanas_cols} if proc_sel == "Programa Despacho" else {s: cap_din_sem[proc_sel].get(s, 1000.0) for s in semanas_cols}
    mapa_kg = {"Ingeniería": "Kg por cargar (Ing)", "CNCL": "Kg por procesar CNCL", "DoblezL": "Kg por procesar DoblezL", "Soldadura": "Kg por procesar Soldadura", "Fabricado": "Kg por procesar Fabricado", "Galvanizado": "Kg por galvanizar", "Embalaje": "Kg por Embalar"}

    lotes_t2 = []
    for _, lt in df_mostrar.iterrows():
        id_f, f_dt = lt["Identificador de fila"], lt["Fecha_DT"]
        if proc_sel == "Programa Despacho":
            kg_p = limpiar_numero(lt.get("Kilos Programados", 0.0))
            dias_p = int(lt.get("Total_Dias", 0)) or (int(np.ceil(kg_p / cap_dia_kg["Embalaje"])) if kg_p > 0 else 0)
            sem_l = f"{f_dt.isocalendar()[0]}-{f_dt.isocalendar()[1]:02d}"
            s_dict = {s: (kg_p if s == sem_l else 0.0) for s in semanas_cols}
            desv = int(lt.get("Desviacion_Dias", 0))
            fp, f_est_s, tip_a = (desv > 0), lt["Fecha Estimada Finalizar"], f"Desviado +{desv}d"
        else:
            kg_p = limpiar_numero(lt.get(mapa_kg.get(proc_sel, ""), 0.0))
            dias_p = int(np.ceil(kg_p / cap_dia_kg[proc_sel])) if kg_p > 0 else 0
            fl = df_sem_tot[(df_sem_tot["Identificador de fila"] == id_f) & (df_sem_tot["Proceso"] == proc_sel)] if not df_sem_tot.empty else pd.DataFrame()
            s_dict = fl.groupby("Semana")["Kilos"].sum().to_dict() if not fl.empty else {}
            rp = df_g_filt[(df_g_filt["Identificador de fila"] == id_f) & (df_g_filt["Proceso"] == proc_sel)] if not df_g_filt.empty else pd.DataFrame()
            if not rp.empty:
                f_est_s = pd.Timestamp(rp["Fin"].values[0]).strftime("%d/%m/%Y")
                fp = bool(rp["Fuera_Plazo"].values[0])
                tip_a = f"Desviado +{int(rp['Dias_Desv_Proc'].values[0])}d (Límite: {pd.Timestamp(rp['Fecha_Limite'].values[0]).strftime('%d/%m/%Y')})"
            else:
                f_est_s, fp, tip_a = "-", False, ""

        if kg_p > 50.0:
            est_disp = f"{f_est_s} <span style='color:#dc2626;cursor:help' title='{tip_a}'>⚠️</span>" if fp else f_est_s
            lotes_t2.append({
                "Identificador de fila": id_f, "Subproyecto": lt["Subproyecto"], "Fecha_DT": f_dt, "Cant_Procesos": int(lt["Cant_Procesos"]),
                "Ruta_Tooltip": lt["Ruta_Detalle"], "Fecha_Despacho": lt["Fecha Programada"], "Fecha_Est_Display": est_disp,
                "Cant_Dias": dias_p, "Kilos_Procesar": kg_p, **{s: s_dict.get(s, 0.0) for s in semanas_cols}
            })

    df_t2 = pd.DataFrame(lotes_t2)
    if df_t2.empty:
        st.info(f"🎉 No hay lotes pendientes en **{proc_sel}** (Carga ≤ {fmt_u(50.0, factor_u, fmt_u_str)} {corto_u}).")
    else:
        df_t2 = df_t2.sort_values(by=["Fecha_DT", "Cant_Procesos", "Subproyecto"], ascending=[True, False, True])
        components.html(render_tabla_carga_proceso_html(df_t2, semanas_cols, cap_vista, factor_u, fmt_u_str, lbl_u), height=550, scrolling=False)

    st.markdown("---")
    st.download_button("📥 Descargar Plan de Despachos Completo (CSV)", data=df_mostrar.to_csv(index=False).encode('utf-8'), file_name=f"plan_despachos_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")

except Exception as e:
    st.error(f"Error procesando los datos: {e}")
