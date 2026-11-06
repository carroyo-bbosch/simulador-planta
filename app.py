import streamlit as st
import pandas as pd
import smartsheet
import re

@st.cache_data(ttl=300)
def cargar_hoja(sheet_id):
    cliente = smartsheet.Smartsheet(st.secrets["SMARTSHEET_ACCESS_TOKEN"])
    hoja = cliente.Sheets.get_sheet(int(sheet_id))
    columnas = [col.title.strip() for col in hoja.columns]
    filas = [[celda.value for celda in f.cells] for f in hoja.rows]
    return pd.DataFrame(filas, columns=columnas)

def normalizar_procesos(df_proc):
    """
    Convierte la tabla ancha de Smartsheet a formato largo,
    conmutando entre Estimados y Lanzados según Kilos Cargados.
    """
    cols = df_proc.columns.tolist()
    
    # 1. Identificar columnas clave de control
    col_subproj = next((c for c in cols if "sub-proyecto" in c.lower() or "subproyecto" in c.lower()), None)
    col_cargados = next((c for c in cols if "cargados" in c.lower()), None)
    
    # 2. Mapear columnas por proceso
    procesos_detectados = set()
    mapa_cols = {} # {proceso: {'estimado': col, 'lanzado': col, 'avance': col}}

    for c in cols:
        c_low = c.lower().strip()
        
        # Detectar columnas de Estimados
        if c_low.startswith("estimado"):
            proc = re.sub(r"^estimados?\s*", "", c, flags=re.IGNORECASE).strip()
            procesos_detectados.add(proc)
            mapa_cols.setdefault(proc, {})['estimado'] = c
            
        # Detectar columnas de Lanzados
        elif "kilos lanzados" in c_low:
            proc = re.sub(r"^kilos\s+lanzados\s*", "", c, flags=re.IGNORECASE).strip()
            procesos_detectados.add(proc)
            mapa_cols.setdefault(proc, {})['lanzado'] = c
            
        # Detectar columnas de Avance Real
        elif c_low.startswith("kilos ") and not any(k in c_low for k in ["lanzados", "orden", "cargados"]):
            proc = re.sub(r"^kilos\s+", "", c, flags=re.IGNORECASE).strip()
            procesos_detectados.add(proc)
            mapa_cols.setdefault(proc, {})['avance'] = c

    # 3. Construir la tabla vertical normalizada fila por fila
    registros = []
    
    for _, fila in df_proc.iterrows():
        subproyecto = fila.get(col_subproj, "Sin Subproyecto")
        kilos_cargados = pd.to_numeric(fila.get(col_cargados, 0), errors='coerce') or 0
        es_estimado = (kilos_cargados <= 0)
        
        for proc in procesos_detectados:
            col_est = mapa_cols.get(proc, {}).get('estimado')
            col_lanz = mapa_cols.get(proc, {}).get('lanzado')
            col_av = mapa_cols.get(proc, {}).get('avance')
            
            kg_est = pd.to_numeric(fila.get(col_est, 0), errors='coerce') or 0 if col_est else 0
            kg_lanz = pd.to_numeric(fila.get(col_lanz, 0), errors='coerce') or 0 if col_lanz else 0
            kg_av = pd.to_numeric(fila.get(col_av, 0), errors='coerce') or 0 if col_av else 0
            
            # Conmutación de demanda
            if es_estimado:
                kg_necesidad = kg_est
                origen = "Estimación Preliminar"
            else:
                kg_necesidad = kg_lanz
                origen = "Ingeniería Firme"
                
            kg_pendientes = max(0.0, kg_necesidad - kg_av)
            
            # Solo agregar si hay carga real o estimada para ese proceso
            if kg_necesidad > 0 or kg_av > 0:
                registros.append({
                    "Subproyecto": subproyecto,
                    "Proceso": proc,
                    "Estado_Ingenieria": origen,
                    "Kg_Requeridos": kg_necesidad,
                    "Kg_Avanzados": kg_av,
                    "Kg_Pendientes": kg_pendientes
                })
                
    return pd.DataFrame(registros)
