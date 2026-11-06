import streamlit as st
import pandas as pd
import smartsheet
import re
import unicodedata
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

st.set_page_config(page_title="APS - Control de Carga y Capacidad", layout="wide")

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

# Reglas de Precedencia y Umbrales (% mínimo del predecesor para habilitar)
COMPUERTAS_PROCESO = {
    "Ingeniería": [],
    "Oficina Técnica": [("Ingeniería", 0.50)],
    "CNCL": [("Oficina Técnica", 0.25)],
    "CNCPL": [("Oficina Técnica", 0.25)],
    "Talón": [("CNCL", 0.25)],
    "DoblezL": [("Talón", 0.10)],
    "Perforado": [("DoblezL", 0.20)],
    "Armado Soldadura": [("Perforado", 0.25), ("CNCPL", 0.25)], # Requiere ambos
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
# 2. MOTOR DE EVALUACIÓN DE COMPUERTAS Y CAUSA-RAÍZ
# -------------------------------------------------------------
def diagnosticar_bloqueos_subproyecto(df_subp_procesos):
    """
    Evalúa para un subproyecto en qué proceso está trabada la cadena física y por qué.
    """
    # 1. Rellenar mapa de avance por proceso
    avances = {}
    pendientes = {}
    for proc in PROCESOS_LINEA:
        fila = df_subp_procesos[df_subp_procesos["Proceso"] == proc]
        if not fila.empty:
            req = fila["Kg_Requeridos"].values[0]
            av = fila["Kg_Avanzados"].values[0]
            avances[proc] = (av / req) if req > 0 else 1.0
            pendientes[proc] = fila["Kg_Pendientes"].values[0]
        else:
            avances[proc] = 1.0
            pendientes[proc] = 0.0

    # 2. Rastrear de adelante hacia atrás la estación que traba el avance
    tapon_proceso = "Embalaje"
    causa_tapon = "En flujo normal hacia entrega"
    estado_general = "🟢 FLUYENDO"

    for proc in PROCESOS_LINEA:
        # Si tiene kilos pendientes y no ha terminado al 100%
        if pendientes[proc] > 0 and avances[proc] < 1.0:
            predecesores = COMPUERTAS_PROCESO.get(proc, [])
            bloqueado = False
            motivos = []

            for pred, umbral in predecesores:
                av_pred = avances.get(pred, 0.0)
                if av_pred < umbral:
                    bloqueado = True
                    motivos.append(f"{pred} lleva {av_pred*100:.0f}% (requiere {umbral*100:.0f}%)")

            if bloqueado:
                tapon_proceso = proc
                causa_tapon = f"Aguas arriba: {', '.join(motivos)}"
                estado_general = "🔴 BLOQUEADO EN PLANTA"
                break
            else:
                # Está habilitado pero tiene material en cola
                if avances[proc] == 0:
                    tapon_proceso = proc
                    causa_tapon = f"Habilitado con {pendientes[proc]:,.0f} kg en cola (sin iniciar)"
                    estado_general = "🟡 EN ESPERA DE OPERARIOS"
                    break
                else:
                    tapon_proceso = proc
                    causa_tapon = f"En fabricación activa ({avances[proc]*100:.0f}% avance, restan {pendientes[proc]:,.0f} kg)"
                    estado_general = "🔵 EN PROCESO"
                    break

    return {
        "Punto_Freno": tapon_proceso,
        "Causa_Raiz": causa_tapon,
        "Estado_Flujo": estado_general,
        "Avances_Dict": avances,
        "Pendientes_Dict": pendientes
    }

# -------------------------------------------------------------
# 3. CONTROLES Y EJECUCIÓN PRINCIPAL
# -------------------------------------------------------------
st.sidebar.header("⚙️ Parámetros de Operación")
horas_sem = st.sidebar.number_input("Horas turno / semana por operario", value=44, step=1)
eficiencia = st.sidebar.slider("Eficiencia Operativa / OEE (%)", 50, 100, 85) / 100.0

st.title("🏭 APS Manufactura: Diagnóstico de Flujo & Causa Raíz")

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

    df_backlog_activo["Fecha_DT"] = pd.to_datetime(df_backlog_activo[col_fecha_prog], errors='coerce')
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

    # Productividad (kg/HH)
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

    # Normalizar procesos
    df_procesos = normalizar_procesos(df_procesos_raw)
    df_carga = df_procesos[df_procesos["Subproyecto"].isin(subproyectos_horizonte)].copy()
    df_carga["Productividad"] = df_carga["Proceso"].map(lambda p: ratios_productividad.get(p, 80.0))
    df_carga["HH_Pendientes"] = df_carga["Kg_Pendientes"] / df_carga["Productividad"]

    # Repartir los kilos pendientes de cada subproyecto entre sus lotes activos
    kg_tot_subp = df_backlog_horizonte.groupby(col_sub_backlog)["Kg_Prog_Num"].sum().to_dict()
    registros_lote_proc = []
    for _, lote in df_backlog_horizonte.iterrows():
        subp = lote[col_sub_backlog]
        kg_lote = lote["Kg_Prog_Num"]
        tot_subp = kg_tot_subp.get(subp, 0)
        factor = (kg_lote / tot_subp) if tot_subp > 0 else 0
        
        cargas_subp = df_carga[df_carga["Subproyecto"] == subp]
        for _, cp in cargas_subp.iterrows():
            proc_name = cp["Proceso"]
            kg_lote_proc = cp["Kg_Pendientes"] * factor
            registros_lote_proc.append({
                "ID_Lote": lote[col_id_fila],
                "Subproyecto": subp,
                "Fecha_DT": lote["Fecha_DT"],
                "Fecha_Prog": lote[col_fecha_prog],
                "Mes_Entrega": lote["Mes_Entrega"],
                "Semana_Entrega": lote["Semana_Entrega"],
                "Proceso": proc_name,
                "Kg_Asignados": kg_lote_proc
            })
    df_demanda_detallada = pd.DataFrame(registros_lote_proc)

    # Matriz Semanal de Dotación
    semanas_horizonte = sorted(df_backlog_horizonte["Semana_Entrega"].unique().tolist())
    semanas_matriz = semanas_horizonte[:14] if len(semanas_horizonte) >= 6 else semanas_horizonte + [f"Sem+{i:02d}" for i in range(1, 7 - len(semanas_horizonte))]

    if "matriz_dotacion" not in st.session_state:
        datos_matriz = {"Proceso": PROCESOS_LINEA}
        for sem in semanas_matriz:
            datos_matriz[sem] = [DOTACION_BASE_DEFAULT.get(p, 4) for p in PROCESOS_LINEA]
        st.session_state.matriz_dotacion = pd.DataFrame(datos_matriz)
    else:
        for sem in semanas_matriz:
            if sem not in st.session_state.matriz_dotacion.columns:
                st.session_state.matriz_dotacion[sem] = [DOTACION_BASE_DEFAULT.get(p, 4) for p in PROCESOS_LINEA]

    # Diagnosticar bloqueos por subproyecto
    dict_diagnosticos = {}
    for subp in subproyectos_horizonte:
        df_subp_p = df_carga[df_carga["Subproyecto"] == subp]
        dict_diagnosticos[subp] = diagnosticar_bloqueos_subproyecto(df_subp_p)

    # -------------------------------------------------------------
    # 4. PESTAÑAS DE TRABAJO
    # -------------------------------------------------------------
    tab_diagnostico, tab_torre_control, tab_sim_proceso, tab_matriz_global = st.tabs([
        "🚨 Diagnóstico Causa-Raíz (Por Lote)",
        "🚀 Torre de Control & Entregas",
        "🎯 Simulador por Proceso",
        "🗓️ Matriz Global (12 Procesos)"
    ])

    # -------------------------------------------------------------
    # TAB 1: DIAGNÓSTICO CAUSA-RAÍZ SUBPROYECTO POR SUBPROYECTO
    # -------------------------------------------------------------
    with tab_diagnostico:
        st.markdown(f"### 🔍 ¿Dónde está detenida la planta y por qué? (Desde {mes_seleccionado})")
        st.caption("Evaluación lote por lote según el avance real acumulado y las compuertas mínimas de precedencia física.")

        # Armar la tabla analítica consolidada
        filas_diagnostico = []
        ahora = datetime.now()

        for _, lote in df_backlog_horizonte.iterrows():
            subp = str(lote[col_sub_backlog]).strip()
            id_lote = str(lote[col_id_fila])
            fecha_entrega = lote["Fecha_DT"]
            kg_lote = lote["Kg_Prog_Num"]
            semana_e = lote["Semana_Entrega"]

            diag = dict_diagnosticos.get(subp, {
                "Punto_Freno": "Sin Carga", "Causa_Raiz": "Completado", "Estado_Flujo": "🟢 OK"
            })

            dias_disp = (fecha_entrega - ahora).days
            semanas_disp = max(0.1, round(dias_disp / 7.0, 1))

            filas_diagnostico.append({
                "ID Lote": id_lote,
                "Subproyecto": subp,
                "Fecha Despacho": fecha_entrega.strftime("%d/%m/%Y"),
                "Semana Salida": semana_e,
                "Kilos Lote": f"{kg_lote:,.0f} kg",
                "Semanas Restantes": semanas_disp,
                "Estación Detenida / Crítica": diag["Punto_Freno"],
                "Estado del Flujo": diag["Estado_Flujo"],
                "Causa Raíz del Bloqueo": diag["Causa_Raiz"]
            })

        df_diag_tabla = pd.DataFrame(filas_diagnostico)

        # Filtros rápidos para el planificador
        col_f1, col_f2 = st.columns([2, 3])
        with col_f1:
            filtro_estado = st.multiselect(
                "Filtrar por estado del flujo:",
                options=["🔴 BLOQUEADO EN PLANTA", "🟡 EN ESPERA DE OPERARIOS", "🔵 EN PROCESO", "🟢 FLUYENDO"],
                default=["🔴 BLOQUEADO EN PLANTA", "🟡 EN ESPERA DE OPERARIOS"]
            )
        with col_f2:
            st.info("💡 **🔴 BLOQUEADO:** Falta compuerta de predecesor | **🟡 EN ESPERA:** Habilitado con material en piso | **🔵 EN PROCESO:** Avanzando")

        df_diag_mostrar = df_diag_tabla[df_diag_tabla["Estado del Flujo"].isin(filtro_estado)] if filtro_estado else df_diag_tabla
        st.dataframe(df_diag_mostrar, use_container_width=True)

        st.markdown("---")
        st.markdown("#### 🔬 Radiografía de Avance por Proceso de un Subproyecto")
        subp_analizar = st.selectbox("Selecciona un subproyecto para ver el estado de sus 12 estaciones:", subproyectos_horizonte)
        
        diag_subp = dict_diagnosticos.get(subp_analizar)
        if diag_subp:
            cols_procs = st.columns(6)
            for idx, proc in enumerate(PROCESOS_LINEA):
                col_target = cols_procs[idx % 6]
                av_pct = diag_subp["Avances_Dict"].get(proc, 0.0) * 100
                pend_kg = diag_subp["Pendientes_Dict"].get(proc, 0.0)
                
                icono = "🟢" if av_pct >= 100 else ("🔵" if av_pct > 0 else "⚪")
                if proc == diag_subp["Punto_Freno"]:
                    icono = "🚨"

                col_target.metric(
                    f"{icono} {proc}",
                    f"{av_pct:.0f}%",
                    f"{pend_kg:,.0f} kg pend." if pend_kg > 0 else "Completado"
                )

    # -------------------------------------------------------------
    # TAB 2: TORRE DE CONTROL & ENTREGAS
    # -------------------------------------------------------------
    with tab_torre_control:
        st.markdown(f"### 📅 Programa de Despacho (Desde {mes_seleccionado})")
        col_filtro_t2, _ = st.columns([2, 3])
        with col_filtro_t2:
            filtro_proceso_t2 = st.selectbox(
                "Filtrar visión de entregas por proceso:",
                ["Consolidado Planta (Despachos Generales)"] + PROCESOS_LINEA,
                index=0
            )

        c_mes, c_sem = st.columns(2)
        if filtro_proceso_t2 == "Consolidado Planta (Despachos Generales)":
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
        else:
            df_dem_proc = df_demanda_detallada[df_demanda_detallada["Proceso"] == filtro_proceso_t2]
            with c_mes:
                df_mes_p = df_dem_proc.groupby("Mes_Entrega", as_index=False)["Kg_Asignados"].sum().sort_values("Mes_Entrega")
                fig_mes_p = px.bar(df_mes_p, x="Mes_Entrega", y="Kg_Asignados", text_auto='.2s', title=f"Kilos en {filtro_proceso_t2} por Mes")
                fig_mes_p.update_traces(marker_color='#1f77b4')
                st.plotly_chart(fig_mes_p, use_container_width=True)
            with c_sem:
                df_sem_p = df_dem_proc.groupby("Semana_Entrega", as_index=False)["Kg_Asignados"].sum().sort_values("Semana_Entrega")
                fig_sem_p = px.bar(df_sem_p, x="Semana_Entrega", y="Kg_Asignados", text_auto='.2s', title=f"Kilos en {filtro_proceso_t2} por Semana")
                fig_sem_p.update_traces(marker_color='#ff7f0e')
                st.plotly_chart(fig_sem_p, use_container_width=True)

    # -------------------------------------------------------------
    # TAB 3: SIMULADOR POR PROCESO (CARGA VS CAPACIDAD)
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

        ratio_foco = ratios_productividad.get(proceso_foco, 80.0)
        df_dem_foco = df_demanda_detallada[df_demanda_detallada["Proceso"] == proceso_foco]
        demanda_sem_dict = df_dem_foco.groupby("Semana_Entrega")["Kg_Asignados"].sum().to_dict()

        datos_balance = []
        for sem in semanas_matriz:
            demanda_kg = demanda_sem_dict.get(sem, 0.0)
            operarios_sem = fila_editada[sem].values[0] if sem in fila_editada.columns else 4
            capacidad_hh = operarios_sem * horas_sem * eficiencia
            capacidad_kg = capacidad_hh * ratio_foco
            es_cuello = demanda_kg > capacidad_kg and demanda_kg > 0
            
            datos_balance.append({
                "Semana": sem,
                "Demanda_Kg": demanda_kg,
                "Capacidad_Kg": capacidad_kg,
                "Color": "#d62728" if es_cuello else "#1f77b4"
            })
        df_balance = pd.DataFrame(datos_balance)

        col_g1, col_g2 = st.columns([2, 3])
        with col_g1:
            df_mes_foco = df_dem_foco.groupby("Mes_Entrega", as_index=False)["Kg_Asignados"].sum().sort_values("Mes_Entrega")
            fig_m = px.bar(df_mes_foco, x="Mes_Entrega", y="Kg_Asignados", text_auto='.2s', title=f"Kilos Mensuales en {proceso_foco}")
            st.plotly_chart(fig_m, use_container_width=True)

        with col_g2:
            fig_balance = go.Figure()
            fig_balance.add_trace(go.Bar(
                x=df_balance["Semana"], y=df_balance["Demanda_Kg"],
                marker_color=df_balance["Color"], name="Demanda (Kg)",
                text=[f"{v:,.0f}" if v > 0 else "" for v in df_balance["Demanda_Kg"]],
                textposition='auto'
            ))
            fig_balance.add_trace(go.Scatter(
                x=df_balance["Semana"], y=df_balance["Capacidad_Kg"],
                mode='lines+markers', name="Límite Capacidad (Kg)",
                line=dict(color='black', width=3, dash='dash')
            ))
            fig_balance.update_layout(title=f"Carga vs Capacidad en {proceso_foco} (Rojo = Cuello de Botella)", xaxis_title="Semana", yaxis_title="Kilos")
            st.plotly_chart(fig_balance, use_container_width=True)

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
