# Contratos Agnósticos de Datos M37 (`SPEC-03`)

Todos los artefactos del Vault son validados en tiempo de escritura por `src/m37/schemas.py` (Pydantic v2):

- `brief` (`00_Brief.md`): `BriefContract`
- `plan` (`01_Plan.md`): `PlanContract`
- `nodo` (`Nodos/N-*.md`): `NodeContract`
- `evidencia` (`30_Evidencia_Publica/E-*.md` y `Evidencia_Interna/EI-*.md`): `EvidenceContract`
- `derivacion` (`Derivaciones/D-*.json` o `.md`): `DerivationContract`
- `hallazgo` (`Hallazgos/H-*.md`): `FindingContract`
- `veredicto_qa` (`QA/V-*.md`): `QAVerdictContract`
