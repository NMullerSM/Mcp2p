import React, { useCallback, useState } from 'react';
import { HumanInbox } from './HumanInbox';
import { RelayStatus } from './RelayStatus';
import { SendToAgent } from './SendToAgent';
import { describeRelayError } from './relayModel';

/**
 * Sección «Relevo entre agentes» del slider de Terminales: parada global + auditoría, bandeja del humano y envío de
 * un mensaje a un agente. Las acciones llaman al puente; los diálogos de confirmación son NATIVOS (los abre el
 * backend, el JS de la ventana no puede pulsarlos).
 *
 * `relay` = resultado de `useRelayState` (lo posee SwarmTerminalMatrix porque la franja de cada panel también lo
 * necesita). `targets` = terminales abiertas del proyecto `[{ paneId, label }]`.
 */
export function RelayPanel({ api, projectUuid, relay, targets = [] }) {
  const [busyId, setBusyId] = useState(null);
  const [inboxFeedback, setInboxFeedback] = useState(null);
  const [pauseBusy, setPauseBusy] = useState(false);
  const [resumingNode, setResumingNode] = useState(null);
  const [pauseFeedback, setPauseFeedback] = useState(null);

  const resolve = useCallback(
    async (msgId, decision) => {
      if (!api?.relay_resolve_parked || busyId) return;
      const verb = decision === 'release' ? 'liberar el mensaje' : 'descartar el mensaje';
      setBusyId(msgId);
      setInboxFeedback(null);
      try {
        const res = await api.relay_resolve_parked(msgId, decision);
        if (res?.success === false) {
          setInboxFeedback({ tone: res.code === 'CONFIRMATION_DECLINED' ? 'info' : 'error', text: describeRelayError(res, verb) });
        } else {
          setInboxFeedback({
            tone: 'ok',
            text: decision === 'release' ? 'Mensaje liberado: ya está en la bandeja del agente destino.' : 'Mensaje descartado: el destino no lo verá.'
          });
        }
      } catch (err) {
        setInboxFeedback({ tone: 'error', text: describeRelayError(err, verb) });
      } finally {
        setBusyId(null);
        await relay.refresh();
      }
    },
    [api, busyId, relay]
  );

  const setGlobalPause = useCallback(
    async (enabled) => {
      if (!api?.relay_global_pause || pauseBusy || resumingNode) return;
      const verb = enabled ? 'activar la parada global' : 'reanudar el relevo';
      setPauseBusy(true);
      setPauseFeedback(null);
      try {
        const res = await api.relay_global_pause(enabled);
        if (res?.success === false) {
          setPauseFeedback({ tone: res.code === 'CONFIRMATION_DECLINED' ? 'info' : 'error', text: describeRelayError(res, verb) });
        } else {
          setPauseFeedback({
            tone: enabled ? 'warning' : 'ok',
            text: enabled ? 'Relevo detenido: nadie recibe ni envía mensajes.' : 'Relevo reanudado.'
          });
        }
      } catch (err) {
        setPauseFeedback({ tone: 'error', text: describeRelayError(err, verb) });
      } finally {
        setPauseBusy(false);
        await relay.refresh();
      }
    },
    [api, pauseBusy, resumingNode, relay]
  );

  const resumePane = useCallback(
    async (nodeUuid) => {
      if (!api?.relay_resume_pane || pauseBusy || resumingNode) return;
      const verb = 'reanudar el relevo del panel';
      setResumingNode(nodeUuid);
      setPauseFeedback(null);
      try {
        const res = await api.relay_resume_pane(nodeUuid);
        if (res?.success === false) {
          setPauseFeedback({ tone: res.code === 'CONFIRMATION_DECLINED' ? 'info' : 'error', text: describeRelayError(res, verb) });
        } else {
          setPauseFeedback({ tone: 'ok', text: 'Relevo del panel reanudado.' });
        }
      } catch (err) {
        setPauseFeedback({ tone: 'error', text: describeRelayError(err, verb) });
      } finally {
        setResumingNode(null);
        await relay.refresh();
      }
    },
    [api, pauseBusy, resumingNode, relay]
  );

  const send = useCallback(
    async (paneId, text, kind) => {
      if (!api?.relay_send_as_human) {
        return { ok: false, text: 'Esta versión de MCP2P no permite enviar mensajes desde la interfaz. Actualice la aplicación.' };
      }
      const res = await api.relay_send_as_human(projectUuid || null, paneId, text, kind);
      if (res?.success === false) {
        return { ok: false, text: describeRelayError(res, 'enviar el mensaje') };
      }
      await relay.refresh();
      const target = targets.find((t) => t.paneId === paneId)?.label || paneId;
      if (res?.held) {
        return { ok: true, tone: 'warning', text: `Guardado en la bandeja de ${target}, pero el relevo está detenido: se entregará al reanudarlo.` };
      }
      return { ok: true, text: `Enviado a la bandeja de ${target}. El agente lo recibirá cuando llame a inbox_next o inbox_wait.` };
    },
    [api, projectUuid, relay, targets]
  );

  if (!relay?.available) return null;

  return (
    <div data-testid="relay-panel" style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
      <RelayStatus
        audit={relay.audit}
        auditError={relay.auditError}
        loading={relay.loading}
        globalPaused={relay.globalPaused}
        pausedPanes={relay.pausedPanes}
        resumingNode={resumingNode}
        busy={pauseBusy}
        feedback={pauseFeedback}
        onSetGlobalPause={api?.relay_global_pause ? setGlobalPause : undefined}
        onResumePane={api?.relay_resume_pane ? resumePane : undefined}
      />
      <HumanInbox
        items={relay.inbox.items}
        loading={relay.loading}
        error={relay.inbox.error}
        busyId={busyId}
        feedback={inboxFeedback}
        onResolve={resolve}
        onRefresh={relay.refresh}
      />
      {api?.relay_send_as_human && <SendToAgent targets={targets} onSend={send} />}
    </div>
  );
}

