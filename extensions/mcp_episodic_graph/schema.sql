-- M37 (Move 37) — SPEC-05 §3.1: Bitemporal Read-Only Analytical Graph Schema (DuckDB, 0 LLM Tokens)

CREATE TABLE IF NOT EXISTS artefactos (
    id VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    tipo VARCHAR NOT NULL,
    encargo VARCHAR,
    dominio VARCHAR,
    metodo VARCHAR,
    estado_qa VARCHAR,
    fuerza_afirmacion VARCHAR,
    invalidada BOOLEAN DEFAULT FALSE,
    fecha_verificacion DATE,
    vigencia_dias_max INTEGER,
    ruta_relativa VARCHAR NOT NULL,
    valido_desde TIMESTAMP NOT NULL,
    valido_hasta TIMESTAMP,
    registrado_en TIMESTAMP NOT NULL,
    PRIMARY KEY (id, sha256)
);

CREATE TABLE IF NOT EXISTS linaje_aristas (
    origen_id VARCHAR NOT NULL,
    destino_id VARCHAR NOT NULL,
    relacion VARCHAR NOT NULL,
    encargo VARCHAR,
    registrado_en TIMESTAMP NOT NULL,
    PRIMARY KEY (origen_id, destino_id, relacion)
);

CREATE TABLE IF NOT EXISTS episodios_ejecucion (
    idx INTEGER PRIMARY KEY,
    timestamp_utc VARCHAR,
    evento VARCHAR NOT NULL,
    encargo VARCHAR,
    arquetipo VARCHAR,
    nodo VARCHAR,
    perfil VARCHAR,
    modelo VARCHAR,
    familia VARCHAR,
    modelo_qa VARCHAR,
    aprobado_r1 BOOLEAN,
    estado_final VARCHAR,
    turnos INTEGER,
    costo_usd DOUBLE
);

CREATE TABLE IF NOT EXISTS qa_defectos_hist (
    veredicto_id VARCHAR NOT NULL,
    encargo VARCHAR NOT NULL,
    artefacto_auditado VARCHAR NOT NULL,
    ronda INTEGER NOT NULL,
    modelo_auditor VARCHAR NOT NULL,
    familia_auditor VARCHAR NOT NULL,
    id_defecto VARCHAR NOT NULL,
    severidad VARCHAR NOT NULL,
    categoria VARCHAR NOT NULL,
    exige_cambio_material BOOLEAN NOT NULL,
    PRIMARY KEY (veredicto_id, id_defecto)
);
