import React, { useCallback, useId, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { HumanInbox } from './HumanInbox';
import { NodeRequestCard } from './NodeRequestCard';
import { describeRelayError } from './relayModel';
import { useInboxResolution } from './useInboxResolution';
import { btnBase, mutedText, sectionTitle, toneBox } from './relayStyles';

// Un mensaje de otro nodo llega con `remote` (objeto); los retenidos locales no lo traen.
function isNodeItem(item) {
  return Boolean(item?.remote) && typeof item.remote === 'object';
}

const SECTION = { display: 'flex', flexDirection: 'column', gap: '8px', minWidth: 0 };

/**
 * Pestaña «Permisos» del chat: lo que necesita una decisión del humano en el proyecto activo.
 * Dos grupos: mensajes retenidos del relevo (bandeja del humano) y mensajes de otros nodos en cuarentena.
 *
 * `relay` = resultado de `useRelayState` (lo posee `usePermissionsTab`). Liberar/Descartar pasan por
 * `useInboxResolution`. El proyecto ya viene resuelto por quien monta la pestaña.
 */
export function PermissionsTab({ api, relay }) {
  const { resolve, busyId, feedback, dialog } = useInboxResolution(api, relay);
  const [policyBusy, setPolicyBusy] = useState(false);
  const [policyFeedback, setPolicyFeedback] = useState(null);
  const uid = useId();
  const localTitleId = `${uid}-local`;
  const nodeTitleId = `${uid}-nodes`;

  const items = Array.isArray(relay?.inbox?.items) ? relay.inbox.items : [];
  const localItems = items.filter((item) => !isNodeItem(item));
  const nodeItems = items.filter(isNodeItem);
  const inboxError = relay?.inbox?.error || null;
  const loading = Boolean(relay?.loading);
  const listOrderKey = nodeItems.map((item) => item.msg_id).join('|');

  const refresh = useCallback(() => relay?.refresh?.(), [relay]);

  // Confiar o bloquear un nodo: el puente guarda la política y luego se recarga la lista.
  const setNodePolicy = useCallback(
    async (fingerprint, policy) => {
      if (!api?.relay_set_node_policy || policyBusy) return;
      const verb = policy === 'blocked' ? 'bloquear el nodo' : 'cambiar la política del nodo';
      setPolicyBusy(true);
      setPolicyFeedback(null);
      try {
        const res = await api.relay_set_node_policy(fingerprint, policy);
        if (res?.success === false) {
          setPolicyFeedback({ tone: res.code === 'CONFIRMATION_DECLINED' ? 'info' : 'error', text: describeRelayError(res, verb) });
        } else {
          setPolicyFeedback({ tone: 'ok', text: 'Política del nodo actualizada.' });
        }
      } catch (err) {
        setPolicyFeedback({ tone: 'error', text: describeRelayError(err, verb) });
      } finally {
        setPolicyBusy(false);
        await refresh();
      }
    },
    [api, policyBusy, refresh]
  );

  const empty = !loading && items.length === 0 && !inboxError;

  return (
    <div
      data-testid="permissions-tab"
      style={{ display: 'flex', flexDirection: 'column', gap: '14px', padding: '12px', minWidth: 0, boxSizing: 'border-box' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <p style={{ ...mutedText, margin: 0, flex: '1 1 180px', minWidth: 0 }}>
          Lo que necesita su decisión en este proyecto.
        </p>
        <button
          type="button"
          data-testid="permissions-refresh"
          onClick={refresh}
          disabled={loading}
          style={{ ...btnBase, marginLeft: 'auto', padding: '3px 8px' }}
        >
          <RefreshCw size={11} aria-hidden="true" />
          <span>Actualizar</span>
        </button>
      </div>

      {feedback && (
        <div data-testid="permissions-feedback" role={feedback.tone === 'error' ? 'alert' : 'status'} style={toneBox(feedback.tone)}>
          {feedback.text}
        </div>
      )}

      {inboxError && (
        <div data-testid="permissions-error" role="alert" style={toneBox('error')}>
          {inboxError}
        </div>
      )}

      {loading && items.length === 0 && !inboxError && (
        <p data-testid="permissions-loading" style={{ ...mutedText, margin: 0 }}>Cargando permisos…</p>
      )}

      {empty && (
        <p data-testid="permissions-empty" style={{ ...mutedText, margin: 0 }}>No hay permisos pendientes.</p>
      )}

      {localItems.length > 0 && (
        <section aria-labelledby={localTitleId} data-testid="permissions-local" style={SECTION}>
          <h3 id={localTitleId} style={sectionTitle}>Mensajes retenidos</h3>
          <HumanInbox items={localItems} busyId={busyId} onResolve={resolve} />
        </section>
      )}

      {nodeItems.length > 0 && (
        <section aria-labelledby={nodeTitleId} data-testid="permissions-nodes" style={SECTION}>
          <h3 id={nodeTitleId} style={sectionTitle}>Mensajes de otros nodos</h3>
          {policyFeedback && (
            <div data-testid="permissions-policy-feedback" role={policyFeedback.tone === 'error' ? 'alert' : 'status'} style={toneBox(policyFeedback.tone)}>
              {policyFeedback.text}
            </div>
          )}
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '8px', minWidth: 0 }}>
            {nodeItems.map((item) => (
              <li key={item.msg_id} style={{ minWidth: 0, maxWidth: '100%' }}>
                <NodeRequestCard
                  item={item}
                  api={api}
                  busy={Boolean(busyId) || policyBusy}
                  listOrderKey={listOrderKey}
                  onResolve={resolve}
                  onSetNodePolicy={setNodePolicy}
                  onDone={refresh}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      {dialog}
    </div>
  );
}
