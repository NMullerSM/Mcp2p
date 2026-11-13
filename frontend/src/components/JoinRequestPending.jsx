import React, { useState } from 'react';
import { Clock, Copy, Check, RefreshCw, ArrowLeft } from 'lucide-react';

/**
 * Pantalla mostrada al SOLICITANTE justo después de que `join_p2p_project` devuelva
 * `status: "pending_approval"` (Tarea 5): el stub del proyecto ya existe localmente
 * pero todavía no hay nada clonado, y la aprobación HITL del dueño sigue pendiente.
 *
 * `onRetry` es provisto por App.jsx y encapsula la llamada a
 * `api.check_join_request_status(project.project_uuid)` (Tarea 7): cuando esa unión
 * ya se completó, App.jsx navega fuera de esta pantalla directamente (no hay nada que
 * mostrar aquí). En cualquier otro caso (`still_pending`, `no_remote_repos`, o un
 * error genérico -- nótese que `check_join_request_status` nunca devuelve realmente
 * `"locked"`, ver api_bridge.py), App.jsx resuelve la promesa con un mensaje de texto
 * ya listo para el usuario, que esta pantalla simplemente renderiza.
 *
 * `onBack` es la única forma de abandonar esta pantalla sin una unión exitosa (p. ej.
 * el caso terminal `no_remote_repos`, que por diseño nunca se resuelve reintentando):
 * App.jsx la conecta a la misma lógica de "volver a proyectos" que ya usa el resto de
 * la app. No afecta nada en el backend -- el stub del proyecto ya quedó persistido en
 * `join_p2p_project` independientemente de que esta pantalla se muestre o no.
 */
export function JoinRequestPending({ project, requestCode, onRetry, onBack }) {
  const [copied, setCopied] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');

  const copyRequestCode = () => {
    if (!requestCode) return;
    navigator.clipboard.writeText(requestCode);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleRetry = async () => {
    if (!onRetry || retrying) return;
    setRetrying(true);
    setStatusMessage('');
    try {
      const message = await onRetry();
      if (message) setStatusMessage(message);
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div
      data-testid="join-request-pending"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        padding: '24px'
      }}
    >
      <div style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '28px 32px',
        maxWidth: '480px',
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
        gap: '16px',
        boxShadow: 'var(--shadow-sm)'
      }}>
        {onBack && (
          <button
            onClick={onBack}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              alignSelf: 'flex-start',
              padding: '5px 0',
              fontSize: '12px',
              fontWeight: 500,
              border: 'none',
              background: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer'
            }}
          >
            <ArrowLeft size={14} /> Volver a Proyectos
          </button>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{
            width: '38px',
            height: '38px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'rgba(245, 158, 11, 0.12)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-warning)',
            flexShrink: 0
          }}>
            <Clock size={18} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
              Solicitud de Unión Pendiente
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
              Esperando la aprobación del dueño de <strong>{project?.name || 'este proyecto'}</strong>
            </div>
          </div>
        </div>

        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.5 }}>
          Comparte este código de solicitud con el dueño del proyecto si la notificación automática no llega.
          En cuanto apruebe tu acceso, pulsa "Reintentar" para completar la unión y clonar los repositorios.
        </p>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <code style={{
            flex: 1,
            fontSize: '11px',
            fontFamily: 'var(--font-mono)',
            backgroundColor: 'var(--bg-app)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-sm)',
            padding: '8px 10px',
            overflowWrap: 'anywhere',
            wordBreak: 'break-all',
            color: 'var(--text-primary)'
          }}>
            {requestCode}
          </code>
          <button
            onClick={copyRequestCode}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '7px 14px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              border: 'none',
              backgroundColor: copied ? 'var(--accent-success)' : 'var(--accent-primary)',
              color: '#ffffff',
              cursor: 'pointer',
              boxShadow: 'var(--shadow-sm)',
              flexShrink: 0
            }}
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? 'Copiado' : 'Copiar Código'}
          </button>
        </div>

        {statusMessage && (
          <div
            data-testid="join-request-status-message"
            style={{
              fontSize: '12px',
              color: 'var(--text-secondary)',
              backgroundColor: 'var(--bg-subtle)',
              borderRadius: 'var(--radius-sm)',
              padding: '8px 12px',
              lineHeight: 1.45
            }}
          >
            {statusMessage}
          </div>
        )}

        <button
          onClick={handleRetry}
          disabled={retrying}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            padding: '9px 16px',
            fontSize: '13px',
            fontWeight: 600,
            borderRadius: 'var(--radius-md)',
            border: 'none',
            backgroundColor: 'var(--accent-primary)',
            color: '#ffffff',
            cursor: retrying ? 'default' : 'pointer',
            opacity: retrying ? 0.7 : 1
          }}
        >
          <RefreshCw size={14} />
          {retrying ? 'Reintentando...' : 'Reintentar'}
        </button>
      </div>
    </div>
  );
}
