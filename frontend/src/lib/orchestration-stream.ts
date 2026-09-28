import { streamOrchestrationRunEvents } from "@/lib/api";

export type OrchestrationEvent = {
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  task_id: string | null;
};

/**
 * Reads the run's SSE event stream (events_bus.py's persist-then-publish wire format - see
 * api.ts's streamOrchestrationRunEvents) until a terminal event, `[DONE]`, or `signal` aborts.
 * Same fetch+reader parsing shape as chat-stream-runtime.ts's runStreamLoop (no native
 * EventSource: it can't send the Authorization header the backend requires), simplified for a
 * single page-scoped consumer rather than a cross-page persistent registry.
 */
export async function consumeOrchestrationEvents(
  projectId: string,
  runId: string,
  options: {
    afterSeq?: number;
    signal: AbortSignal;
    onEvent: (event: OrchestrationEvent) => void;
    onError?: (message: string) => void;
  },
): Promise<void> {
  try {
    const response = await streamOrchestrationRunEvents(projectId, runId, {
      afterSeq: options.afterSeq,
      signal: options.signal,
    });
    if (!response.ok) {
      throw new Error(`Поток событий недоступен (код ${response.status})`);
    }
    if (!response.body) throw new Error("Пустой ответ сервера");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let partial = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      partial += decoder.decode(value, { stream: true });
      const lines = partial.split("\n\n");
      partial = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const raw = line.slice("data: ".length);
        if (raw === "[DONE]") return;
        try {
          options.onEvent(JSON.parse(raw) as OrchestrationEvent);
        } catch {
          // Malformed chunk - skip it rather than aborting the whole live tail.
        }
      }
    }
  } catch (err) {
    if (options.signal.aborted) return;
    options.onError?.(err instanceof Error ? err.message : "Соединение с потоком событий прервано");
  }
}
