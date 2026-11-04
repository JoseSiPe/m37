---
tipo: brief
id_encargo: DEMO-01_entrada-mercado
cliente: ACME
fecha: 2026-10-28
pregunta_central: "¿Debe la división SaaS entrar al segmento de operadores logísticos medianos (50-250 unidades) mediante despliegue modular directo?"
decision_que_habilita: "Aprobar o descartar la inversión semilla de USD 1.20M para el lanzamiento comercial Q1."
alcance:
  - "Evaluación económico-estratégica del segmento mediano (50-250 vehículos) en corredores México-Colombia"
exclusiones:
  - "Segmento micro-flotas (<15 vehículos) y adquisiciones inorgánicas (M&A)"
entregables_esperados:
  - "Informe.md"
audiencia: "Comité de Inversión Corporativo"
nivel_rigor: L3
dominios_involucrados:
  - estrategia-base
dominios_bloqueantes:
  - estrategia-base
hipotesis_a_falsear:
  - "H0: El despliegue modular directo alcanza un VAN positivo (> USD 1.5M a 36 meses) y recuperación menor a 24 meses bajo tasas reales de churn."
puerta_1_aprobada: true
---

## Contexto

Evaluación de demostración incluida en `starter_vault/` para comprobar de extremo a extremo la cadena `Evidencia -> Derivación -> Hallazgo -> Veredicto QA Cruzado -> Compilación Editorial 5 Pasadas`.

## Hipótesis iniciales a falsear

- **H0:** El segmento mediano (50-250 unidades) permite recuperar la inversión inicial de USD 1.20M en menos de 24 meses sin subsidiar hardware propietario.

## Criterios de fracaso

- `VAN < USD 1.50M` al 12% anual o periodo de recuperación (`payback`) superior a 24 meses.

## Restricciones

- Tope de inversión inicial de USD 1.20M y cumplimiento estricto de límites muestrales de las fuentes citadas.
