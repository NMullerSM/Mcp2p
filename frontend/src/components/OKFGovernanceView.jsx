import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { ViewSliderContent } from './ViewSlider';
import {
  BookOpen,
  Cpu,
  CheckCircle2,
  AlertTriangle,
  FileCode,
  Wrench,
  ShieldCheck,
  Shield,
  FolderOpen,
  Tag,
  Activity,
  RefreshCw,
  ChevronDown,
  ChevronRight
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
          padding: '2px 8px',
          borderRadius: 'var(--radius-full)',
          fontFamily: 'var(--font-mono)',
          fontSize: '10.5px',
          fontWeight: 600,
          backgroundColor: 'rgba(var(--accent-primary-rgb), 0.10)',
          color: 'var(--accent-primary)',
          border: '1px solid rgba(var(--accent-primary-rgb), 0.25)'
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
        title="Documento sin firma Ed25519 aplicada aún"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '4px',
          padding: '2px 8px',
          borderRadius: 'var(--radius-full)',
          fontFamily: 'var(--font-mono)',
          fontSize: '10.5px',
          fontWeight: 500,
          backgroundColor: 'var(--bg-subtle)',
          color: 'var(--text-muted)',
          border: '1px solid var(--border-subtle)'
        }}
      >
        <Shield size={11} /> Sin Firma Ed25519
      </span>
    );
  }
  return null;
}

export function OKFGovernanceView({
  project,
  api,
  externalTagFilter = 'all',
  externalSearchQuery = '',
  initialSubTab = 'adrs',
  hideTopTagFilter = false
}) {
  const [subTab, setSubTab] = useState(initialSubTab || 'adrs');
  const [okfData, setOkfData] = useState(null);
  const [freshnessReport, setFreshnessReport] = useState(null);
  const [tagsIndex, setTagsIndex] = useState(null);
  const [folders, setFolders] = useState([]);
  const [selectedTag, setSelectedTag] = useState(externalTagFilter || 'all');
  const [expandedAdrIds, setExpandedAdrIds] = useState({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (externalTagFilter) {
      setSelectedTag(externalTagFilter);
    }
  }, [externalTagFilter]);

  useEffect(() => {
    if (initialSubTab) {
      setSubTab(initialSubTab);
    }
  }, [initialSubTab]);

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
  const normalizedQuery = (externalSearchQuery || '').trim().toLowerCase();

  const filteredAdrs = adrs.filter(adr => {
    const f = findFreshnessForAdr(adr);
    const tags = getTagsForItem(adr, f, f?.path || adr.id);
    if (selectedTag !== 'all' && !tags.includes(selectedTag)) return false;
    if (normalizedQuery) {
      const haystack = [adr.id, adr.title, adr.context, adr.decision, adr.consequences, ...tags]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(normalizedQuery)) return false;
    }
    return true;
  });

  const filteredSkills = skills.filter(sk => {
    const f = findFreshnessForSkill(sk);
    const expectedPath = `docs/okf/${sk.area}/${sk.slug}.md`;
    const tags = getTagsForItem(sk, f, f?.path || expectedPath);
    if (selectedTag !== 'all' && !tags.includes(selectedTag)) return false;
    if (normalizedQuery) {
      const haystack = [sk.slug, sk.title, sk.area, f?.path || expectedPath, ...tags]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(normalizedQuery)) return false;
    }
    return true;
  });

  const filteredFreshnessItems = freshnessItems.filter(item => {
    if (selectedTag !== 'all') {
      const tags = item.tags || [];
      if (!tags.includes(selectedTag)) return false;
    }
    if (normalizedQuery) {
      const haystack = [item.title, item.id, item.path, ...(item.tags || [])]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(normalizedQuery)) return false;
    }
    return true;
  });

  const toggleAdrExpand = (adrId) => {
    setExpandedAdrIds(prev => ({ ...prev, [adrId]: !prev[adrId] }));
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '16px', minHeight: 0 }}>
      {/* Cabecera segmentada limpia al estilo Collaborators / Control de Cambios */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '10px',
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-lg)',
        padding: '8px 12px',
        boxShadow: 'var(--shadow-sm)'
      }}>
        <div style={{
          display: 'inline-flex',
          backgroundColor: 'var(--bg-subtle)',
          padding: '3px',
          borderRadius: 'var(--radius-md)',
          gap: '4px',
          flexWrap: 'wrap'
        }}>
          {[
            { id: 'adrs', label: `Decisiones (${filteredAdrs.length})`, icon: BookOpen },
            { id: 'skills', label: `Fichas & Skills (${filteredSkills.length})`, icon: Cpu },
            { id: 'audit', label: 'Auditoría & Carpetas', icon: CheckCircle2 }
          ].map(tab => {
            const Icon = tab.icon;
            const active = subTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setSubTab(tab.id)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 12px',
                  fontSize: '12px',
                  fontWeight: active ? 700 : 500,
                  borderRadius: 'var(--radius-sm)',
                  border: 'none',
                  backgroundColor: active ? 'var(--bg-surface)' : 'transparent',
                  color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  boxShadow: active ? 'var(--shadow-sm)' : 'none',
                  transition: 'var(--transition)'
                }}
              >
                <Icon size={13} color={active ? 'var(--accent-primary)' : 'currentColor'} />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

      </div>

      {/* Slider lateral derecho (pestana en el borde): filtro por tag, refresco, salud
          (freshness/firmas) y carpetas OKF activas. En pantalla queda solo el visor. */}
      <ViewSliderContent
        title="Gobernanza OKF: filtros y salud"
        subtitle="Tags, freshness y carpetas"
        data-testid="okf-slider"
      >
        <section style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          {!hideTopTagFilter && (
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
              <Tag size={13} color="var(--text-muted)" />
              <select
                data-testid="okf-tag-filter"
                aria-label="Filtrar artefactos OKF por Tag YAML"
                className="mcp-combobox"
                value={selectedTag}
                onChange={(e) => setSelectedTag(e.target.value)}
                style={{ height: '30px', fontSize: '12px', maxWidth: '240px' }}
              >
                <option value="all">Todos los Tags YAML</option>
                {availableTags.map(t => (
                  <option key={t} value={t}>#{t}</option>
                ))}
              </select>
            </div>
          )}
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
              backgroundColor: 'var(--bg-surface)',
              color: 'var(--text-secondary)',
              cursor: 'pointer'
            }}
          >
            <RefreshCw size={13} />
          </button>
        </section>
      {/* Resumen Sutil y Armónico (sin colores estridentes amarillos/rojos) */}
      {(freshnessSummary || folders.length > 0) && (
        <div
          data-testid="okf-summary-strip"
          style={{
            display: 'flex',
            justifyContent: 'flex-start',
            flexDirection: 'column',
            alignItems: 'stretch',
            gap: '10px',
            padding: '10px 14px',
            borderRadius: 'var(--radius-lg)',
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            fontSize: '12px'
          }}
        >
          {freshnessSummary && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontWeight: 600, color: 'var(--text-primary)' }}>
                <Activity size={14} color="var(--accent-primary)" /> Salud OKF
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--accent-primary)' }}>
                Vigentes: {freshnessSummary.fresh ?? 0}
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--accent-indigo)' }}>
                Revisión: {freshnessSummary.aging ?? 0}
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-muted)' }}>
                Desactualizados: {freshnessSummary.stale ?? 0}
              </span>
              {typeof freshnessSummary.avg_score === 'number' && (
                <span style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '11px',
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-sm)',
                  backgroundColor: 'var(--bg-subtle)',
                  color: 'var(--text-primary)',
                  fontWeight: 600
                }}>
                  Score {Math.round(freshnessSummary.avg_score <= 1 ? freshnessSummary.avg_score * 100 : freshnessSummary.avg_score)}%
                </span>
              )}
              {typeof freshnessSummary.verified_signatures === 'number' && (
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-secondary)' }}>
                  Ed25519: {freshnessSummary.verified_signatures}
                  {freshnessSummary.unverified_adrs ? ` · ${freshnessSummary.unverified_adrs} pendientes` : ''}
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
              <span style={{ color: 'var(--text-secondary)', fontWeight: 600, fontSize: '11.5px' }}>
                Carpetas ({folders.length}):
              </span>
              {folders.map(f => (
                <span
                  key={f.path}
                  title={f.description || f.category}
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: '10.5px',
                    padding: '2px 7px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'var(--bg-subtle)',
                    color: 'var(--text-secondary)',
                    border: '1px solid var(--border-subtle)'
                  }}
                >
                  {f.path} ({f.file_count ?? 0})
                </span>
              ))}
            </div>
          )}
        </div>
      )}
      </ViewSliderContent>

      {/* Contenido Principal */}
      <div style={{ flex: 1, overflowY: 'auto', paddingRight: '4px', minHeight: 0, scrollbarGutter: 'stable' }}>

        {/* SUBTAB 1: ADRs en formato tarjeta limpia estilo CollaboratorsView */}
        {subTab === 'adrs' && (
          <div style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-lg)',
            padding: '18px 20px',
            display: 'flex',
            flexDirection: 'column',
            gap: '12px',
            boxShadow: 'var(--shadow-sm)'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <BookOpen size={17} color="var(--accent-primary)" />
                <h4 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>
                  Registros de Decisión Arquitectónica ({filteredAdrs.length})
                </h4>
              </div>
              <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                Click en una decisión para expandir detalles
              </span>
            </div>

            {filteredAdrs.length === 0 ? (
              <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
                {selectedTag === 'all' && !normalizedQuery
                  ? 'No hay ADRs registrados aún.'
                  : 'Ningún ADR coincide con el filtro o búsqueda actual.'}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {filteredAdrs.map(adr => {
                  const fresh = findFreshnessForAdr(adr);
                  const freshBadge = getFreshnessBadgeMeta(fresh?.status, fresh?.score);
                  const sigValid = fresh?.signature_valid ?? adr.signature_valid;
                  const tags = getTagsForItem(adr, fresh, fresh?.path || adr.id);
                  const isExpanded = Boolean(expandedAdrIds[adr.id]);

                  return (
                    <div
                      key={adr.id}
                      data-testid={`adr-card-${adr.id}`}
                      style={{
                        backgroundColor: 'var(--bg-surface)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-md)',
                        padding: '14px 16px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '10px',
                        transition: 'var(--transition)'
                      }}
                    >
                      <div
                        onClick={() => toggleAdrExpand(adr.id)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: '12px',
                          cursor: 'pointer',
                          flexWrap: 'wrap'
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0, flex: 1 }}>
                          <div style={{
                            width: '36px',
                            height: '36px',
                            borderRadius: 'var(--radius-md)',
                            backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                            color: 'var(--accent-primary)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            flexShrink: 0
                          }}>
                            <BookOpen size={16} />
                          </div>

                          <div style={{ minWidth: 0, flex: 1 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                              <span style={{
                                fontFamily: 'var(--font-mono)',
                                fontSize: '10.5px',
                                fontWeight: 700,
                                padding: '2px 7px',
                                borderRadius: 'var(--radius-sm)',
                                backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                                color: 'var(--accent-primary)'
                              }}>
                                {adr.id}
                              </span>
                              <span style={{ fontSize: '13.5px', fontWeight: 600, color: 'var(--text-primary)' }}>
                                {adr.title}
                              </span>
                            </div>

                            {adr.decision && (
                              <div style={{
                                fontSize: '12px',
                                color: 'var(--text-secondary)',
                                marginTop: '3px',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: isExpanded ? 'normal' : 'nowrap'
                              }}>
                                {adr.decision}
                              </div>
                            )}
                          </div>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', flexShrink: 0 }}>
                          {freshBadge && (
                            <span
                              className={freshBadge.cls}
                              title={`Commits posteriores: ${fresh.commits_after ?? 0} · Antigüedad: ${fresh.days_old ?? 0} días`}
                            >
                              {freshBadge.label}
                            </span>
                          )}
                          <SignatureBadge signatureValid={sigValid} />
                          {isExpanded ? <ChevronDown size={14} color="var(--text-muted)" /> : <ChevronRight size={14} color="var(--text-muted)" />}
                        </div>
                      </div>

                      {tags.length > 0 && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '5px', flexWrap: 'wrap', paddingLeft: '48px' }}>
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

                      {isExpanded && (
                        <div style={{
                          paddingLeft: '48px',
                          paddingTop: '8px',
                          borderTop: '1px solid var(--border-subtle)',
                          fontSize: '12px',
                          lineHeight: 1.55,
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '5px'
                        }}>
                          <div>
                            <strong style={{ color: 'var(--text-secondary)' }}>Contexto: </strong>
                            <span style={{ color: 'var(--text-primary)' }}>{adr.context || 'Sin especificar'}</span>
                          </div>
                          <div>
                            <strong style={{ color: 'var(--text-secondary)' }}>Decisión: </strong>
                            <span style={{ color: 'var(--text-primary)' }}>{adr.decision || 'Sin especificar'}</span>
                          </div>
                          {adr.consequences && (
                            <div>
                              <strong style={{ color: 'var(--text-secondary)' }}>Consecuencias: </strong>
                              <span style={{ color: 'var(--text-muted)' }}>{adr.consequences}</span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* SUBTAB 2: Skills & Fichas con estética uniforme */}
        {subTab === 'skills' && (
          <div style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-lg)',
            padding: '18px 20px',
            display: 'flex',
            flexDirection: 'column',
            gap: '12px',
            boxShadow: 'var(--shadow-sm)'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Cpu size={17} color="var(--accent-indigo)" />
              <h4 style={{ fontSize: '15px', fontWeight: 600, margin: 0 }}>
                Fichas de Dominio &amp; Skills ({filteredSkills.length})
              </h4>
            </div>

            {filteredSkills.length === 0 ? (
              <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
                {selectedTag === 'all' && !normalizedQuery
                  ? 'No hay fichas registradas.'
                  : 'Ninguna ficha coincide con el filtro actual.'}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {filteredSkills.map(sk => {
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
                        backgroundColor: 'var(--bg-surface)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-md)',
                        padding: '12px 16px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '12px',
                        flexWrap: 'wrap'
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0, flex: 1 }}>
                        <div style={{
                          width: '36px',
                          height: '36px',
                          borderRadius: 'var(--radius-md)',
                          backgroundColor: sk.area === 'backend'
                            ? 'rgba(var(--accent-indigo-rgb), 0.12)'
                            : 'rgba(var(--accent-primary-rgb), 0.12)',
                          color: sk.area === 'backend' ? 'var(--accent-indigo)' : 'var(--accent-primary)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          flexShrink: 0
                        }}>
                          <Cpu size={16} />
                        </div>

                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                            <span style={{ fontSize: '13.5px', fontWeight: 600, color: 'var(--text-primary)' }}>
                              {sk.title}
                            </span>
                            <span style={{
                              fontSize: '10px',
                              fontFamily: 'var(--font-mono)',
                              fontWeight: 700,
                              padding: '1px 6px',
                              borderRadius: 'var(--radius-sm)',
                              backgroundColor: sk.area === 'backend' ? 'rgba(var(--accent-indigo-rgb), 0.12)' : 'rgba(var(--accent-primary-rgb), 0.12)',
                              color: sk.area === 'backend' ? 'var(--accent-indigo)' : 'var(--accent-primary)'
                            }}>
                              {String(sk.area || 'okf').toUpperCase()}
                            </span>
                          </div>

                          <div style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '6px', marginTop: '3px', flexWrap: 'wrap' }}>
                            <FileCode size={11} />
                            <span style={{ fontFamily: 'var(--font-mono)' }}>{fresh?.path || expectedPath}</span>
                            {tags.slice(0, 3).map(t => (
                              <span key={t} className="mcp-tag-pill">#{t}</span>
                            ))}
                          </div>
                        </div>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                        {freshBadge && <span className={freshBadge.cls}>{freshBadge.label}</span>}
                        <SignatureBadge signatureValid={sigValid} />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* SUBTAB 3: Auditoría, Freshness & Carpetas */}
        {subTab === 'audit' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div style={{
              backgroundColor: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-lg)',
              padding: '18px 20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
              boxShadow: 'var(--shadow-sm)'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <div style={{
                    width: '34px',
                    height: '34px',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: 'rgba(var(--accent-primary-rgb), 0.12)',
                    color: 'var(--accent-primary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center'
                  }}>
                    {validation.valid ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
                  </div>
                  <div>
                    <h4 style={{ fontSize: '14.5px', fontWeight: 600, margin: 0 }}>
                      {validation.valid ? 'Especificación OKF Conforme' : 'Revisión de Conformidad OKF'}
                    </h4>
                    <span style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                      Métricas estructurales y sincronización de memoria huérfana
                    </span>
                  </div>
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
                    backgroundColor: 'var(--bg-surface)',
                    color: 'var(--text-primary)',
                    cursor: 'pointer'
                  }}
                >
                  <Wrench size={13} /> Sincronizar OKF
                </button>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '10px' }}>
                <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', padding: '12px', borderRadius: 'var(--radius-md)' }}>
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>ADRs Detectados</div>
                  <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'var(--font-mono)', marginTop: '2px' }}>
                    {validation.stats?.adrs_found || adrs.length || 0}
                  </div>
                </div>
                <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', padding: '12px', borderRadius: 'var(--radius-md)' }}>
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Historias de Usuario</div>
                  <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'var(--font-mono)', marginTop: '2px' }}>
                    {validation.stats?.user_stories_found || 0}
                  </div>
                </div>
                <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', padding: '12px', borderRadius: 'var(--radius-md)' }}>
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Features Backend</div>
                  <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'var(--font-mono)', marginTop: '2px' }}>
                    {validation.stats?.backend_features || 0}
                  </div>
                </div>
                {freshnessSummary && (
                  <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', padding: '12px', borderRadius: 'var(--radius-md)' }}>
                    <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Firmas Ed25519</div>
                    <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--accent-primary)', marginTop: '2px' }}>
                      {freshnessSummary.verified_signatures ?? 0}
                    </div>
                  </div>
                )}
              </div>

              {validation.errors?.length > 0 && (
                <div style={{ background: 'var(--bg-surface)', padding: '12px', borderRadius: 'var(--radius-md)', border: '1px solid rgba(239, 68, 68, 0.3)' }}>
                  <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--accent-danger)', marginBottom: '4px' }}>Observaciones:</div>
                  <ul style={{ fontSize: '11.5px', color: 'var(--accent-danger)', paddingLeft: '18px' }}>
                    {validation.errors.map((e, idx) => <li key={idx}>{e}</li>)}
                  </ul>
                </div>
              )}
            </div>

            {folders.length > 0 && (
              <div style={{
                backgroundColor: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-lg)',
                padding: '18px 20px',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px',
                boxShadow: 'var(--shadow-sm)'
              }}>
                <h5 style={{ fontSize: '14px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <FolderOpen size={15} color="var(--accent-primary)" /> Carpetas Activas OKF ({folders.length})
                </h5>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '8px' }}>
                  {folders.map(folder => (
                    <div
                      key={folder.path}
                      style={{
                        backgroundColor: 'var(--bg-surface)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-md)',
                        padding: '10px 12px',
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

            {filteredFreshnessItems.length > 0 && (
              <div style={{
                backgroundColor: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-lg)',
                padding: '18px 20px',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px',
                boxShadow: 'var(--shadow-sm)'
              }}>
                <h5 style={{ fontSize: '14px', fontWeight: 600, margin: 0 }}>
                  Reporte del Algoritmo Freshness ({filteredFreshnessItems.length} artefactos)
                </h5>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {filteredFreshnessItems.map(item => {
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
                          padding: '10px 12px',
                          borderRadius: 'var(--radius-md)',
                          backgroundColor: 'var(--bg-surface)',
                          border: '1px solid var(--border-subtle)',
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
