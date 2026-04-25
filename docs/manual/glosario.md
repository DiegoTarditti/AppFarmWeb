# Glosario

> ⚠ STATUS: PENDIENTE — completar definiciones

Términos que aparecen en todo el sistema. Linkear desde otros docs hacia este, no repetir definiciones.

---

## A

### Alfabeta
_(Código numérico de Alfabeta — base de datos farmacéutica argentina. ObServer indexa productos por este código, no por EAN.)_

## C

### Canal de compra
_(Cómo entra la mercadería: directo del laboratorio o vía droguería. Se elige en el paso 4 del análisis del pedido.)_

### Codigo_alfabeta
Ver [Alfabeta](#alfabeta).

## D

### Droguería
_(Intermediario que vende productos de varios laboratorios. Tiene CUIT propio y emite factura. En el sistema viven en la tabla `proveedores`.)_

## E

### EAN
_(European Article Number — el código de barras de 13 dígitos impreso en cada producto. En Argentina los productos farmacéuticos usan EANs que empiezan con 779.)_

## I

### IdProducto
_(El ID que ObServer asigna a cada producto. Lo usamos en `obs_productos.observer_id` y como bridge vía `productos.observer_id`.)_

## L

### Laboratorio
_(Fabricante. En el sistema viven en la tabla `laboratorios` y tienen su contraparte en `obs_laboratorios`.)_

## M

### Monodroga
_(El principio activo de un medicamento — ej. Paracetamol, Ibuprofeno. Distintos laboratorios pueden vender la misma monodroga con marcas distintas (Tafirol vs Geniol = ambos Paracetamol).)_

### Modulo
_(Lista de productos con cantidad y descuento que un laboratorio ofrece como paquete. Se importa por Excel.)_

## O

### ObServer
_(Sistema de gestión existente en la farmacia, corre sobre SQL Server 2014. AppFarmWeb se conecta a su DB para traer ventas, stock y catálogo. Las tablas locales `obs_*` son cache.)_

### Oferta con mínimo
_(Compra de N unidades del mismo producto que dispara un descuento extra del laboratorio. Se importa de Excel.)_

## P

### Pedido
_(Resultado del análisis de compra: lista de productos con cantidad sugerida. Vive en `pedidos` y `pedido_items`.)_

### Proceso de compra
_(Wrapper sobre un pedido que rastrea su ciclo: ANALISIS → PEDIDO → FACTURA → CRUCE → CERRADO.)_

## R

### Rotación
_(Clasificación de un producto según velocidad de venta: Alta / Media / Baja.)_

## S

### Stock dormido
_(Producto que tiene stock pero no tuvo ventas en los últimos 3 meses. Capital congelado.)_
