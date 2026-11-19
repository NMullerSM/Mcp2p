import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ShieldAlert, ShieldCheck } from 'lucide-react';
import { formatWhen, partyLabel } from './relayModel';
import { relationLabel, shortFingerprint, transportLabel } from './nodeModel';
import { btnBase, chip, mutedText, toneBox } from './relayStyles';

/**
 * Tarjeta de cuarentena para un mensaje llegado desde otro nodo (v2.0.2, WP-7).
 *
 * Seguridad de interacción:
 * - Botones desactivados 1,5 s al aparecer y si la lista se reordena bajo el puntero.
 * - «Entregar»/«Confiar» en extremos opuestos de «Rechazar»/«Bloquear».
 * - Ningún botón recibe foco inicial ni se activa con Enter por defecto.
 * - El texto remoto se pinta en `<pre>` como nodo de texto (`white-space: pre-wrap`), nunca HTML.
 */
export function NodeRequestCard({
  item,
  api = null,
  busy = false,
  armDelayMs = 1500,
  listOrderKey = null,
  onResolve,
  onSetNodePolicy,
  onDone
}) {
  const [armed, setArmed] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const pointerInsideRef = useRef(false);
  const timerRef = useRef(null);
  const prevOrderKeyRef = useRef(listOrderKey);

  const arm = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }
    setArmed(false);
    timerRef.current = setTimeout(() => {
      setArmed(true);
      timerRef.current = null;
    }, armDelayMs);
  }, [armDelayMs]);

  useEffect(() => {
    arm();
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [item?.msg_id, arm]);

  useEffect(() => {
    if (prevOrderKeyRef.current !== listOrderKey) {
      prevOrderKeyRef.current = listOrderKey;
      if (pointerInsideRef.current) {
        arm();
      }
    }
  }, [listOrderKey, arm]);

  if (!item) return null;

  const remote = item.remote || {};
  const fp = remote.device_fingerprint || '';
  const fpShort = remote.fingerprint_short || shortFingerprint(fp);
  const isSameOwner = remote.relation === 'same_owner';
  const expiresAt = remote.expires_at ?? item.expires_at;
  const actionsDisabled = !armed || busy || localBusy;

  const runAction = async (fn) => {
    if (actionsDisabled) return;
    setLocalBusy(true);
    try {
      const res = await fn();
      if (onDone) await onDone(res);
    } finally {
      setLocalBusy(false);
    }
  };

  const handleApprove = () =>
    runAction(() =>
      onResolve ? onResolve(item.msg_id, 'release') : api?.relay_resolve_parked?.(item.msg_id, 'release')
    );

  const handleDiscard = () =>
    runAction(() =>
      onResolve ? onResolve(item.msg_id, 'discard') : api?.relay_resolve_parked?.(item.msg_id, 'discard')
    );

  const handleTrust = () =>
    runAction(() =>
      onSetNodePolicy ? onSetNodePolicy(fp, 'direct') : api?.relay_set_node_policy?.(fp, 'direct')
    );

  const handleBlock = () =>
    runAction(() =>
      onSetNodePolicy ? onSetNodePolicy(fp, 'blocked') : api?.relay_set_node_policy?.(fp, 'blocked')
    );

  return (
    <article
      data-testid={`node-request-card-${item.msg_id}`}
      onMouseEnter={() => {
        pointerInsideRef.current = true;
      }}
      onMouseLeave={() => {
        pointerInsideRef.current = false;
      }}
      style={{
        maxWidth: '100%',
        boxSizing: 'border-box',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        padding: '10px',
        backgroundColor: 'var(--bg-card)',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)', wordBreak: 'break-word' }}>
          {remote.display_name || 'Nodo remoto'}
        </span>
        <span style={{ ...mutedText, fontSize: '11px' }}>(nombre declarado por el nodo)</span>
        {remote.previous_name && (
          <span data-testid={`node-request-prev-name-${item.msg_id}`} style={{ ...chip, color: 'var(--accent-warning)' }}>
            antes: {remote.previous_name}
          </span>
        )}
        <span
          data-testid={`node-request-relation-${item.msg_id}`}
          style={{
            ...chip,
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            color: isSameOwner ? 'var(--accent-success)' : 'var(--accent-warning)'
          }}
        >
          {isSameOwner ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />}
          <span>{relationLabel(remote.relation)}</span>
        </span>
      </div>

      {remote.name_collision && (
        <div
          role="alert"
          data-testid={`node-request-collision-${item.msg_id}`}
          style={toneBox('warning')}
        >
          Un nodo nuevo usa el nombre de un nodo conocido (clave distinta). Comprueba la huella antes de aprobar.
        </div>
      )}

      <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap' }}>
        {fpShort && (
          <span data-testid={`node-request-fp-${item.msg_id}`} style={chip} title={fp}>
            {fpShort}
          </span>
        )}
        <span data-testid={`node-request-to-${item.msg_id}`} style={chip}>
          Para: {partyLabel(item.to)}
        </span>
        <span style={chip}>{item.kind}</span>
        <span style={chip}>{item.trust}</span>
        <span data-testid={`node-request-transport-${item.msg_id}`} style={chip}>
          transporte: {transportLabel(remote.transport)}
        </span>
        {item.created_at ? (
          <span style={chip}>llegó: {formatWhen(item.created_at)}</span>
        ) : null}
        {expiresAt ? (
          <span data-testid={`node-request-expires-${item.msg_id}`} style={chip}>
            caduca: {formatWhen(expiresAt)}
          </span>
        ) : null}
      </div>

      <pre
        data-testid={`node-request-text-${item.msg_id}`}
        style={{
          margin: 0,
          padding: '6px 8px',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: 'var(--bg-app)',
          border: '1px solid var(--border-subtle)',
          fontFamily: 'var(--font-mono)',
          fontSize: '11px',
          color: 'var(--text-primary)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          maxHeight: '160px',
          overflow: 'auto'
        }}
      >
        {item.text ?? '(el texto se borró por retención; solo queda su huella)'}
      </pre>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '8px',
          flexWrap: 'wrap'
        }}
      >
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          <button
            type="button"
            data-testid={`node-request-approve-${item.msg_id}`}
            disabled={actionsDisabled}
            onClick={handleApprove}
            style={{
              ...btnBase,
              color: 'var(--accent-primary)',
              opacity: actionsDisabled ? 0.55 : 1,
              cursor: actionsDisabled ? 'not-allowed' : 'pointer'
            }}
          >
            Entregar este mensaje
          </button>
          <button
            type="button"
            data-testid={`node-request-trust-${item.msg_id}`}
            disabled={actionsDisabled || !fp}
            onClick={handleTrust}
            style={{
              ...btnBase,
              opacity: actionsDisabled || !fp ? 0.55 : 1,
              cursor: actionsDisabled || !fp ? 'not-allowed' : 'pointer'
            }}
          >
            Confiar en este nodo…
          </button>
        </div>

        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          <button
            type="button"
            data-testid={`node-request-discard-${item.msg_id}`}
            disabled={actionsDisabled}
            onClick={handleDiscard}
            style={{
              ...btnBase,
              opacity: actionsDisabled ? 0.55 : 1,
              cursor: actionsDisabled ? 'not-allowed' : 'pointer'
            }}
          >
            Rechazar
          </button>
          <button
            type="button"
            data-testid={`node-request-block-${item.msg_id}`}
            disabled={actionsDisabled || !fp}
            onClick={handleBlock}
            style={{
              ...btnBase,
              color: 'var(--accent-danger)',
              opacity: actionsDisabled || !fp ? 0.55 : 1,
              cursor: actionsDisabled || !fp ? 'not-allowed' : 'pointer'
            }}
          >
            Bloquear nodo
          </button>
        </div>
      </div>
    </article>
  );
}
