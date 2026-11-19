import React, { useEffect, useRef } from 'react';
import { HELP_SECTIONS } from './helpTexts';
import { DeliverySettingsSection } from './DeliverySettingsSection';
import { CliMeasurementSettingsSection } from './CliMeasurementSettingsSection';
import { SessionRestoreSettingsSection } from './SessionRestoreSettingsSection';
import { ConfirmationPrefsSection } from './ConfirmationPrefsSection';
import { NodesSettingsSection } from './NodesSettingsSection';

const SLOT_COMPONENTS = {
  'ajustes-entrega': DeliverySettingsSection,
  'ajustes-medicion': CliMeasurementSettingsSection,
  'ajustes-sesiones': SessionRestoreSettingsSection,
  'ajustes-confirmaciones': ConfirmationPrefsSection,
  'ajustes-nodos': NodesSettingsSection
};

/**
 * Pestaña «Terminales y relevo» de Ajustes: reúne la ayuda veraz de cada mecanismo
 * con anclas fijas y reserva los huecos de configuración para los paquetes de la ola 2.
 */
export function TerminalsRelaySettings({ api, initialAnchor = null }) {
  const sectionRefs = useRef({});
  const headingRefs = useRef({});

  useEffect(() => {
    if (!initialAnchor) return;
    const normalizedId = sectionRefs.current[initialAnchor]
      ? initialAnchor
      : (sectionRefs.current[`ayuda-${initialAnchor}`] ? `ayuda-${initialAnchor}` : null);
    if (!normalizedId) return;

    const sectionEl = sectionRefs.current[normalizedId];
    const headingEl = headingRefs.current[normalizedId];
    if (sectionEl && typeof sectionEl.scrollIntoView === 'function') {
      sectionEl.scrollIntoView({ block: 'start' });
    }
    if (headingEl && typeof headingEl.focus === 'function') {
      try {
        headingEl.focus({ preventScroll: true });
      } catch {
        headingEl.focus();
      }
    }
  }, [initialAnchor]);

  return (
    <div
      data-testid="terminals-relay-settings"
      style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}
    >
      <div>
        <h4 style={{ fontSize: '13.5px', fontWeight: 700, margin: '0 0 4px 0', color: 'var(--text-primary)' }}>
          Terminales y relevo
        </h4>
        <p style={{ fontSize: '11.5px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.45 }}>
          Referencia rápida del funcionamiento de las terminales, la entrega de mensajes, los roles y las preferencias.
        </p>
      </div>

      {HELP_SECTIONS.map((section) => {
        const SlotComponent = section.slotId ? SLOT_COMPONENTS[section.slotId] : null;
        return (
          <section
            key={section.id}
            id={section.id}
            ref={(el) => { sectionRefs.current[section.id] = el; }}
            style={{
              backgroundColor: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-md)',
              padding: '12px 14px',
              display: 'flex',
              flexDirection: 'column',
              gap: '6px'
            }}
          >
            <h5
              ref={(el) => { headingRefs.current[section.id] = el; }}
              tabIndex={-1}
              style={{
                fontSize: '12.5px',
                fontWeight: 700,
                color: 'var(--text-primary)',
                margin: 0,
                outline: 'none'
              }}
            >
              {section.title}
            </h5>
            <p
              style={{
                fontSize: '12px',
                color: 'var(--text-secondary)',
                margin: 0,
                lineHeight: 1.5
              }}
            >
              {section.body}
            </p>
            {section.slotId && SlotComponent && (
              <div id={section.slotId} data-testid={section.slotId}>
                <SlotComponent api={api} />
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
