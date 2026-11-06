import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Simulador de Capacidad - Torres & Subestaciones", layout="wide")

st.title("🏭 Simulador de Carga y Capacidad de Planta")
st.markdown("Control de colas de fabricación y simulación de escenarios de entrega.")

# -------------------------------------------------------------
# 1. CONTROLES LATERALES (DOTACIÓN DINÁMICA)
# -------------------------------------------------------------
st.sidebar.header("👷 Distribución de Mano de Obra (MOD)")
horas_sem = st.sidebar.number_input("Horas laborales / semana por operario", value=40, step=1)
eficiencia = st.sidebar.slider("Eficiencia Operativa (%)", 50, 100, 85) / 100.0

st.sidebar.subheader("Asignación de Operarios")
mod_ing = st.sidebar.slider("Ingeniería / Cubicadores", 1, 10, 2)
mod_cnc = st.sidebar.slider("Corte y Punzonado CNC", 1, 20, 6)
mod_sold = st.sidebar.slider("Armado y Soldadura", 1, 30, 14)
mod_galv = st.sidebar.slider("Galvanizado en Caliente", 1, 20, 8)
mod_desp = st.sidebar.slider("Kitting y Despacho", 1, 15, 4)

dotacion = {
    "Ingeniería": mod_ing,
    "Corte CNC": mod_cnc,
    "Soldadura": mod_sold,
    "Galvanizado": mod_galv,
    "Despacho": mod_desp
}

# -------------------------------------------------------------
# 2. RATIOS DE PRODUCTIVIDAD (kg / HH)
# -------------------------------------------------------------
ratios_productividad = {
    "Ingeniería": 800.0,
    "Corte CNC": 120.0,
    "Soldadura": 45.0,
    "Galvanizado": 160.0,
    "Despacho": 300.0
}

# -------------------------------------------------------------
# 3. BACKLOG Y SIMULADOR DE NUEVO PROYECTO
# -------------------------------------------------------------
datos = [
    {"Proyecto": "P-101 Torre 220kV", "Proceso": "Corte CNC", "Kg_Totales": 85000, "Kg_Avanzados": 45000},
    {"Proyecto": "P-101 Torre 220kV", "Proceso": "Soldadura", "Kg_Totales": 85000, "Kg_Avanzados": 20000},
    {"Proyecto": "P-101 Torre 220kV", "Proceso": "Galvanizado", "Kg_Totales": 85000, "Kg_Avanzados": 5000},
    {"Proyecto": "P-102 Subestación Norte", "Proceso": "Corte CNC", "Kg_Totales": 40000, "Kg_Avanzados": 38000},
    {"Proyecto": "P-102 Subestación Norte", "Proceso": "Soldadura", "Kg_Totales": 40000, "Kg_Avanzados": 15000},
    {"Proyecto": "P-102 Subestación Norte", "Proceso": "Galvanizado", "Kg_Totales": 40000, "Kg_Avanzados": 0},
    {"Proyecto": "P-103 Ampliación 500kV", "Proceso": "Corte CNC", "Kg_Totales": 60000, "Kg_Avanzados": 5000},
    {"Proyecto": "P-103 Ampliación 500kV", "Proceso": "Soldadura", "Kg_Totales": 60000, "Kg_Avanzados": 0},
    {"Proyecto": "P-103 Ampliación 500kV", "Proceso": "Galvanizado", "Kg_Totales": 60000, "Kg_Avanzados": 0},
]
df = pd.DataFrame(datos)

st.sidebar.markdown("---")
st.sidebar.header("🧪 Simulación de Proyecto Nuevo")
simular = st.sidebar.checkbox("¿Evaluar nuevo proyecto?")
if simular:
    nombre_p = st.sidebar.text_input("Nombre de Proyecto", value="Licitación Nueva")
    kilos_p = st.sidebar.number_input("Kilos Totales", value=30000, step=5000)
    nuevos = [
        {"Proyecto": nombre_p, "Proceso": "Corte CNC", "Kg_Totales": kilos_p, "Kg_Avanzados": 0},
        {"Proyecto": nombre_p, "Proceso": "Soldadura", "Kg_Totales": kilos_p, "Kg_Avanzados": 0},
        {"Proyecto": nombre_p, "Proceso": "Galvanizado", "Kg_Totales": kilos_p, "Kg_Avanzados": 0},
    ]
    df = pd.concat([df, pd.DataFrame(nuevos)], ignore_index=True)

# -------------------------------------------------------------
# 4. MOTOR DE CÁLCULO
# -------------------------------------------------------------
df["Kg_Pendientes"] = (df["Kg_Totales"] - df["Kg_Avanzados"]).clip(lower=0)
df["Productividad"] = df["Proceso"].map(ratios_productividad)
df["HH_Pendientes"] = df["Kg_Pendientes"] / df["Productividad"]

resumen = df.groupby("Proceso").agg(
    Kg_Pendientes=("Kg_Pendientes", "sum"),
    HH_Pendientes=("HH_Pendientes", "sum")
).reset_index()

resumen["Operarios"] = resumen["Proceso"].map(dotacion)
resumen["Capacidad_HH_Sem"] = resumen["Operarios"] * horas_sem * eficiencia
resumen["Capacidad_Kg_Sem"] = resumen["Capacidad_HH_Sem"] * resumen["Proceso"].map(ratios_productividad)
resumen["Semanas_Cola"] = (resumen["HH_Pendientes"] / resumen["Capacidad_HH_Sem"]).round(1)

# Ocupación en una ventana de 2 semanas de trabajo
resumen["Ocupacion_%"] = ((resumen["HH_Pendientes"] / (resumen["Capacidad_HH_Sem"] * 2)) * 100).round(1)

def diagnostico(ocup):
    if ocup > 100:
        return "🔴 CUELLO DE BOTELLA"
    elif ocup >= 80:
        return "🟡 CARGA ALTA"
    return "🟢 FLUIDO"

resumen["Diagnostico"] = resumen["Ocupacion_%"].apply(diagnostico)

# -------------------------------------------------------------
# 5. VISUALIZACIÓN
# -------------------------------------------------------------
c1, c2, c3 = st.columns(3)
c1.metric("Kilos Totales en Cola", f"{df['Kg_Pendientes'].sum():,.0f} kg")
c2.metric("HH Pendientes de Fabricación", f"{df['HH_Pendientes'].sum():,.0f} HH")
c3.metric("Mano de Obra Directa Total", f"{sum(dotacion.values())} operarios")

st.markdown("### 📊 Capacidad vs Carga Real en Cola")
fig = px.bar(
    resumen, 
    x="Proceso", 
    y=["HH_Pendientes", "Capacidad_HH_Sem"], 
    barmode="group",
    labels={"value": "Horas Hombre (HH)", "variable": "Indicador"}
)
st.plotly_chart(fig, use_container_width=True)

st.markdown("### 📋 Tabla de Estado de Línea")
st.dataframe(
    resumen[["Proceso", "Operarios", "Kg_Pendientes", "Capacidad_Kg_Sem", "Semanas_Cola", "Diagnostico"]],
    use_container_width=True
)
