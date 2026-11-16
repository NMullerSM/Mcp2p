/**
 * Bucle de sondeo de UN panel. Espera la respuesta de `poll()` antes de programar la
 * siguiente (nunca hay dos llamadas solapadas para el mismo panel) y el retardo lo decide
 * `getDelay()` en cada vuelta, asi el ritmo se adapta a la actividad.
 *
 *   poll():            Promise con la respuesta de pty_poll_output (o lo que sea).
 *   onResult(res):     procesa la respuesta; devolver `false` detiene el bucle. Tambien se
 *                      llama con la respuesta de una llamada que ya estaba en vuelo cuando se
 *                      hizo `stop()` (no se programan mas); quien la recibe decide si aplicarla.
 *   getDelay():        ms hasta la siguiente llamada.
 *
 * `wake()` adelanta la siguiente llamada (tras teclear): si el temporizador pendiente es
 * mas lento que el retardo actual se reprograma con este (nunca la retrasa); si hay una llamada en vuelo no hace nada
 * (al terminar, `getDelay()` ya reflejara la actividad).
 */
export function createPanePoller({ poll, onResult, getDelay }) {
  let stopped = false;
  let running = false;
  let inFlight = false;
  let timer = null;
  let dueAt = 0;

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const schedule = (delay) => {
    if (stopped) return;
    clearTimer();
    const wait = Math.max(0, delay);
    dueAt = Date.now() + wait;
    timer = setTimeout(tick, wait);
  };

  async function tick() {
    timer = null;
    if (stopped || inFlight) return;
    inFlight = true;
    let res = null;
    try {
      res = await poll();
    } catch {
      res = null; // fallo transitorio: no romper el ciclo
    }
    inFlight = false;
    if (res && onResult(res) === false) stopped = true;
    if (stopped) return;
    schedule(getDelay());
  }

  return {
    start() {
      if (running || stopped) return;
      running = true;
      void tick(); // la primera llamada sale ya, sin esperar un turno del bucle de eventos
    },
    stop() {
      stopped = true;
      clearTimer();
    },
    wake() {
      if (stopped || !running || inFlight || timer === null) return;
      // Solo adelanta: si ya toca antes, no se retrasa (teclear deprisa no debe dejar sin sondeo).
      const delay = Math.max(0, getDelay());
      if (Date.now() + delay < dueAt) schedule(delay);
    },
    isRunning: () => running && !stopped,
    isInFlight: () => inFlight
  };
}
