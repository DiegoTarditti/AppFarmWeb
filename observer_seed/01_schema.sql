-- Esquema simulado de ObServer
-- Imita las tablas desde las que hoy importamos PDF/Excel
-- Solo las columnas que realmente se consultan; sin las extensiones propias (ej: rotacion, tipo).

CREATE TABLE articulos (
    codigo_barra       VARCHAR(20) PRIMARY KEY,
    descripcion        VARCHAR(200) NOT NULL,
    laboratorio        VARCHAR(150),
    monodroga          VARCHAR(200),
    presentacion       VARCHAR(500),
    accion_terapeutica VARCHAR(200),
    precio_pvp         DECIMAL(14, 2),
    stock_actual       INTEGER NOT NULL DEFAULT 0,
    actualizado_en     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_articulos_laboratorio ON articulos(laboratorio);

-- Ventas agregadas por producto y mes (equivalente a lo que hoy parsea sales_history)
CREATE TABLE ventas_mensuales (
    id                SERIAL PRIMARY KEY,
    codigo_barra      VARCHAR(20) NOT NULL REFERENCES articulos(codigo_barra) ON DELETE CASCADE,
    anio              INTEGER NOT NULL,
    mes               INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    unidades          INTEGER NOT NULL DEFAULT 0,
    monto             DECIMAL(14, 2) NOT NULL DEFAULT 0,
    UNIQUE(codigo_barra, anio, mes)
);

CREATE INDEX idx_ventas_mes ON ventas_mensuales(anio, mes);
CREATE INDEX idx_ventas_ean ON ventas_mensuales(codigo_barra);

-- Recepciones de mercadería (equivalente a lo que hoy se sube como Excel ERP para el cruce)
CREATE TABLE recepciones (
    id                 SERIAL PRIMARY KEY,
    fecha_recepcion    DATE NOT NULL,
    proveedor_cuit     VARCHAR(20),
    proveedor_nombre   VARCHAR(200),
    numero_factura     VARCHAR(50),
    codigo_barra       VARCHAR(20) NOT NULL,
    descripcion        VARCHAR(200),
    cantidad           INTEGER NOT NULL DEFAULT 0,
    precio_unitario    DECIMAL(14, 2),
    lote               VARCHAR(30),
    vencimiento        DATE,
    creado_en          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_recepciones_factura ON recepciones(numero_factura);
CREATE INDEX idx_recepciones_cuit ON recepciones(proveedor_cuit);
CREATE INDEX idx_recepciones_ean ON recepciones(codigo_barra);

-- Stock histórico por día (opcional, para reportes de evolución)
CREATE TABLE stock_diario (
    id            SERIAL PRIMARY KEY,
    fecha         DATE NOT NULL,
    codigo_barra  VARCHAR(20) NOT NULL,
    cantidad      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(fecha, codigo_barra)
);

CREATE INDEX idx_stock_diario_fecha ON stock_diario(fecha);
