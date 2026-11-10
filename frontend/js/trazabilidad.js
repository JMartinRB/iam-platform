/* Scripts generados y registro de auditoria.
 *
 * Las dos pantallas responden a la misma pregunta desde angulos
 * distintos: que hizo el sistema y quien lo autorizo. Son las que
 * convierten una demostracion en algo auditable.
 */

const Trazabilidad = (() => {

  // ------------------------------------------------------------- scripts

  async function scripts() {
    let filas = [];
    try {
      filas = await API.scripts();
    } catch (e) {
      return Vista.pintar(cabecera('Scripts generados',
        'Historial de scripts producidos, validados y aprobados') + Vista.error(e.message));
    }

    const cuerpo = filas.length ? `
      <section class="panel">
        <table>
          <thead>
            <tr>
              <th class="num" style="width:70px">ID</th>
              <th style="width:150px">Plataforma</th>
              <th>Proveedor</th>
              <th class="num" style="width:90px">Intentos</th>
              <th style="width:130px">Validación</th>
              <th style="width:140px">Aprobado por</th>
              <th style="width:160px">Generado</th>
            </tr>
          </thead>
          <tbody>${filas.map((s) => `
            <tr class="clicable" data-script="${s.id}">
              <td class="num">${s.id}</td>
              <td>${Vista.esc(s.plataforma)}</td>
              <td>${Vista.esc(s.proveedor)} · ${Vista.esc(s.modelo)}
                  ${s.proveedor_es_simulado
                    ? '<div class="sub-celda">proveedor simulado</div>' : ''}</td>
              <td class="num">${s.intentos}</td>
              <td>${s.estado_validacion === 'valido'
                    ? '<span class="sello ok">Válido</span>'
                    : '<span class="sev critica">Rechazado</span>'}</td>
              <td>${Vista.esc(s.aprobado_por || '—')}</td>
              <td>${Vista.fechaHora(s.generado_en)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </section>` : `<section class="panel">${Vista.vacio(
        'Todavía no se generó ningún script.')}</section>`;

    Vista.pintar(
      cabecera('Scripts generados', 'Historial de scripts producidos, validados y aprobados')
      + cuerpo,
      (raiz) => raiz.querySelectorAll('[data-script]').forEach((f) =>
        f.addEventListener('click', () =>
          verScript(filas.find((s) => s.id === Number(f.dataset.script))))),
    );
  }

  function verScript(s) {
    if (!s) return;
    const controles = s.controles.map((c) => `
      <span class="control ${c.aprobado ? '' : 'falla'}">
        ${c.aprobado ? '✓' : '✗'} ${Vista.esc(c.nombre)}
      </span>`).join('');

    Vista.modal(`Script ${s.id} · ${s.plataforma}`, `
      <div class="detalle-campo">
        <pre class="codigo">${Vista.codigo(s.contenido)}</pre>
      </div>
      <div class="detalle-campo">
        <div class="rotulo">Controles automáticos</div>
        <div class="controles">${controles}</div>
      </div>
      <div class="detalle-campo">
        <div class="rotulo">Trazabilidad</div>
        <dl class="lista-datos">
          <div><dt>Proveedor</dt><dd>${Vista.esc(s.proveedor)} · ${Vista.esc(s.modelo)}</dd></div>
          <div><dt>Salida de un modelo</dt>
               <dd>${s.proveedor_es_simulado ? 'No, proveedor simulado' : 'Sí'}</dd></div>
          <div><dt>Intentos</dt><dd>${s.intentos}</dd></div>
          <div><dt>Solicitud</dt><dd>${s.solicitud_id ?? '—'}</dd></div>
          <div><dt>Aprobado por</dt><dd>${Vista.esc(s.aprobado_por || 'sin aprobar')}</dd></div>
          <div><dt>Generado</dt><dd>${Vista.fechaHora(s.generado_en)}</dd></div>
        </dl>
      </div>`);
  }

  // ----------------------------------------------------------- auditoria

  async function auditoria() {
    let eventos = [];
    try {
      eventos = await API.auditoria({ limite: 200 });
    } catch (e) {
      return Vista.pintar(cabecera('Registro de auditoría',
        'Qué hizo el sistema y quién lo autorizó') + Vista.error(e.message));
    }

    const cuerpo = eventos.length ? `
      <section class="panel">
        <table>
          <thead>
            <tr>
              <th style="width:170px">Momento</th>
              <th style="width:120px">Actor</th>
              <th style="width:190px">Acción</th>
              <th style="width:150px">Entidad</th>
              <th>Detalle</th>
            </tr>
          </thead>
          <tbody>${eventos.map((e) => `
            <tr>
              <td>${Vista.fechaHora(e.ocurrido_en)}</td>
              <td class="mono">${Vista.esc(e.actor)}</td>
              <td class="titulo-celda">${Vista.esc(e.accion)}</td>
              <td>${Vista.esc(e.entidad)}${e.entidad_id ? ' #' + e.entidad_id : ''}</td>
              <td class="sub-celda mono" style="margin:0">${resumirDetalle(e.detalle)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </section>` : `<section class="panel">${Vista.vacio(
        'El registro está vacío.')}</section>`;

    Vista.pintar(
      cabecera('Registro de auditoría', 'Qué hizo el sistema y quién lo autorizó')
      + cuerpo
      + `<div class="nota">
           El registro es de solo lectura: la API no expone ninguna forma de escribir ni
           de borrar un evento. El entrevistado del apéndice F pidió además que fuera
           inalterable; hoy es una tabla común y cerrarlo de verdad requiere
           almacenamiento append-only o encadenamiento de hashes, que quedó declarado
           como trabajo futuro.
         </div>`);
  }

  /* Recorta primero y escapa despues. Al reves, el corte puede partir una
   * entidad HTML por la mitad y la celda termina mostrando "&quo". */
  function resumirDetalle(detalle) {
    const pares = Object.entries(detalle || {});
    if (!pares.length) return '—';
    const texto = pares
      .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
      .join(' · ');
    return Vista.esc(texto.length > 130 ? texto.slice(0, 130) + '…' : texto);
  }

  function cabecera(titulo, bajada) {
    return `
      <div class="encabezado">
        <div><h2>${Vista.esc(titulo)}</h2><p>${Vista.esc(bajada)}</p></div>
      </div>`;
  }

  return { scripts, auditoria };
})();
