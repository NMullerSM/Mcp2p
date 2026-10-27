import React from 'react';
import { Plus, Link2, Trash2, ArrowRight, FolderKanban } from 'lucide-react';

export function ProjectsHub({ projects, onSelectProject, onNewProject, onJoinP2P, onDeleteProject }) {
  return (
    <section style={{ padding: '28px 36px', overflowY: 'auto', flex: 1, width: '100%' }}>
      {/* Cabecera del Hub */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '26px'
      }}>
        <div>
          <h2 style={{ fontSize: '22px', fontWeight: 700, margin: 0, color: 'var(--text-primary)', letterSpacing: '-0.3px' }}>
            Proyectos
          </h2>
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
              cursor: 'pointer',
              transition: 'var(--transition)'
            }}
          >
            <Link2 size={15} /> Unirse vía P2P
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
              cursor: 'pointer',
              boxShadow: 'var(--shadow-sm)',
              transition: 'var(--transition)'
            }}
          >
            <Plus size={16} /> Nuevo Proyecto
          </button>
        </div>
      </div>

      {/* Grid de Proyectos */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
        gap: '18px',
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
              padding: '18px 20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
              boxShadow: 'var(--shadow-sm)',
              cursor: 'pointer',
              transition: 'border-color 0.15s, transform 0.15s, box-shadow 0.15s'
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--accent-primary)';
              e.currentTarget.style.transform = 'translateY(-2px)';
              e.currentTarget.style.boxShadow = 'var(--shadow-md)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--border-subtle)';
              e.currentTarget.style.transform = 'translateY(0)';
              e.currentTarget.style.boxShadow = 'var(--shadow-sm)';
            }}
          >
            {/* Top row */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: 'var(--radius-md)',
                  backgroundColor: 'var(--bg-subtle)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'var(--accent-primary)',
                  flexShrink: 0
                }}>
                  <FolderKanban size={20} />
                </div>
                <div>
                  <h3 style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 2px 0' }}>
                    {proj.name}
                  </h3>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                    Creado {new Date(proj.created_at).toLocaleDateString()}
                  </span>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '10px',
                  backgroundColor: 'var(--bg-subtle)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--text-secondary)',
                  padding: '2px 7px',
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
                  <Trash2 size={14} />
                </button>
              </div>
            </div>

            {/* Footer con Acción */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', marginTop: 'auto', paddingTop: '4px' }}>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectProject(proj);
                }}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '6px 14px',
                  fontSize: '12px',
                  fontWeight: 600,
                  borderRadius: 'var(--radius-sm)',
                  border: 'none',
                  backgroundColor: 'var(--accent-primary)',
                  color: '#ffffff',
                  cursor: 'pointer'
                }}
              >
                Abrir Espacio <ArrowRight size={13} />
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
            minHeight: '130px',
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
          <Plus size={26} />
          <span style={{ fontSize: '14px', fontWeight: 600 }}>Crear Nuevo Proyecto</span>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Nuevo espacio de trabajo</span>
        </div>
      </div>
    </section>
  );
}
