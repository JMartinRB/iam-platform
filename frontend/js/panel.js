/* Panel de diagnostico — Figura 4.4 del documento.
 *
 * El filtro por severidad opera sobre los datos ya traidos y no vuelve a
 * pedirlos al servidor (RF-09: sin recargar la pagina). Cada fila abre el
 * detalle con los cinco elementos que exige RF-10.
 */

const Panel = (() => {

  let estado = { resumen: null, hallazgos: [], severidad: null };

  // Cuántas filas del detalle se muestran de entrada. El analista trabaja
  // la cola de arriba hacia abajo: volcar doscientas filas en la primera
  // pantalla entierra justamente las que hay que mirar primero.
  const FILAS_VISIBLES = 25;

  // Corta dos análisis en paralelo sobre el mismo snapshot, que dejarían
  // los hallazgos duplicados.
  let corriendo = false;

  async function mostrar() {
    if (corriendo) return;
    const snapshot = App.snapshotActual();
    if (!snapshot) {
      return Vista.pintar(cabecera() + Vista.vacio(
        'Todavía no hay ningún snapshot cargado.',
        '<button class="boton" id="ir-carga">Cargar un dataset</button>',
      ), (raiz) => {
        raiz.querySelector('#ir-carga')?.addEventListener('click', () => App.ir('carga'));
      });
    }

    Vista.pintar(cabecera(snapshot) + '<div class="cargando">Analizando…</div>');

    corriendo = true;
    try {
      estado.resumen = await API.resumen(snapshot.id);
      if (!estado.resumen.analizado) {
        estado.resumen = await API.analizar(snapshot.id);
      }
      estado.hallazgos = await API.hallazgos(snapshot.id, { limite: 1000 });
    } catch (e) {
      return Vista.pintar(cabecera(snapshot) + Vista.error(e.message));
    } finally {
      corriendo = false;
    }

    render();
  }

  function cabecera(snapshot) {
    const ficha = snapshot ? `
      <div class="ficha">
        <div><span>Organización:</span> <b>Conjunto sintético de validación</b></div>
        <div><span>Snapshot:</span> <b>${Vista.esc(snapshot.etiqueta)}</b> ·
             ${Vista.numero(snapshot.usuarios)} usuarios ·
             ${Vista.numero(snapshot.filas_leidas)} filas leídas</div>
        <div><span>Cargado:</span> <b>${Vista.fechaHora(snapshot.creado_en)}</b></div>
      </div>` : '';

    return `
      <div class="encabezado">
        <div>
          <h2>Panel de diagnóstico</h2>
          <p>Estado de los accesos al momento del último snapshot cargado</p>
        </div>
        ${ficha}
      </div>`;
  }

  function render() {
    const r = estado.resumen;
    const visibles = estado.severidad
      ? estado.hallazgos.filter((h) => h.severidad === estado.severidad)
      : estado.hallazgos;

    Vista.pintar(
      cabecera(r.snapshot) + indicadores(r) + categorias(r) + tabla(visibles) + nota(),
      conectar,
    );
  }

  function indicadores(r) {
    const nivel = { critico: 'critica', alto: 'alta', medio: 'media', bajo: 'baja' }[r.nivel_riesgo] || 'baja';
    const pct = (n, total) => total ? Math.min(100, Math.round(100 * n / total)) : 0;

    return `
      <div class="indicadores">
        <div class="indicador ${nivel}">
          <div class="rotulo">Índice de riesgo</div>
          <div class="cifra">${r.indice_riesgo}<small> / 100</small></div>
          <div class="barra"><i style="width:${r.indice_riesgo}%"></i></div>
          <div class="pie">Nivel ${Vista.esc(r.nivel_riesgo)}. Ponderado por la peor
              severidad de cada usuario.</div>
        </div>
        <div class="indicador">
          <div class="rotulo">Hallazgos totales</div>
          <div class="cifra">${Vista.numero(r.hallazgos)}</div>
          <div class="barra"><i style="width:100%"></i></div>
          <div class="pie">Sobre ${Vista.numero(r.usuarios_analizados)} usuarios analizados.</div>
        </div>
        <div class="indicador critica">
          <div class="rotulo">Hallazgos críticos</div>
          <div class="cifra">${Vista.numero(r.criticos)}</div>
          <div class="barra"><i style="width:${pct(r.criticos, r.hallazgos)}%"></i></div>
          <div class="pie">Requieren remediación inmediata.</div>
        </div>
        <div class="indicador alta">
          <div class="rotulo">Usuarios señalados</div>
          <div class="cifra">${Vista.numero(r.usuarios_senalados)}</div>
          <div class="barra"><i style="width:${pct(r.usuarios_senalados, r.usuarios_analizados)}%"></i></div>
          <div class="pie">${pct(r.usuarios_senalados, r.usuarios_analizados)}% de la población
              tiene al menos un hallazgo.</div>
        </div>
      </div>`;
  }

  /* La severidad de un hallazgo depende del peso del permiso, no de la
   * categoria. Mostrar solo el maximo pondria "critica" en las cinco filas
   * y la columna dejaria de distinguir nada: lo que informa es el reparto. */
  function repartoSeveridad(c) {
    const partes = ['critica', 'alta', 'media', 'baja']
      .filter((s) => c.por_severidad[s])
      .map((s) => `<span class="pizca ${s}" title="${Vista.NOMBRE_SEVERIDAD[s]}">
                     ${c.por_severidad[s]}</span>`);
    return partes.length > 1 ? `<div class="reparto">${partes.join('')}</div>` : '';
  }

  function categorias(r) {
    const filas = r.categorias.map((c) => `
      <tr class="clicable" data-categoria="${Vista.esc(c.categoria)}">
        <td>${Vista.sev(c.severidad_maxima)}
            ${repartoSeveridad(c)}</td>
        <td>
          <div class="titulo-celda">${Vista.esc(c.titulo)}</div>
          <div class="sub-celda">${Vista.esc(c.descripcion)}</div>
        </td>
        <td class="num"><b>${Vista.numero(c.usuarios)}</b></td>
        <td class="num">${Vista.numero(c.hallazgos)}</td>
      </tr>`).join('');

    const botones = ['critica', 'alta', 'media', 'baja'].map((s) => `
      <button class="filtro ${estado.severidad === s ? 'activo' : ''}" data-sev="${s}">
        ${Vista.NOMBRE_SEVERIDAD[s]}
      </button>`).join('');

    return `
      <section class="panel">
        <header>
          <h3>Hallazgos por categoría</h3>
          <div class="filtros">
            <button class="filtro ${estado.severidad ? '' : 'activo'}" data-sev="">Todas</button>
            ${botones}
          </div>
        </header>
        <table>
          <thead>
            <tr>
              <th style="width:130px">Severidad</th>
              <th>Categoría y descripción</th>
              <th class="num" style="width:100px">Usuarios</th>
              <th class="num" style="width:110px">Hallazgos</th>
            </tr>
          </thead>
          <tbody>${filas}</tbody>
        </table>
      </section>`;
  }

  function tabla(hallazgos) {
    if (!hallazgos.length) {
      return `<section class="panel">${Vista.vacio(
        'Ningún hallazgo coincide con el filtro aplicado.')}</section>`;
    }

    const filas = hallazgos.slice(0, FILAS_VISIBLES).map((h) => `
      <tr class="clicable" data-hallazgo="${h.id}">
        <td>${Vista.sev(h.severidad)}</td>
        <td>
          <div class="titulo-celda">${Vista.esc(h.legajo || '—')} ·
              ${Vista.esc(h.nombre || 'sin titular')}</div>
          <div class="sub-celda">${Vista.esc(h.puesto || '')}${h.area ? ' · ' + Vista.esc(h.area) : ''}</div>
        </td>
        <td>${Vista.esc(h.titulo)}</td>
        <td class="num"><b>${Vista.numero(Math.round(h.puntaje))}</b></td>
      </tr>`).join('');

    const pie = hallazgos.length > FILAS_VISIBLES
      ? `<div class="recuento">Se muestran los ${FILAS_VISIBLES} de mayor riesgo,
         de <b>${Vista.numero(hallazgos.length)}</b> que coinciden con el filtro.</div>`
      : `<div class="recuento"><b>${Vista.numero(hallazgos.length)}</b> hallazgos.</div>`;

    const exceptuados = estado.resumen.exceptuados
      ? `<span class="sello">${estado.resumen.exceptuados} exceptuado${
           estado.resumen.exceptuados === 1 ? '' : 's'} fuera del panel</span>`
      : '';

    return `
      <section class="panel">
        <header>
          <h3>Detalle, ordenado por riesgo</h3>
          <div style="display:flex;gap:8px;align-items:center">
            ${exceptuados}
            <span class="sello">Clic en una fila para ver la evidencia</span>
          </div>
        </header>
        <table>
          <thead>
            <tr>
              <th style="width:130px">Severidad</th>
              <th style="width:34%">Usuario</th>
              <th>Hallazgo</th>
              <th class="num" style="width:100px">Riesgo</th>
            </tr>
          </thead>
          <tbody>${filas}</tbody>
        </table>
        ${pie}
      </section>`;
  }

  function nota() {
    return `
      <div class="nota">
        La maqueta de la Figura 4.4 del documento enumera siete categorías. El motor
        implementa las cinco que se muestran acá. Las dos restantes —cuentas compartidas
        y cuentas de servicio sin responsable— surgieron de la entrevista al especialista
        y están declaradas como trabajo futuro en el capítulo de conclusiones.
      </div>`;
  }

  function conectar(raiz) {
    raiz.querySelectorAll('[data-sev]').forEach((b) =>
      b.addEventListener('click', () => {
        estado.severidad = b.dataset.sev || null;
        render();
      }));

    raiz.querySelectorAll('[data-categoria]').forEach((f) =>
      f.addEventListener('click', () => {
        const categoria = f.dataset.categoria;
        const del = estado.hallazgos.filter((h) => h.categoria === categoria);
        if (del.length) abrirDetalle(del[0].id);
      }));

    raiz.querySelectorAll('[data-hallazgo]').forEach((f) =>
      f.addEventListener('click', () => abrirDetalle(Number(f.dataset.hallazgo))));
  }

  async function abrirDetalle(hallazgoId) {
    const snapshot = App.snapshotActual();
    let d;
    try {
      d = await API.hallazgo(snapshot.id, hallazgoId);
    } catch (e) {
      return Vista.modal('Error', `<div class="detalle-campo">${Vista.esc(e.message)}</div>`);
    }

    const h = d.hallazgo;
    const contexto = Object.entries(d.contexto || {})
      .map(([k, v]) => `<div><dt>${Vista.esc(k.replaceAll('_', ' '))}</dt>
                         <dd>${Vista.esc(typeof v === 'object' ? JSON.stringify(v) : v)}</dd></div>`)
      .join('');

    const bloqueExcepcion = h.exceptuado
      ? `<div class="detalle-campo">
           <div class="rotulo">Excepción vigente</div>
           <p style="margin:0 0 8px">${Vista.esc(h.excepcion.motivo)}</p>
           <div class="sub-celda">
             Justificada por ${Vista.esc(h.excepcion.responsable)} el
             ${Vista.fecha(h.excepcion.creada_en)}, con alcance de
             ${h.excepcion.alcance === 'permiso' ? 'este permiso' : 'toda la categoría'}.
           </div>
           <button class="boton suave" id="revocar" style="margin-top:12px"
                   data-excepcion="${h.excepcion.id}">Revocar la excepción</button>
         </div>`
      : `<div class="detalle-campo">
           <div class="rotulo">Excepción justificada</div>
           <p style="margin:0 0 10px;color:var(--tinta-suave)">
             Un acceso que en los datos se ve riesgoso puede estar justificado por
             el negocio. Marcarlo como excepción lo saca del panel y del índice, y
             deja asentado quién lo justificó y por qué.
           </p>
           <label class="campo" for="motivo">Motivo</label>
           <input type="text" id="motivo" style="width:100%"
                  placeholder="Por qué este acceso está justificado">
           <div style="display:flex;gap:12px;align-items:end;margin-top:10px">
             <div>
               <label class="campo" for="responsable">Responsable</label>
               <input type="text" id="responsable" value="U00009" style="width:130px">
             </div>
             <div>
               <label class="campo" for="alcance">Alcance</label>
               <select id="alcance">
                 <option value="permiso">Sólo este permiso</option>
                 <option value="categoria">Toda la categoría para el usuario</option>
               </select>
             </div>
             <button class="boton" id="exceptuar" data-hallazgo="${h.id}">
               Marcar como excepción
             </button>
           </div>
           <div id="aviso-excepcion"></div>
         </div>`;

    Vista.modal(`${h.titulo} · ${h.legajo || ''}`, `
      <div class="detalle-campo">
        <div class="rotulo">Severidad y riesgo</div>
        ${Vista.sev(h.severidad)} &nbsp; índice ${Vista.numero(Math.round(h.puntaje))}
        ${h.exceptuado ? ' &nbsp; <span class="sello">Exceptuado</span>' : ''}
      </div>
      <div class="detalle-campo">
        <div class="rotulo">Regla aplicada</div>${Vista.esc(d.regla_aplicada)}
      </div>
      <div class="detalle-campo">
        <div class="rotulo">Umbral</div>${Vista.esc(d.umbral)}
      </div>
      <div class="detalle-campo">
        <div class="rotulo">Fuente</div><span class="mono">${Vista.esc(d.fuente)}</span>
      </div>
      <div class="detalle-campo">
        <div class="rotulo">Fecha del snapshot</div>${Vista.fechaHora(d.fecha_snapshot)}
      </div>
      <div class="detalle-campo">
        <div class="rotulo">Contexto del acceso</div>
        <dl class="lista-datos">${contexto || '<div><dd>Sin datos adicionales.</dd></div>'}</dl>
      </div>
      <div class="detalle-campo">
        <div class="rotulo">Recomendación</div>${Vista.esc(h.recomendacion || '—')}
      </div>
      ${bloqueExcepcion}`);

    conectarExcepcion(hallazgoId);
  }

  function conectarExcepcion(hallazgoId) {
    const raiz = document.getElementById('modal');
    const snapshot = App.snapshotActual();

    raiz.querySelector('#exceptuar')?.addEventListener('click', async (ev) => {
      const motivo = raiz.querySelector('#motivo').value.trim();
      if (motivo.length < 3) {
        raiz.querySelector('#aviso-excepcion').innerHTML =
          Vista.error('Escribí el motivo: una excepción sin justificación no sirve de nada.');
        return;
      }
      ev.currentTarget.disabled = true;
      try {
        await API.exceptuar(snapshot.id, hallazgoId, {
          motivo,
          responsable: raiz.querySelector('#responsable').value.trim() || 'U00009',
          solo_este_permiso: raiz.querySelector('#alcance').value === 'permiso',
        });
        raiz.innerHTML = '';
        await mostrar();
      } catch (e) {
        raiz.querySelector('#aviso-excepcion').innerHTML = Vista.error(e.message);
        ev.currentTarget.disabled = false;
      }
    });

    raiz.querySelector('#revocar')?.addEventListener('click', async (ev) => {
      ev.currentTarget.disabled = true;
      try {
        await API.revocar(snapshot.id, Number(ev.currentTarget.dataset.excepcion), 'U00009');
        raiz.innerHTML = '';
        await mostrar();
      } catch (e) {
        ev.currentTarget.disabled = false;
      }
    });
  }

  return { mostrar };
})();
