"""M37 (Move 37) — SPEC-05 §3.1: 0-LLM Bitemporal DuckDB Projection & Graph Queries (with Pure-Python Fallback)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    import duckdb  # type: ignore[import-not-found]
except ImportError:
    duckdb = None  # type: ignore[assignment]

from m37.config import WorkspacePaths
from m37.guards import sha256_file
from m37.schemas import ContractValidationError, extract_wikilink_id, parse_raw_artifact


def _connect_rw(db_path: Path) -> Any:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    schema_sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    con.execute(schema_sql)
    return con


def _json_index_path(db_path: Path) -> Path:
    return db_path.with_suffix(".json")


def _load_json_index(db_path: Path) -> dict[str, Any]:
    jpath = _json_index_path(db_path)
    if jpath.is_file():
        try:
            return dict(json.loads(jpath.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return {
        "artefactos": [],
        "linaje_aristas": [],
        "episodios_ejecucion": [],
        "qa_defectos_hist": [],
    }


def _save_json_index(db_path: Path, data: dict[str, Any]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    jpath = _json_index_path(db_path)
    jpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if not db_path.is_file():
        db_path.write_text("m37-json-fallback-index\n", encoding="utf-8")


def _reindex_vault_to_json_fallback(paths: WorkspacePaths) -> dict[str, int]:
    db_path = paths.duckdb_index_path()
    state = _load_json_index(db_path)
    artefactos_hist: list[dict[str, Any]] = list(state.get("artefactos") or [])
    aristas: list[dict[str, Any]] = []
    episodios: list[dict[str, Any]] = []
    qa_defectos: list[dict[str, Any]] = []

    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    artefactos_count = 0
    aristas_count = 0
    plan_archetype_by_enc: dict[str, str] = {}

    vault = paths.vault_root.resolve()
    candidate_files = sorted(
        p
        for p in vault.rglob("*")
        if p.is_file()
        and p.suffix.lower() in (".md", ".json")
        and ".indice" not in p.parts
        and "00_Sistema" not in p.parts
    )

    seen_edges: set[tuple[str, str, str]] = set()

    for fpath in candidate_files:
        try:
            meta, _ = parse_raw_artifact(fpath.read_text(encoding="utf-8"), suffix=fpath.suffix)
        except ContractValidationError:
            continue
        tipo = str(meta.get("tipo") or "").strip()
        if not tipo:
            continue

        if tipo == "plan" and meta.get("encargo") and meta.get("arquetipo_base"):
            plan_archetype_by_enc[str(meta["encargo"])] = str(meta["arquetipo_base"])

        art_id = str(meta.get("id") or meta.get("id_encargo") or meta.get("id_plan") or fpath.stem)
        file_sha = sha256_file(fpath)
        encargo = str(meta.get("encargo") or meta.get("id_encargo") or "") or None
        dominio = str(meta.get("dominio") or "") or None
        metodo = str(meta.get("metodo") or "") or None
        estado_qa = str(meta.get("estado_qa") or meta.get("decision") or "") or None
        fuerza = str(meta.get("fuerza_afirmacion") or "") or None
        invalidada = bool(meta.get("invalidada", False))
        f_verif = str(meta.get("fecha_verificacion") or "") or None
        vigencia = int(meta["vigencia_dias_max"]) if meta.get("vigencia_dias_max") is not None else None
        rel_path = fpath.relative_to(vault).as_posix()

        # Bitemporal close of any older active version with different sha256
        for row in artefactos_hist:
            if row["id"] == art_id and row["sha256"] != file_sha and row["valido_hasta"] is None:
                row["valido_hasta"] = now_iso

        if not any(r["id"] == art_id and r["sha256"] == file_sha for r in artefactos_hist):
            artefactos_hist.append(
                {
                    "id": art_id,
                    "sha256": file_sha,
                    "tipo": tipo,
                    "encargo": encargo,
                    "dominio": dominio,
                    "metodo": metodo,
                    "estado_qa": estado_qa,
                    "fuerza_afirmacion": fuerza,
                    "invalidada": invalidada,
                    "fecha_verificacion": f_verif,
                    "vigencia_dias_max": vigencia,
                    "ruta_relativa": rel_path,
                    "valido_desde": now_iso,
                    "valido_hasta": None,
                    "registrado_en": now_iso,
                }
            )
        artefactos_count += 1

        def _add_edge(orig: str, dest: str, rel: str) -> None:
            nonlocal aristas_count
            key = (orig, dest, rel)
            if key not in seen_edges:
                seen_edges.add(key)
                aristas.append(
                    {
                        "origen_id": orig,
                        "destino_id": dest,
                        "relacion": rel,
                        "encargo": encargo,
                        "registrado_en": now_iso,
                    }
                )
            aristas_count += 1

        if tipo == "hallazgo":
            for ev in meta.get("sustento_evidencia") or []:
                _add_edge(extract_wikilink_id(str(ev)), art_id, "sustenta_evidencia")
            for der in meta.get("sustento_derivaciones") or []:
                _add_edge(extract_wikilink_id(str(der)), art_id, "sustenta_derivacion")
        elif tipo == "derivacion":
            for inp in meta.get("insumos") or []:
                raw_ref = inp.get("id_o_ruta", "") if isinstance(inp, dict) else str(inp)
                _add_edge(extract_wikilink_id(str(raw_ref)), art_id, "insumo_derivacion")
        elif tipo == "veredicto_qa":
            audited = extract_wikilink_id(str(meta.get("artefacto_auditado") or ""))
            if audited:
                _add_edge(art_id, audited, "audita_qa")
            for defect in meta.get("hallazgos_critica") or []:
                if isinstance(defect, dict) and defect.get("id_defecto"):
                    qa_defectos.append(
                        {
                            "veredicto_id": art_id,
                            "encargo": str(encargo or ""),
                            "artefacto_auditado": audited,
                            "ronda": int(meta.get("ronda", 1)),
                            "modelo_auditor": str(meta.get("modelo_auditor", "")),
                            "familia_auditor": str(meta.get("familia_auditor", "")),
                            "id_defecto": str(defect["id_defecto"]),
                            "severidad": str(defect.get("severidad", "menor")),
                            "categoria": str(defect.get("categoria", "formato_o_claridad")),
                            "exige_cambio_material": bool(defect.get("exige_cambio_material", False)),
                        }
                    )

    episodios_count = 0
    ep_file = paths.episodios_path()
    if ep_file.is_file():
        for idx, raw_line in enumerate(ep_file.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            enc_name = str(ev.get("encargo") or "") or None
            arq = str(ev.get("arquetipo") or (plan_archetype_by_enc.get(enc_name, "") if enc_name else "")) or None
            episodios.append(
                {
                    "id_evento": idx,
                    "timestamp_utc": str(ev.get("timestamp_utc") or ev.get("ts") or ""),
                    "evento": str(ev.get("evento") or "desconocido"),
                    "encargo": enc_name,
                    "arquetipo": arq,
                    "nodo": str(ev.get("nodo") or "") or None,
                    "perfil": str(ev.get("perfil") or "") or None,
                    "modelo": str(ev.get("modelo") or "") or None,
                    "familia": str(ev.get("familia") or "") or None,
                    "modelo_qa": str(ev.get("modelo_qa") or "") or None,
                    "aprobado_r1": ev.get("aprobado_r1"),
                    "estado_final": str(ev.get("estado_final") or "") or None,
                    "turnos": int(ev["turnos"]) if ev.get("turnos") is not None else None,
                    "costo_usd": float(ev["costo_usd"]) if ev.get("costo_usd") is not None else None,
                }
            )
            episodios_count += 1

    _save_json_index(
        db_path,
        {
            "artefactos": artefactos_hist,
            "linaje_aristas": aristas,
            "episodios_ejecucion": episodios,
            "qa_defectos_hist": qa_defectos,
        },
    )
    return {"artefactos": artefactos_count, "aristas": aristas_count, "episodios": episodios_count}


def reindex_vault_to_duckdb(paths: WorkspacePaths) -> dict[str, int]:
    """Project Markdown/JSON Vault artifacts + episodios.jsonl into bitemporal DuckDB (or JSON fallback) without calling any LLM."""
    if duckdb is None:
        return _reindex_vault_to_json_fallback(paths)
    db_path = paths.duckdb_index_path()
    con = _connect_rw(db_path)
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        con.execute("DELETE FROM linaje_aristas")
        con.execute("DELETE FROM episodios_ejecucion")
        con.execute("DELETE FROM qa_defectos_hist")

        artefactos_count = 0
        aristas_count = 0
        plan_archetype_by_enc: dict[str, str] = {}

        vault = paths.vault_root.resolve()
        candidate_files = sorted(
            p
            for p in vault.rglob("*")
            if p.is_file()
            and p.suffix.lower() in (".md", ".json")
            and ".indice" not in p.parts
            and "00_Sistema" not in p.parts
        )

        for fpath in candidate_files:
            try:
                meta, _ = parse_raw_artifact(fpath.read_text(encoding="utf-8"), suffix=fpath.suffix)
            except ContractValidationError:
                continue
            tipo = str(meta.get("tipo") or "").strip()
            if not tipo:
                continue

            if tipo == "plan" and meta.get("encargo") and meta.get("arquetipo_base"):
                plan_archetype_by_enc[str(meta["encargo"])] = str(meta["arquetipo_base"])

            art_id = str(meta.get("id") or meta.get("id_encargo") or meta.get("id_plan") or fpath.stem)
            file_sha = sha256_file(fpath)
            encargo = str(meta.get("encargo") or meta.get("id_encargo") or "") or None
            dominio = str(meta.get("dominio") or "") or None
            metodo = str(meta.get("metodo") or "") or None
            estado_qa = str(meta.get("estado_qa") or meta.get("decision") or "") or None
            fuerza = str(meta.get("fuerza_afirmacion") or "") or None
            invalidada = bool(meta.get("invalidada", False))
            f_verif = str(meta.get("fecha_verificacion") or "") or None
            vigencia = int(meta["vigencia_dias_max"]) if meta.get("vigencia_dias_max") is not None else None
            rel_path = fpath.relative_to(vault).as_posix()

            # Bitemporal close of any older active version with different sha256
            con.execute(
                """
                UPDATE artefactos
                SET valido_hasta = ?
                WHERE id = ? AND sha256 != ? AND valido_hasta IS NULL
                """,
                [now_ts, art_id, file_sha],
            )

            existing = con.execute(
                "SELECT 1 FROM artefactos WHERE id = ? AND sha256 = ?",
                [art_id, file_sha],
            ).fetchone()
            if existing is None:
                con.execute(
                    """
                    INSERT INTO artefactos (
                        id, sha256, tipo, encargo, dominio, metodo, estado_qa,
                        fuerza_afirmacion, invalidada, fecha_verificacion, vigencia_dias_max,
                        ruta_relativa, valido_desde, valido_hasta, registrado_en
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    [
                        art_id,
                        file_sha,
                        tipo,
                        encargo,
                        dominio,
                        metodo,
                        estado_qa,
                        fuerza,
                        invalidada,
                        f_verif,
                        vigencia,
                        rel_path,
                        now_ts,
                        now_ts,
                    ],
                )
            artefactos_count += 1

            # Record lineage edges
            if tipo == "hallazgo":
                for ev in meta.get("sustento_evidencia") or []:
                    src_id = extract_wikilink_id(str(ev))
                    con.execute(
                        "INSERT OR IGNORE INTO linaje_aristas VALUES (?, ?, 'sustenta_evidencia', ?, ?)",
                        [src_id, art_id, encargo, now_ts],
                    )
                    aristas_count += 1
                for der in meta.get("sustento_derivaciones") or []:
                    src_id = extract_wikilink_id(str(der))
                    con.execute(
                        "INSERT OR IGNORE INTO linaje_aristas VALUES (?, ?, 'sustenta_derivacion', ?, ?)",
                        [src_id, art_id, encargo, now_ts],
                    )
                    aristas_count += 1
            elif tipo == "derivacion":
                for inp in meta.get("insumos") or []:
                    raw_ref = inp.get("id_o_ruta", "") if isinstance(inp, dict) else str(inp)
                    src_id = extract_wikilink_id(str(raw_ref))
                    con.execute(
                        "INSERT OR IGNORE INTO linaje_aristas VALUES (?, ?, 'insumo_derivacion', ?, ?)",
                        [src_id, art_id, encargo, now_ts],
                    )
                    aristas_count += 1
            elif tipo == "veredicto_qa":
                audited = extract_wikilink_id(str(meta.get("artefacto_auditado") or ""))
                if audited:
                    con.execute(
                        "INSERT OR IGNORE INTO linaje_aristas VALUES (?, ?, 'audita_qa', ?, ?)",
                        [art_id, audited, encargo, now_ts],
                    )
                    aristas_count += 1
                for defect in meta.get("hallazgos_critica") or []:
                    if isinstance(defect, dict) and defect.get("id_defecto"):
                        con.execute(
                            """
                            INSERT OR REPLACE INTO qa_defectos_hist VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            [
                                art_id,
                                str(encargo or ""),
                                audited,
                                int(meta.get("ronda", 1)),
                                str(meta.get("modelo_auditor", "")),
                                str(meta.get("familia_auditor", "")),
                                str(defect["id_defecto"]),
                                str(defect.get("severidad", "menor")),
                                str(defect.get("categoria", "formato_o_claridad")),
                                bool(defect.get("exige_cambio_material", False)),
                            ],
                        )

        # Index episodios.jsonl
        episodios_count = 0
        ep_file = paths.episodios_path()
        if ep_file.is_file():
            for idx, raw_line in enumerate(ep_file.read_text(encoding="utf-8").splitlines(), start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                enc_name = str(ev.get("encargo") or "") or None
                arq = str(ev.get("arquetipo") or (plan_archetype_by_enc.get(enc_name, "") if enc_name else "")) or None
                con.execute(
                    """
                    INSERT INTO episodios_ejecucion VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        idx,
                        str(ev.get("timestamp_utc") or ""),
                        str(ev.get("evento") or "desconocido"),
                        enc_name,
                        arq,
                        str(ev.get("nodo") or "") or None,
                        str(ev.get("perfil") or "") or None,
                        str(ev.get("modelo") or "") or None,
                        str(ev.get("familia") or "") or None,
                        str(ev.get("modelo_qa") or "") or None,
                        ev.get("aprobado_r1"),
                        str(ev.get("estado_final") or "") or None,
                        int(ev["turnos"]) if ev.get("turnos") is not None else None,
                        float(ev["costo_usd"]) if ev.get("costo_usd") is not None else None,
                    ],
                )
                episodios_count += 1

        return {"artefactos": artefactos_count, "aristas": aristas_count, "episodios": episodios_count}
    finally:
        con.close()


# ======================================================================
# 5 Read-Only Analytical Graph Queries (SPEC-05 §3.1)
# ======================================================================
def query_fuentes_por_vencer(db_path: Path, dias_horizonte: int = 30) -> list[dict[str, Any]]:
    if duckdb is None:
        state = _load_json_index(db_path)
        today = date.today()
        out: list[dict[str, Any]] = []
        for r in state.get("artefactos") or []:
            if r.get("tipo") != "evidencia" or r.get("valido_hasta") is not None:
                continue
            f_verif = r.get("fecha_verificacion")
            if not f_verif:
                continue
            try:
                verif_date = date.fromisoformat(str(f_verif)[:10])
            except ValueError:
                continue
            vig = r.get("vigencia_dias_max")
            dias_rest = (int(vig) - (today - verif_date).days) if vig is not None else None
            inv = bool(r.get("invalidada", False))
            if inv or (dias_rest is not None and dias_rest <= dias_horizonte):
                out.append(
                    {
                        "id": r["id"],
                        "ruta_relativa": r["ruta_relativa"],
                        "fecha_verificacion": str(f_verif),
                        "vigencia_dias_max": vig,
                        "invalidada": inv,
                        "dias_restantes": dias_rest,
                    }
                )
        out.sort(key=lambda x: (x["dias_restantes"] is None, x["dias_restantes"] or 0))
        return out
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT id, ruta_relativa, CAST(fecha_verificacion AS VARCHAR) AS fecha_verificacion,
                   vigencia_dias_max, invalidada,
                   (vigencia_dias_max - DATE_DIFF('day', fecha_verificacion, CURRENT_DATE)) AS dias_restantes
            FROM artefactos
            WHERE tipo = 'evidencia'
              AND valido_hasta IS NULL
              AND fecha_verificacion IS NOT NULL
              AND (invalidada = TRUE OR (vigencia_dias_max - DATE_DIFF('day', fecha_verificacion, CURRENT_DATE)) <= ?)
            ORDER BY dias_restantes ASC
            """,
            [dias_horizonte],
        ).fetchall()
        cols = ["id", "ruta_relativa", "fecha_verificacion", "vigencia_dias_max", "invalidada", "dias_restantes"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()


def query_desempeno_empirico_modelos(db_path: Path) -> list[dict[str, Any]]:
    if duckdb is None:
        state = _load_json_index(db_path)
        groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
        for ev in state.get("episodios_ejecucion") or []:
            if ev.get("evento") == "nodo_ejecutado" and ev.get("modelo") is not None:
                key = (ev.get("modelo"), ev.get("familia"), ev.get("perfil"))
                groups.setdefault(key, []).append(ev)
        res: list[dict[str, Any]] = []
        for (modelo, familia, perfil), items in groups.items():
            n = len(items)
            r1_rate = round(sum(1.0 if i.get("aprobado_r1") else 0.0 for i in items) / n, 3)
            costs = [float(i["costo_usd"]) for i in items if i.get("costo_usd") is not None]
            turns = [float(i["turnos"]) for i in items if i.get("turnos") is not None]
            res.append(
                {
                    "modelo": modelo,
                    "familia": familia,
                    "perfil": perfil,
                    "ejecuciones": n,
                    "tasa_aprobacion_r1": r1_rate,
                    "costo_medio_usd": round(sum(costs) / len(costs), 4) if costs else None,
                    "turnos_medios": round(sum(turns) / len(turns), 1) if turns else None,
                }
            )
        res.sort(key=lambda x: (-x["ejecuciones"], -(x["tasa_aprobacion_r1"] or 0.0)))
        return res
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT modelo, familia, perfil,
                   COUNT(*) AS ejecuciones,
                   ROUND(AVG(CASE WHEN aprobado_r1 THEN 1.0 ELSE 0.0 END), 3) AS tasa_aprobacion_r1,
                   ROUND(AVG(costo_usd), 4) AS costo_medio_usd,
                   ROUND(AVG(turnos), 1) AS turnos_medios
            FROM episodios_ejecucion
            WHERE evento = 'nodo_ejecutado' AND modelo IS NOT NULL
            GROUP BY modelo, familia, perfil
            ORDER BY ejecuciones DESC, tasa_aprobacion_r1 DESC
            """
        ).fetchall()
        cols = [
            "modelo",
            "familia",
            "perfil",
            "ejecuciones",
            "tasa_aprobacion_r1",
            "costo_medio_usd",
            "turnos_medios",
        ]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()


def query_patrones_rechazo_qa(db_path: Path) -> list[dict[str, Any]]:
    if duckdb is None:
        state = _load_json_index(db_path)
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for d in state.get("qa_defectos_hist") or []:
            key = (str(d.get("categoria") or ""), str(d.get("severidad") or ""))
            groups.setdefault(key, []).append(d)
        res: list[dict[str, Any]] = []
        for (cat, sev), items in groups.items():
            res.append(
                {
                    "categoria": cat,
                    "severidad": sev,
                    "ocurrencias": len(items),
                    "exigieron_cambio_material": sum(1 for i in items if i.get("exige_cambio_material")),
                }
            )
        res.sort(key=lambda x: -x["ocurrencias"])
        return res
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT categoria, severidad,
                   COUNT(*) AS ocurrencias,
                   SUM(CASE WHEN exige_cambio_material THEN 1 ELSE 0 END) AS exigieron_cambio_material
            FROM qa_defectos_hist
            GROUP BY categoria, severidad
            ORDER BY ocurrencias DESC
            """
        ).fetchall()
        cols = ["categoria", "severidad", "ocurrencias", "exigieron_cambio_material"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()


def query_cuellos_presupuesto_arquetipo(db_path: Path) -> list[dict[str, Any]]:
    if duckdb is None:
        state = _load_json_index(db_path)
        groups: dict[tuple[str, Any], list[dict[str, Any]]] = {}
        for ev in state.get("episodios_ejecucion") or []:
            if ev.get("evento") == "nodo_ejecutado":
                arq = str(ev.get("arquetipo") or "sin_arquetipo")
                key = (arq, ev.get("perfil"))
                groups.setdefault(key, []).append(ev)
        res: list[dict[str, Any]] = []
        for (arq, perfil), items in groups.items():
            res.append(
                {
                    "arquetipo": arq,
                    "perfil": perfil,
                    "nodos_totales": len(items),
                    "costo_total_usd": round(sum(float(i.get("costo_usd") or 0.0) for i in items), 4),
                    "bloqueos_presupuesto": sum(
                        1 for i in items if i.get("estado_final") == "bloqueado_presupuesto"
                    ),
                }
            )
        res.sort(key=lambda x: -x["costo_total_usd"])
        return res
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT COALESCE(arquetipo, 'sin_arquetipo') AS arquetipo,
                   perfil,
                   COUNT(*) AS nodos_totales,
                   ROUND(SUM(COALESCE(costo_usd, 0.0)), 4) AS costo_total_usd,
                   SUM(CASE WHEN estado_final = 'bloqueado_presupuesto' THEN 1 ELSE 0 END) AS bloqueos_presupuesto
            FROM episodios_ejecucion
            WHERE evento = 'nodo_ejecutado'
            GROUP BY COALESCE(arquetipo, 'sin_arquetipo'), perfil
            ORDER BY costo_total_usd DESC
            """
        ).fetchall()
        cols = ["arquetipo", "perfil", "nodos_totales", "costo_total_usd", "bloqueos_presupuesto"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()


def query_linaje_artefacto(db_path: Path, artefacto_id: str) -> dict[str, Any]:
    if duckdb is None:
        state = _load_json_index(db_path)
        aristas = state.get("linaje_aristas") or []
        artefactos = state.get("artefactos") or []
        upstream = sorted(
            [{"id": a["origen_id"], "relacion": a["relacion"]} for a in aristas if a["destino_id"] == artefacto_id],
            key=lambda x: x["id"],
        )
        downstream = sorted(
            [{"id": a["destino_id"], "relacion": a["relacion"]} for a in aristas if a["origen_id"] == artefacto_id],
            key=lambda x: x["id"],
        )
        versions = [
            {
                "sha256": r["sha256"],
                "estado_qa": r["estado_qa"],
                "invalidada": r["invalidada"],
                "valido_desde": r["valido_desde"],
                "valido_hasta": r["valido_hasta"],
            }
            for r in artefactos
            if r["id"] == artefacto_id
        ]
        return {
            "artefacto_id": artefacto_id,
            "insumos_upstream": upstream,
            "dependientes_downstream": downstream,
            "historial_bitemporal": versions,
        }
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        upstream = con.execute(
            "SELECT origen_id, relacion FROM linaje_aristas WHERE destino_id = ? ORDER BY origen_id",
            [artefacto_id],
        ).fetchall()
        downstream = con.execute(
            "SELECT destino_id, relacion FROM linaje_aristas WHERE origen_id = ? ORDER BY destino_id",
            [artefacto_id],
        ).fetchall()
        versions = con.execute(
            """
            SELECT sha256, estado_qa, invalidada,
                   CAST(valido_desde AS VARCHAR) AS valido_desde,
                   CAST(valido_hasta AS VARCHAR) AS valido_hasta
            FROM artefactos
            WHERE id = ?
            ORDER BY valido_desde ASC
            """,
            [artefacto_id],
        ).fetchall()
        return {
            "artefacto_id": artefacto_id,
            "insumos_upstream": [{"id": r[0], "relacion": r[1]} for r in upstream],
            "dependientes_downstream": [{"id": r[0], "relacion": r[1]} for r in downstream],
            "historial_bitemporal": [
                {
                    "sha256": r[0],
                    "estado_qa": r[1],
                    "invalidada": r[2],
                    "valido_desde": r[3],
                    "valido_hasta": r[4],
                }
                for r in versions
            ],
        }
    finally:
        con.close()
