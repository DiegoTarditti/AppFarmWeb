"""Crea tabla obs_codigos_barras en Render directamente, sin pasar por init_db
(que demora demasiado en el plan free). Idempotente."""
import os

from sqlalchemy import create_engine, text


def main(db_url):
    e = create_engine(db_url, pool_pre_ping=True)
    with e.connect() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS obs_codigos_barras (
                id_codigo_barras INTEGER PRIMARY KEY,
                producto_observer INTEGER NOT NULL REFERENCES obs_productos(observer_id),
                codigo_barras VARCHAR(20) NOT NULL,
                orden INTEGER NOT NULL DEFAULT 1,
                fecha_ingreso TIMESTAMP,
                fecha_baja TIMESTAMP,
                sync_en TIMESTAMP DEFAULT NOW()
            )
        """))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_obs_cb_prod ON obs_codigos_barras(producto_observer)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_obs_cb_ean  ON obs_codigos_barras(codigo_barras)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_obs_cb_baja ON obs_codigos_barras(fecha_baja)"))
        c.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS compra_minima_pesos DECIMAL(14,2)"))
        c.commit()
    print('OK — obs_codigos_barras + compra_minima_pesos en Render')


if __name__ == '__main__':
    main(os.environ['DATABASE_URL'])
