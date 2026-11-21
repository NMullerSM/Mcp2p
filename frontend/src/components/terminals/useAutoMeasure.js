import { useEffect, useRef } from 'react';

/**
 * Disparo de la medición automática (WP-5e): una sola llamada al montar y otra al cambiar de proyecto.
 * El backend decide si hay algo que medir y solo actúa si el interruptor está activo; aquí no hay bucle.
 * El API se lee por ref: si cambia su identidad no se vuelve a disparar. Nunca lanza: es un disparo best-effort.
 */
export function useAutoMeasure(api, projectUuid) {
  const apiRef = useRef(api);
  useEffect(() => {
    apiRef.current = api;
  }, [api]);

  useEffect(() => {
    if (!projectUuid) return;
    const current = apiRef.current;
    if (typeof current?.pty_auto_measure_tick !== 'function') return;
    try {
      const pending = current.pty_auto_measure_tick(projectUuid);
      if (pending && typeof pending.catch === 'function') pending.catch(() => {});
    } catch {
      // Un puente que falla no debe tumbar la matriz de terminales.
    }
  }, [projectUuid]);
}
