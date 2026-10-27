import React, { useState, useEffect } from 'react';
import { KanbanBoard } from './KanbanBoard';
import { OKFGovernanceView } from './OKFGovernanceView';
import { CollaboratorsView } from './CollaboratorsView';

export function ProjectWorkspace({ project, activeTab = 'kanban', onBack, api }) {
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
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', overflow: 'hidden' }}>
      {/* Main Workspace View Content */}
      <div style={{ flex: 1, overflow: 'hidden', padding: '16px 20px' }}>
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
