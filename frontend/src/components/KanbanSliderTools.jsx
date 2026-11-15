import React from 'react';
import { Plus, Tag, Filter, GitBranch } from 'lucide-react';
import { ViewSliderContent, useViewSlider } from './ViewSlider';

const sectionTitle = {
  fontSize: '12px',
  fontWeight: 700,
  color: 'var(--text-primary)',
  margin: '0 0 8px'
};

const fieldLabel = {
  display: 'flex',
  flexDirection: 'column',
  gap: '4px',
  fontSize: '11.5px',
  color: 'var(--text-secondary)'
};

/**
 * Calcula las metricas del tablero (pura, para poder probarla sin render).
 * "merged" agrupa tambien el estado legado "consolidated", igual que el filtro del tablero.
 */
export function computeBoardMetrics(cards, now = Date.now()) {
  const byStatus = { backlog: 0, in_progress: 0, in_review: 0, merged: 0 };
  let unassigned = 0;
  let activeLeases = 0;
  for (const card of cards) {
    const raw = card.status || 'backlog';
    const st = raw === 'consolidated' ? 'merged' : raw;
    if (st in byStatus) byStatus[st] += 1;
    if (!card.assignee_node_uuid) unassigned += 1;
    if (card.lease_expires_at && st === 'in_progress') {
      const ts = Date.parse(card.lease_expires_at);
      if (Number.isNaN(ts) || ts > now) activeLeases += 1;
    }
  }
  return { total: cards.length, byStatus, unassigned, activeLeases };
}

const METRIC_ROWS = [
  ['backlog', 'Por Hacer'],
  ['in_progress', 'En Proceso'],
  ['in_review', 'En Revision'],
  ['merged', 'Fusionado / Consolidado']
];

/**
 * Herramientas del Tablero Kanban en el slider lateral: crear tarjeta, filtros por tag,
 * nodo/estado y rama, y metricas del tablero. Crear/editar/asignar/lease de UNA tarjeta
 * sigue en el detalle de tarjeta (se abre al elegir una tarjeta del tablero).
 */
export function KanbanSliderTools({
  cards,
  filteredCount,
  availableTags,
  availableNodes,
  availableBranches,
  selectedTag,
  onTagChange,
  nodeStatusFilter,
  onNodeStatusChange,
  branchFilter,
  onBranchChange,
  onNewTask
}) {
  const { close } = useViewSlider();
  const metrics = computeBoardMetrics(cards);

  return (
    <ViewSliderContent
      title="Herramientas del Tablero"
      subtitle="Tarjetas, filtros y metricas del Kanban"
      data-testid="kanban-slider"
    >
      {onNewTask && (
        <section>
          <h4 style={sectionTitle}>Tarjetas</h4>
          <button
            type="button"
            data-testid="kanban-new-task-btn"
            onClick={() => {
              close();
              onNewTask();
            }}
            title="Crear Nueva Tarea Kanban (Ctrl+N)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '7px 14px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: 'pointer'
            }}
          >
            <Plus size={14} /> Nueva Tarea
          </button>
          <p style={{ margin: '8px 0 0', fontSize: '11px', color: 'var(--text-muted)' }}>
            Para editar, asignar o gestionar el lease de una tarjeta, elige la tarjeta en el tablero.
          </p>
        </section>
      )}

      <section>
        <h4 style={sectionTitle}>Filtros</h4>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <label style={fieldLabel}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
              <Tag size={12} /> Tag OKF
            </span>
            <select
              data-testid="workspace-tag-filter"
              aria-label="Filtrar por Tag OKF"
              className="mcp-combobox"
              value={selectedTag}
              onChange={(e) => onTagChange(e.target.value)}
              style={{ height: '30px', fontSize: '12px' }}
            >
              <option value="all">Todos los Tags OKF</option>
              {availableTags.map(tag => (
                <option key={tag} value={tag}>#{tag}</option>
              ))}
            </select>
          </label>

          <label style={fieldLabel}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
              <Filter size={12} /> Nodo o estado
            </span>
            <select
              data-testid="workspace-node-status-filter"
              aria-label="Filtrar por Nodo o Estado"
              className="mcp-combobox"
              value={nodeStatusFilter}
              onChange={(e) => onNodeStatusChange(e.target.value)}
              style={{ height: '30px', fontSize: '12px' }}
            >
              <option value="all">Todos los Nodos / Estados</option>
              <optgroup label="Estado">
                <option value="status:backlog">Por Hacer</option>
                <option value="status:in_progress">En Proceso</option>
                <option value="status:in_review">En Revision</option>
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
          </label>

          <label style={fieldLabel}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
              <GitBranch size={12} /> Rama
            </span>
            <select
              data-testid="workspace-branch-filter"
              aria-label="Filtrar por Rama"
              className="mcp-combobox"
              value={branchFilter}
              onChange={(e) => onBranchChange(e.target.value)}
              style={{ height: '30px', fontSize: '12px' }}
            >
              <option value="all">Todas las ramas</option>
              {availableBranches.map(branch => (
                <option key={branch} value={branch}>{branch}</option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <section data-testid="kanban-board-metrics">
        <h4 style={sectionTitle}>Metricas del tablero</h4>
        <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: '1fr auto', gap: '4px 12px', fontSize: '12px' }}>
          <dt style={{ color: 'var(--text-secondary)' }}>Tarjetas visibles</dt>
          <dd style={{ margin: 0, fontFamily: 'var(--font-mono)' }}>{filteredCount} / {metrics.total}</dd>
          {METRIC_ROWS.map(([key, label]) => (
            <React.Fragment key={key}>
              <dt style={{ color: 'var(--text-secondary)' }}>{label}</dt>
              <dd style={{ margin: 0, fontFamily: 'var(--font-mono)' }}>{metrics.byStatus[key]}</dd>
            </React.Fragment>
          ))}
          <dt style={{ color: 'var(--text-secondary)' }}>Sin asignar</dt>
          <dd style={{ margin: 0, fontFamily: 'var(--font-mono)' }}>{metrics.unassigned}</dd>
          <dt style={{ color: 'var(--text-secondary)' }}>Leases activos</dt>
          <dd style={{ margin: 0, fontFamily: 'var(--font-mono)' }}>{metrics.activeLeases}</dd>
        </dl>
      </section>
    </ViewSliderContent>
  );
}
