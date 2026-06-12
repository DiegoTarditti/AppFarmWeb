/* Componente reutilizable: buscador de cliente + dirección con autocomplete,
   manejo de domicilios guardados, geocodificación y modales nuevo/editar.

   API pública (window.ClientePicker):
     init({onAddressChange, onClienteSelected, onClear})
       - Hookea callbacks opcionales. onAddressChange dispara al cambiar
         dirección/coords/ciudad (útil para que el host re-cotice envío).
         onClienteSelected dispara al pickCli. onClear dispara al limpiar.
     getValues()           → {cid, oid, clienteNombre, direccion, ciudad,
                              piso, depto, referencia, domicilioId,
                              domLat, domLng}
     clear()               → resetea inputs y state interno
     populateCiudades(arr) → llena el <select> de ciudades (si ya están en
                              el doc, pasarlas acá)

   IDs esperados en el DOM (ver _cliente_picker.html):
     pCliente, pDir, pPiso, pDepto, pRef, pCiudad, pDom, pDomWrap,
     pDomSingle, pDomCount, pDomCoords, resCli, resGeo, btnEditarCli,
     modalNuevo, modalEditar, ncNombre, ncApellido, ncDni, ncTel,
     edNombre, edApellido, edDni, edTel, edDom, edCiudad, edObsRef

   Estado global expuesto en window (legado, para integración con el host):
     window._cid, window._oid, window._doms, window._domLat, window._domLng,
     window._domGeoAt, window._editCid

   Endpoints consumidos (movidos a /api/clientes/* el 2026-06-10; los paths
   viejos /reparto/api/* siguen vivos como redirects 308 en routes/reparto.py):
     GET  /api/clientes/buscar?q=
     GET  /api/clientes/ficha?cliente_id= | observer_id=
     GET  /api/clientes/geocodificar?q=&loc=
     GET  /api/clientes/observer/<oid>/domicilios
     POST /api/clientes
     POST /api/clientes/<id>
     POST /api/clientes/domicilios/<id>/geo
     POST /api/clientes/separar-direccion
*/
(function(){
  'use strict';

  const callbacks = {
    onAddressChange: null,
    onClienteSelected: null,
    onClear: null,
  };

  const $ = id => document.getElementById(id);

  function esc(s){
    return (s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  }

  async function jpost(u, b){
    return (await fetch(u, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(b||{})
    })).json();
  }

  function fmtFechaGeo(iso){
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('es-AR', {day:'2-digit', month:'2-digit', year:'2-digit'});
  }

  function semaforoGeo(iso){
    if (!iso) return {bg:'transparent', label:'(sin fecha)'};
    const ahora = new Date();
    const ts = new Date(iso);
    const meses = (ahora - ts) / (1000*60*60*24*30.4);
    if (meses < 3)  return {bg:'rgba(29,158,117,0.22)', label:'reciente'};
    if (meses < 12) return {bg:'rgba(239,159,39,0.22)', label:'>3 meses'};
    return                  {bg:'rgba(220,90,40,0.28)', label:'>1 año'};
  }

  function renderDomCoords(){
    const el = $('pDomCoords');
    if (!el) return;
    if (window._domLat != null && window._domLng != null){
      const lat = Number(window._domLat).toFixed(6);
      const lng = Number(window._domLng).toFixed(6);
      const sem = semaforoGeo(window._domGeoAt);
      el.innerHTML = `
        <div style="display:flex; align-items:center; gap:14px; flex-wrap:nowrap; white-space:nowrap;">
          <span style="font-size:18px; font-weight:700; color:var(--accent); font-family:monospace;">📍 ${lat}, ${lng}</span>
          <span style="font-size:18px; font-weight:600; color:var(--title);">${sem.label}${window._domGeoAt ? ' · '+fmtFechaGeo(window._domGeoAt) : ''}</span>
          <a href="https://www.google.com/maps?q=${lat},${lng}" target="_blank" rel="noopener"
             style="font-size:14px; font-weight:700; color:#fff; background:#185FA5;
                    padding:6px 14px; border-radius:8px; text-decoration:none;"
             title="Abrir en Google Maps">🗺️ Ver en mapa</a>
        </div>`;
      el.style.background = sem.bg;
      el.style.padding = '8px 10px';
      el.style.borderRadius = '8px';
      el.style.marginTop = '4px';
    } else {
      el.innerHTML = `<span class="muted2" style="font-size:11px;">sin geolocalización</span>`;
      el.style.background = 'transparent';
      el.style.padding = '2px 0';
      el.style.marginTop = '0';
    }
    refreshDomWrapVisibility();
  }

  function refreshDomWrapVisibility(){
    const wrap = $('pDomWrap');
    if (!wrap) return;  // host sin bloque domicilio (ej. /reparto)
    const n = (window._doms||[]).length;
    const hayCoords = window._domLat != null && window._domLng != null;
    wrap.style.display = (n>0 || hayCoords) ? '' : 'none';
  }

  function actualizarDomDropdown(doms){
    const sel = $('pDom');
    const single = $('pDomSingle');
    const count = $('pDomCount');
    if (!sel) return;  // host sin bloque domicilio
    const n = (doms || []).length;
    if (n >= 2){
      sel.style.display = '';
      if (single) single.style.display = 'none';
      if (count) count.textContent = `(${n} guardados)`;
    } else if (n === 1){
      sel.style.display = 'none';
      const d = doms[0];
      if (single){
        const loc = d.localidad ? ` · ${esc(d.localidad)}` : '';
        single.innerHTML = `<b>${esc(d.etiqueta||'Casa')}</b> — ${esc(d.direccion||'')}${loc}`;
        single.style.display = '';
      }
      if (count) count.textContent = '';
    } else {
      sel.style.display = 'none';
      if (single) single.style.display = 'none';
      if (count) count.textContent = '';
    }
    refreshDomWrapVisibility();
  }

  let _cliTmr = null;
  function onClienteInput(){
    window._oid = null;
    window._cid = null;
    $('btnEditarCli').style.display = 'none';
    clearTimeout(_cliTmr);
    const q = $('pCliente').value.trim();
    const box = $('resCli');
    if (q.length < 2){ box.style.display='none'; return; }
    _cliTmr = setTimeout(buscarCli, 250);
  }

  async function buscarCli(){
    const q = $('pCliente').value.trim(); if(q.length<2) return;
    const d = await (await fetch('/api/clientes/buscar?q='+encodeURIComponent(q))).json();
    const box = $('resCli');
    const cs = d.clientes||[];
    box.style.display = cs.length?'block':'none';
    box.innerHTML = cs.map(c=>{
      const ref = c.cliente_id ? `loc:${c.cliente_id}` : `obs:${c.observer_id}`;
      const doc = c.documento ? ` <span class="muted2">${esc(String(c.documento))}</span>` : '';
      const tel = c.telefono ? ` <span class="muted2" style="font-size:10px;">📞${esc(c.telefono)}</span>` : '';
      const dirCiu = [c.direccion, c.ciudad].filter(Boolean).join(', ');
      const dom = dirCiu ? `<div class="muted2" style="font-size:10px; margin-top:1px;">🏠 ${esc(dirCiu)}</div>` : '';
      return `<div class="it" onclick='ClientePicker.pickCli(${JSON.stringify(ref)}, ${JSON.stringify(c.nombre)}, ${c.cliente_id||'null'}, ${c.observer_id||'null'})'>
        <b>${esc(c.nombre)}</b>${doc}${tel}
        ${dom}
      </div>`;
    }).join('');
  }

  async function pickCli(ref, nombre, cid, oid){
    $('pCliente').value = nombre;
    $('resCli').style.display='none';
    return loadCliente({cliente_id: cid, observer_id: oid});
  }

  // Carga ficha sin pasar por el autocomplete (entrada externa: deep-link
  // /pedido/nuevo?observer_id=X, transición desde /atencion, etc.).
  async function loadCliente({cliente_id, observer_id} = {}){
    window._oid = observer_id || null;
    window._cid = cliente_id || null;
    const params = cliente_id ? `cliente_id=${cliente_id}`
                              : `observer_id=${observer_id}`;
    const ficha = await (await fetch(`/api/clientes/ficha?${params}`)).json();
    if(ficha && !ficha.error){
      // Setear nombre solo si el input está vacío (pickCli lo setea explícito antes).
      if (!$('pCliente').value){
        const raw = ficha.raw || {};
        const visible = ficha.nombre || [raw.apellido, raw.nombre].filter(Boolean).join(', ');
        if (visible) $('pCliente').value = visible;
      }
      const cid = window._cid;
      const dir = ficha.direccion || ficha.domicilio || '';
      const loc = ficha.localidad || ficha.ciudad || '';
      if (dir) $('pDir').value = dir;
      if (loc){
        const selC = $('pCiudad');
        let found = false;
        for(let i=0;i<selC.options.length;i++){
          if(selC.options[i].value===loc){ selC.selectedIndex=i; found=true; break; }
        }
        if (!found){
          const opt = document.createElement('option');
          opt.value = loc; opt.text = loc; opt.selected = true;
          selC.appendChild(opt);
        }
      }
      const _dirEl = $('pDir');
      if (_dirEl.value && (_dirEl.value.match(/(dto|dpto|depto|dep|departamento|uf|piso|pb|planta baja|monoblock|torre|entre|°|º)/i) || ficha.direccion != null)){
        try {
          const r = await jpost('/api/clientes/separar-direccion', {texto: _dirEl.value});
          if (r && r.direccion){
            _dirEl.value = r.direccion;
            // pPiso/pDepto/pRef solo existen en hosts con bloque domicilio
            // estructurado (pedido_nuevo). En /reparto solo hay pDir.
            const _piso = $('pPiso');   if (_piso)  _piso.value  = r.piso || '';
            const _dpto = $('pDepto');  if (_dpto)  _dpto.value  = r.depto || '';
            const _ref  = $('pRef');    if (_ref)   _ref.value   = r.referencia || '';
          }
        } catch(e) { /* ok, dejamos como está */ }
      }
      const doms = ficha.domicilios||[];
      window._doms = doms;
      actualizarDomDropdown(doms);
      const algunoConGeo = doms.some(x => x.lat != null && x.lng != null);
      if (!algunoConGeo && dir && callbacks.onAddressChange){
        setTimeout(callbacks.onAddressChange, 100);
      }
      const sel = $('pDom');
      sel.innerHTML = '<option value="">— escribir dirección —</option>' +
        doms.map(x=>{
          const loc = x.localidad ? ` · ${esc(x.localidad)}` : '';
          const geoBadge = (x.lat!=null && x.lng!=null)
            ? ` 📍${x.geo_actualizado_en ? ' '+fmtFechaGeo(x.geo_actualizado_en) : ''}`
            : '';
          return `<option value="${x.id}" data-lat="${x.lat||''}" data-lng="${x.lng||''}" data-loc="${esc(x.localidad||'')}" data-dir="${esc(x.direccion||'')}" data-piso="${esc(x.piso||'')}" data-depto="${esc(x.depto||'')}" data-ref="${esc(x.referencia||'')}">${esc(x.etiqueta)} — ${esc(x.direccion||'(sin dirección)')}${loc}${geoBadge}</option>`;
        }).join('');
      $('btnEditarCli').style.display = cid ? 'inline-block' : 'none';
      if (ficha.domicilio && callbacks.onAddressChange) callbacks.onAddressChange();
      if (callbacks.onClienteSelected) callbacks.onClienteSelected(ficha);
    }
  }

  let _geoTmr = null;
  function onDirInput(){
    window._domLat = null;
    window._domLng = null;
    window._domGeoAt = null;
    renderDomCoords();
    clearTimeout(_geoTmr);
    const q = $('pDir').value.trim();
    if (q.length < 3){ $('resGeo').style.display='none'; return; }
    _geoTmr = setTimeout(buscarGeoSugerencias, 350);
  }

  async function buscarGeoSugerencias(){
    const q = $('pDir').value.trim();
    const loc = $('pCiudad').value;
    const box = $('resGeo');
    if (q.length < 3) return;
    try {
      const r = await (await fetch(`/api/clientes/geocodificar?q=${encodeURIComponent(q)}&loc=${encodeURIComponent(loc||'')}`)).json();
      const sug = r.sugerencias || [];
      if (!sug.length){ box.style.display='none'; return; }
      box.style.display='block';
      box.innerHTML = sug.map(s=>{
        return `<div class="it" onclick='ClientePicker.pickGeo(${JSON.stringify(s)})'>
          <b>${esc(s.direccion||s.nomenclatura)}</b>
          <span class="muted2" style="font-size:10px;"> · ${esc(s.localidad||'')}</span>
          <span class="muted2" style="float:right; font-size:10px;">📍 ${s.lat.toFixed(4)}, ${s.lng.toFixed(4)}</span>
        </div>`;
      }).join('');
    } catch(e){ box.style.display='none'; }
  }

  function pickGeo(s){
    $('pDir').value = s.direccion || s.nomenclatura;
    if (s.localidad){
      const selC = $('pCiudad');
      let found = false;
      for(let i=0;i<selC.options.length;i++){
        if(selC.options[i].value===s.localidad){ selC.selectedIndex=i; found=true; break; }
      }
      if (!found){
        const opt = document.createElement('option');
        opt.value = s.localidad; opt.text = s.localidad; opt.selected = true;
        selC.appendChild(opt);
      }
    }
    window._domLat = s.lat;
    window._domLng = s.lng;
    window._domGeoAt = new Date().toISOString();
    renderDomCoords();
    $('resGeo').style.display='none';
    const domSel = $('pDom');
    if (domSel.value){
      jpost(`/api/clientes/domicilios/${domSel.value}/geo`, {lat: s.lat, lng: s.lng}).then(r=>{
        if (r.ok){
          const opt = domSel.options[domSel.selectedIndex];
          opt.dataset.lat = s.lat;
          opt.dataset.lng = s.lng;
          if (!opt.text.includes('📍')) opt.text = opt.text + ' 📍';
        }
      });
    }
    if (callbacks.onAddressChange) callbacks.onAddressChange();
  }

  function onDomChange(){
    const sel = $('pDom');
    const opt = sel.options[sel.selectedIndex];
    if (!opt || !sel.value){
      window._domLat = null;
      window._domLng = null;
      window._domGeoAt = null;
      renderDomCoords();
      return;
    }
    const domObj = (window._doms||[]).find(x=>String(x.id)===sel.value);
    window._domGeoAt = (domObj && domObj.geo_actualizado_en) || null;
    const dir = opt.dataset.dir || '';
    const loc = opt.dataset.loc || '';
    if (dir) $('pDir').value = dir;
    if (loc){
      const selCiu = $('pCiudad');
      for(let i=0;i<selCiu.options.length;i++){
        if(selCiu.options[i].value===loc){ selCiu.selectedIndex=i; break; }
      }
    }
    if (opt.dataset.lat && opt.dataset.lng){
      window._domLat = parseFloat(opt.dataset.lat);
      window._domLng = parseFloat(opt.dataset.lng);
      renderDomCoords();
      if (callbacks.onAddressChange) callbacks.onAddressChange();
    } else {
      window._domLat = null;
      window._domLng = null;
      renderDomCoords();
      if (callbacks.onAddressChange) callbacks.onAddressChange();
    }
  }

  function abrirNuevoCliente(){
    $('modalNuevo').style.display='flex';
    setTimeout(()=>$('ncNombre').focus(), 80);
  }

  function cerrarModal(id){ $(id).style.display='none'; }

  async function guardarNuevoCliente(){
    const body = {
      nombre: $('ncNombre').value.trim(),
      apellido: $('ncApellido').value.trim(),
      dni: $('ncDni').value.trim(),
      telefono: $('ncTel').value.trim(),
    };
    // Hosts que tienen domicilio + ciudad en el modal (ej. /reparto) los suman.
    const _dom = $('ncDom');     if (_dom && _dom.value.trim())     body.domicilio = _dom.value.trim();
    const _ciu = $('ncCiudad');  if (_ciu && _ciu.value)            body.ciudad    = _ciu.value;
    if(!body.nombre && !body.apellido && !body.dni){
      alert('Completá al menos nombre, apellido o DNI.');
      return;
    }
    const d = await jpost('/api/clientes', body);
    if(!d.ok){ alert('⚠️ '+(d.error||'no se pudo')); return; }
    window._cid = d.cliente_id;
    $('pCliente').value = [body.apellido, body.nombre].filter(Boolean).join(', ') || body.dni;
    if (body.domicilio) $('pDir').value = body.domicilio;
    if (body.ciudad){
      const selC = $('pCiudad');
      for(let i=0;i<selC.options.length;i++){
        if(selC.options[i].value===body.ciudad){ selC.selectedIndex=i; break; }
      }
    }
    $('btnEditarCli').style.display = 'inline-block';
    $('pDom').innerHTML = '<option value="">— escribir dirección —</option>';
    cerrarModal('modalNuevo');
    setTimeout(()=>$('pDir').focus(), 80);
  }

  function abrirEditarCliente(cid, oid){
    window._editCid = cid;
    $('modalEditar').style.display='flex';
    const params = cid ? `cliente_id=${cid}` : `observer_id=${oid}`;
    fetch(`/api/clientes/ficha?${params}`).then(r=>r.json()).then(f=>{
      $('edObsRef').textContent = f.fuente==='observer' ? `Fuente ObServer (ref: ${f.nombre})` : 'Cliente local';
      const r = f.raw || {};
      $('edNombre').value = r.nombre || '';
      $('edApellido').value = r.apellido || '';
      $('edDni').value = r.dni || '';
      $('edTel').value = r.telefono || '';
      $('edDom').value = r.domicilio || '';
      $('edCiudad').value = r.ciudad || '';
    });
  }

  async function guardarEditarCliente(){
    const cid = window._editCid;
    if(!cid) return;
    const body = {
      nombre: $('edNombre').value.trim(),
      apellido: $('edApellido').value.trim(),
      dni: $('edDni').value.trim(),
      telefono: $('edTel').value.trim(),
      domicilio: $('edDom').value.trim(),
      ciudad: $('edCiudad').value.trim(),
    };
    const d = await jpost(`/api/clientes/${cid}`, body);
    if(!d.ok){ alert('⚠️ '+(d.error||'no se pudo')); return; }
    const nombre = [body.apellido, body.nombre].filter(Boolean).join(', ') || body.dni;
    if(nombre) $('pCliente').value = nombre;
    cerrarModal('modalEditar');
  }

  function getValues(){
    return {
      cid: window._cid || null,
      oid: window._oid || null,
      clienteNombre: $('pCliente').value.trim(),
      direccion: $('pDir').value.trim(),
      ciudad: $('pCiudad').value,
      piso: $('pPiso').value.trim(),
      depto: $('pDepto').value.trim(),
      referencia: $('pRef').value.trim(),
      domicilioId: $('pDom').value || null,
      domLat: window._domLat,
      domLng: window._domLng,
    };
  }

  function clear(){
    ['pCliente','pDir','pPiso','pDepto','pRef'].forEach(i=>{
      const el = $(i); if (el) el.value = '';
    });
    $('pDom').innerHTML = '<option value="">— escribir dirección —</option>';
    $('btnEditarCli').style.display = 'none';
    window._cid = null;
    window._oid = null;
    window._doms = [];
    window._domLat = null;
    window._domLng = null;
    window._domGeoAt = null;
    actualizarDomDropdown([]);
    renderDomCoords();
    if (callbacks.onClear) callbacks.onClear();
  }

  function init(opts){
    Object.assign(callbacks, opts || {});

    // Auto-close dropdowns al clickear fuera (idempotente: solo una vez).
    if (!window._cpDocListener){
      document.addEventListener('click', (e) => {
        const boxCli = $('resCli');
        const boxGeo = $('resGeo');
        if (boxCli && !e.target.closest('#pCliente') && !boxCli.contains(e.target)) boxCli.style.display='none';
        if (boxGeo && !e.target.closest('#pDir') && !boxGeo.contains(e.target)) boxGeo.style.display='none';
      });
      window._cpDocListener = true;
    }
  }

  window.ClientePicker = {
    init,
    getValues,
    clear,
    loadCliente,
    // métodos expuestos para handlers onclick/oninput del macro:
    onClienteInput, buscarCli, pickCli,
    onDirInput, buscarGeoSugerencias, pickGeo,
    onDomChange,
    abrirNuevoCliente, cerrarModal, guardarNuevoCliente,
    abrirEditarCliente, guardarEditarCliente,
    // helpers internos usables por el host:
    renderDomCoords,
    actualizarDomDropdown,
  };
})();
