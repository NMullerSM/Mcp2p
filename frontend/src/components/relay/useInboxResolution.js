import { useCallback, useState } from 'react';
import { useAppConfirmation } from '../common/useAppConfirmation';
import { describeRelayError } from './relayModel';

/**
 * Resolver un mensaje retenido (liberar o descartar) desde cualquier vista de la bandeja del humano.
 *
 * Descartar pasa por el diálogo de confirmación de la app (`useAppConfirmation`); liberar lo confirma el
 * backend con su propio diálogo nativo. Al terminar, `relay.refresh()` recarga la bandeja.
 *
 * Devuelve `dialog`: la vista debe pintarlo (es el diálogo de confirmación de la app).
 */
export function useInboxResolution(api, relay) {
  const [busyId, setBusyId] = useState(null);
  const [feedback, setFeedback] = useState(null);
  const { dialog, run: runWithAppConfirmation } = useAppConfirmation(api);

  const resolve = useCallback(
    async (msgId, decision) => {
      if (!api?.relay_resolve_parked || busyId) return;
      const verb = decision === 'release' ? 'liberar el mensaje' : 'descartar el mensaje';
      setBusyId(msgId);
      setFeedback(null);
      try {
        const invoke = () => api.relay_resolve_parked(msgId, decision);
        const res = await (decision === 'discard' ? runWithAppConfirmation(invoke) : invoke());
        if (res?.success === false) {
          setFeedback({ tone: res.code === 'CONFIRMATION_DECLINED' ? 'info' : 'error', text: describeRelayError(res, verb) });
        } else {
          setFeedback({
            tone: 'ok',
            text: decision === 'release' ? 'Mensaje liberado: ya está en la bandeja del agente destino.' : 'Mensaje descartado: el destino no lo verá.'
          });
        }
      } catch (err) {
        setFeedback({ tone: 'error', text: describeRelayError(err, verb) });
      } finally {
        setBusyId(null);
        await relay?.refresh?.();
      }
    },
    [api, busyId, relay, runWithAppConfirmation]
  );

  return { resolve, busyId, feedback, dialog };
}
