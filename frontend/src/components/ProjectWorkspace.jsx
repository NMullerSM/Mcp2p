import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Search, Columns, X, Tag, Filter, BookOpen, GitBranch, ShieldCheck } from 'lucide-react';
import { KanbanBoard } from './KanbanBoard';
import { OKFGovernanceView } from './OKFGovernanceView';
import { CollaboratorsView } from './CollaboratorsView';
import { BranchConsensusView } from './BranchConsensusView';
import { HumanApprovalsView } from './HumanApprovalsView';
import { CardDetailModal } from './Modals';
import { useProjectPulse } from '../hooks/useProjectPulse';

const ULTRAWIDE_BREAKPOINT = 1440;

function extractCardTags(card) {
  const raw = card?.tags || card?.okf_tags || [];
  if (Array.isArray(raw)) return raw.map(t => String(t).trim()).filter(Boolean);
  if (typeof raw === 'string') return raw.split(',').map(t => t.trim()).filter(Boolean);
  return [];
}

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

  // Barra de filtros y Vista Dual (Hito 4.2)
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTag, setSelectedTag] = useState('all');
  const [nodeStatusFilter, setNodeStatusFilter] = useState('all');
  const [tagsIndex, setTagsIndex] = useState({});
  const [isUltrawide, setIsUltrawide] = useState(() => window.innerWidth > ULTRAWIDE_BREAKPOINT);
  const [isSplitView, setIsSplitView] = useState(false);
  const [splitSecondaryTab, setSplitSecondaryTab] = useState('okf');

  useEffect(() => {
    const handleResize = () => setIsUltrawide(window.innerWidth > ULTRAWIDE_BREAKPOINT);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    if (activeTab === 'okf' || activeTab === 'changes' || activeTab === 'approvals') {
      setSplitSecondaryTab(activeTab);
    }
  }, [activeTab]);

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

      return true;
    });
  }, [cards, searchQuery, selectedTag, nodeStatusFilter, tagsIndex]);

  const hasActiveFilter = Boolean(searchQuery.trim() || selectedTag !== 'all' || nodeStatusFilter !== 'all');

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
          const res = await api.sign_and_approve_item(project.project_uuid, 'pr_merge', cardId, 'APPROVED');
          if (!res.success) {
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
    if (isSplitView) {
      setSplitSecondaryTab('changes');
    } else if (onTabChange) {
      onTabChange('changes');
    }
  };

  const renderSecondaryView = (tabKey) => {
    if (tabKey === 'changes' || tabKey === 'branches') {
      return (
        <BranchConsensusView
          project={project}
          api={api}
          initialBranch={targetBranch}
          onOpenCard={handleOpenCardById}
          onNavigateToHitl={() => (isSplitView ? setSplitSecondaryTab('approvals') : onTabChange?.('approvals'))}
          gitPulseTrigger={gitPulseTrigger}
        />
      );
    }
    if (tabKey === 'approvals') {
      return (
        <HumanApprovalsView
          project={project}
          api={api}
          onOpenCard={handleOpenCardById}
          onNavigateToBranch={handleNavigateToBranch}
        />
      );
    }
    return <OKFGovernanceView project={project} api={api} externalTagFilter={selectedTag} />;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', overflow: 'hidden', minHeight: 0 }}>
      {/* Barra Superior Compacta de Control (38px) */}
      <div className="mcp-toolbar" data-testid="workspace-toolbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 }}>
          {/* Búsqueda de texto */}
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            height: '26px',
            padding: '0 8px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-subtle)',
            minWidth: '150px',
            maxWidth: '250px',
            flex: '1 1 180px'
          }}>
            <Search size={12} color="var(--text-muted)" style={{ flexShrink: 0 }} />
            <input
              type="text"
              data-testid="workspace-search-input"
              aria-label="Buscar tareas, ramas o tags"
              placeholder="Buscar tarea, rama o tag..."
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

          {/* Combobox filtro por Tag OKF */}
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
            <Tag size={12} color="var(--text-muted)" className="mcp-hide-compact" />
            <select
              data-testid="workspace-tag-filter"
              aria-label="Filtrar por Tag OKF"
              className="mcp-combobox"
              value={selectedTag}
              onChange={(e) => setSelectedTag(e.target.value)}
              style={{ height: '26px', fontSize: '11.5px', maxWidth: '170px' }}
            >
              <option value="all">Todos los Tags OKF</option>
              {availableTags.map(tag => (
                <option key={tag} value={tag}>#{tag}</option>
              ))}
            </select>
          </div>

          {/* Combobox filtro por Nodo / Estado */}
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
            <Filter size={12} color="var(--text-muted)" className="mcp-hide-compact" />
            <select
              data-testid="workspace-node-status-filter"
              aria-label="Filtrar por Nodo o Estado"
              className="mcp-combobox"
              value={nodeStatusFilter}
              onChange={(e) => setNodeStatusFilter(e.target.value)}
              style={{ height: '26px', fontSize: '11.5px', maxWidth: '185px' }}
            >
              <option value="all">Todos los Nodos / Estados</option>
              <optgroup label="Estado">
                <option value="status:backlog">Por Hacer</option>
                <option value="status:in_progress">En Proceso</option>
                <option value="status:in_review">En Revisión</option>
                <option value="status:merged">Fusionado / Consolidado</option>
              </optgroup>
              <optgroup label="Nodo Asignado">
                <option value="node:__unassigned__">Sin asignar</option>
                {availableNodes.map(nodeUuid => (
                  <option key={nodeUuid} value={`node:${nodeUuid}`}>
                    {nodeUuid.slice(0, 14)}
                  </option>
                ))}
              </optgroup>
            </select>
          </div>

          {hasActiveFilter && (
            <button
              type="button"
              onClick={() => {
                setSearchQuery('');
                setSelectedTag('all');
                setNodeStatusFilter('all');
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
              Limpiar ({filteredCards.length}/{cards.length})
            </button>
          )}
        </div>

        {/* Botón Toggle Vista Dual (Split) */}
        <button
          type="button"
          data-testid="toggle-split-view"
          aria-pressed={isSplitView}
          onClick={() => setIsSplitView(prev => !prev)}
          title="Acoplar Kanban a la izquierda y Gobernanza / Cambios / HITL a la derecha"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            height: '26px',
            padding: '0 10px',
            fontSize: '11.5px',
            fontWeight: 600,
            borderRadius: 'var(--radius-sm)',
            border: isSplitView
              ? '1px solid var(--accent-primary)'
              : '1px solid var(--border-subtle)',
            backgroundColor: isSplitView
              ? 'rgba(var(--accent-primary-rgb), 0.16)'
              : 'var(--bg-subtle)',
            color: isSplitView ? 'var(--accent-primary)' : 'var(--text-primary)',
            cursor: 'pointer',
            flexShrink: 0,
            transition: 'var(--transition)'
          }}
        >
          <Columns size={13} />
          <span>Vista Dual (Split)</span>
          {isUltrawide && !isSplitView && (
            <span
              data-testid="split-suggested-badge"
              style={{
                fontSize: '9.5px',
                fontFamily: 'var(--font-mono)',
                padding: '1px 5px',
                borderRadius: 'var(--radius-full)',
                backgroundColor: 'rgba(var(--accent-primary-rgb), 0.18)',
                color: 'var(--accent-primary)'
              }}
            >
              Sugerido
            </span>
          )}
        </button>
      </div>

      {/* Main Workspace View Content */}
      <div style={{ flex: 1, overflow: 'hidden', padding: '14px 18px', minHeight: 0 }}>
        {isSplitView && activeTab !== 'collaborators' ? (
          <div className="mcp-split-layout" data-testid="workspace-split-container">
            <div className="mcp-split-pane">
              <KanbanBoard
                cards={filteredCards}
                onCardMove={handleCardMove}
                onSelectCard={(card) => setSelectedCard(card)}
              />
            </div>

            <div
              className="mcp-split-pane"
              style={{
                backgroundColor: 'var(--bg-surface)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-lg)',
                padding: '12px'
              }}
            >
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                marginBottom: '10px',
                paddingBottom: '8px',
                borderBottom: '1px solid var(--border-subtle)',
                flexShrink: 0
              }}>
                {[
                  { id: 'okf', label: 'Gobernanza OKF', icon: BookOpen },
                  { id: 'changes', label: 'Control de Cambios', icon: GitBranch },
                  { id: 'approvals', label: 'HITL', icon: ShieldCheck }
                ].map(pane => {
                  const Icon = pane.icon;
                  const active = splitSecondaryTab === pane.id;
                  return (
                    <button
                      key={pane.id}
                      type="button"
                      data-testid={`split-pane-tab-${pane.id}`}
                      onClick={() => setSplitSecondaryTab(pane.id)}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '5px',
                        padding: '4px 10px',
                        fontSize: '11.5px',
                        fontWeight: active ? 600 : 500,
                        borderRadius: 'var(--radius-sm)',
                        border: active ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                        backgroundColor: active ? 'rgba(var(--accent-primary-rgb), 0.14)' : 'var(--bg-subtle)',
                        color: active ? 'var(--accent-primary)' : 'var(--text-secondary)',
                        cursor: 'pointer'
                      }}
                    >
                      <Icon size={12} />
                      <span>{pane.label}</span>
                    </button>
                  );
                })}
              </div>
              <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
                {renderSecondaryView(splitSecondaryTab)}
              </div>
            </div>
          </div>
        ) : (
          <>
            {activeTab === 'kanban' && (
              <KanbanBoard
                cards={filteredCards}
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
              <OKFGovernanceView project={project} api={api} externalTagFilter={selectedTag} />
            )}
            {activeTab === 'collaborators' && (
              <CollaboratorsView project={project} api={api} />
            )}
          </>
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
