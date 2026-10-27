import React, { useState, useEffect } from 'react';
import { KanbanBoard } from './KanbanBoard';
import { OKFGovernanceView } from './OKFGovernanceView';
import { CollaboratorsView } from './CollaboratorsView';
import { BranchConsensusView } from './BranchConsensusView';
import { CardDetailModal } from './Modals';

export function ProjectWorkspace({ project, activeTab = 'kanban', onBack, api, taskTrigger = 0 }) {
  const [cards, setCards] = useState([]);
  const [selectedCard, setSelectedCard] = useState(null);

  const loadCards = async () => {
    if (!api || !project) return;
    try {
      const res = await api.get_kanban_board();
      if (res.success) {
        const fetchedCards = res.cards || [];
        setCards(fetchedCards);
        // Si hay una tarjeta abierta en el modal, actualizar sus datos en vivo
        if (selectedCard) {
          const fresh = fetchedCards.find(c => c.card_id === selectedCard.card_id);
          if (fresh) setSelectedCard(fresh);
        }
      }
    } catch (e) {
      console.error("Error loading cards:", e);
    }
  };

  useEffect(() => {
    loadCards();
  }, [project, api, taskTrigger]);

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

  const handleSaveCard = async (cardData) => {
    if (!api) return;
    try {
      const res = await api.update_kanban_card_details(
        cardData.card_id,
        cardData.title,
        cardData.description,
        cardData.acceptance_criteria,
        cardData.status,
        cardData.branch_ref
      );
      if (res.success) {
        setSelectedCard(null);
        await loadCards();
      } else {
        alert("Error al actualizar tarea: " + res.error);
      }
    } catch (e) {
      alert("Error de comunicación: " + e);
    }
  };

  const handleAcceptCard = async (cardData) => {
    if (!api) return;
    try {
      const res = await api.update_kanban_card_details(
        cardData.card_id,
        cardData.title,
        cardData.description,
        cardData.acceptance_criteria,
        'merged',
        cardData.branch_ref
      );
      if (res.success) {
        setSelectedCard(null);
        await loadCards();
      } else {
        alert("Error al consolidar tarea: " + res.error);
      }
    } catch (e) {
      alert("Error de comunicación: " + e);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', overflow: 'hidden' }}>
      {/* Main Workspace View Content */}
      <div style={{ flex: 1, overflow: 'hidden', padding: '16px 20px' }}>
        {activeTab === 'kanban' && (
          <KanbanBoard
            cards={cards}
            onCardMove={handleCardMove}
            onSelectCard={(card) => setSelectedCard(card)}
          />
        )}
        {(activeTab === 'changes' || activeTab === 'branches') && (
          <BranchConsensusView project={project} api={api} />
        )}
        {activeTab === 'okf' && (
          <OKFGovernanceView project={project} api={api} />
        )}
        {activeTab === 'collaborators' && (
          <CollaboratorsView project={project} api={api} />
        )}
      </div>

      {/* Modal de Detalle de Tarjeta & Criterios de Aceptación (HITL) */}
      <CardDetailModal
        isOpen={Boolean(selectedCard)}
        card={selectedCard}
        onClose={() => setSelectedCard(null)}
        onSave={handleSaveCard}
        onAcceptTask={handleAcceptCard}
      />
    </div>
  );
}
