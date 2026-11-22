import React, { useId, useLayoutEffect, useRef, useState } from 'react';
import { Send } from 'lucide-react';
import {
  BYTES_WARN_RATIO,
  MAX_MESSAGE_BYTES,
  applyMention,
  filterParticipants,
  mentionQueryAt,
  utf8ByteLength
} from './chatModel';

const CODE_MESSAGES = {
  UNKNOWN_MENTION: 'Hay una mención que no existe en este proyecto.',
  EMPTY_MESSAGE: 'Escribe un mensaje antes de enviar.'
};

function failureText(res) {
  return res?.message || CODE_MESSAGES[res?.code] || 'No se pudo enviar el mensaje.';
}

/**
 * Redactor del chat: Enter envia, Shift+Enter hace nueva linea, y "@" abre la lista de menciones
 * (flechas para moverse, Tab o Enter para elegir, Esc para cerrarla).
 */
export function ChatComposer({ api, projectUuid, participants = [], onSent }) {
  const uid = useId();
  const textareaId = `${uid}-text`;
  const hintId = `${uid}-hint`;
  const counterId = `${uid}-counter`;
  const errorId = `${uid}-error`;
  const listId = `${uid}-mentions`;
  const optionId = (i) => `${uid}-opt-${i}`;

  const textareaRef = useRef(null);
  const pendingCaretRef = useRef(null);
  const [text, setText] = useState('');
  const [caret, setCaret] = useState(0);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const [partial, setPartial] = useState([]);
  const [dismissed, setDismissed] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [focused, setFocused] = useState(false);

  const mention = mentionQueryAt(text, caret);
  const suggestions = mention ? filterParticipants(participants, mention.query) : [];
  const listOpen = Boolean(mention) && !dismissed && suggestions.length > 0;
  const current = suggestions.length ? Math.min(activeIndex, suggestions.length - 1) : 0;

  const bytes = utf8ByteLength(text);
  const overLimit = bytes > MAX_MESSAGE_BYTES;
  const nearLimit = bytes > MAX_MESSAGE_BYTES * BYTES_WARN_RATIO;
  const canSend = text.trim() !== '' && !sending && !overLimit;

  // Tras elegir una mencion el cursor debe quedar al final de lo insertado.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (el && pendingCaretRef.current !== null) {
      el.setSelectionRange(pendingCaretRef.current, pendingCaretRef.current);
      pendingCaretRef.current = null;
    }
  }, [text]);

  const syncCaret = (e) => setCaret(e.target.selectionStart ?? 0);

  const onChange = (e) => {
    setText(e.target.value);
    setCaret(e.target.selectionStart ?? e.target.value.length);
    setDismissed(false);
    setActiveIndex(0);
  };

  const choose = (participant) => {
    const next = applyMention(text, caret, participant);
    pendingCaretRef.current = next.caret;
    setText(next.text);
    setCaret(next.caret);
    setActiveIndex(0);
  };

  const send = async () => {
    const body = text.trim();
    if (!canSend || body === '') return;
    if (typeof api?.chat_send !== 'function') {
      setError('Este puente no permite enviar mensajes de chat.');
      return;
    }
    setSending(true);
    setError(null);
    setPartial([]);
    try {
      const res = await api.chat_send(projectUuid, body);
      const results = Array.isArray(res?.results) ? res.results : [];
      setPartial(
        results
          .filter((r) => r && r.ok === false)
          .map((r) => ({ target: r.target || 'destino', message: r.message || CODE_MESSAGES[r.code] || 'No entregado' }))
      );
      if (res && res.success) {
        // Solo se limpia si el usuario no ha seguido escribiendo mientras se enviaba.
        setText((now) => (now.trim() === body ? '' : now));
        setCaret(0);
        setDismissed(false);
        setActiveIndex(0);
        if (typeof onSent === 'function') onSent(res);
      } else {
        setError(failureText(res));
      }
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e) => {
    if (e.nativeEvent?.isComposing) return;
    if (listOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % suggestions.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if ((e.key === 'Tab' && !e.shiftKey) || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        choose(suggestions[current]);
        return;
      }
      if (e.key === 'Escape') {
        // Esc cierra solo la lista: no debe llegar al panel y cerrar el chat entero.
        e.preventDefault();
        e.stopPropagation();
        setDismissed(true);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const describedBy = [hintId, nearLimit ? counterId : null, error ? errorId : null].filter(Boolean).join(' ');

  return (
    <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', gap: '6px', padding: '8px 12px 10px', borderTop: '1px solid var(--border-subtle)', minWidth: 0 }}>
      {listOpen && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Menciones disponibles"
          style={{
            position: 'absolute',
            left: '12px',
            right: '12px',
            bottom: '100%',
            margin: 0,
            marginBottom: '4px',
            padding: '4px',
            listStyle: 'none',
            maxHeight: '220px',
            overflowY: 'auto',
            zIndex: 2,
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            boxShadow: 'var(--shadow-md)'
          }}
        >
          {suggestions.map((p, i) => {
            const selected = i === current;
            return (
              <li
                key={`${p.principal || p.mention}-${i}`}
                id={optionId(i)}
                role="option"
                aria-selected={selected}
                onMouseDown={(e) => {
                  // mousedown y no click: el campo no debe perder el foco al elegir.
                  e.preventDefault();
                  choose(p);
                }}
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'baseline',
                  gap: '6px',
                  padding: '5px 8px',
                  borderRadius: 'var(--radius-sm)',
                  cursor: 'pointer',
                  fontSize: '12px',
                  color: 'var(--text-primary)',
                  backgroundColor: selected ? 'rgba(var(--accent-primary-rgb), 0.16)' : 'transparent'
                }}
              >
                <strong style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{p.mention}</strong>
                <span style={{ color: 'var(--text-secondary)' }}>{p.label}</span>
                {p.role_label && <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>({p.role_label})</span>}
              </li>
            );
          })}
        </ul>
      )}

      <label htmlFor={textareaId} style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>
        Mensaje
      </label>
      <textarea
        id={textareaId}
        ref={textareaRef}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={listOpen}
        aria-controls={listOpen ? listId : undefined}
        aria-activedescendant={listOpen ? optionId(current) : undefined}
        aria-describedby={describedBy || undefined}
        aria-invalid={overLimit || undefined}
        rows={2}
        value={text}
        onChange={onChange}
        onKeyDown={onKeyDown}
        onKeyUp={syncCaret}
        onClick={syncCaret}
        onSelect={syncCaret}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder="Escribe un mensaje. Usa @ para mencionar."
        style={{
          width: '100%',
          minWidth: 0,
          resize: 'vertical',
          padding: '7px 9px',
          fontSize: '13px',
          fontFamily: 'var(--font-ui)',
          lineHeight: 1.4,
          borderRadius: 'var(--radius-md)',
          border: `1px solid ${focused ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
          backgroundColor: 'var(--bg-app)',
          color: 'var(--text-primary)'
        }}
      />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', minWidth: 0 }}>
        <p id={hintId} style={{ margin: 0, fontSize: '10.5px', color: 'var(--text-muted)', lineHeight: 1.3, minWidth: 0 }}>
          Enter envía · Shift+Enter nueva línea
        </p>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
          {nearLimit && (
            <span
              id={counterId}
              data-tone={overLimit ? 'danger' : 'warn'}
              style={{
                fontSize: '10.5px',
                fontFamily: 'var(--font-mono)',
                color: overLimit ? 'var(--accent-danger)' : 'var(--accent-warning)'
              }}
            >
              {bytes} / {MAX_MESSAGE_BYTES} bytes{overLimit ? ' · demasiado largo' : ''}
            </span>
          )}
          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            aria-busy={sending || undefined}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '5px 12px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--accent-primary)',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: canSend ? 'pointer' : 'not-allowed',
              opacity: canSend ? 1 : 0.5
            }}
          >
            <Send size={13} aria-hidden="true" />
            {sending ? 'Enviando…' : 'Enviar'}
          </button>
        </div>
      </div>

      {error && (
        <p id={errorId} role="alert" style={{ margin: 0, fontSize: '11.5px', color: 'var(--accent-danger)', lineHeight: 1.4 }}>
          {error}
        </p>
      )}

      {partial.length > 0 && (
        <ul aria-label="Destinos que no recibieron el mensaje" style={{ margin: 0, paddingLeft: '16px', fontSize: '11px', color: 'var(--accent-warning)', lineHeight: 1.4 }}>
          {partial.map((p, i) => (
            <li key={`${p.target}-${i}`}>
              {p.target}: {p.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
