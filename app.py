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

st.set_page_config(page_title="Control de Despachos & Fechas Estimadas", layout="wide")

# -------------------------------------------------------------
# 0. CONFIGURACIÓN DE PROCESOS Y PRECEDENCIAS
# -------------------------------------------------------------
PROCESOS_LINEA = [
    "Ingeniería",
    "CNCL",
    "CNCPL",
    "Talón",
    "DoblezL",
    "Perforado",
    "Armado Soldadura",
    "Soldadura",
    "Fabricado",
    "Galvanizado",
    "Embalaje"
]

DOTACION_BASE_DEFAULT = {
    "Ingeniería": 2, "CNCL": 4, "CNCPL": 4, 
    "Talón": 2, "DoblezL": 2, "Perforado": 3, "Armado Soldadura": 8, 
    "Soldadura": 8, "Fabricado": 2, "Galvanizado": 8, "Embalaje": 4
}

def limpiar_texto(txt):
    if not isinstance(txt, str): return ""
    txt = unicodedata.normalize('NFKD', txt).encode('ASCII', 'ignore').decode('utf-8')
    return re.sub(r'[^a-zA-Z0-9]', '', txt).lower()

# -------------------------------------------------------------
# 1. CONEXIÓN A SMARTSHEET Y NORMALIZACIÓN
# -------------------------------------------------------------
@st.cache_data(ttl=300)
def cargar_hoja(sheet_id):
    cliente = smartsheet.Smartsheet(st.secrets["SMARTSHEET_ACCESS_TOKEN"])
    hoja = cliente.Sheets.get_sheet(int(sheet_id))
    columnas = [col.title.strip() for col in hoja.columns]
    filas = [[celda.value for celda in f.cells] for f in hoja.rows]
    return pd.DataFrame(filas, columns=columnas)

def normalizar_procesos(df_proc):
    cols = df_proc.columns.tolist()
    col_subproj = next((c for c in cols if "sub-proyecto" in c.lower() or "subproyecto" in c.lower()), None)
    col_cargados = next((c for c in cols if "cargados" in c.lower()), None)
    col_orden = next((c for c in cols if "orden de proceder" in c.lower()), None)

    procesos_taller = [p for p in PROCESOS_LINEA if p != "Ingeniería"]
    procesos_busqueda = sorted(procesos_taller, key=len, reverse=True)
    mapa_cols = {p: {} for p in procesos_taller}

    for c in cols:
        c_clean = limpiar_texto(c)
        if any(x in c_clean for x in ["subproyecto", "sub-proyecto", "orden", "cargad"]):
            continue

        for proc in procesos_busqueda:
            alias = limpiar_texto(proc)
            if proc == "Fabricado": alias = "fabricad"
            elif proc == "Galvanizado": alias = "galvaniz"
            elif proc == "Embalaje": alias = "embala"
            elif proc == "Armado Soldadura": alias = "armadosoldadura"
            elif proc == "Soldadura": alias = "soldadura"

            if alias in c_clean:
                if "estimad" in c_clean:
                    mapa_cols[proc]['estimado'] = c
                elif "lanzad" in c_clean:
                    mapa_cols[proc]['lanzado'] = c
                else:
                    mapa_cols[proc]['avance'] = c
                break

    registros = []
    datos_ingenieria = {}

    for _, fila in df_proc.iterrows():
        subproyecto = str(fila.get(col_subproj, "")).strip()
        if not subproyecto or subproyecto in ["None", "nan"]: continue
        
        kilos_cargados = pd.to_numeric(fila.get(col_cargados, 0), errors='coerce') or 0
        kilos_orden = pd.to_numeric(fila.get(col_orden, 0), errors='coerce') or 0
        datos_ingenieria[subproyecto] = {"Orden": kilos_orden, "Cargados": kilos_cargados}

        kg_total_proyecto = kilos_cargados if kilos_cargados > 0 else kilos_orden

        # 1. Ingeniería
        kg_necesidad_ing = kilos_orden if kilos_orden > 0 else kilos_cargados
        kg_av_ing = kilos_cargados
        kg_pend_ing = max(0.0, kg_necesidad_ing - kg_av_ing)
        
        registros.append({
            "Subproyecto": subproyecto,
            "Proceso": "Ingeniería",
            "Kg_Requeridos": kg_necesidad_ing,
            "Kg_Avanzados": kg_av_ing,
            "Kg_Pendientes": kg_pend_ing
        })

        # 2. Procesos de Taller
        es_estimado = (kilos_cargados <= 0)
        for proc in procesos_taller:
            col_est = mapa_cols[proc].get('estimado')
            col_lanz = mapa_cols[proc].get('lanzado')
            col_av = mapa_cols[proc].get('avance')
            
            kg_av = pd.to_numeric(fila.get(col_av, 0), errors='coerce') or 0 if col_av else 0
            
            # Para Fabricado toma la meta total del proyecto; Galvanizado y Embalaje se fijan por despacho
            if proc == "Fabricado":
                kg_necesidad = kg_total_proyecto
            else:
                kg_est = pd.to_numeric(fila.get(col_est, 0), errors='coerce') or 0 if col_est else 0
                kg_lanz = pd.to_numeric(fila.get(col_lanz, 0), errors='coerce') or 0 if col_lanz else 0
                kg_necesidad = kg_est if es_estimado else kg_lanz
            
            kg_pendientes = max(0.0, kg_necesidad - kg_av)
            
            registros.append({
                "Subproyecto": subproyecto,
                "Proceso": proc,
                "Kg_Requeridos": kg_necesidad,
                "Kg_Avanzados": kg_av,
                "Kg_Pendientes": kg_pendientes
            })
                
    return pd.DataFrame(registros), datos_ingenieria

# -------------------------------------------------------------
# 2. CONTROLES GENERALES DE PLANTA
# -------------------------------------------------------------
st.sidebar.header("⚙️ Parámetros de Operación")
horas_sem = st.sidebar.number_input("Horas turno / semana por operario", value=44, step=1)
eficiencia = st.sidebar.slider("Eficiencia Operativa / OEE (%)", 50, 100, 85) / 100.0

st.title("📋 Control de Despachos: Desviación & Fecha Estimada de Entrega")

if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()
    st.rerun()

try:
    with st.spinner("Sincronizando información de planta..."):
        df_backlog_raw = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
        df_procesos_raw = cargar_hoja(st.secrets["SHEET_ID_PROCESOS"])
        df_prod_raw = cargar_hoja(st.secrets["SHEET_ID_PRODUCTIVIDAD"])

    # Detección de columnas de Backlog con prioridad a "Identificador de fila"
    cols_backlog = df_backlog_raw.columns.tolist()
    col_id_fila = next((c for c in cols_backlog if "identificador" in c.lower() and "fila" in c.lower()), None)
    if not col_id_fila:
        col_id_fila = next((c for c in cols_backlog if "identificador" in c.lower()), None)
    if not col_id_fila:
        col_id_fila = next((c for c in cols_backlog if "id" in c.lower() or "despacho" in c.lower()), cols_backlog[0])

    col_sub_backlog = next((c for c in cols_backlog if "sub-proyecto" in c.lower() or "subproyecto" in c.lower()), None)
    col_fecha_prog = next((c for c in cols_backlog if "programad" in c.lower() and "fecha" in c.lower()), None)
    if not col_fecha_prog:
        col_fecha_prog = next((c for c in cols_backlog if "fecha" in c.lower()), None)
    col_despacho_real = next((c for c in cols_backlog if "despacho" in c.lower() and ("real" in c.lower() or "fecha" in c.lower())), None)
    col_kg_prog = next((c for c in cols_backlog if "kilos" in c.lower() and "programad" in c.lower()), None)

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

    # Filtro de Horizonte
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
    subproyectos_horizonte = df_backlog_horizonte[col_sub_backlog].dropna().astype(str).str.strip().unique()

    # Normalizar procesos
    df_carga, datos_ing = normalizar_procesos(df_procesos_raw)
    df_carga = df_carga[df_carga["Subproyecto"].isin(subproyectos_horizonte)].copy()

    # Productividad
    col_prod_proc = next((c for c in df_prod_raw.columns if "proceso" in c.lower() and "simulador" in c.lower()), df_prod_raw.columns[0])
    col_prod_kghh = next((c for c in df_prod_raw.columns if "kg" in c.lower() and "hh" in c.lower()), df_prod_raw.columns[1])
    
    ratios_productividad = {}
    for _, r in df_prod_raw.iterrows():
        proc_str = limpiar_texto(str(r[col_prod_proc]))
        kghh_val = pd.to_numeric(r[col_prod_kghh], errors='coerce')
        if pd.notna(kghh_val) and kghh_val > 0:
            for p_oficial in PROCESOS_LINEA:
                if limpiar_texto(p_oficial) in proc_str:
                    ratios_productividad[p_oficial] = float(kghh_val)

    # -------------------------------------------------------------
    # 3. MOTOR DE CÁLCULO: CAPACIDADES DIARIAS Y RUTA CRÍTICA
    # -------------------------------------------------------------
    capacidad_diaria_kg = {}
    for proc in PROCESOS_LINEA:
        oper = DOTACION_BASE_DEFAULT.get(proc, 4)
        ratio = ratios_productividad.get(proc, 80.0)
        hh_dia = (oper * horas_sem / 5.5) * eficiencia
        capacidad_diaria_kg[proc] = max(100.0, hh_dia * ratio)

    hoy = pd.Timestamp.now().normalize()
    filas_tabla_despachos = []

    for subp in subproyectos_horizonte:
        lotes_subp = df_backlog_horizonte[df_backlog_horizonte[col_sub_backlog] == subp].sort_values("Fecha_DT")
        tot_prog_subp = lotes_subp["Kg_Prog_Num"].sum()
        df_subp_carga = df_carga[df_carga["Subproyecto"] == subp]

        info_ing = datos_ing.get(subp, {"Orden": 0.0, "Cargados": 0.0})
        kg_orden_subp = info_ing["Orden"]
        kg_carg_subp = info_ing["Cargados"]

        # Pool de avances reales registrados en el subproyecto para consumo FIFO
        av_disponible = {}
        req_total_subp = {}
        for proc in PROCESOS_LINEA:
            f = df_subp_carga[df_subp_carga["Proceso"] == proc]
            req_total_subp[proc] = f["Kg_Requeridos"].values[0] if not f.empty else 0.0
            av_disponible[proc] = f["Kg_Avanzados"].values[0] if not f.empty else 0.0

        for _, lote in lotes_subp.iterrows():
            id_despacho = str(lote[col_id_fila])
            f_prog = lote["Fecha_DT"]
            kg_lote = lote["Kg_Prog_Num"]
            factor = (kg_lote / tot_prog_subp) if tot_prog_subp > 0 else (1.0 / len(lotes_subp))

            kg_orden_lote = kg_orden_subp * factor
            kg_carg_lote = kg_carg_subp * factor

            pends_lote = {}
            dias_proc = {}

            for proc in PROCESOS_LINEA:
                # REGLA CLAVE: Para Galvanizado y Embalaje, la meta requerida es exactamente los kilos programados del despacho
                if proc in ["Galvanizado", "Embalaje"]:
                    req_lp = kg_lote
                else:
                    req_lp = req_total_subp[proc] * factor

                av_lp = min(req_lp, av_disponible[proc])
                av_disponible[proc] -= av_lp
                pend_lp = max(0.0, req_lp - av_lp)
                pends_lote[proc] = pend_lp
                
                dias_proc[proc] = pend_lp / capacidad_diaria_kg[proc]

            # Kilos de las columnas específicas solicitadas
            kg_por_cargar_ing = max(0.0, kg_orden_lote - kg_carg_lote)
            kg_cncl = pends_lote.get("CNCL", 0.0)
            kg_cncpl = pends_lote.get("CNCPL", 0.0)
            kg_soldadura = max(pends_lote.get("Armado Soldadura", 0.0), pends_lote.get("Soldadura", 0.0))
            kg_fabricado = pends_lote.get("Fabricado", 0.0)
            kg_galvanizado = pends_lote.get("Galvanizado", 0.0)
            kg_embalaje = pends_lote.get("Embalaje", 0.0)

            # Modelo de traslapes y ruta crítica (días hábiles)
            t_ing = dias_proc.get("Ingeniería", 0.0)
            t_corte_start = t_ing * 0.5
            t_cncl_end = t_corte_start + dias_proc.get("CNCL", 0.0)
            t_cncpl_end = t_corte_start + dias_proc.get("CNCPL", 0.0)

            t_sold_start = max(t_corte_start + 0.25 * dias_proc.get("CNCL", 0.0),
                               t_corte_start + 0.25 * dias_proc.get("CNCPL", 0.0))
            t_sold_dur = dias_proc.get("Armado Soldadura", 0.0) + dias_proc.get("Soldadura", 0.0)
            t_sold_end = t_sold_start + t_sold_dur

            t_fab_start = t_sold_start + 0.5 * t_sold_dur
            t_fab_end = t_fab_start + dias_proc.get("Fabricado", 0.0)

            t_galv_start = t_fab_start + 0.8 * dias_proc.get("Fabricado", 0.0)
            t_galv_end = t_galv_start + dias_proc.get("Galvanizado", 0.0)

            t_emb_start = t_galv_start + 0.3 * dias_proc.get("Galvanizado", 0.0)
            t_emb_end = t_emb_start + dias_proc.get("Embalaje", 0.0)

            dias_habiles_totales = int(np.ceil(max(0.0, t_emb_end)))

            # Cálculo de fechas con corrimiento dinámico
            if dias_habiles_totales == 0:
                fecha_inicio_guia = f_prog
                fecha_estimada_final = f_prog
                desviacion_dias = 0
                estado_desv = "🟢 Completado"
            else:
                fecha_inicio_guia = f_prog - pd.offsets.BDay(dias_habiles_totales)

                if fecha_inicio_guia < hoy:
                    fecha_estimada_final = hoy + pd.offsets.BDay(dias_habiles_totales)
                    desviacion_dias = (fecha_estimada_final - f_prog).days
                    if desviacion_dias > 0:
                        estado_desv = f"🔴 Retraso (+{desviacion_dias}d)"
                    else:
                        estado_desv = "🟡 Al Límite"
                else:
                    fecha_estimada_final = f_prog
                    holgura = (fecha_inicio_guia - hoy).days
                    desviacion_dias = 0
                    estado_desv = f"🟢 A Tiempo ({holgura}d holgura)"

            filas_tabla_despachos.append({
                "Identificador de fila": id_despacho,
                "Subproyecto": subp,
                "Kg Orden Proceder": kg_orden_lote,
                "Kg por cargar (Ing)": kg_por_cargar_ing,
                "Kg por procesar CNCL": kg_cncl,
                "Kg por procesar CNCPL": kg_cncpl,
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

    # -------------------------------------------------------------
    # 4. VISUALIZACIÓN EN PANTALLA
    # -------------------------------------------------------------
    total_lotes = len(df_resultado)
    lotes_atrasados = len(df_resultado[df_resultado["Desviacion_Dias"] > 0])
    lotes_ok = total_lotes - lotes_atrasados
    max_retraso = df_resultado["Desviacion_Dias"].max() if not df_resultado.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Lotes Evaluados", total_lotes)
    c2.metric("Lotes en Fecha", lotes_ok)
    c3.metric("Lotes Desviados (Retraso)", lotes_atrasados, delta=f"-{lotes_atrasados}" if lotes_atrasados > 0 else "0", delta_color="inverse")
    c4.metric("Máximo Retraso Detectado", f"+{max_retraso} días" if max_retraso > 0 else "0 días", delta_color="inverse")

    st.markdown("---")

    col_f1, col_f2 = st.columns([2, 3])
    with col_f1:
        filtro_subp = st.multiselect("Filtrar por Subproyecto:", sorted(df_resultado["Subproyecto"].unique()))
    with col_f2:
        solo_atrasados = st.checkbox("Mostrar únicamente lotes con fecha desviada (retraso)")

    df_mostrar = df_resultado.copy()
    if filtro_subp:
        df_mostrar = df_mostrar[df_mostrar["Subproyecto"].isin(filtro_subp)]
    if solo_atrasados:
        df_mostrar = df_mostrar[df_mostrar["Desviacion_Dias"] > 0]

    df_mostrar = df_mostrar.sort_values(by="Fecha_DT")

    # Totales para el pie de tabla
    tot_orden = df_mostrar["Kg Orden Proceder"].sum()
    tot_ing = df_mostrar["Kg por cargar (Ing)"].sum()
    tot_cncl = df_mostrar["Kg por procesar CNCL"].sum()
    tot_cncpl = df_mostrar["Kg por procesar CNCPL"].sum()
    tot_sold = df_mostrar["Kg por procesar Soldadura"].sum()
    tot_fab = df_mostrar["Kg por procesar Fabricado"].sum()
    tot_galv = df_mostrar["Kg por galvanizar"].sum()
    tot_emb = df_mostrar["Kg por Embalar"].sum()

    filas_html = []
    for idx, r in df_mostrar.iterrows():
        bg = "#ffffff" if idx % 2 == 0 else "#fafafa"
        color_diag = "#b7e1cd" if "A Tiempo" in r["Diagnostico"] or "Completado" in r["Diagnostico"] else "#f4c7c3"
        
        filas_html.append(f"""
        <tr style="background-color:{bg}; border-bottom:1px solid #eee;">
            <td style="padding:8px 6px; text-align:left; font-weight:600; position:sticky; left:0; background-color:{bg}; z-index:2; border-right:1px solid #ddd;">{r['Identificador de fila']}</td>
            <td style="padding:8px 6px; text-align:left; position:sticky; left:130px; background-color:{bg}; z-index:2; border-right:1px solid #ddd;">{r['Subproyecto']}</td>
            <td style="padding:8px 6px; text-align:right;">{r['Kg Orden Proceder']:,.0f}</td>
            <td style="padding:8px 6px; text-align:right; font-weight:500; color:{'#d62728' if r['Kg por cargar (Ing)'] > 0 else '#555'};">{r['Kg por cargar (Ing)']:,.0f}</td>
            <td style="padding:8px 6px; text-align:right;">{r['Kg por procesar CNCL']:,.0f}</td>
            <td style="padding:8px 6px; text-align:right;">{r['Kg por procesar CNCPL']:,.0f}</td>
            <td style="padding:8px 6px; text-align:right;">{r['Kg por procesar Soldadura']:,.0f}</td>
            <td style="padding:8px 6px; text-align:right;">{r['Kg por procesar Fabricado']:,.0f}</td>
            <td style="padding:8px 6px; text-align:right; font-weight:500;">{r['Kg por galvanizar']:,.0f}</td>
            <td style="padding:8px 6px; text-align:right; font-weight:500;">{r['Kg por Embalar']:,.0f}</td>
            <td style="padding:8px 6px; text-align:center; font-weight:500;">{r['Fecha Programada']}</td>
            <td style="padding:8px 6px; text-align:center; font-weight:bold; color:{'#d62728' if r['Desviacion_Dias'] > 0 else '#2ca02c'};">{r['Fecha Estimada Finalizar']}</td>
            <td style="padding:8px 6px; text-align:center; background-color:{color_diag}; font-weight:600; font-size:11px;">{r['Diagnostico']}</td>
        </tr>
        """)

    tabla_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
    body {{ margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
    .tabla-container {{ overflow:auto; max-height:640px; border:1px solid #ccc; border-radius:4px; }}
    table {{ width:100%; border-collapse:collapse; font-size:12px; }}
    thead th {{ background-color:#fce5cd; color:#333; position:sticky; top:0; z-index:5; border-bottom:2px solid #ccc; font-weight:600; text-align:center; }}
    tfoot td {{ background-color:#e8eaed; position:sticky; bottom:0; z-index:5; border-top:2px solid #999; font-weight:bold; }}
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
        <th style="padding:9px 6px; text-align:right; min-width:90px;">Kg por procesar CNCPL</th>
        <th style="padding:9px 6px; text-align:right; min-width:95px;">Kg por procesar Soldadura</th>
        <th style="padding:9px 6px; text-align:right; min-width:90px;">Kg por procesar Fabricado</th>
        <th style="padding:9px 6px; text-align:right
