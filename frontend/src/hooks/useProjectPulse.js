import { useEffect, useRef, useState } from 'react';

/**
 * Hook reactivo de sincronización en tiempo real para MCP2P.
 * Detecta cambios producidos por agentes vía MCP, comandos de terminal o Git hooks
 * y dispara las funciones de actualización correspondientes inmediatamente.
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
  const [isLive, setIsLive] = useState(true);
  const [pulseCount, setPulseCount] = useState(0);

  useEffect(() => {
    if (!api || !projectUuid) return;

    let isMounted = true;

    const checkPulse = async () => {
      // Si la ventana no está visible, reducir consumo de ciclo
      if (document.hidden) return;

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
            if (onCardsChange) onCardsChange();
          }

          // 2. ¿Cambiaron las deliberaciones A2A o sesiones de consenso?
          if (a2a_updated_at !== lastA2ATsRef.current) {
            lastA2ATsRef.current = a2a_updated_at;
            if (onConsensusChange) onConsensusChange();
          }

          // 3. Notificar cambio en Git / Commits
          if (onGitChange) onGitChange();
        }
      } catch (e) {
        // Silencioso para evitar saturación de consola en desconexión momentánea
      }
    };

    // Ejecutar chequeo periódico
    const timer = setInterval(checkPulse, intervalMs);

    return () => {
      isMounted = false;
      clearInterval(timer);
    };
  }, [api, projectUuid, onCardsChange, onConsensusChange, onGitChange, intervalMs]);

  return { isLive, pulseCount };
}
