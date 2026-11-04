---
id: H-DEMO-01-01
tipo: hallazgo
encargo: DEMO-01_entrada-mercado
dominio: estrategia-base
naturaleza: estrategico
fuerza_afirmacion: prescripcion_accionable
afirmacion_corta: El despliegue SaaS modular directo en flotas medianas (50-250 unidades)
  en corredores México-Colombia genera un VAN de USD 2.45M y recuperación en 17 meses.
sustento_evidencia:
- '[[E-202610-001]]'
sustento_derivaciones:
- '[[D-DEMO-01-01]]'
metodo: matriz-comparativa-alternativas
valores_anclados:
  van_musd: 2.45
  payback_meses: 17
  van_estres_churn_musd: 1.62
hash_insumos: 26c474e326cddde74314f8b9b4c306ec6803e512c94d8e37474bc70a1191c94e
confianza: alta
estado_qa: aprobado
contrahipotesis_probadas: 2
que_lo_refutaria: Un churn bruto anual superior al 15.5% o un ciclo comercial mayor
  a 7 meses reduciría el VAN por debajo del umbral mínimo de USD 1.50M.
promovible_a_capital: true
---

## Afirmación

La entrada directa con plataforma SaaS modular sin subsidio de hardware en operadores logísticos medianos (50 a 250 vehículos) supera holgadamente los criterios de inversión a 36 meses, alcanzando un VAN estimado de USD 2.45M (tasa de descuento del 12%) y un periodo de recuperación de 17 meses, restringido estrictamente a corredores industriales urbanos en México y Colombia.

## Sustento y trazabilidad

- Fuente primaria `[[E-202610-001]]` (ORCS 2026, Tabla 4.2, pág. 38): CAGR verificado de 18.4%, ARPU mediano de USD 28,500/año y churn bruto anual de 8.8% sobre $n = 312$ empresas auditadas.
- Derivación estructurada `[[D-DEMO-01-01]]`: flujo descontado a 36 meses con VAN base de USD 2.45M y VAN bajo estrés (+20% en churn) de USD 1.62M.

## Explicaciones rivales descartadas

1. **Subsidio de hardware telemático propio:** descartado porque duplica el CAPEX inicial y extiende el periodo de recuperación a 29 meses (por encima del tope de 24 meses).
2. **Expansión simultánea a micro-flotas (<15 vehículos):** descartada porque la evidencia `[[E-202610-001]]` excluye explícitamente ese segmento y exhibe alta rotación informal.

## Condiciones de validez y límites

Válido exclusivamente para flotas de 50 a 250 vehículos en corredores urbanos de México y Colombia con integración vía API sobre GPS ya instalado. No extrapolable a transporte marítimo, ferroviario ni micro-transportistas.
