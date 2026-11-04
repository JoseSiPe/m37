---
id: sdd-ears-tdd
tipo_metodo: ingenieria_software_verificable
validador_plugin: sdd_codigo
---

# Método: Construcción de Software SDD + EARS + TDD + AppSec

## Protocolo Verificable (`validador_plugin: sdd_codigo`)
1. **Especificación EARS:** Cada requisito funcional lleva identificador `RF-XX` y cláusula verificable (`CUANDO ..., EL SISTEMA DEBERÁ ...`).
2. **TDD con Recibo Criptográfico:** Ejecutar `run_verification_cmd("pytest -q")` en `codigo/<ID-Encargo>/` para generar `.m37_verify_receipt.json` con `exit_code == 0` y `tree_sha256` coincidente con el árbol vivo.
3. **Cobertura 100% de Requisitos:** `Derivaciones/D-*.json` debe declarar `productor_modo: verificacion_codigo` y mapear el 100% de los `RF-*` en `salidas.cobertura_rf` hacia funciones `def test_*` reales dentro de `codigo/<ID-Encargo>/`.
