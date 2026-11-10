/* Altas de usuario — Figura 4.5 del documento.
 *
 * El orden en que la pantalla presenta las cosas es el orden en que
 * ocurren, y eso importa mas que la estetica: primero los accesos que
 * resolvio el motor de reglas, despues el script que los traduce, y al
 * final la aprobacion. Si el script apareciera arriba, la pantalla
 * sugeriria que el modelo decide, que es exactamente lo contrario de lo
 * que hace el sistema.
 */

const Altas = (() => {

  let detalle = null;
  let aviso = '';

  // -------------------------------------------------------------- listado

  async function mostrar() {
    aviso = '';
    detalle = null;
    let solicitudes = [];
    try {
      solicitudes = await API.altas();
    } catch (e) {
      return Vista.pintar(cabeceraLista() + Vista.error(e.message));
    }

    Vista.pintar(cabeceraLista() + formulario() + listado(solicitudes), conectarLista);
  }

  function cabeceraLista() {
    return `
      <div class="encabezado">
        <div>
          <h2>Altas pendientes</h2>
          <p>Resolución de accesos y generación del script de aplicación</p>
        </div>
      </div>`;
  }

  function formulario() {
    return `
      <section class="panel">
        <header>
          <h3>Nueva solicitud de alta</h3>
          <span class="sello">Motor de reglas RBAC + ABAC</span>
        </header>
        <div class="cuerpo">
          <div style="display:grid;grid-template-columns:1fr 1fr auto;gap:14px;align-items:end">
            <div>
              <label class="campo" for="legajo">Usuario</label>
              <input type="search" id="legajo" list="lista-usuarios"
                     placeholder="Legajo, nombre o puesto" autocomplete="off">
              <datalist id="lista-usuarios"></datalist>
            </div>
            <div>
              <label class="campo" for="movimiento">Tipo de movimiento</label>
              <select id="movimiento">
                <option value="alta">Alta nueva · resuelve desde cero</option>
                <option value="cambio">Cambio de puesto · conserva lo vigente</option>
              </select>
            </div>
            <div>
              <button class="boton" id="crear">Resolver accesos</button>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px">
            <div>
              <label class="campo" for="solicitante">Solicitada por</label>
              <input type="text" id="solicitante" value="U00002" style="width:100%">
            </div>
          </div>

          <div style="margin-top:14px">
            <label class="campo" for="extras">
              Permisos pedidos a mano, separados por coma (opcional)
            </label>
            <input type="text" id="extras" style="width:100%"
                   placeholder="ERP_APROB_PAGOS, ERP_CARGA_PAGOS">
          </div>

          <div class="nota">
            La distinción entre los dos movimientos es la que hace aparecer los
            conflictos que ningún formulario ve. En un alta nueva la persona no tiene
            nada y la validación sólo mira la propuesta. En un cambio de puesto sigue
            teniendo lo anterior, y ahí el permiso nuevo es inofensivo, el viejo
            también, y juntos no.
            <br><br>
            Los permisos pedidos a mano entran a la propuesta como cualquier otro y
            pasan por la misma validación cruzada de Segregación de Funciones:
            pedirlos explícitamente no saltea el control.
          </div>
          <div id="aviso">${aviso}</div>
        </div>
      </section>`;
  }

  function listado(solicitudes) {
    if (!solicitudes.length) {
      return `<section class="panel">${Vista.vacio(
        'Todavía no hay solicitudes. Creá una con el formulario de arriba.')}</section>`;
    }

    return `
      <section class="panel">
        <header><h3>Solicitudes</h3></header>
        <table>
          <thead>
            <tr>
              <th class="num" style="width:70px">ID</th>
              <th>Usuario</th>
              <th>Puesto solicitado</th>
              <th class="num" style="width:110px">Accesos</th>
              <th class="num" style="width:110px">Conflictos</th>
              <th style="width:130px">Estado</th>
              <th style="width:120px">Script</th>
            </tr>
          </thead>
          <tbody>${solicitudes.map((s) => `
            <tr class="clicable" data-solicitud="${s.id}">
              <td class="num">${s.id}</td>
              <td>
                <div class="titulo-celda">${Vista.esc(s.nombre)}</div>
                <div class="sub-celda">${Vista.esc(s.legajo)}</div>
              </td>
              <td>${Vista.esc(s.puesto_solicitado)}
                  <div class="sub-celda">${Vista.esc(s.area_solicitada)}</div></td>
              <td class="num"><b>${s.otorgados}</b></td>
              <td class="num">${s.conflictos
                    ? `<span class="sev critica">${s.conflictos}</span>`
                    : '<span style="color:var(--tinta-tenue)">—</span>'}</td>
              <td>${estadoSello(s.estado)}</td>
              <td>${s.tiene_script
                    ? '<span class="sello ok">Generado</span>'
                    : '<span class="sello">Pendiente</span>'}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </section>`;
  }

  const estadoSello = (estado) => ({
    pendiente: '<span class="sello aviso">Pendiente</span>',
    aprobada: '<span class="sello ok">Aprobada</span>',
    rechazada: '<span class="sev critica">Rechazada</span>',
  }[estado] || `<span class="sello">${Vista.esc(estado)}</span>`);

  async function conectarLista(raiz) {
    const entrada = raiz.querySelector('#legajo');
    const lista = raiz.querySelector('#lista-usuarios');

    const sugerir = async () => {
      try {
        const usuarios = await API.usuarios(entrada.value);
        lista.innerHTML = usuarios.map((u) =>
          `<option value="${Vista.esc(u.legajo)}">${Vista.esc(u.nombre)} · ${Vista.esc(u.puesto)}</option>`
        ).join('');
      } catch (e) { /* sin sugerencias; se puede escribir el legajo a mano */ }
    };
    entrada?.addEventListener('input', sugerir);
    sugerir();

    raiz.querySelector('#crear')?.addEventListener('click', async (ev) => {
      const legajo = entrada.value.trim();
      if (!legajo) {
        raiz.querySelector('#aviso').innerHTML = Vista.error('Indicá el legajo del usuario.');
        return;
      }
      const boton = ev.currentTarget;
      boton.disabled = true;
      boton.textContent = 'Resolviendo…';
      try {
        const extras = raiz.querySelector('#extras').value
          .split(',').map((s) => s.trim()).filter(Boolean);
        detalle = await API.crearAlta({
          legajo,
          solicitada_por: raiz.querySelector('#solicitante').value.trim() || 'U00002',
          permisos_solicitados: extras,
          conservar_vigentes: raiz.querySelector('#movimiento').value === 'cambio',
        });
        renderDetalle();
      } catch (e) {
        raiz.querySelector('#aviso').innerHTML = Vista.error(e.message);
        boton.disabled = false;
        boton.textContent = 'Resolver accesos';
      }
    });

    raiz.querySelectorAll('[data-solicitud]').forEach((f) =>
      f.addEventListener('click', () => abrir(Number(f.dataset.solicitud))));
  }

  // -------------------------------------------------------------- detalle

  async function abrir(solicitudId) {
    aviso = '';
    try {
      detalle = await API.alta(solicitudId);
    } catch (e) {
      return Vista.pintar(cabeceraLista() + Vista.error(e.message));
    }
    renderDetalle();
  }

  function renderDetalle() {
    const d = detalle;
    Vista.pintar(`
      <div class="encabezado">
        <div>
          <h2>Alta de usuario · Solicitud ALT-${String(d.id).padStart(4, '0')}</h2>
          <p>Revisión de los accesos resueltos y del script de aplicación antes de su ejecución</p>
        </div>
        <div class="ficha">
          <div><span>Estado:</span> ${estadoSello(d.estado)}</div>
          <div><span>Solicitada por:</span> <b>${Vista.esc(d.solicitada_por)}</b> ·
               ${Vista.fechaHora(d.creada_en)}</div>
          <div><span>Plataforma destino:</span>
               <b>${d.script ? Vista.esc(d.script.plataforma) : 'sin script todavía'}</b></div>
        </div>
      </div>

      <div id="aviso">${aviso}</div>

      <div class="dos-columnas">
        <div>${datosUsuario(d)}${accesos(d)}</div>
        <div>${panelScript(d)}</div>
      </div>

      ${cierre(d)}

      <div style="margin-top:18px">
        <button class="boton suave" id="volver">← Volver al listado</button>
      </div>
    `, conectarDetalle);
  }

  function datosUsuario(d) {
    const u = d.usuario;
    return `
      <section class="panel">
        <header>
          <h3>Datos del usuario</h3>
          <span class="sello">Origen: padrón de identidades</span>
        </header>
        <table class="datos-usuario">
          <tbody>
            <tr><td>Nombre</td><td>${Vista.esc(u.nombre)}</td></tr>
            <tr><td>Legajo</td><td class="mono">${Vista.esc(u.legajo)}</td></tr>
            <tr><td>Puesto</td><td>${Vista.esc(d.puesto_solicitado)}</td></tr>
            <tr><td>Área</td><td>${Vista.esc(d.area_solicitada)}</td></tr>
            <tr><td>Fecha de ingreso</td><td>${Vista.fecha(u.fecha_ingreso)}</td></tr>
            <tr>
              <td>${d.perfil_inferido ? 'Rol inferido' : 'Rol asignado'}</td>
              <td>${d.roles.length
                 ? Vista.esc(d.roles.join(', '))
                 : '<span style="color:var(--tinta-tenue)">sin rol de catálogo</span>'}</td>
            </tr>
          </tbody>
        </table>
        ${d.perfil_inferido ? `
          <div class="nota" style="margin:0 20px 16px">
            El catálogo no declara qué roles le corresponden a este puesto, así que el
            motor los infirió a partir de lo que tienen efectivamente sus pares. Es la
            situación que el entrevistado describió como la más común: el catálogo
            existe, pero no coincide con lo que la gente realmente tiene.
          </div>` : ''}
      </section>`;
  }

  function accesos(d) {
    /* La tabla lista los accesos otorgados y tambien los retenidos. Si los
     * retenidos no aparecieran, la pantalla mostraria una lista limpia y el
     * conflicto quedaria contado solo en un aviso al pie: el permiso que el
     * sistema bloqueo tiene que verse, porque es la decision mas importante
     * que tomo el motor. */
    const otorgadas = d.otorgados.map((p) => `
      <tr>
        <td>
          <div class="titulo-celda mono">${Vista.esc(p.codigo)}</div>
          <div class="sub-celda">${Vista.esc(p.descripcion)}</div>
        </td>
        <td>${Vista.esc(Vista.NOMBRE_ORIGEN[p.origen] || p.origen)}
            ${p.rol ? `<div class="sub-celda">${Vista.esc(p.rol)}</div>` : ''}</td>
        <td>${p.conflicto
              ? `<span class="sev media">Otorgado</span>
                 <div class="sub-celda">la contraparte quedó retenida</div>`
              : '<span class="sev sin-conflicto">Sin conflicto</span>'}</td>
      </tr>`).join('');

    const retenidas = d.excluidos.map((e) => `
      <tr class="fila-conflicto">
        <td>
          <div class="titulo-celda mono">${Vista.esc(e.codigo)}</div>
          <div class="sub-celda">${Vista.esc(e.detalle)}</div>
        </td>
        <td>${Vista.esc(e.motivo === 'conflicto_sod'
              ? 'Pedido a mano' : e.motivo)}</td>
        <td><span class="sev conflicto">Retenido</span>
            <div class="sub-celda">fuera del script</div></td>
      </tr>`).join('');

    const filas = otorgadas + retenidas;

    const bloqueados = d.excluidos.filter((e) => e.motivo === 'conflicto_sod');
    const avisos = bloqueados.map((e) => `
      <div class="aviso-conflicto">
        <h4>Conflicto de Segregación de Funciones detectado</h4>
        <p>
          Regla <b>${Vista.esc(e.regla || '—')}</b>. El permiso
          <b class="mono">${Vista.esc(e.codigo)}</b> es incompatible con
          <b class="mono">${Vista.esc(e.permiso_en_conflicto || '—')}</b>.
          El permiso quedó excluido del script generado hasta que se resuelva la excepción.
        </p>
      </div>`).join('');

    const otros = d.excluidos.filter((e) => e.motivo !== 'conflicto_sod');
    const avisoOtros = otros.length ? `
      <div class="recuento">
        También quedaron afuera ${otros.map((e) =>
          `<b class="mono">${Vista.esc(e.codigo)}</b> (${Vista.esc(e.motivo)})`).join(', ')}.
      </div>` : '';

    const total = d.otorgados.length + d.excluidos.length;
    return `
      <section class="panel">
        <header>
          <h3>Accesos resueltos</h3>
          <span class="sello">Motor de reglas RBAC + ABAC</span>
        </header>
        <table>
          <thead>
            <tr><th>Permiso</th><th style="width:28%">Origen</th>
                <th style="width:130px">Validación SoD</th></tr>
          </thead>
          <tbody>${filas || '<tr><td colspan="3">Sin accesos resueltos.</td></tr>'}</tbody>
        </table>
        ${avisos}${avisoOtros}
        <div class="recuento">
          <b>${d.otorgados.length}</b> de ${total} accesos habilitados para su aplicación.
          ${d.excluidos.length
            ? (d.excluidos.length === 1
                ? '<b>1</b> acceso retenido.'
                : `<b>${d.excluidos.length}</b> accesos retenidos.`)
            : ''}
        </div>
      </section>`;
  }

  function panelScript(d) {
    if (!d.script) {
      /* Que no haya nada que aplicar no es un error: es el resultado
       * correcto cuando la persona ya tiene todo lo que le corresponde.
       * Dejar el boton habilitado para que falle seria peor que explicarlo. */
      const sinCambios = d.cambios_pendientes === 0;
      const aviso = sinCambios
        ? `<div class="nota">
             No hay cambios que aplicar. Los ${d.conservados.length} accesos resueltos
             ya los tiene el usuario, así que la decisión del motor es que no
             corresponde tocar nada. Para ver la generación, elegí
             <b>Alta nueva</b> en el formulario, que resuelve desde cero.
           </div>`
        : d.estado !== 'pendiente'
          ? '<div class="nota">La solicitud ya fue resuelta, no se puede generar un script nuevo.</div>'
          : '';

      return `
        <section class="panel">
          <header>
            <h3>Script de aplicación</h3>
            <span class="sello">${sinCambios ? 'Sin cambios que aplicar' : 'No generado'}</span>
          </header>
          <div class="cuerpo">
            <p style="margin-top:0">
              El script se genera a partir de la decisión que ya tomó el motor de reglas.
              Lo que viaja al modelo es la lista cerrada de cambios con la identidad
              seudonimizada, nunca el legajo real ni la solicitud original.
            </p>
            <dl class="lista-datos" style="margin-bottom:14px">
              <div><dt>Cambios a aplicar</dt><dd>${d.cambios_pendientes}</dd></div>
              <div><dt>Accesos ya vigentes</dt><dd>${d.conservados.length}</dd></div>
            </dl>
            <label class="campo" for="plataforma">Plataforma destino</label>
            <select id="plataforma">
              <option value="powershell">Active Directory · PowerShell</option>
              <option value="okta">Okta · API REST</option>
            </select>
            <label class="campo" for="modelo-real" style="margin-top:14px">
              <input type="checkbox" id="modelo-real"> Usar el modelo configurado en el .env
            </label>
            <div style="margin-top:10px">
              <button class="boton" id="generar-script"
                      ${d.estado !== 'pendiente' || sinCambios ? 'disabled' : ''}>
                Generar script
              </button>
            </div>
            ${aviso}
          </div>
        </section>`;
    }

    const s = d.script;
    const controles = s.controles.map((c) => `
      <span class="control ${c.aprobado ? '' : 'falla'}" title="${Vista.esc(c.detalle)}">
        ${c.aprobado ? '✓' : '✗'} ${Vista.esc(nombreControl(c.nombre))}
      </span>`).join('');

    return `
      <section class="panel">
        <header>
          <h3>Script generado · ${Vista.esc(s.plataforma)}</h3>
          <span class="sello aviso">${s.proveedor_es_simulado
            ? 'Proveedor simulado · no ejecutado'
            : 'Generado por IA · no ejecutado'}</span>
        </header>
        <div class="cuerpo">
          <pre class="codigo">${Vista.codigo(s.contenido)}</pre>
          <div class="controles">${controles}</div>
          <dl class="lista-datos" style="margin-top:16px">
            <div><dt>Proveedor</dt><dd>${Vista.esc(s.proveedor)} · ${Vista.esc(s.modelo)}</dd></div>
            <div><dt>Intentos</dt><dd>${s.intentos}</dd></div>
            <div><dt>Estado de validación</dt><dd>${Vista.esc(s.estado_validacion)}</dd></div>
            <div><dt>Aprobado por</dt><dd>${Vista.esc(s.aprobado_por || '—')}</dd></div>
          </dl>
          ${s.proveedor_es_simulado ? `
            <div class="nota">
              Este script no lo escribió un modelo de lenguaje. Lo armó el proveedor
              simulado a partir del propio contexto, que es lo que permite recorrer el
              sistema sin credenciales ni red. Para usar el modelo real, marcá la casilla
              antes de generar.
            </div>` : ''}
        </div>
      </section>`;
  }

  const nombreControl = (nombre) => ({
    esquema: 'Esquema de salida',
    sintaxis: 'Sintaxis',
    lista_blanca: 'Lista blanca de operaciones',
    lista_negra: 'Sin operaciones destructivas',
    correspondencia: 'Identificadores coincidentes',
  }[nombre] || nombre);

  function cierre(d) {
    const resuelta = d.estado !== 'pendiente';
    return `
      <section class="panel">
        <div class="cierre">
          <p>
            <b>Revisión humana obligatoria.</b> El motor de reglas decidió los accesos y
            el modelo solo los tradujo a la sintaxis de la plataforma. No otorgó ni
            sugirió accesos. El script no se ejecuta mientras un aprobador no lo
            autorice de forma explícita, y quien originó la solicitud no puede aprobarla.
          </p>
          <div class="acciones">
            <label class="campo" style="margin:0">
              Aprobador
              <input type="text" id="aprobador" value="U00009" style="width:110px">
            </label>
            <button class="boton roja" id="rechazar" ${resuelta ? 'disabled' : ''}>Rechazar</button>
            <button class="boton verde" id="aprobar"
                    ${resuelta || !d.script ? 'disabled' : ''}>Aprobar y ejecutar</button>
          </div>
        </div>
      </section>`;
  }

  function conectarDetalle(raiz) {
    raiz.querySelector('#volver')?.addEventListener('click', mostrar);

    raiz.querySelector('#generar-script')?.addEventListener('click', async (ev) => {
      const boton = ev.currentTarget;
      boton.disabled = true;
      boton.textContent = 'Generando…';
      try {
        await API.generarScript(detalle.id, {
          plataforma: raiz.querySelector('#plataforma').value,
          actor: detalle.solicitada_por,
          usar_modelo_real: raiz.querySelector('#modelo-real').checked,
        });
        detalle = await API.alta(detalle.id);
        aviso = '';
        renderDetalle();
      } catch (e) {
        aviso = Vista.error(e.message);
        renderDetalle();
      }
    });

    const resolver = async (aprobar) => {
      const actor = raiz.querySelector('#aprobador').value.trim() || 'U00009';
      try {
        if (detalle.script && aprobar) {
          await API.aprobarScript(detalle.script.id, { actor, aprobar: true });
        }
        await API.resolverAlta(detalle.id, { actor, aprobar });
        detalle = await API.alta(detalle.id);
        aviso = `<div class="nota">Solicitud ${aprobar ? 'aprobada' : 'rechazada'}
                 por ${Vista.esc(actor)}. El evento quedó en el registro de auditoría.</div>`;
      } catch (e) {
        aviso = Vista.error(e.message);
      }
      renderDetalle();
    };

    raiz.querySelector('#aprobar')?.addEventListener('click', () => resolver(true));
    raiz.querySelector('#rechazar')?.addEventListener('click', () => resolver(false));
  }

  return { mostrar };
})();
