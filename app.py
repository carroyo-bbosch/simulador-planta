import streamlit as st
import pandas as pd
import smartsheet
import re
import unicodedata
import plotly.express as px
from datetime import datetime

st.set_page_config(page_title="APS - Control de Carga y Capacidad", layout="wide")

# -------------------------------------------------------------
# 0. CONFIGURACIÓN BASE Y PROCESOS
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
# 1. CARGA Y NORMALIZACIÓN DE SMARTSHEET
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
    for _, fila in df_proc.iterrows():
        subproyecto = str(fila.get(col_subproj, "")).strip()
        if not subproyecto or subproyecto in ["None", "nan"]: continue
        
        kilos_cargados = pd.to_numeric(fila.get(col_cargados, 0), errors='coerce') or 0
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
            
            if kg_necesidad > 0 or kg_av > 0:
                registros.append({
                    "Subproyecto": subproyecto,
                    "Proceso": proc,
                    "Kg_Requeridos": kg_necesidad,
                    "Kg_Avanzados": kg_av,
                    "Kg_Pendientes": kg_pendientes
                })
                
    return pd.DataFrame(registros)

# -------------------------------------------------------------
# 2. CONTROLES GENERALES DE PLANTA
# -------------------------------------------------------------
st.sidebar.header("⚙️ Parámetros Generales")
horas_sem = st.sidebar.number_input("Horas turno / semana por operario", value=44, step=1)
eficiencia = st.sidebar.slider("Eficiencia Operativa / OEE (%)", 50, 100, 85) / 100.0

st.title("🏭 APS Manufactura: Torres de Transmisión y Subestaciones")

if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()
    st.rerun()

try:
    with st.spinner("Sincronizando información de planta..."):
        df_backlog_raw = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
        df_procesos_raw = cargar_hoja(st.secrets["SHEET_ID_PROCESOS"])
        df_prod_raw = cargar_hoja(st.secrets["SHEET_ID_PRODUCTIVIDAD"])

    # 1. Filtro estricto de Backlog: Fecha programada activa y despacho real vacío
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

    # Fechas, Meses y Semanas
    df_backlog_activo["Fecha_DT"] = pd.to_datetime(df_backlog_activo[col_fecha_prog], errors='coerce')
    df_backlog_activo = df_backlog_activo.dropna(subset=["Fecha_DT"]).sort_values("Fecha_DT")
    df_backlog_activo["Mes_Entrega"] = df_backlog_activo["Fecha_DT"].dt.strftime("%Y-%m")
    df_backlog_activo["Semana_Entrega"] = df_backlog_activo["Fecha_DT"].dt.strftime("%Y-Sem%U")
    df_backlog_activo["Kg_Prog_Num"] = pd.to_numeric(df_backlog_activo[col_kg_prog], errors='coerce').fillna(0)

    subproyectos_vivos = df_backlog_activo[col_sub_backlog].dropna().astype(str).str.strip().unique()

    # 2. Cargar Productividad (kg/HH)
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

    # 3. Normalizar procesos y asociar Productividad
    df_procesos = normalizar_procesos(df_procesos_raw)
    df_carga = df_procesos[df_procesos["Subproyecto"].isin(subproyectos_vivos)].copy()
    df_carga["Productividad"] = df_carga["Proceso"].map(lambda p: ratios_productividad.get(p, 80.0))
    df_carga["HH_Pendientes"] = df_carga["Kg_Pendientes"] / df_carga["Productividad"]

    # -------------------------------------------------------------
    # 4. MATRIZ EDITABLE DE DOTACIÓN (PROCESO X SEMANA)
    # -------------------------------------------------------------
    # Obtener semanas activas (mínimo las próximas 8 semanas para horizonte de simulación)
    semanas_backlog = sorted(df_backlog_activo["Semana_Entrega"].unique().tolist())
    if len(semanas_backlog) < 6:
        semanas_horizonte = semanas_backlog + [f"Futuro-Sem+{i}" for i in range(1, 7 - len(semanas_backlog))]
    else:
        semanas_horizonte = semanas_backlog[:10]

    # Inicializar matriz en sesión para no perder cambios al editar
    if "matriz_dotacion" not in st.session_state:
        datos_matriz = {"Proceso": PROCESOS_LINEA}
        for sem in semanas_horizonte:
            datos_matriz[sem] = [DOTACION_BASE_DEFAULT.get(p, 4) for p in PROCESOS_LINEA]
        st.session_state.matriz_dotacion = pd.DataFrame(datos_matriz)
    else:
        # Asegurar que todas las semanas actuales del horizonte existan en la matriz
        for sem in semanas_horizonte:
            if sem not in st.session_state.matriz_dotacion.columns:
                st.session_state.matriz_dotacion[sem] = [DOTACION_BASE_DEFAULT.get(p, 4) for p in PROCESOS_LINEA]

    # -------------------------------------------------------------
    # 5. PESTAÑAS PRINCIPALES
    # -------------------------------------------------------------
    tab_ejecutiva, tab_matriz, tab_procesos, tab_prod = st.tabs([
        "🚀 Torre de Control & Entregas",
        "🗓️ Matriz de Capacidad (Semanas)",
        "🔍 Detalle por Proceso",
        "📋 Productividad (kg/HH)"
    ])

    # -------------------------------------------------------------
    # TAB 2: MATRIZ DE CAPACIDAD (Se ejecuta antes para alimentar Tab 1)
    # -------------------------------------------------------------
    with tab_matriz:
        st.markdown("### 🗓️ Matriz de Dotación de Operarios (Proceso $\\times$ Semana)")
        st.caption("Modifica directamente las celdas para simular refuerzos, horas extras o rotación de operarios semana a semana.")
        
        column_config = {
            "Proceso": st.column_config.TextColumn("Proceso Productivo", disabled=True),
        }
        for col_sem in semanas_horizonte:
            column_config[col_sem] = st.column_config.NumberColumn(col_sem, min_value=0, max_value=60, step=1)

        matriz_editada = st.data_editor(
            st.session_state.matriz_dotacion[["Proceso"] + semanas_horizonte],
            column_config=column_config,
            hide_index=True,
            use_container_width=True,
            key="editor_matriz"
        )
        st.session_state.matriz_dotacion.update(matriz_editada)

        # Cálculo de dotación promedio semanal por proceso para el simulador
        dotacion_promedio = matriz_editada.set_index("Proceso")[semanas_horizonte].mean(axis=1).to_dict()

    # -------------------------------------------------------------
    # TAB 1: TORRE DE CONTROL (VISTA EJECUTIVA)
    # -------------------------------------------------------------
    with tab_ejecutiva:
        st.markdown("### 📅 Distribución Temporal de Despachos Programados")
        c_mes, c_sem = st.columns(2)

        with c_mes:
            df_mes = df_backlog_activo.groupby("Mes_Entrega", as_index=False)["Kg_Prog_Num"].sum().sort_values("Mes_Entrega")
            fig_mes = px.bar(
                df_mes, x="Mes_Entrega", y="Kg_Prog_Num", 
                text_auto='.2s',
                title="Kilos Programados por Mes",
                labels={"Mes_Entrega": "Mes", "Kg_Prog_Num": "Kilos Totales"}
            )
            fig_mes.update_traces(marker_color='#1f77b4')
            st.plotly_chart(fig_mes, use_container_width=True)

        with c_sem:
            df_sem = df_backlog_activo.groupby("Semana_Entrega", as_index=False)["Kg_Prog_Num"].sum().sort_values("Semana_Entrega")
            fig_sem = px.bar(
                df_sem, x="Semana_Entrega", y="Kg_Prog_Num",
                text_auto='.2s',
                title="Kilos Programados por Semana (Salida de Planta)",
                labels={"Semana_Entrega": "Semana Calendario", "Kg_Prog_Num": "Kilos"}
            )
            fig_sem.update_traces(marker_color='#2ca02c')
            st.plotly_chart(fig_sem, use_container_width=True)

        st.markdown("---")
        st.markdown("### 🚨 Diagnóstico de Cumplimiento de Entregas por Proyecto")
        st.caption("Compara el tiempo disponible hasta la fecha del cliente con las semanas requeridas de trabajo según la capacidad de la matriz.")

        # Capacidad semanal calculada por proceso
        capacidad_semanal_hh = {
            p: dotacion_promedio.get(p, 4) * horas_sem * eficiencia 
            for p in PROCESOS_LINEA
        }

        # Evaluación proyecto por proyecto / lote
        ahora = datetime.now()
        analisis_lotes = []

        for _, lote in df_backlog_activo.iterrows():
            subp = str(lote[col_sub_backlog]).strip()
            id_lote = str(lote[col_id_fila])
            fecha_entrega = lote["Fecha_DT"]
            kg_lote = lote["Kg_Prog_Num"]
            semana_e = lote["Semana_Entrega"]

            # Días y semanas disponibles
            dias_disponibles = (fecha_entrega - ahora).days
            semanas_disp = max(0.2, round(dias_disponibles / 7.0, 1))

            # Kilos pendientes de este subproyecto en planta
            carga_subp = df_carga[df_carga["Subproyecto"] == subp]
            
            # Encontrar el proceso más crítico (con mayor tiempo de evacuación)
            peor_proceso = "Sin Carga"
            max_semanas_necesarias = 0.0

            for _, fila_c in carga_subp.iterrows():
                proc_c = fila_c["Proceso"]
                hh_nec = fila_c["HH_Pendientes"]
                cap_hh = capacidad_semanal_hh.get(proc_c, 1)
                semanas_proc = round(hh_nec / cap_hh, 1) if cap_hh > 0 else 0
                if semanas_proc > max_semanas_necesarias:
                    max_semanas_necesarias = semanas_proc
                    peor_proceso = proc_c

            # Diagnóstico de viabilidad
            brecha = round(semanas_disp - max_semanas_necesarias, 1)
            if dias_disponibles < 0:
                estado = "🔴 VENCIDO (Fecha Pasada)"
            elif brecha < 0:
                estado = f"🔴 EN RIESGO ({abs(brecha)} sem atraso)"
            elif brecha <= 1.0:
                estado = "🟡 AL LÍMITE (Buffer crítico)"
            else:
                estado = "🟢 A TIEMPO"

            analisis_lotes.append({
                "ID Lote": id_lote,
                "Subproyecto": subp,
                "Fecha Entrega": fecha_entrega.strftime("%d/%m/%Y"),
                "Semana": semana_e,
                "Kilos Programados": f"{kg_lote:,.0f} kg",
                "Semanas Disponibles": semanas_disp,
                "Semanas Requeridas": max_semanas_necesarias,
                "Estación Cuello Botella": peor_proceso,
                "Diagnóstico": estado
            })

        df_analisis = pd.DataFrame(analisis_lotes)

        # Métricas de resumen
        total_lotes = len(df_analisis)
        lotes_riesgo = len(df_analisis[df_analisis["Diagnóstico"].str.contains("🔴")])
        lotes_limite = len(df_analisis[df_analisis["Diagnóstico"].str.contains("🟡")])

        m1, m2, m3 = st.columns(3)
        m1.metric("Total Lotes Activos", total_lotes)
        m2.metric("Lotes en Riesgo de Incumplimiento", lotes_riesgo, delta=f"-{lotes_riesgo}" if lotes_riesgo > 0 else "0", delta_color="inverse")
        m3.metric("Lotes al Límite de Capacidad", lotes_limite)

        st.dataframe(
            df_analisis.sort_values(by="Semanas Requeridas", ascending=False),
            use_container_width=True
        )

    # -------------------------------------------------------------
    # TAB 3: DETALLE POR PROCESO (PESTAÑA ESPECÍFICA)
    # -------------------------------------------------------------
    with tab_procesos:
        st.markdown("### 🔍 Explorador Individual por Proceso")
        proceso_seleccionado = st.selectbox("Selecciona el proceso a auditar:", PROCESOS_LINEA, index=10) # 10 = Galvanizado

        col_p1, col_p2 = st.columns([2, 3])
        
        carga_proc = df_carga[df_carga["Proceso"] == proceso_seleccionado]
        kg_totales_proc = carga_proc["Kg_Pendientes"].sum()
        ratio_p = ratios_productividad.get(proceso_seleccionado, 80.0)
        hh_totales_proc = kg_totales_proc / ratio_p
        operarios_p = dotacion_promedio.get(proceso_seleccionado, 4)
        cap_sem_proc = operarios_p * horas_sem * eficiencia

        with col_p1:
            st.metric("Kilos en Cola", f"{kg_totales_proc:,.0f} kg")
            st.metric("HH de Esfuerzo Restante", f"{hh_totales_proc:,.0f} HH")
            st.metric("Productividad Asignada", f"{ratio_p} kg/HH")
            st.metric("Capacidad Semanal (Promedio)", f"{cap_sem_proc:,.0f} HH/sem")
            semanas_evac = round(hh_totales_proc / cap_sem_proc, 1) if cap_sem_proc > 0 else 0
            st.metric("Semanas para Evacuar Cola Actual", f"{semanas_evac} semanas")

        with col_p2:
            st.markdown(f"#### Kilos Pendientes en **{proceso_seleccionado}** por Subproyecto")
            if not carga_proc.empty and kg_totales_proc > 0:
                fig_subp = px.bar(
                    carga_proc.sort_values("Kg_Pendientes", ascending=False).head(10),
                    x="Kg_Pendientes", y="Subproyecto", orientation='h',
                    title=f"Top 10 Subproyectos con mayor cola en {proceso_seleccionado}"
                )
                fig_subp.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_subp, use_container_width=True)
            else:
                st.info(f"No hay kilos pendientes en {proceso_seleccionado} para los subproyectos activos.")

    # -------------------------------------------------------------
    # TAB 4: PRODUCTIVIDAD & AUDITORÍA
    # -------------------------------------------------------------
    with tab_prod:
        st.markdown("### 📋 Ratios Oficiales de Productividad (kg/HH)")
        st.caption("Leídos directamente desde la columna `procesos simulador` y `Kg/hh` de Smartsheet.")
        df_prod_show = pd.DataFrame([
            {"Proceso": p, "Kg/HH": ratios_productividad.get(p, "80.0 (Fallback)")}
            for p in PROCESOS_LINEA
        ])
        st.dataframe(df_prod_show, use_container_width=True)

except Exception as e:
    st.error(f"Error procesando los datos: {e}")
