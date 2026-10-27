import React, { useState, useEffect } from 'react';
import { BookOpen, Cpu, CheckCircle2, AlertTriangle, FileCode, Wrench } from 'lucide-react';

export function OKFGovernanceView({ project, api }) {
  const [subTab, setSubTab] = useState('adrs');
  const [okfData, setOkfData] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadData = () => {
    if (!api || !project) return;
    setLoading(true);
    api.get_okf_overview(project.project_uuid).then(res => {
      if (res.success) {
        setOkfData(res);
      }
      setLoading(false);
    });
  };

  useEffect(() => {
    loadData();
  }, [project, api]);

  const handleRepairOKF = async () => {
    if (!api || !project) return;
    try {
      setLoading(true);
      const res = await api.repair_project_okf(project.project_uuid);
      if (res.success) {
        loadData();
      } else {
        alert("Error al reparar OKF: " + res.error);
        setLoading(false);
      }
    } catch (e) {
      alert("Error: " + e);
      setLoading(false);
    }
  };

  if (loading && !okfData) {
    return <div style={{ padding: '24px', color: 'var(--text-secondary)' }}>Cargando gobernanza OKF...</div>;
  }

  const adrs = okfData?.adrs || [];
  const skills = okfData?.skills || [];
  const validation = okfData?.validation || {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '16px' }}>
      {/* Subtab Bar */}
      <div style={{
        display: 'flex',
        gap: '8px',
        borderBottom: '1px solid var(--border-subtle)',
        paddingBottom: '8px'
      }}>
        <button
          onClick={() => setSubTab('adrs')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 14px',
            fontSize: '13px',
            fontWeight: subTab === 'adrs' ? 600 : 500,
            borderRadius: 'var(--radius-md)',
            border: 'none',
            backgroundColor: subTab === 'adrs' ? 'var(--accent-primary)' : 'var(--bg-card)',
            color: subTab === 'adrs' ? '#ffffff' : 'var(--text-primary)',
            cursor: 'pointer'
          }}
        >
          <BookOpen size={15} /> Decisiones (ADRs) ({adrs.length})
        </button>

        <button
          onClick={() => setSubTab('skills')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 14px',
            fontSize: '13px',
            fontWeight: subTab === 'skills' ? 600 : 500,
            borderRadius: 'var(--radius-md)',
            border: 'none',
            backgroundColor: subTab === 'skills' ? 'var(--accent-primary)' : 'var(--bg-card)',
            color: subTab === 'skills' ? '#ffffff' : 'var(--text-primary)',
            cursor: 'pointer'
          }}
        >
          <Cpu size={15} /> Fichas & Skills ({skills.length})
        </button>

        <button
          onClick={() => setSubTab('audit')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 14px',
            fontSize: '13px',
            fontWeight: subTab === 'audit' ? 600 : 500,
            borderRadius: 'var(--radius-md)',
            border: 'none',
            backgroundColor: subTab === 'audit' ? 'var(--accent-primary)' : 'var(--bg-card)',
            color: subTab === 'audit' ? '#ffffff' : 'var(--text-primary)',
            cursor: 'pointer'
          }}
        >
          <CheckCircle2 size={15} /> Auditoría e Integridad
        </button>
      </div>

      {/* Subtab Content */}
      <div style={{ flex: 1, overflowY: 'auto', paddingRight: '4px' }}>
        
        {/* SUBTAB 1: ADRs */}
        {subTab === 'adrs' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            {adrs.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No hay ADRs registrados aún.</div>
            ) : (
              adrs.map(adr => (
                <div
                  key={adr.id}
                  style={{
                    backgroundColor: 'var(--bg-card)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-md)',
                    padding: '16px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '10px'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{
                      backgroundColor: 'rgba(79, 70, 229, 0.15)',
                      color: 'var(--accent-primary)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '11px',
                      fontWeight: 700,
                      padding: '2px 8px',
                      borderRadius: 'var(--radius-sm)'
                    }}>
                      {adr.id}
                    </span>
                    <h4 style={{ fontSize: '14px', fontWeight: 600, margin: 0, color: 'var(--text-primary)' }}>
                      {adr.title}
                    </h4>
                  </div>

                  <div style={{ fontSize: '12px', lineHeight: 1.5, display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <div>
                      <strong style={{ color: 'var(--text-secondary)' }}>Contexto: </strong>
                      <span style={{ color: 'var(--text-primary)' }}>{adr.context}</span>
                    </div>
                    <div>
                      <strong style={{ color: 'var(--text-secondary)' }}>Decisión: </strong>
                      <span style={{ color: 'var(--text-primary)' }}>{adr.decision}</span>
                    </div>
                    <div>
                      <strong style={{ color: 'var(--text-secondary)' }}>Consecuencias: </strong>
                      <span style={{ color: 'var(--text-muted)' }}>{adr.consequences}</span>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* SUBTAB 2: Skills & Fichas */}
        {subTab === 'skills' && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '14px' }}>
            {skills.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No hay fichas registradas.</div>
            ) : (
              skills.map(sk => (
                <div
                  key={sk.slug}
                  style={{
                    backgroundColor: 'var(--bg-card)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius-md)',
                    padding: '16px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px'
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{
                      fontSize: '10px',
                      fontFamily: 'var(--font-mono)',
                      fontWeight: 700,
                      padding: '2px 6px',
                      borderRadius: '3px',
                      backgroundColor: sk.area === 'backend' ? 'rgba(79, 70, 229, 0.15)' : 'rgba(16, 185, 129, 0.15)',
                      color: sk.area === 'backend' ? 'var(--accent-primary)' : 'var(--accent-success)'
                    }}>
                      {sk.area.toUpperCase()}
                    </span>
                    <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                      {sk.slug}.md
                    </span>
                  </div>
                  <h4 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
                    {sk.title}
                  </h4>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: 'auto', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <FileCode size={12} />
                    <span>docs/okf/{sk.area}/{sk.slug}.md</span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* SUBTAB 3: Auditoría */}
        {subTab === 'audit' && (
          <div style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-md)',
            padding: '20px',
            display: 'flex',
            flexDirection: 'column',
            gap: '14px'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {validation.valid ? (
                  <CheckCircle2 size={18} color="var(--accent-success)" />
                ) : (
                  <AlertTriangle size={18} color="var(--accent-warning)" />
                )}
                <h4 style={{ fontSize: '14px', fontWeight: 600, margin: 0 }}>
                  {validation.valid ? 'Especificación OKF Válida y Conforme' : 'Advertencias de Conformidad OKF'}
                </h4>
              </div>

              <button
                onClick={handleRepairOKF}
                title="Sincronizar e inicializar cualquier archivo o directorio OKF faltante"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 12px',
                  fontSize: '12px',
                  fontWeight: 600,
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  backgroundColor: 'var(--bg-subtle)',
                  color: 'var(--text-primary)',
                  cursor: 'pointer'
                }}
              >
                <Wrench size={13} /> Reparar / Sincronizar OKF
              </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '10px' }}>
              <div style={{ background: 'var(--bg-subtle)', padding: '10px', borderRadius: 'var(--radius-sm)' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>ADRs Detectados</div>
                <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
                  {validation.stats?.adrs_found || 0}
                </div>
              </div>
              <div style={{ background: 'var(--bg-subtle)', padding: '10px', borderRadius: 'var(--radius-sm)' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Historias de Usuario</div>
                <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
                  {validation.stats?.user_stories_found || 0}
                </div>
              </div>
              <div style={{ background: 'var(--bg-subtle)', padding: '10px', borderRadius: 'var(--radius-sm)' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Features Backend</div>
                <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
                  {validation.stats?.backend_features || 0}
                </div>
              </div>
            </div>

            {validation.errors?.length > 0 && (
              <div style={{ background: 'rgba(239, 68, 68, 0.1)', padding: '12px', borderRadius: 'var(--radius-sm)', border: '1px solid rgba(239, 68, 68, 0.3)' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--accent-danger)', marginBottom: '4px' }}>Errores:</div>
                <ul style={{ fontSize: '11px', color: 'var(--accent-danger)', paddingLeft: '18px' }}>
                  {validation.errors.map((e, idx) => <li key={idx}>{e}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}
