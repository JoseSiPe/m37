# Skill de Dominio: Ciencia de Datos, Econometría y Modelado Cuantitativo (`datos-base`)

## Estándares Obligatorios de Evaluación y Auditoría (`Fase A` + `Fase B`)
1. **Prohibición de Cifras sin Script (`productor_modo: script`):** Toda `Derivación` (`D-XX.json`) cuyo diccionario `salidas` contenga valores numéricos DEBE ser producida por un script determinista en `analisis/<encargo>/` con `productor_sha256` verificado e imprimir en `stdout` el JSON exacto de `salidas` (re-ejecutable con `m37 verify <encargo> --rerun`).
2. **Diseño Muestral Complejo y Microdatos (ENOE, ENIGH, Censos Económicos):**
   - Nunca calcules promedios o proporciones simples sin ponderar por el **factor de expansión** oficial (`fac`, `fac_tri`, `factor`).
   - Cuando el diseño sea bietápico, estratificado y por conglomerados (`upm` / `est_d`), reporta **errores estándar ajustados por diseño muestral** (linearización de Taylor o *replicate weights*), tamaño muestral efectivo ($n$ sin expandir y población expandida $N$) e intervalos de confianza al $95\%$.
   - Declara explícitamente filtros de universo (ej. población ocupada $15+$ años, exclusión de no especificados y tratamiento de valores imputados o *top-coding*).
3. **Identificación Causal vs. Asociación Descriptiva:**
   - No utilices verbos causales (*"el programa incrementó"*, *"el precio redujo"*) con fuerza `recomendacion_condicionada` o `prescripcion_accionable` sin una estrategia de identificación explícita (DiD con prueba de tendencias paralelas, RDD con test de McCrary, IV con estadístico $F$ de primera etapa $> 10$, o experimento aleatorio con balance de covariables).
   - Sin identificación causal validada, la fuerza epistémica máxima permitida es `observacion_acotada` o `inferencia_soportada`.
4. **Sensibilidad, Deflactación y Unidades:**
   - Especifica siempre moneda, año base del deflactor (ej. INPC base segunda quincena julio 2018 o USD constantes) y sufijos de unidad en cada clave de `salidas` (`_musd`, `_mxn_real`, `_pct`, `_ee`, `_ic95_inf`, `_ic95_sup`).
   - Incluye al menos un escenario de estrés paramétrico (ej. choque de tasa de descuento, elasticidad o *churn*) para probar la robustez del signo de la decisión.
