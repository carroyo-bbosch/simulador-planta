import streamlit as st
import pandas as pd
import smartsheet
import re

st.set_page_config(page_title="Validador de Estructura - Planta", layout="wide")
st.title("🏭 Inspección y Normalización de Datos")

@st.cache_data(ttl=300)
def cargar_hoja(sheet_id):
    cliente = smartsheet.Smartsheet(st.secrets["SMARTSHEET_ACCESS_TOKEN"])
    hoja = cliente.Sheets.get_sheet(int(sheet_id))
    columnas = [col.title.strip() for col in hoja.columns]
    filas = [[celda.value for celda in f.cells] for f in hoja.rows]
    return pd.DataFrame(filas, columns=columnas)

try:
    df_backlog = cargar_hoja(st.secrets["SHEET_ID_BACKLOG"])
    df_procesos = cargar_hoja(st.secrets["SHEET_ID_PROCESOS"])
    df_prod = cargar_hoja(st.secrets["SHEET_ID_PRODUCTIVIDAD"])

    st.success("✅ Hojas conectadas correctamente.")

    # 1. Análisis de Columnas de la Hoja Procesos
    cols_procesos = df_procesos.columns.tolist()
    
    # Identificar columnas de "kilos lanzados" y "kilos [avance]"
    lanzados_dict = {}
    avances_dict = {}

    for c in cols_procesos:
        c_lower = c.lower().strip()
        if "kilos lanzados" in c_lower:
            proceso_nombre = re.sub(r"kilos lanzados", "", c, flags=re.IGNORECASE).strip()
            lanzados_dict[proceso_nombre] = c
        elif c_lower.startswith("kilos ") and not any(x in c_lower for x in ["lanzados", "orden", "cargados"]):
            proceso_nombre = re.sub(r"^kilos\s+", "", c, flags=re.IGNORECASE).strip()
            avances_dict[proceso_nombre] = c

    # Mostrar diagnóstico en pantalla
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### 🔍 Procesos detectados para 'Lanzados'")
        st.json(lanzados_dict)
    with c2:
        st.markdown("### 🔍 Procesos detectados para 'Avance Real'")
        st.json(avances_dict)

    # 2. Validación de columnas de ingeniería
    col_orden = [c for c in cols_procesos if "orden de proceder" in c.lower()]
    col_cargados = [c for c in cols_procesos if "cargados" in c.lower()]
    
    st.markdown("### 📐 Columnas de Ingeniería detectadas")
    st.write(f"- **Orden de Proceder:** {col_orden[0] if col_orden else '❌ No encontrada'}")
    st.write(f"- **Kilos Cargados:** {col_cargados[0] if col_cargados else '❌ No encontrada'}")

    st.markdown("### 📋 Muestra de la Hoja de Productividad")
    st.dataframe(df_prod.head(10), use_container_width=True)

except Exception as e:
    st.error(f"Error durante la inspección: {e}")
