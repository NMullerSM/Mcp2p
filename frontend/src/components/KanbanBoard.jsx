import React, { useState } from 'react';
import { Lock, User, GitBranch, CheckSquare, Maximize2, Minimize2 } from 'lucide-react';

const COLUMNS = [
  { id: 'backlog', title: 'Por Hacer', dotColor: '#94a3b8' },
  { id: 'in_progress', title: 'En Proceso', dotColor: '#6366f1' },
  { id: 'in_review', title: 'En Revisión', dotColor: '#f59e0b' },
  { id: 'merged', title: 'Fusionado / Consolidado', dotColor: '#10b981' }
];

export function KanbanBoard({ cards, onCardMove, onSelectCard }) {
  const [draggedCardId, setDraggedCardId] = useState(null);
  const [dragOverCol, setDragOverCol] = useState(null);
  const [expandedCol, setExpandedCol] = useState(null);

  const handleDragStart = (e, cardId) => {
    setDraggedCardId(cardId);
    e.dataTransfer.setData('text/plain', cardId);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragEnd = () => {
    setDraggedCardId(null);
    setDragOverCol(null);
  };

  const handleDragOver = (e, colId) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (dragOverCol !== colId) {
      setDragOverCol(colId);
    }
  };

  const handleDragLeave = (e, colId) => {
    if (e.currentTarget.contains(e.relatedTarget)) return;
    if (dragOverCol === colId) {
      setDragOverCol(null);
    }
  };

  const handleDrop = (e, targetStatus) => {
    e.preventDefault();
    setDragOverCol(null);
    const cardId = e.dataTransfer.getData('text/plain') || draggedCardId;
    if (cardId) {
      onCardMove(cardId, targetStatus);
    }
  };

  return (
    <div style={{
      display: 'flex',
      gap: '16px',
      height: '100%',
      width: '100%',
      overflowX: 'auto',
      padding: '4px 0',
      scrollBehavior: 'smooth'
    }}>
      {COLUMNS.map((col) => {
        const colCards = cards.filter(c => {
          const st = c.status || 'backlog';
          if (col.id === 'merged') {
            return st === 'merged' || st === 'consolidated';
          }
          return st === col.id;
        });

        const isDragOver = dragOverCol === col.id;
        const isExpanded = expandedCol === col.id;
        const isAnyExpanded = Boolean(expandedCol);

        let flexStyle = '1 1 270px';
        let minWidthStyle = '250px';
        let maxWidthStyle = '380px';
        if (isExpanded) {
          flexStyle = '3 1 540px';
          minWidthStyle = '380px';
          maxWidthStyle = 'none';
        } else if (isAnyExpanded) {
          flexStyle = '0 0 170px';
          minWidthStyle = '150px';
          maxWidthStyle = '190px';
        }

        return (
          <div
            key={col.id}
            onDragOver={(e) => handleDragOver(e, col.id)}
            onDragLeave={(e) => handleDragLeave(e, col.id)}
            onDrop={(e) => handleDrop(e, col.id)}
            style={{
              flex: flexStyle,
              minWidth: minWidthStyle,
              maxWidth: maxWidthStyle,
              backgroundColor: isDragOver ? 'var(--bg-subtle)' : 'var(--bg-card)',
              border: isDragOver ? '2px dashed var(--accent-primary)' : '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-lg)',
              display: 'flex',
              flexDirection: 'column',
              padding: isAnyExpanded && !isExpanded ? '10px' : '14px',
              transition: 'var(--transition)',
              opacity: isAnyExpanded && !isExpanded ? 0.8 : 1
            }}
          >
            {/* Column Header */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '12px',
              paddingBottom: '10px',
              borderBottom: '1px solid var(--border-subtle)'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', overflow: 'hidden' }}>
                <span style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  backgroundColor: col.dotColor,
                  flexShrink: 0
                }} />
                <span style={{
                  fontSize: '13px',
                  fontWeight: 600,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis'
                }}>
                  {col.title}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                <span style={{
                  fontSize: '11px',
                  fontFamily: 'var(--font-mono)',
                  backgroundColor: 'var(--bg-subtle)',
                  padding: '2px 7px',
                  borderRadius: 'var(--radius-sm)',
                  fontWeight: 600,
                  color: 'var(--text-muted)'
                }}>
                  {colCards.length}
                </span>
                <button
                  type="button"
                  onClick={() => setExpandedCol(isExpanded ? null : col.id)}
                  title={isExpanded ? "Restaurar tamaño normal de columnas" : "Expandir columna para lectura amplia"}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: isExpanded ? 'rgba(99, 102, 241, 0.15)' : 'transparent',
                    border: 'none',
                    color: isExpanded ? 'var(--accent-primary)' : 'var(--text-muted)',
                    borderRadius: 'var(--radius-sm)',
                    padding: '3px 4px',
                    cursor: 'pointer',
                    transition: 'var(--transition)'
                  }}
                >
                  {isExpanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                </button>
              </div>
            </div>

            {/* Cards List */}
            <div style={{
              flex: 1,
              overflowY: 'auto',
              display: 'flex',
              flexDirection: 'column',
              gap: '10px',
              paddingRight: '2px'
            }}>
              {colCards.map((card) => {
                const isDragging = draggedCardId === card.card_id;
                const hasLease = card.lease_expires_at && card.status === 'in_progress';

                return (
                  <div
                    key={card.card_id}
                    draggable
                    onDragStart={(e) => handleDragStart(e, card.card_id)}
                    onDragEnd={handleDragEnd}
                    onClick={() => onSelectCard && onSelectCard(card)}
                    title="Click para ver detalles, modificar o aceptar tarea"
                    style={{
                      backgroundColor: 'var(--bg-surface)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 'var(--radius-md)',
                      padding: '12px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px',
                      cursor: 'pointer',
                      opacity: isDragging ? 0.35 : 1,
                      transform: isDragging ? 'scale(0.98)' : 'scale(1)',
                      boxShadow: 'var(--shadow-sm)',
                      transition: 'transform 0.15s, opacity 0.15s, border-color 0.15s'
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.borderColor = 'var(--accent-primary)'}
                    onMouseLeave={(e) => e.currentTarget.style.borderColor = 'var(--border-subtle)'}
                  >
                    {/* Card Top */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: '11px',
                        color: 'var(--text-muted)',
                        fontWeight: 600
                      }}>
                        {card.card_id}
                      </span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        {card.acceptance_criteria && (
                          <span
                            title="Tiene criterios de aceptación definidos"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '2px',
                              fontSize: '10px',
                              backgroundColor: 'rgba(99, 102, 241, 0.12)',
                              color: 'var(--accent-primary)',
                              padding: '1px 5px',
                              borderRadius: '3px'
                            }}
                          >
                            <CheckSquare size={10} /> Criterios
                          </span>
                        )}
                        {hasLease && (
                          <span style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '3px',
                            fontSize: '10px',
                            fontFamily: 'var(--font-mono)',
                            backgroundColor: 'var(--badge-lease)',
                            color: 'var(--badge-lease-text)',
                            padding: '2px 6px',
                            borderRadius: 'var(--radius-sm)'
                          }}>
                            <Lock size={10} /> Lease
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Card Title */}
                    <div style={{ fontSize: '13px', fontWeight: 600, lineHeight: 1.35, color: 'var(--text-primary)' }}>
                      {card.title}
                    </div>

                    {/* Card Description */}
                    {card.description && (
                      <div style={{
                        fontSize: '12px',
                        color: 'var(--text-secondary)',
                        lineHeight: 1.35,
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden'
                      }}>
                        {card.description}
                      </div>
                    )}

                    {/* Card Footer */}
                    <div style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      paddingTop: '6px',
                      borderTop: '1px dashed var(--border-subtle)',
                      fontSize: '11px',
                      color: 'var(--text-muted)'
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <User size={12} />
                        <span>{card.assignee_node_uuid ? card.assignee_node_uuid.slice(0, 12) : 'Sin asignar'}</span>
                      </div>
                      {card.branch_ref && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '3px', fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'var(--accent-primary)' }}>
                          <GitBranch size={10} />
                          <span>{card.branch_ref}</span>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
