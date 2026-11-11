import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import smartsheet
import re
import unicodedata
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import numpy as np

st.set_page_config(page_title="Control de Despachos & Carga Semanal", layout="wide")

# -------------------------------------------------------------
# 0. CONFIGURACIÓN DE LOS 7 PROCESOS CLAVE Y DOTACIÓN BASE
# -------------------------------------------------------------
PROCESOS_LINEA = [
    "Ingeniería",
    "CNCL",
    "DoblezL",
    "Soldadura",
    "Fabricado",
    "Galvanizado",
    "Embalaje"
]

PROCESOS_GANTT = [
    "Ingeniería",
    "CNCL",
    "DoblezL",
    "Soldadura",
    "Fabricado",
    "Galvanizado",
    "Embalaje"
]

# Dotación actualizada según estándar de planta (editable)
DOTACION_BASE_DEFAULT = {
    "Ingeniería": 8,
    "CNCL": 12,
    "DoblezL": 3,
    "Soldadura": 12,
    "Fabricado": 1,
    "Galvanizado": 1,
    "Embalaje": 8
}

def limpiar_texto(txt):
    if not isinstance(txt, str): return ""
    txt = unicodedata.normalize('NFKD', txt).encode('ASCII', 'ignore').decode('utf-8')
    return re.sub(r'[^a-zA-Z0-9]', '', txt).lower()

def buscar_columna_kilos(cols, proceso_clave, nombres_prioritarios):
    for target in nombres_prioritarios:
        t_clean = target.lower().replace("_", "").replace(" ", "").strip()
        for c in cols:
            c_clean = c.lower().replace("_", "").replace(" ", "").strip()
            if c_clean == t_clean:
                return c

    candidatos_kilos = []
    for c in cols:
        c_low = c.lower().replace("_", " ")
        if ("kilo" in c_low or "kg" in c_low) and proceso_clave in c_low:
            candidatos_kilos.append(c)
    if candidatos_kilos:
        for cand in candidatos_kilos:
            if "por" in cand.lower():
                return cand
        return candidatos_kilos[0]

    for c in cols:
        c_low = c.lower().replace("_", " ")
        if proceso_clave in c_low and not any(neg in c_low for neg in ["check", "¿", "?", "listo", "ok", "estado", "pasa"]):
            return c

    return None

def limpiar_numero(val):
    if pd.isna(val) or val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return max(0.0, float(val))
    val_str = str(val).strip()
    if not val_str or val_str.lower() in ["none", "nan", "-", "false"]:
        return 0.0
    if val_str.lower() == "true":
        return 0.0
    if "," in val_str and "." in val_str:
        if val_str.rfind(",") > val_str.rfind("."):
            val_str = val_str.replace(".", "").replace(",", ".")
        else:
            val_str = val_str.replace(",", "")
    elif "," in val_str:
        val_str = val_str.replace(",", ".")
    try:
        return max(0.0, float(val_str))
    except Exception:
        return 0.0

# -------------------------------------------------------------
# 1. CONEXIÓN A SMARTSHEET
# -------------------------------------------------------------
@st.cache_data(ttl=300)
def cargar_hoja(sheet_id):
    cliente = smartsheet.Smartsheet(st.secrets["SMARTSHEET_ACCESS_TOKEN"])
    hoja = cliente.Sheets.get_sheet(int(sheet_id))
    columnas = [col.title.strip() for col in hoja.columns]
    filas = [[celda.value for celda in f.cells] for f in hoja.rows]
    return pd.DataFrame(filas, columns=columnas)

# -------------------------------------------------------------
# 2. GENERADORES HTML DE TABLAS
# -------------------------------------------------------------
def renderizar_tabla_html(df_mostrar, totales):
    filas = []
    for idx, r in df_mostrar.iterrows():
        bg = "#ffffff" if idx % 2 == 0 else "#fafafa"
        color_diag = "#b7e1cd" if ("A Tiempo" in r["Diagnostico"] or "Completado" in r["Diagnostico"]) else "#f4c7c3"
        color_ing = "#d62728" if r["Kg por cargar (Ing)"] > 0 else "#555"
        color_final = "#d62728" if r["Desviacion_Dias"] > 0 else "#2ca02c"
        
        filas.append(
            f'<tr style="background-color:{bg}; border-bottom:1px solid #eee;">'
            f'<td style="padding:8px 6px; text-align:left; font-weight:600; position:sticky; left:0; background-color:{bg}; z-index:2; border-right:1px solid #ddd;">{r["Identificador de fila"]}</td>'
            f'<td style="padding:8px 6px; text-align:left; position:sticky; left:130px; background-color:{bg}; z-index:2; border-right:1px solid #ddd;">{r["Subproyecto"]}</td>'
            f'<td style="padding:8px 6px; text-align:right;">{r["Kg Orden Proceder"]:,.0f}</td>'
            f'<td style="padding:8px 6px; text-align:right; font-weight:500; color:{color_ing};">{r["Kg por cargar (Ing)"]:,.0f}</td>'
            f'<td style="padding:8px 6px; text-align:right;">{r["Kg por procesar CNCL"]:,.0f}</td>'
            f'<td style="padding:8px 6px; text-align:right;">{r["Kg por procesar DoblezL"]:,.0f}</td>'
            f'<td style="padding:8px 6px; text-align:right;">{r["Kg por procesar Soldadura"]:,.0f}</td>'
            f'<td style="padding:8px 6px; text-align:right;">{r["Kg por procesar Fabricado"]:,.0f}</td>'
            f'<td style="padding:8px 6px; text-align:right; font-weight:500;">{r["Kg por galvanizar"]:,.0f}</td>'
            f'<td style="padding:8px 6px; text-align:right; font-weight:500;">{r["Kg por Embalar"]:,.0f}</td>'
            f'<td style="padding:8px 6px; text-align:center; font-weight:500;">{r["Fecha Programada"]}</td>'
            f'<td style="padding:8px 6px; text-align:center; font-weight:bold; color:{color_final};">{r["Fecha Estimada Finalizar"]}</td>'
            f'<td style="padding:8px 6px; text-align:center; background-color:{color_diag}; font-weight:600; font-size:11px;">{r["Diagnostico"]}</td>'
            f'</tr>'
        )
    
    tbody = "".join(filas)
    
    tfoot = (
        f'<tr style="background-color:#e8eaed; position:sticky; bottom:0; z-index:5; border-top:2px solid #999; font-weight:bold;">'
        f'<td style="padding:8px 6px; text-align:left; position:sticky; left:0; background-color:#e8eaed; z-index:6;">TOTAL PENDIENTE</td>'
        f'<td style="padding:8px 6px; text-align:left; position:sticky; left:130px; background-color:#e8eaed; z-index:6;">{len(df_mostrar)} lotes</td>'
        f'<td style="padding:8px 6px; text-align:right;">{totales["orden"]:,.0f}</td>'
        f'<td style="padding:8px 6px; text-align:right;">{totales["ing"]:,.0f}</td>'
        f'<td style="padding:8px 6px; text-align:right;">{totales["cncl"]:,.0f}</td>'
        f'<td style="padding:8px 6px; text-align:right;">{totales["doblez"]:,.0f}</td>'
        f'<td style="padding:8px 6px; text-align:right;">{totales["sold"]:,.0f}</td>'
        f'<td style="padding:8px 6px; text-align:right;">{totales["fab"]:,.0f}</td>'
        f'<td style="padding:8px 6px; text-align:right;">{totales["galv"]:,.0f}</td>'
        f'<td style="padding:8px 6px; text-align:right;">{totales["emb"]:,.0f}</td>'
        f'<td style="padding:8px 6px; text-align:center;">-</td>'
        f'<td style="padding:8px 6px; text-align:center;">-</td>'
        f'<td style="padding:8px 6px; text-align:center;">-</td>'
        f'</tr>'
    )
    
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
.tabla-container {{ overflow:auto; max-height:560px; border:1px solid #ccc; border-radius:4px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
thead th {{ background-color:#fce5cd; color:#333; position:sticky; top:0; z-index:5; border-bottom:2px solid #ccc; font-weight:600; text-align:center; }}
</style>
</head>
<body>
<div class="tabla-container">
<table>
<thead>
<tr>
    <th style="padding:9px 6px; text-align:left; position:sticky; left:0; background-color:#fce5cd; z-index:6; min-width:130px;">Identificador de fila</th>
    <th style="padding:9px 6px; text-align:left; position:sticky; left:130px; background-color:#fce5cd; z-index:6; min-width:130px;">Subproyecto</th>
    <th style="padding:9px 6px; text-align:right; min-width:95px;">Kg Orden Proceder</th>
    <th style="padding:9px 6px; text-align:right; min-width:95px;">Kg por cargar (Ing)</th>
    <th style="padding:9px 6px; text-align:right; min-width:90px;">Kg por procesar CNCL</th>
    <th style="padding:9px 6px; text-align:right; min-width:90px;">Kg por procesar DoblezL</th>
    <th style="padding:9px 6px; text-align:right; min-width:95px;">Kg por procesar Soldadura</th>
    <th style="padding:9px 6px; text-align:right; min-width:90px;">Kg por procesar Fabricado</th>
    <th style="padding:9px 6px; text-align:right; min-width:90px;">Kg por galvanizar</th>
    <th style="padding:9px 6px; text-align:right; min-width:90px;">Kg por Embalar</th>
    <th style="padding:9px 6px; text-align:center; min-width:95px;">Fecha Programada</th>
    <th style="padding:9px 6px; text-align:center; min-width:105px;">Fecha Estimada Finalizar</th>
    <th style="padding:9px 6px; text-align:center; min-width:115px;">Desviación</th>
</tr>
</thead>
<tbody>{tbody}</tbody>
<tfoot>{tfoot}</tfoot>
</table>
</div>
</body>
</html>"""
    return html

def render_tabla_carga_proceso_html(df_lotes_proc, semanas_cols, cap_por_semana):
    tot_dias = int(df_lotes_proc["Cant_Dias"].sum()) if not df_lotes_proc.empty else 0
    tot_kg_proc = float(df_lotes_proc["Kilos_Procesar"].sum()) if not df_lotes_proc.empty else 0.0
    tot_por_semana = {
        sem: float(df_lotes_proc[sem].sum()) if (not df_lotes_proc.empty and sem in df_lotes_proc.columns) else 0.0
        for sem in semanas_cols
    }

    # 1. Capacidad Semanal (Fila 1 thead)
    th_caps = "".join([
        f'<th style="top:0px; position:sticky; z-index:7; background:#f8fafc; color:#475569; text-align:right; padding:7px 10px; font-weight:700; font-size:12px; border:1px solid #e2e8f0; min-width:90px;">{cap_por_semana.get(sem, 0):,.0f}</th>'
        for sem in semanas_cols
    ])

    # 2. Totales Semanales Arriba (Fila 2 thead)
    th_totals_semanas = []
    for sem in semanas_cols:
        val_tot = tot_por_semana[sem]
        cap_sem = cap_por_semana.get(sem, 0.0)
        is_over = (val_tot > cap_sem and cap_sem > 0)
        if is_over:
            badge = f'<span style="background:#fee2e2; color:#b91c1c; padding:2px 6px; border-radius:4px; font-weight:700;">{val_tot:,.0f}</span>'
        else:
            badge = f'<span style="color:#1e293b; font-weight:600;">{val_tot:,.0f}</span>' if val_tot > 0 else '<span style="color:#94a3b8;">-</span>'
        
        th_totals_semanas.append(
            f'<th style="top:32px; position:sticky; z-index:7; background:#f1f5f9; padding:6px 10px; text-align:right; border:1px solid #cbd5e1; font-size:12px; min-width:90px;">{badge}</th>'
        )
    th_totals = "".join(th_totals_semanas)

    # 3. Encabezados de Semanas (Fila 3 thead)
    th_semanas = "".join([
        f'<th style="top:66px; position:sticky; z-index:7; padding:9px 10px; text-align:center; border:1px solid #0e3d54; min-width:90px; color:#ffffff; background:#135b79; font-weight:600; font-size:12px;">{sem}</th>'
        for sem in semanas_cols
    ])

    # 4. Filas del Cuerpo con estilo moderno y corte congelado
    filas_html = []
    for idx, r in df_lotes_proc.iterrows():
        id_fila = r["Identificador de fila"]
        subp = r["Subproyecto"]
        dias = int(r["Cant_Dias"])
        kg_p = float(r["Kilos_Procesar"])

        celdas_semanas = []
        for sem in semanas_cols:
            val = float(r.get(sem, 0.0))
            cap_sem = cap_por_semana.get(sem, 0.0)
            if val > 0:
                is_over = (val > cap_sem and cap_sem > 0)
                if is_over:
                    cell_content = f'<span style="background:#fef2f2; color:#dc2626; font-weight:700; padding:2px 6px; border-radius:4px;">{val:,.0f}</span>'
                else:
                    cell_content = f'<span style="color:#1e293b; font-weight:500;">{val:,.0f}</span>'
            else:
                cell_content = '<span style="color:#cbd5e1;">-</span>'
                
            celdas_semanas.append(f'<td style="padding:6px 10px; text-align:right; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{cell_content}</td>')

        bg_col = "#ffffff" if idx % 2 == 0 else "#fafbfc"
        fila_str = (
            f'<tr class="data-row" style="background:{bg_col};">'
            f'<td style="padding:7px 10px; text-align:left; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; font-weight:600; color:#0f172a; position:sticky; left:0px; width:140px; min-width:140px; max-width:140px; background:{bg_col}; z-index:3;">{id_fila}</td>'
            f'<td style="padding:7px 10px; text-align:left; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; color:#334155; position:sticky; left:140px; width:130px; min-width:130px; max-width:130px; background:{bg_col}; z-index:3;">{subp}</td>'
            f'<td style="padding:7px 10px; text-align:right; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; color:#475569; position:sticky; left:270px; width:85px; min-width:85px; max-width:85px; background:{bg_col}; z-index:3;">{dias}</td>'
            f'<td style="padding:7px 10px; text-align:right; border-bottom:1px solid #e2e8f0; border-right:2px solid #94a3b8; font-weight:600; color:#0f172a; position:sticky; left:355px; width:125px; min-width:125px; max-width:125px; background:{bg_col}; z-index:3;">{kg_p:,.0f}</td>'
            f'{"".join(celdas_semanas)}'
            f'</tr>'
        )
        filas_html.append(fila_str)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; background:#ffffff; }}
.tabla-container {{ overflow:auto; max-height:570px; border-radius:8px; border:1px solid #cbd5e1; box-shadow:0 2px 6px rgba(0,0,0,0.04); }}
table {{ width:100%; border-collapse:separate; border-spacing:0; font-size:12px; }}
th, td {{ white-space: nowrap; }}
thead th {{ position: sticky; }}
tr.data-row:hover td {{ background-color:#f1f5f9 !important; }}
</style>
</head>
<body>
<div class="tabla-container">
<table>
<thead>
<!-- Fila 1: Capacidad Semanal -->
<tr>
    <th colspan="4" style="top:0px; left:0px; position:sticky; z-index:10; background:#f8fafc; color:#334155; text-align:right; padding:7px 14px; font-weight:700; font-size:12px; border:1px solid #e2e8f0; border-right:2px solid #94a3b8; width:480px; min-width:480px; max-width:480px;">Capacidad Semanal Kg:</th>
    {th_caps}
</tr>

<!-- Fila 2: Totales de Carga Semanal Arriba -->
<tr>
    <th style="top:32px; left:0px; position:sticky; z-index:10; background:#f1f5f9; color:#0f172a; text-align:left; padding:6px 10px; width:140px; min-width:140px; max-width:140px; border:1px solid #cbd5e1; font-weight:700;">TOTAL PENDIENTE</th>
    <th style="top:32px; left:140px; position:sticky; z-index:10; background:#f1f5f9; color:#475569; text-align:left; padding:6px 10px; width:130px; min-width:130px; max-width:130px; border:1px solid #cbd5e1; font-weight:600;">{len(df_lotes_proc)} lotes</th>
    <th style="top:32px; left:270px; position:sticky; z-index:10; background:#f1f5f9; color:#0f172a; text-align:right; padding:6px 10px; width:85px; min-width:85px; max-width:85px; border:1px solid #cbd5e1; font-weight:700;">{tot_dias}</th>
    <th style="top:32px; left:355px; position:sticky; z-index:10; background:#f1f5f9; color:#0f172a; text-align:right; padding:6px 10px; width:125px; min-width:125px; max-width:125px; border:1px solid #cbd5e1; border-right:2px solid #94a3b8; font-weight:700;">{tot_kg_proc:,.0f}</th>
    {th_totals}
</tr>

<!-- Fila 3: Encabezados de Columna -->
<tr>
    <th style="top:66px; left:0px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:left; padding:9px 10px; width:140px; min-width:140px; max-width:140px; border:1px solid #0e3d54; font-weight:600;">Identificador de fila</th>
    <th style="top:66px; left:140px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:left; padding:9px 10px; width:130px; min-width:130px; max-width:130px; border:1px solid #0e3d54; font-weight:600;">Subproyecto</th>
    <th style="top:66px; left:270px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:center; padding:9px 10px; width:85px; min-width:85px; max-width:85px; border:1px solid #0e3d54; font-weight:600;">Cant. Días</th>
    <th style="top:66px; left:355px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:right; padding:9px 10px; width:125px; min-width:125px; max-width:125px; border:1px solid #0e3d54; border-right:2px solid #94a3b8; font-weight:600;">Kilos por Procesar</th>
    {th_semanas}
</tr>
</thead>
<tbody>
{''.join(filas_html)}
</tbody>
</table>
</div>
</body>
</html>"""
    return html

def renderizar_cuadro_semanal_html(piv_data, dias_dict, semanas_cols, cap_semanal_dict):
    proc_labels = [
        ("Ingeniería", "1. Ingeniería"),
        ("CNCL", "2. CNCL"),
        ("DoblezL", "3. DoblezL"),
        ("Soldadura", "4. Soldadura"),
        ("Fabricado", "5. Fabricado"),
        ("Galvanizado", "6. Galvanizado"),
        ("Embalaje", "7. Embalaje"),
    ]
    
    filas_html = []
    for proc_key, proc_nombre in proc_labels:
        dias = dias_dict.get(proc_key, 0)
        cap_sem = cap_semanal_dict.get(proc_key, 999999)
        
        celdas_semanas = []
        for sem in semanas_cols:
            val = piv_data.get((proc_key, sem), 0.0)
            is_over = (val > cap_sem and val > 0)
            color_txt = "color: #e53935; font-weight: bold;" if is_over else "color: #111;"
            val_str = f"{val:,.0f}" if val > 0 else "0"
            celdas_semanas.append(
                f'<td style="padding:6px 12px; text-align:right; border:1px solid #333; {color_txt}">{val_str}</td>'
            )
            
        fila = (
            f'<tr style="background:#fff;">'
            f'<td style="padding:6px 12px; text-align:left; border:1px solid #333; font-weight:500; position:sticky; left:0; background:#fff; z-index:2; min-width:140px;">{proc_nombre}</td>'
            f'<td style="padding:6px 12px; text-align:right; border:1px solid #333; position:sticky; left:140px; background:#fff; z-index:2; min-width:90px;">{dias}</td>'
            f'{"".join(celdas_semanas)}'
            f'</tr>'
        )
        filas_html.append(fila)
        
    th_semanas = "".join([f'<th style="padding:8px 10px; text-align:center; border:1px solid #333; min-width:85px; color:#fff; background-color:#135b79;">{sem}</th>' for sem in semanas_cols])
    
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
.tabla-container {{ overflow:auto; max-height:450px; border:1px solid #333; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th, td {{ border: 1px solid #333; }}
</style>
</head>
<body>
<div class="tabla-container">
<table>
<thead>
<tr style="background-color:#135b79; color:#fff;">
    <th style="padding:8px 12px; text-align:left; position:sticky; left:0; background-color:#135b79; z-index:5; min-width:140px; border:1px solid #333;">Procesos</th>
    <th style="padding:8px 12px; text-align:center; position:sticky; left:140px; background-color:#135b79; z-index:5; min-width:90px; border:1px solid #333;">Cant. Días</th>
    {th_semanas}
</tr>
</thead>
<tbody>
{''.join(filas_html)}
</tbody>
</table>
</div>
</body>
</html>"""
    return html

def generar_semanas_consecutivas(min_wk_str, max_wk_str, min_count=7):
    try:
        y1, w1 = map(int, str(min_wk_str).split("-"))
        y2, w2 = map(int, str(max_wk_str).split("-"))
        dt1 = pd.Timestamp.fromisocalendar(y1, w1, 4)
        dt2 = pd.Timestamp.fromisocalendar(y2, w2, 4)
    except Exception:
        dt1 = pd.Timestamp("2026-11-26")
        dt2 = pd.Timestamp("2027-01-14")
        
    if dt1 > dt2:
        dt1, dt2 = dt2, dt1
        
    weeks = []
    cur = dt1
    while cur <= dt2:
        iso_y, iso_w, _ = cur.isocalendar()
        weeks.append(f"{iso_y}-{iso_w:02d}")
        cur += pd.Timedelta(weeks=1)
        
    while len(weeks) < min_count:
        iso_y, iso_w, _ = cur.isocalendar()
        weeks.append(f"{iso_y}-{iso_w:02d}")
        cur += pd.Timedelta(weeks=1)
        
    return weeks

# -------------------------------------------------------------
# 3. CONTROLES GENERALES DE PLANTA
# -------------------------------------------------------------
st.sidebar.header("⚙️ Parámetros de Operación")
horas_sem = st.sidebar.number_input("Horas turno / semana por operario", value=44, step=1)
eficiencia = st.sidebar.slider("Eficiencia Operativa / OEE (%)", 50, 100, 85) / 100.0

st.title("📋 Control de Despachos & Carga de Planta")

if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()
    st.rerun()

try:
    with st.spinner("Sincronizando información de planta..."):
        df_backlog_raw = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
        df_prod_raw = cargar_hoja(st.secrets["SHEET_ID_PRODUCTIVIDAD"])

    cols_backlog = df_backlog_raw.columns.tolist()

    col_id_fila = next((c for c in cols_backlog if "identificador" in c.lower() and "fila" in c.lower()), None)
    if not col_id_fila:
        col_id_fila = next((c for c in cols_backlog if "identificador" in c.lower()), None)
    if not col_id_fila:
        col_id_fila = next((c for c in cols_backlog if "id" in c.lower() or "despacho" in c.lower()), cols_backlog[0])

    col_sub_backlog = next((c for c in cols_backlog if "sub-proyecto" in c.lower() or "subproyecto" in c.lower()), None)
    col_proyecto = next((c for c in cols_backlog if c.lower().strip() in ["proyecto", "proyectos", "cod proyecto", "código proyecto"]), None)
    if not col_proyecto:
        col_proyecto = next((c for c in cols_backlog if "proyecto" in c.lower() and "sub" not in c.lower()), None)

    col_nom_proyecto = next((c for c in cols_backlog if any(k in c.lower() for k in ["nombre proyecto", "nombre del proyecto", "nom_proyecto", "nombre de proyecto", "obra", "cliente"])), None)

    col_fecha_prog = next((c for c in cols_backlog if "programad" in c.lower() and "fecha" in c.lower()), None)
    if not col_fecha_prog:
        col_fecha_prog = next((c for c in cols_backlog if "fecha" in c.lower()), None)
    col_despacho_real = next((c for c in cols_backlog if "despacho" in c.lower() and ("real" in c.lower() or "fecha" in c.lower())), None)
    col_kg_prog = next((c for c in cols_backlog if "kilos" in c.lower() and "programad" in c.lower()), None)
    col_orden = next((c for c in cols_backlog if "orden" in c.lower() and "proceder" in c.lower()), None)

    # DETECCIÓN ESTRICTA Y JERÁRQUICA DE COLUMNAS DE KILOS
    col_kg_ing = buscar_columna_kilos(cols_backlog, "cargar", ["Kilos por Cargar_", "Kilos por Cargar"])
    col_kg_cncl = buscar_columna_kilos(cols_backlog, "cncl", ["Kilos por CNCL", "Kilos por CNCL_"])
    col_kg_doblez = buscar_columna_kilos(cols_backlog, "doblez", ["Kilos por DoblezL", "Kilos por Doblez", "Kilos por Doblez L"])
    col_kg_sold = buscar_columna_kilos(cols_backlog, "soldadura", ["Kilos por Soldadura"])
    col_kg_fab = buscar_columna_kilos(cols_backlog, "fabricar", ["Kilos por Fabricar", "Kilos por Fabricado"])
    col_kg_galv = buscar_columna_kilos(cols_backlog, "galvanizar", ["Kilos por Galvanizar", "Kilos por Galvanizado"])
    col_kg_emb = buscar_columna_kilos(cols_backlog, "embalar", ["Kilos por Embalar", "Kilos por Embalaje"])

    # Productividad base de Smartsheet
    col_prod_proc = next((c for c in df_prod_raw.columns if "proceso" in c.lower() and "simulador" in c.lower()), df_prod_raw.columns[0])
    col_prod_kghh = next((c for c in df_prod_raw.columns if "kg" in c.lower() and "hh" in c.lower()), df_prod_raw.columns[1])
    
    ratios_sistema = {}
    for _, r in df_prod_raw.iterrows():
        proc_str = limpiar_texto(str(r[col_prod_proc]))
        kghh_val = pd.to_numeric(r[col_prod_kghh], errors='coerce')
        if pd.notna(kghh_val) and kghh_val > 0:
            for p_oficial in PROCESOS_LINEA:
                if limpiar_texto(p_oficial) in proc_str:
                    ratios_sistema[p_oficial] = float(kghh_val)

    # BARRA LATERAL: PRODUCTIVIDAD SISTEMA VS EDITABLE
    with st.sidebar.expander("⚖️ Productividad por Proceso (Kg/HH)", expanded=False):
        st.caption("Valores leídos de Smartsheet vs. simulados editables:")
        if "df_prod_ratios" not in st.session_state:
            st.session_state.df_prod_ratios = pd.DataFrame([
                {"Proceso": p, "Kg/HH Sistema": ratios_sistema.get(p, 80.0), "Kg/HH Simulado": ratios_sistema.get(p, 80.0)}
                for p in PROCESOS_LINEA
            ])
        else:
            for p in PROCESOS_LINEA:
                st.session_state.df_prod_ratios.loc[st.session_state.df_prod_ratios["Proceso"] == p, "Kg/HH Sistema"] = ratios_sistema.get(p, 80.0)

        edited_prod_df = st.data_editor(
            st.session_state.df_prod_ratios,
            column_config={
                "Proceso": st.column_config.TextColumn("Proceso", disabled=True),
                "Kg/HH Sistema": st.column_config.NumberColumn("Sistema", disabled=True, format="%.1f"),
                "Kg/HH Simulado": st.column_config.NumberColumn("Simulado", min_value=1.0, max_value=2500.0, step=5.0, format="%.1f")
            },
            hide_index=True,
            key="editor_sidebar_ratios"
        )
        st.session_state.df_prod_ratios = edited_prod_df
        ratios_activos = dict(zip(edited_prod_df["Proceso"], edited_prod_df["Kg/HH Simulado"]))

    with st.sidebar.expander("🔍 Columnas de Kilos Vinculadas", expanded=False):
        st.write(f"**Ingeniería:** `{col_kg_ing}`")
        st.write(f"**CNCL:** `{col_kg_cncl}`")
        st.write(f"**DoblezL:** `{col_kg_doblez}`")
        st.write(f"**Soldadura:** `{col_kg_sold}`")
        st.write(f"**Fabricado:** `{col_kg_fab}`")
        st.write(f"**Galvanizado:** `{col_kg_galv}`")
        st.write(f"**Embalaje:** `{col_kg_emb}`")

    valido_fecha_prog = (
        df_backlog_raw[col_fecha_prog].notna() & 
        (df_backlog_raw[col_fecha_prog].astype(str).str.strip() != "") & 
        (~df_backlog_raw[col_fecha_prog].astype(str).str.lower().isin(["none", "nan"]))
    )
    
    if col_despacho_real:
        despacho_en_blanco = (
            df_backlog_raw[col_despacho_real].isna() | 
            (df_backlog_raw[col_despacho_real].astype(str).str.strip() == "") | 
            (df_backlog_raw[col_despacho_real].astype(str).str.lower().isin(["none", "nan"]))
        )
        df_backlog_activo = df_backlog_raw[valido_fecha_prog & despacho_en_blanco].copy()
    else:
        df_backlog_activo = df_backlog_raw[valido_fecha_prog].copy()

    df_backlog_activo["Fecha_DT"] = pd.to_datetime(df_backlog_activo[col_fecha_prog], errors='coerce', dayfirst=True)
    df_backlog_activo = df_backlog_activo.dropna(subset=["Fecha_DT"]).sort_values("Fecha_DT")
    df_backlog_activo["Mes_Entrega"] = df_backlog_activo["Fecha_DT"].dt.strftime("%Y-%m")
    df_backlog_activo["Kg_Prog_Num"] = pd.to_numeric(df_backlog_activo[col_kg_prog], errors='coerce').fillna(0)

    if col_proyecto:
        df_backlog_activo["Proyecto_Codigo"] = df_backlog_activo[col_proyecto].fillna("").astype(str).str.strip()
    else:
        df_backlog_activo["Proyecto_Codigo"] = df_backlog_activo[col_sub_backlog].apply(
            lambda s: "-".join(str(s).split("-")[:2]) if len(str(s).split("-")) >= 2 else str(s)
        )

    if col_nom_proyecto:
        df_backlog_activo["Proyecto_Nombre"] = df_backlog_activo[col_nom_proyecto].fillna("").astype(str).str.strip()
        df_backlog_activo["Proyecto_Display"] = df_backlog_activo["Proyecto_Codigo"] + " - " + df_backlog_activo["Proyecto_Nombre"]
    else:
        df_backlog_activo["Proyecto_Nombre"] = df_backlog_activo["Proyecto_Codigo"]
        df_backlog_activo["Proyecto_Display"] = df_backlog_activo["Proyecto_Codigo"]

    # Filtro de Horizonte Temporal
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Filtro de Horizonte")
    meses_disponibles = sorted(df_backlog_activo["Mes_Entrega"].unique().tolist())
    mes_actual_str = datetime.now().strftime("%Y-%m")
    
    indice_defecto = 0
    if mes_actual_str in meses_disponibles:
        indice_defecto = meses_disponibles.index(mes_actual_str)
    else:
        futuros = [i for i, m in enumerate(meses_disponibles) if m >= mes_actual_str]
        if futuros: indice_defecto = futuros[0]

    mes_seleccionado = st.sidebar.selectbox("Mostrar despachos desde:", options=meses_disponibles, index=indice_defecto)
    df_backlog_horizonte = df_backlog_activo[df_backlog_activo["Mes_Entrega"] >= mes_seleccionado].copy()

    # -------------------------------------------------------------
    # 4. CAPACIDADES DIARIAS Y RUTA CRÍTICA
    # -------------------------------------------------------------
    capacidad_diaria_kg = {}
    capacidad_semanal_kg = {}
    for proc in PROCESOS_LINEA:
        oper = DOTACION_BASE_DEFAULT.get(proc, 4)
        ratio = ratios_activos.get(proc, 80.0)
        hh_dia = (oper * horas_sem / 5.5) * eficiencia
        capacidad_diaria_kg[proc] = max(100.0, hh_dia * ratio)
        capacidad_semanal_kg[proc] = capacidad_diaria_kg[proc] * 5.5

    hoy = pd.Timestamp.now().normalize()
    filas_tabla_despachos = []
    gantt_records_all = []

    for _, lote in df_backlog_horizonte.iterrows():
        id_despacho = str(lote[col_id_fila])
        subp = str(lote[col_sub_backlog])
        f_prog = lote["Fecha_DT"]
        kg_lote = lote["Kg_Prog_Num"]
        proy_display = str(lote["Proyecto_Display"])
        proy_nombre = str(lote["Proyecto_Nombre"])

        kg_por_cargar_ing = limpiar_numero(lote.get(col_kg_ing)) if col_kg_ing else 0.0
        kg_cncl = limpiar_numero(lote.get(col_kg_cncl)) if col_kg_cncl else 0.0
        kg_doblez = limpiar_numero(lote.get(col_kg_doblez)) if col_kg_doblez else 0.0
        kg_soldadura = limpiar_numero(lote.get(col_kg_sold)) if col_kg_sold else 0.0
        kg_fabricado = limpiar_numero(lote.get(col_kg_fab)) if col_kg_fab else 0.0
        kg_galvanizado = limpiar_numero(lote.get(col_kg_galv)) if col_kg_galv else 0.0
        kg_embalaje = limpiar_numero(lote.get(col_kg_emb)) if col_kg_emb else 0.0
        kg_orden_lote = limpiar_numero(lote.get(col_orden)) if col_orden else kg_lote

        d_ing = kg_por_cargar_ing / capacidad_diaria_kg["Ingeniería"]
        d_cncl = kg_cncl / capacidad_diaria_kg["CNCL"]
        d_doblez = kg_doblez / capacidad_diaria_kg["DoblezL"]
        d_sold = kg_soldadura / capacidad_diaria_kg["Soldadura"]
        d_fab = kg_fabricado / capacidad_diaria_kg["Fabricado"]
        d_galv = kg_galvanizado / capacidad_diaria_kg["Galvanizado"]
        d_emb = kg_embalaje / capacidad_diaria_kg["Embalaje"]

        # TRASLAPES Y REGLAS DE FIN
        t_ing_start = 0.0
        t_ing_end = d_ing

        t_cncl_start = t_ing_start + 0.50 * d_ing
        t_cncl_end = t_cncl_start + d_cncl

        t_doblez_start = t_cncl_start + 0.30 * d_cncl
        t_doblez_end = t_doblez_start + d_doblez

        t_sold_start = max(t_doblez_start + 0.25 * d_doblez, t_cncl_start + 0.50 * d_cncl)
        t_sold_end = t_sold_start + d_sold

        t_fab_start = t_sold_start + 0.50 * d_sold
        t_fab_end = t_fab_start + d_fab

        max_ante_fab = max(t_ing_end, t_cncl_end, t_doblez_end, t_sold_end)
        if t_fab_end < max_ante_fab:
            t_fab_end = max_ante_fab
            if d_fab > 0:
                t_fab_start = max(t_fab_start, t_fab_end - d_fab)

        t_galv_start = max(t_fab_start + 0.50 * d_fab, t_sold_end) if d_fab > 0 else t_sold_end
        t_galv_end = t_galv_start + d_galv

        if t_galv_end < t_fab_end:
            t_galv_end = t_fab_end
            if d_galv > 0:
                t_galv_start = max(t_galv_start, t_galv_end - d_galv)

        t_emb_start = max(t_galv_start + 0.30 * d_galv, t_fab_end) if d_galv > 0 else t_galv_end
        t_emb_end = t_emb_start + d_emb

        if t_emb_end < t_galv_end:
            t_emb_end = t_galv_end
            if d_emb > 0:
                t_emb_start = max(t_emb_start, t_emb_end - d_emb)

        dias_habiles_totales = int(np.ceil(max(0.0, t_emb_end)))

        if dias_habiles_totales == 0:
            fecha_inicio_guia = f_prog
            fecha_estimada_final = f_prog
            desviacion_dias = 0
            estado_desv = "🟢 Completado"
            base_start = f_prog
        else:
            fecha_inicio_guia = f_prog - pd.offsets.BDay(dias_habiles_totales)

            if fecha_inicio_guia < hoy:
                fecha_estimada_final = hoy + pd.offsets.BDay(dias_habiles_totales)
                desviacion_dias = (fecha_estimada_final - f_prog).days
                estado_desv = f"🔴 Retraso (+{desviacion_dias}d)" if desviacion_dias > 0 else "🟡 Al Límite"
                base_start = hoy
            else:
                fecha_estimada_final = f_prog
                holgura = (fecha_inicio_guia - hoy).days
                desviacion_dias = 0
                estado_desv = f"🟢 A Tiempo ({holgura}d holgura)"
                base_start = fecha_inicio_guia

        procs_lote = [
            ("Ingeniería", t_ing_start, t_ing_end, kg_por_cargar_ing),
            ("CNCL", t_cncl_start, t_cncl_end, kg_cncl),
            ("DoblezL", t_doblez_start, t_doblez_end, kg_doblez),
            ("Soldadura", t_sold_start, t_sold_end, kg_soldadura),
            ("Fabricado", t_fab_start, t_fab_end, kg_fabricado),
            ("Galvanizado", t_galv_start, t_galv_end, kg_galvanizado),
            ("Embalaje", t_emb_start, t_emb_end, kg_embalaje),
        ]

        for p_nombre, s_off, e_off, kg_val in procs_lote:
            dur_p = max(1, int(np.ceil(e_off - s_off))) if kg_val > 0 else 0
            st_dt = base_start + pd.offsets.BDay(int(np.floor(s_off)))
            en_dt = st_dt + pd.offsets.BDay(dur_p) if dur_p > 0 else st_dt + pd.offsets.BDay(1)
            
            gantt_records_all.append({
                "Identificador de fila": id_despacho,
                "Subproyecto": subp,
                "Proyecto_Display": proy_display,
                "Proyecto_Nombre": proy_nombre,
                "Proceso": p_nombre,
                "Inicio": st_dt,
                "Fin": en_dt,
                "Duracion": dur_p,
                "Kilos": kg_val,
            })

        filas_tabla_despachos.append({
            "Identificador de fila": id_despacho,
            "Subproyecto": subp,
            "Proyecto_Display": proy_display,
            "Proyecto_Nombre": proy_nombre,
            "Kg Orden Proceder": kg_orden_lote,
            "Kg por cargar (Ing)": kg_por_cargar_ing,
            "Kg por procesar CNCL": kg_cncl,
            "Kg por procesar DoblezL": kg_doblez,
            "Kg por procesar Soldadura": kg_soldadura,
            "Kg por procesar Fabricado": kg_fabricado,
            "Kg por galvanizar": kg_galvanizado,
            "Kg por Embalar": kg_embalaje,
            "Fecha Programada": f_prog.strftime("%d/%m/%Y"),
            "Fecha Estimada Finalizar": fecha_estimada_final.strftime("%d/%m/%Y"),
            "Fecha_DT": f_prog,
            "Desviacion_Dias": desviacion_dias,
            "Diagnostico": estado_desv
        })

    df_resultado = pd.DataFrame(filas_tabla_despachos)
    df_gantt_total = pd.DataFrame(gantt_records_all)

    # -------------------------------------------------------------
    # 5. ESTRUCTURA EN PESTAÑAS
    # -------------------------------------------------------------
    tab_despachos, tab_proceso = st.tabs([
        "📋 Control de Despachos & Carga Semanal",
        "⚡ Carga por Proceso & Dotación"
    ])

    # -------------------------------------------------------------
    # TAB 1: CONTROL DE DESPACHOS & CUADRO SEMANAL
    # -------------------------------------------------------------
    with tab_despachos:
        col_f1, col_f2, col_f3, col_f4 = st.columns([2, 2, 2, 1.2])

        with col_f1:
            proyectos_disponibles = sorted([p for p in df_resultado["Proyecto_Display"].unique() if p.strip()])
            filtro_proy = st.multiselect("Filtrar por Nombre / Proyecto:", proyectos_disponibles, placeholder="Todos los proyectos", key="f_proy_t1")

        df_sub_pool = df_resultado if not filtro_proy else df_resultado[df_resultado["Proyecto_Display"].isin(filtro_proy)]
        subproyectos_disponibles = sorted([s for s in df_sub_pool["Subproyecto"].unique() if s.strip()])

        with col_f2:
            filtro_subp = st.multiselect("Filtrar por Subproyecto:", subproyectos_disponibles, placeholder="Todos los subproyectos", key="f_subp_t1")

        df_disp_pool = df_sub_pool if not filtro_subp else df_sub_pool[df_sub_pool["Subproyecto"].isin(filtro_subp)]
        despachos_disponibles = sorted([d for d in df_disp_pool["Identificador de fila"].unique() if d.strip()])

        with col_f3:
            filtro_despacho = st.multiselect("Filtrar por ID Despacho:", despachos_disponibles, placeholder="Todos los despachos", key="f_desp_t1")

        with col_f4:
            st.write("")
            st.write("")
            solo_atrasados = st.checkbox("Solo con retraso", key="f_atr_t1")

        df_mostrar = df_resultado.copy()
        if filtro_proy:
            df_mostrar = df_mostrar[df_mostrar["Proyecto_Display"].isin(filtro_proy)]
        if filtro_subp:
            df_mostrar = df_mostrar[df_mostrar["Subproyecto"].isin(filtro_subp)]
        if filtro_despacho:
            df_mostrar = df_mostrar[df_mostrar["Identificador de fila"].isin(filtro_despacho)]
        if solo_atrasados:
            df_mostrar = df_mostrar[df_mostrar["Desviacion_Dias"] > 0]

        df_mostrar = df_mostrar.sort_values(by="Fecha_DT")

        total_lotes = len(df_mostrar)
        lotes_atrasados = len(df_mostrar[df_mostrar["Desviacion_Dias"] > 0])
        lotes_ok = total_lotes - lotes_atrasados
        max_retraso = df_mostrar["Desviacion_Dias"].max() if not df_mostrar.empty else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Lotes en Vista", total_lotes)
        c2.metric("Lotes en Fecha", lotes_ok)
        c3.metric("Lotes Desviados", lotes_atrasados, delta=f"-{lotes_atrasados}" if lotes_atrasados > 0 else "0", delta_color="inverse")
        c4.metric("Máximo Retraso", f"+{max_retraso} días" if max_retraso > 0 else "0 días", delta_color="inverse")

        st.markdown("---")

        totales_dict = {
            "orden": df_mostrar["Kg Orden Proceder"].sum(),
            "ing": df_mostrar["Kg por cargar (Ing)"].sum(),
            "cncl": df_mostrar["Kg por procesar CNCL"].sum(),
            "doblez": df_mostrar["Kg por procesar DoblezL"].sum(),
            "sold": df_mostrar["Kg por procesar Soldadura"].sum(),
            "fab": df_mostrar["Kg por procesar Fabricado"].sum(),
            "galv": df_mostrar["Kg por galvanizar"].sum(),
            "emb": df_mostrar["Kg por Embalar"].sum(),
        }

        tabla_html = renderizar_tabla_html(df_mostrar, totales_dict)
        components.html(tabla_html, height=580, scrolling=False)

        st.markdown("---")
        st.markdown("### 📊 Cuadro de Carga Semanal por Proceso")
        st.caption("Distribución de kilos por semana para los 7 procesos clave. Los números en rojo indican semanas que superan la capacidad semanal disponible.")

        lotes_visibles = df_mostrar["Identificador de fila"].unique()
        df_gantt_filtrado = df_gantt_total[df_gantt_total["Identificador de fila"].isin(lotes_visibles)].copy()

        def calcular_kilos_semanales(df_g):
            rows_dist = []
            for _, r in df_g.iterrows():
                start_dt, end_dt, kg = r["Inicio"], r["Fin"], r["Kilos"]
                if kg <= 0: continue
                cur = start_dt
                days_by_week = {}
                total_days = 0
                while cur <= end_dt:
                    if cur.weekday() < 5:
                        iso = f"{cur.isocalendar()[0]}-{cur.isocalendar()[1]:02d}"
                        days_by_week[iso] = days_by_week.get(iso, 0) + 1
                        total_days += 1
                    cur += pd.Timedelta(days=1)
                if total_days == 0:
                    total_days = 1
                    iso = f"{start_dt.isocalendar()[0]}-{start_dt.isocalendar()[1]:02d}"
                    days_by_week[iso] = 1
                for wk, cnt in days_by_week.items():
                    rows_dist.append({
                        "Semana": wk,
                        "Proceso": r["Proceso"],
                        "Identificador de fila": r["Identificador de fila"],
                        "Subproyecto": r["Subproyecto"],
                        "Kilos": (cnt / total_days) * kg
                    })
            return pd.DataFrame(rows_dist)

        df_semanal_total = calcular_kilos_semanales(df_gantt_filtrado)

        if not df_semanal_total.empty:
            piv_dict = df_semanal_total.groupby(["Proceso", "Semana"])["Kilos"].sum().to_dict()
            min_semana = df_semanal_total["Semana"].min()
            max_semana = df_semanal_total["Semana"].max()
        else:
            piv_dict = {}
            min_semana = "2026-48"
            max_semana = "2027-02"

        if "2026-48" < min_semana:
            min_semana = "2026-48"
        if "2027-02" > max_semana:
            max_semana = "2027-02"

        semanas_cols = generar_semanas_consecutivas(min_semana, max_semana, min_count=7)

        dias_calculados = {}
        for proc in PROCESOS_LINEA:
            kg_tot_proc = df_gantt_filtrado[df_gantt_filtrado["Proceso"] == proc]["Kilos"].sum() if not df_gantt_filtrado.empty else 0.0
            if len(lotes_visibles) == 1 and not df_gantt_filtrado.empty:
                sub_p = df_gantt_filtrado[df_gantt_filtrado["Proceso"] == proc]
                dias_calculados[proc] = int(sub_p["Duracion"].values[0]) if not sub_p.empty else 0
            else:
                cap_d = capacidad_diaria_kg.get(proc, 1000.0)
                dias_calculados[proc] = int(np.ceil(kg_tot_proc / cap_d)) if cap_d > 0 and kg_tot_proc > 0 else 0

        html_cuadro = renderizar_cuadro_semanal_html(
            piv_data=piv_dict,
            dias_dict=dias_calculados,
            semanas_cols=semanas_cols,
            cap_semanal_dict=capacidad_semanal_kg
        )
        components.html(html_cuadro, height=360, scrolling=False)

        st.download_button(
            label="📥 Descargar Plan de Despachos (CSV)",
            data=df_resultado.to_csv(index=False).encode('utf-8'),
            file_name=f"plan_despachos_estimados_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key="dl_tab1"
        )

    # -------------------------------------------------------------
    # TAB 2: CARGA POR PROCESO & DOTACIÓN SEMANAL
    # -------------------------------------------------------------
    with tab_proceso:
        st.markdown("### ⚡ Carga Semanal por Despacho & Capacidad de Planta")
        st.caption("Monitorea la carga lote por lote comparada con la capacidad semanal calculada según la dotación asignada.")

        if "matriz_dotacion_semanal" not in st.session_state:
            datos_matriz = {"Proceso": PROCESOS_LINEA}
            for sem in semanas_cols:
                datos_matriz[sem] = [DOTACION_BASE_DEFAULT.get(p, 4) for p in PROCESOS_LINEA]
            st.session_state.matriz_dotacion_semanal = pd.DataFrame(datos_matriz)
        else:
            for sem in semanas_cols:
                if sem not in st.session_state.matriz_dotacion_semanal.columns:
                    st.session_state.matriz_dotacion_semanal[sem] = [DOTACION_BASE_DEFAULT.get(p, 4) for p in PROCESOS_LINEA]

        c_top1, c_top2 = st.columns([2, 3])
        with c_top1:
            proceso_seleccionado = st.selectbox(
                "🎯 Selecciona el Proceso a Analizar:",
                ["Consolidado (Todos los Procesos)"] + PROCESOS_LINEA,
                index=2 # Default CNCL
            )

        with st.expander("👷 Dotación Semanal de Operarios por Proceso (Editable)", expanded=False):
            st.caption("Ajusta la cantidad de operarios por semana para actualizar la 'Capacidad Semanal Kg' que se muestra en la cabecera:")
            matriz_dot_edit = st.data_editor(
                st.session_state.matriz_dotacion_semanal[["Proceso"] + semanas_cols],
                column_config={"Proceso": st.column_config.TextColumn(disabled=True)},
                hide_index=True,
                use_container_width=True,
                key="editor_dotacion_tab2"
            )
            st.session_state.matriz_dotacion_semanal.update(matriz_dot_edit)

        capacidad_dinamica_semanal = {p: {} for p in PROCESOS_LINEA}
        for p in PROCESOS_LINEA:
            ratio_p = ratios_activos.get(p, 80.0)
            fila_oper = st.session_state.matriz_dotacion_semanal[st.session_state.matriz_dotacion_semanal["Proceso"] == p]
            for sem in semanas_cols:
                oper_val = float(fila_oper[sem].values[0]) if not fila_oper.empty and sem in fila_oper.columns else DOTACION_BASE_DEFAULT.get(p, 4)
                hh_sem_disp = oper_val * horas_sem * eficiencia
                capacidad_dinamica_semanal[p][sem] = hh_sem_disp * ratio_p

        cap_semanal_vista = {}
        if proceso_seleccionado == "Consolidado (Todos los Procesos)":
            for sem in semanas_cols:
                cap_semanal_vista[sem] = sum(capacidad_dinamica_semanal[p][sem] for p in PROCESOS_LINEA)
        else:
            for sem in semanas_cols:
                cap_semanal_vista[sem] = capacidad_dinamica_semanal[proceso_seleccionado].get(sem, 1000.0)

        df_lotes_tab2_list = []
        df_gantt_vista = df_gantt_total if df_mostrar.empty else df_gantt_total[df_gantt_total["Identificador de fila"].isin(df_mostrar["Identificador de fila"])]

        df_semanal_desglose = calcular_kilos_semanales(df_gantt_vista)

        for _, lote in df_mostrar.iterrows():
            id_f = lote["Identificador de fila"]
            subp = lote["Subproyecto"]

            if proceso_seleccionado == "Consolidado (Todos los Procesos)":
                kg_proc = sum([
                    lote.get("Kg por cargar (Ing)", 0),
                    lote.get("Kg por procesar CNCL", 0),
                    lote.get("Kg por procesar DoblezL", 0),
                    lote.get("Kg por procesar Soldadura", 0),
                    lote.get("Kg por procesar Fabricado", 0),
                    lote.get("Kg por galvanizar", 0),
                    lote.get("Kg por Embalar", 0),
                ])
                cap_dia_cons = sum([capacidad_diaria_kg.get(p, 1000.0) for p in PROCESOS_LINEA])
                dias_p = int(np.ceil(kg_proc / cap_dia_cons)) if cap_dia_cons > 0 and kg_proc > 0 else 0
                df_filtro_lote = df_semanal_desglose[df_semanal_desglose["Identificador de fila"] == id_f]
            else:
                mapa_col_kg = {
                    "Ingeniería": "Kg por cargar (Ing)",
                    "CNCL": "Kg por procesar CNCL",
                    "DoblezL": "Kg por procesar DoblezL",
                    "Soldadura": "Kg por procesar Soldadura",
                    "Fabricado": "Kg por procesar Fabricado",
                    "Galvanizado": "Kg por galvanizar",
                    "Embalaje": "Kg por Embalar"
                }
                kg_proc = lote.get(mapa_col_kg.get(proceso_seleccionado, ""), 0.0)
                cap_dia = capacidad_diaria_kg.get(proceso_seleccionado, 1000.0)
                dias_p = int(np.ceil(kg_proc / cap_dia)) if cap_dia > 0 and kg_proc > 0 else 0
                df_filtro_lote = df_semanal_desglose[
                    (df_semanal_desglose["Identificador de fila"] == id_f) & 
                    (df_semanal_desglose["Proceso"] == proceso_seleccionado)
                ]

            # REGLA 1: FILTRAR PARA MOSTRAR EXCLUSIVAMENTE FILAS CON KILOS POR PROCESAR > 50 KG
            if kg_proc > 50.0:
                semanas_dict = df_filtro_lote.groupby("Semana")["Kilos"].sum().to_dict() if not df_filtro_lote.empty else {}
                fila_reg = {
                    "Identificador de fila": id_f,
                    "Subproyecto": subp,
                    "Cant_Dias": dias_p,
                    "Kilos_Procesar": kg_proc,
                }
                for sem in semanas_cols:
                    fila_reg[sem] = semanas_dict.get(sem, 0.0)

                df_lotes_tab2_list.append(fila_reg)

        df_lotes_tab2 = pd.DataFrame(df_lotes_tab2_list)

        if df_lotes_tab2.empty:
            st.info(f"🎉 No hay lotes pendientes con trabajo por procesar en **{proceso_seleccionado}** (Kilos por procesar ≤ 50 kg).")
        else:
            html_tab2 = render_tabla_carga_proceso_html(
                df_lotes_proc=df_lotes_tab2,
                semanas_cols=semanas_cols,
                cap_por_semana=cap_semanal_vista
            )
            components.html(html_tab2, height=600, scrolling=False)

except Exception as e:
    st.error(f"Error procesando los datos: {e}")
