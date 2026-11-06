import streamlit as st
import pandas as pd
import smartsheet
import re
import plotly.express as px

st.set_page_config(page_title="Simulador de Planta", layout="wide")

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
        if not subproyecto or subproyecto == "None": continue
        
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
# 2. INTERFAZ: CONTROLES LATERALES
# -------------------------------------------------------------
st.sidebar.header("⚙️ Parámetros de Planta")
horas_sem = st.sidebar.number_input("Horas laborables/semana", value=44)
eficiencia = st.sidebar.slider("Eficiencia (OEE) %", 50, 100, 85) / 100.0

st.sidebar.header("👷 Distribución de Operarios")
# Nota: Puedes ajustar los nombres de estos procesos para que calcen con los tuyos
mod_cnc = st.sidebar.slider("CNC", 1, 20, 6)
mod_sold = st.sidebar.slider("Soldadura", 1, 30, 14)
mod_galv = st.sidebar.slider("Galvanizado", 1, 20, 8)

dotacion = {
    "CNC": mod_cnc,
    "Soldadura": mod_sold,
    "Galvanizado": mod_galv
}

# Ratios estándar temporales (kg/HH) hasta conectar tu hoja de productividad
ratios = {
    "CNC": 120.0,
    "Soldadura": 45.0,
    "Galvanizado": 160.0
}

# -------------------------------------------------------------
# 3. EJECUCIÓN PRINCIPAL
# -------------------------------------------------------------
st.title("🏭 Simulador de Carga vs Capacidad")

if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()

try:
    with st.spinner("Descargando y procesando datos..."):
        df_backlog_raw = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
        df_procesos_raw = cargar_hoja(st.secrets["SHEET_ID_PROCESOS"])
        
    # 1. Normalizar Procesos
    df_procesos = normalizar_procesos(df_procesos_raw)
    
    # 2. Filtrar Backlog (Solo subproyectos con fecha programada)
    col_sub_backlog = next((c for c in df_backlog_raw.columns if "sub-proyecto" in c.lower() or "subproyecto" in c.lower()), None)
    col_fecha = next((c for c in df_backlog_raw.columns if "fecha" in c.lower()), None)
    
    df_backlog = df_backlog_raw.dropna(subset=[col_fecha]) # Elimina los que no tienen fecha
    subproyectos_activos = df_backlog[col_sub_backlog].astype(str).str.strip().unique()
    
    # 3. Cruzar: Dejar solo la carga de los subproyectos activos
    df_carga = df_procesos[df_procesos["Subproyecto"].isin(subproyectos_activos)].copy()
    
    # 4. Cálculo de Carga en Horas
    df_carga["Productividad"] = df_carga["Proceso"].map(ratios).fillna(100) # Fallback a 100 kg/HH si no encuentra el proceso
    df_carga["HH_Pendientes"] = df_carga["Kg_Pendientes"] / df_carga["Productividad"]
    
    resumen = df_carga.groupby("Proceso").agg(
        Kg_Pendientes=("Kg_Pendientes", "sum"),
        HH_Pendientes=("HH_Pendientes", "sum")
    ).reset_index()
    
    # 5. Cálculo de Capacidad
    resumen["Operarios"] = resumen["Proceso"].map(dotacion).fillna(0)
    resumen["Capacidad_HH_Sem"] = resumen["Operarios"] * horas_sem * eficiencia
    resumen["Semanas_Cola"] = (resumen["HH_Pendientes"] / resumen["Capacidad_HH_Sem"]).round(1)
    resumen["Saturacion_%"] = ((resumen["HH_Pendientes"] / (resumen["Capacidad_HH_Sem"] * 2)) * 100).round(1) # Ventana de 2 semanas
    
    resumen["Estado"] = resumen["Saturacion_%"].apply(
        lambda x: "🔴 CUELLO DE BOTELLA" if x > 100 else ("🟡 CARGA ALTA" if x > 80 else "🟢 OK")
    )
    
    # -------------------------------------------------------------
    # 4. VISUALIZACIÓN
    # -------------------------------------------------------------
    c1, c2 = st.columns(2)
    c1.metric("Kilos Totales Pendientes", f"{df_carga['Kg_Pendientes'].sum():,.0f} kg")
    c2.metric("Subproyectos Activos en Planta", len(subproyectos_activos))
    
    st.markdown("### 📊 Demanda (HH) vs Capacidad Instalada (HH)")
    fig = px.bar(
        resumen[resumen["Operarios"] > 0], # Muestra solo procesos que tienen operarios asignados en la app
        x="Proceso", 
        y=["HH_Pendientes", "Capacidad_HH_Sem"], 
        barmode="group"
    )
    st.plotly_chart(fig, use_container_width=True)
    
    st.markdown("### 📋 Alertas por Estación")
    st.dataframe(resumen[["Proceso", "Operarios", "Kg_Pendientes", "Semanas_Cola", "Estado"]], use_container_width=True)

except Exception as e:
    st.error(f"Error procesando los datos: {e}")
    st.info("Asegúrate de que los nombres de las columnas 'Sub-proyecto' y 'Fecha' existan en tu Backlog.")
