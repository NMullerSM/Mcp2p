import React, { useState, useEffect, useCallback } from 'react';
import { CheckCircle2, RefreshCw, Eye, GitBranch, ExternalLink, Lock, Sparkles, CheckSquare, X } from 'lucide-react';

export function HumanApprovalsView({ project, api, onOpenCard, onNavigateToBranch, searchQuery = '' }) {
  const [loading, setLoading] = useState(true);
  const [approvals, setApprovals] = useState({
    pending_decisions: [],
    open_disputes: [],
    pending_prs: [],
    history_precedents: [],
    total_count: 0
  });
  const [actionInProgress, setActionInProgress] = useState(null);

  // Modal de Inspección Profunda (Lectura de Mayéutica, Propuestas A/B/C, PRs y Decisiones)
  const [inspectedItem, setInspectedItem] = useState(null); // { type: 'decision' | 'jury_vote' | 'pr_merge', data: {...} }

  // Modal de Doble Factor de Confirmación Consciente (HITL 2FA Cognitivo)
  const [confirmRequest, setConfirmRequest] = useState(null);
  // { itemType, itemId, decision, title, summaryText, badgeLabel, badgeColor }
  const [confirmChecked, setConfirmChecked] = useState(false);
  const [confirmComment, setConfirmComment] = useState('');
  const [historyExpanded, setHistoryExpanded] = useState(false);

  const loadApprovals = useCallback(async () => {
    if (!api || !project) return;
    try {
      setLoading(true);
      const res = await api.get_pending_human_approvals(project.project_uuid);
      if (res.success) {
        setApprovals(res);
      }
    } catch (err) {
      console.error("Error al cargar aprobaciones HITL:", err);
    } finally {
      setLoading(false);
    }
  }, [api, project]);

  useEffect(() => {
    loadApprovals();
  }, [loadApprovals]);

  const openInspector = async (type, itemData, itemId) => {
    setInspectedItem({ type, data: itemData });
    if (api && api.get_hitl_item_detail && project) {
      try {
        const detailRes = await api.get_hitl_item_detail(project.project_uuid, type, itemId);
        if (detailRes && detailRes.success && detailRes.item) {
          setInspectedItem({ type, data: detailRes.item });
        }
      } catch (e) {
        console.debug("Usando datos en caché para inspector HITL:", e);
      }
    }
  };

  // Paso 1: Solicitar confirmación de doble factor antes de ejecutar cualquier firma
  const requestDoubleConfirmation = ({ itemType, itemId, decision, title, summaryText, badgeLabel, badgeColor }) => {
    setConfirmChecked(false);
    setConfirmComment('');
    setConfirmRequest({
      itemType,
      itemId,
      decision,
      title,
      summaryText,
      badgeLabel,
      badgeColor
    });
  };

  // Paso 2: Ejecutar firma Ed25519 tras confirmación explícita del usuario
  const executeConfirmedAction = async () => {
    if (!confirmRequest || !api || !project) return;
    const { itemType, itemId, decision } = confirmRequest;
    const defaultNote =
      itemType === 'jury_vote'
        ? `Voto decisorio con firma criptográfica humana (${decision}).`
        : itemType === 'pr_merge'
        ? decision === 'APPROVED'
          ? 'Revisión aprobada y fusionada mediante doble confirmación HITL.'
          : decision === 'REJECTED'
          ? 'Entrega rechazada mediante revisión HITL.'
          : 'Devuelto a En Proceso tras revisión HITL.'
        : itemType === 'bootstrap_dossier'
        ? 'Ratificación consciente de Dossier de Alineación Inicial OKF v2 con firma Ed25519.'
        : decision === 'APPROVED'
        ? 'Decisión aprobada y promovida a ADR mediante firma Ed25519.'
        : 'Decisión descartada mediante firma Ed25519.';

    try {
      setActionInProgress(itemId);
      const res = await api.sign_and_approve_item(
        project.project_uuid,
        itemType,
        itemId,
        decision,
        confirmComment.trim() || defaultNote
      );
      if (res.success) {
        setConfirmRequest(null);
        setInspectedItem(null);
        await loadApprovals();
      } else {
        alert("Error al procesar firma HITL: " + (res.error || 'Fallo desconocido'));
      }
    } catch (err) {
      alert("Error de comunicación: " + err);
    } finally {
      setActionInProgress(null);
    }
  };

  const normalizedQuery = (searchQuery || '').trim().toLowerCase();
  const matchesQuery = (item) => {
    if (!normalizedQuery) return true;
    return [
      item?.item_id,
      item?.card_id,
      item?.ticket_id,
      item?.id,
      item?.title,
      item?.topic,
      item?.summary,
      item?.description,
      item?.branch_ref,
      item?.branch_name,
      item?.decision,
      item?.status,
      item?.author
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(normalizedQuery);
  };
  const filteredPendingDecisions = (approvals.pending_decisions || []).filter(matchesQuery);
  const filteredOpenDisputes = (approvals.open_disputes || []).filter(matchesQuery);
  const filteredPendingPrs = (approvals.pending_prs || []).filter(matchesQuery);
  const filteredHistoryPrecedents = (approvals.history_precedents || []).filter(matchesQuery);

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      backgroundColor: 'var(--bg-app)',
      gap: '16px',
      overflowY: 'auto',
      paddingBottom: '24px'
    }}>
      {/* Banner Superior HITL (Sin escudo redundante a la izquierda) */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '14px 20px',
        backgroundColor: 'var(--bg-surface)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border-subtle)',
        boxShadow: 'var(--shadow-sm)'
      }}>
        <div>
          <h2 style={{ fontSize: '15px', fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>
            Bandeja HITL — Inspección Dialéctica y Firma Humana
          </h2>
          <p style={{ fontSize: '12px', margin: '3px 0 0', color: 'var(--text-secondary)' }}>
            Haz clic en cualquier tarjeta para desplegar las propuestas completas, el proceso de <strong>Mayéutica de Subagentes</strong> y los criterios de aceptación antes de emitir tu firma.
          </p>
        </div>

        <button
          onClick={loadApprovals}
          disabled={loading}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '7px 14px',
            fontSize: '12px',
            fontWeight: 600,
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-subtle)',
            color: 'var(--text-primary)',
            cursor: loading ? 'wait' : 'pointer'
          }}
        >
          <RefreshCw size={13} className={loading ? "spin" : ""} /> Actualizar
        </button>
      </div>

      {/* Tarjeta Destacada de Alineación Inicial OKF (Bootstrap Ingestion - Modo 1) */}
      {(approvals.has_pending_bootstrap || approvals.alignment_status === 'pending_bootstrap') && (
        <div style={{
          backgroundColor: 'rgba(234, 179, 8, 0.06)',
          border: '1px solid rgba(234, 179, 8, 0.45)',
          borderRadius: 'var(--radius-md)',
          padding: '16px 20px',
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
          boxShadow: 'var(--shadow-sm)'
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{
                fontSize: '10.5px',
                fontFamily: 'var(--font-mono)',
                fontWeight: 700,
                backgroundColor: 'rgba(234, 179, 8, 0.22)',
                color: '#eab308',
                padding: '3px 8px',
                borderRadius: '4px'
              }}>
                MODO 1: BOOTSTRAP PENDIENTE
              </span>
              <span style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)' }}>
                Dossier Unificado de Ingesta y Alineación Inicial OKF v2
              </span>
            </div>
            <span style={{ fontSize: '11px', color: 'var(--text-secondary)', fontWeight: 600 }}>
              Ratificación Requerida
            </span>
          </div>

          <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
            Se ha escaneado el código fuente y la documentación existente del repositorio. Se detectó el stack tecnológico y se extrajeron dinámicamente todas las decisiones arquitectónicas (ADRs), requisitos funcionales/no funcionales y convenciones. Puedes inspeccionar el expediente o contrastarlo con tu agente (ej. Claude Pro vía MCP) antes de ratificar.
          </p>

          <div style={{ display: 'flex', gap: '10px', marginTop: '4px' }}>
            <button
              onClick={() => openInspector('bootstrap_dossier', {
                item_id: 'ALIGNMENT_DOSSIER',
                title: `Dossier de Alineación Inicial OKF v2 — ${project?.name || ''}`,
                full_content: approvals.alignment_dossier_markdown
              }, 'ALIGNMENT_DOSSIER')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '7px 14px',
                fontSize: '12px',
                fontWeight: 600,
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-subtle)',
                backgroundColor: 'var(--bg-surface)',
                color: 'var(--text-primary)',
                cursor: 'pointer'
              }}
            >
              <Eye size={13} /> Inspeccionar Dossier Completo
            </button>

            <button
              onClick={() => requestDoubleConfirmation({
                itemType: 'bootstrap_dossier',
                itemId: 'ALIGNMENT_DOSSIER',
                decision: 'APPROVED',
                title: 'Ratificar Dossier de Alineación Inicial OKF v2',
                summaryText: `Se estampará tu firma sobre el expediente inicial de gobernanza.\n\nEl proyecto pasará formalmente a estado alineado (ALIGNED_OKF) y se inicializará la gobernanza documental sin modificar tu código fuente.`,
                badgeLabel: 'RATIFICAR OKF',
                badgeColor: 'var(--accent-success)'
              })}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '7px 16px',
                fontSize: '12px',
                fontWeight: 700,
                borderRadius: 'var(--radius-sm)',
                border: 'none',
                backgroundColor: 'var(--accent-success)',
                color: '#ffffff',
                cursor: 'pointer'
              }}
            >
              <CheckCircle2 size={13} /> Ratificar Alineación
            </button>
          </div>
        </div>
      )}

      {/* Barra de Estado Alineado OKF (Modo 2: Drift Audit) */}
      {approvals.alignment_status === 'aligned_okf' && !approvals.has_pending_bootstrap && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '9px 16px',
          backgroundColor: 'rgba(var(--accent-primary-rgb), 0.05)',
          border: '1px solid rgba(var(--accent-primary-rgb), 0.25)',
          borderRadius: 'var(--radius-sm)',
          fontSize: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{
              fontSize: '10px',
              fontFamily: 'var(--font-mono)',
              fontWeight: 700,
              backgroundColor: 'rgba(var(--accent-primary-rgb), 0.15)',
              color: 'var(--accent-success)',
              padding: '2px 6px',
              borderRadius: '4px'
            }}>
              ALIGNED_OKF
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>
              Auditoría de deriva continua activa.
            </span>
          </div>
          <button
            onClick={async () => {
              if (!api || !project) return;
              try {
                const driftRes = await api.audit_okf_drift(project.project_uuid);
                if (driftRes.success) {
                  openInspector('drift_audit', {
                    item_id: 'DRIFT_AUDIT',
                    title: `Auditoría de Deriva OKF — ${project.name}`,
                    summary_markdown: driftRes.drift?.summary_markdown || 'Sin deriva detectada.'
                  }, 'DRIFT_AUDIT');
                } else {
                  alert('Error al auditar deriva: ' + (driftRes.error || 'Fallo desconocido'));
                }
              } catch (e) {
                alert('Error de comunicación: ' + e);
              }
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '5px',
              fontSize: '11px',
              fontWeight: 600,
              padding: '5px 12px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            <RefreshCw size={11} /> Auditar Deriva (Drift)
          </button>
        </div>
      )}

      {/* Grid de 3 Columnas Limpias (Sin iconos en las cabeceras, igual que el Tablero Kanban) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(330px, 1fr))', gap: '16px' }}>

        {/* 1. COLUMNA: DECISIONES ARQUITECTÓNICAS */}
        <div style={{
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-subtle)',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '8px', borderBottom: '1px solid var(--border-subtle)' }}>
            <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.2px' }}>
              Decisiones Arquitectónicas ({filteredPendingDecisions.length})
            </span>
            <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
              Decisiones arquitectónicas
            </span>
          </div>

          {filteredPendingDecisions.length === 0 ? (
            <div style={{ padding: '28px 16px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '12px' }}>
              {normalizedQuery ? 'Ninguna decisión coincide con la búsqueda.' : 'No hay decisiones pendientes de firma.'}
            </div>
          ) : (
            filteredPendingDecisions.map(dec => (
              <div
                key={dec.item_id}
                onClick={() => openInspector('decision', dec, dec.item_id)}
                style={{
                  backgroundColor: 'var(--bg-subtle)',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  padding: '13px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '9px',
                  cursor: 'pointer',
                  transition: 'var(--transition)'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                  <span style={{ fontWeight: 700, fontSize: '13px', color: 'var(--text-primary)' }}>
                    {dec.item_id}: {dec.title}
                  </span>
                  <span style={{
                    fontSize: '10px',
                    fontFamily: 'var(--font-mono)',
                    backgroundColor: 'rgba(234, 179, 8, 0.15)',
                    color: '#eab308',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    flexShrink: 0
                  }}>
                    PENDIENTE
                  </span>
                </div>

                <p style={{ fontSize: '11.5px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.45, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  {dec.excerpt}
                </p>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '2px' }}>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      openInspector('decision', dec, dec.item_id);
                    }}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '5px',
                      fontSize: '11px',
                      fontWeight: 600,
                      color: 'var(--accent-primary)',
                      background: 'transparent',
                      border: 'none',
                      padding: 0,
                      cursor: 'pointer'
                    }}
                  >
                    <Eye size={13} /> Leer documento completo
                  </button>
                </div>

                <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }} onClick={(e) => e.stopPropagation()}>
                  <button
                    disabled={actionInProgress === dec.item_id}
                    onClick={() => requestDoubleConfirmation({
                      itemType: 'decision',
                      itemId: dec.item_id,
                      decision: 'APPROVED',
                      title: `Aprobar y Promover a ADR: ${dec.item_id}`,
                      summaryText: `Vas a firmar criptográficamente con Ed25519 la decisión "${dec.title}" y promoverla al Registro de decisiones de arquitectura ADR.`,
                      badgeLabel: 'PROMOVER A ADR',
                      badgeColor: 'var(--accent-primary)'
                    })}
                    style={{
                      flex: 1,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '6px',
                      backgroundColor: 'var(--accent-primary)',
                      color: '#ffffff',
                      border: 'none',
                      padding: '7px',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '11px',
                      fontWeight: 600,
                      cursor: 'pointer'
                    }}
                  >
                    <CheckCircle2 size={13} /> Aprobar y Promover
                  </button>
                  <button
                    disabled={actionInProgress === dec.item_id}
                    onClick={() => requestDoubleConfirmation({
                      itemType: 'decision',
                      itemId: dec.item_id,
                      decision: 'DISCARDED',
                      title: `Descartar Decisión: ${dec.item_id}`,
                      summaryText: `Vas a descartar formalmente la propuesta "${dec.title}" y archivarla en Propuestas descartadas.`,
                      badgeLabel: 'DESCARTAR',
                      badgeColor: 'var(--accent-danger)'
                    })}
                    style={{
                      padding: '7px 10px',
                      backgroundColor: 'transparent',
                      color: 'var(--text-secondary)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '11px',
                      cursor: 'pointer'
                    }}
                  >
                    Descartar
                  </button>
                </div>
              </div>
            ))
          )}
        </div>

        {/* 2. COLUMNA: JUICIOS DE HONOR ABIERTOS (3 Fases: Tesis A, Antítesis B, Síntesis C + Mayéutica) */}
        <div style={{
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-subtle)',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '8px', borderBottom: '1px solid var(--border-subtle)' }}>
            <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.2px' }}>
              Juicios de Honor Abiertos ({filteredOpenDisputes.length})
            </span>
            <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
              status: open
            </span>
          </div>

          {filteredOpenDisputes.length === 0 ? (
            <div style={{ padding: '28px 16px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '12px' }}>
              {normalizedQuery ? 'Ninguna disputa coincide con la búsqueda.' : 'No hay disputas técnicas abiertas.'}
            </div>
          ) : (
            filteredOpenDisputes.map(dispute => (
              <div
                key={dispute.ticket_id}
                onClick={() => openInspector('jury_vote', dispute, dispute.ticket_id)}
                style={{
                  backgroundColor: 'var(--bg-subtle)',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  padding: '13px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '9px',
                  cursor: 'pointer'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                  <span style={{ fontWeight: 700, fontSize: '12.5px', color: 'var(--text-primary)' }}>
                    {dispute.title}
                  </span>
                  <span style={{
                    fontSize: '10px',
                    fontFamily: 'var(--font-mono)',
                    color: '#c084fc',
                    backgroundColor: 'var(--bg-surface)',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    border: '1px solid var(--border-subtle)',
                    flexShrink: 0
                  }}>
                    {dispute.ticket_id}
                  </span>
                </div>

                <p style={{ fontSize: '11.5px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.4, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  <strong>Colisión:</strong> {dispute.collision_reason}
                </p>

                {dispute.is_outdated_matrix && (
                  <div style={{
                    fontSize: '11px',
                    color: '#eab308',
                    backgroundColor: 'rgba(234, 179, 8, 0.1)',
                    border: '1px solid rgba(234, 179, 8, 0.3)',
                    borderRadius: 'var(--radius-sm)',
                    padding: '5px 8px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px'
                  }}>
                    <Clock size={12} />
                    <span>{dispute.matrix_warning || 'Matriz de jurado generada con versión previa del motor (sin directivas de cámara limpia 2.0.0).'}</span>
                  </div>
                )}

                {dispute.escalation_recommendation && (
                  <div style={{
                    fontSize: '11px',
                    color: 'var(--accent-warning)',
                    backgroundColor: 'rgba(245, 158, 11, 0.1)',
                    border: '1px solid rgba(245, 158, 11, 0.3)',
                    borderRadius: 'var(--radius-sm)',
                    padding: '5px 8px'
                  }}>
                    <strong>Recomendación del agente:</strong> {dispute.escalation_recommendation}
                  </div>
                )}

                {/* Las 3 Vías Dialécticas con Voto Ciego (EGM-07) */}
                <details style={{ fontSize: '11px', color: 'var(--text-secondary)' }} onClick={(e) => e.stopPropagation()}>
                  <summary style={{ cursor: 'pointer', userSelect: 'none', color: 'var(--text-muted)', fontWeight: 600 }}>
                    {dispute.has_voted ? 'Ver opciones en disputa y autorías reveladas' : 'Ver opciones anonimizadas en disputa (Voto Ciego)'}
                  </summary>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', fontSize: '11px', backgroundColor: 'var(--bg-surface)', padding: '8px 10px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-subtle)', marginTop: '6px' }}>
                    {dispute.options_view ? (
                      dispute.options_view.map((opt) => (
                        <div key={opt.alias_id} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          <strong style={{ color: 'var(--text-primary)' }}>{opt.alias_label}{opt.canonical_option ? ` (${opt.canonical_option}${opt.author ? ` — ${opt.author}` : ''})` : ''}:</strong> {opt.text || '(Pendiente de formulación)'}
                        </div>
                      ))
                    ) : (
                      <>
                        <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          <strong style={{ color: 'var(--text-primary)' }}>Tesis (A):</strong> {dispute.proposal_a_ref}
                        </div>
                        <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          <strong style={{ color: 'var(--text-primary)' }}>Antítesis (B):</strong> {dispute.proposal_b_ref}
                        </div>
                        <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          <strong style={{ color: 'var(--text-primary)' }}>Síntesis (C):</strong> {dispute.proposal_c_ref || 'Pendiente de síntesis'}
                        </div>
                      </>
                    )}
                  </div>
                </details>

                {/* Enlace a Mayéutica + Tarjeta Kanban asociada */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }} onClick={(e) => e.stopPropagation()}>
                  <button
                    type="button"
                    onClick={() => openInspector('jury_vote', dispute, dispute.ticket_id)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '5px',
                      fontSize: '11px',
                      fontWeight: 600,
                      color: 'var(--accent-primary)',
                      background: 'transparent',
                      border: 'none',
                      padding: 0,
                      cursor: 'pointer'
                    }}
                  >
                    <Eye size={13} /> Inspeccionar mayéutica ({dispute.mayeutic_discussions?.length || 3} subagentes)
                  </button>

                  {dispute.card_id && onOpenCard && (
                    <button
                      type="button"
                      onClick={() => onOpenCard(dispute.card_id)}
                      style={{
                        fontSize: '10.5px',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 600,
                        padding: '2px 6px',
                        borderRadius: '4px',
                        border: '1px solid var(--border-subtle)',
                        backgroundColor: 'var(--bg-surface)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer'
                      }}
                    >
                      {dispute.card_id}
                    </button>
                  )}
                </div>

                {/* Botones de Votación Neutros con Voto Ciego (EGM-07) */}
                {dispute.has_voted ? (
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '6px',
                    padding: '8px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'rgba(var(--accent-primary-rgb), 0.1)',
                    border: '1px solid rgba(var(--accent-primary-rgb), 0.3)',
                    color: 'var(--accent-success)',
                    fontSize: '11px',
                    fontWeight: 600
                  }}>
                    <CheckCircle2 size={13} /> Voto decisorio registrado para este ticket
                  </div>
                ) : (
                  <div style={{ display: 'grid', gridTemplateColumns: dispute.options_view ? `repeat(${dispute.options_view.length}, 1fr)` : '1fr 1fr 1fr', gap: '6px', marginTop: '4px' }} onClick={(e) => e.stopPropagation()}>
                    {dispute.options_view ? (
                      dispute.options_view.map((opt) => (
                        <button
                          key={opt.alias_id}
                          disabled={actionInProgress === dispute.ticket_id || !opt.is_available}
                          onClick={() => requestDoubleConfirmation({
                            itemType: 'jury_vote',
                            itemId: dispute.ticket_id,
                            decision: opt.alias_id,
                            title: `Votar por ${opt.alias_label} en ${dispute.ticket_id}`,
                            summaryText: `${opt.alias_label}: ${opt.text}\n\nTu voto decisorio con firma criptográfica humana resolverá el conflicto sobre el pool de subagentes.`,
                            badgeLabel: `VOTAR ${opt.alias_id}`,
                            badgeColor: 'var(--accent-primary)'
                          })}
                          style={{
                            padding: '7px 4px',
                            fontSize: '11px',
                            fontWeight: 600,
                            backgroundColor: 'var(--bg-surface)',
                            color: 'var(--text-primary)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: 'var(--radius-sm)',
                            cursor: opt.is_available ? 'pointer' : 'not-allowed',
                            opacity: opt.is_available ? 1 : 0.5
                          }}
                        >
                          {opt.alias_label}
                        </button>
                      ))
                    ) : (
                      <>
                        <button
                          disabled={actionInProgress === dispute.ticket_id}
                          onClick={() => requestDoubleConfirmation({
                            itemType: 'jury_vote',
                            itemId: dispute.ticket_id,
                            decision: 'OPTION_A',
                            title: `Votar por Opción Alfa en ${dispute.ticket_id}`,
                            summaryText: `Propuesta A: ${dispute.proposal_a_ref}`,
                            badgeLabel: 'VOTAR ALFA',
                            badgeColor: 'var(--accent-primary)'
                          })}
                          style={{
                            padding: '7px 4px',
                            fontSize: '11px',
                            fontWeight: 600,
                            backgroundColor: 'var(--bg-surface)',
                            color: 'var(--text-primary)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: 'var(--radius-sm)',
                            cursor: 'pointer'
                          }}
                        >
                          Opción Alfa
                        </button>
                        <button
                          disabled={actionInProgress === dispute.ticket_id}
                          onClick={() => requestDoubleConfirmation({
                            itemType: 'jury_vote',
                            itemId: dispute.ticket_id,
                            decision: 'OPTION_B',
                            title: `Votar por Opción Beta en ${dispute.ticket_id}`,
                            summaryText: `Propuesta B: ${dispute.proposal_b_ref}`,
                            badgeLabel: 'VOTAR BETA',
                            badgeColor: 'var(--accent-primary)'
                          })}
                          style={{
                            padding: '7px 4px',
                            fontSize: '11px',
                            fontWeight: 600,
                            backgroundColor: 'var(--bg-surface)',
                            color: 'var(--text-primary)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: 'var(--radius-sm)',
                            cursor: 'pointer'
                          }}
                        >
                          Opción Beta
                        </button>
                        <button
                          disabled={actionInProgress === dispute.ticket_id || !dispute.proposal_c_ref}
                          onClick={() => requestDoubleConfirmation({
                            itemType: 'jury_vote',
                            itemId: dispute.ticket_id,
                            decision: 'THIRD_WAY_C',
                            title: `Votar por Opción Gamma en ${dispute.ticket_id}`,
                            summaryText: `Propuesta C: ${dispute.proposal_c_ref || 'Pendiente de síntesis'}`,
                            badgeLabel: 'VOTAR GAMMA',
                            badgeColor: 'var(--accent-primary)'
                          })}
                          style={{
                            padding: '7px 4px',
                            fontSize: '11px',
                            fontWeight: 600,
                            backgroundColor: 'var(--bg-surface)',
                            color: 'var(--text-primary)',
                            border: '1px solid var(--border-subtle)',
                            borderRadius: 'var(--radius-sm)',
                            cursor: dispute.proposal_c_ref ? 'pointer' : 'not-allowed',
                            opacity: dispute.proposal_c_ref ? 1 : 0.5
                          }}
                        >
                          Opción Gamma
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        {/* 3. COLUMNA: TAREAS EN REVISIÓN / PRS */}
        <div style={{
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-subtle)',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '8px', borderBottom: '1px solid var(--border-subtle)' }}>
            <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.2px' }}>
              Tareas en Revisión / PRs ({filteredPendingPrs.length})
            </span>
            <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
              status: in_review
            </span>
          </div>

          {filteredPendingPrs.length === 0 ? (
            <div style={{ padding: '28px 16px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '12px' }}>
              {normalizedQuery ? 'Ninguna tarea en revisión coincide con la búsqueda.' : 'No hay tareas en revisión esperando merge.'}
            </div>
          ) : (
            filteredPendingPrs.map(pr => (
              <div
                key={pr.card_id}
                onClick={() => openInspector('pr_merge', pr, pr.card_id)}
                style={{
                  backgroundColor: 'var(--bg-subtle)',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  padding: '13px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '9px',
                  cursor: 'pointer'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                  <span style={{ fontWeight: 700, fontSize: '12.5px', color: 'var(--text-primary)' }}>
                    {pr.card_id}: {pr.title}
                  </span>
                  <span style={{
                    fontSize: '10px',
                    fontFamily: 'var(--font-mono)',
                    color: 'var(--accent-primary)',
                    backgroundColor: 'var(--bg-surface)',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    border: '1px solid var(--border-subtle)',
                    flexShrink: 0
                  }}>
                    {pr.branch_ref || 'main'}
                  </span>
                </div>

                {pr.description && (
                  <p style={{ fontSize: '11.5px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.4, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                    {pr.description}
                  </p>
                )}

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }} onClick={(e) => e.stopPropagation()}>
                  <button
                    type="button"
                    onClick={() => openInspector('pr_merge', pr, pr.card_id)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '5px',
                      fontSize: '11px',
                      fontWeight: 600,
                      color: 'var(--accent-primary)',
                      background: 'transparent',
                      border: 'none',
                      padding: 0,
                      cursor: 'pointer'
                    }}
                  >
                    <Eye size={13} /> Inspeccionar entrega y criterios
                  </button>

                  {pr.branch_ref && onNavigateToBranch && (
                    <button
                      type="button"
                      onClick={() => onNavigateToBranch(pr.branch_ref)}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '4px',
                        fontSize: '10.5px',
                        color: 'var(--text-secondary)',
                        background: 'transparent',
                        border: 'none',
                        cursor: 'pointer'
                      }}
                    >
                      <GitBranch size={11} /> Ver rama
                    </button>
                  )}
                </div>

                <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }} onClick={(e) => e.stopPropagation()}>
                  <button
                    disabled={actionInProgress === pr.card_id}
                    onClick={() => requestDoubleConfirmation({
                      itemType: 'pr_merge',
                      itemId: pr.card_id,
                      decision: 'REJECTED',
                      title: `Rechazar Tarea ${pr.card_id}: ${pr.title}`,
                      summaryText: `Rama asociada: ${pr.branch_ref || 'main'}\n\nLa entrega será rechazada formalmente y la tarjeta pasará a estado Rechazado (rejected).`,
                      badgeLabel: 'RECHAZAR ENTREGA',
                      badgeColor: '#ef4444'
                    })}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '4px',
                      backgroundColor: 'transparent',
                      color: 'var(--accent-danger)',
                      border: '1px solid rgba(239, 68, 68, 0.4)',
                      padding: '7px 12px',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '11px',
                      fontWeight: 600,
                      cursor: 'pointer'
                    }}
                  >
                    <X size={12} /> Rechazar
                  </button>
                  <button
                    disabled={actionInProgress === pr.card_id}
                    onClick={() => requestDoubleConfirmation({
                      itemType: 'pr_merge',
                      itemId: pr.card_id,
                      decision: 'APPROVED',
                      title: `Aprobar y Fusionar Tarea ${pr.card_id}: ${pr.title}`,
                      summaryText: `Rama asociada: ${pr.branch_ref || 'main'}\nCriterios verificados: ${pr.acceptance_criteria || 'Estándar OKF'}\n\nSe fusionará la rama y la tarjeta pasará a estado Consolidado (merged) bajo tu firma Ed25519.`,
                      badgeLabel: 'APROBAR Y FUSIONAR',
                      badgeColor: 'var(--accent-success)'
                    })}
                    style={{
                      flex: 1,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '6px',
                      backgroundColor: 'var(--accent-success)',
                      color: '#ffffff',
                      border: 'none',
                      padding: '7px',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '11px',
                      fontWeight: 600,
                      cursor: 'pointer'
                    }}
                  >
                    <CheckCircle2 size={13} /> Aprobar y Fusionar
                  </button>
                </div>
              </div>
            ))
          )}
        </div>

      </div>

      {/* 4. SECCIÓN DE TRAZABILIDAD: PRECEDENTES OKF Y JUICIOS RATIFICADOS */}
      {filteredHistoryPrecedents.length > 0 && (
        <div style={{
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-subtle)',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
          marginTop: '4px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: '8px', borderBottom: '1px solid var(--border-subtle)' }}>
            <div>
              <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.2px' }}>
                Trazabilidad Histórica: Precedentes y Juicios Ratificados ({filteredHistoryPrecedents.length})
              </span>
              <p style={{ margin: '3px 0 0', fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                Registro inmutable de dictámenes y actas de juicio en la gobernanza OKF.
              </p>
            </div>
            <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--accent-primary)', fontWeight: 600 }}>
              0 tokens de contexto
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '12px' }}>
            {(historyExpanded ? filteredHistoryPrecedents : filteredHistoryPrecedents.slice(0, 3)).map((item, idx) => (
              <div
                key={item.id || idx}
                onClick={() => openInspector(item.type === 'JURY_RULING' ? 'ruling' : 'decision', item, item.id)}
                style={{
                  backgroundColor: 'var(--bg-subtle)',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  padding: '12px 14px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                  cursor: 'pointer',
                  transition: 'var(--transition)'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                  <span style={{ fontWeight: 700, fontSize: '12.5px', color: 'var(--text-primary)' }}>
                    {item.title || item.id}
                  </span>
                  <span style={{
                    fontSize: '10px',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 600,
                    padding: '2px 6px',
                    borderRadius: '4px',
                    backgroundColor: item.type === 'JURY_RULING' ? 'rgba(168, 85, 247, 0.15)' : 'rgba(var(--accent-primary-rgb), 0.15)',
                    color: item.type === 'JURY_RULING' ? '#c084fc' : 'var(--accent-success)',
                    flexShrink: 0
                  }}>
                    {item.type === 'JURY_RULING' ? 'JUICIO RATIFICADO' : 'ADR'}
                  </span>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '11px', color: 'var(--text-secondary)' }}>
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{item.id}</span>
                  {item.winning_option && (
                    <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                      Ganador: <strong style={{ color: 'var(--accent-primary)' }}>{item.winning_option}</strong>
                    </span>
                  )}
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '4px' }}>
                  <span style={{ fontSize: '11px', color: 'var(--accent-primary)', display: 'inline-flex', alignItems: 'center', gap: '4px', fontWeight: 600 }}>
                    <Eye size={12} /> Consultar acta y mayéutica
                  </span>
                  {item.card_id && onOpenCard && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpenCard(item.card_id);
                      }}
                      style={{
                        fontSize: '10.5px',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 600,
                        padding: '2px 6px',
                        borderRadius: '4px',
                        border: '1px solid var(--border-subtle)',
                        backgroundColor: 'var(--bg-surface)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer'
                      }}
                    >
                      {item.card_id}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>

          {filteredHistoryPrecedents.length > 3 && (
            <button
              type="button"
              onClick={() => setHistoryExpanded(prev => !prev)}
              style={{
                alignSelf: 'center',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '11.5px',
                fontWeight: 600,
                color: 'var(--accent-primary)',
                background: 'transparent',
                border: 'none',
                padding: '4px',
                cursor: 'pointer'
              }}
            >
              {historyExpanded
                ? 'Ver menos'
                : `Ver ${filteredHistoryPrecedents.length - 3} más`}
            </button>
          )}
        </div>
      )}

      {/* =====================================================================
          MODAL FRONTAL DE INSPECCIÓN PROFUNDA (MAYÉUTICA, PROPUESTAS Y TAREAS)
         ===================================================================== */}
      {inspectedItem && (
        <div
          onClick={() => setInspectedItem(null)}
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.65)',
            backdropFilter: 'blur(3px)',
            zIndex: 100,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '20px'
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: '820px',
              maxHeight: '88vh',
              backgroundColor: 'var(--bg-surface)',
              borderRadius: 'var(--radius-lg)',
              border: '1px solid var(--border-subtle)',
              boxShadow: '0 20px 50px rgba(0,0,0,0.45)',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden'
            }}
          >
            {/* Cabecera del Inspector */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '16px 20px',
              borderBottom: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-subtle)'
            }}>
              <div>
                <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--accent-primary)', textTransform: 'uppercase' }}>
                  {inspectedItem.type === 'jury_vote'
                    ? `Expediente de Juicio Dialéctico · ${inspectedItem.data.ticket_id}`
                    : inspectedItem.type === 'ruling'
                    ? `Acta de Juicio Ratificado · ${inspectedItem.data.ruling_id || inspectedItem.data.ticket_id || inspectedItem.data.id}`
                    : inspectedItem.type === 'pr_merge'
                    ? `Inspección de Tarea en Revisión · ${inspectedItem.data.card_id}`
                    : `Inspección de Decisión Arquitectónica · ${inspectedItem.data.item_id || inspectedItem.data.id}`}
                </span>
                <h3 style={{ margin: '4px 0 0', fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>
                  {inspectedItem.data.title}
                </h3>
              </div>
              <button
                onClick={() => setInspectedItem(null)}
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  backgroundColor: 'var(--bg-surface)',
                  color: 'var(--text-secondary)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer'
                }}
              >
                <X size={16} />
              </button>
            </div>

            {/* Cuerpo Desplegable del Inspector */}
            <div style={{ padding: '20px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '18px' }}>

              {/* VISTA A: JUICIO DE HONOR (3 FASES + VOTO CIEGO + MAYÉUTICA) */}
              {inspectedItem.type === 'jury_vote' && (
                <>
                  <div style={{ backgroundColor: 'var(--bg-subtle)', padding: '12px 14px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)', fontSize: '12.5px', color: 'var(--text-primary)' }}>
                    <strong>Motivo de la Colisión Técnica:</strong> {inspectedItem.data.collision_reason}
                    <div style={{ display: 'flex', gap: '16px', marginTop: '8px', fontSize: '11px', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                      {inspectedItem.data.has_voted ? (
                        <>
                          <span>Contendiente A: {inspectedItem.data.contender_a_node || 'N/A'}</span>
                          <span>Contendiente B: {inspectedItem.data.contender_b_node || 'N/A'}</span>
                        </>
                      ) : (
                        <span>🔒 Voto Ciego Activado: Autorías protegidas contra sesgo hasta emitir tu voto</span>
                      )}
                      {inspectedItem.data.card_id && <span>Tarjeta: {inspectedItem.data.card_id}</span>}
                    </div>
                  </div>

                  {/* FASE 1: LAS PROPUESTAS EN DISPUTA */}
                  <div>
                    <h4 style={{ fontSize: '13px', fontWeight: 700, margin: '0 0 10px', color: 'var(--text-primary)' }}>
                      Fase 1 — {inspectedItem.data.has_voted ? 'Exposición de Propuestas (Autorías Reveladas)' : 'Exposición de Propuestas Anonimizadas (Voto Ciego)'}
                    </h4>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '12px' }}>
                      {inspectedItem.data.options_view ? (
                        inspectedItem.data.options_view.map((opt) => (
                          <div key={opt.alias_id} style={{ padding: '12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-surface)', minWidth: 0, overflow: 'hidden' }}>
                            <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '6px' }}>
                              {opt.alias_label} {opt.canonical_option ? `(${opt.canonical_option}${opt.author ? ` — ${opt.author}` : ''})` : ''}
                            </div>
                            <p style={{ fontSize: '12px', margin: 0, lineHeight: 1.5, color: 'var(--text-primary)', wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
                              {opt.text || '(Pendiente de formulación)'}
                            </p>
                          </div>
                        ))
                      ) : (
                        <>
                          <div style={{ padding: '12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-surface)', minWidth: 0, overflow: 'hidden' }}>
                            <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '6px' }}>
                              OPCIÓN ALFA (TESIS)
                            </div>
                            <p style={{ fontSize: '12px', margin: 0, lineHeight: 1.5, color: 'var(--text-primary)', wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
                              {inspectedItem.data.proposal_a_ref}
                            </p>
                          </div>
                          <div style={{ padding: '12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-surface)', minWidth: 0, overflow: 'hidden' }}>
                            <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '6px' }}>
                              OPCIÓN BETA (ANTÍTESIS)
                            </div>
                            <p style={{ fontSize: '12px', margin: 0, lineHeight: 1.5, color: 'var(--text-primary)', wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
                              {inspectedItem.data.proposal_b_ref}
                            </p>
                          </div>
                          <div style={{ padding: '12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-surface)', minWidth: 0, overflow: 'hidden' }}>
                            <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '6px' }}>
                              OPCIÓN GAMMA (SÍNTESIS)
                            </div>
                            <p style={{ fontSize: '12px', margin: 0, lineHeight: 1.5, color: 'var(--text-primary)', wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
                              {inspectedItem.data.proposal_c_ref || 'Pendiente de síntesis'}
                            </p>
                          </div>
                        </>
                      )}
                    </div>
                  </div>

                  {/* FASE 2: PROCESO DE MAYÉUTICA Y MATRIZ ANTI-SYCOPHANCY */}
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                      <h4 style={{ fontSize: '13px', fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>
                        Fase 2 — Proceso de Mayéutica (Subagentes Neutrales Clean-Room)
                      </h4>
                      <span style={{ fontSize: '11px', color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                        <Lock size={11} /> {inspectedItem.data.has_voted ? 'Veredictos revelados tras tu voto' : 'Veredictos numéricos ocultos antes de tu voto'}
                      </span>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                      {(inspectedItem.data.mayeutic_discussions || []).map((juror, idx) => (
                        <div
                          key={juror.juror_id || idx}
                          style={{
                            padding: '12px 14px',
                            borderRadius: 'var(--radius-md)',
                            backgroundColor: 'var(--bg-subtle)',
                            border: '1px solid var(--border-subtle)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '6px'
                          }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '6px' }}>
                            <span style={{ fontSize: '12px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                              {juror.juror_id} ({juror.role})
                            </span>
                            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                              {juror.temperature !== undefined && (
                                <span style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', padding: '2px 6px', borderRadius: '4px', backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', color: 'var(--accent-primary)' }}>
                                  Temp: {juror.temperature} ({juror.temperature_profile || 'strict'})
                                </span>
                              )}
                              {(juror.focus_labels && juror.focus_labels.length > 0 ? juror.focus_labels : (juror.focus_vectors || ['Evaluación Holística'])).map(fv => (
                                <span key={fv} style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', padding: '2px 6px', borderRadius: '4px', backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}>
                                  {fv}
                                </span>
                              ))}
                            </div>
                          </div>
                          <p style={{ fontSize: '12px', margin: 0, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                            {juror.mayeutic_argument}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* FASE 3: VOTACIÓN HUMANA PONDERADA */}
                  <div style={{
                    padding: '14px',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: 'var(--bg-subtle)',
                    border: '1px solid var(--border-subtle)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '10px'
                  }}>
                    <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
                      Fase 3 — Emisión de Voto Humano Decisorio con Firma Criptográfica
                    </div>
                    {inspectedItem.data.has_voted ? (
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '6px',
                        padding: '10px',
                        borderRadius: 'var(--radius-sm)',
                        backgroundColor: 'rgba(var(--accent-primary-rgb), 0.1)',
                        border: '1px solid rgba(var(--accent-primary-rgb), 0.3)',
                        color: 'var(--accent-success)',
                        fontSize: '12px',
                        fontWeight: 600
                      }}>
                        <CheckCircle2 size={15} /> Tu voto decisorio ya fue emitido y firmado para este juicio
                      </div>
                    ) : (
                      <div style={{ display: 'grid', gridTemplateColumns: inspectedItem.data.options_view ? `repeat(${inspectedItem.data.options_view.length}, 1fr)` : '1fr 1fr 1fr', gap: '10px' }}>
                        {inspectedItem.data.options_view ? (
                          inspectedItem.data.options_view.map((opt) => (
                            <button
                              key={opt.alias_id}
                              disabled={!opt.is_available}
                              onClick={() => requestDoubleConfirmation({
                                itemType: 'jury_vote',
                                itemId: inspectedItem.data.ticket_id,
                                decision: opt.alias_id,
                                title: `Votar por ${opt.alias_label} en ${inspectedItem.data.ticket_id}`,
                                summaryText: `${opt.alias_label}: ${opt.text}\n\nTu voto decisorio con firma criptográfica humana resolverá el conflicto sobre el pool de subagentes.`,
                                badgeLabel: `VOTAR ${opt.alias_id}`,
                                badgeColor: 'var(--accent-primary)'
                              })}
                              style={{
                                padding: '9px',
                                fontSize: '12px',
                                fontWeight: 700,
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-subtle)',
                                backgroundColor: 'var(--bg-surface)',
                                color: 'var(--text-primary)',
                                cursor: opt.is_available ? 'pointer' : 'not-allowed',
                                opacity: opt.is_available ? 1 : 0.5
                              }}
                            >
                              Votar {opt.alias_label}
                            </button>
                          ))
                        ) : (
                          <>
                            <button
                              onClick={() => requestDoubleConfirmation({
                                itemType: 'jury_vote',
                                itemId: inspectedItem.data.ticket_id,
                                decision: 'OPTION_A',
                                title: `Votar por Opción Alfa en ${inspectedItem.data.ticket_id}`,
                                summaryText: `Propuesta A: ${inspectedItem.data.proposal_a_ref}`,
                                badgeLabel: 'VOTAR ALFA',
                                badgeColor: 'var(--accent-primary)'
                              })}
                              style={{
                                padding: '9px',
                                fontSize: '12px',
                                fontWeight: 700,
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-subtle)',
                                backgroundColor: 'var(--bg-surface)',
                                color: 'var(--text-primary)',
                                cursor: 'pointer'
                              }}
                            >
                              Votar Opción Alfa
                            </button>
                            <button
                              onClick={() => requestDoubleConfirmation({
                                itemType: 'jury_vote',
                                itemId: inspectedItem.data.ticket_id,
                                decision: 'OPTION_B',
                                title: `Votar por Opción Beta en ${inspectedItem.data.ticket_id}`,
                                summaryText: `Propuesta B: ${inspectedItem.data.proposal_b_ref}`,
                                badgeLabel: 'VOTAR BETA',
                                badgeColor: 'var(--accent-primary)'
                              })}
                              style={{
                                padding: '9px',
                                fontSize: '12px',
                                fontWeight: 700,
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-subtle)',
                                backgroundColor: 'var(--bg-surface)',
                                color: 'var(--text-primary)',
                                cursor: 'pointer'
                              }}
                            >
                              Votar Opción Beta
                            </button>
                            <button
                              disabled={!inspectedItem.data.proposal_c_ref}
                              onClick={() => requestDoubleConfirmation({
                                itemType: 'jury_vote',
                                itemId: inspectedItem.data.ticket_id,
                                decision: 'THIRD_WAY_C',
                                title: `Votar por Opción Gamma en ${inspectedItem.data.ticket_id}`,
                                summaryText: `Propuesta C: ${inspectedItem.data.proposal_c_ref || 'Pendiente de síntesis'}`,
                                badgeLabel: 'VOTAR GAMMA',
                                badgeColor: 'var(--accent-primary)'
                              })}
                              style={{
                                padding: '9px',
                                fontSize: '12px',
                                fontWeight: 700,
                                borderRadius: 'var(--radius-sm)',
                                border: '1px solid var(--border-subtle)',
                                backgroundColor: 'var(--bg-surface)',
                                color: 'var(--text-primary)',
                                cursor: inspectedItem.data.proposal_c_ref ? 'pointer' : 'not-allowed',
                                opacity: inspectedItem.data.proposal_c_ref ? 1 : 0.5
                              }}
                            >
                              Votar Opción Gamma
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* VISTA B: TAREA EN REVISIÓN / PR (ej. TASK-102) */}
              {inspectedItem.type === 'pr_merge' && (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px', backgroundColor: 'var(--bg-subtle)', padding: '12px 14px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
                    <div style={{ fontSize: '12px' }}>
                      <span style={{ color: 'var(--text-secondary)' }}>Agente / Desarrollador Asignado: </span>
                      <strong style={{ fontFamily: 'var(--font-mono)' }}>{inspectedItem.data.assignee_node_uuid || 'Nodo Local'}</strong>
                    </div>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      <span style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', color: 'var(--accent-primary)' }}>
                        Rama: {inspectedItem.data.branch_ref || 'main'}
                      </span>
                      {inspectedItem.data.branch_ref && onNavigateToBranch && (
                        <button
                          onClick={() => {
                            setInspectedItem(null);
                            onNavigateToBranch(inspectedItem.data.branch_ref);
                          }}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '4px',
                            fontSize: '11px',
                            fontWeight: 600,
                            padding: '4px 8px',
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-subtle)',
                            backgroundColor: 'var(--bg-surface)',
                            color: 'var(--accent-primary)',
                            cursor: 'pointer'
                          }}
                        >
                          <GitBranch size={12} /> Abrir en Control de Cambios
                        </button>
                      )}
                      {onOpenCard && (
                        <button
                          onClick={() => {
                            const cId = inspectedItem.data.card_id;
                            setInspectedItem(null);
                            onOpenCard(cId);
                          }}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '4px',
                            fontSize: '11px',
                            fontWeight: 600,
                            padding: '4px 8px',
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-subtle)',
                            backgroundColor: 'var(--bg-surface)',
                            color: 'var(--text-primary)',
                            cursor: 'pointer'
                          }}
                        >
                          <ExternalLink size={12} /> Editar Tarjeta Kanban
                        </button>
                      )}
                    </div>
                  </div>

                  <div>
                    <h4 style={{ fontSize: '13px', fontWeight: 700, margin: '0 0 6px', color: 'var(--text-primary)' }}>
                      Descripción Técnica de la Tarea
                    </h4>
                    <div style={{ padding: '12px', borderRadius: 'var(--radius-md)', backgroundColor: 'var(--bg-subtle)', border: '1px solid var(--border-subtle)', fontSize: '12.5px', lineHeight: 1.55, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                      {inspectedItem.data.description || 'Sin descripción adicional registrada.'}
                    </div>
                  </div>

                  <div>
                    <h4 style={{ fontSize: '13px', fontWeight: 700, margin: '0 0 6px', color: 'var(--accent-primary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <CheckSquare size={14} /> Criterios de Aceptación (Acceptance Criteria)
                    </h4>
                    <div style={{ padding: '12px', borderRadius: 'var(--radius-md)', backgroundColor: 'var(--bg-subtle)', border: '1px solid var(--border-subtle)', fontSize: '12px', fontFamily: 'var(--font-mono)', lineHeight: 1.55, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                      {inspectedItem.data.acceptance_criteria || '- Pruebas unitarias e integración verificadas.\n- Sin conflictos con la rama principal.\n- Cumple lineamientos de arquitectura OKF.'}
                    </div>
                  </div>

                  {inspectedItem.data.commits && inspectedItem.data.commits.length > 0 && (
                    <div>
                      <h4 style={{ fontSize: '13px', fontWeight: 700, margin: '0 0 6px', color: 'var(--text-primary)' }}>
                        Commits Recientes Asociados ({inspectedItem.data.commits.length})
                      </h4>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        {inspectedItem.data.commits.map(cm => (
                          <div key={cm.hash} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 12px', borderRadius: 'var(--radius-sm)', backgroundColor: 'var(--bg-subtle)', border: '1px solid var(--border-subtle)', fontSize: '11.5px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--accent-primary)' }}>
                                {cm.short_hash || cm.hash?.slice(0, 7)}
                              </span>
                              <span style={{ color: 'var(--text-primary)' }}>{cm.message}</span>
                            </div>
                            <span style={{ color: 'var(--text-secondary)', fontSize: '11px' }}>{cm.author}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', paddingTop: '10px', borderTop: '1px solid var(--border-subtle)' }}>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
                      <button
                        type="button"
                        onClick={() => requestDoubleConfirmation({
                          itemType: 'pr_merge',
                          itemId: inspectedItem.data.card_id,
                          decision: 'REJECTED',
                          title: `Rechazar Entrega ${inspectedItem.data.card_id}: ${inspectedItem.data.title}`,
                          summaryText: `Rama asociada: ${inspectedItem.data.branch_ref || 'main'}\n\nLa entrega será rechazada formalmente y la tarjeta pasará a estado Rechazado (rejected).`,
                          badgeLabel: 'RECHAZAR ENTREGA',
                          badgeColor: '#ef4444'
                        })}
                        style={{
                          padding: '8px 16px',
                          fontSize: '12px',
                          fontWeight: 600,
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid rgba(239, 68, 68, 0.4)',
                          backgroundColor: 'rgba(239, 68, 68, 0.1)',
                          color: '#ef4444',
                          cursor: 'pointer'
                        }}
                      >
                        Rechazar Entrega
                      </button>
                      <button
                        type="button"
                        onClick={() => requestDoubleConfirmation({
                          itemType: 'pr_merge',
                          itemId: inspectedItem.data.card_id,
                          decision: 'APPROVED',
                          title: `Aprobar y Fusionar ${inspectedItem.data.card_id}: ${inspectedItem.data.title}`,
                          summaryText: `Rama asociada: ${inspectedItem.data.branch_ref || 'main'}\n\nSe fusionará la entrega y la tarjeta pasará a estado Consolidado (merged) con tu firma Ed25519.`,
                          badgeLabel: 'APROBAR Y FUSIONAR',
                          badgeColor: 'var(--accent-success)'
                        })}
                        style={{
                          padding: '8px 18px',
                          fontSize: '12px',
                          fontWeight: 700,
                          borderRadius: 'var(--radius-sm)',
                          border: 'none',
                          backgroundColor: 'var(--accent-success)',
                          color: '#ffffff',
                          cursor: 'pointer'
                        }}
                      >
                        Aprobar y Fusionar Entrega
                      </button>
                    </div>

                    <details style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                      <summary style={{ cursor: 'pointer', userSelect: 'none', color: 'var(--text-muted)' }}>
                        Opciones avanzadas
                      </summary>
                      <div style={{ marginTop: '8px', display: 'flex', justifyContent: 'flex-end' }}>
                        <button
                          type="button"
                          onClick={() => requestDoubleConfirmation({
                            itemType: 'pr_merge',
                            itemId: inspectedItem.data.card_id,
                            decision: 'DISCARDED',
                            title: `Devolver ${inspectedItem.data.card_id} a En Proceso`,
                            summaryText: `La tarea "${inspectedItem.data.title}" volverá a la columna En Proceso (in_progress) para iteración adicional.`,
                            badgeLabel: 'SOLICITAR CAMBIOS',
                            badgeColor: 'var(--accent-warning)'
                          })}
                          style={{
                            padding: '6px 12px',
                            fontSize: '11px',
                            fontWeight: 500,
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-subtle)',
                            backgroundColor: 'transparent',
                            color: 'var(--text-secondary)',
                            cursor: 'pointer'
                          }}
                        >
                          Devolver a En Proceso (Solicitar cambios)
                        </button>
                      </div>
                    </details>
                  </div>
                </>
              )}

              {/* VISTA D: ACTA DE JUICIO RATIFICADO / PRECEDENTE OKF */}
              {inspectedItem.type === 'ruling' && (
                <>
                  <div style={{ backgroundColor: 'var(--bg-subtle)', padding: '12px 14px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)', fontSize: '12.5px', color: 'var(--text-primary)' }}>
                    <div><strong>Título:</strong> {inspectedItem.data.title}</div>
                    <div style={{ marginTop: '4px' }}>
                      <strong>Estado:</strong> <span style={{ color: 'var(--accent-success)', fontWeight: 600 }}>{inspectedItem.data.status}</span>
                      {inspectedItem.data.resolution_mode && <span style={{ marginLeft: '12px', color: 'var(--text-secondary)' }}>Modo: {inspectedItem.data.resolution_mode}</span>}
                    </div>
                  </div>

                  {inspectedItem.data.phases?.phase_1_positions && (
                    <div>
                      <h4 style={{ fontSize: '13px', fontWeight: 700, margin: '0 0 8px', color: 'var(--text-primary)' }}>
                        Posturas y Síntesis Registradas
                      </h4>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '10px' }}>
                        <div style={{ padding: '10px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-subtle)' }}>
                          <strong style={{ fontSize: '11px', color: 'var(--accent-primary)' }}>Tesis (Opción A):</strong>
                          <p style={{ fontSize: '11.5px', margin: '4px 0 0', color: 'var(--text-secondary)' }}>
                            {inspectedItem.data.phases.phase_1_positions.thesis_option_a?.proposal_ref}
                          </p>
                        </div>
                        <div style={{ padding: '10px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-subtle)' }}>
                          <strong style={{ fontSize: '11px', color: '#c084fc' }}>Antítesis (Opción B):</strong>
                          <p style={{ fontSize: '11.5px', margin: '4px 0 0', color: 'var(--text-secondary)' }}>
                            {inspectedItem.data.phases.phase_1_positions.antithesis_option_b?.proposal_ref}
                          </p>
                        </div>
                        <div style={{ padding: '10px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-subtle)' }}>
                          <strong style={{ fontSize: '11px', color: 'var(--accent-success)' }}>Síntesis (Opción C):</strong>
                          <p style={{ fontSize: '11.5px', margin: '4px 0 0', color: 'var(--text-secondary)' }}>
                            {inspectedItem.data.phases.phase_2_synthesis?.summary}
                          </p>
                        </div>
                      </div>
                    </div>
                  )}

                  {inspectedItem.data.phases?.phase_3_voting && (
                    <div style={{ padding: '12px', borderRadius: 'var(--radius-md)', backgroundColor: 'var(--bg-subtle)', border: '1px solid var(--border-subtle)' }}>
                      <h4 style={{ fontSize: '13px', fontWeight: 700, margin: '0 0 6px', color: 'var(--text-primary)' }}>
                        Resultado de Votación y Dictamen Final
                      </h4>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <div>Opción Ganadora: <strong style={{ color: 'var(--accent-primary)' }}>{inspectedItem.data.phases.phase_3_voting.winning_option}</strong></div>
                        <div>Consenso Alcanzado: <strong style={{ color: 'var(--accent-success)' }}>{inspectedItem.data.phases.phase_3_voting.consensus_reached ? 'Sí' : 'No'}</strong> (Ratio: {inspectedItem.data.phases.phase_3_voting.consensus_ratio})</div>
                        <div>Humanos Elegibles: {inspectedItem.data.phases.phase_3_voting.eligible_humans} | Jurados Subagentes: {inspectedItem.data.phases.phase_3_voting.agent_jurors_count}</div>
                      </div>
                    </div>
                  )}

                  {inspectedItem.data.phases?.phase_2_neutral_matrix && inspectedItem.data.phases.phase_2_neutral_matrix.length > 0 && (
                    <div>
                      <h4 style={{ fontSize: '13px', fontWeight: 700, margin: '0 0 8px', color: 'var(--text-primary)' }}>
                        Jurados Neutrales y Focos Asignados
                      </h4>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {inspectedItem.data.phases.phase_2_neutral_matrix.map((j, idx) => (
                          <div key={idx} style={{ padding: '8px 12px', borderRadius: 'var(--radius-sm)', backgroundColor: 'var(--bg-subtle)', border: '1px solid var(--border-subtle)', fontSize: '11.5px' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)' }}>
                              <strong>{j.juror_id}</strong>
                              <span>T={j.temperature} · {j.focus_labels?.join(', ')}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* VISTA C: DECISIÓN ARQUITECTÓNICA COMPLETA */}
              {inspectedItem.type === 'decision' && (
                <>
                  <div style={{
                    padding: '16px',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: 'var(--bg-subtle)',
                    border: '1px solid var(--border-subtle)',
                    fontSize: '12.5px',
                    fontFamily: 'var(--font-mono)',
                    lineHeight: 1.6,
                    color: 'var(--text-primary)',
                    whiteSpace: 'pre-wrap'
                  }}>
                    {inspectedItem.data.full_content || inspectedItem.data.excerpt}
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', paddingTop: '10px', borderTop: '1px solid var(--border-subtle)' }}>
                    <button
                      onClick={() => requestDoubleConfirmation({
                        itemType: 'decision',
                        itemId: inspectedItem.data.item_id,
                        decision: 'DISCARDED',
                        title: `Descartar Decisión ${inspectedItem.data.item_id}`,
                        summaryText: `Se moverá "${inspectedItem.data.title}" a Propuestas descartadas con tu firma de rechazo.`,
                        badgeLabel: 'DESCARTAR',
                        badgeColor: 'var(--accent-danger)'
                      })}
                      style={{
                        padding: '8px 14px',
                        fontSize: '12px',
                        fontWeight: 600,
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-subtle)',
                        backgroundColor: 'transparent',
                        color: 'var(--text-secondary)',
                        cursor: 'pointer'
                      }}
                    >
                      Descartar Decisión
                    </button>
                    <button
                      onClick={() => requestDoubleConfirmation({
                        itemType: 'decision',
                        itemId: inspectedItem.data.item_id,
                        decision: 'APPROVED',
                        title: `Aprobar y Promover a ADR: ${inspectedItem.data.item_id}`,
                        summaryText: `Se promoverá "${inspectedItem.data.title}" al Registro de decisiones de arquitectura ADR firmado con tu llave Ed25519.`,
                        badgeLabel: 'PROMOVER A ADR',
                        badgeColor: 'var(--accent-primary)'
                      })}
                      style={{
                        padding: '8px 18px',
                        fontSize: '12px',
                        fontWeight: 700,
                        borderRadius: 'var(--radius-sm)',
                        border: 'none',
                        backgroundColor: 'var(--accent-primary)',
                        color: '#ffffff',
                        cursor: 'pointer'
                      }}
                    >
                      Aprobar y Promover a ADR
                    </button>
                  </div>
                </>
              )}

              {/* VISTA E: DOSSIER DE ALINEACIÓN INICIAL OKF (BOOTSTRAP) O AUDITORÍA DE DERIVA (DRIFT) */}
              {(inspectedItem.type === 'bootstrap_dossier' || inspectedItem.type === 'drift_audit') && (
                <>
                  <div style={{
                    padding: '16px 18px',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: 'var(--bg-subtle)',
                    border: '1px solid var(--border-subtle)',
                    fontSize: '12.5px',
                    lineHeight: 1.65,
                    color: 'var(--text-primary)',
                    whiteSpace: 'pre-wrap',
                    maxHeight: '62vh',
                    overflowY: 'auto'
                  }}>
                    {inspectedItem.data.full_content || inspectedItem.data.summary_markdown || inspectedItem.data.excerpt || 'Sin contenido registrado.'}
                  </div>

                  {inspectedItem.type === 'bootstrap_dossier' && (
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: '12px', borderTop: '1px solid var(--border-subtle)' }}>
                      <span style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                        Al ratificar, se materializarán todas las ADRs, requisitos y se poblará el tablero Kanban.
                      </span>
                      <button
                        onClick={() => requestDoubleConfirmation({
                          itemType: 'bootstrap_dossier',
                          itemId: 'ALIGNMENT_DOSSIER',
                          decision: 'APPROVED',
                          title: 'Ratificar Dossier de Alineación Inicial OKF v2',
                          summaryText: `Se estampará tu firma sobre el expediente inicial de gobernanza.\n\nEl proyecto pasará formalmente a estado alineado (ALIGNED_OKF) y se inicializará la gobernanza documental sin modificar tu código fuente.`,
                          badgeLabel: 'RATIFICAR OKF',
                          badgeColor: 'var(--accent-success)'
                        })}
                        style={{
                          padding: '9px 20px',
                          fontSize: '12.5px',
                          fontWeight: 700,
                          borderRadius: 'var(--radius-sm)',
                          border: 'none',
                          backgroundColor: 'var(--accent-success)',
                          color: '#ffffff',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px'
                        }}
                      >
                        <CheckCircle2 size={14} /> Ratificar Alineación
                      </button>
                    </div>
                  )}
                </>
              )}

            </div>
          </div>
        </div>
      )}

      {/* =====================================================================
          MODAL DE DOBLE FACTOR DE CONFIRMACIÓN CONSCIENTE (2FA COGNITIVO HITL)
         ===================================================================== */}
      {confirmRequest && (
        <div
          onClick={() => setConfirmRequest(null)}
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.72)',
            backdropFilter: 'blur(4px)',
            zIndex: 120,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '20px'
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: '480px',
              backgroundColor: 'var(--bg-surface)',
              borderRadius: 'var(--radius-lg)',
              border: '1px solid var(--border-subtle)',
              boxShadow: '0 24px 60px rgba(0,0,0,0.55)',
              padding: '22px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{
                fontSize: '10.5px',
                fontFamily: 'var(--font-mono)',
                fontWeight: 700,
                padding: '3px 8px',
                borderRadius: '4px',
                backgroundColor: confirmRequest.badgeColor || 'var(--accent-primary)',
                color: '#ffffff'
              }}>
                DOBLE CONFIRMACIÓN HITL · {confirmRequest.badgeLabel}
              </span>
              <button
                onClick={() => setConfirmRequest(null)}
                style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}
              >
                <X size={16} />
              </button>
            </div>

            <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)' }}>
              {confirmRequest.title}
            </h3>

            <div style={{
              padding: '12px',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--bg-subtle)',
              border: '1px solid var(--border-subtle)',
              fontSize: '12px',
              lineHeight: 1.5,
              color: 'var(--text-secondary)',
              whiteSpace: 'pre-wrap'
            }}>
              {confirmRequest.summaryText}
            </div>

            <div>
              <label style={{ display: 'block', fontSize: '11.5px', fontWeight: 600, marginBottom: '5px', color: 'var(--text-primary)' }}>
                Nota de auditoría (opcional):
              </label>
              <input
                type="text"
                value={confirmComment}
                onChange={(e) => setConfirmComment(e.target.value)}
                placeholder="Ej: Verificado en entorno local, procede..."
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  fontSize: '12px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  backgroundColor: 'var(--bg-app)',
                  color: 'var(--text-primary)',
                  outline: 'none'
                }}
              />
            </div>

            {/* Casilla obligatoria de segundo factor consciente */}
            <label style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: '9px',
              padding: '10px 12px',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: confirmChecked ? 'rgba(var(--accent-primary-rgb), 0.1)' : 'var(--bg-subtle)',
              border: `1px solid ${confirmChecked ? 'rgba(var(--accent-primary-rgb), 0.4)' : 'var(--border-subtle)'}`,
              cursor: 'pointer',
              fontSize: '12px',
              fontWeight: 600,
              color: 'var(--text-primary)'
            }}>
              <input
                type="checkbox"
                checked={confirmChecked}
                onChange={(e) => setConfirmChecked(e.target.checked)}
                style={{ marginTop: '2px', cursor: 'pointer' }}
              />
              <span>
                Sí, acepto y confirmo conscientemente esta decisión bajo mi firma criptográfica.
              </span>
            </label>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '4px' }}>
              <button
                type="button"
                onClick={() => setConfirmRequest(null)}
                style={{
                  padding: '8px 14px',
                  fontSize: '12px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  backgroundColor: 'transparent',
                  color: 'var(--text-primary)',
                  cursor: 'pointer'
                }}
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={!confirmChecked || Boolean(actionInProgress)}
                onClick={executeConfirmedAction}
                style={{
                  padding: '8px 16px',
                  fontSize: '12px',
                  fontWeight: 700,
                  borderRadius: 'var(--radius-sm)',
                  border: 'none',
                  backgroundColor: confirmChecked ? (confirmRequest.badgeColor || 'var(--accent-primary)') : 'var(--bg-subtle)',
                  color: confirmChecked ? '#ffffff' : 'var(--text-secondary)',
                  cursor: confirmChecked ? 'pointer' : 'not-allowed',
                  transition: 'var(--transition)'
                }}
              >
                {actionInProgress ? 'Firmando...' : 'Sí, Acepto y Confirmo'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
