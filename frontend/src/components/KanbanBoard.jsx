import React, { useState } from 'react';
import { Lock, User, GitBranch, CheckSquare, Maximize2, Minimize2, Tag } from 'lucide-react';

const COLUMNS = [
  { id: 'backlog', title: 'Por Hacer', dotColor: '#94a3b8' },
  { id: 'in_progress', title: 'En Proceso', dotColor: 'var(--accent-indigo)' },
  { id: 'in_review', title: 'En Revisión', dotColor: '#f59e0b' },
  { id: 'merged', title: 'Fusionado / Consolidado', dotColor: 'var(--accent-success)' }
];

function normalizeCardTags(card) {
  const raw = card?.tags || card?.okf_tags || [];
  if (Array.isArray(raw)) {
    return raw.map(t => String(t).trim()).filter(Boolean);
  }
  if (typeof raw === 'string') {
    return raw.split(',').map(t => t.trim()).filter(Boolean);
  }
  return [];
}

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
    <div className="mcp-kanban-grid">
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

        let flexStyle = '1 1 250px';
        let minWidthStyle = '230px';
        let maxWidthStyle = '400px';
        if (isExpanded) {
          flexStyle = '3 1 500px';
          minWidthStyle = '360px';
          maxWidthStyle = 'none';
        } else if (isAnyExpanded) {
          flexStyle = '0 0 165px';
          minWidthStyle = '145px';
          maxWidthStyle = '185px';
        }

        return (
          <div
            key={col.id}
            data-testid={`kanban-col-${col.id}`}
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
              padding: isAnyExpanded && !isExpanded ? '10px' : '12px',
              transition: 'var(--transition)',
              opacity: isAnyExpanded && !isExpanded ? 0.82 : 1,
              minHeight: 0
            }}
          >
            {/* Column Header */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '10px',
              paddingBottom: '8px',
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
                  fontSize: '12.5px',
                  fontWeight: 600,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis'
                }}>
                  {col.title}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '5px', flexShrink: 0 }}>
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
                  title={isExpanded ? 'Restaurar tamaño normal de columnas' : 'Expandir columna para lectura amplia'}
                  aria-label={isExpanded ? `Restaurar ${col.title}` : `Expandir ${col.title}`}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: isExpanded ? 'rgba(var(--accent-primary-rgb), 0.15)' : 'transparent',
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
              gap: '9px',
              paddingRight: '2px'
            }}>
              {colCards.length === 0 ? (
                <div style={{
                  fontSize: '11px',
                  color: 'var(--text-muted)',
                  textAlign: 'center',
                  padding: '18px 8px',
                  border: '1px dashed var(--border-subtle)',
                  borderRadius: 'var(--radius-md)'
                }}>
                  Sin tareas
                </div>
              ) : (
                colCards.map((card) => {
                  const isDragging = draggedCardId === card.card_id;
                  const hasLease = card.lease_expires_at && card.status === 'in_progress';
                  const tags = normalizeCardTags(card);
                  const visibleTags = tags.slice(0, 2);
                  const extraTagsCount = tags.length - visibleTags.length;

                  return (
                    <div
                      key={card.card_id}
                      data-testid={`kanban-card-${card.card_id}`}
                      draggable
                      onDragStart={(e) => handleDragStart(e, card.card_id)}
                      onDragEnd={handleDragEnd}
                      onClick={() => onSelectCard && onSelectCard(card)}
                      title="Click para ver detalles, modificar o aceptar tarea"
                      style={{
                        backgroundColor: 'var(--bg-surface)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-md)',
                        padding: '11px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '7px',
                        cursor: 'pointer',
                        opacity: isDragging ? 0.35 : 1,
                        transform: isDragging ? 'scale(0.98)' : 'scale(1)',
                        boxShadow: 'var(--shadow-sm)',
                        transition: 'transform 0.15s, opacity 0.15s, border-color 0.15s'
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-primary)'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; }}
                    >
                      {/* Nivel 1 (Ley de Hick): ID + señales críticas (Criterios / Lease) */}
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '6px' }}>
                        <span style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: '10.5px',
                          color: 'var(--text-muted)',
                          fontWeight: 600,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap'
                        }}>
                          {card.card_id}
                        </span>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
                          {card.acceptance_criteria && (
                            <span
                              title="Tiene criterios de aceptación definidos"
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '2px',
                                fontSize: '10px',
                                backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                                color: 'var(--accent-primary)',
                                padding: '1px 5px',
                                borderRadius: '3px'
                              }}
                            >
                              <CheckSquare size={10} />
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
                              padding: '1px 5px',
                              borderRadius: 'var(--radius-sm)'
                            }}>
                              <Lock size={9} /> Lease
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Nivel 2: Título principal */}
                      <div style={{
                        fontSize: '13px',
                        fontWeight: 600,
                        lineHeight: 1.35,
                        color: 'var(--text-primary)',
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden'
                      }}>
                        {card.title}
                      </div>

                      {/* Nivel 3: Descripción breve (máx 2 líneas) */}
                      {card.description && (
                        <div style={{
                          fontSize: '11.5px',
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

                      {/* Nivel 4: Máx 2 Tag pills YAML visibles (+N si hay más) */}
                      {visibleTags.length > 0 && (
                        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '4px' }}>
                          {visibleTags.map(tag => (
                            <span key={tag} className="mcp-tag-pill" title={tag}>
                              <Tag size={9} style={{ marginRight: '3px', flexShrink: 0 }} />
                              {tag}
                            </span>
                          ))}
                          {extraTagsCount > 0 && (
                            <span
                              data-testid={`card-extra-tags-${card.card_id}`}
                              title={tags.slice(2).join(', ')}
                              style={{
                                fontSize: '10px',
                                fontFamily: 'var(--font-mono)',
                                fontWeight: 600,
                                padding: '1px 5px',
                                borderRadius: 'var(--radius-sm)',
                                backgroundColor: 'var(--bg-subtle)',
                                color: 'var(--text-secondary)'
                              }}
                            >
                              +{extraTagsCount}
                            </span>
                          )}
                        </div>
                      )}

                      {/* Nivel 5: Footer limpio */}
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '8px',
                        paddingTop: '6px',
                        borderTop: '1px dashed var(--border-subtle)',
                        fontSize: '10.5px',
                        color: 'var(--text-muted)'
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', minWidth: 0 }}>
                          <User size={11} style={{ flexShrink: 0 }} />
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {card.assignee_node_uuid ? card.assignee_node_uuid.slice(0, 12) : 'Sin asignar'}
                          </span>
                        </div>
                        {card.branch_ref && (
                          <div style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '3px',
                            fontFamily: 'var(--font-mono)',
                            fontSize: '10px',
                            color: 'var(--accent-primary)',
                            maxWidth: '50%',
                            overflow: 'hidden'
                          }}>
                            <GitBranch size={10} style={{ flexShrink: 0 }} />
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {card.branch_ref}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
