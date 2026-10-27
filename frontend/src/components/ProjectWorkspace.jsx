import React, { useState, useEffect } from 'react';
import { ArrowLeft, Kanban, BookOpen, Users } from 'lucide-react';
import { KanbanBoard } from './KanbanBoard';
import { OKFGovernanceView } from './OKFGovernanceView';
import { CollaboratorsView } from './CollaboratorsView';

export function ProjectWorkspace({ project, onBack, api }) {
  const [activeTab, setActiveTab] = useState('kanban');
  const [cards, setCards] = useState([]);

  const loadCards = async () => {
    if (!api || !project) return;
    try {
      const res = await api.get_kanban_board();
      if (res.success) {
        setCards(res.cards || []);
      }
    } catch (e) {
      console.error("Error loading cards:", e);
    }
  };

  useEffect(() => {
    loadCards();
  }, [project, api]);

  const handleCardMove = async (cardId, targetStatus) => {
    if (!api) return;
    try {
      if (targetStatus === 'in_progress') {
        const sys = await api.get_system_info();
        const node = sys.active_node || 'node_user';
        const res = await api.claim_task_lease(cardId, node, 30);
        if (!res.success) {
          alert("Aviso: " + (res.error || "Colisión de lease"));
        }
      } else {
        const res = await api.update_task_status(cardId, targetStatus);
        if (!res.success) {
          alert("Error al mover tarea: " + res.error);
        }
      }
      loadCards();
    } catch (e) {
      console.error("Error moving card:", e);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Breadcrumb & Navigation Bar */}
      <div style={{
        backgroundColor: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border-subtle)',
        padding: '10px 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexShrink: 0
      }}>
        {/* Left: Back & Title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <button
            onClick={onBack}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 500,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            <ArrowLeft size={14} /> Todos los Proyectos
          </button>

          <span style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)' }}>
            {project.name}
          </span>
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            backgroundColor: 'var(--bg-subtle)',
            color: 'var(--accent-primary)',
            padding: '2px 8px',
            borderRadius: 'var(--radius-sm)',
            fontWeight: 600
          }}>
            {project.project_uuid}
          </span>
        </div>

        {/* Center: Tabs */}
        <div style={{
          display: 'flex',
          backgroundColor: 'var(--bg-subtle)',
          padding: '3px',
          borderRadius: 'var(--radius-md)',
          gap: '4px'
        }}>
          {[
            { id: 'kanban', label: 'Tablero Kanban', icon: Kanban },
            { id: 'okf', label: 'Gobernanza OKF', icon: BookOpen },
            { id: 'collaborators', label: 'Colaboradores P2P', icon: Users },
          ].map(tab => {
            const Icon = tab.icon;
            const isSelected = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 12px',
                  fontSize: '12px',
                  fontWeight: isSelected ? 600 : 500,
                  borderRadius: 'var(--radius-sm)',
                  border: 'none',
                  backgroundColor: isSelected ? 'var(--bg-surface)' : 'transparent',
                  color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  boxShadow: isSelected ? 'var(--shadow-sm)' : 'none',
                  transition: 'var(--transition)'
                }}
              >
                <Icon size={14} />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Right Info */}
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          Aislamiento Activo
        </div>
      </div>

      {/* Main Workspace View Content */}
      <div style={{ flex: 1, overflow: 'hidden', padding: '18px 24px' }}>
        {activeTab === 'kanban' && (
          <KanbanBoard cards={cards} onCardMove={handleCardMove} />
        )}
        {activeTab === 'okf' && (
          <OKFGovernanceView project={project} api={api} />
        )}
        {activeTab === 'collaborators' && (
          <CollaboratorsView project={project} api={api} />
        )}
      </div>
    </div>
  );
}
