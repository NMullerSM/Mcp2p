import { useState, useEffect } from 'react';

export function usePyWebView() {
  const [isReady, setIsReady] = useState(false);
  const [api, setApi] = useState(null);

  useEffect(() => {
    function init() {
      if (window.pywebview && window.pywebview.api) {
        setApi(window.pywebview.api);
        setIsReady(true);
      }
    }

    if (window.pywebview && window.pywebview.api) {
      init();
    } else {
      window.addEventListener('pywebviewready', init);
    }

    return () => {
      window.removeEventListener('pywebviewready', init);
    };
  }, []);

  return { isReady, api };
}
