import { useEffect, useRef, useState } from 'react';

/**
 * Hook reactivo de sincronización en tiempo real para MCP2P.
 * Los callbacks se leen por ref: cambiar su identidad NO re-arma el intervalo.
 * Si una llamada IPC sigue en vuelo (bridge lento en Windows), el tick se omite en vez de solaparse.
 */
export function useProjectPulse({
  api,
  projectUuid,
  onCardsChange,
  onConsensusChange,
  onGitChange,
  intervalMs = 1200
}) {
  const lastFingerprintRef = useRef('');
  const lastCardsTsRef = useRef('');
  const lastA2ATsRef = useRef('');
  const [pulseCount, setPulseCount] = useState(0);

  const onCardsChangeRef = useRef(onCardsChange);
  const onConsensusChangeRef = useRef(onConsensusChange);
  const onGitChangeRef = useRef(onGitChange);
  useEffect(() => {
    onCardsChangeRef.current = onCardsChange;
    onConsensusChangeRef.current = onConsensusChange;
    onGitChangeRef.current = onGitChange;
  });

  useEffect(() => {
    if (!api || !projectUuid) return;

    let isMounted = true;
    let inFlight = false;

    const checkPulse = async () => {
      // Si la ventana no está visible, reducir consumo de ciclo
      if (document.hidden || inFlight) return;
      inFlight = true;
      try {
        const res = await api.get_project_sync_pulse(projectUuid);
        if (!isMounted || !res || !res.success) return;

        const { fingerprint, cards_updated_at, a2a_updated_at } = res;

        // Primer chequeo: inicializar referencias sin disparar recarga innecesaria
        if (!lastFingerprintRef.current) {
          lastFingerprintRef.current = fingerprint;
          lastCardsTsRef.current = cards_updated_at;
          lastA2ATsRef.current = a2a_updated_at;
          return;
        }

        // Si la huella atómica cambió
        if (fingerprint !== lastFingerprintRef.current) {
          lastFingerprintRef.current = fingerprint;
          setPulseCount(prev => prev + 1);

          // 1. ¿Cambiaron las tarjetas Kanban?
          if (cards_updated_at !== lastCardsTsRef.current) {
            lastCardsTsRef.current = cards_updated_at;
            onCardsChangeRef.current?.();
          }

          // 2. ¿Cambiaron las deliberaciones A2A o sesiones de consenso?
          if (a2a_updated_at !== lastA2ATsRef.current) {
            lastA2ATsRef.current = a2a_updated_at;
            onConsensusChangeRef.current?.();
          }

          // 3. Notificar cambio en Git / Commits
          onGitChangeRef.current?.();
        }
      } catch (e) {
        // Silencioso para evitar saturación de consola en desconexión momentánea
      } finally {
        inFlight = false;
      }
    };

    // Ejecutar chequeo periódico
    const timer = setInterval(checkPulse, intervalMs);

    return () => {
      isMounted = false;
      clearInterval(timer);
    };
  }, [api, projectUuid, intervalMs]);

  return { pulseCount };
}
