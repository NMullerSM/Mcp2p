import React from 'react';
import { Plus, Link2, Trash2, ArrowRight, FolderGit2, BookText } from 'lucide-react';

export function ProjectsHub({ projects, onSelectProject, onNewProject, onJoinP2P, onDeleteProject }) {
  return (
    <section style={{ padding: '28px 36px', overflowY: 'auto', flex: 1 }}>
      {/* Cabecera del Hub */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '26px'
      }}>
        <div>
          <h2 style={{ fontSize: '20px', fontWeight: 700, margin: '0 0 4px 0' }}>
            Tus Proyectos & Espacios de Trabajo
          </h2>
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: 0 }}>
            Cada proyecto opera con doble repositorio aislado: código (<code>app_{'{nombre}'}</code>) y gobernanza (<code>okf_{'{nombre}_{id}'}</code>).
          </p>
        </div>

        <div style={{ display: 'flex', gap: '10px' }}>
          <button
            onClick={onJoinP2P}
            title="Unirse a un proyecto mediante enlace P2P"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '8px 14px',
              fontSize: '13px',
              fontWeight: 500,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-card)',
              color: 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            <Link2 size={16} /> Unirse vía P2P
          </button>

          <button
            onClick={onNewProject}
            title="Crear Nuevo Proyecto (Ctrl+P)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '8px 14px',
              fontSize: '13px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: 'pointer'
            }}
          >
            <Plus size={16} /> Nuevo Proyecto
          </button>
        </div>
      </div>

      {/* Grid de Proyectos */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))',
        gap: '20px',
        paddingBottom: '30px'
      }}>
        {projects.map((proj) => (
          <div
            key={proj.project_uuid}
            onDoubleClick={() => onSelectProject(proj)}
            style={{
              backgroundColor: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-lg)',
              padding: '20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
              boxShadow: 'var(--shadow-sm)',
              cursor: 'pointer',
              transition: 'var(--transition)'
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--border-focus)';
              e.currentTarget.style.transform = 'translateY(-2px)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--border-subtle)';
              e.currentTarget.style.transform = 'translateY(0)';
            }}
          >
            {/* Top row */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '10px' }}>
              <div>
                <h3 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 2px 0' }}>
                  {proj.name}
                </h3>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                  Creado: {new Date(proj.created_at).toLocaleDateString()}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '11px',
                  backgroundColor: 'var(--bg-subtle)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--accent-primary)',
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-sm)',
                  fontWeight: 600
                }}>
                  {proj.project_uuid}
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteProject(proj);
                  }}
                  title="Eliminar Proyecto"
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--text-muted)',
                    cursor: 'pointer',
                    padding: '4px',
                    borderRadius: 'var(--radius-sm)',
                    display: 'flex',
                    alignItems: 'center'
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = '#ef4444'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-muted)'; }}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>

            {/* Doble Repositorio info */}
            <div style={{
              backgroundColor: 'var(--bg-subtle)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-md)',
              padding: '10px 12px',
              display: 'flex',
              flexDirection: 'column',
              gap: '6px',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-secondary)' }}>
                <FolderGit2 size={13} color="var(--accent-primary)" />
                <span style={{
                  fontSize: '10px',
                  fontWeight: 700,
                  padding: '1px 5px',
                  borderRadius: '3px',
                  backgroundColor: 'rgba(79, 70, 229, 0.15)',
                  color: 'var(--accent-primary)'
                }}>
                  APP
                </span>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {proj.code_repo_path ? proj.code_repo_path.split('/').pop() : 'app/'}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-secondary)' }}>
                <BookText size={13} color="var(--accent-success)" />
                <span style={{
                  fontSize: '10px',
                  fontWeight: 700,
                  padding: '1px 5px',
                  borderRadius: '3px',
                  backgroundColor: 'rgba(16, 185, 129, 0.15)',
                  color: 'var(--accent-success)'
                }}>
                  OKF
                </span>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {proj.gov_repo_path ? proj.gov_repo_path.split('/').pop() : 'governance/'}
                </span>
              </div>
            </div>

            {/* Footer */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 'auto' }}>
              <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                orphan-conversa-memory-{proj.project_uuid.slice(-6)}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectProject(proj);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  padding: '6px 12px',
                  fontSize: '12px',
                  fontWeight: 600,
                  borderRadius: 'var(--radius-sm)',
                  border: 'none',
                  backgroundColor: 'var(--accent-primary)',
                  color: '#ffffff',
                  cursor: 'pointer'
                }}
              >
                Abrir Proyecto <ArrowRight size={13} />
              </button>
            </div>
          </div>
        ))}

        {/* Tarjeta Nuevo Proyecto */}
        <div
          onClick={onNewProject}
          style={{
            border: '2px dashed var(--border-subtle)',
            borderRadius: 'var(--radius-lg)',
            padding: '24px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            cursor: 'pointer',
            minHeight: '180px',
            color: 'var(--text-secondary)',
            transition: 'var(--transition)'
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = 'var(--accent-primary)';
            e.currentTarget.style.color = 'var(--accent-primary)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = 'var(--border-subtle)';
            e.currentTarget.style.color = 'var(--text-secondary)';
          }}
        >
          <Plus size={28} />
          <span style={{ fontSize: '14px', fontWeight: 600 }}>Crear Nuevo Proyecto</span>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Doble Repositorio + OKF Aislado</span>
        </div>
      </div>
    </section>
  );
}
