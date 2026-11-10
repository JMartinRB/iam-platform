/* Utilidades de presentacion compartidas por las pantallas.
 *
 * Todo texto que venga del servidor pasa por `esc` antes de entrar al
 * HTML. Los datos que muestra este sistema son nombres de personas,
 * codigos de permiso y salidas de un modelo de lenguaje; cualquiera de los
 * tres puede traer caracteres que el navegador interpretaria como
 * etiquetas, y la salida de un modelo con mas razon.
 */

const Vista = (() => {

  const esc = (valor) => String(valor ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const NOMBRE_SEVERIDAD = {
    critica: 'Crítica', alta: 'Alta', media: 'Media',
    baja: 'Baja', ninguna: 'Sin hallazgos',
  };

  const sev = (severidad) =>
    `<span class="sev ${esc(severidad)}">${esc(NOMBRE_SEVERIDAD[severidad] || severidad)}</span>`;

  const NOMBRE_CATEGORIA = {
    conflicto_sod: 'Conflicto de Segregación de Funciones',
    cuenta_huerfana: 'Cuentas huérfanas con privilegio',
    sobre_privilegio: 'Usuarios sobre-privilegiados',
    privilege_creep: 'Privilege creep',
    cuenta_dormida: 'Cuentas dormidas',
  };

  const NOMBRE_ORIGEN = {
    rbac_rol: 'RBAC · rol',
    abac_atributo: 'ABAC · atributo',
    directa: 'Asignación directa',
  };

  const numero = (n) => new Intl.NumberFormat('es-AR').format(n ?? 0);

  const fecha = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: 'numeric' });
  };

  const fechaHora = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString('es-AR', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  };

  /* Resalta los comentarios del script. No es un coloreador de sintaxis:
   * lo unico que aporta claridad real sobre una salida de cuatro o cinco
   * lineas es separar la cabecera de trazabilidad del codigo efectivo. */
  const codigo = (texto) => esc(texto)
    .split('\n')
    .map((linea) => linea.trimStart().startsWith('#') || linea.trimStart().startsWith('//')
      ? `<span class="comentario">${linea}</span>`
      : linea)
    .join('\n');

  const vacio = (mensaje, accion = '') =>
    `<div class="vacio"><p>${esc(mensaje)}</p>${accion}</div>`;

  const error = (mensaje) => `<div class="error">${esc(mensaje)}</div>`;

  /* Monta HTML y despues conecta los manejadores. Hacerlo en este orden
   * evita el error de enganchar eventos a nodos que todavia no existen. */
  function pintar(html, conectar) {
    const principal = document.getElementById('principal');
    principal.innerHTML = html;
    principal.scrollTop = 0;
    if (conectar) conectar(principal);
  }

  function modal(titulo, cuerpo) {
    const contenedor = document.getElementById('modal');
    contenedor.innerHTML = `
      <div class="fondo-modal" data-cerrar>
        <div class="modal" role="dialog" aria-modal="true" aria-label="${esc(titulo)}">
          <header>
            <h3>${esc(titulo)}</h3>
            <button class="cerrar" data-cerrar aria-label="Cerrar">&times;</button>
          </header>
          ${cuerpo}
        </div>
      </div>`;

    const cerrar = () => { contenedor.innerHTML = ''; document.removeEventListener('keydown', porTecla); };
    const porTecla = (e) => { if (e.key === 'Escape') cerrar(); };

    contenedor.querySelectorAll('[data-cerrar]').forEach((n) =>
      n.addEventListener('click', (e) => { if (e.target === n) cerrar(); }));
    document.addEventListener('keydown', porTecla);
  }

  return {
    esc, sev, numero, fecha, fechaHora, codigo, vacio, error, pintar, modal,
    NOMBRE_CATEGORIA, NOMBRE_SEVERIDAD, NOMBRE_ORIGEN,
  };
})();
