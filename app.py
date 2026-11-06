import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import smartsheet
import re
import unicodedata
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

st.set_page_config(page_title="Reporte Operaciones - Avance Planta", layout="wide")

# -------------------------------------------------------------
# 0. CONFIGURACIÓN Y COMPUERTAS DE PROCESOS
# -------------------------------------------------------------
PROCESOS_LINEA = [
    "Ingeniería",
    "Oficina Técnica",
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

COMPUERTAS_PROCESO = {
    "Ingeniería": [],
    "Oficina Técnica": [("Ingeniería", 0.50)],
    "CNCL": [("Oficina Técnica", 0.25)],
    "CNCPL": [("Oficina Técnica", 0.25)],
    "Talón": [("CNCL", 0.25)],
    "DoblezL": [("Talón", 0.10)],
    "Perforado": [("DoblezL", 0.20)],
    "Armado Soldadura": [("Perforado", 0.25), ("CNCPL", 0.25)],
    "Soldadura": [("Armado Soldadura", 0.25)],
    "Fabricado": [("Soldadura", 0.50)],
    "Galvanizado": [("Fabricado", 0.80)],
    "Embalaje": [("Galvanizado", 0.30)]
}

DOTACION_BASE_DEFAULT = {
    "Ingeniería": 2, "Oficina Técnica": 2, "CNCL": 4, "CNCPL": 4, 
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

    procesos_busqueda = sorted(PROCESOS_LINEA, key=len, reverse=True)
    mapa_cols = {p: {} for p in PROCESOS_LINEA}

    for c in cols:
        c_clean = limpiar_texto(c)
        for proc in procesos_busqueda:
            p_clean = limpiar_texto(proc)
            if p_clean in c_clean:
                if c_clean.startswith("estimado"):
                    mapa_cols[proc]['estimado'] = c
                elif "lanzado" in c_clean:
                    mapa_cols[proc]['lanzado'] = c
                elif c_clean.startswith("kilo") and not any(x in c_clean for x in ["lanzado", "cargado", "orden"]):
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

        es_estimado = (kilos_cargados <= 0)
        
        for proc in PROCESOS_LINEA:
            col_est = mapa_cols[proc].get('estimado')
            col_lanz = mapa_cols[proc].get('lanzado')
            col_av = mapa_cols[proc].get('avance')
            
            kg_est = pd.to_numeric(fila.get(col_est, 0), errors='coerce') or 0 if col_est else 0
            kg_lanz = pd.to_numeric(fila.get(col_lanz, 0), errors='coerce') or 0 if col_lanz else 0
            kg_av = pd.to_numeric(fila.get(col_av, 0), errors='coerce') or 0 if col_av else 0
            
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
# 2. MOTOR DE EVALUACIÓN DE COMPUERTAS
# -------------------------------------------------------------
def diagnosticar_bloqueos_subproyecto(df_subp_procesos):
    avances = {}
    pendientes = {}
    requeridos = {}
    for proc in PROCESOS_LINEA:
        fila = df_subp_procesos[df_subp_procesos["Proceso"] == proc]
        if not fila.empty:
            req = fila["Kg_Requeridos"].values[0]
            av = fila["Kg_Avanzados"].values[0]
            requeridos[proc] = req
            avances[proc] = (av / req) if req > 0 else 1.0
            pendientes[proc] = fila["Kg_Pendientes"].values[0]
        else:
            requeridos[proc] = 0.0
            avances[proc] = 1.0
            pendientes[proc] = 0.0

    estados_estaciones = {}
    for proc in PROCESOS_LINEA:
        req = requeridos[proc]
        av_pct = avances[proc]
        
        if req == 0:
            estados_estaciones[proc] = {"estado": "NA", "color": "#f1f3f4", "texto": "-", "detalle": "Sin carga para este proceso"}
            continue
            
        if av_pct >= 1.0:
            estados_estaciones[proc] = {"estado": "OK", "color": "#b7e1cd", "texto": "100%", "detalle": "Completado al 100%"}
            continue

        predecesores = COMPUERTAS_PROCESO.get(proc, [])
        bloqueado = False
        motivos = []

        for pred, umbral in predecesores:
            av_pred = avances.get(pred, 0.0)
            if av_pred < umbral:
                bloqueado = True
                motivos.append(f"{pred} ({av_pred*100:.0f}% de {umbral*100:.0f}%)")

        if bloqueado:
            estados_estaciones[proc] = {
                "estado": "BLOQUEADO", "color": "#f4c7c3", 
                "texto": f"{av_pct*100:.0f}%", "detalle": f"Bloqueado por: {', '.join(motivos)}"
            }
        elif av_pct > 0:
            estados_estaciones[proc] = {
                "estado": "EN_PROCESO", "color": "#fff2cc", 
                "texto": f"{av_pct*100:.0f}%", "detalle": f"En fabricación activa: restan {pendientes[proc]:,.0f} kg"
            }
        else:
            estados_estaciones[proc] = {
                "estado": "EN_ESPERA", "color": "#ffe599", 
                "texto": "0%", "detalle": f"Habilitado con {req:,.0f} kg en espera de operarios"
            }

    return estados_estaciones, requeridos, pendientes, avances

# -------------------------------------------------------------
# 3. CONTROLES Y EJECUCIÓN PRINCIPAL
# -------------------------------------------------------------
st.sidebar.header("⚙️ Parámetros de Operación")
horas_sem = st.sidebar.number_input("Horas turno / semana por operario", value=44, step=1)
eficiencia = st.sidebar.slider("Eficiencia Operativa / OEE (%)", 50, 100, 85) / 100.0

st.title("📋 Reporte de Operaciones: Avance & Carga por Proceso")

if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()
    st.rerun()

try:
    with st.spinner("Sincronizando información de planta..."):
        df_backlog_raw = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
        df_procesos_raw = cargar_hoja(st.secrets["SHEET_ID_PROCESOS"])
        df_prod_raw = cargar_hoja(st.secrets["SHEET_ID_PRODUCTIVIDAD"])

    # Filtro Backlog Activo
    cols_backlog = df_backlog_raw.columns.tolist()
    col_id_fila = next((c for c in cols_backlog if "identificador" in c.lower()), cols_backlog[0])
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

    # Formateo de fechas robusto (con soporte para formato DD/MM/AAAA)
    df_backlog_activo["Fecha_DT"] = pd.to_datetime(df_backlog_activo[col_fecha_prog], errors='coerce', dayfirst=True)
    df_backlog_activo = df_backlog_activo.dropna(subset=["Fecha_DT"]).sort_values("Fecha_DT")
    df_backlog_activo["Mes_Entrega"] = df_backlog_activo["Fecha_DT"].dt.strftime("%Y-%m")
    df_backlog_activo["Semana_Entrega"] = df_backlog_activo["Fecha_DT"].apply(
        lambda d: f"{d.isocalendar()[0]}-{d.isocalendar()[1]:02d}"
    )
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

    # Normalizar procesos y datos comerciales
    df_carga, datos_ing = normalizar_procesos(df_procesos_raw)
    df_carga = df_carga[df_carga["Subproyecto"].isin(subproyectos_horizonte)].copy()

    # Evaluación de compuertas por subproyecto
    diag_subp_dict = {}
    for subp in subproyectos_horizonte:
        df_subp_p = df_carga[df_carga["Subproyecto"] == subp]
        diag_subp_dict[subp] = diagnosticar_bloqueos_subproyecto(df_subp_p)

    # Semanas para matriz
    semanas_horizonte = sorted(df_backlog_horizonte["Semana_Entrega"].unique().tolist())
    semanas_matriz = semanas_horizonte[:14] if len(semanas_horizonte) >= 6 else semanas_horizonte + [f"Sem+{i:02d}" for i in range(1, 7 - len(semanas_horizonte))]

    if "matriz_dotacion" not in st.session_state:
        datos_matriz = {"Proceso": PROCESOS_LINEA}
        for sem in semanas_matriz:
            datos_matriz[sem] = [DOTACION_BASE_DEFAULT.get(p, 4) for p in PROCESOS_LINEA]
        st.session_state.matriz_dotacion = pd.DataFrame(datos_matriz)

    # -------------------------------------------------------------
    # 4. PESTAÑAS DE TRABAJO
    # -------------------------------------------------------------
    tab_reporte, tab_torre_control, tab_sim_proceso, tab_matriz_global = st.tabs([
        "📊 Matriz Visual de Avance (Estilo Reporte)",
        "🚀 Torre de Control & Despachos",
        "🎯 Simulador por Estación",
        "🗓️ Matriz Global (Dotación)"
    ])

    # -------------------------------------------------------------
    # TAB 1: REPORTE DE OPERACIONES ESTILO HEATMAP (LA IMAGEN)
    # -------------------------------------------------------------
    with tab_reporte:
        c_sup1, c_sup2 = st.columns([3, 1])
        with c_sup1:
            st.markdown(f"### 📋 Avance Proyecto por Despacho y Proceso (Desde {mes_seleccionado})")
            st.caption(f"Lotes activos encontrados: **{len(df_backlog_horizonte)}**. Pasa el cursor sobre las celdas rojas para conocer qué proceso bloquea el paso.")
        with c_sup2:
            modo_celda = st.radio("Mostrar en celdas:", ["Kilos Avanzados", "Porcentaje (%)"], horizontal=True)

        if df_backlog_horizonte.empty:
            st.warning("⚠️ No se encontraron lotes con fechas programadas activas para el mes seleccionado.")
        else:
            # Leyenda visual
            st.markdown("""
            <div style="display:flex; gap:12px; margin-bottom:10px; font-size:12px; flex-wrap:wrap;">
                <span><span style="background-color:#b7e1cd; padding:2px 7px; border-radius:3px; font-weight:bold;">100%</span> Completado</span>
                <span><span style="background-color:#fff2cc; padding:2px 7px; border-radius:3px; font-weight:bold;">> 0%</span> En Fabricación</span>
                <span><span style="background-color:#ffe599; padding:2px 7px; border-radius:3px; font-weight:bold;">0%</span> Habilitado en Piso (En Espera)</span>
                <span><span style="background-color:#f4c7c3; padding:2px 7px; border-radius:3px; font-weight:bold;">⛔</span> Bloqueado por Predecesor</span>
                <span><span style="background-color:#fff; border:1px solid #ccc; padding:2px 7px; border-radius:3px;">⚠️</span> Dif. > 5% Orden vs Cargados</span>
            </div>
            """, unsafe_allow_html=True)

            # Generación limpia del HTML sin sangrías que rompan el parser
            filas_html = []
            for idx, lote in df_backlog_horizonte.iterrows():
                subp = str(lote[col_sub_backlog]).strip()
                id_lote = str(lote[col_id_fila])
                f_prog = lote["Fecha_DT"].strftime("%d/%m/%Y")
                kg_lote = lote["Kg_Prog_Num"]

                info_ing = datos_ing.get(subp, {"Orden": 0, "Cargados": 0})
                kg_orden = info_ing["Orden"]
                kg_carg = info_ing["Cargados"]

                alerta_ing = ""
                if kg_orden > 0 and kg_carg > 0:
                    dif_pct = abs(kg_carg - kg_orden) / kg_orden
                    if dif_pct > 0.05:
                        alerta_ing = f' <span title="Diferencia mayor a 5% ({dif_pct*100:.1f}%)" style="cursor:help;">⚠️</span>'

                info_subp = diag_subp_dict.get(subp)
                estados, reqs, pends, avances = info_subp if info_subp else ({}, {}, {}, {})

                tot_req = sum(reqs.values())
                tot_av = sum(reqs[p] - pends[p] for p in PROCESOS_LINEA if p in reqs)
                pct_global = (tot_av / tot_req * 100) if tot_req > 0 else 0

                bg_fila = "#ffffff" if idx % 2 == 0 else "#fafafa"

                celdas_proc = []
                for proc in PROCESOS_LINEA:
                    est = estados.get(proc, {"estado": "NA", "color": "#f1f3f4", "texto": "-", "detalle": ""})
                    color_bg = est["color"]
                    hover = est["detalle"]
                    req_p = reqs.get(proc, 0.0)
                    pend_p = pends.get(proc, 0.0)
                    av_kg = max(0.0, req_p - pend_p)
                    av_pct = avances.get(proc, 0.0) * 100

                    val = f"{av_kg:,.0f}" if modo_celda == "Kilos Avanzados" else f"{av_pct:.0f}%"
                    if req_p == 0: val = "-"
                    icono = " ⛔" if est["estado"] == "BLOQUEADO" else ""

                    celdas_proc.append(
                        f'<td title="{hover}" style="background-color:{color_bg}; padding:6px 4px; text-align:center; border-left:1px solid #e0e0e0; font-size:11px; font-weight:500;">{val}{icono}</td>'
                    )

                color_total = "#b7e1cd" if pct_global >= 100 else ("#fff2cc" if pct_global > 30 else "#f4c7c3")

                fila_str = f"""<tr style="background-color:{bg_fila}; border-bottom:1px solid #eee;">
<td style="padding:7px; text-align:left; font-weight:600; position:sticky; left:0; background-color:{bg_fila}; z-index:2; border-right:1px solid #ddd;">{subp}</td>
<td style="padding:7px; text-align:left; position:sticky; left:135px; background-color:{bg_fila}; z-index:2; border-right:1px solid #ddd; color:#555;">{id_lote}</td>
<td style="padding:7px; text-align:center;">{f_prog}</td>
<td style="padding:7px; text-align:right; font-weight:500;">{kg_lote:,.0f}</td>
<td style="padding:7px; text-align:right; color:#555;">{kg_orden:,.0f}</td>
<td style="padding:7px; text-align:right; font-weight:500;">{kg_carg:,.0f}{alerta_ing}</td>
{''.join(celdas_proc)}
<td style="padding:7px; text-align:center; font-weight:bold; background-color:{color_total}; border-left:2px solid #bbb;">{pct_global:.0f}%</td>
</tr>"""
                filas_html.append(fila_str)

            encabezados_proc = "".join([f'<th style="padding:9px 5px; text-align:center; min-width:80px; border-left:1px solid #e0e0e0;">{p}</th>' for p in PROCESOS_LINEA])

            tabla_completa = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
.tabla-container {{ overflow:auto; max-height:600px; border:1px solid #ccc; border-radius:4px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ background-color:#fce5cd; color:#333; position:sticky; top:0; z-index:5; border-bottom:2px solid #ccc; }}
</style>
</head>
<body>
<div class="tabla-container">
<table>
<thead>
<tr>
<th style="padding:9px 7px; text-align:left; position:sticky; left:0; background-color:#fce5cd; z-index:6; min-width:135px;">Sub-Proyecto</th>
<th style="padding:9px 7px; text-align:left; position:sticky; left:135px; background-color:#fce5cd; z-index:6; min-width:85px;">Lote ID</th>
<th style="padding:9px 7px; text-align:center; min-width:90px;">F. Despacho</th>
<th style="padding:9px 7px; text-align:right; min-width:95px;">Kg Despacho</th>
<th style="padding:9px 7px; text-align:right; min-width:105px;">Orden Proceder</th>
<th style="padding:9px 7px; text-align:right; min-width:105px;">Kilos Cargados</th>
{encabezados_proc}
<th style="padding:9px 7px; text-align:center; min-width:85px; border-left:2px solid #bbb;">Avance Total</th>
</tr>
</thead>
<tbody>
{''.join(filas_html)}
</tbody>
</table>
</div>
</body>
</html>
"""
            # Renderizado en contenedor aislado garantizado
            components.html(tabla_completa, height=620, scrolling=False)

    # -------------------------------------------------------------
    # TAB 2: TORRE DE CONTROL & DESPACHOS
    # -------------------------------------------------------------
    with tab_torre_control:
        st.markdown(f"### 📅 Programa de Despacho (Desde {mes_seleccionado})")
        c_mes, c_sem = st.columns(2)
        with c_mes:
            df_mes = df_backlog_horizonte.groupby("Mes_Entrega", as_index=False)["Kg_Prog_Num"].sum().sort_values("Mes_Entrega")
            fig_mes = px.bar(df_mes, x="Mes_Entrega", y="Kg_Prog_Num", text_auto='.2s', title="Kilos Totales por Mes")
            fig_mes.update_traces(marker_color='#1f77b4')
            st.plotly_chart(fig_mes, use_container_width=True)
        with c_sem:
            df_sem = df_backlog_horizonte.groupby("Semana_Entrega", as_index=False)["Kg_Prog_Num"].sum().sort_values("Semana_Entrega")
            fig_sem = px.bar(df_sem, x="Semana_Entrega", y="Kg_Prog_Num", text_auto='.2s', title="Kilos Totales por Semana")
            fig_sem.update_traces(marker_color='#2ca02c')
            st.plotly_chart(fig_sem, use_container_width=True)

    # -------------------------------------------------------------
    # TAB 3: SIMULADOR POR ESTACIÓN (CARGA VS CAPACIDAD)
    # -------------------------------------------------------------
    with tab_sim_proceso:
        st.markdown("### 🎯 Balanceo de Capacidad & Simulación por Estación")
        proceso_foco = st.selectbox("Selecciona la estación a simular:", PROCESOS_LINEA, index=2)

        st.markdown(f"#### 👷 Dotación Semanal en **{proceso_foco}** (Operarios)")
        df_fila_proc = st.session_state.matriz_dotacion[st.session_state.matriz_dotacion["Proceso"] == proceso_foco][semanas_matriz]
        cfg_cols = {c: st.column_config.NumberColumn(c, min_value=0, max_value=60, step=1) for c in semanas_matriz}
        fila_editada = st.data_editor(df_fila_proc, column_config=cfg_cols, hide_index=True, use_container_width=True, key=f"ed_{proceso_foco}")
        
        for col_sem in semanas_matriz:
            st.session_state.matriz_dotacion.loc[st.session_state.matriz_dotacion["Proceso"] == proceso_foco, col_sem] = fila_editada[col_sem].values[0]

    # -------------------------------------------------------------
    # TAB 4: MATRIZ GLOBAL
    # -------------------------------------------------------------
    with tab_matriz_global:
        st.markdown(f"### 🗓️ Matriz Completa de Dotación (12 Procesos $\\times$ Semanas)")
        column_config_g = {"Proceso": st.column_config.TextColumn("Proceso", disabled=True)}
        for c_sem in semanas_matriz:
            column_config_g[c_sem] = st.column_config.NumberColumn(c_sem, min_value=0, max_value=60, step=1)

        matriz_edit_global = st.data_editor(
            st.session_state.matriz_dotacion[["Proceso"] + semanas_matriz],
            column_config=column_config_g, hide_index=True, use_container_width=True, key="ed_global"
        )
        st.session_state.matriz_dotacion.update(matriz_edit_global)

except Exception as e:
    st.error(f"Error procesando los datos: {e}")
