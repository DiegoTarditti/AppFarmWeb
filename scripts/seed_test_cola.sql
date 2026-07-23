-- Seed de prueba para test local de la cola de despachos clínica.
-- Se ejecuta contra la DB local `farmacia` (docker-compose).
-- Crea las 3 tablas mínimas que necesita el endpoint /atencion/api/despachos-clinica
-- (que en Render viven en Badia por la fusión con AppClinica) + 2 despachos
-- de prueba: uno con fecha HOY (aparece en la cola) y uno con fecha +30 días
-- (no aparece, solo aparecerá su día).
--
-- Uso:
--   docker-compose exec -T db psql -U postgres -d farmacia < scripts/seed_test_cola.sql

BEGIN;

-- ── Tablas mínimas (subset de las de AppClinica, sin FKs cross-schema) ──

CREATE TABLE IF NOT EXISTS pacientes (
    id             SERIAL PRIMARY KEY,
    apellido       VARCHAR(80),
    nombre         VARCHAR(80),
    dni            VARCHAR(20),
    observer_id    INTEGER,
    telefono       VARCHAR(35),
    domicilio      VARCHAR(200),
    ciudad         VARCHAR(120),
    afiliado_nro   VARCHAR(40),
    obra_social_id INTEGER
);

-- Migraciones idempotentes por si la tabla ya existía de un seed anterior
-- sin estas columnas.
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS afiliado_nro VARCHAR(40);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS obra_social_id INTEGER;

-- Tabla obras_sociales de AppClinica (para el JOIN con obra_social_nombre).
CREATE TABLE IF NOT EXISTS obras_sociales (
    id     SERIAL PRIMARY KEY,
    nombre VARCHAR(120) NOT NULL,
    activa BOOLEAN DEFAULT TRUE
);
INSERT INTO obras_sociales (id, nombre) VALUES (1, 'PAMI') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS paciente_medicamentos (
    id                    SERIAL PRIMARY KEY,
    paciente_id           INTEGER REFERENCES pacientes(id) ON DELETE CASCADE,
    producto_snapshot     VARCHAR(200),
    observer_id_producto  INTEGER,
    cantidad              INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS despachos_programados (
    id                       SERIAL PRIMARY KEY,
    paciente_medicamento_id  INTEGER REFERENCES paciente_medicamentos(id) ON DELETE CASCADE,
    paciente_id              INTEGER REFERENCES pacientes(id) ON DELETE CASCADE,
    fecha_programada         DATE NOT NULL,
    modalidad                VARCHAR(20),
    estado                   VARCHAR(20) DEFAULT 'a_confirmar',
    pedido_reparto_id        INTEGER,
    programado_en            TIMESTAMP,
    creado_en                TIMESTAMP DEFAULT NOW()
);

-- ── Datos de prueba (limpiamos primero para poder correr el script varias veces) ──

DELETE FROM despachos_programados WHERE paciente_id IN (
    SELECT id FROM pacientes WHERE dni IN ('99999901', '99999902')
);
DELETE FROM paciente_medicamentos WHERE paciente_id IN (
    SELECT id FROM pacientes WHERE dni IN ('99999901', '99999902')
);
DELETE FROM pacientes WHERE dni IN ('99999901', '99999902');

-- Paciente 1 (con observer_id — se puede vincular en la ficha), PAMI
INSERT INTO pacientes (apellido, nombre, dni, observer_id, telefono, domicilio, ciudad, afiliado_nro, obra_social_id)
VALUES ('TARDITTI', 'Diego', '99999901', 99999999, '3411234567', 'Corrientes 1500', 'Rosario', '150-12345678-01', 1);

-- Paciente 2 (sin observer_id — lead local), PAMI
INSERT INTO pacientes (apellido, nombre, dni, telefono, domicilio, ciudad, afiliado_nro, obra_social_id)
VALUES ('GONZÁLEZ', 'María', '99999902', '3417654321', 'Mendoza 900', 'Rosario', '150-98765432-02', 1);

-- Medicamento del paciente 1
INSERT INTO paciente_medicamentos (paciente_id, producto_snapshot, observer_id_producto, cantidad)
SELECT id, 'ENALAPRIL 10 mg × 30 comp', 45678, 1
FROM pacientes WHERE dni = '99999901';

-- Medicamento del paciente 2
INSERT INTO paciente_medicamentos (paciente_id, producto_snapshot, observer_id_producto, cantidad)
SELECT id, 'ATORVASTATINA 20 mg × 30 comp', 45679, 1
FROM pacientes WHERE dni = '99999902';

-- Despacho 1: paciente 1, fecha HOY, modalidad envío → aparece en la cola.
INSERT INTO despachos_programados (paciente_medicamento_id, paciente_id, fecha_programada, modalidad, estado)
SELECT pm.id, pm.paciente_id, (NOW() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date, 'envio_domicilio', 'a_confirmar'
FROM paciente_medicamentos pm JOIN pacientes p ON p.id = pm.paciente_id
WHERE p.dni = '99999901';

-- Despacho 2: paciente 2, fecha HOY, modalidad retiro → aparece en la cola.
INSERT INTO despachos_programados (paciente_medicamento_id, paciente_id, fecha_programada, modalidad, estado)
SELECT pm.id, pm.paciente_id, (NOW() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date, 'retiro_local', 'a_confirmar'
FROM paciente_medicamentos pm JOIN pacientes p ON p.id = pm.paciente_id
WHERE p.dni = '99999902';

-- Despacho 3: paciente 1, fecha +30 días → NO aparece hasta ese día (control).
INSERT INTO despachos_programados (paciente_medicamento_id, paciente_id, fecha_programada, modalidad, estado)
SELECT pm.id, pm.paciente_id, (NOW() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date + 30, 'envio_domicilio', 'a_confirmar'
FROM paciente_medicamentos pm JOIN pacientes p ON p.id = pm.paciente_id
WHERE p.dni = '99999901';

COMMIT;

-- Chequeo: la cola debería devolver 2 filas (los 2 de fecha HOY, no el de +30d).
SELECT d.id AS despacho_id, d.fecha_programada, d.modalidad,
       pm.producto_snapshot, p.apellido, p.nombre
  FROM despachos_programados d
  JOIN paciente_medicamentos pm ON pm.id = d.paciente_medicamento_id
  JOIN pacientes p ON p.id = d.paciente_id
 WHERE d.estado = 'a_confirmar' AND d.fecha_programada <= (NOW() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date
 ORDER BY d.fecha_programada, p.apellido;
