import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  BookOpen,
  Cpu,
  CheckCircle2,
  AlertTriangle,
  FileCode,
  Wrench,
  ShieldCheck,
  ShieldAlert,
  FolderOpen,
  Tag,
  Activity,
  RefreshCw
} from 'lucide-react';

function getFreshnessBadgeMeta(status, score) {
  const normalized = String(status || '').toUpperCase();
  const pct = typeof score === 'number' ? `${Math.round(score <= 1 ? score * 100 : score)}%` : null;
  if (normalized === 'FRESH') {
    return { cls: 'mcp-badge-fresh', label: pct ? `FRESH · ${pct}` : 'FRESH' };
  }
  if (normalized === 'AGING') {
    return { cls: 'mcp-badge-aging', label: pct ? `AGING · ${pct}` : 'AGING' };
  }
  if (normalized === 'STALE') {
    return { cls: 'mcp-badge-stale', label: pct ? `STALE · ${pct}` : 'STALE' };
  }
  return null;
}

function SignatureBadge({ signatureValid }) {
  if (signatureValid === true) {
    return (
      <span
        data-testid="ed25519-verified-badge"
        title="Firma criptográfica Ed25519 verificada"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '2px 7px',
          borderRadius: 'var(--radius-full)',
          fontFamily: 'var(--font-mono)',
          fontSize: '10.5px',
          fontWeight: 600,
          backgroundColor: 'rgba(var(--accent-primary-rgb), 0.14)',
          color: 'var(--accent-success)',
          border: '1px solid rgba(var(--accent-primary-rgb), 0.3)'
        }}
      >
        <ShieldCheck size={11} /> Ed25519 OK
      </span>
    );
  }
  if (signatureValid === false) {
    return (
      <span
        data-testid="ed25519-unverified-badge"
        title="Sin firma Ed25519 válida"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '2px 7px',
          borderRadius: 'var(--radius-full)',
          fontFamily: 'var(--font-mono)',
          fontSize: '10.5px',
          fontWeight: 600,
          backgroundColor: 'rgba(245, 158, 11, 0.14)',
          color: 'var(--accent-warning)',
          border: '1px solid rgba(245, 158, 11, 0.3)'
        }}
      >
        <ShieldAlert size={11} /> Sin Firma Ed25519
      </span>
    );
  }
  return null;
}

export function OKFGovernanceView({ project, api, externalTagFilter = 'all' }) {
  const [subTab, setSubTab] = useState('adrs');
  const [okfData, setOkfData] = useState(null);
  const [freshnessReport, setFreshnessReport] = useState(null);
  const [tagsIndex, setTagsIndex] = useState(null);
  const [folders, setFolders] = useState([]);
  const [selectedTag, setSelectedTag] = useState(externalTagFilter || 'all');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (externalTagFilter) {
      setSelectedTag(externalTagFilter);
    }
  }, [externalTagFilter]);

  const loadData = useCallback(async () => {
    if (!api || !project?.project_uuid) return;
    setLoading(true);
    try {
      const uuid = project.project_uuid;
      const results = await Promise.allSettled([
        api.get_okf_overview?.(uuid),
        api.get_okf_freshness_report?.(uuid),
        api.get_okf_tags_index?.(uuid),
        api.list_okf_folders?.(uuid)
      ]);

      const [overviewRes, freshRes, tagsRes, foldersRes] = results;

      if (overviewRes?.status === 'fulfilled' && overviewRes.value?.success) {
        setOkfData(overviewRes.value);
      }
      if (freshRes?.status === 'fulfilled' && freshRes.value?.success) {
        setFreshnessReport(freshRes.value);
      }
      if (tagsRes?.status === 'fulfilled' && tagsRes.value?.success) {
        setTagsIndex(tagsRes.value);
      }
      if (foldersRes?.status === 'fulfilled' && foldersRes.value?.success) {
        setFolders(foldersRes.value.folders || []);
      }
    } catch (e) {
      console.error('Error al cargar gobernanza OKF:', e);
    } finally {
      setLoading(false);
    }
  }, [api, project]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleRepairOKF = async () => {
    if (!api || !project) return;
    try {
      setLoading(true);
      const res = await api.repair_project_okf?.(project.project_uuid);
      if (res?.success) {
        await loadData();
      } else {
        alert('Error al reparar OKF: ' + (res?.error || 'Error desconocido'));
        setLoading(false);
      }
    } catch (e) {
      alert('Error: ' + e);
      setLoading(false);
    }
  };

  const freshnessItems = freshnessReport?.items || [];
  const freshnessSummary = freshnessReport?.summary || null;

  const findFreshnessForAdr = useCallback((adr) => {
    return freshnessItems.find(item =>
      (item.id && adr.id && item.id === adr.id) ||
      (item.path && adr.id && item.path.includes(adr.id)) ||
      (item.title && adr.title && item.title === adr.title)
    );
  }, [freshnessItems]);

  const findFreshnessForSkill = useCallback((sk) => {
    const expectedPath = `docs/okf/${sk.area}/${sk.slug}.md`;
    return freshnessItems.find(item =>
      item.path === expectedPath ||
      (item.path && sk.slug && item.path.includes(sk.slug)) ||
      (item.id && sk.slug && item.id === sk.slug)
    );
  }, [freshnessItems]);

  const getTagsForItem = useCallback((item, freshnessItem, fallbackPath) => {
    const set = new Set();
    const sources = [item?.tags, freshnessItem?.tags];
    sources.forEach(src => {
      if (Array.isArray(src)) src.forEach(t => t && set.add(String(t)));
    });
    if (tagsIndex?.items && fallbackPath) {
      const idxItem = tagsIndex.items.find(it => it.path === fallbackPath || it.path?.endsWith(fallbackPath));
      if (Array.isArray(idxItem?.tags)) {
        idxItem.tags.forEach(t => t && set.add(String(t)));
      }
    }
    if (tagsIndex?.tags && fallbackPath) {
      Object.entries(tagsIndex.tags).forEach(([tag, paths]) => {
        if (Array.isArray(paths) && paths.some(p => p === fallbackPath || p.endsWith(fallbackPath))) {
          set.add(tag);
        }
      });
    }
    return Array.from(set);
  }, [tagsIndex]);

  const availableTags = useMemo(() => {
    const set = new Set(Object.keys(tagsIndex?.tags || {}));
    (tagsIndex?.items || []).forEach(it => (it.tags || []).forEach(t => t && set.add(String(t))));
    freshnessItems.forEach(it => (it.tags || []).forEach(t => t && set.add(String(t))));
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [tagsIndex, freshnessItems]);

  if (loading && !okfData && !freshnessReport) {
    return <div style={{ padding: '24px', color: 'var(--text-secondary)' }}>Cargando gobernanza OKF...</div>;
  }

  const adrs = okfData?.adrs || [];
  const skills = okfData?.skills || [];
  const validation = okfData?.validation || {};

  const filteredAdrs = selectedTag === 'all'
    ? adrs
    : adrs.filter(adr => {
        const f = findFreshnessForAdr(adr);
        const tags = getTagsForItem(adr, f, f?.path || adr.id);
        return tags.includes(selectedTag);
      });

  const filteredSkills = selectedTag === 'all'
    ? skills
    : skills.filter(sk => {
        const f = findFreshnessForSkill(sk);
        const expectedPath = `docs/okf/${sk.area}/${sk.slug}.md`;
        const tags = getTagsForItem(sk, f, f?.path || expectedPath);
        return tags.includes(selectedTag);
      });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '12px', minHeight: 0 }}>
      {/* Subtab Bar + Filtro de Tag YAML */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '8px',
        borderBottom: '1px solid var(--border-subtle)',
        paddingBottom: '8px'
      }}>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={() => setSubTab('adrs')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              fontSize: '12.5px',
              fontWeight: subTab === 'adrs' ? 600 : 500,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: subTab === 'adrs' ? 'var(--accent-primary)' : 'var(--bg-card)',
              color: subTab === 'adrs' ? '#ffffff' : 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            <BookOpen size={14} /> Decisiones (ADRs) ({filteredAdrs.length})
          </button>

          <button
            type="button"
            onClick={() => setSubTab('skills')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              fontSize: '12.5px',
              fontWeight: subTab === 'skills' ? 600 : 500,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: subTab === 'skills' ? 'var(--accent-primary)' : 'var(--bg-card)',
              color: subTab === 'skills' ? '#ffffff' : 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            <Cpu size={14} /> Fichas &amp; Skills ({filteredSkills.length})
          </button>

          <button
            type="button"
            onClick={() => setSubTab('audit')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              fontSize: '12.5px',
              fontWeight: subTab === 'audit' ? 600 : 500,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: subTab === 'audit' ? 'var(--accent-primary)' : 'var(--bg-card)',
              color: subTab === 'audit' ? '#ffffff' : 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            <CheckCircle2 size={14} /> Auditoría &amp; Freshness
          </button>
        </div>

        {/* Filtro Tag YAML + Recargar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
            <Tag size={13} color="var(--text-muted)" />
            <select
              data-testid="okf-tag-filter"
              aria-label="Filtrar artefactos OKF por Tag YAML"
              className="mcp-combobox"
              value={selectedTag}
              onChange={(e) => setSelectedTag(e.target.value)}
              style={{ height: '30px', fontSize: '12px', maxWidth: '185px' }}
            >
              <option value="all">Todos los Tags YAML</option>
              {availableTags.map(t => (
                <option key={t} value={t}>#{t}</option>
              ))}
            </select>
          </div>
          <button
            type="button"
            onClick={loadData}
            title="Refrescar métricas Freshness, firmas y tags"
            style={{
              height: '30px',
              width: '30px',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-card)',
              color: 'var(--text-secondary)',
              cursor: 'pointer'
            }}
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {/* Cinta compacta de Resumen Freshness + Carpetas OKF activas */}
      {(freshnessSummary || folders.length > 0) && (
        <div
          data-testid="okf-summary-strip"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '8px',
            padding: '8px 12px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            fontSize: '11.5px'
          }}
        >
          {freshnessSummary && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                <Activity size={13} color="var(--accent-primary)" /> Freshness:
              </span>
              <span className="mcp-badge-fresh">FRESH {freshnessSummary.fresh ?? 0}</span>
              <span className="mcp-badge-aging">AGING {freshnessSummary.aging ?? 0}</span>
              <span className="mcp-badge-stale">STALE {freshnessSummary.stale ?? 0}</span>
              {typeof freshnessSummary.avg_score === 'number' && (
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', fontWeight: 600 }}>
                  Promedio: {Math.round(freshnessSummary.avg_score <= 1 ? freshnessSummary.avg_score * 100 : freshnessSummary.avg_score)}%
                </span>
              )}
              {typeof freshnessSummary.verified_signatures === 'number' && (
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                  Firmas Ed25519: {freshnessSummary.verified_signatures}
                  {freshnessSummary.unverified_adrs ? ` (${freshnessSummary.unverified_adrs} sin firma)` : ''}
                </span>
              )}
            </div>
          )}

          {folders.length > 0 && (
            <div
              data-testid="okf-active-folders-summary"
              style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}
            >
              <FolderOpen size={13} color="var(--accent-indigo)" />
              <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>
                Carpetas OKF ({folders.length}):
              </span>
              {folders.slice(0, 4).map(f => (
                <span
                  key={f.path}
                  title={f.description || f.category}
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: '10.5px',
                    padding: '1px 6px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'var(--bg-subtle)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-subtle)'
                  }}
                >
                  {f.path} ({f.file_count ?? 0})
                </span>
              ))}
              {folders.length > 4 && (
                <span style={{ fontSize: '10.5px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  +{folders.length - 4} más
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* Subtab Content */}
      <div style={{ flex: 1, overflowY: 'auto', paddingRight: '4px', minHeight: 0 }}>

        {/* SUBTAB 1: ADRs */}
        {subTab === 'adrs' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {filteredAdrs.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>
                {selectedTag === 'all' ? 'No hay ADRs registrados aún.' : `No hay ADRs con el tag #${selectedTag}.`}
              </div>
            ) : (
              filteredAdrs.map(adr => {
                const fresh = findFreshnessForAdr(adr);
                const freshBadge = getFreshnessBadgeMeta(fresh?.status, fresh?.score);
                const sigValid = fresh?.signature_valid ?? adr.signature_valid;
                const tags = getTagsForItem(adr, fresh, fresh?.path || adr.id);

                return (
                  <div
                    key={adr.id}
                    data-testid={`adr-card-${adr.id}`}
                    style={{
                      backgroundColor: 'var(--bg-card)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 'var(--radius-md)',
                      padding: '15px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '9px'
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                        <span style={{
                          backgroundColor: 'rgba(var(--accent-primary-rgb), 0.15)',
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

                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                        {freshBadge && (
                          <span
                            className={freshBadge.cls}
                            title={`Commits posteriores: ${fresh.commits_after ?? 0} · Antigüedad: ${fresh.days_old ?? 0} días`}
                          >
                            {freshBadge.label}
                          </span>
                        )}
                        <SignatureBadge signatureValid={sigValid} />
                      </div>
                    </div>

                    {tags.length > 0 && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '5px', flexWrap: 'wrap' }}>
                        {tags.slice(0, 4).map(t => (
                          <span key={t} className="mcp-tag-pill">#{t}</span>
                        ))}
                        {tags.length > 4 && (
                          <span style={{ fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                            +{tags.length - 4}
                          </span>
                        )}
                      </div>
                    )}

                    <div style={{ fontSize: '12px', lineHeight: 1.5, display: 'flex', flexDirection: 'column', gap: '5px' }}>
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
                );
              })
            )}
          </div>
        )}

        {/* SUBTAB 2: Skills & Fichas */}
        {subTab === 'skills' && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '12px' }}>
            {filteredSkills.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>
                {selectedTag === 'all' ? 'No hay fichas registradas.' : `No hay fichas con el tag #${selectedTag}.`}
              </div>
            ) : (
              filteredSkills.map(sk => {
                const expectedPath = `docs/okf/${sk.area}/${sk.slug}.md`;
                const fresh = findFreshnessForSkill(sk);
                const freshBadge = getFreshnessBadgeMeta(fresh?.status, fresh?.score);
                const sigValid = fresh?.signature_valid ?? sk.signature_valid;
                const tags = getTagsForItem(sk, fresh, fresh?.path || expectedPath);

                return (
                  <div
                    key={sk.slug}
                    data-testid={`skill-card-${sk.slug}`}
                    style={{
                      backgroundColor: 'var(--bg-card)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 'var(--radius-md)',
                      padding: '15px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px'
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '6px' }}>
                      <span style={{
                        fontSize: '10px',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 700,
                        padding: '2px 6px',
                        borderRadius: '3px',
                        backgroundColor: sk.area === 'backend' ? 'rgba(var(--accent-indigo-rgb), 0.15)' : 'rgba(var(--accent-primary-rgb), 0.15)',
                        color: sk.area === 'backend' ? 'var(--accent-indigo)' : 'var(--accent-success)'
                      }}>
                        {String(sk.area || 'okf').toUpperCase()}
                      </span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        {freshBadge && <span className={freshBadge.cls}>{freshBadge.label}</span>}
                        <SignatureBadge signatureValid={sigValid} />
                      </div>
                    </div>

                    <h4 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
                      {sk.title}
                    </h4>

                    {tags.length > 0 && (
                      <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                        {tags.slice(0, 3).map(t => (
                          <span key={t} className="mcp-tag-pill">#{t}</span>
                        ))}
                        {tags.length > 3 && (
                          <span style={{ fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                            +{tags.length - 3}
                          </span>
                        )}
                      </div>
                    )}

                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: 'auto', display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <FileCode size={12} />
                      <span>{fresh?.path || expectedPath}</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}

        {/* SUBTAB 3: Auditoría, Freshness & Carpetas */}
        {subTab === 'audit' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            <div style={{
              backgroundColor: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-md)',
              padding: '18px',
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
                  type="button"
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
                    {validation.stats?.adrs_found || adrs.length || 0}
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
                {freshnessSummary && (
                  <div style={{ background: 'var(--bg-subtle)', padding: '10px', borderRadius: 'var(--radius-sm)' }}>
                    <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Firmas Ed25519</div>
                    <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--accent-success)' }}>
                      {freshnessSummary.verified_signatures ?? 0}
                    </div>
                  </div>
                )}
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

            {/* Carpetas activas de OKF */}
            {folders.length > 0 && (
              <div style={{
                backgroundColor: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <FolderOpen size={14} color="var(--accent-primary)" /> Carpetas Activas OKF ({folders.length})
                </h5>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '8px' }}>
                  {folders.map(folder => (
                    <div
                      key={folder.path}
                      style={{
                        backgroundColor: 'var(--bg-subtle)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-sm)',
                        padding: '10px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '4px'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '6px' }}>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11.5px', fontWeight: 600 }}>
                          {folder.path}
                        </span>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10.5px', color: 'var(--text-muted)' }}>
                          {folder.file_count ?? 0} arch.
                        </span>
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                        {folder.description || folder.category}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Tabla detallada de Freshness */}
            {freshnessItems.length > 0 && (
              <div style={{
                backgroundColor: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-md)',
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <h5 style={{ fontSize: '13px', fontWeight: 600, margin: 0 }}>
                  Reporte del Algoritmo Freshness ({freshnessItems.length} artefactos)
                </h5>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {freshnessItems.map(item => {
                    const badge = getFreshnessBadgeMeta(item.status, item.score);
                    return (
                      <div
                        key={item.path || item.id}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          flexWrap: 'wrap',
                          gap: '8px',
                          padding: '8px 10px',
                          borderRadius: 'var(--radius-sm)',
                          backgroundColor: 'var(--bg-subtle)',
                          fontSize: '11.5px'
                        }}
                      >
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
                          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{item.title || item.id || item.path}</span>
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10.5px', color: 'var(--text-muted)' }}>
                            {item.path} · {item.days_old ?? 0}d · {item.commits_after ?? 0} commits post.
                          </span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          {badge && <span className={badge.cls}>{badge.label}</span>}
                          <SignatureBadge signatureValid={item.signature_valid} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}
