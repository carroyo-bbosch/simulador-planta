import streamlit as st
import pandas as pd
import smartsheet
import re
import unicodedata
import plotly.express as px

st.set_page_config(page_title="Simulador de Capacidad - Planta", layout="wide")

# -------------------------------------------------------------
# 0. DEFINICIÓN OFICIAL DE LA LÍNEA DE PRODUCCIÓN (12 PROCESOS)
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

    # Ordenar de mayor a menor longitud para que 'Armado Soldadura' se evalúe antes que 'Soldadura'
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
        if not subproyecto or subproyecto in ["None", "nan"]: 
            continue
        
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
                    "Kg_Pendientes": kg_pendientes
                })
                
    return pd.DataFrame(registros)

# -------------------------------------------------------------
# 2. CONTROLES LATERALES (DOTACIÓN Y SIMULACIÓN)
# -------------------------------------------------------------
st.sidebar.header("⚙️ Jornada y Rendimiento")
horas_sem = st.sidebar.number_input("Horas laborables / semana", value=44, step=1)
eficiencia = st.sidebar.slider("Eficiencia Operativa (OEE) %", 50, 100, 85) / 100.0

st.sidebar.header("👷 Dotación por Proceso")
dotacion_default = {
    "Ingeniería": 2, "Oficina Técnica": 2, "CNCL": 4, "CNCPL": 4, 
    "Talón": 2, "DoblezL": 2, "Perforado": 3, "Armado Soldadura": 8, 
    "Soldadura": 8, "Fabricado": 2, "Galvanizado": 8, "Embalaje": 4
}

dotacion = {}
with st.sidebar.expander("Configurar Operarios Asignados", expanded=False):
    for proc in PROCESOS_LINEA:
        dotacion[proc] = st.number_input(
            f"{proc}", 
            min_value=0, 
            max_value=50, 
            value=dotacion_default.get(proc, 2), 
            step=1
        )

st.sidebar.info(f"**Total Mano de Obra Activa:** {sum(dotacion.values())} operarios")

# Módulo What-If: Evaluar Proyecto Nuevo
st.sidebar.markdown("---")
st.sidebar.header("🧪 Simulación Proyecto Nuevo")
simular_activo = st.sidebar.checkbox("¿Evaluar nuevo proyecto?")
nuevo_kg = 0
nuevo_nombre = "Proyecto Simulado"
if simular_activo:
    nuevo_nombre = st.sidebar.text_input("Nombre de Referencia", value="Subestación 220kV")
    nuevo_kg = st.sidebar.number_input("Kilos a Inyectar", value=35000, step=5000)

# -------------------------------------------------------------
# 3. EJECUCIÓN DEL MODELO
# -------------------------------------------------------------
st.title("🏭 Simulador de Línea: Torres de Alta Tensión y Subestaciones")

if st.button("🔄 Refrescar Datos Smartsheet"):
    st.cache_data.clear()
    st.rerun()

try:
    with st.spinner("Sincronizando información de planta..."):
        df_backlog_raw = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
        df_procesos_raw = cargar_hoja(st.secrets["SHEET_ID_PROCESOS"])
        df_prod_raw = cargar_hoja(st.secrets["SHEET_ID_PRODUCTIVIDAD"])

    # 1. Filtro estricto de Backlog: Fecha programada activa y despacho real en blanco
    cols_backlog = df_backlog_raw.columns.tolist()
    col_sub_backlog = next((c for c in cols_backlog if "sub-proyecto" in c.lower() or "subproyecto" in c.lower()), None)
    col_fecha_prog = next((c for c in cols_backlog if "programad" in c.lower() and "fecha" in c.lower()), None)
    if not col_fecha_prog:
        col_fecha_prog = next((c for c in cols_backlog if "fecha" in c.lower()), None)
    col_despacho_real = next((c for c in cols_backlog if "despacho" in c.lower() and ("real" in c.lower() or "fecha" in c.lower())), None)

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
    df_backlog_activo["Semana_Entrega"] = df_backlog_activo["Fecha_DT"].dt.strftime("%Y-Sem%U")
    subproyectos_vivos = df_backlog_activo[col_sub_backlog].dropna().astype(str).str.strip().unique()

    # 2. Normalizar procesos y filtrar carga viva
    df_procesos = normalizar_procesos(df_procesos_raw)
    df_carga = df_procesos[df_procesos["Subproyecto"].isin(subproyectos_vivos)].copy()

    # Inyección de proyecto simulado en toda la ruta
    if simular_activo and nuevo_kg > 0:
        simulados = [{"Subproyecto": f"🧪 {nuevo_nombre}", "Proceso": p, "Kg_Pendientes": nuevo_kg} for p in PROCESOS_LINEA]
        df_carga = pd.concat([df_carga, pd.DataFrame(simulados)], ignore_index=True)

    # 3. Cargar Productividad (kg/HH) desde la hoja oficial
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

    # Asignar ratio con fallback por defecto de 80 kg/HH si alguno no está cargado
    df_carga["Productividad"] = df_carga["Proceso"].map(lambda p: ratios_productividad.get(p, 80.0))
    df_carga["HH_Pendientes"] = df_carga["Kg_Pendientes"] / df_carga["Productividad"]

    # 4. Consolidar Resumen respetando el orden secuencial de la línea
    resumen = df_carga.groupby("Proceso", as_index=False)[["Kg_Pendientes", "HH_Pendientes"]].sum()
    
    # Asegurar que los 12 procesos existan en la tabla aunque tengan 0 carga
    df_base = pd.DataFrame({"Proceso": PROCESOS_LINEA})
    resumen = df_base.merge(resumen, on="Proceso", how="left").fillna(0)

    resumen["Operarios"] = resumen["Proceso"].map(dotacion)
    resumen["Productividad_KgHH"] = resumen["Proceso"].map(lambda p: ratios_productividad.get(p, 80.0))
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
        return "🟢 FLUIDO"

    resumen["Estado"] = resumen["Saturacion_%"].apply(diagnostico)

    # Convertir a categoría ordenada para asegurar el flujo secuencial en gráficos
    resumen["Proceso"] = pd.Categorical(resumen["Proceso"], categories=PROCESOS_LINEA, ordered=True)
    resumen = resumen.sort_values("Proceso")

    # -------------------------------------------------------------
    # 4. VISUALIZACIÓN EN PANTALLA
    # -------------------------------------------------------------
    tab1, tab2, tab3 = st.tabs([
        "📊 Balance de Línea (12 Procesos)", 
        "📅 Calendario de Despachos", 
        "📋 Tabla de Ratios y Auditoría"
    ])

    with tab1:
        m1, m2, m3 = st.columns(3)
        m1.metric("Kilos Totales en Proceso", f"{df_carga['Kg_Pendientes'].sum():,.0f} kg")
        m2.metric("HH Pendientes de Taller", f"{df_carga['HH_Pendientes'].sum():,.0f} HH")
        m3.metric("Lotes en Fabricación", len(df_backlog_activo))

        st.markdown("### Carga Pendiente vs Capacidad Semanal Disponible")
        fig_bar = px.bar(
            resumen,
            x="Proceso",
            y=["HH_Pendientes", "Capacidad_HH_Sem"],
            barmode="group",
            labels={"value": "Horas Hombre (HH)", "variable": "Indicador"},
            category_orders={"Proceso": PROCESOS_LINEA}
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        st.markdown("### Detalle Operativo por Estación")
        st.dataframe(
            resumen[["Proceso", "Operarios", "Productividad_KgHH", "Kg_Pendientes", "Capacidad_HH_Sem", "Semanas_Cola", "Estado"]],
            use_container_width=True
        )

    with tab2:
        st.markdown("### Distribución de Despachos por Semana")
        col_kg_prog = next((c for c in cols_backlog if "kilos" in c.lower() and "programad" in c.lower()), None)
        if col_kg_prog:
            df_backlog_activo["Kg_Prog_Num"] = pd.to_numeric(df_backlog_activo[col_kg_prog], errors='coerce').fillna(0)
            resumen_semanal = df_backlog_activo.groupby("Semana_Entrega", as_index=False)["Kg_Prog_Num"].sum().sort_values("Semana_Entrega")
            
            fig_sem = px.bar(
                resumen_semanal, 
                x="Semana_Entrega", 
                y="Kg_Prog_Num",
                labels={"Kg_Prog_Num": "Kilos Programados", "Semana_Entrega": "Semana Calendario"},
                title="Kilos a Entregar por Semana de Salida"
            )
            st.plotly_chart(fig_sem, use_container_width=True)

        st.dataframe(
            df_backlog_activo[[col_sub_backlog, col_fecha_prog, col_kg_prog, "Semana_Entrega"]].sort_values(by=col_fecha_prog),
            use_container_width=True
        )

    with tab3:
        st.markdown("### Ratios de Productividad Detectados (`procesos simulador` / `Kg/hh`)")
        df_prod_show = pd.DataFrame([
            {"Proceso": p, "Kg/HH": ratios_productividad.get(p, "80.0 (Fallback)")}
            for p in PROCESOS_LINEA
        ])
        st.dataframe(df_prod_show, use_container_width=True)
        st.caption(f"Columnas detectadas en la hoja: `{col_prod_proc}` y `{col_prod_kghh}`")

except Exception as e:
    st.error(f"Error procesando los datos: {e}")
