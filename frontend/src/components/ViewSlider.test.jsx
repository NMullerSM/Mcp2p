// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import React from 'react';
import { ViewSliderProvider, ViewSliderContent, ViewSliderTrigger, useViewSlider } from './ViewSlider';

afterEach(cleanup);

function Harness({ viewKey = 'a', suspended = false, children }) {
  return (
    <ViewSliderProvider viewKey={viewKey} suspended={suspended}>
      <button type="button" data-testid="outside-btn">fuera</button>
      <ViewSliderContent title="Panel de prueba" subtitle="sub" data-testid="demo-slider">
        {children ?? (
          <div>
            <label>
              Campo
              <input data-testid="demo-input" />
            </label>
            <button type="button" data-testid="demo-action">Accion</button>
          </div>
        )}
      </ViewSliderContent>
    </ViewSliderProvider>
  );
}

const tab = () => screen.getByTestId('view-slider-tab');
const isOpen = () => tab().getAttribute('aria-expanded') === 'true';

describe('ViewSlider', () => {
  it('muestra siempre la pestana con tooltip y aria-label, y el panel arranca cerrado', () => {
    render(<Harness />);
    expect(tab().getAttribute('aria-label')).toBe('Abrir panel lateral: Panel de prueba');
    expect(tab().getAttribute('title')).toBe('Abrir panel lateral: Panel de prueba');
    expect(isOpen()).toBe(false);
    expect(screen.getByTestId('view-slider-panel').getAttribute('aria-hidden')).toBe('true');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('abre y cierra con clic en la pestana', () => {
    render(<Harness />);
    fireEvent.click(tab());
    expect(isOpen()).toBe(true);
    expect(screen.getByRole('dialog', { name: 'Panel de prueba' })).toBeTruthy();
    expect(tab().getAttribute('aria-label')).toBe('Cerrar panel lateral: Panel de prueba');
    fireEvent.click(tab());
    expect(isOpen()).toBe(false);
  });

  it('cierra con Esc, con clic fuera (fondo) y con el boton Cerrar', () => {
    render(<Harness />);

    fireEvent.click(tab());
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(isOpen()).toBe(false);

    fireEvent.click(tab());
    fireEvent.click(screen.getByTestId('demo-slider-backdrop'));
    expect(isOpen()).toBe(false);

    fireEvent.click(tab());
    fireEvent.click(screen.getByLabelText('Cerrar panel'));
    expect(isOpen()).toBe(false);
  });

  it('Esc no hace nada si el panel esta cerrado', () => {
    render(<Harness />);
    const evt = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    document.dispatchEvent(evt);
    expect(evt.defaultPrevented).toBe(false);
    expect(isOpen()).toBe(false);
  });

  it('gestiona el foco: al abrir va al boton Cerrar, con Tab queda atrapado, y al cerrar vuelve a la pestana', () => {
    render(<Harness />);
    tab().focus();
    fireEvent.click(tab());
    const closeBtn = screen.getByLabelText('Cerrar panel');
    expect(document.activeElement).toBe(closeBtn);

    // Shift+Tab desde el primero salta al ultimo; Tab desde el ultimo vuelve al primero.
    fireEvent.keyDown(closeBtn, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(screen.getByTestId('demo-action'));
    fireEvent.keyDown(screen.getByTestId('demo-action'), { key: 'Tab' });
    expect(document.activeElement).toBe(closeBtn);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.activeElement).toBe(tab());
  });

  it('resetea el scroll interno cada vez que se abre', () => {
    render(<Harness />);
    const scroller = screen.getByTestId('view-slider-panel').querySelector('.mcp-view-slider-scroll');
    fireEvent.click(tab());
    scroller.scrollTop = 120;
    fireEvent.click(tab());
    fireEvent.click(tab());
    expect(scroller.scrollTop).toBe(0);
  });

  it('el estado abierto es por vista: al cambiar viewKey el panel vuelve a estar cerrado', () => {
    const { rerender } = render(<Harness viewKey="a" />);
    fireEvent.click(tab());
    expect(isOpen()).toBe(true);
    rerender(<Harness viewKey="b" />);
    expect(isOpen()).toBe(false);
    rerender(<Harness viewKey="a" />);
    expect(isOpen()).toBe(false);
  });

  it('suspended cierra el panel y oculta la pestana (p. ej. mientras esta abierto el detalle de tarjeta)', () => {
    const { rerender } = render(<Harness />);
    fireEvent.click(tab());
    rerender(<Harness suspended />);
    expect(isOpen()).toBe(false);
    expect(screen.getByTestId('demo-slider').style.visibility).toBe('hidden');
    rerender(<Harness />);
    expect(screen.getByTestId('demo-slider').style.visibility).toBe('visible');
  });

  it('el contenido mantiene su estado React al cerrar y reabrir (no se desmonta)', () => {
    render(<Harness />);
    fireEvent.click(tab());
    fireEvent.change(screen.getByTestId('demo-input'), { target: { value: 'borrador' } });
    fireEvent.click(tab());
    fireEvent.click(tab());
    expect(screen.getByTestId('demo-input').value).toBe('borrador');
  });

  it('un slot registrado sin hijos (Terminales) muestra el titulo y un aviso de vacio', () => {
    render(
      <ViewSliderProvider viewKey="terminals">
        <ViewSliderContent title="Terminales" />
      </ViewSliderProvider>
    );
    expect(tab().getAttribute('aria-label')).toBe('Abrir panel lateral: Terminales');
    expect(screen.getByTestId('view-slider-empty')).toBeTruthy();
  });

  it('useViewSlider permite abrir y cerrar por codigo', () => {
    function Opener() {
      const { open, close, isOpen: o } = useViewSlider();
      return (
        <>
          <button type="button" data-testid="opener" onClick={open}>abrir</button>
          <button type="button" data-testid="closer" onClick={close}>cerrar</button>
          <span data-testid="state">{String(o)}</span>
        </>
      );
    }
    render(
      <ViewSliderProvider viewKey="x">
        <Opener />
        <ViewSliderContent title="T"><p>hola</p></ViewSliderContent>
      </ViewSliderProvider>
    );
    fireEvent.click(screen.getByTestId('opener'));
    expect(screen.getByTestId('state').textContent).toBe('true');
    expect(isOpen()).toBe(true);
    fireEvent.click(screen.getByTestId('closer'));
    expect(screen.getByTestId('state').textContent).toBe('false');
  });

  it('fuera de un provider es inocuo: no pinta nada y las acciones no fallan', () => {
    function Lonely() {
      const { open, isOpen: o } = useViewSlider();
      open();
      return <span data-testid="lonely">{String(o)}</span>;
    }
    render(
      <>
        <Lonely />
        <ViewSliderContent title="X"><p data-testid="never">no</p></ViewSliderContent>
      </>
    );
    expect(screen.getByTestId('lonely').textContent).toBe('false');
    expect(screen.queryByTestId('never')).toBeNull();
  });

  it('tras el cierre el panel sale del orden de tabulacion (visibility hidden) cuando acaba la animacion', () => {
    vi.useFakeTimers();
    try {
      render(<Harness />);
      const panel = screen.getByTestId('view-slider-panel');
      fireEvent.click(tab());
      expect(panel.style.visibility).toBe('inherit');
      fireEvent.click(tab());
      expect(panel.style.visibility).toBe('inherit'); // aun animando la salida
      act(() => { vi.advanceTimersByTime(600); });
      expect(panel.style.visibility).toBe('hidden');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('ViewSliderTrigger (boton en la barra superior)', () => {
  function WithTrigger({ suspended = false }) {
    return (
      <ViewSliderProvider viewKey="a" suspended={suspended}>
        <div data-testid="toolbar"><ViewSliderTrigger /></div>
        <ViewSliderContent title="Panel de prueba" data-testid="demo-slider">
          <button type="button" data-testid="demo-action">Accion</button>
        </ViewSliderContent>
      </ViewSliderProvider>
    );
  }

  it('reemplaza la pestana pegada al borde: hay un solo view-slider-tab y vive en la barra', () => {
    render(<WithTrigger />);
    expect(screen.getAllByTestId('view-slider-tab')).toHaveLength(1);
    expect(screen.getByTestId('toolbar').contains(tab())).toBe(true);
    expect(screen.getByTestId('demo-slider').querySelector('.mcp-view-slider-tab')).toBeNull();
    expect(tab().getAttribute('aria-controls')).toBe(screen.getByTestId('view-slider-panel').id);
  });

  it('abre y cierra el panel, y devuelve el foco al boton al cerrar con Esc', () => {
    render(<WithTrigger />);
    expect(isOpen()).toBe(false);
    fireEvent.click(tab());
    expect(isOpen()).toBe(true);
    expect(tab().getAttribute('aria-label')).toBe('Cerrar panel lateral: Panel de prueba');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(isOpen()).toBe(false);
    expect(document.activeElement).toBe(tab());
  });

  it('se oculta mientras la vista esta suspendida (detalle de tarjeta abierto)', () => {
    render(<WithTrigger suspended />);
    expect(tab().style.visibility).toBe('hidden');
  });
});
