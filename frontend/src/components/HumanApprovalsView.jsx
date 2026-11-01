import React, { useState, useEffect, useCallback } from 'react';
import { ShieldCheck, CheckCircle2, XCircle, AlertTriangle, Scale, GitPullRequest, FileText, ArrowRight, RefreshCw } from 'lucide-react';

export function HumanApprovalsView({ project, api }) {
  const [loading, setLoading] = useState(true);
  const [approvals, setApprovals] = useState({
    pending_decisions: [],
    open_disputes: [],
    pending_prs: [],
    total_count: 0
  });
  const [actionInProgress, setActionInProgress] = useState(null);

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

  const handleApproveDecision = async (itemId, decision = 'APPROVED') => {
    if (!api || !project) return;
    try {
      setActionInProgress(itemId);
      const res = await api.sign_and_approve_item(
        project.project_uuid,
        'decision',
        itemId,
        decision,
        decision === 'APPROVED' ? 'Aprobado formalmente mediante firma HITL.' : 'Descartado por decisión humana.'
      );
      if (res.success) {
        await loadApprovals();
      } else {
        alert("Error al firmar decisión: " + (res.error || 'Fallo desconocido'));
      }
    } catch (err) {
      alert("Error de comunicación: " + err);
    } finally {
      setActionInProgress(null);
    }
  };

  const handleJuryVote = async (ticketId, selectedOption) => {
    if (!api || !project) return;
    try {
      setActionInProgress(ticketId);
      const res = await api.sign_and_approve_item(
        project.project_uuid,
        'jury_vote',
        ticketId,
        selectedOption,
        `Voto humano emitido para ${selectedOption} con peso 2.0.`
      );
      if (res.success) {
        await loadApprovals();
      } else {
        alert("Error al registrar voto: " + (res.error || 'Fallo desconocido'));
      }
    } catch (err) {
      alert("Error de comunicación: " + err);
    } finally {
      setActionInProgress(null);
    }
  };

  const handleMergePR = async (prOrCardId) => {
    if (!api || !project) return;
    try {
      setActionInProgress(prOrCardId);
      const res = await api.sign_and_approve_item(
        project.project_uuid,
        'pr_merge',
        prOrCardId,
        'APPROVED',
        'Merge aprobado por desarrollador humano.'
      );
      if (res.success) {
        await loadApprovals();
      } else {
        alert("Error al fusionar PR: " + (res.error || 'Fallo desconocido'));
      }
    } catch (err) {
      alert("Error de comunicación: " + err);
    } finally {
      setActionInProgress(null);
    }
  };

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
      {/* Banner Superior HITL */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '16px 20px',
        backgroundColor: 'var(--bg-surface)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border-subtle)',
        boxShadow: 'var(--shadow-sm)'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{
            width: '40px',
            height: '40px',
            borderRadius: '50%',
            backgroundColor: 'rgba(56, 189, 248, 0.1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-primary)'
          }}>
            <ShieldCheck size={22} />
          </div>
          <div>
            <h2 style={{ fontSize: '16px', fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>
              Bandeja HITL de Aprobación Humana
            </h2>
            <p style={{ fontSize: '12px', margin: '3px 0 0', color: 'var(--text-secondary)' }}>
              Firma criptográfica <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>Ed25519</span> exclusiva del desarrollador. Los agentes de IA no pueden auto-firmar.
            </p>
          </div>
        </div>

        <button
          onClick={loadApprovals}
          disabled={loading}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 14px',
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

      {/* Grid de Secciones */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '16px' }}>

        {/* 1. SECCIÓN: DECISIONES PENDIENTES */}
        <div style={{
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-subtle)',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <FileText size={16} color="var(--accent-primary)" />
              <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)' }}>
                Decisiones Arquitectónicas ({approvals.pending_decisions.length})
              </span>
            </div>
            <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>docs/okf/decisions/</span>
          </div>

          {approvals.pending_decisions.length === 0 ? (
            <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '12px' }}>
              No hay decisiones pendientes de firma.
            </div>
          ) : (
            approvals.pending_decisions.map(dec => (
              <div key={dec.item_id} style={{
                backgroundColor: 'var(--bg-subtle)',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-subtle)',
                padding: '12px',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px'
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <span style={{ fontWeight: 700, fontSize: '13px', color: 'var(--text-primary)' }}>
                    {dec.item_id}: {dec.title}
                  </span>
                  <span style={{
                    fontSize: '10px',
                    fontFamily: 'var(--font-mono)',
                    backgroundColor: 'rgba(234, 179, 8, 0.15)',
                    color: '#eab308',
                    padding: '2px 6px',
                    borderRadius: '4px'
                  }}>
                    PENDIENTE
                  </span>
                </div>
                <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: 0, maxHeight: '60px', overflow: 'hidden' }}>
                  {dec.excerpt}
                </p>
                <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                  <button
                    disabled={actionInProgress === dec.item_id}
                    onClick={() => handleApproveDecision(dec.item_id, 'APPROVED')}
                    style={{
                      flex: 1,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '6px',
                      backgroundColor: 'var(--accent-primary)',
                      color: '#ffffff',
                      border: 'none',
                      padding: '6px',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '11px',
                      fontWeight: 600,
                      cursor: 'pointer'
                    }}
                  >
                    <CheckCircle2 size={13} /> Aprobar y Promover a ADR
                  </button>
                  <button
                    disabled={actionInProgress === dec.item_id}
                    onClick={() => handleApproveDecision(dec.item_id, 'DISCARDED')}
                    style={{
                      padding: '6px 10px',
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

        {/* 2. SECCIÓN: JUICIOS DE HONOR PONDERADOS */}
        <div style={{
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-subtle)',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Scale size={16} color="#a855f7" />
              <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)' }}>
                Juicios de Honor Abiertos ({approvals.open_disputes.length})
              </span>
            </div>
            <span style={{ fontSize: '11px', color: '#a855f7', fontWeight: 600 }}>Peso Humano: 2.0 pts</span>
          </div>

          {approvals.open_disputes.length === 0 ? (
            <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '12px' }}>
              No hay disputas técnicas abiertas.
            </div>
          ) : (
            approvals.open_disputes.map(dispute => (
              <div key={dispute.ticket_id} style={{
                backgroundColor: 'var(--bg-subtle)',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-subtle)',
                padding: '12px',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px'
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ fontWeight: 700, fontSize: '12px', color: 'var(--text-primary)' }}>
                    {dispute.title}
                  </span>
                  <span style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                    {dispute.ticket_id}
                  </span>
                </div>
                <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: 0 }}>
                  Motivo: {dispute.collision_reason}
                </p>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginTop: '6px' }}>
                  <button
                    disabled={actionInProgress === dispute.ticket_id || dispute.has_voted}
                    onClick={() => handleJuryVote(dispute.ticket_id, 'OPTION_A')}
                    style={{
                      padding: '6px',
                      fontSize: '11px',
                      fontWeight: 600,
                      backgroundColor: 'rgba(56, 189, 248, 0.15)',
                      color: 'var(--accent-primary)',
                      border: '1px solid rgba(56, 189, 248, 0.3)',
                      borderRadius: 'var(--radius-sm)',
                      cursor: dispute.has_voted ? 'default' : 'pointer'
                    }}
                  >
                    Votar Opción A
                  </button>
                  <button
                    disabled={actionInProgress === dispute.ticket_id || dispute.has_voted}
                    onClick={() => handleJuryVote(dispute.ticket_id, 'OPTION_B')}
                    style={{
                      padding: '6px',
                      fontSize: '11px',
                      fontWeight: 600,
                      backgroundColor: 'rgba(168, 85, 247, 0.15)',
                      color: '#c084fc',
                      border: '1px solid rgba(168, 85, 247, 0.3)',
                      borderRadius: 'var(--radius-sm)',
                      cursor: dispute.has_voted ? 'default' : 'pointer'
                    }}
                  >
                    Votar Opción B
                  </button>
                </div>
                {dispute.has_voted && (
                  <span style={{ fontSize: '10px', color: '#10b981', textAlign: 'center', marginTop: '2px' }}>
                    ✓ Ya has emitido tu voto humano de 2.0 puntos en este ticket.
                  </span>
                )}
              </div>
            ))
          )}
        </div>

        {/* 3. SECCIÓN: PULL REQUESTS Y REVISIÓN DE CÓDIGO */}
        <div style={{
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border-subtle)',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <GitPullRequest size={16} color="#10b981" />
              <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)' }}>
                Tareas en Revisión / PRs ({approvals.pending_prs.length})
              </span>
            </div>
            <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Status: in_review</span>
          </div>

          {approvals.pending_prs.length === 0 ? (
            <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '12px' }}>
              No hay tareas en revisión esperando merge.
            </div>
          ) : (
            approvals.pending_prs.map(pr => (
              <div key={pr.card_id} style={{
                backgroundColor: 'var(--bg-subtle)',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-subtle)',
                padding: '12px',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px'
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <span style={{ fontWeight: 700, fontSize: '12px', color: 'var(--text-primary)' }}>
                    {pr.card_id}: {pr.title}
                  </span>
                  <span style={{ fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                    {pr.branch_ref || 'branch'}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                  <button
                    disabled={actionInProgress === pr.card_id}
                    onClick={() => handleMergePR(pr.card_id)}
                    style={{
                      flex: 1,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '6px',
                      backgroundColor: '#10b981',
                      color: '#ffffff',
                      border: 'none',
                      padding: '6px',
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
    </div>
  );
}
