# Skill de Dominio: Ingeniería de Software SDD, TDD y AppSec (`ingenieria-software-base`)

## Estándares Obligatorios
1. **Requisitos EARS Verificables:** Cada requisito funcional lleva código `RF-XX` y redacción EARS (`CUANDO <disparador>, EL SISTEMA DEBERÁ <comportamiento medible>`).
2. **TDD Estricto y Antipatrones Prohibidos:**
   - Prohibidos tests vacíos (`assert True`) o mocks que sustituyan la unidad bajo prueba misma.
   - Cubre casos nominales, bordes y entradas malformadas.
3. **AppSec por Defecto (Clean-Room OWASP / VibeSec):**
   - Cero credenciales, tokens o claves privadas hardcodeadas en el árbol de código.
   - Consultas SQL parametrizadas (prohibida interpolación de strings en SQL/shell).
   - Validación explícita de tipos, rutas (`Path.resolve()`) y límites de tamaño en entradas externas.
   - Sin `shell=True`, `eval()` ni `pickle.loads()` sobre datos no confiables.
