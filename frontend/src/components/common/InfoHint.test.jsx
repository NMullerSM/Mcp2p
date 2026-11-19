// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import React from 'react';
import { InfoHint } from './InfoHint';

describe('InfoHint', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('does_not_show_before_delay', () => {
    render(
      <InfoHint
        topic="Auto-Relay"
        text="Deja avisos de tareas asignadas en la bandeja de este agente."
        anchor="ayuda-auto-relay"
        testId="hint-auto-relay"
      />
    );
    const btn = screen.getByTestId('hint-auto-relay');
    fireEvent.mouseEnter(btn);

    act(() => {
      vi.advanceTimersByTime(699);
    });
    expect(screen.queryByRole('tooltip')).toBeNull();

    // Si sale antes de los 700 ms, cancela el temporizador y nunca aparece.
    fireEvent.mouseLeave(btn);
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('shows_after_700ms_on_hover', () => {
    render(
      <InfoHint
        topic="Auto-Relay"
        text="Deja avisos de tareas asignadas en la bandeja de este agente."
        anchor="ayuda-auto-relay"
        testId="hint-auto-relay"
      />
    );
    const btn = screen.getByTestId('hint-auto-relay');
    fireEvent.mouseEnter(btn);

    act(() => {
      vi.advanceTimersByTime(700);
    });
    const tooltip = screen.getByRole('tooltip');
    expect(tooltip.textContent).toContain('Deja avisos de tareas asignadas');

    fireEvent.mouseLeave(btn);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('shows_after_delay_on_keyboard_focus_and_hides_on_escape', () => {
    render(
      <InfoHint
        topic="Modo trabajador"
        text="El agente espera mensajes en su bandeja y responde solo. Gasta turnos mientras espera."
        anchor="ayuda-modo-trabajador"
        testId="hint-worker"
      />
    );
    const btn = screen.getByTestId('hint-worker');
    fireEvent.focus(btn);

    act(() => {
      vi.advanceTimersByTime(699);
    });
    expect(screen.queryByRole('tooltip')).toBeNull();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(screen.getByRole('tooltip')).toBeTruthy();

    fireEvent.keyDown(btn, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('touch_first_tap_shows_without_navigating', () => {
    const OriginalPointerEvent = window.PointerEvent;
    class TestPointerEvent extends MouseEvent {
      constructor(type, props = {}) {
        super(type, props);
        this.pointerType = props.pointerType || '';
      }
    }
    window.PointerEvent = TestPointerEvent;
    try {
      const onOpenSettings = vi.fn();
      render(
        <InfoHint
          topic="Auto-Relay"
          text="Deja avisos de tareas asignadas en la bandeja de este agente."
          anchor="ayuda-auto-relay"
          onOpenSettings={onOpenSettings}
          testId="hint-touch"
        />
      );
      const btn = screen.getByTestId('hint-touch');
      fireEvent.pointerDown(btn, { pointerType: 'touch' });
      fireEvent.click(btn);

      expect(onOpenSettings).not.toHaveBeenCalled();
      const tooltip = screen.getByRole('tooltip');
      expect(tooltip).toBeTruthy();

      const moreInfo = screen.getByRole('button', { name: 'Más información' });
      fireEvent.click(moreInfo);
      expect(onOpenSettings).toHaveBeenCalledTimes(1);
      expect(onOpenSettings).toHaveBeenCalledWith('terminals_relay', 'ayuda-auto-relay');
    } finally {
      window.PointerEvent = OriginalPointerEvent;
    }
  });

  it('click_opens_settings_with_tab_and_anchor', () => {
    const onOpenSettings = vi.fn();
    render(
      <InfoHint
        topic="Modo trabajador"
        text="El agente espera mensajes en su bandeja y responde solo."
        anchor="ayuda-modo-trabajador"
        onOpenSettings={onOpenSettings}
        testId="hint-click"
      />
    );
    const btn = screen.getByTestId('hint-click');
    expect(btn.getAttribute('aria-label')).toBe('Ayuda: Modo trabajador');

    fireEvent.pointerDown(btn, { pointerType: 'mouse' });
    fireEvent.click(btn);
    expect(onOpenSettings).toHaveBeenNthCalledWith(1, 'terminals_relay', 'ayuda-modo-trabajador');

    fireEvent.keyDown(btn, { key: 'Enter' });
    expect(onOpenSettings).toHaveBeenNthCalledWith(2, 'terminals_relay', 'ayuda-modo-trabajador');

    fireEvent.keyDown(btn, { key: ' ' });
    expect(onOpenSettings).toHaveBeenNthCalledWith(3, 'terminals_relay', 'ayuda-modo-trabajador');
  });

  it('tooltip_has_role_and_aria_describedby_while_visible', () => {
    render(
      <InfoHint
        topic="Auto-Relay"
        text="Deja avisos de tareas asignadas en la bandeja de este agente."
        anchor="ayuda-auto-relay"
        testId="hint-aria"
      />
    );
    const btn = screen.getByTestId('hint-aria');
    expect(btn.getAttribute('aria-describedby')).toBeNull();

    fireEvent.mouseEnter(btn);
    act(() => {
      vi.advanceTimersByTime(700);
    });

    const tooltip = screen.getByRole('tooltip');
    expect(tooltip.id).toBeTruthy();
    expect(btn.getAttribute('aria-describedby')).toBe(tooltip.id);

    fireEvent.mouseLeave(btn);
    expect(btn.getAttribute('aria-describedby')).toBeNull();
  });

  it('tooltip_width_never_exceeds_viewport_at_375px', () => {
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { value: 375, configurable: true, writable: true });

    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
      if (this.getAttribute('role') === 'tooltip') {
        return { top: 60, left: 330, right: 570, bottom: 120, width: 240, height: 60 };
      }
      return { top: 30, left: 330, right: 350, bottom: 50, width: 20, height: 20 };
    });

    try {
      render(
        <InfoHint
          topic="Auto-Relay"
          text="Deja avisos de tareas asignadas en la bandeja de este agente."
          anchor="ayuda-auto-relay"
          testId="hint-375"
        />
      );
      const btn = screen.getByTestId('hint-375');
      fireEvent.mouseEnter(btn);
      act(() => {
        vi.advanceTimersByTime(700);
      });

      const tooltip = screen.getByRole('tooltip');
      expect(tooltip.style.maxWidth).toBe('min(240px, calc(100vw - 32px))');
      // 375 - 240 - 16 = 119; desde left=330 debe desplazarse a la izquierda (-211px) para no salir de 375px.
      expect(tooltip.style.transform).toBe('translateX(-211px)');
    } finally {
      rectSpy.mockRestore();
      Object.defineProperty(window, 'innerWidth', { value: originalInnerWidth, configurable: true, writable: true });
    }
  });
});
