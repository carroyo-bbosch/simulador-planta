import streamlit as st
import pandas as pd
import plotly.express as px
import smartsheet

st.set_page_config(page_title="Simulador de Capacidad - Planta", layout="wide")

# -------------------------------------------------------------
# FUNCIÓN DE CONEXIÓN A SMARTSHEET (CON CACHÉ DE 10 MINUTOS)
# -------------------------------------------------------------
@st.cache_data(ttl=600)
def cargar_hoja_smartsheet(sheet_id):
    """Descarga una hoja de Smartsheet y la convierte en DataFrame de pandas."""
    token = st.secrets["SMARTSHEET_ACCESS_TOKEN"]
    cliente = smartsheet.Smartsheet(token)
    cliente.errors_as_exceptions(True)
    
    hoja = cliente.Sheets.get_sheet(int(sheet_id))
    columnas = [col.title for col in hoja.columns]
    
    filas_datos = []
    for fila in hoja.rows:
        valores_fila = [celda.value for celda in fila.cells]
        filas_datos.append(valores_fila)
        
    return pd.DataFrame(filas_datos, columns=columnas)

st.title("🏭 Simulador de Carga y Capacidad de Planta")

# Botón para refrescar datos manualmente
if st.button("🔄 Recargar datos desde Smartsheet"):
    st.cache_data.clear()
    st.rerun()

# -------------------------------------------------------------
# LECTURA DE PLANILLAS
# -------------------------------------------------------------
try:
    with st.spinner("Conectando con Smartsheet..."):
        df_backlog = cargar_hoja_smartsheet(st.secrets["SHEET_ID_BACKLOG"])
        df_procesos = cargar_hoja_smartsheet(st.secrets["SHEET_ID_PROCESOS"])
        df_prod = cargar_hoja_smartsheet(st.secrets["SHEET_ID_PRODUCTIVIDAD"])
        
    st.success("✅ Conexión exitosa con Smartsheet.")
    
    # Vista previa de validación de columnas
    st.markdown("### Verificación de Estructura de Datos")
    t1, t2, t3 = st.tabs(["Hoja Backlog", "Hoja Procesos/Kilos", "Hoja Productividad"])
    with t1:
        st.write(f"Filas encontradas: {len(df_backlog)}")
        st.dataframe(df_backlog.head(3), use_container_width=True)
    with t2:
        st.write(f"Filas encontradas: {len(df_procesos)}")
        st.dataframe(df_procesos.head(3), use_container_width=True)
    with t3:
        st.write(f"Filas encontradas: {len(df_prod)}")
        st.dataframe(df_prod.head(3), use_container_width=True)

except Exception as e:
    st.error(f"Error al conectar con Smartsheet: {e}")
    st.info("Revisa que los IDs y el Token en 'Secrets' estén bien escritos y que la cuenta tenga acceso a esas hojas.")
