/**
 * Envio ORDENADO de la entrada de UN panel al PTY (EGM-80).
 *
 * PyWebView ejecuta cada llamada del puente en un hilo nuevo de Python, asi que dos llamadas
 * `pty_write_input` solapadas pueden escribir en el PTY en orden inverso (medido en la ventana
 * real: "ab" llegaba como "ba" con varios paneles con salida). Aqui hay como mucho UNA llamada en
 * vuelo por panel; lo que se teclea mientras tanto se acumula y sale junto en la siguiente.
 *
 *   send(chunk):     Promise con la respuesta de pty_write_input.
 *   onResult(res):   se llama con cada respuesta. Si es un fallo (`success === false`) se descarta lo
 *                    pendiente: no se sigue escribiendo en un panel que ya no acepta entrada.
 *   onError(err):    excepcion del puente; tambien descarta lo pendiente.
 *
 * La primera llamada sale en el mismo turno (sin esperar al bucle de eventos).
 */
export function createInputSender({ send, onResult, onError }) {
  let pending = '';
  let running = null;

  async function drain() {
    try {
      while (pending) {
        const chunk = pending;
        pending = '';
        let res;
        try {
          res = await send(chunk);
        } catch (err) {
          pending = '';
          onError?.(err);
          return;
        }
        onResult?.(res);
        if (res && res.success === false) {
          pending = '';
          return;
        }
      }
    } finally {
      running = null;
    }
  }

  return {
    push(data) {
      if (data) {
        pending += data;
        if (!running) running = drain();
      }
      return running || Promise.resolve();
    },
    clear() {
      pending = '';
    },
    isBusy: () => running !== null
  };
}
