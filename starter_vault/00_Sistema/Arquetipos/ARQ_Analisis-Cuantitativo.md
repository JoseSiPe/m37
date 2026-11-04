---
tipo: arquetipo
id_arquetipo: ARQ_Analisis-Cuantitativo
descripcion: "Modelado cuantitativo reproducible con scripts aislados en analisis/<encargo>/ y Derivaciones D-*.json."
nodos:
  - id: N-01
    objetivo: "Recolectar y verificar diccionarios de datos, parámetros e insumos primarios en fichas de evidencia."
    perfil_herramientas: documental
    rol_tarea: evidencia
    eje_dominante: extraccion_fiel
    metodo: matriz-comparativa-alternativas
    requiere_computo: false
    requiere_edicion_quirurgica: false
    depende_de: []
    entregables_esperados:
      - "Evidencia_Interna/EI-*.md o 30_Evidencia_Publica/E-*.md"
    artefactos_requeridos:
      - evidencia
    criterio_suficiencia: "Fichas de evidencia primaria verificadas con localizador y límites."
    contexto_estimado_tokens: 24000
    presupuesto_max_usd: 0.75
    max_turns: 14
  - id: N-02
    objetivo: "Construir y ejecutar modelo reproducible en analisis/<encargo>/, emitir Derivación D-*.json y Hallazgo H-*.md."
    perfil_herramientas: analitico
    rol_tarea: analisis-sintesis
    eje_dominante: ejecucion_formal
    metodo: matriz-comparativa-alternativas
    requiere_computo: true
    requiere_edicion_quirurgica: false
    depende_de:
      - N-01
    entregables_esperados:
      - "Derivaciones/D-*.json"
      - "Hallazgos/H-*.md"
    artefactos_requeridos:
      - derivacion
      - hallazgo
    criterio_suficiencia: "Script reproducible, Derivación D-* validada en Fase A y Hallazgo aprobado en QA Ciego Cruzado."
    contexto_estimado_tokens: 36000
    presupuesto_max_usd: 1.00
    max_turns: 16
  - id: N-03
    objetivo: "Redactar Entregables/Informe.md interpolando métricas auditadas {{D-XX.clave}} y sustentos H-XX."
    perfil_herramientas: editorial
    rol_tarea: redaccion
    eje_dominante: coherencia_documental
    metodo: null
    requiere_computo: false
    requiere_edicion_quirurgica: false
    depende_de:
      - N-02
    entregables_esperados:
      - "Entregables/Informe.md"
    artefactos_requeridos:
      - entregable
    criterio_suficiencia: "Entregable compilable con interpolación exacta desde Derivaciones/D-*."
    contexto_estimado_tokens: 28000
    presupuesto_max_usd: 0.65
    max_turns: 12
---

# Arquetipo DAG: Análisis Cuantitativo y Modelado (`ARQ_Analisis-Cuantitativo`)

## Secuencia Canónica de Nodos
1. **`N-01` (`documental` · `evidencia`):** Recolección y verificación de fuentes de datos primarios (`E-*` / `EI-*`).
2. **`N-02` (`analitico` · `analisis-sintesis`):** Construcción y ejecución aislada de scripts en `analisis/<ID-Encargo>/`, emisión de `Derivaciones/D-*.json` y `Hallazgos/H-*.md` con `valores_anclados` + QA Ciego Cruzado.
3. **`N-03` (`editorial` · `redaccion`):** Ensamblaje de `Entregables/Informe.md` usando interpolación segura `{{D-XX.clave | fmt(...)}}`.
