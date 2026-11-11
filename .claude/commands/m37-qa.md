---
description: Ejecuta Fase A mecánica y lanza el subagente aislado m37-qa-ciego sobre un Hallazgo M37.
---

Audita con QA Ciego el Hallazgo indicado en `$ARGUMENTS`:

1. Llama a `m37_prepare_blind_qa` para validar Fase A mecánica y obtener `allowlist_files` y `subagent_prompt`.
2. Si `phase_a_passed` es `true`, invoca al subagente `m37-qa-ciego` pasándole exclusivamente el `subagent_prompt` para que audite a ciegas y registre el veredicto con `m37_record_qa_verdict`.
