import React, { useState, useEffect, useCallback, useRef } from 'react';
import { KanbanBoard } from './KanbanBoard';
import { OKFGovernanceView } from './OKFGovernanceView';
import { CollaboratorsView } from './CollaboratorsView';
import { BranchConsensusView } from './BranchConsensusView';
import { HumanApprovalsView } from './HumanApprovalsView';
import { CardDetailModal } from './Modals';
import { useProjectPulse } from '../hooks/useProjectPulse';

export function ProjectWorkspace({
  project,
  activeTab = 'kanban',
  onTabChange,
  onBack,
  api,
  taskTrigger = 0
}) {
  const [cards, setCards] = useState([]);
  const [selectedCard, setSelectedCard] = useState(null);
  const selectedCardRef = useRef(selectedCard);
  selectedCardRef.current = selectedCard;

  const [targetBranch, setTargetBranch] = useState('');
  const [gitPulseTrigger, setGitPulseTrigger] = useState(0);

  const loadCards = useCallback(async () => {
    if (!api || !project) return;
    try {
      const res = await api.get_kanban_board();
      if (res.success) {
        const fetchedCards = res.cards || [];
        setCards(fetchedCards);
        // Si hay una tarjeta abierta en el modal, actualizar sus datos en vivo sin revivirla si fue cerrada
        const currentSelected = selectedCardRef.current;
        if (currentSelected) {
          const fresh = fetchedCards.find(c => c.card_id === currentSelected.card_id);
          if (fresh) setSelectedCard(fresh);
        }
      }
    } catch (e) {
      console.error("Error loading cards:", e);
    }
  }, [api, project]);

  useEffect(() => {
    loadCards();
  }, [loadCards, taskTrigger]);

  // Hook reactivo de sincronización en tiempo real para eventos de agentes MCP y Git
  useProjectPulse({
    api,
    projectUuid: project?.project_uuid,
    onCardsChange: loadCards,
    onConsensusChange: () => setGitPulseTrigger(prev => prev + 1),
    onGitChange: () => setGitPulseTrigger(prev => prev + 1),
    intervalMs: 1200
  });

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
      } else if (targetStatus === 'merged') {
        // Al arrastrar directamente a Hecho/Merged, fusionar y consolidar con sign_and_approve_item
        if (project && api.sign_and_approve_item) {
          const res = await api.sign_and_approve_item(project.project_uuid, 'pr_merge', cardId, 'APPROVED');
          if (!res.success) {
            await api.update_task_status(cardId, targetStatus);
          }
        } else {
          await api.update_task_status(cardId, targetStatus);
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
        cardData.branch_ref,
        cardData.assignee_node_uuid
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
      if (project && api.sign_and_approve_item) {
        const res = await api.sign_and_approve_item(
          project.project_uuid,
          'pr_merge',
          cardData.card_id,
          'APPROVED',
          'Aprobación directa desde tarjeta Kanban'
        );
        if (res.success) {
          setSelectedCard(null);
          await loadCards();
          return;
        }
      }
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
        alert("Error al aprobar tarea: " + res.error);
      }
    } catch (e) {
      alert("Error de comunicación: " + e);
    }
  };

  const handleOpenCardById = (cardId) => {
    const found = cards.find(c => c.card_id === cardId);
    if (found) {
      setSelectedCard(found);
    } else {
      // Intentar buscar creando un objeto dummy para abrir o consultar
      setSelectedCard({ card_id: cardId, title: `Tarea ${cardId}`, description: '', status: 'in_progress' });
    }
  };

  const handleNavigateToBranch = (branchName) => {
    setTargetBranch(branchName);
    if (onTabChange) {
      onTabChange('changes');
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
          <BranchConsensusView
            project={project}
            api={api}
            initialBranch={targetBranch}
            onOpenCard={handleOpenCardById}
            onNavigateToHitl={() => onTabChange && onTabChange('approvals')}
            gitPulseTrigger={gitPulseTrigger}
          />
        )}
        {activeTab === 'approvals' && (
          <HumanApprovalsView
            project={project}
            api={api}
            onOpenCard={handleOpenCardById}
            onNavigateToBranch={handleNavigateToBranch}
          />
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
        api={api}
        project={project}
        onClose={() => setSelectedCard(null)}
        onSave={handleSaveCard}
        onAcceptTask={handleAcceptCard}
        onNavigateToBranch={handleNavigateToBranch}
        onNavigateToHitl={() => {
          setSelectedCard(null);
          if (onTabChange) onTabChange('approvals');
        }}
      />
    </div>
  );
}
