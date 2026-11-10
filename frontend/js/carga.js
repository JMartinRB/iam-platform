/* Carga de datasets.
 *
 * Dos caminos. El de generar un conjunto sintetico existe para poder
 * recorrer el sistema sin tener que conseguir un export, y la pantalla
 * deja claro que los datos son inventados. El de subir archivos es el
 * camino real: recibe los ocho CSV del esquema documentado.
 */

const Carga = (() => {

  let ultimo = null;

  async function mostrar() {
    let snapshots = [];
    try {
      snapshots = await API.snapshots();
    } catch (e) { /* se muestra igual, con la lista vacia */ }

    Vista.pintar(`
      <div class="encabezado">
        <div>
          <h2>Carga de datasets</h2>
          <p>Importación de un export de accesos y versionado como snapshot</p>
        </div>
      </div>

      <div class="dos-columnas">
        <section class="panel">
          <header>
            <h3>Generar un conjunto de prueba</h3>
            <span class="sello aviso">Datos sintéticos</span>
          </header>
          <div class="cuerpo">
            <p style="margin-top:0">
              Genera un conjunto sintético con hallazgos plantados a propósito y lo
              importa. Sirve para recorrer el sistema sin depender de un export, y
              para medir: como se sabe de antemano qué hallazgos tiene, se puede
              contrastar lo que el motor encuentra contra lo que debería encontrar.
            </p>
            <label class="campo" for="perfil">Tamaño del conjunto</label>
            <select id="perfil">
              <option value="D-1">D-1 · 500 usuarios, 5.000 asignaciones</option>
              <option value="D-2">D-2 · 2.000 usuarios, 20.000 asignaciones</option>
              <option value="D-3">D-3 · 5.000 usuarios, 50.000 asignaciones</option>
            </select>
            <div style="margin-top:14px">
              <button class="boton" id="generar">Generar e importar</button>
            </div>
            <div class="nota">
              No se usa información real de ninguna organización, por confidencialidad
              y por la Ley 25.326 de Protección de Datos Personales.
            </div>
          </div>
        </section>

        <section class="panel">
          <header><h3>Importar un export</h3></header>
          <div class="cuerpo">
            <p style="margin-top:0">
              Se esperan los ocho archivos del esquema: aplicaciones, permisos, roles,
              roles_permisos, usuarios, asignaciones, reglas_sod y politicas_abac.
              Las filas con defectos se rechazan de a una, informando línea y motivo,
              en lugar de abortar la carga entera.
            </p>
            <input type="file" id="archivos" multiple accept=".csv">
            <div style="margin-top:14px">
              <button class="boton suave" id="importar">Importar archivos</button>
            </div>
          </div>
        </section>
      </div>

      <div id="resultado">${ultimo ? resultado(ultimo) : ''}</div>
      ${listado(snapshots)}
    `, conectar);
  }

  function resultado(r) {
    const rechazos = r.rechazos.length ? `
      <table>
        <thead><tr><th>Archivo</th><th class="num">Línea</th><th>Motivo</th><th>Valor</th></tr></thead>
        <tbody>${r.rechazos.slice(0, 25).map((x) => `
          <tr>
            <td class="mono">${Vista.esc(x.archivo)}</td>
            <td class="num">${x.linea}</td>
            <td>${Vista.esc(x.motivo)}</td>
            <td class="mono">${Vista.esc(x.valor || '—')}</td>
          </tr>`).join('')}
        </tbody>
      </table>` : '<div class="recuento">No se rechazó ninguna fila.</div>';

    return `
      <section class="panel">
        <header>
          <h3>Resultado de la carga</h3>
          <span class="sello ok">Snapshot ${r.snapshot_id} · ${Vista.esc(r.etiqueta)}</span>
        </header>
        <div class="cuerpo">
          <dl class="lista-datos">
            <div><dt>Filas leídas</dt><dd>${Vista.numero(r.leidas)}</dd></div>
            <div><dt>Filas aceptadas</dt><dd>${Vista.numero(r.aceptadas)}</dd></div>
            <div><dt>Filas rechazadas</dt><dd>${Vista.numero(r.rechazadas)}</dd></div>
            <div><dt>Tiempo de carga</dt><dd>${r.segundos} s</dd></div>
          </dl>
        </div>
        ${rechazos}
        <div class="recuento">
          <button class="boton" id="ver-panel">Analizar este snapshot</button>
        </div>
      </section>`;
  }

  function listado(snapshots) {
    if (!snapshots.length) return '';
    return `
      <section class="panel">
        <header><h3>Snapshots cargados</h3></header>
        <table>
          <thead>
            <tr>
              <th class="num" style="width:70px">ID</th>
              <th>Etiqueta</th>
              <th class="num">Filas</th>
              <th class="num">Descartadas</th>
              <th>Cargado</th>
              <th style="width:150px">Estado</th>
            </tr>
          </thead>
          <tbody>${snapshots.map((s) => `
            <tr class="clicable" data-snapshot="${s.id}">
              <td class="num">${s.id}</td>
              <td class="titulo-celda">${Vista.esc(s.etiqueta)}</td>
              <td class="num">${Vista.numero(s.filas_leidas)}</td>
              <td class="num">${Vista.numero(s.filas_descartadas)}</td>
              <td>${Vista.fechaHora(s.creado_en)}</td>
              <td>${s.analizado
                    ? '<span class="sello ok">Analizado</span>'
                    : '<span class="sello">Sin analizar</span>'}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </section>`;
  }

  function conectar(raiz) {
    raiz.querySelector('#generar')?.addEventListener('click', async (ev) => {
      const boton = ev.currentTarget;
      const perfil = raiz.querySelector('#perfil').value;
      boton.disabled = true;
      boton.textContent = 'Generando…';
      try {
        ultimo = await API.generar(perfil);
        await App.refrescarSnapshots(ultimo.snapshot_id);
        await mostrar();
      } catch (e) {
        raiz.querySelector('#resultado').innerHTML = Vista.error(e.message);
        boton.disabled = false;
        boton.textContent = 'Generar e importar';
      }
    });

    raiz.querySelector('#importar')?.addEventListener('click', async (ev) => {
      const entrada = raiz.querySelector('#archivos');
      if (!entrada.files.length) {
        raiz.querySelector('#resultado').innerHTML =
          Vista.error('Elegí los archivos CSV antes de importar.');
        return;
      }
      const boton = ev.currentTarget;
      boton.disabled = true;
      boton.textContent = 'Importando…';
      const datos = new FormData();
      for (const archivo of entrada.files) datos.append('archivos', archivo);
      try {
        ultimo = await API.importar(datos);
        await App.refrescarSnapshots(ultimo.snapshot_id);
        await mostrar();
      } catch (e) {
        raiz.querySelector('#resultado').innerHTML = Vista.error(e.message);
        boton.disabled = false;
        boton.textContent = 'Importar archivos';
      }
    });

    raiz.querySelector('#ver-panel')?.addEventListener('click', () => App.ir('panel'));

    raiz.querySelectorAll('[data-snapshot]').forEach((f) =>
      f.addEventListener('click', async () => {
        await App.elegirSnapshot(Number(f.dataset.snapshot));
        App.ir('panel');
      }));
  }

  return { mostrar };
})();
