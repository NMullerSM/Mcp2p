import React, { useState, useEffect } from 'react';
import { X, CheckCircle2, GitBranch, User, CheckSquare, Save, Clock } from 'lucide-react';

export function ModalOverlay({ isOpen, onClose, title, maxWidth = '500px', children }) {
  if (!isOpen) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.6)',
        backdropFilter: 'blur(3px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 200,
        padding: '16px'
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          backgroundColor: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-lg)',
          width: '100%',
          maxWidth: maxWidth,
          maxHeight: '88vh',
          overflowY: 'auto',
          padding: '22px',
          boxShadow: 'var(--shadow-lg)',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px'
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ fontSize: '16px', fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>{title}</h3>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', padding: '4px' }}
          >
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function NewProjectModal({ isOpen, onClose, onSubmit }) {
  const [name, setName] = useState('');
  const [codeUrl, setCodeUrl] = useState('');
  const [govUrl, setGovUrl] = useState('');
  const [autoPublish, setAutoPublish] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    onSubmit(name.trim(), codeUrl.trim(), govUrl.trim(), autoPublish);
    setName('');
    setCodeUrl('');
    setGovUrl('');
    setAutoPublish(false);
  };

  return (
    <ModalOverlay isOpen={isOpen} onClose={onClose} title="Crear Nuevo Proyecto Aislado">
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>
          Se generará automáticamente la estructura <code>app_{'{nombre}'}</code> y <code>okf_{'{nombre}_{id}'}</code> con sus repositorios Git y rama huérfana.
        </p>

        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>Nombre del Proyecto *</label>
          <input
            autoFocus
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Ej: Servicio de Facturación"
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '13px',
              outline: 'none'
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>URL Remota Repositorio Código (Opcional)</label>
          <input
            type="text"
            value={codeUrl}
            onChange={(e) => setCodeUrl(e.target.value)}
            placeholder="https://github.com/usuario/mi-repo.git"
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '13px',
              outline: 'none'
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>URL Remota Gobernanza OKF (Opcional)</label>
          <input
            type="text"
            value={govUrl}
            onChange={(e) => setGovUrl(e.target.value)}
            placeholder="https://github.com/usuario/mi-repo-okf.git"
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '13px',
              outline: 'none'
            }}
          />
        </div>

        <label style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          cursor: 'pointer',
          fontSize: '12px',
          padding: '8px 10px',
          backgroundColor: 'var(--bg-subtle)',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-subtle)',
          marginTop: '2px'
        }}>
          <input
            type="checkbox"
            checked={autoPublish}
            onChange={(e) => setAutoPublish(e.target.checked)}
            style={{ cursor: 'pointer' }}
          />
          <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
            ⚡ Publicar automáticamente en GitHub al crear (vía API con credenciales guardadas)
          </span>
        </label>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '6px' }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'transparent',
              color: 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            Cancelar
          </button>
          <button
            type="submit"
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: 'pointer'
            }}
          >
            Crear Proyecto
          </button>
        </div>
      </form>
    </ModalOverlay>
  );
}

export function NewTaskModal({ isOpen, onClose, onSubmit }) {
  const [title, setTitle] = useState('');
  const [desc, setDesc] = useState('');
  const [criteria, setCriteria] = useState('');
  const [cardId, setCardId] = useState(`CARD-${Math.floor(100 + Math.random() * 900)}`);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!title.trim() || !cardId.trim()) return;
    onSubmit(cardId.trim(), title.trim(), desc.trim(), criteria.trim());
    setTitle('');
    setDesc('');
    setCriteria('');
    setCardId(`CARD-${Math.floor(100 + Math.random() * 900)}`);
  };

  return (
    <ModalOverlay isOpen={isOpen} onClose={onClose} title="Nueva Tarea Kanban">
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>Identificador</label>
          <input
            type="text"
            required
            value={cardId}
            onChange={(e) => setCardId(e.target.value)}
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '13px',
              fontFamily: 'var(--font-mono)',
              outline: 'none'
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>Título de la Tarea *</label>
          <input
            autoFocus
            type="text"
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Ej: Implementar endpoint de salud"
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '13px',
              outline: 'none'
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>Descripción Técnica</label>
          <textarea
            rows={2}
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="Detalles técnicos u objetivos..."
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '13px',
              outline: 'none',
              resize: 'none'
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <CheckSquare size={13} color="var(--accent-primary)" /> Criterios de Aceptación (Acceptance Criteria)
            </span>
          </label>
          <textarea
            rows={3}
            value={criteria}
            onChange={(e) => setCriteria(e.target.value)}
            placeholder="Ej: 1. Endpoint /health responde HTTP 200 con JSON.&#10;2. Timeout no excede los 5 segundos.&#10;3. Tests unitarios pasando al 100%."
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
              outline: 'none',
              resize: 'none'
            }}
          />
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '6px' }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'transparent',
              color: 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            Cancelar
          </button>
          <button
            type="submit"
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: 'pointer'
            }}
          >
            Crear Tarea
          </button>
        </div>
      </form>
    </ModalOverlay>
  );
}

export function JoinP2PModal({ isOpen, onClose, onSubmit }) {
  const [token, setToken] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!token.trim()) return;
    onSubmit(token.trim());
    setToken('');
  };

  return (
    <ModalOverlay isOpen={isOpen} onClose={onClose} title="🔗 Unirse a Proyecto P2P">
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>
          Pega el enlace de invitación (<code>mcp2p://join?...</code>) o el token compartido. Se conectará al proyecto y replicará la estructura de repositorios en tu equipo.
        </p>

        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>Token o Enlace P2P *</label>
          <textarea
            autoFocus
            rows={3}
            required
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="mcp2p://join?token=eyJ..."
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
              outline: 'none',
              resize: 'none'
            }}
          />
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '6px' }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'transparent',
              color: 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            Cancelar
          </button>
          <button
            type="submit"
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: 'pointer'
            }}
          >
            Conectar y Desplegar
          </button>
        </div>
      </form>
    </ModalOverlay>
  );
}

export function DeleteProjectModal({ isOpen, project, onClose, onConfirm }) {
  if (!isOpen || !project) return null;

  return (
    <ModalOverlay isOpen={isOpen} onClose={onClose} title="⚠️ Eliminar Proyecto">
      <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.5 }}>
          ¿Estás seguro de que deseas eliminar el proyecto <strong style={{ color: 'var(--text-primary)' }}>{project.name}</strong> ({project.project_uuid})?
          Esta acción eliminará todas las tareas, leases y mensajes asociados de la base de datos local.
        </p>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '8px' }}>
          <button
            onClick={onClose}
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'transparent',
              color: 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            Cancelar
          </button>
          <button
            onClick={onConfirm}
            style={{
              padding: '7px 14px',
              fontSize: '13px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-danger)',
              color: '#ffffff',
              cursor: 'pointer'
            }}
          >
            Eliminar Definitivamente
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}

export function CardDetailModal({ isOpen, card, onClose, onSave, onAcceptTask, onNavigateToBranch, onNavigateToHitl }) {
  const [title, setTitle] = useState(card?.title || '');
  const [desc, setDesc] = useState(card?.description || '');
  const [criteria, setCriteria] = useState(card?.acceptance_criteria || '');
  const [branchRef, setBranchRef] = useState(card?.branch_ref || '');
  const [status, setStatus] = useState(card?.status || 'backlog');

  useEffect(() => {
    if (card) {
      setTitle(card.title || '');
      setDesc(card.description || '');
      setCriteria(card.acceptance_criteria || '');
      setBranchRef(card.branch_ref || '');
      setStatus(card.status || 'backlog');
    }
  }, [card]);

  if (!isOpen || !card) return null;

  const handleSave = (e) => {
    e.preventDefault();
    onSave({
      card_id: card.card_id,
      title: title.trim(),
      description: desc.trim(),
      acceptance_criteria: criteria.trim(),
      branch_ref: branchRef.trim(),
      status
    });
  };

  const handleAccept = () => {
    onAcceptTask({
      card_id: card.card_id,
      title: title.trim(),
      description: desc.trim(),
      acceptance_criteria: criteria.trim(),
      branch_ref: branchRef.trim(),
      status: 'merged'
    });
  };

  const isMerged = status === 'merged' || status === 'consolidated';

  return (
    <ModalOverlay isOpen={isOpen} onClose={onClose} title={`Detalles de Tarea — ${card.card_id}`} maxWidth="560px">
      <form onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
        {/* Metadatos superiores */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          backgroundColor: 'var(--bg-subtle)',
          padding: '8px 12px',
          borderRadius: 'var(--radius-md)',
          fontSize: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <User size={13} color="var(--text-secondary)" />
            <span style={{ color: 'var(--text-secondary)' }}>Asignado a:</span>
            <strong style={{ fontFamily: 'var(--font-mono)' }}>
              {card.assignee_node_uuid || 'Sin asignar'}
            </strong>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ color: 'var(--text-secondary)' }}>Estado:</span>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              style={{
                backgroundColor: 'var(--bg-card)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-sm)',
                padding: '2px 8px',
                fontSize: '12px',
                fontWeight: 600
              }}
            >
              <option value="backlog">Por Hacer (Backlog)</option>
              <option value="in_progress">En Proceso</option>
              <option value="in_review">En Revisión</option>
              <option value="merged">Fusionado / Consolidado</option>
            </select>
          </div>
        </div>

        {/* Título */}
        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, marginBottom: '6px' }}>
            Título de la Tarea
          </label>
          <input
            type="text"
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '13px',
              outline: 'none'
            }}
          />
        </div>

        {/* Rama Git vinculada */}
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
            <label style={{ fontSize: '12px', fontWeight: 600 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                <GitBranch size={13} color="var(--accent-primary)" /> Rama Git Asociada
              </span>
            </label>
            {branchRef && onNavigateToBranch && (
              <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    onNavigateToBranch(branchRef);
                  }}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--accent-primary)',
                    fontSize: '11px',
                    fontWeight: 600,
                    cursor: 'pointer',
                    padding: 0
                  }}
                >
                  Ver en Grafo de Commits →
                </button>
                {(status === 'in_review' || status === 'disputed') && onNavigateToHitl && (
                  <button
                    type="button"
                    onClick={onNavigateToHitl}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      background: 'transparent',
                      border: 'none',
                      color: '#10b981',
                      fontSize: '11px',
                      fontWeight: 600,
                      cursor: 'pointer',
                      padding: 0
                    }}
                  >
                    Inspeccionar en HITL →
                  </button>
                )}
              </div>
            )}
          </div>
          <input
            type="text"
            value={branchRef}
            onChange={(e) => setBranchRef(e.target.value)}
            placeholder="Ej: feature/telemetria-p2p"
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--accent-primary)',
              fontFamily: 'var(--font-mono)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '12px',
              outline: 'none'
            }}
          />
        </div>

        {/* Descripción Técnica */}
        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, marginBottom: '6px' }}>
            Descripción Técnica
          </label>
          <textarea
            rows={3}
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="Especificaciones o detalles técnicos de la tarea..."
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '8px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '12px',
              outline: 'none',
              resize: 'vertical'
            }}
          />
        </div>

        {/* Criterios de Aceptación (Acceptance Criteria) */}
        <div>
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, marginBottom: '6px' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--accent-primary)' }}>
              <CheckSquare size={14} /> Criterios de Aceptación (Acceptance Criteria)
            </span>
          </label>
          <textarea
            rows={4}
            value={criteria}
            onChange={(e) => setCriteria(e.target.value)}
            placeholder="Define las condiciones necesarias para aceptar esta tarea...&#10;- Cumple con las pruebas unitarias.&#10;- No introduce regresiones en la rama principal.&#10;- Aprobado por el revisor del enjambre."
            style={{
              width: '100%',
              backgroundColor: 'var(--bg-app)',
              border: '1px solid var(--border-subtle)',
              color: 'var(--text-primary)',
              padding: '10px 12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
              lineHeight: 1.45,
              outline: 'none',
              resize: 'vertical'
            }}
          />
        </div>

        {/* Botones de Acción */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '10px',
          marginTop: '8px',
          paddingTop: '12px',
          borderTop: '1px solid var(--border-subtle)'
        }}>
          {!isMerged ? (
            <button
              type="button"
              onClick={handleAccept}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                padding: '8px 16px',
                fontSize: '12px',
                fontWeight: 600,
                borderRadius: 'var(--radius-md)',
                border: 'none',
                backgroundColor: 'var(--accent-success)',
                color: '#ffffff',
                cursor: 'pointer'
              }}
            >
              <CheckCircle2 size={14} /> Aceptar y Consolidar (HITL)
            </button>
          ) : (
            <div style={{ fontSize: '12px', color: 'var(--accent-success)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '4px' }}>
              <CheckCircle2 size={14} /> Tarea Aceptada / Consolidada
            </div>
          )}

          <div style={{ display: 'flex', gap: '10px' }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '8px 14px',
                fontSize: '12px',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-subtle)',
                backgroundColor: 'transparent',
                color: 'var(--text-primary)',
                cursor: 'pointer'
              }}
            >
              Cancelar
            </button>
            <button
              type="submit"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                padding: '8px 16px',
                fontSize: '12px',
                fontWeight: 600,
                borderRadius: 'var(--radius-md)',
                border: 'none',
                backgroundColor: 'var(--accent-primary)',
                color: '#ffffff',
                cursor: 'pointer'
              }}
            >
              <Save size={14} /> Guardar Cambios
            </button>
          </div>
        </div>
      </form>
    </ModalOverlay>
  );
}
