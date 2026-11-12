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

DOTACION_BASE_DEFAULT = {
    "Ingeniería": 8,
    "CNCL": 12,
    "DoblezL": 3,
    "Soldadura": 12,
    "Fabricado": 1,
    "Galvanizado": 1,
    "Embalaje": 8
}

RATIO_FALLBACK_DEFAULT = {
    "Ingeniería": 120.0,
    "CNCL": 120.0,
    "DoblezL": 100.0,
    "Soldadura": 60.0,
    "Fabricado": 1500.0,
    "Galvanizado": 2000.0,
    "Embalaje": 350.0
}

DOTACION_VERSION = "v6_ui_unidades_fechas_edd"

# -------------------------------------------------------------
# 1. CONEXIÓN A SMARTSHEET (DEFINICIÓN GLOBAL)
# -------------------------------------------------------------
@st.cache_data(ttl=300)
def cargar_hoja(sheet_id):
    cliente = smartsheet.Smartsheet(st.secrets["SMARTSHEET_ACCESS_TOKEN"])
    hoja = cliente.Sheets.get_sheet(int(sheet_id))
    columnas = [col.title.strip() for col in hoja.columns]
    filas = [[celda.value for celda in f.cells] for f in hoja.rows]
    return pd.DataFrame(filas, columns=columnas)

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
        if ("kilo" in c_low or "kg" in c_low) and proceso_clave in c_low and "fecha" not in c_low:
            candidatos_kilos.append(c)
    if candidatos_kilos:
        for cand in candidatos_kilos:
            if "por" in cand.lower():
                return cand
        return candidatos_kilos[0]

    for c in cols:
        c_low = c.lower().replace("_", " ")
        if proceso_clave in c_low and not any(neg in c_low for neg in ["check", "¿", "?", "listo", "ok", "estado", "pasa", "fecha"]):
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

def formatear_valor_unidad(val_kg, factor_unidad, fmt_str):
    val_conv = float(val_kg) / factor_unidad
    return f"{val_conv:{fmt_str}}"

# -------------------------------------------------------------
# 2. GENERADORES HTML DE TABLAS (DISEÑO EJECUTIVO MODERNO)
# -------------------------------------------------------------
def renderizar_tabla_html(df_mostrar, totales, factor_unidad=1.0, fmt_str=",.0f", unidad_label="Kg"):
    filas = []
    for idx, r in df_mostrar.iterrows():
        bg = "#ffffff" if idx % 2 == 0 else "#fafbfc"
        diag_txt = str(r["Diagnostico"])
        if "Retraso" in diag_txt:
            badge_diag = f'<span style="background:#fee2e2; color:#991b1b; padding:3px 8px; border-radius:12px; font-weight:700; font-size:11px;">{diag_txt}</span>'
        elif "Al Límite" in diag_txt:
            badge_diag = f'<span style="background:#fef3c7; color:#92400e; padding:3px 8px; border-radius:12px; font-weight:700; font-size:11px;">{diag_txt}</span>'
        else:
            badge_diag = f'<span style="background:#dcfce7; color:#166534; padding:3px 8px; border-radius:12px; font-weight:700; font-size:11px;">{diag_txt}</span>'

        color_ing = "#dc2626" if r["Kg por cargar (Ing)"] > 0 else "#475569"
        color_final = "#dc2626" if r["Desviacion_Dias"] > 0 else "#15803d"
        ruta_info = r.get("Ruta_Detalle", "")

        v_orden = formatear_valor_unidad(r["Kg Orden Proceder"], factor_unidad, fmt_str)
        v_ing = formatear_valor_unidad(r["Kg por cargar (Ing)"], factor_unidad, fmt_str)
        v_cncl = formatear_valor_unidad(r["Kg por procesar CNCL"], factor_unidad, fmt_str)
        v_doblez = formatear_valor_unidad(r["Kg por procesar DoblezL"], factor_unidad, fmt_str)
        v_sold = formatear_valor_unidad(r["Kg por procesar Soldadura"], factor_unidad, fmt_str)
        v_fab = formatear_valor_unidad(r["Kg por procesar Fabricado"], factor_unidad, fmt_str)
        v_galv = formatear_valor_unidad(r["Kg por galvanizar"], factor_unidad, fmt_str)
        v_emb = formatear_valor_unidad(r["Kg por Embalar"], factor_unidad, fmt_str)

        filas.append(
            f'<tr class="data-row" style="background-color:{bg};">'
            f'<td title="{ruta_info}" style="padding:7px 8px; text-align:left; font-weight:600; color:#0f172a; position:sticky; left:0px; width:125px; min-width:125px; max-width:125px; background-color:{bg}; z-index:3; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{r["Identificador de fila"]}</td>'
            f'<td title="{ruta_info}" style="padding:7px 8px; text-align:left; color:#334155; position:sticky; left:125px; width:115px; min-width:115px; max-width:115px; background-color:{bg}; z-index:3; border-bottom:1px solid #e2e8f0; border-right:2px solid #94a3b8;">{r["Subproyecto"]}</td>'
            f'<td style="padding:7px 8px; text-align:right; color:#1e293b; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{v_orden}</td>'
            f'<td style="padding:7px 8px; text-align:right; font-weight:600; color:{color_ing}; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{v_ing}</td>'
            f'<td style="padding:7px 8px; text-align:right; color:#1e293b; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{v_cncl}</td>'
            f'<td style="padding:7px 8px; text-align:right; color:#1e293b; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{v_doblez}</td>'
            f'<td style="padding:7px 8px; text-align:right; color:#1e293b; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{v_sold}</td>'
            f'<td style="padding:7px 8px; text-align:right; color:#1e293b; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{v_fab}</td>'
            f'<td style="padding:7px 8px; text-align:right; font-weight:500; color:#1e293b; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{v_galv}</td>'
            f'<td style="padding:7px 8px; text-align:right; font-weight:500; color:#1e293b; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{v_emb}</td>'
            f'<td style="padding:7px 8px; text-align:center; font-weight:500; color:#334155; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{r["Fecha Programada"]}</td>'
            f'<td style="padding:7px 8px; text-align:center; font-weight:700; color:{color_final}; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0;">{r["Fecha Estimada Finalizar"]}</td>'
            f'<td style="padding:7px 8px; text-align:center; border-bottom:1px solid #e2e8f0;">{badge_diag}</td>'
            f'</tr>'
        )
    
    tbody = "".join(filas)

    t_orden = formatear_valor_unidad(totales["orden"], factor_unidad, fmt_str)
    t_ing = formatear_valor_unidad(totales["ing"], factor_unidad, fmt_str)
    t_cncl = formatear_valor_unidad(totales["cncl"], factor_unidad, fmt_str)
    t_doblez = formatear_valor_unidad(totales["doblez"], factor_unidad, fmt_str)
    t_sold = formatear_valor_unidad(totales["sold"], factor_unidad, fmt_str)
    t_fab = formatear_valor_unidad(totales["fab"], factor_unidad, fmt_str)
    t_galv = formatear_valor_unidad(totales["galv"], factor_unidad, fmt_str)
    t_emb = formatear_valor_unidad(totales["emb"], factor_unidad, fmt_str)
    
    tfoot = (
        f'<tr style="background-color:#f1f5f9; font-weight:700; color:#0f172a;">'
        f'<td style="padding:9px 8px; text-align:left; position:sticky; left:0px; bottom:0px; width:125px; min-width:125px; max-width:125px; background-color:#f1f5f9; z-index:8; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">TOTAL PENDIENTE</td>'
        f'<td style="padding:9px 8px; text-align:left; position:sticky; left:125px; bottom:0px; width:115px; min-width:115px; max-width:115px; background-color:#f1f5f9; z-index:8; border-top:2px solid #64748b; border-right:2px solid #94a3b8; color:#475569;">{len(df_mostrar)} lotes</td>'
        f'<td style="padding:9px 8px; text-align:right; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">{t_orden}</td>'
        f'<td style="padding:9px 8px; text-align:right; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">{t_ing}</td>'
        f'<td style="padding:9px 8px; text-align:right; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">{t_cncl}</td>'
        f'<td style="padding:9px 8px; text-align:right; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">{t_doblez}</td>'
        f'<td style="padding:9px 8px; text-align:right; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">{t_sold}</td>'
        f'<td style="padding:9px 8px; text-align:right; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">{t_fab}</td>'
        f'<td style="padding:9px 8px; text-align:right; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">{t_galv}</td>'
        f'<td style="padding:9px 8px; text-align:right; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">{t_emb}</td>'
        f'<td style="padding:9px 8px; text-align:center; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">-</td>'
        f'<td style="padding:9px 8px; text-align:center; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b; border-right:1px solid #cbd5e1;">-</td>'
        f'<td style="padding:9px 8px; text-align:center; position:sticky; bottom:0px; background-color:#f1f5f9; z-index:6; border-top:2px solid #64748b;">-</td>'
        f'</tr>'
    )
    
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; background:#ffffff; }}
.tabla-container {{ overflow:auto; max-height:560px; border-radius:8px; border:1px solid #cbd5e1; box-shadow:0 2px 6px rgba(0,0,0,0.04); }}
table {{ width:100%; border-collapse:separate; border-spacing:0; font-size:12px; }}
th, td {{ white-space: nowrap; }}
thead th {{ background-color:#135b79; color:#ffffff; position:sticky; top:0px; z-index:6; border-bottom:2px solid #0e3d54; border-right:1px solid #1e6f91; font-weight:600; text-align:center; line-height:1.2; }}
tr.data-row:hover td {{ background-color:#f1f5f9 !important; }}
</style>
</head>
<body>
<div class="tabla-container">
<table>
<thead>
<tr>
    <th style="padding:8px 8px; text-align:left; position:sticky; left:0px; top:0px; background-color:#135b79; z-index:10; width:125px; min-width:125px; max-width:125px;">Identificador<br>de fila</th>
    <th style="padding:8px 8px; text-align:left; position:sticky; left:125px; top:0px; background-color:#135b79; z-index:10; width:115px; min-width:115px; max-width:115px; border-right:2px solid #94a3b8;">Subproyecto</th>
    <th style="padding:8px 8px; text-align:right; min-width:95px;">{unidad_label} Orden<br>Proceder</th>
    <th style="padding:8px 8px; text-align:right; min-width:95px;">{unidad_label} por cargar<br>(Ingeniería)</th>
    <th style="padding:8px 8px; text-align:right; min-width:90px;">{unidad_label} por<br>CNCL</th>
    <th style="padding:8px 8px; text-align:right; min-width:90px;">{unidad_label} por<br>DoblezL</th>
    <th style="padding:8px 8px; text-align:right; min-width:95px;">{unidad_label} por<br>Soldadura</th>
    <th style="padding:8px 8px; text-align:right; min-width:90px;">{unidad_label} por<br>Fabricado</th>
    <th style="padding:8px 8px; text-align:right; min-width:90px;">{unidad_label} por<br>Galvanizar</th>
    <th style="padding:8px 8px; text-align:right; min-width:90px;">{unidad_label} por<br>Embalar</th>
    <th style="padding:8px 8px; text-align:center; min-width:95px;">Fecha<br>Programada</th>
    <th style="padding:8px 8px; text-align:center; min-width:105px;">Fecha Est.<br>Finalizar</th>
    <th style="padding:8px 8px; text-align:center; min-width:125px;">Estado /<br>Desviación</th>
</tr>
</thead>
<tbody>{tbody}</tbody>
<tfoot>{tfoot}</tfoot>
</table>
</div>
</body>
</html>"""
    return html

def renderizar_cuadro_semanal_html(piv_data, info_procesos_dict, semanas_cols, cap_semanal_dict, factor_unidad=1.0, fmt_str=",.0f", unidad_label="Kg"):
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
    for idx, (proc_key, proc_nombre) in enumerate(proc_labels):
        p_info = info_procesos_dict.get(proc_key, {})
        dias = int(p_info.get("dias", 0))
        f_ini_str = p_info.get("inicio_est", "-")
        f_fin_str = p_info.get("termino_est", "-")
        total_kg_p = float(p_info.get("total_kg", 0.0))
        cap_sem = cap_semanal_dict.get(proc_key, 999999999.0)
        
        celdas_semanas = []
        for sem in semanas_cols:
            val = float(piv_data.get((proc_key, sem), 0.0))
            if val > 0:
                is_over = (val > cap_sem and cap_sem > 0)
                val_str = formatear_valor_unidad(val, factor_unidad, fmt_str)
                if is_over:
                    cell_content = f'<span style="background:#fef2f2; color:#dc2626; font-weight:700; padding:2px 6px; border-radius:4px;">{val_str}</span>'
                else:
                    cell_content = f'<span style="color:#1e293b; font-weight:500;">{val_str}</span>'
            else:
                cell_content = '<span style="color:#cbd5e1;">-</span>'
            celdas_semanas.append(
                f'<td style="padding:7px 10px; text-align:right; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; font-size:12px;">{cell_content}</td>'
            )

        tot_p_str = formatear_valor_unidad(total_kg_p, factor_unidad, fmt_str) if total_kg_p > 0 else "-"
        bg_col = "#ffffff" if idx % 2 == 0 else "#fafbfc"

        fila = (
            f'<tr class="data-row" style="background:{bg_col};">'
            f'<td style="padding:7px 10px; text-align:left; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; font-weight:600; color:#0f172a; position:sticky; left:0px; width:130px; min-width:130px; max-width:130px; background:{bg_col}; z-index:3;">{proc_nombre}</td>'
            f'<td style="padding:7px 8px; text-align:center; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; color:#334155; position:sticky; left:130px; width:95px; min-width:95px; max-width:95px; background:{bg_col}; z-index:3;">{f_ini_str}</td>'
            f'<td style="padding:7px 8px; text-align:center; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; color:#334155; position:sticky; left:225px; width:95px; min-width:95px; max-width:95px; background:{bg_col}; z-index:3;">{f_fin_str}</td>'
            f'<td style="padding:7px 8px; text-align:right; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; color:#475569; position:sticky; left:320px; width:75px; min-width:75px; max-width:75px; background:{bg_col}; z-index:3;">{dias}</td>'
            f'<td style="padding:7px 10px; text-align:right; border-bottom:1px solid #e2e8f0; border-right:2px solid #94a3b8; font-weight:700; color:#0f172a; position:sticky; left:395px; width:115px; min-width:115px; max-width:115px; background:{bg_col}; z-index:3;">{tot_p_str}</td>'
            f'{"".join(celdas_semanas)}'
            f'</tr>'
        )
        filas_html.append(fila)
        
    th_semanas = "".join([
        f'<th style="padding:8px 10px; text-align:center; border-bottom:2px solid #0e3d54; border-right:1px solid #1e6f91; min-width:88px; color:#ffffff; background-color:#135b79; font-weight:600; font-size:12px;">{sem}</th>'
        for sem in semanas_cols
    ])
    
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; background:#ffffff; }}
.tabla-container {{ overflow:auto; max-height:420px; border-radius:8px; border:1px solid #cbd5e1; box-shadow:0 2px 6px rgba(0,0,0,0.04); }}
table {{ width:100%; border-collapse:separate; border-spacing:0; font-size:12px; }}
th, td {{ white-space: nowrap; }}
thead th {{ position: sticky; top:0px; z-index:6; }}
tr.data-row:hover td {{ background-color:#f1f5f9 !important; }}
</style>
</head>
<body>
<div class="tabla-container">
<table>
<thead>
<tr style="background-color:#135b79; color:#ffffff;">
    <th style="padding:8px 10px; text-align:left; position:sticky; left:0px; top:0px; background-color:#135b79; z-index:10; width:130px; min-width:130px; max-width:130px; border-bottom:2px solid #0e3d54; border-right:1px solid #1e6f91; line-height:1.2;">Procesos</th>
    <th style="padding:8px 8px; text-align:center; position:sticky; left:130px; top:0px; background-color:#135b79; z-index:10; width:95px; min-width:95px; max-width:95px; border-bottom:2px solid #0e3d54; border-right:1px solid #1e6f91; line-height:1.2;">Inicio<br>Estimado</th>
    <th style="padding:8px 8px; text-align:center; position:sticky; left:225px; top:0px; background-color:#135b79; z-index:10; width:95px; min-width:95px; max-width:95px; border-bottom:2px solid #0e3d54; border-right:1px solid #1e6f91; line-height:1.2;">Término<br>Estimado</th>
    <th style="padding:8px 8px; text-align:center; position:sticky; left:320px; top:0px; background-color:#135b79; z-index:10; width:75px; min-width:75px; max-width:75px; border-bottom:2px solid #0e3d54; border-right:1px solid #1e6f91; line-height:1.2;">Cant.<br>Días</th>
    <th style="padding:8px 10px; text-align:right; position:sticky; left:395px; top:0px; background-color:#135b79; z-index:10; width:115px; min-width:115px; max-width:115px; border-bottom:2px solid #0e3d54; border-right:2px solid #94a3b8; line-height:1.2;">Total por<br>Procesar ({unidad_label})</th>
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

def render_tabla_carga_proceso_html(df_lotes_proc, semanas_cols, cap_por_semana, factor_unidad=1.0, fmt_str=",.0f", unidad_label="Kg"):
    tot_dias = int(df_lotes_proc["Cant_Dias"].sum()) if not df_lotes_proc.empty else 0
    tot_kg_proc = float(df_lotes_proc["Kilos_Procesar"].sum()) if not df_lotes_proc.empty else 0.0
    tot_por_semana = {
        sem: float(df_lotes_proc[sem].sum()) if (not df_lotes_proc.empty and sem in df_lotes_proc.columns) else 0.0
        for sem in semanas_cols
    }

    # 1. Capacidad Semanal (Fila 1 thead)
    th_caps = "".join([
        f'<th style="top:0px; position:sticky; z-index:7; background:#f8fafc; color:#475569; text-align:right; padding:6px 8px; font-weight:700; font-size:11px; border:1px solid #e2e8f0; min-width:80px;">{formatear_valor_unidad(cap_por_semana.get(sem, 0), factor_unidad, fmt_str)}</th>'
        for sem in semanas_cols
    ])

    # 2. Totales Semanales Arriba (Fila 2 thead)
    th_totals_semanas = []
    for sem in semanas_cols:
        val_tot = tot_por_semana[sem]
        cap_sem = cap_por_semana.get(sem, 0.0)
        is_over = (val_tot > cap_sem and cap_sem > 0)
        val_str = formatear_valor_unidad(val_tot, factor_unidad, fmt_str)
        if is_over:
            badge = f'<span style="background:#fee2e2; color:#b91c1c; padding:2px 5px; border-radius:3px; font-weight:700;">{val_str}</span>'
        else:
            badge = f'<span style="color:#1e293b; font-weight:600;">{val_str}</span>' if val_tot > 0 else '<span style="color:#94a3b8;">-</span>'
        
        th_totals_semanas.append(
            f'<th style="top:30px; position:sticky; z-index:7; background:#f1f5f9; padding:5px 8px; text-align:right; border:1px solid #cbd5e1; font-size:11px; min-width:80px;">{badge}</th>'
        )
    th_totals = "".join(th_totals_semanas)

    # 3. Encabezados de Semanas (Fila 3 thead)
    th_semanas = "".join([
        f'<th style="top:60px; position:sticky; z-index:7; padding:7px 8px; text-align:center; border:1px solid #0e3d54; min-width:80px; color:#ffffff; background:#135b79; font-weight:600; font-size:11px;">{sem}</th>'
        for sem in semanas_cols
    ])

    # 4. Filas del Cuerpo con 6 columnas compactas fijas
    filas_html = []
    for idx, r in df_lotes_proc.iterrows():
        id_fila = r["Identificador de fila"]
        subp = r["Subproyecto"]
        f_desp = r.get("Fecha_Despacho", "-")
        f_est_disp = r.get("Fecha_Est_Display", "-")
        dias = int(r["Cant_Dias"])
        kg_p = float(r["Kilos_Procesar"])
        ruta_tooltip = r.get("Ruta_Tooltip", "")

        celdas_semanas = []
        for sem in semanas_cols:
            val = float(r.get(sem, 0.0))
            cap_sem = cap_por_semana.get(sem, 0.0)
            if val > 0:
                is_over = (val > cap_sem and cap_sem > 0)
                val_str = formatear_valor_unidad(val, factor_unidad, fmt_str)
                if is_over:
                    cell_content = f'<span style="background:#fef2f2; color:#dc2626; font-weight:700; padding:1px 5px; border-radius:3px;">{val_str}</span>'
                else:
                    cell_content = f'<span style="color:#1e293b; font-weight:500;">{val_str}</span>'
            else:
                cell_content = '<span style="color:#cbd5e1;">-</span>'
                
            celdas_semanas.append(f'<td style="padding:5px 8px; text-align:right; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; font-size:11px;">{cell_content}</td>')

        kg_p_str = formatear_valor_unidad(kg_p, factor_unidad, fmt_str)
        bg_col = "#ffffff" if idx % 2 == 0 else "#fafbfc"
        fila_str = (
            f'<tr class="data-row" style="background:{bg_col};">'
            f'<td title="{ruta_tooltip}" style="padding:5px 6px; text-align:left; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; font-weight:600; color:#0f172a; position:sticky; left:0px; width:105px; min-width:105px; max-width:105px; background:{bg_col}; z-index:3; font-size:11px;">{id_fila}</td>'
            f'<td title="{ruta_tooltip}" style="padding:5px 6px; text-align:left; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; color:#334155; position:sticky; left:105px; width:95px; min-width:95px; max-width:95px; background:{bg_col}; z-index:3; font-size:11px;">{subp}</td>'
            f'<td style="padding:5px 4px; text-align:center; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; color:#334155; position:sticky; left:200px; width:80px; min-width:80px; max-width:80px; background:{bg_col}; z-index:3; font-size:11px;">{f_desp}</td>'
            f'<td style="padding:5px 4px; text-align:center; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; color:#0f172a; position:sticky; left:280px; width:90px; min-width:90px; max-width:90px; background:{bg_col}; z-index:3; font-size:11px; font-weight:500;">{f_est_disp}</td>'
            f'<td style="padding:5px 4px; text-align:right; border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; color:#475569; position:sticky; left:370px; width:50px; min-width:50px; max-width:50px; background:{bg_col}; z-index:3; font-size:11px;">{dias}</td>'
            f'<td style="padding:5px 6px; text-align:right; border-bottom:1px solid #e2e8f0; border-right:2px solid #94a3b8; font-weight:600; color:#0f172a; position:sticky; left:420px; width:85px; min-width:85px; max-width:85px; background:{bg_col}; z-index:3; font-size:11px;">{kg_p_str}</td>'
            f'{"".join(celdas_semanas)}'
            f'</tr>'
        )
        filas_html.append(fila_str)

    tot_kg_str = formatear_valor_unidad(tot_kg_proc, factor_unidad, fmt_str)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; background:#ffffff; }}
.tabla-container {{ overflow:auto; max-height:570px; border-radius:6px; border:1px solid #cbd5e1; box-shadow:0 2px 5px rgba(0,0,0,0.03); }}
table {{ width:100%; border-collapse:separate; border-spacing:0; font-size:11px; }}
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
    <th colspan="6" style="top:0px; left:0px; position:sticky; z-index:10; background:#f8fafc; color:#334155; text-align:right; padding:6px 10px; font-weight:700; font-size:11px; border:1px solid #e2e8f0; border-right:2px solid #94a3b8; width:505px; min-width:505px; max-width:505px;">Capacidad Semanal ({unidad_label}):</th>
    {th_caps}
</tr>

<!-- Fila 2: Totales de Carga Semanal Arriba -->
<tr>
    <th style="top:30px; left:0px; position:sticky; z-index:10; background:#f1f5f9; color:#0f172a; text-align:left; padding:5px 6px; width:105px; min-width:105px; max-width:105px; border:1px solid #cbd5e1; font-weight:700; font-size:11px;">TOTAL PENDIENTE</th>
    <th style="top:30px; left:105px; position:sticky; z-index:10; background:#f1f5f9; color:#475569; text-align:left; padding:5px 6px; width:95px; min-width:95px; max-width:95px; border:1px solid #cbd5e1; font-weight:600; font-size:11px;">{len(df_lotes_proc)} lotes</th>
    <th style="top:30px; left:200px; position:sticky; z-index:10; background:#f1f5f9; color:#475569; text-align:center; padding:5px 4px; width:80px; min-width:80px; max-width:80px; border:1px solid #cbd5e1; font-weight:600; font-size:11px;">-</th>
    <th style="top:30px; left:280px; position:sticky; z-index:10; background:#f1f5f9; color:#475569; text-align:center; padding:5px 4px; width:90px; min-width:90px; max-width:90px; border:1px solid #cbd5e1; font-weight:600; font-size:11px;">-</th>
    <th style="top:30px; left:370px; position:sticky; z-index:10; background:#f1f5f9; color:#0f172a; text-align:right; padding:5px 4px; width:50px; min-width:50px; max-width:50px; border:1px solid #cbd5e1; font-weight:700; font-size:11px;">{tot_dias}</th>
    <th style="top:30px; left:420px; position:sticky; z-index:10; background:#f1f5f9; color:#0f172a; text-align:right; padding:5px 6px; width:85px; min-width:85px; max-width:85px; border:1px solid #cbd5e1; border-right:2px solid #94a3b8; font-weight:700; font-size:11px;">{tot_kg_str}</th>
    {th_totals}
</tr>

<!-- Fila 3: Encabezados de Columna Compactos -->
<tr>
    <th style="top:60px; left:0px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:left; padding:6px 6px; width:105px; min-width:105px; max-width:105px; border:1px solid #0e3d54; font-weight:600; font-size:11px; line-height:1.15;">Identificador<br>de fila</th>
    <th style="top:60px; left:105px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:left; padding:6px 6px; width:95px; min-width:95px; max-width:95px; border:1px solid #0e3d54; font-weight:600; font-size:11px; line-height:1.15;">Subproyecto</th>
    <th style="top:60px; left:200px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:center; padding:6px 4px; width:80px; min-width:80px; max-width:80px; border:1px solid #0e3d54; font-weight:600; font-size:11px; line-height:1.15;">Fecha<br>Despacho</th>
    <th style="top:60px; left:280px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:center; padding:6px 4px; width:90px; min-width:90px; max-width:90px; border:1px solid #0e3d54; font-weight:600; font-size:11px; line-height:1.15;">Fecha Est.<br>Entrega</th>
    <th style="top:60px; left:370px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:center; padding:6px 4px; width:50px; min-width:50px; max-width:50px; border:1px solid #0e3d54; font-weight:600; font-size:11px; line-height:1.15;">Cant.<br>Días</th>
    <th style="top:60px; left:420px; position:sticky; z-index:10; background-color:#135b79; color:#ffffff; text-align:right; padding:6px 6px; width:85px; min-width:85px; max-width:85px; border:1px solid #0e3d54; border-right:2px solid #94a3b8; font-weight:600; font-size:11px; line-height:1.15;">{unidad_label} por<br>Procesar</th>
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
unidad_medida = st.sidebar.radio(
    "📏 Unidad de Visualización:",
    ["Kilos (kg)", "Toneladas (t)"],
    index=0,
    horizontal=True
)
es_toneladas = (unidad_medida == "Toneladas (t)")
factor_unidad = 1000.0 if es_toneladas else 1.0
fmt_unidad = ",.2f" if es_toneladas else ",.0f"
label_unidad = "Ton" if es_toneladas else "Kg"
corto_unidad = "t" if es_toneladas else "kg"

horas_sem = st.sidebar.number_input("Horas turno / semana por operario", value=44, step=1)
eficiencia = st.sidebar.slider("Eficiencia Operativa / OEE (%)", 50, 100, 85) / 100.0

st.title("📋 Control de Despachos & Carga de Planta")

if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()
    st.session_state.pop("dotacion_version_key", None)
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
    col_orden = next((c for c in cols_backlog if "orden" in c.lower() and "proceder" in c.lower()), None)

    # DETECCIÓN DE FECHA EMBALAJE EN PROGRAMA DESPACHO
    col_fecha_embalaje = next((c for c in cols_backlog if "fecha" in c.lower() and "embalaje" in c.lower()), None)
    if not col_fecha_embalaje:
        col_fecha_embalaje = next((c for c in cols_backlog if "embalaje" in c.lower() and ("f." in c.lower() or "real" in c.lower() or "termino" in c.lower())), None)

    # DETECCIÓN DE COLUMNAS DE KILOS DEL PROGRAMA DESPACHO
    col_kg_prog = buscar_columna_kilos(cols_backlog, "programad", [
        "Kilos Programados", "Kilos Programado", "Kilos programados", "Kg Programados", "Kg. Programados", "Kilos Programados Despacho"
    ])
    if not col_kg_prog:
        col_kg_prog = next((c for c in cols_backlog if "programad" in c.lower() and ("kilo" in c.lower() or "kg" in c.lower()) and "fecha" not in c.lower()), None)
    if not col_kg_prog:
        col_kg_prog = next((c for c in cols_backlog if "despach" in c.lower() and ("kilo" in c.lower() or "kg" in c.lower()) and "fecha" not in c.lower()), None)

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
                {
                    "Proceso": p,
                    "Kg/HH Sistema": ratios_sistema.get(p, RATIO_FALLBACK_DEFAULT.get(p, 100.0)),
                    "Kg/HH Simulado": ratios_sistema.get(p, RATIO_FALLBACK_DEFAULT.get(p, 100.0))
                }
                for p in PROCESOS_LINEA
            ])
        else:
            for p in PROCESOS_LINEA:
                st.session_state.df_prod_ratios.loc[st.session_state.df_prod_ratios["Proceso"] == p, "Kg/HH Sistema"] = ratios_sistema.get(p, RATIO_FALLBACK_DEFAULT.get(p, 100.0))

        edited_prod_df = st.data_editor(
            st.session_state.df_prod_ratios,
            column_config={
                "Proceso": st.column_config.TextColumn("Proceso", disabled=True),
                "Kg/HH Sistema": st.column_config.NumberColumn("Sistema", disabled=True, format="%.1f"),
                "Kg/HH Simulado": st.column_config.NumberColumn("Simulado", min_value=1.0, max_value=10000.0, step=5.0, format="%.1f")
            },
            hide_index=True,
            key="editor_sidebar_ratios"
        )
        st.session_state.df_prod_ratios = edited_prod_df
        ratios_activos = dict(zip(edited_prod_df["Proceso"], edited_prod_df["Kg/HH Simulado"]))

    with st.sidebar.expander("🔍 Columnas Vinculadas", expanded=False):
        st.write(f"**Kilos Programados:** `{col_kg_prog}`")
        st.write(f"**Fecha Embalaje:** `{col_fecha_embalaje}`")
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
    df_backlog_activo["Kg_Prog_Num"] = df_backlog_activo[col_kg_prog].apply(limpiar_numero) if col_kg_prog else 0.0
    
    if col_fecha_embalaje:
        df_backlog_activo["Fecha_Embalaje_DT"] = pd.to_datetime(df_backlog_activo[col_fecha_embalaje], errors='coerce', dayfirst=True)
    else:
        df_backlog_activo["Fecha_Embalaje_DT"] = pd.NaT

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

    # -------------------------------------------------------------
    # BARRA LATERAL: FILTRO DE HORIZONTE + SEMANAS ISO (NO EDITABLE)
    # -------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Filtro de Horizonte")
    meses_disponibles = sorted(df_backlog_activo["Mes_Entrega"].unique().tolist())
    hoy_dt = datetime.now()
    mes_actual_str = hoy_dt.strftime("%Y-%m")
    semana_actual_iso = f"{hoy_dt.isocalendar()[0]}-{hoy_dt.isocalendar()[1]:02d}"
    
    indice_defecto = 0
    if mes_actual_str in meses_disponibles:
        indice_defecto = meses_disponibles.index(mes_actual_str)
    else:
        futuros = [i for i, m in enumerate(meses_disponibles) if m >= mes_actual_str]
        if futuros: indice_defecto = futuros[0]

    mes_seleccionado = st.sidebar.selectbox("Mostrar despachos desde:", options=meses_disponibles, index=indice_defecto)
    df_backlog_horizonte = df_backlog_activo[df_backlog_activo["Mes_Entrega"] >= mes_seleccionado].copy()

    # Semana ISO de inicio del filtro aplicado (solo lectura)
    try:
        dt_inicio_filtro = pd.to_datetime(f"{mes_seleccionado}-01")
        semana_inicio_filtro_iso = f"{dt_inicio_filtro.isocalendar()[0]}-{dt_inicio_filtro.isocalendar()[1]:02d}"
    except Exception:
        semana_inicio_filtro_iso = semana_actual_iso

    st.sidebar.info(
        f"📍 **Semana Actual:** `{semana_actual_iso}`\n\n"
        f"▶️ **Inicio Filtro ({mes_seleccionado}):** `{semana_inicio_filtro_iso}`"
    )

    # -------------------------------------------------------------
    # 4. CAPACIDADES DIARIAS (CON REFLEJO DINÁMICO DE DOTACIÓN EDITADA)
    # -------------------------------------------------------------
    dotacion_efectiva = {}
    for proc in PROCESOS_LINEA:
        if (
            "matriz_dotacion_semanal" in st.session_state
            and st.session_state.get("dotacion_version_key") == DOTACION_VERSION
        ):
            df_mat = st.session_state.matriz_dotacion_semanal
            fila_p = df_mat[df_mat["Proceso"] == proc]
            cols_sem_mat = [c for c in df_mat.columns if c != "Proceso"]
            if not fila_p.empty and cols_sem_mat:
                val_prom = pd.to_numeric(fila_p[cols_sem_mat].iloc[0], errors='coerce').mean()
                dotacion_efectiva[proc] = float(val_prom) if pd.notna(val_prom) and val_prom > 0 else float(DOTACION_BASE_DEFAULT.get(proc, 4))
            else:
                dotacion_efectiva[proc] = float(DOTACION_BASE_DEFAULT.get(proc, 4))
        else:
            dotacion_efectiva[proc] = float(DOTACION_BASE_DEFAULT.get(proc, 4))

    capacidad_diaria_kg = {}
    capacidad_semanal_kg = {}
    for proc in PROCESOS_LINEA:
        oper = dotacion_efectiva[proc]
        ratio = ratios_activos.get(proc, RATIO_FALLBACK_DEFAULT.get(proc, 100.0))
        hh_dia = (oper * horas_sem / 5.5) * eficiencia
        capacidad_diaria_kg[proc] = max(100.0, hh_dia * ratio)
        capacidad_semanal_kg[proc] = capacidad_diaria_kg[proc] * 5.5

    # -------------------------------------------------------------
    # 5. SECUENCIACIÓN EDD (EARLIEST DUE DATE + PRIORIDAD POR RUTA)
    # -------------------------------------------------------------
    lotes_preparados = []
    for _, lote in df_backlog_horizonte.iterrows():
        kg_ing = limpiar_numero(lote.get(col_kg_ing)) if col_kg_ing else 0.0
        kg_cncl = limpiar_numero(lote.get(col_kg_cncl)) if col_kg_cncl else 0.0
        kg_doblez = limpiar_numero(lote.get(col_kg_doblez)) if col_kg_doblez else 0.0
        kg_sold = limpiar_numero(lote.get(col_kg_sold)) if col_kg_sold else 0.0
        kg_fab = limpiar_numero(lote.get(col_kg_fab)) if col_kg_fab else 0.0
        kg_galv = limpiar_numero(lote.get(col_kg_galv)) if col_kg_galv else 0.0
        kg_emb = limpiar_numero(lote.get(col_kg_emb)) if col_kg_emb else 0.0
        kg_lote = float(lote["Kg_Prog_Num"])
        kg_orden_lote = limpiar_numero(lote.get(col_orden)) if col_orden else kg_lote

        procesos_activos_lote = [
            p_nom for p_nom, val in [
                ("Ingeniería", kg_ing),
                ("CNCL", kg_cncl),
                ("DoblezL", kg_doblez),
                ("Soldadura", kg_sold),
                ("Fabricado", kg_fab),
                ("Galvanizado", kg_galv),
                ("Embalaje", kg_emb),
            ] if val > 0.0
        ]
        cant_procesos = len(procesos_activos_lote)
        ruta_str = " → ".join(procesos_activos_lote) if procesos_activos_lote else "Completado / Embalado"

        lotes_preparados.append({
            "id_despacho": str(lote[col_id_fila]),
            "subp": str(lote[col_sub_backlog]),
            "f_prog": lote["Fecha_DT"],
            "f_emb_dt": lote["Fecha_Embalaje_DT"],
            "proy_display": str(lote["Proyecto_Display"]),
            "proy_nombre": str(lote["Proyecto_Nombre"]),
            "kg_lote": kg_lote,
            "kg_orden": kg_orden_lote,
            "kg_ing": kg_ing,
            "kg_cncl": kg_cncl,
            "kg_doblez": kg_doblez,
            "kg_sold": kg_sold,
            "kg_fab": kg_fab,
            "kg_galv": kg_galv,
            "kg_emb": kg_emb,
            "cant_procesos": cant_procesos,
            "ruta_str": ruta_str,
        })

    lotes_preparados.sort(key=lambda x: (x["f_prog"], -x["cant_procesos"], x["subp"]))

    hoy = pd.Timestamp.now().normalize()
    machine_cursor = {p: 0.0 for p in PROCESOS_LINEA}

    filas_tabla_despachos = []
    gantt_records_all = []

    for idx_edd, item in enumerate(lotes_preparados, start=1):
        id_despacho = item["id_despacho"]
        subp = item["subp"]
        f_prog = item["f_prog"]
        f_emb_dt = item["f_emb_dt"]
        proy_display = item["proy_display"]
        proy_nombre = item["proy_nombre"]
        kg_lote = item["kg_lote"]
        kg_orden_lote = item["kg_orden"]

        kg_por_cargar_ing = item["kg_ing"]
        kg_cncl = item["kg_cncl"]
        kg_doblez = item["kg_doblez"]
        kg_soldadura = item["kg_sold"]
        kg_fabricado = item["kg_fab"]
        kg_galvanizado = item["kg_galv"]
        kg_embalaje = item["kg_emb"]
        cant_procesos = item["cant_procesos"]
        ruta_tooltip = f"Secuencia EDD #{idx_edd} | Procesos activos ({cant_procesos}): {item['ruta_str']}"

        embalado_finalizado = (kg_embalaje < 10.0 and pd.notna(f_emb_dt))

        if embalado_finalizado:
            fecha_estimada_final = f_emb_dt
            desviacion_dias = max(0, (fecha_estimada_final - f_prog).days)
            if desviacion_dias > 0:
                estado_desv = f"🔴 Retraso (+{desviacion_dias}d)"
            else:
                estado_desv = "🟢 Embalado (En Fecha)"
            dias_habiles_totales = 0

            for p_nombre, kg_val in [
                ("Ingeniería", kg_por_cargar_ing),
                ("CNCL", kg_cncl),
                ("DoblezL", kg_doblez),
                ("Soldadura", kg_soldadura),
                ("Fabricado", kg_fabricado),
                ("Galvanizado", kg_galvanizado),
                ("Embalaje", kg_embalaje),
            ]:
                gantt_records_all.append({
                    "Identificador de fila": id_despacho,
                    "Subproyecto": subp,
                    "Proyecto_Display": proy_display,
                    "Proyecto_Nombre": proy_nombre,
                    "Proceso": p_nombre,
                    "Inicio": f_emb_dt,
                    "Fin": f_emb_dt,
                    "Fecha_Limite": f_prog,
                    "Duracion": 0,
                    "Kilos": kg_val,
                    "Fuera_Plazo": (f_emb_dt > f_prog),
                    "Dias_Desv_Proc": max(0, (f_emb_dt - f_prog).days),
                    "Fecha_Fin_DT": f_emb_dt,
                    "Ruta_Tooltip": ruta_tooltip
                })
        else:
            d_ing = kg_por_cargar_ing / capacidad_diaria_kg["Ingeniería"]
            d_cncl = kg_cncl / capacidad_diaria_kg["CNCL"]
            d_doblez = kg_doblez / capacidad_diaria_kg["DoblezL"]
            d_sold = kg_soldadura / capacidad_diaria_kg["Soldadura"]
            d_fab = kg_fabricado / capacidad_diaria_kg["Fabricado"]
            d_galv = kg_galvanizado / capacidad_diaria_kg["Galvanizado"]
            d_emb = kg_embalaje / capacidad_diaria_kg["Embalaje"]

            t_ing_s = 0.0
            t_ing_e = d_ing

            t_cncl_s = 0.50 * d_ing
            t_cncl_e = t_cncl_s + d_cncl

            t_dob_s = t_cncl_s + 0.30 * d_cncl
            t_dob_e = t_dob_s + d_doblez

            if d_doblez > 0:
                t_sol_s = max(t_dob_s + 0.25 * d_doblez, t_cncl_s + 0.50 * d_cncl)
            else:
                t_sol_s = t_cncl_s + 0.30 * d_cncl
            t_sol_e = t_sol_s + d_sold

            if d_sold > 0:
                t_fab_s = t_sol_s + 0.50 * d_sold
            elif d_doblez > 0:
                t_fab_s = t_dob_s + 0.50 * d_doblez
            else:
                t_fab_s = t_cncl_s + 0.50 * d_cncl
            t_fab_e = max(t_fab_s + d_fab, t_ing_e, t_cncl_e, t_dob_e, t_sol_e)

            t_gal_s = max(t_fab_s + 0.50 * d_fab, t_sol_e) if d_fab > 0 else t_sol_e
            t_gal_e = max(t_gal_s + d_galv, t_fab_e)

            t_emb_s = max(t_gal_s + 0.30 * d_galv, t_fab_e) if d_galv > 0 else t_gal_e
            t_emb_e = max(t_emb_s + d_emb, t_gal_e)

            e_rel = {
                "Ingeniería": t_ing_e,
                "CNCL": t_cncl_e,
                "DoblezL": t_dob_e,
                "Soldadura": t_sol_e,
                "Fabricado": t_fab_e,
                "Galvanizado": t_gal_e,
                "Embalaje": t_emb_e,
            }

            bdays_to_due = int(np.busday_count(hoy.date(), f_prog.date()))
            ideal_base = max(0.0, float(bdays_to_due) - t_emb_e)

            s_act, e_act = {}, {}

            s_act["Ingeniería"] = max(ideal_base, machine_cursor["Ingeniería"]) if d_ing > 0 else ideal_base
            e_act["Ingeniería"] = s_act["Ingeniería"] + d_ing
            if d_ing > 0:
                machine_cursor["Ingeniería"] = e_act["Ingeniería"]

            req_cncl = s_act["Ingeniería"] + 0.50 * d_ing
            s_act["CNCL"] = max(req_cncl, machine_cursor["CNCL"]) if d_cncl > 0 else req_cncl
            e_act["CNCL"] = s_act["CNCL"] + d_cncl
            if d_cncl > 0:
                machine_cursor["CNCL"] = e_act["CNCL"]

            req_dob = s_act["CNCL"] + 0.30 * d_cncl
            s_act["DoblezL"] = max(req_dob, machine_cursor["DoblezL"]) if d_doblez > 0 else req_dob
            e_act["DoblezL"] = s_act["DoblezL"] + d_doblez
            if d_doblez > 0:
                machine_cursor["DoblezL"] = e_act["DoblezL"]

            if d_doblez > 0:
                req_sol = max(s_act["DoblezL"] + 0.25 * d_doblez, s_act["CNCL"] + 0.50 * d_cncl)
            else:
                req_sol = s_act["CNCL"] + 0.30 * d_cncl
            s_act["Soldadura"] = max(req_sol, machine_cursor["Soldadura"]) if d_sold > 0 else req_sol
            e_act["Soldadura"] = s_act["Soldadura"] + d_sold
            if d_sold > 0:
                machine_cursor["Soldadura"] = e_act["Soldadura"]

            if d_sold > 0:
                req_fab = s_act["Soldadura"] + 0.50 * d_sold
            elif d_doblez > 0:
                req_fab = s_act["DoblezL"] + 0.50 * d_doblez
            else:
                req_fab = s_act["CNCL"] + 0.50 * d_cncl
            s_act["Fabricado"] = max(req_fab, machine_cursor["Fabricado"]) if d_fab > 0 else req_fab
            e_act["Fabricado"] = max(
                s_act["Fabricado"] + d_fab,
                e_act["Ingeniería"], e_act["CNCL"], e_act["DoblezL"], e_act["Soldadura"]
            )
            if d_fab > 0:
                s_act["Fabricado"] = max(s_act["Fabricado"], e_act["Fabricado"] - d_fab)
                machine_cursor["Fabricado"] = e_act["Fabricado"]

            req_gal = max(s_act["Fabricado"] + 0.50 * d_fab, e_act["Soldadura"]) if d_fab > 0 else e_act["Soldadura"]
            s_act["Galvanizado"] = max(req_gal, machine_cursor["Galvanizado"]) if d_galv > 0 else req_gal
            e_act["Galvanizado"] = max(s_act["Galvanizado"] + d_galv, e_act["Fabricado"])
            if d_galv > 0:
                s_act["Galvanizado"] = max(s_act["Galvanizado"], e_act["Galvanizado"] - d_galv)
                machine_cursor["Galvanizado"] = e_act["Galvanizado"]

            req_emb = max(s_act["Galvanizado"] + 0.30 * d_galv, e_act["Fabricado"]) if d_galv > 0 else e_act["Fabricado"]
            s_act["Embalaje"] = max(req_emb, machine_cursor["Embalaje"]) if d_emb > 0 else req_emb
            e_act["Embalaje"] = max(s_act["Embalaje"] + d_emb, e_act["Galvanizado"])
            if d_emb > 0:
                s_act["Embalaje"] = max(s_act["Embalaje"], e_act["Embalaje"] - d_emb)
                machine_cursor["Embalaje"] = e_act["Embalaje"]

            dias_habiles_totales = int(np.ceil(max(0.0, e_act["Embalaje"])))

            if dias_habiles_totales == 0:
                fecha_estimada_final = f_prog
                desviacion_dias = 0
                estado_desv = "🟢 Completado"
            else:
                fecha_estimada_final = hoy + pd.offsets.BDay(dias_habiles_totales)
                if fecha_estimada_final <= f_prog:
                    holgura = (f_prog - fecha_estimada_final).days
                    fecha_estimada_final = f_prog
                    desviacion_dias = 0
                    estado_desv = f"🟢 A Tiempo ({holgura}d holgura)"
                else:
                    desviacion_dias = (fecha_estimada_final - f_prog).days
                    estado_desv = f"🔴 Retraso (+{desviacion_dias}d)"

            for p_nombre, kg_val in [
                ("Ingeniería", kg_por_cargar_ing),
                ("CNCL", kg_cncl),
                ("DoblezL", kg_doblez),
                ("Soldadura", kg_soldadura),
                ("Fabricado", kg_fabricado),
                ("Galvanizado", kg_galvanizado),
                ("Embalaje", kg_embalaje),
            ]:
                s_off = s_act[p_nombre]
                e_off = e_act[p_nombre]
                dur_p = max(1, int(np.ceil(e_off - s_off))) if kg_val > 0 else 0
                st_dt = hoy + pd.offsets.BDay(int(np.floor(s_off)))
                en_dt = hoy + pd.offsets.BDay(max(1, int(np.ceil(e_off)))) if kg_val > 0 else st_dt

                rem_bdays = max(0, int(np.ceil(t_emb_e - e_rel[p_nombre])))
                f_limite_p = f_prog - pd.offsets.BDay(rem_bdays)

                if kg_val > 0 and en_dt > f_limite_p:
                    fuera_plazo_p = True
                    dias_desv_p = max(1, (en_dt - f_limite_p).days)
                else:
                    fuera_plazo_p = False
                    dias_desv_p = 0

                gantt_records_all.append({
                    "Identificador de fila": id_despacho,
                    "Subproyecto": subp,
                    "Proyecto_Display": proy_display,
                    "Proyecto_Nombre": proy_nombre,
                    "Proceso": p_nombre,
                    "Inicio": st_dt,
                    "Fin": en_dt,
                    "Fecha_Limite": f_limite_p,
                    "Duracion": dur_p,
                    "Kilos": kg_val,
                    "Fuera_Plazo": fuera_plazo_p,
                    "Dias_Desv_Proc": dias_desv_p,
                    "Fecha_Fin_DT": en_dt,
                    "Ruta_Tooltip": ruta_tooltip
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
            "Kilos Programados": kg_lote,
            "Kg_Prog_Num": kg_lote,
            "Cant_Procesos": cant_procesos,
            "Ruta_Detalle": ruta_tooltip,
            "Fecha Programada": f_prog.strftime("%d/%m/%Y"),
            "Fecha Estimada Finalizar": fecha_estimada_final.strftime("%d/%m/%Y"),
            "Fecha_DT": f_prog,
            "Fecha_Fin_DT": fecha_estimada_final,
            "Total_Dias": dias_habiles_totales,
            "Desviacion_Dias": desviacion_dias,
            "Diagnostico": estado_desv
        })

    df_resultado = pd.DataFrame(filas_tabla_despachos)
    df_gantt_total = pd.DataFrame(gantt_records_all)

    # -------------------------------------------------------------
    # 6. ESTRUCTURA EN PESTAÑAS
    # -------------------------------------------------------------
    tab_despachos, tab_proceso = st.tabs([
        "📋 Control de Despachos & Carga Semanal",
        "⚡ Carga por Proceso & Dotación"
    ])

    # -------------------------------------------------------------
    # TAB 1: CONTROL DE DESPACHOS & CUADRO SEMANAL
    # -------------------------------------------------------------
    with tab_despachos:
        # Fila 1 de Filtros: Proyecto -> Subproyecto -> ID Despacho -> Solo con retraso
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

        # Fila 2 de Filtros: Filtro Jerárquico de Fecha Programada (Año -> Mes -> Fecha)
        df_fecha_pool_t1 = df_disp_pool if not filtro_despacho else df_disp_pool[df_disp_pool["Identificador de fila"].isin(filtro_despacho)]
        col_d1, col_d2, col_d3, col_d4 = st.columns([1.5, 1.8, 2.2, 1.7])

        with col_d1:
            anios_t1 = sorted(df_fecha_pool_t1["Fecha_DT"].dt.year.dropna().unique().astype(int).tolist())
            filtro_anio_t1 = st.multiselect("📅 Año (F. Programada):", anios_t1, placeholder="Todos los años", key="f_anio_t1")

        pool_anio_t1 = df_fecha_pool_t1 if not filtro_anio_t1 else df_fecha_pool_t1[df_fecha_pool_t1["Fecha_DT"].dt.year.isin(filtro_anio_t1)]
        with col_d2:
            meses_t1 = sorted(pool_anio_t1["Fecha_DT"].dt.strftime("%Y-%m").dropna().unique().tolist())
            filtro_mes_t1 = st.multiselect("📅 Mes (F. Programada):", meses_t1, placeholder="Todos los meses", key="f_mes_t1")

        pool_mes_t1 = pool_anio_t1 if not filtro_mes_t1 else pool_anio_t1[pool_anio_t1["Fecha_DT"].dt.strftime("%Y-%m").isin(filtro_mes_t1)]
        with col_d3:
            fechas_dt_t1 = sorted(pool_mes_t1["Fecha_DT"].dropna().unique())
            fechas_str_t1 = [pd.Timestamp(d).strftime("%d/%m/%Y") for d in fechas_dt_t1]
            filtro_fecha_t1 = st.multiselect("📅 Fecha Programada:", fechas_str_t1, placeholder="Todas las fechas", key="f_fecha_t1")

        df_mostrar = df_resultado.copy()
        if filtro_proy:
            df_mostrar = df_mostrar[df_mostrar["Proyecto_Display"].isin(filtro_proy)]
        if filtro_subp:
            df_mostrar = df_mostrar[df_mostrar["Subproyecto"].isin(filtro_subp)]
        if filtro_despacho:
            df_mostrar = df_mostrar[df_mostrar["Identificador de fila"].isin(filtro_despacho)]
        if filtro_anio_t1:
            df_mostrar = df_mostrar[df_mostrar["Fecha_DT"].dt.year.isin(filtro_anio_t1)]
        if filtro_mes_t1:
            df_mostrar = df_mostrar[df_mostrar["Fecha_DT"].dt.strftime("%Y-%m").isin(filtro_mes_t1)]
        if filtro_fecha_t1:
            df_mostrar = df_mostrar[df_mostrar["Fecha_DT"].dt.strftime("%d/%m/%Y").isin(filtro_fecha_t1)]
        if solo_atrasados:
            df_mostrar = df_mostrar[df_mostrar["Desviacion_Dias"] > 0]

        df_mostrar = df_mostrar.sort_values(by=["Fecha_DT", "Cant_Procesos", "Subproyecto"], ascending=[True, False, True])

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
        st.markdown(f"### 📋 Programa de Despachos & Estado de Carga por Estación ({unidad_medida})")
        st.caption("Secuenciado bajo algoritmo EDD (fecha más próxima + prioridad a rutas con más procesos). Totales consolidados al pie de cada columna.")

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

        tabla_html = renderizar_tabla_html(
            df_mostrar,
            totales_dict,
            factor_unidad=factor_unidad,
            fmt_str=fmt_unidad,
            unidad_label=label_unidad
        )
        components.html(tabla_html, height=580, scrolling=False)

        st.markdown("---")
        st.markdown(f"### 📊 Cuadro de Carga Semanal por Proceso ({unidad_medida})")
        st.caption("Muestra la ventana estimada de inicio y término, días hábiles, carga total pendiente y distribución semanal para los lotes filtrados.")

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

        info_procesos_cuadro = {}
        for proc in PROCESOS_LINEA:
            sub_p = df_gantt_filtrado[(df_gantt_filtrado["Proceso"] == proc) & (df_gantt_filtrado["Kilos"] > 0)] if not df_gantt_filtrado.empty else pd.DataFrame()
            kg_tot_proc = float(sub_p["Kilos"].sum()) if not sub_p.empty else 0.0

            if not sub_p.empty and kg_tot_proc > 0:
                f_ini_min = pd.to_datetime(sub_p["Inicio"]).min()
                f_fin_max = pd.to_datetime(sub_p["Fin"]).max()
                ini_str = f_ini_min.strftime("%d/%m/%Y") if pd.notna(f_ini_min) else "-"
                fin_str = f_fin_max.strftime("%d/%m/%Y") if pd.notna(f_fin_max) else "-"
                if len(lotes_visibles) == 1:
                    dias_p = int(sub_p["Duracion"].values[0])
                else:
                    cap_d = capacidad_diaria_kg.get(proc, 1000.0)
                    dias_p = int(np.ceil(kg_tot_proc / cap_d)) if cap_d > 0 else 0
            else:
                ini_str = "-"
                fin_str = "-"
                dias_p = 0

            info_procesos_cuadro[proc] = {
                "inicio_est": ini_str,
                "termino_est": fin_str,
                "dias": dias_p,
                "total_kg": kg_tot_proc
            }

        html_cuadro = renderizar_cuadro_semanal_html(
            piv_data=piv_dict,
            info_procesos_dict=info_procesos_cuadro,
            semanas_cols=semanas_cols,
            cap_semanal_dict=capacidad_semanal_kg,
            factor_unidad=factor_unidad,
            fmt_str=fmt_unidad,
            unidad_label=label_unidad
        )
        components.html(html_cuadro, height=380, scrolling=False)

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
        st.markdown(f"### ⚡ Carga Semanal por Despacho & Capacidad de Planta ({unidad_medida})")
        st.caption("Secuenciación EDD (fecha más próxima + prioridad a rutas con más procesos) y control de fechas estimadas por estación.")

        if st.session_state.get("dotacion_version_key") != DOTACION_VERSION:
            st.session_state.dotacion_version_key = DOTACION_VERSION
            datos_matriz = {"Proceso": PROCESOS_LINEA}
            for sem in semanas_cols:
                datos_matriz[sem] = [DOTACION_BASE_DEFAULT.get(p, 4) for p in PROCESOS_LINEA]
            st.session_state.matriz_dotacion_semanal = pd.DataFrame(datos_matriz)
        else:
            for sem in semanas_cols:
                if sem not in st.session_state.matriz_dotacion_semanal.columns:
                    st.session_state.matriz_dotacion_semanal[sem] = [DOTACION_BASE_DEFAULT.get(p, 4) for p in PROCESOS_LINEA]

        # Fila superior de filtros: Selector de Proceso + Filtro Jerárquico de Fecha Programada (Año -> Mes -> Fecha)
        c_top1, c_top2, c_top3, c_top4 = st.columns([2.2, 1.3, 1.5, 2.0])
        with c_top1:
            proceso_seleccionado = st.selectbox(
                "🎯 Selecciona el Proceso a Analizar:",
                ["Programa Despacho"] + PROCESOS_LINEA,
                index=0,
                key="sel_proc_tab2"
            )

        with c_top2:
            anios_t2 = sorted(df_mostrar["Fecha_DT"].dt.year.dropna().unique().astype(int).tolist())
            filtro_anio_t2 = st.multiselect("📅 Año (F. Prog):", anios_t2, placeholder="Todos", key="f_anio_t2")

        pool_anio_t2 = df_mostrar if not filtro_anio_t2 else df_mostrar[df_mostrar["Fecha_DT"].dt.year.isin(filtro_anio_t2)]
        with c_top3:
            meses_t2 = sorted(pool_anio_t2["Fecha_DT"].dt.strftime("%Y-%m").dropna().unique().tolist())
            filtro_mes_t2 = st.multiselect("📅 Mes (F. Prog):", meses_t2, placeholder="Todos", key="f_mes_t2")

        pool_mes_t2 = pool_anio_t2 if not filtro_mes_t2 else pool_anio_t2[pool_anio_t2["Fecha_DT"].dt.strftime("%Y-%m").isin(filtro_mes_t2)]
        with c_top4:
            fechas_dt_t2 = sorted(pool_mes_t2["Fecha_DT"].dropna().unique())
            fechas_str_t2 = [pd.Timestamp(d).strftime("%d/%m/%Y") for d in fechas_dt_t2]
            filtro_fecha_t2 = st.multiselect("📅 Fecha Programada:", fechas_str_t2, placeholder="Todas", key="f_fecha_t2")

        df_tab2_filtrado = df_mostrar.copy()
        if filtro_anio_t2:
            df_tab2_filtrado = df_tab2_filtrado[df_tab2_filtrado["Fecha_DT"].dt.year.isin(filtro_anio_t2)]
        if filtro_mes_t2:
            df_tab2_filtrado = df_tab2_filtrado[df_tab2_filtrado["Fecha_DT"].dt.strftime("%Y-%m").isin(filtro_mes_t2)]
        if filtro_fecha_t2:
            df_tab2_filtrado = df_tab2_filtrado[df_tab2_filtrado["Fecha_DT"].dt.strftime("%d/%m/%Y").isin(filtro_fecha_t2)]

        with st.expander("👷 Dotación Semanal de Operarios por Proceso (Editable)", expanded=False):
            st.caption("Ajusta la cantidad de operarios por semana para recalcular capacidades, días y fechas estimadas:")
            
            if proceso_seleccionado == "Programa Despacho":
                df_dot_vista = st.session_state.matriz_dotacion_semanal[["Proceso"] + semanas_cols].copy()
            else:
                df_dot_vista = st.session_state.matriz_dotacion_semanal[
                    st.session_state.matriz_dotacion_semanal["Proceso"] == proceso_seleccionado
                ][["Proceso"] + semanas_cols].copy()

            matriz_dot_edit = st.data_editor(
                df_dot_vista,
                column_config={
                    "Proceso": st.column_config.TextColumn("Proceso", disabled=True),
                    **{sem: st.column_config.NumberColumn(sem, min_value=0, max_value=100, step=1) for sem in semanas_cols}
                },
                hide_index=True,
                use_container_width=True,
                key=f"editor_dotacion_{proceso_seleccionado}"
            )
            
            for _, r_ed in matriz_dot_edit.iterrows():
                p_name = r_ed["Proceso"]
                for sem in semanas_cols:
                    st.session_state.matriz_dotacion_semanal.loc[
                        st.session_state.matriz_dotacion_semanal["Proceso"] == p_name, sem
                    ] = r_ed[sem]

        capacidad_dinamica_semanal = {p: {} for p in PROCESOS_LINEA}
        for p in PROCESOS_LINEA:
            ratio_p = ratios_activos.get(p, RATIO_FALLBACK_DEFAULT.get(p, 100.0))
            fila_oper = st.session_state.matriz_dotacion_semanal[st.session_state.matriz_dotacion_semanal["Proceso"] == p]
            for sem in semanas_cols:
                oper_val = float(fila_oper[sem].values[0]) if not fila_oper.empty and sem in fila_oper.columns else DOTACION_BASE_DEFAULT.get(p, 4)
                hh_sem_disp = oper_val * horas_sem * eficiencia
                capacidad_dinamica_semanal[p][sem] = hh_sem_disp * ratio_p

        cap_semanal_vista = {}
        if proceso_seleccionado == "Programa Despacho":
            for sem in semanas_cols:
                cap_semanal_vista[sem] = capacidad_dinamica_semanal["Embalaje"].get(sem, 1000.0)
        else:
            for sem in semanas_cols:
                cap_semanal_vista[sem] = capacidad_dinamica_semanal[proceso_seleccionado].get(sem, 1000.0)

        df_lotes_tab2_list = []
        df_gantt_vista = df_gantt_total[df_gantt_total["Identificador de fila"].isin(df_tab2_filtrado["Identificador de fila"])] if not df_tab2_filtrado.empty else pd.DataFrame()
        df_semanal_desglose = calcular_kilos_semanales(df_gantt_vista) if not df_gantt_vista.empty else pd.DataFrame()

        for _, lote in df_tab2_filtrado.iterrows():
            id_f = lote["Identificador de fila"]
            subp = lote["Subproyecto"]
            f_dt_lote = lote["Fecha_DT"]
            f_despacho_str = lote["Fecha Programada"]
            cant_procs_lote = int(lote.get("Cant_Procesos", 0))
            ruta_tip = str(lote.get("Ruta_Detalle", ""))

            if proceso_seleccionado == "Programa Despacho":
                kg_proc = limpiar_numero(lote.get("Kilos Programados", lote.get("Kg_Prog_Num", 0.0)))
                cap_dia_emb = capacidad_diaria_kg.get("Embalaje", 1000.0)
                dias_p = int(lote.get("Total_Dias", 0))
                if dias_p == 0 and kg_proc > 0 and cap_dia_emb > 0:
                    dias_p = int(np.ceil(kg_proc / cap_dia_emb))

                sem_lote = f"{f_dt_lote.isocalendar()[0]}-{f_dt_lote.isocalendar()[1]:02d}"
                semanas_dict = {sem: (kg_proc if sem == sem_lote else 0.0) for sem in semanas_cols}

                f_est_dt = lote.get("Fecha_Fin_DT", f_dt_lote)
                desv = int(lote.get("Desviacion_Dias", 0))
                fuera_plazo = (desv > 0)
                f_est_str = f_est_dt.strftime("%d/%m/%Y") if pd.notna(f_est_dt) else str(lote.get("Fecha Estimada Finalizar", "-"))
                tooltip_alerta = f"Desviado +{desv} días respecto a Fecha Despacho"
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
                kg_proc = limpiar_numero(lote.get(mapa_col_kg.get(proceso_seleccionado, ""), 0.0))
                cap_dia = capacidad_diaria_kg.get(proceso_seleccionado, 1000.0)
                dias_p = int(np.ceil(kg_proc / cap_dia)) if cap_dia > 0 and kg_proc > 0 else 0

                df_filtro_lote = df_semanal_desglose[
                    (df_semanal_desglose["Identificador de fila"] == id_f) & 
                    (df_semanal_desglose["Proceso"] == proceso_seleccionado)
                ] if not df_semanal_desglose.empty else pd.DataFrame()
                semanas_dict = df_filtro_lote.groupby("Semana")["Kilos"].sum().to_dict() if not df_filtro_lote.empty else {}

                rec_proc = df_gantt_vista[
                    (df_gantt_vista["Identificador de fila"] == id_f) & 
                    (df_gantt_vista["Proceso"] == proceso_seleccionado)
                ] if not df_gantt_vista.empty else pd.DataFrame()
                if not rec_proc.empty:
                    f_est_val = rec_proc["Fin"].values[0]
                    if isinstance(f_est_val, np.datetime64):
                        f_est_val = pd.Timestamp(f_est_val)
                    f_est_dt = f_est_val
                    fuera_plazo = bool(rec_proc["Fuera_Plazo"].values[0])
                    dias_desv_p = int(rec_proc["Dias_Desv_Proc"].values[0])
                    f_lim_val = rec_proc["Fecha_Limite"].values[0]
                    if isinstance(f_lim_val, np.datetime64):
                        f_lim_val = pd.Timestamp(f_lim_val)
                    f_est_str = f_est_dt.strftime("%d/%m/%Y")
                    tooltip_alerta = f"Proceso desviado +{dias_desv_p}d (Límite para cumplir despacho: {f_lim_val.strftime('%d/%m/%Y')})"
                else:
                    f_est_dt = f_dt_lote
                    fuera_plazo = False
                    f_est_str = "-"
                    tooltip_alerta = ""

            if fuera_plazo:
                fecha_est_disp = f'{f_est_str} <span style="color:#dc2626; font-size:12px; cursor:help;" title="{tooltip_alerta}">⚠️</span>'
            else:
                fecha_est_disp = f_est_str

            if kg_proc > 50.0:
                fila_reg = {
                    "Identificador de fila": id_f,
                    "Subproyecto": subp,
                    "Fecha_DT": f_dt_lote,
                    "Cant_Procesos": cant_procs_lote,
                    "Ruta_Tooltip": ruta_tip,
                    "Fecha_Despacho": f_despacho_str,
                    "Fecha_Est_Display": fecha_est_disp,
                    "Cant_Dias": dias_p,
                    "Kilos_Procesar": kg_proc,
                }
                for sem in semanas_cols:
                    fila_reg[sem] = semanas_dict.get(sem, 0.0)

                df_lotes_tab2_list.append(fila_reg)

        df_lotes_tab2 = pd.DataFrame(df_lotes_tab2_list)

        if df_lotes_tab2.empty:
            st.info(f"🎉 No hay lotes pendientes en **{proceso_seleccionado}** (Carga ≤ {formatear_valor_unidad(50.0, factor_unidad, fmt_unidad)} {corto_unidad}).")
        else:
            df_lotes_tab2 = df_lotes_tab2.sort_values(
                by=["Fecha_DT", "Cant_Procesos", "Subproyecto"],
                ascending=[True, False, True]
            )

            html_tab2 = render_tabla_carga_proceso_html(
                df_lotes_proc=df_lotes_tab2,
                semanas_cols=semanas_cols,
                cap_por_semana=cap_semanal_vista,
                factor_unidad=factor_unidad,
                fmt_str=fmt_unidad,
                unidad_label=label_unidad
            )
            components.html(html_tab2, height=600, scrolling=False)

except Exception as e:
    st.error(f"Error procesando los datos: {e}")
