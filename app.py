import streamlit as st
import pandas as pd
import smartsheet
import re
import plotly.express as px

st.set_page_config(page_title="Simulador de Planta - Carga Activa", layout="wide")

# -------------------------------------------------------------
# 1. FUNCIONES DE CONEXIÓN Y NORMALIZACIÓN
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
    
    procesos_detectados = set()
    mapa_cols = {} 

    for c in cols:
        c_low = c.lower().strip()
        if c_low.startswith("estimado"):
            proc = re.sub(r"^estimados?\s*", "", c, flags=re.IGNORECASE).strip()
            procesos_detectados.add(proc)
            mapa_cols.setdefault(proc, {})['estimado'] = c
        elif "kilos lanzados" in c_low:
            proc = re.sub(r"^kilos\s+lanzados\s*", "", c, flags=re.IGNORECASE).strip()
            procesos_detectados.add(proc)
            mapa_cols.setdefault(proc, {})['lanzado'] = c
        elif c_low.startswith("kilos ") and not any(k in c_low for k in ["lanzados", "orden", "cargados"]):
            proc = re.sub(r"^kilos\s+", "", c, flags=re.IGNORECASE).strip()
            procesos_detectados.add(proc)
            mapa_cols.setdefault(proc, {})['avance'] = c

    registros = []
    for _, fila in df_proc.iterrows():
        subproyecto = str(fila.get(col_subproj, "")).strip()
        if not subproyecto or subproyecto in ["None", "nan"]: 
            continue
        
        kilos_cargados = pd.to_numeric(fila.get(col_cargados, 0), errors='coerce') or 0
        es_estimado = (kilos_cargados <= 0)
        
        for proc in procesos_detectados:
            col_est = mapa_cols.get(proc, {}).get('estimado')
            col_lanz = mapa_cols.get(proc, {}).get('lanzado')
            col_av = mapa_cols.get(proc, {}).get('avance')
            
            kg_est = pd.to_numeric(fila.get(col_est, 0), errors='coerce') or 0 if col_est else 0
            kg_lanz = pd.to_numeric(fila.get(col_lanz, 0), errors='coerce') or 0 if col_lanz else 0
            kg_av = pd.to_numeric(fila.get(col_av, 0), errors='coerce') or 0 if col_av else 0
            
            kg_necesidad = kg_est if es_estimado else kg_lanz
            kg_pendientes = max(0.0, kg_necesidad - kg_av)
            
            if kg_necesidad > 0 or kg_av > 0:
                registros.append({
                    "Subproyecto": subproyecto,
                    "Proceso": proc,
                    "Kg_Pendientes": kg_pendientes
                })
                
    return pd.DataFrame(registros)

# -------------------------------------------------------------
# 2. CONTROLES LATERALES (DOTACIÓN)
# -------------------------------------------------------------
st.sidebar.header("⚙️ Jornada Laboral")
horas_sem = st.sidebar.number_input("Horas laborables / semana", value=44, step=1)
eficiencia = st.sidebar.slider("Eficiencia (OEE) %", 50, 100, 85) / 100.0

st.sidebar.header("👷 Distribución de Operarios")
mod_cnc = st.sidebar.slider("Corte CNC", 0, 30, 6)
mod_sold = st.sidebar.slider("Armado / Soldadura", 0, 40, 14)
mod_galv = st.sidebar.slider("Galvanizado", 0, 30, 8)

dotacion = {
    "CNC": mod_cnc,
    "Soldadura": mod_sold,
    "Galvanizado": mod_galv
}

# Ratios estándar de productividad (kg/HH)
ratios = {
    "CNC": 120.0,
    "Soldadura": 45.0,
    "Galvanizado": 160.0
}

# -------------------------------------------------------------
# 3. EJECUCIÓN PRINCIPAL
# -------------------------------------------------------------
st.title("🏭 Monitor de Carga Viva y Simulación")

if st.button("🔄 Refrescar Datos de Smartsheet"):
    st.cache_data.clear()
    st.rerun()

try:
    with st.spinner("Descargando y filtrando backlog activo..."):
        df_backlog_raw = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
        df_procesos_raw = cargar_hoja(st.secrets["SHEET_ID_PROCESOS"])
        
    cols_backlog = df_backlog_raw.columns.tolist()
    
    # Detección de columnas clave en Backlog
    col_sub_backlog = next((c for c in cols_backlog if "sub-proyecto" in c.lower() or "subproyecto" in c.lower()), None)
    col_fecha_prog = next((c for c in cols_backlog if "programad" in c.lower() and "fecha" in c.lower()), None)
    if not col_fecha_prog:
        col_fecha_prog = next((c for c in cols_backlog if "fecha" in c.lower()), None)
        
    col_despacho_real = next((c for c in cols_backlog if "despacho" in c.lower() and ("real" in c.lower() or "fecha" in c.lower())), None)

    # Filtrar solo lotes activos (con fecha programada y sin despacho real)
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

    subproyectos_vivos = df_backlog_activo[col_sub_backlog].dropna().astype(str).str.strip().unique()

    # Normalizar hoja de procesos y filtrar solo los que siguen en fabricación
    df_procesos = normalizar_procesos(df_procesos_raw)
    df_carga = df_procesos[df_procesos["Subproyecto"].isin(subproyectos_vivos)].copy()

    # Cálculo de HH Pendientes
    def asignar_ratio(nombre_proceso):
        for k, v in ratios.items():
            if k.lower() in str(nombre_proceso).lower():
                return v
        return 100.0

    df_carga["Productividad"] = df_carga["Proceso"].apply(asignar_ratio)
    df_carga["HH_Pendientes"] = df_carga["Kg_Pendientes"] / df_carga["Productividad"]

    # Agrupación por Proceso (sintaxis compacta de una sola línea)
    resumen = df_carga.groupby("Proceso", as_index=False)[["Kg_Pendientes", "HH_Pendientes"]].sum()

    def asignar_operarios(nombre_proceso):
        for k, v in dotacion.items():
            if k.lower() in str(nombre_proceso).lower():
                return v
        return 0

    resumen["Operarios"] = resumen["Proceso"].apply(asignar_operarios)
    resumen["Capacidad_HH_Sem"] = resumen["Operarios"] * horas_sem * eficiencia

    def calc_semanas(row):
        return round(row["HH_Pendientes"] / row["Capacidad_HH_Sem"], 1) if row["Capacidad_HH_Sem"] > 0 else 0.0

    def calc_saturacion(row):
        cap_2sem = row["Capacidad_HH_Sem"] * 2
        return round((row["HH_Pendientes"] / cap_2sem) * 100, 1) if cap_2sem > 0 else 0.0

    resumen["Semanas_Cola"] = resumen.apply(calc_semanas, axis=1)
    resumen["Saturacion_%"] = resumen.apply(calc_saturacion, axis=1)

    def diagnostico(sat):
        if sat > 100:
            return "🔴 CUELLO DE BOTELLA"
        elif sat >= 80:
            return "🟡 CARGA ALTA"
        return "🟢 OK"

    resumen["Diagnostico"] = resumen["Saturacion_%"].apply(diagnostico)

    # -------------------------------------------------------------
    # 4. VISUALIZACIÓN
    # -------------------------------------------------------------
    m1, m2, m3 = st.columns(3)
    m1.metric("Kilos Reales en Cola", f"{df_carga['Kg_Pendientes'].sum():,.0f} kg")
    m2.metric("Lotes / Despachos Pendientes", len(df_backlog_activo))
    m3.metric("Subproyectos Activos en Taller", len(subproyectos_vivos))

    st.markdown("---")
    
    col_izq, col_der = st.columns([3, 2])
    with col_izq:
        st.markdown("### 📊 Demanda (HH) vs Capacidad Semanal")
        fig = px.bar(
            resumen[resumen["Operarios"] > 0],
            x="Proceso",
            y=["HH_Pendientes", "Capacidad_HH_Sem"],
            barmode="group",
            labels={"value": "Horas Hombre", "variable": "Indicador"}
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_der:
        st.markdown("### 📌 Filtros de Auditoría")
        st.info(f"**Columna Fecha Programada:** `{col_fecha_prog}`")
        st.info(f"**Columna Despacho Real:** `{col_despacho_real if col_despacho_real else 'No detectada'}`")
        st.write(f"Filas totales Backlog: **{len(df_backlog_raw)}**")
        st.write(f"Filas activas a fabricar: **{len(df_backlog_activo)}**")

    st.markdown("### 📋 Diagnóstico por Estación de Trabajo")
    st.dataframe(
        resumen[["Proceso", "Operarios", "Kg_Pendientes", "Capacidad_HH_Sem", "Semanas_Cola", "Diagnostico"]], 
        use_container_width=True
    )

except Exception as e:
    st.error(f"Error procesando la información: {e}")
