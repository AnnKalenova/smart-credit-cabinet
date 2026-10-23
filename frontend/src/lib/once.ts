import { useCallback, useRef } from "react";

/** Действие не запускается второй раз, пока первое не закончилось: двойной клик или Enter не дублируют запрос. */
export function useSingleFlight(): (action: () => Promise<unknown> | void) => Promise<void> {
  const busy = useRef(false);
  return useCallback(async (action: () => Promise<unknown> | void) => {
    if (busy.current) return;
    busy.current = true;
    try {
      await action();
    } finally {
      busy.current = false;
    }
  }, []);
}
