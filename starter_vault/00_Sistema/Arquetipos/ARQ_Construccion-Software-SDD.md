---
tipo: arquetipo
id_arquetipo: ARQ_Construccion-Software-SDD
descripcion: "Ingeniería de software verificable mediante SDD + EARS + TDD y recibo criptográfico .m37_verify_receipt.json."
nodos:
  - id: N-01
    objetivo: "Formalizar la especificación funcional verificable en sintaxis EARS con requisitos numerados RF-01..RF-N."
    perfil_herramientas: documental
    rol_tarea: evidencia
    eje_dominante: extraccion_fiel
    metodo: sdd-ears-tdd
    requiere_computo: false
    requiere_edicion_quirurgica: false
    depende_de: []
    entregables_esperados:
      - "Evidencia_Interna/EI-*.md con requisitos EARS RF-*"
    artefactos_requeridos:
      - evidencia
    criterio_suficiencia: "Especificación EARS verificable registrada como Evidencia con requisitos RF-* y controles AppSec."
    contexto_estimado_tokens: 28000
    presupuesto_max_usd: 0.75
    max_turns: 14
  - id: N-02
    objetivo: "Implementar código modular y suite TDD en codigo/<encargo>/, sellar .m37_verify_receipt.json y emitir Derivación y Hallazgo."
    perfil_herramientas: construccion
    rol_tarea: desarrollo-codigo
    eje_dominante: ejecucion_formal
    metodo: sdd-ears-tdd
    requiere_computo: true
    requiere_edicion_quirurgica: true
    depende_de:
      - N-01
    entregables_esperados:
      - "Código y tests TDD en codigo/<encargo>/"
      - "Derivaciones/D-*.json (productor_modo=verificacion_codigo)"
      - "Hallazgos/H-*.md"
    artefactos_requeridos:
      - codigo
      - derivacion
      - hallazgo
    criterio_suficiencia: "Suite de pruebas con exit_code=0, recibo tree_sha256 vigente, cobertura 100% de RF-* y aprobación QA Ciego."
    contexto_estimado_tokens: 48000
    presupuesto_max_usd: 1.25
    max_turns: 18
  - id: N-03
    objetivo: "Redactar Entregables/Informe.md documentando arquitectura, seguridad y métricas interpoladas desde D-*."
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
    criterio_suficiencia: "Informe técnico y ejecutivo compilable con trazabilidad completa hacia H-* y D-*."
    contexto_estimado_tokens: 28000
    presupuesto_max_usd: 0.65
    max_turns: 12
---

# Arquetipo DAG: Construcción de Software SDD + EARS + TDD (`ARQ_Construccion-Software-SDD`)

## Secuencia Canónica de Nodos
1. **`N-01` (`documental` · `evidencia`):** Formalización de requisitos verificables en sintaxis EARS (`RF-01`, `RF-02`, ...) respaldados por el Brief o documentación técnica del cliente.
2. **`N-02` (`construccion` · `desarrollo-codigo`):** Desarrollo TDD y AppSec en `codigo/<ID-Encargo>/`, ejecución de `run_verification_cmd("pytest -q")` estampando `.m37_verify_receipt.json`, registro de `Derivaciones/D-*.json` (`productor_modo: verificacion_codigo` con `cobertura_rf` al 100%) y `Hallazgos/H-*.md` + QA Ciego Cruzado de código y tests.
3. **`N-03` (`editorial` · `redaccion`):** Documentación ejecutiva y técnica de entrega en `Entregables/Informe.md` con métricas de cobertura interpoladas desde `D-*`.
