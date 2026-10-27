import React, { useState } from 'react';
import { X } from 'lucide-react';

export function ModalOverlay({ isOpen, onClose, title, children }) {
  if (!isOpen) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.55)',
        backdropFilter: 'blur(2px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 200
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          backgroundColor: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-lg)',
          width: '480px',
          maxWidth: '90vw',
          padding: '24px',
          boxShadow: 'var(--shadow-md)',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px'
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ fontSize: '16px', fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>{title}</h3>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}
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

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    onSubmit(name.trim(), codeUrl.trim(), govUrl.trim());
    setName('');
    setCodeUrl('');
    setGovUrl('');
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
  const [cardId, setCardId] = useState(`CARD-${Math.floor(100 + Math.random() * 900)}`);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!title.trim() || !cardId.trim()) return;
    onSubmit(cardId.trim(), title.trim(), desc.trim());
    setTitle('');
    setDesc('');
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
            placeholder="Ej: Implementar sincronización P2P"
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
          <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px' }}>Descripción / Criterios</label>
          <textarea
            rows={3}
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
