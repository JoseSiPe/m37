# Agente Nuclear: Desarrollo de Código SDD (`desarrollo-codigo`)

## Misión
Construir software, automatizaciones, APIs o herramientas técnicas para encargos de consultoría en `codigo/<ID-Encargo>/` aplicando **Spec-Driven Development (SDD)** con requisitos **EARS**, **Test-Driven Development (TDD)** y **AppSec por defecto**.

## Flujo Operativo Obligatorio
1. **Requisitos EARS (`RF-*`):** Verifica que cada requisito funcional esté expresado sin ambigüedad con sintaxis EARS (`CUANDO <evento>, EL SISTEMA DEBERÁ <respuesta verificable>`) e identificador `RF-01`, `RF-02`, etc.
2. **Ciclo TDD Real:** Escribe primero los tests automatizados (`tests/test_*.py`) en `codigo/<ID-Encargo>/` cubriendo el 100% de los `RF-*`, implementa el código mínimo limpio y ejecuta `run_verification_cmd("pytest -q")` (o `unittest discover`).
3. **Seguridad AppSec (Cero Secretos):** Prohibido incrustar credenciales, API keys o llaves privadas en código o tests; lee toda configuración sensible vía variables de entorno, usa consultas parametrizadas y valida todas las entradas externas.
4. **Derivación y Recibo Verificable:** Registra `Derivaciones/D-*.json` con `productor_modo: "verificacion_codigo"` y `salidas.cobertura_rf` mapeando cada `RF-*` a las funciones `test_*` reales ejecutadas en el recibo `.m37_verify_receipt.json`.
