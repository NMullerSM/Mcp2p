import React, { useState } from 'react';
import { Send } from 'lucide-react';
import { MAX_MESSAGE_BYTES, utf8Length } from './relayModel';
import { btnBase, fieldStyle, mutedText, sectionTitle, toneBox } from './relayStyles';

/**
 * Enviar un mensaje a un agente POR SU BANDEJA, como humano. No escribe en la terminal del agente: llega como un
 * sobre firmado cuyo remitente es el humano y el agente lo lee con inbox_next / inbox_wait.
 *
 * `targets`: terminales abiertas del proyecto `[{ paneId, label }]`. `onSend(paneId, text, kind)` devuelve
 * `{ ok, text }` con el resultado a mostrar (lo resuelve RelayPanel con describeRelayError).
 */
export function SendToAgent({ targets = [], onSend, defaultTarget = '' }) {
  const [target, setTarget] = useState(defaultTarget);
  const [kind, setKind] = useState('note');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const effectiveTarget = targets.some((t) => t.paneId === target) ? target : targets[0]?.paneId || '';
  const bytes = utf8Length(text);
  const tooLong = bytes > MAX_MESSAGE_BYTES;
  const canSend = Boolean(effectiveTarget) && text.trim().length > 0 && !tooLong && !busy;

  async function submit(event) {
    event.preventDefault();
    if (!canSend) return;
    setBusy(true);
    setResult(null);
    try {
      const out = await onSend(effectiveTarget, text, kind);
      setResult(out);
      if (out?.ok) setText('');
    } catch (err) {
      setResult({ ok: false, text: `No se pudo enviar el mensaje: ${err?.message || 'error desconocido'}. Vuelva a intentarlo.` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section data-testid="send-to-agent" aria-label="Enviar un mensaje a un agente" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <h4 style={sectionTitle}>Enviar a un agente</h4>
      <p style={{ ...mutedText, margin: 0 }}>
        Llega a su bandeja y, si esa terminal tiene la entrega directa, tambien se escribe en ella: el agente lo lee
        con inbox_next o inbox_wait.
      </p>

      {targets.length === 0 ? (
        <p data-testid="send-to-agent-empty" style={{ ...mutedText, margin: 0 }}>
          No hay terminales abiertas en este proyecto. Abra un agente desde «Lanzar» para poder escribirle.
        </p>
      ) : (
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            <select
              data-testid="send-to-agent-target"
              aria-label="Terminal de destino"
              value={effectiveTarget}
              onChange={(event) => setTarget(event.target.value)}
              style={{ ...fieldStyle, flex: 1 }}
            >
              {targets.map((t) => (
                <option key={t.paneId} value={t.paneId}>{t.label}</option>
              ))}
            </select>
            <select
              data-testid="send-to-agent-kind"
              aria-label="Tipo de mensaje"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              style={fieldStyle}
            >
              <option value="note">Nota</option>
              <option value="task.request">Tarea</option>
            </select>
          </div>
          <textarea
            data-testid="send-to-agent-text"
            aria-label="Texto del mensaje"
            rows={4}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Escriba el mensaje para el agente…"
            style={{ ...fieldStyle, resize: 'vertical', fontFamily: 'inherit' }}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span
              data-testid="send-to-agent-size"
              style={{ ...mutedText, color: tooLong ? 'var(--accent-danger)' : 'var(--text-muted)' }}
            >
              {bytes} / {MAX_MESSAGE_BYTES} bytes{tooLong ? ' — demasiado largo: acórtelo' : ''}
            </span>
            <button
              type="submit"
              data-testid="send-to-agent-submit"
              disabled={!canSend}
              style={{ ...btnBase, marginLeft: 'auto', color: 'var(--accent-primary)', cursor: canSend ? 'pointer' : 'not-allowed' }}
            >
              <Send size={11} />
              <span>{busy ? 'Enviando…' : 'Enviar'}</span>
            </button>
          </div>
        </form>
      )}

      {result && (
        <div data-testid="send-to-agent-result" role={result.ok ? 'status' : 'alert'} style={toneBox(result.ok ? (result.tone || 'ok') : 'error')}>
          {result.text}
        </div>
      )}
    </section>
  );
}
