/** Правки, которые ещё не дошли до банка. Перед выходом из кабинета их сохраняют, пока вход ещё действует. */
const savers = new Set<() => Promise<void>>();

export function registerSaver(save: () => Promise<void>): () => void {
  savers.add(save);
  return () => {
    savers.delete(save);
  };
}

/** Ошибка или таймаут отменяют выход: вызывающий код сохраняет сессию и открытую форму. */
export async function savePending(timeoutMs = 3000): Promise<void> {
  let timer: number | undefined;
  try {
    await Promise.race([
      Promise.all([...savers].map((save) => save())),
      new Promise<never>((_, reject) => {
        timer = window.setTimeout(() => reject(new Error("Сохранение ещё не завершилось")), timeoutMs);
      }),
    ]);
  } finally {
    window.clearTimeout(timer);
  }
}
