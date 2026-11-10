/* Cliente HTTP de la API.
 *
 * Una sola funcion hace todos los pedidos para que el manejo de errores
 * este en un lugar y no repartido por cada pantalla. Cuando la API
 * devuelve un error, lo que se muestra es el mensaje que mando el
 * servidor: ahi esta escrito el motivo real, y reemplazarlo por un
 * "ocurrio un error" seria tapar justo lo que hace falta leer.
 */

const API = (() => {

  class ErrorAPI extends Error {
    constructor(mensaje, estado) {
      super(mensaje);
      this.estado = estado;
    }
  }

  async function pedir(ruta, opciones = {}) {
    let respuesta;
    try {
      respuesta = await fetch(ruta, opciones);
    } catch (e) {
      throw new ErrorAPI('No se pudo contactar al servidor. ¿Sigue corriendo?', 0);
    }

    if (!respuesta.ok) {
      let detalle = `${respuesta.status} ${respuesta.statusText}`;
      try {
        const cuerpo = await respuesta.json();
        if (cuerpo && cuerpo.detail) {
          detalle = typeof cuerpo.detail === 'string'
            ? cuerpo.detail
            : JSON.stringify(cuerpo.detail);
        }
      } catch (e) { /* la respuesta no era JSON; queda el texto de estado */ }
      throw new ErrorAPI(detalle, respuesta.status);
    }

    if (respuesta.status === 204) return null;
    return respuesta.json();
  }

  const json = (ruta, cuerpo, metodo = 'POST') => pedir(ruta, {
    method: metodo,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cuerpo),
  });

  const query = (parametros) => {
    const limpios = Object.entries(parametros || {})
      .filter(([, v]) => v !== undefined && v !== null && v !== '');
    return limpios.length ? '?' + new URLSearchParams(limpios) : '';
  };

  return {
    ErrorAPI,

    salud:      ()      => pedir('/api/salud'),

    snapshots:  ()      => pedir('/api/snapshots'),
    generar:    (perfil) => json('/api/snapshots/generar', { perfil }),
    importar:   (datos) => pedir('/api/snapshots/importar', { method: 'POST', body: datos }),
    analizar:   (id)    => json(`/api/snapshots/${id}/analizar`, {}),
    resumen:    (id)    => pedir(`/api/snapshots/${id}/resumen`),
    hallazgos:  (id, f) => pedir(`/api/snapshots/${id}/hallazgos${query(f)}`),
    hallazgo:   (id, h) => pedir(`/api/snapshots/${id}/hallazgos/${h}`),
    exceptuar:  (id, h, datos) => json(`/api/snapshots/${id}/hallazgos/${h}/excepcion`, datos),
    excepciones: (id)   => pedir(`/api/snapshots/${id}/excepciones`),
    revocar:    (id, e, actor) =>
      pedir(`/api/snapshots/${id}/excepciones/${e}${query({ actor })}`, { method: 'DELETE' }),

    usuarios:   (q)     => pedir(`/api/usuarios${query({ q })}`),
    altas:      ()      => pedir('/api/altas'),
    alta:       (id)    => pedir(`/api/altas/${id}`),
    crearAlta:  (datos) => json('/api/altas', datos),
    generarScript: (id, datos) => json(`/api/altas/${id}/script`, datos),
    resolverAlta:  (id, datos) => json(`/api/altas/${id}/resolver`, datos),

    scripts:    ()      => pedir('/api/scripts'),
    aprobarScript: (id, datos) => json(`/api/scripts/${id}/aprobar`, datos),

    auditoria:  (f)     => pedir(`/api/auditoria${query(f)}`),
  };
})();
