# Seed de datos para Compra del día

`seed_pedidos_dia.sql.gz` es un dump `--data-only` de las tablas necesarias para
probar el flujo `/compras/dia` desde otra máquina (sin `obs_ventas_detalle` que
pesa 200+ MB).

## Tablas incluidas

Catálogo:
- `laboratorios`, `proveedores`, `productos`
- `obs_laboratorios`, `obs_rubros`, `obs_subrubros`
- `obs_productos`, `obs_stock`, `obs_ventas_mensuales`, `obs_codigos_barras`

Relaciones del flujo:
- `laboratorio_drogueria`, `descuentos_base`, `proveedor_horarios_reparto`
- `pedido_emitido`, `pedido_emitido_item`

## Cómo restaurar

Asume que ya levantaste la app una vez (para que `init_db` cree las tablas
vacías). Después:

```bash
gunzip -c dumps/seed_pedidos_dia.sql.gz | \
  docker-compose exec -T db psql -U postgres -d farmacia
```

Si querés vaciar antes para no duplicar:

```bash
docker-compose exec -T db psql -U postgres -d farmacia -c "
TRUNCATE pedido_emitido_item, pedido_emitido,
         laboratorio_drogueria, descuentos_base, proveedor_horarios_reparto,
         obs_codigos_barras, obs_ventas_mensuales, obs_stock, obs_productos,
         obs_subrubros, obs_rubros, obs_laboratorios,
         productos, proveedores, laboratorios CASCADE;"
```

## Cómo regenerar el dump

```bash
docker-compose exec -T db pg_dump -U postgres -d farmacia \
  --data-only --no-owner --no-privileges \
  -t laboratorios -t proveedores -t productos \
  -t obs_laboratorios -t obs_rubros -t obs_subrubros \
  -t obs_productos -t obs_stock -t obs_ventas_mensuales -t obs_codigos_barras \
  -t laboratorio_drogueria -t descuentos_base -t proveedor_horarios_reparto \
  -t pedido_emitido -t pedido_emitido_item \
  > dumps/seed_pedidos_dia.sql
gzip -9 -f dumps/seed_pedidos_dia.sql
```
