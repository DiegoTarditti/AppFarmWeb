
- Host: localhost (porque el contenedor expone el puerto en tu máquina)
- Puerto: 5432
- Base de datos: farmacia
- Usuario: postgres
- Contraseña: postgres

el numero que aparece al lado de la "rotacion" aclarar en la columna qué es
Ahora entiendo el problema. La lógica de Cant.Calc. calcula por ítem cuántos módulos soporta ese ítem (floor(pedida / unid_mod)), pero la cantidad real de módulos a pedir es el mínimo de todos los ítems (el ítem más restrictivo). Eso aparece como "→ N módulos sugeridos" en el header pero es difícil de ver y no hay un control a nivel módulo.

El rediseño: agrego una fila footer por módulo con el cálculo consolidado y un input de "módulos a pedir" que propagua a todos los ítems:

Edit c:\AppFarmacia\templates\order_detail.html
Added 13 lines
Ahora actualizo procesarModulos() y agrego recalcModuloAll():

Edit c:\AppFarmacia\templates\order_detail.html
Added 14 lines
Ahora agrego recalcModuloAll y limpio las funciones que ya no aplican:

Edit c:\AppFarmacia\templates\order_detail.html
Added 17 lines
Ahora también hay que actualizar updateModuloSugerido y el código de confirmarModulos que lee it.propuesta:

Edit c:\AppFarmacia\templates\order_detail.html
Added 4 lines
Ahora actualizo confirmarModulos — que lee it.propuesta, pero ahora la propuesta viene del modprop a nivel módulo:

Read c:\AppFarmacia\templates\order_detail.html (lines 593-622)
Read c:\AppFarmacia\templates\order_detail.html (lines 637-686)
El confirmarModulos usa it.propuesta que ahora se asigna desde recalcModuloAll, así que está bien. Pero el reeditarModulos intenta reactivar prop-${mi}-${ii} que ya no existe. Lo corrijo:

Edit c:\AppFarmacia\templates\order_detail.html
Removed 3 lines
También el exportModulos() necesita leer la propuesta correcta (ahora a nivel módulo):

Read c:\AppFarmacia\templates\order_detail.html (lines 896-925)
Bash Find exportModulos and propuesta references
IN
grep -n "exportModulos\|cant_modulo\|propuesta\|modulos_propuesta\|modulos_sugeridos" c:/AppFarmacia/templates/order_detail.html | head -20

OUT
185:                    <button type="button" id="btn-export-mod-xlsx" onclick="exportModulos()"
537:        mod.modulos_sugeridos = sug;
555:        // Aplicar propuesta módulo a todos los ítems
563:    document.getElementById('modulos-status').textContent = 'Procesado — revisá propuestas y confirmá';
570:    modulesData[mi].modulos_propuesta = prop;
574:        it.propuesta = prop;
576:        // Mostrar unidades cubiertas en la celda propuesta del item
593:function updateSaldoCell(mi, ii, cantPedida, propuesta, cantMod) {
594:    const saldo = cantPedida - propuesta * cantMod;
610:    mod.modulos_sugeridos = sug;
652:                saldosPostMod[eanTarget] -= (it.propuesta || 0) * unidadesPorSlot;
806:            const cantUnidades = (it.propuesta || 0) * (it._unidadesPorSlot ?? it.cant);
811:                cant_modulo: cantUnidades,
826:            cant_modulo: null,
840:            cant_modulo: null,
889:        const cantMod  = row.cant_modulo != null ? `<span class="font-bold text-amber-700">${row.cant_modulo}</span>` : '<span class="text-[#ccc]">—</span>';
930:function exportModulos() {
933:        modulos_sugeridos: mod.modulos_sugeridos ?? 0,
941:            propuesta: it.propuesta ?? 0,
[rerun: b1]
El it.propuesta en línea 806 y 941 ya se asigna correctamente desde recalcModuloAll, así que el export y el resumen están bien.

Ahora el tema del número en la columna de Rotación — la columna Rot. muestra "A/M/B" + el promedio mensual (avg). El usuario quiere que el header aclare qué es ese número. La columna ya tiene title="Rotación A/M/B · promedio mensual" pero el tooltip puede no ser visible. Actualizo el header:

Edit c:\AppFarmacia\templates\order_detail.html
Modified
Verifico que la página carga bien:

Bash Verify new elements in rendered page
IN
curl -s http://localhost:5000/order/3 2>&1 | grep -o "modprop\|Can.Un.Mod\|Prom.mes\|mod-footer\|Módulos a pedir" | sort | uniq

OUT
Can.Un.Mod
Módulos a pedir
Prom.mes
mod-footer
modprop
[rerun: b2]
Todo presente. Resumen de los cambios:

Cantidad por módulo (fix principal):

Por ítem: Cant.Calc. sigue mostrando floor(pedida / unid_mod) (cuántos módulos soporta ese ítem solo)
Nueva fila footer por módulo: muestra "Módulos que soporta cada ítem: N (limitado por: PROD X)" + input "Módulos a pedir"
El input propaga automáticamente a todos los ítems del módulo (recalcModuloAll)
La celda Propuesta por ítem ahora muestra las unidades cubiertas (módulos × Can.Un.Mod.) en lugar de un input duplicado
Columna Rot.:

Renombrada a "Rot. / Prom.mes" — deja claro que el número al lado de A/M/B es el promedio mensual de ventas
en la columna tol.% muestra un numero que hay que dividir por 100 (supongo)
Bash Find tolHtml function
IN
grep -n "tolHtml\|tol_config\|tolPct\|saldo.*cant\|Tol" c:/AppFarmacia/templates/order_detail.html | head -20

OUT
320:const TOL_CONFIG    = {{ tol_config | tojson }};  // {A: %, M: %, B: %}
375:function tolHtml(saldo, cantMod, rot) {
376:    if (!saldo || saldo <= 0 || !cantMod) return '<span class="text-[#ccc]">—</span>';
377:    const pct = Math.round(saldo / cantMod * 100);
441:        <th class="text-right px-2 py-2 text-[#888]" title="% del módulo cubierto por saldo">Tol.%</th>
575:        it.saldo = (it.cant_pedida ?? 0) - prop * _ups;
594:    const saldo = cantPedida - propuesta * cantMod;
602:    if (tolEl) tolEl.innerHTML = tolHtml(saldo, cantMod, rot);
844:            saldo:       it.cantidad,
[rerun: b1]
Read c:\AppFarmacia\templates\order_detail.html (lines 376-390)
Read c:\AppFarmacia\templates\order_detail.html (lines 376-385)
al modificar el valor cantidad de modulos a pedir creo que deberiamos poner además del saldo que va cambiando la cantidad propuesta, 
Al abrir la tabla de productos hay una demora porque son muchos productos, mostrar cartel de "Cargando Productos" hasta que bajen