import React, { useId } from 'react';
import {
  MANDATE_INSTRUCTION_NOTE,
  MANDATE_SECRET_WARNING,
  MAX_MANDATE_CHARS
} from './roleModel';

/**
 * Editor de mandato por panel.
 * No limpia ni altera caracteres en el cliente (solo limita a 2000 caracteres);
 * el saneado definitivo lo hace el backend y la UI refleja el valor devuelto.
 */
export function MandateEditor({
  value = '',
  onChange,
  disabled = false,
  id,
  testIdPrefix = 'mandate'
}) {
  const generatedId = useId();
  const textareaId = id || `${generatedId}-textarea`;
  const counterId = `${generatedId}-counter`;
  const warningId = `${generatedId}-warning`;

  const text = typeof value === 'string' ? value : '';
  const atLimit = text.length >= MAX_MANDATE_CHARS;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '8px',
          flexWrap: 'wrap'
        }}
      >
        <label
          htmlFor={textareaId}
          style={{ fontSize: '11.5px', fontWeight: 600, color: 'var(--text-secondary)' }}
        >
          Mandato (opcional)
        </label>
        <span
          id={counterId}
          data-testid={`${testIdPrefix}-counter`}
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '10.5px',
            color: atLimit ? 'var(--accent-warning)' : 'var(--text-muted)'
          }}
        >
          {text.length} / {MAX_MANDATE_CHARS}
        </span>
      </div>
      <textarea
        id={textareaId}
        data-testid={`${testIdPrefix}-textarea`}
        value={text}
        maxLength={MAX_MANDATE_CHARS}
        disabled={disabled}
        rows={3}
        aria-describedby={`${counterId} ${warningId}`}
        placeholder="Instrucciones específicas para este agente…"
        onChange={(e) => {
          const next = e.target.value.slice(0, MAX_MANDATE_CHARS);
          onChange?.(next);
        }}
        style={{
          width: '100%',
          boxSizing: 'border-box',
          minWidth: 0,
          minHeight: '64px',
          padding: '6px 8px',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-subtle)',
          backgroundColor: 'var(--bg-app)',
          color: 'var(--text-primary)',
          fontFamily: 'var(--font-ui)',
          fontSize: '11.5px',
          lineHeight: 1.4,
          resize: 'vertical'
        }}
      />
      <p
        id={warningId}
        data-testid={`${testIdPrefix}-warning`}
        style={{
          margin: 0,
          fontSize: '10.5px',
          lineHeight: 1.35,
          color: 'var(--text-muted)'
        }}
      >
        {MANDATE_SECRET_WARNING} {MANDATE_INSTRUCTION_NOTE}
      </p>
    </div>
  );
}
