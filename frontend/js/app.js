/* Arranque y navegacion.
 *
 * El estado compartido entre pantallas es chico a proposito: cual es el
 * snapshot activo y nada mas. Todo lo demas lo pide cada pantalla cuando
 * se muestra, que a esta escala es mas simple de seguir que mantener una
 * copia local sincronizada y es imposible que quede desactualizado.
 */

const App = (() => {

  let snapshots = [];
  let snapshotId = null;

  const PANTALLAS = {
    panel: Panel.mostrar,
    carga: Carga.mostrar,
    altas: Altas.mostrar,
    scripts: Trazabilidad.scripts,
    auditoria: Trazabilidad.auditoria,
  };

  const snapshotActual = () =>
    snapshots.find((s) => s.id === snapshotId) || snapshots[0] || null;

  async function refrescarSnapshots(preferido = null) {
    try {
      snapshots = await API.snapshots();
    } catch (e) {
      snapshots = [];
    }
    if (preferido && snapshots.some((s) => s.id === preferido)) {
      snapshotId = preferido;
    } else if (!snapshots.some((s) => s.id === snapshotId)) {
      snapshotId = snapshots.length ? snapshots[0].id : null;
    }
    return snapshots;
  }

  async function elegirSnapshot(id) {
    snapshotId = id;
  }

  let actual = null;

  /* Navegar escribe el hash, y escribir el hash dispara `hashchange`, que
   * vuelve a llamar acá. Sin este corte, cada navegación pintaba la misma
   * pantalla dos veces: dos pedidos de análisis en paralelo sobre el mismo
   * snapshot, y los hallazgos terminaban duplicados. */
  function ir(nombre) {
    const destino = PANTALLAS[nombre] ? nombre : 'panel';
    if (destino === actual) return;
    actual = destino;

    document.querySelectorAll('.nav').forEach((b) =>
      b.classList.toggle('activa', b.dataset.pantalla === destino));
    if (location.hash.replace('#', '') !== destino) location.hash = destino;
    PANTALLAS[destino]();
  }

  async function iniciar() {
    document.querySelectorAll('.nav').forEach((b) =>
      b.addEventListener('click', () => ir(b.dataset.pantalla)));

    window.addEventListener('hashchange', () =>
      ir(location.hash.replace('#', '') || 'panel'));

    await refrescarSnapshots();

    // Sin datos cargados, la pantalla util es la de carga y no un panel
    // vacio que no explica por que esta vacio.
    const inicial = location.hash.replace('#', '')
      || (snapshots.length ? 'panel' : 'carga');
    ir(inicial);
  }

  return { iniciar, ir, snapshotActual, refrescarSnapshots, elegirSnapshot };
})();

document.addEventListener('DOMContentLoaded', App.iniciar);
