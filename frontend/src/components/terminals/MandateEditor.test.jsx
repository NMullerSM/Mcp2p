// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React, { useState } from 'react';
import { MandateEditor } from './MandateEditor';
import { MAX_MANDATE_CHARS } from './roleModel';

afterEach(cleanup);

describe('MandateEditor', () => {
  it('counts_and_caps_2000', () => {
    function Harness() {
      const [val, setVal] = useState('');
      return <MandateEditor value={val} onChange={setVal} />;
    }

    render(<Harness />);
    const textarea = screen.getByTestId('mandate-textarea');
    const counter = screen.getByTestId('mandate-counter');
    const warning = screen.getByTestId('mandate-warning');

    expect(counter.textContent).toBe(`0 / ${MAX_MANDATE_CHARS}`);
    expect(warning.textContent).toContain('No pongas secretos: el mandato se pasa al CLI al lanzarlo');
    expect(warning.textContent).toContain('El mandato es una instrucción, no un candado');
    expect(textarea.getAttribute('maxlength')).toBe(String(MAX_MANDATE_CHARS));

    const overLimit = 'a'.repeat(2050);
    fireEvent.change(textarea, { target: { value: overLimit } });

    expect(textarea.value).toHaveLength(MAX_MANDATE_CHARS);
    expect(counter.textContent).toBe(`${MAX_MANDATE_CHARS} / ${MAX_MANDATE_CHARS}`);
  });

  it('strips_nothing_client_side_but_shows_server_value', () => {
    const onChange = vi.fn();
    const rawInput = '  trabaja en wt/login\x07\x1b[31m\nno toques src/core  ';
    const { rerender } = render(<MandateEditor value="" onChange={onChange} />);

    const textarea = screen.getByTestId('mandate-textarea');
    fireEvent.change(textarea, { target: { value: rawInput } });

    // El cliente no limpia ni recorta caracteres por su cuenta: envia el texto tal cual al estado/backend.
    expect(onChange).toHaveBeenCalledWith(rawInput);

    // Cuando el servidor devuelve el valor saneado, el editor lo muestra fielmente.
    const serverSanitized = '  trabaja en wt/login[31m\nno toques src/core  ';
    rerender(<MandateEditor value={serverSanitized} onChange={onChange} />);
    expect(screen.getByTestId('mandate-textarea').value).toBe(serverSanitized);
    expect(screen.getByTestId('mandate-counter').textContent).toBe(
      `${serverSanitized.length} / ${MAX_MANDATE_CHARS}`
    );
  });
});
