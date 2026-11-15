import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Search, X } from 'lucide-react';
import { KanbanBoard } from './KanbanBoard';
import { OKFGovernanceView } from './OKFGovernanceView';
import { CollaboratorsView } from './CollaboratorsView';
import { BranchConsensusView } from './BranchConsensusView';
import { HumanApprovalsView } from './HumanApprovalsView';
import { SwarmTerminalMatrix } from './SwarmTerminalMatrix';
import { CardDetailModal } from './Modals';
import { ViewSliderProvider } from './ViewSlider';
import { KanbanSliderTools } from './KanbanSliderTools';
import { useProjectPulse } from '../hooks/useProjectPulse';
import { useSigningKeyGuard } from '../hooks/useSigningKeyGuard';

function extractCardTags(card) {
  const raw = card?.tags || card?.okf_tags || [];
  if (Array.isArray(raw)) return raw.map(t => String(t).trim()).filter(Boolean);
  if (typeof raw === 'string') return raw.split(',').map(t => t.trim()).filter(Boolean);
  return [];
}

const SEARCH_META_BY_TAB = {
  kanban: {
    placeholder: 'Buscar en Tablero Kanban (título, ID, descripción, tag)...',
    ariaLabel: 'Buscar en Tablero Kanban'
  },
  changes: {
    placeholder: 'Buscar en Control de Cambios (ramas, commits, autor, SHA)...',
    ariaLabel: 'Buscar en Control de Cambios'
  },
  approvals: {
    placeholder: 'Buscar en Aprobaciones HITL (decisiones, juicios, PRs)...',
    ariaLabel: 'Buscar en Aprobaciones HITL'
  },
  okf: {
    placeholder: 'Buscar en Gobernanza OKF (ADRs, skills, carpetas)...',
    ariaLabel: 'Buscar en Gobernanza OKF'
  },
  collaborators: {
    placeholder: 'Buscar en Colaboradores (nombre, @usuario, nodo, rol)...',
    ariaLabel: 'Buscar en Colaboradores'
  },
  terminals: {
    placeholder: 'Buscar en Swarm Matrix (panel, perfil CLI, rol, salida)...',
    ariaLabel: 'Buscar en Swarm Matrix'
  }
};

export function ProjectWorkspace({
  project,
  activeTab = 'kanban',
  onTabChange,
  onBack,
  onNewTask,
  api,
  taskTrigger = 0
}) {
  const [cards, setCards] = useState([]);
  // Gate 0c: si la clave de firma está bloqueada, pide la frase y reintenta la firma una vez.
  const { withKeyUnlock, unlockDialog } = useSigningKeyGuard(api);
  const [selectedCard, setSelectedCard] = useState(null);
  const selectedCardRef = useRef(selectedCard);
  selectedCardRef.current = selectedCard;

  const [targetBranch, setTargetBranch] = useState('');
  const [gitPulseTrigger, setGitPulseTrigger] = useState(0);

  // Barra modular por pestaña
  const normalizedTab = activeTab === 'branches' ? 'changes' : activeTab;
  const [tabSearches, setTabSearches] = useState({});
  const searchQuery = tabSearches[normalizedTab] || '';
  const setSearchQuery = useCallback((val) => {
    setTabSearches(prev => ({ ...prev, [normalizedTab]: val }));
  }, [normalizedTab]);

  const [selectedTag, setSelectedTag] = useState('all');
  const [nodeStatusFilter, setNodeStatusFilter] = useState('all');
  const [branchFilter, setBranchFilter] = useState('all');
  const [tagsIndex, setTagsIndex] = useState({});

  const loadCards = useCallback(async () => {
    if (!api || !project) return;
    try {
      const res = await api.get_kanban_board?.();
      if (res?.success) {
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

  const loadTagsIndex = useCallback(async () => {
    if (!api?.get_okf_tags_index || !project?.project_uuid) return;
    try {
      const res = await api.get_okf_tags_index(project.project_uuid);
      if (res?.success && res.tags && typeof res.tags === 'object') {
        setTagsIndex(res.tags);
      }
    } catch {
      // Silencioso si el adaptador no soporta tags todavía
    }
  }, [api, project]);

  useEffect(() => {
    loadCards();
    loadTagsIndex();
  }, [loadCards, loadTagsIndex, taskTrigger]);

  const bumpGitPulse = useCallback(() => setGitPulseTrigger(prev => prev + 1), []);

  // Hook reactivo de sincronización en tiempo real para eventos de agentes MCP y Git
  useProjectPulse({
    api,
    projectUuid: project?.project_uuid,
    onCardsChange: loadCards,
    onConsensusChange: bumpGitPulse,
    onGitChange: bumpGitPulse,
    intervalMs: 1200
  });

  const availableTags = useMemo(() => {
    const set = new Set(Object.keys(tagsIndex || {}));
    cards.forEach(c => {
      extractCardTags(c).forEach(t => set.add(t));
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [tagsIndex, cards]);

  const availableNodes = useMemo(() => {
    const set = new Set();
    cards.forEach(c => {
      if (c.assignee_node_uuid) set.add(c.assignee_node_uuid);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [cards]);

  const availableBranches = useMemo(() => {
    const set = new Set();
    cards.forEach(c => {
      if (c.branch_ref) set.add(c.branch_ref);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [cards]);

  const filteredCards = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const tagPaths = selectedTag !== 'all' ? (tagsIndex?.[selectedTag] || []) : [];

    return cards.filter(card => {
      const cardTags = extractCardTags(card);
      if (query) {
        const haystack = [
          card.card_id,
          card.title,
          card.description,
          card.branch_ref,
          card.assignee_node_uuid,
          ...cardTags
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        if (!haystack.includes(query)) return false;
      }

      if (selectedTag !== 'all') {
        const hasDirectTag = cardTags.includes(selectedTag);
        const matchesIndexedPath = tagPaths.some(p =>
          (card.okf_path && card.okf_path === p) ||
          (card.description && card.description.includes(p)) ||
          (card.branch_ref && p.includes(card.branch_ref))
        );
        if (!hasDirectTag && !matchesIndexedPath) return false;
      }

      if (nodeStatusFilter !== 'all') {
        if (nodeStatusFilter.startsWith('status:')) {
          const targetStatus = nodeStatusFilter.slice('status:'.length);
          const st = card.status || 'backlog';
          if (targetStatus === 'merged') {
            if (st !== 'merged' && st !== 'consolidated') return false;
          } else if (st !== targetStatus) {
            return false;
          }
        } else if (nodeStatusFilter === 'node:__unassigned__') {
          if (card.assignee_node_uuid) return false;
        } else if (nodeStatusFilter.startsWith('node:')) {
          const targetNode = nodeStatusFilter.slice('node:'.length);
          if (card.assignee_node_uuid !== targetNode) return false;
        }
      }

      if (branchFilter !== 'all' && card.branch_ref !== branchFilter) return false;

      return true;
    });
  }, [cards, searchQuery, selectedTag, nodeStatusFilter, branchFilter, tagsIndex]);

  const hasActiveFilter = Boolean(
    searchQuery.trim() ||
    ((normalizedTab === 'kanban' || normalizedTab === 'okf') && selectedTag !== 'all') ||
    (normalizedTab === 'kanban' && (nodeStatusFilter !== 'all' || branchFilter !== 'all'))
  );

  const searchMeta = SEARCH_META_BY_TAB[normalizedTab] || SEARCH_META_BY_TAB.kanban;

  const handleCardMove = async (cardId, targetStatus) => {
    if (!api) return;
    try {
      if (targetStatus === 'in_progress') {
        const sys = await api.get_system_info?.();
        const node = sys?.active_node || 'node_user';
        const res = await api.claim_task_lease?.(cardId, node, 30);
        if (res && !res.success) {
          alert("Aviso: " + (res.error || "Colisión de lease"));
        }
      } else if (targetStatus === 'merged') {
        // Al arrastrar directamente a Hecho/Merged, fusionar y consolidar con sign_and_approve_item
        if (project && api.sign_and_approve_item) {
          const res = await withKeyUnlock(
            () => api.sign_and_approve_item(project.project_uuid, 'pr_merge', cardId, 'APPROVED')
          );
          if (!res.success && !res.cancelled) {
            alert("No se pudo fusionar la tarea: " + (res.error || 'Fallo desconocido'));
          }
        } else {
          await api.update_task_status?.(cardId, targetStatus);
        }
      } else {
        const res = await api.update_task_status?.(cardId, targetStatus);
        if (res && !res.success) {
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
      const res = await api.update_kanban_card_details?.(
        cardData.card_id,
        cardData.title,
        cardData.description,
        cardData.acceptance_criteria,
        cardData.status,
        cardData.branch_ref,
        cardData.assignee_node_uuid
      );
      if (res?.success) {
        setSelectedCard(null);
        await loadCards();
      } else {
        alert("Error al actualizar tarea: " + (res?.error || 'Error desconocido'));
      }
    } catch (e) {
      alert("Error de comunicación: " + e);
    }
  };

  const handleAcceptCard = async (cardData) => {
    if (!api) return;
    try {
      if (project && api.sign_and_approve_item) {
        const res = await withKeyUnlock(() => api.sign_and_approve_item(
          project.project_uuid,
          'pr_merge',
          cardData.card_id,
          'APPROVED',
          'Aprobación directa desde tarjeta Kanban'
        ));
        if (res.success) {
          setSelectedCard(null);
          await loadCards();
          return;
        }
        if (res.cancelled) return;
        alert("No se pudo aprobar y fusionar la tarea: " + (res.error || 'Fallo desconocido'));
        return;
      }
      const res = await api.update_kanban_card_details?.(
        cardData.card_id,
        cardData.title,
        cardData.description,
        cardData.acceptance_criteria,
        'merged',
        cardData.branch_ref
      );
      if (res?.success) {
        setSelectedCard(null);
        await loadCards();
      } else {
        alert("Error al aprobar tarea: " + (res?.error || 'Error desconocido'));
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
      setSelectedCard({ card_id: cardId, title: `Tarea ${cardId}`, description: '', status: 'in_progress' });
    }
  };

  const handleNavigateToBranch = (branchName) => {
    setTargetBranch(branchName);
    if (onTabChange) {
      onTabChange('changes');
    }
  };

  const renderPrimaryView = () => {
    if (normalizedTab === 'kanban') {
      return (
        <KanbanBoard
          cards={filteredCards}
          onCardMove={handleCardMove}
          onSelectCard={(card) => setSelectedCard(card)}
        />
      );
    }
    if (normalizedTab === 'changes') {
      return (
        <BranchConsensusView
          project={project}
          api={api}
          initialBranch={targetBranch}
          onOpenCard={handleOpenCardById}
          onNavigateToHitl={() => onTabChange && onTabChange('approvals')}
          gitPulseTrigger={gitPulseTrigger}
          searchQuery={searchQuery}
        />
      );
    }
    if (normalizedTab === 'approvals') {
      return (
        <HumanApprovalsView
          project={project}
          api={api}
          onOpenCard={handleOpenCardById}
          onNavigateToBranch={handleNavigateToBranch}
          searchQuery={searchQuery}
        />
      );
    }
    if (normalizedTab === 'okf') {
      return (
        <OKFGovernanceView
          project={project}
          api={api}
          externalTagFilter={selectedTag}
          externalSearchQuery={searchQuery}
        />
      );
    }
    if (normalizedTab === 'terminals') {
      return <SwarmTerminalMatrix api={api} project={project} searchQuery={searchQuery} />;
    }
    return <CollaboratorsView project={project} api={api} searchQuery={searchQuery} />;
  };

  return (
    <ViewSliderProvider viewKey={normalizedTab} suspended={Boolean(selectedCard)}>
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', overflow: 'hidden', minHeight: 0 }}>
      {/* Barra Modular Contextual por Pestaña (38px) */}
      <div className="mcp-toolbar" data-testid="workspace-toolbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 }}>
          {/* Búsqueda contextual del módulo activo */}
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            height: '26px',
            padding: '0 8px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-subtle)',
            minWidth: '160px',
            maxWidth: '290px',
            flex: '1 1 200px'
          }}>
            <Search size={12} color="var(--text-muted)" style={{ flexShrink: 0 }} />
            <input
              type="text"
              data-testid="workspace-search-input"
              aria-label={searchMeta.ariaLabel}
              placeholder={searchMeta.placeholder}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                border: 'none',
                background: 'transparent',
                color: 'var(--text-primary)',
                fontSize: '11.5px',
                outline: 'none',
                width: '100%'
              }}
            />
            {searchQuery && (
              <button
                type="button"
                aria-label="Limpiar búsqueda"
                onClick={() => setSearchQuery('')}
                style={{
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--text-muted)',
                  cursor: 'pointer',
                  display: 'inline-flex',
                  padding: 0
                }}
              >
                <X size={11} />
              </button>
            )}
          </div>

          {hasActiveFilter && (
            <button
              type="button"
              onClick={() => {
                setSearchQuery('');
                if (normalizedTab === 'kanban' || normalizedTab === 'okf') {
                  setSelectedTag('all');
                }
                if (normalizedTab === 'kanban') {
                  setNodeStatusFilter('all');
                  setBranchFilter('all');
                }
              }}
              style={{
                height: '26px',
                padding: '0 8px',
                fontSize: '11px',
                fontWeight: 600,
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-subtle)',
                backgroundColor: 'transparent',
                color: 'var(--text-secondary)',
                cursor: 'pointer'
              }}
            >
              {normalizedTab === 'kanban' ? `Limpiar (${filteredCards.length}/${cards.length})` : 'Limpiar'}
            </button>
          )}
        </div>

      </div>

      {/* Main Workspace View Content (el patrón de Vista Dual/Split fue retirado: el
          contenido secundario de cada vista ahora vive en el slider lateral derecho,
          ver ViewSlider.jsx) */}
      <div style={{ flex: 1, overflow: 'hidden', padding: '14px 18px', minHeight: 0 }}>
        {renderPrimaryView()}
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

      {/* Slider lateral derecho: cada vista registra aqui su contenido secundario
          (ViewSliderContent). El de Kanban vive en este archivo porque comparte los
          filtros con el tablero; el resto lo registra su propia vista. */}
      {normalizedTab === 'kanban' && (
        <KanbanSliderTools
          cards={cards}
          filteredCount={filteredCards.length}
          availableTags={availableTags}
          availableNodes={availableNodes}
          availableBranches={availableBranches}
          selectedTag={selectedTag}
          onTagChange={setSelectedTag}
          nodeStatusFilter={nodeStatusFilter}
          onNodeStatusChange={setNodeStatusFilter}
          branchFilter={branchFilter}
          onBranchChange={setBranchFilter}
          onNewTask={onNewTask}
        />
      )}

      {unlockDialog}
    </div>
    </ViewSliderProvider>
  );
}
