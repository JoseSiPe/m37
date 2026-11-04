---
tipo: arquetipo
id_arquetipo: ARQ_Diagnostico-Cualitativo
descripcion: "Diagnóstico cualitativo, estratégico o regulatorio sin ejecución de scripts."
nodos:
  - id: N-01
    objetivo: "Localizar, verificar y registrar evidencia primaria o interna verificable para responder al Brief."
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
    criterio_suficiencia: "Al menos una ficha de evidencia verificada con localizador_verificable y límites explícitos."
    contexto_estimado_tokens: 24000
    presupuesto_max_usd: 0.75
    max_turns: 14
  - id: N-02
    objetivo: "Sintetizar evidencia en Hallazgos estructurados con contrahipótesis probadas y calibración epistémica."
    perfil_herramientas: analitico
    rol_tarea: analisis-sintesis
    eje_dominante: abstraccion_conceptual
    metodo: matriz-comparativa-alternativas
    requiere_computo: false
    requiere_edicion_quirurgica: false
    depende_de:
      - N-01
    entregables_esperados:
      - "Hallazgos/H-*.md"
    artefactos_requeridos:
      - hallazgo
    criterio_suficiencia: "Hallazgos con hash_insumos vigente, contrahipótesis probadas y aprobación en QA Ciego Cruzado."
    contexto_estimado_tokens: 32000
    presupuesto_max_usd: 0.85
    max_turns: 14
  - id: N-03
    objetivo: "Redactar Entregables/Informe.md anclando cada bloque sustantivo con <!-- sustento: H-XX -->."
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
    criterio_suficiencia: "Borrador ejecutivo compilable sin bloques huérfanos ni inflación de fuerza epistémica."
    contexto_estimado_tokens: 28000
    presupuesto_max_usd: 0.65
    max_turns: 12
---

# Arquetipo DAG: Diagnóstico Cualitativo y Regulatorio (`ARQ_Diagnostico-Cualitativo`)

## Secuencia Canónica de Nodos
1. **`N-01` (`documental` · `evidencia`):** Levantamiento y verificación de evidencia primaria (`E-*` / `EI-*`).
2. **`N-02` (`analitico` · `analisis-sintesis`):** Síntesis en `Hallazgos/H-*.md` con contrahipótesis probadas y calibración epistémica + QA Ciego Cruzado.
3. **`N-03` (`editorial` · `redaccion`):** Redacción de `Entregables/Informe.md` con trazabilidad bloque a bloque.
