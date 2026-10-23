/** Правки, которые ещё не дошли до банка. Перед выходом из кабинета их сохраняют, пока вход ещё действует. */
const savers = new Set<() => Promise<void>>();

export function registerSaver(save: () => Promise<void>): () => void {
  savers.add(save);
  return () => {
    savers.delete(save);
  };
}

/** Сохраняет всё, что ждёт сохранения, но не дольше timeoutMs: выход из кабинета не должен зависнуть. */
export async function savePending(timeoutMs = 3000): Promise<void> {
  const all = Promise.allSettled([...savers].map((save) => save()));
  await Promise.race([all, new Promise((resolve) => window.setTimeout(resolve, timeoutMs))]);
}
