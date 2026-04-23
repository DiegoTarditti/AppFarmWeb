# Analíticas pendientes de implementar

Todas viables con las tablas `obs_*` que ya están sincronizadas. No requiere cambios de schema.

## 1. Ventas por producto — breakdown por laboratorio

**Qué**: para un producto dado (o su monodroga), mostrar todas las variantes con la misma descripción o misma monodroga, con ventas agrupadas por laboratorio.

**Pregunta que responde**: "Para el paracetamol 500mg, ¿qué laboratorio vende más unidades?"

**Datos**: `obs_ventas_mensuales` + `obs_productos` + `obs_laboratorios` + (opcional) `obs_nombres_drogas`.

**Query base**:
```sql
SELECT l.descripcion AS laboratorio,
       p.descripcion AS producto,
       SUM(v.unidades) AS unidades,
       SUM(v.monto)    AS monto,
       COUNT(DISTINCT (v.anio*100 + v.mes)) AS meses_con_venta
FROM obs_ventas_mensuales v
JOIN obs_productos p      ON p.observer_id = v.producto_observer
JOIN obs_laboratorios l   ON l.observer_id = p.laboratorio_observer
WHERE p.nombre_droga_observer = :droga_id
  AND (v.anio * 100 + v.mes) BETWEEN :desde AND :hasta
GROUP BY l.descripcion, p.descripcion
ORDER BY SUM(v.unidades) DESC;
```

**UI sugerida**: desde la ficha de un producto local o desde `/productos/<id>`, botón "Comparar con otros labs" → tabla pivot con labs en filas, meses en columnas.

## 2. Ventas por monodroga — árbol (droga → laboratorio → producto)

**Qué**: dada una monodroga, expandir todos los productos de todos los labs que la contienen, con ventas agregadas en distintos niveles.

**Pregunta que responde**: "Para ibuprofeno, ¿cómo se reparte la venta entre laboratorios y entre sus presentaciones?"

**Datos**: mismas tablas.

**Estructura**:
```
Ibuprofeno (monodroga, 10.000 un)
├── Bayer (3.500 un)
│   ├── Actron 400mg (2.000 un)
│   └── Actron 600mg (1.500 un)
├── Bagó (2.800 un)
│   └── Ibupirac 400mg (2.800 un)
...
```

**Query base**: dos niveles de agregación en una misma query, render con árbol colapsable en el frontend.

## Consideraciones

- Para ambas, conviene tener un índice en `obs_productos(nombre_droga_observer)` si las consultas se vuelven lentas (ya está el de laboratorio_observer).
- Ambas se beneficiarían de agregar también un filtro por período (ej. últimos 6 meses) para no cargar todo.
- Si las tablas crecen mucho, considerar una vista materializada `obs_ventas_por_droga_lab` precalculada como parte del sync.

## Dónde vivirían en la app

- Ruta: `/productos/<id>/analisis-mercado` (breakdown por lab para un producto)
- Ruta: `/analisis/monodroga/<droga_id>` (árbol droga → lab → producto)
- Link desde ficha producto + desde tabla monodrogas.
