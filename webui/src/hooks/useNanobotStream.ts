import { useCallback, useEffect, useRef, useState } from "react";

import { useClient } from "@/providers/ClientProvider";
import type { StreamError } from "@/lib/nanobot-client";
import type {
  InboundEvent,
  OutboundMedia,
  UIImage,
  UIMessage,
} from "@/lib/types";

interface StreamBuffer {
  /** ID of the assistant message currently receiving deltas. */
  messageId: string;
  /** Sequence of deltas accumulated in order. */
  parts: string[];
  /** Accumulated reasoning/thinking content. */
  reasoningParts: string[];
}

/**
 * Subscribe to a chat by ID. Returns the in-memory message list for the chat,
 * a streaming flag, and a ``send`` function. Initial history must be seeded
 * separately (e.g. via ``fetchSessionMessages``) since the server only replays
 * live events.
 */
/** Payload passed to ``send`` when the user attaches one or more images.
 *
 * ``media`` is handed to the wire client verbatim; ``preview`` powers the
 * optimistic user bubble (blob URLs so the preview appears before the server
 * acks the frame). Keeping the two separate lets the bubble re-use the local
 * blob URL even after the server persists the file under a different name. */
export interface SendImage {
  media: OutboundMedia;
  preview: UIImage;
}

export interface SendOptions {
  forceDisableThinking?: boolean;
}

export interface ThinkingRecipePreview {
  previewId: string;
  recipe?: Record<string, unknown>;
  evidence?: Record<string, unknown>[];
  modelName?: string;
  baseUrl?: string;
}

export interface ThinkingRecipeProgress {
  stage: "submitted" | "extracting" | "compiling" | "preview_ready";
  message?: string;
  meta?: Record<string, unknown>;
}

export interface ThinkingRecipeConflict {
  modelName?: string;
  baseUrl?: string;
  decision?: string;
  scoring?: Record<string, unknown>;
  evidence?: Record<string, unknown>[];
}

export function useNanobotStream(
  chatId: string | null,
  initialMessages: UIMessage[] = [],
): {
  messages: UIMessage[];
  isStreaming: boolean;
  send: (content: string, images?: SendImage[], options?: SendOptions) => void;
  setMessages: React.Dispatch<React.SetStateAction<UIMessage[]>>;
  /** Latest transport-level fault raised since the last ``dismissStreamError``.
   * ``null`` when there is nothing to show. */
  streamError: StreamError | null;
  /** Clear the current ``streamError`` (e.g. after the user dismisses the
   * notification or starts a fresh action). */
  dismissStreamError: () => void;
  thinkingRecipeRequired: boolean;
  clearThinkingRecipeRequired: () => void;
  thinkingRecipePreview: ThinkingRecipePreview | null;
  clearThinkingRecipePreview: () => void;
  thinkingRecipeSaved: boolean;
  clearThinkingRecipeSaved: () => void;
  thinkingRecipeError: string | null;
  clearThinkingRecipeError: () => void;
  thinkingRecipeProgress: ThinkingRecipeProgress | null;
  clearThinkingRecipeProgress: () => void;
  thinkingRecipeConflict: ThinkingRecipeConflict | null;
  clearThinkingRecipeConflict: () => void;
} {
  const {
    client,
    thinkingEnabled,
    reasoningEffort,
    selectedModelId,
    setSelectedModelId,
    setThinkingSupported,
    setThinkingRecipeReady,
    setThinkingUnavailableReason,
    thinkingSupported,
    thinkingRecipeReady,
  } = useClient();
  const [messages, setMessages] = useState<UIMessage[]>(initialMessages);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamError, setStreamError] = useState<StreamError | null>(null);
  const [thinkingRecipeRequired, setThinkingRecipeRequired] = useState(false);
  const [thinkingRecipePreview, setThinkingRecipePreview] = useState<ThinkingRecipePreview | null>(null);
  const [thinkingRecipeSaved, setThinkingRecipeSaved] = useState(false);
  const [thinkingRecipeError, setThinkingRecipeError] = useState<string | null>(null);
  const [thinkingRecipeProgress, setThinkingRecipeProgress] = useState<ThinkingRecipeProgress | null>(null);
  const [thinkingRecipeConflict, setThinkingRecipeConflict] = useState<ThinkingRecipeConflict | null>(null);
  const buffer = useRef<StreamBuffer | null>(null);

  useEffect(() => {
    return client.onError((err) => setStreamError(err));
  }, [client]);

  const dismissStreamError = useCallback(() => setStreamError(null), []);
  const clearThinkingRecipeRequired = useCallback(() => setThinkingRecipeRequired(false), []);
  const clearThinkingRecipePreview = useCallback(() => setThinkingRecipePreview(null), []);
  const clearThinkingRecipeSaved = useCallback(() => setThinkingRecipeSaved(false), []);
  const clearThinkingRecipeError = useCallback(() => setThinkingRecipeError(null), []);
  const clearThinkingRecipeProgress = useCallback(() => setThinkingRecipeProgress(null), []);
  const clearThinkingRecipeConflict = useCallback(() => setThinkingRecipeConflict(null), []);

  // Reset local state when switching chats. ``streamError`` is scoped to the
  // send that triggered it, so a chat swap should wipe it out: a stale
  // "Message too large" banner on a freshly-opened chat-B would confuse the
  // user about which send actually failed (and in which chat).
  useEffect(() => {
    setMessages(initialMessages);
    setIsStreaming(false);
    setStreamError(null);
    setThinkingRecipeRequired(false);
    setThinkingRecipePreview(null);
    setThinkingRecipeSaved(false);
    setThinkingRecipeError(null);
    setThinkingRecipeProgress(null);
    setThinkingRecipeConflict(null);
    buffer.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId]);

  useEffect(() => {
    if (!chatId) return;

    const handle = (ev: InboundEvent) => {
      if (ev.event === "reasoning_delta") {
        // Reasoning chunk: create or update the assistant message's reasoning field.
        const id = buffer.current?.messageId ?? crypto.randomUUID();
        if (!buffer.current) {
          buffer.current = { messageId: id, parts: [], reasoningParts: [] };
          setMessages((prev) => [
            ...prev,
            {
              id,
              role: "assistant",
              content: "",
              isStreaming: true,
              isThinking: true,
              reasoning: "",
              createdAt: Date.now(),
            },
          ]);
          setIsStreaming(true);
        }
        buffer.current.reasoningParts.push(ev.text);
        const combinedReasoning = buffer.current.reasoningParts.join("");
        const targetId = buffer.current.messageId;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === targetId ? { ...m, reasoning: combinedReasoning, isThinking: true } : m,
          ),
        );
        return;
      }

      if (ev.event === "delta") {
        const id = buffer.current?.messageId ?? crypto.randomUUID();
        if (!buffer.current) {
          buffer.current = { messageId: id, parts: [], reasoningParts: [] };
          setMessages((prev) => [
            ...prev,
            {
              id,
              role: "assistant",
              content: "",
              isStreaming: true,
              createdAt: Date.now(),
            },
          ]);
          setIsStreaming(true);
        }
        // When content starts arriving, thinking phase is done
        const targetId = buffer.current.messageId;
        buffer.current.parts.push(ev.text);
        const combined = buffer.current.parts.join("");
        setMessages((prev) =>
          prev.map((m) => (m.id === targetId ? { ...m, content: combined, isThinking: false } : m)),
        );
        return;
      }

      if (ev.event === "stream_end") {
        if (!buffer.current) {
          setIsStreaming(false);
          return;
        }
        const finalId = buffer.current.messageId;
        buffer.current = null;
        setIsStreaming(false);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === finalId ? { ...m, isStreaming: false, isThinking: false } : m,
          ),
        );
        return;
      }

      if (ev.event === "message") {
        // Intermediate agent breadcrumbs (tool-call hints, raw progress).
        // Attach them to the last trace row if it was the last emitted item
        // so a sequence of calls collapses into one compact trace group.
        if (ev.kind === "tool_hint" || ev.kind === "progress") {
          const line = ev.text;
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.kind === "trace" && !last.isStreaming) {
              const merged: UIMessage = {
                ...last,
                traces: [...(last.traces ?? [last.content]), line],
                content: line,
              };
              return [...prev.slice(0, -1), merged];
            }
            return [
              ...prev,
              {
                id: crypto.randomUUID(),
                role: "tool",
                kind: "trace",
                content: line,
                traces: [line],
                createdAt: Date.now(),
              },
            ];
          });
          return;
        }

        // A complete (non-streamed) assistant message. If a stream was in
        // flight, drop the placeholder so we don't render the text twice.
        const activeId = buffer.current?.messageId;
        buffer.current = null;
        setIsStreaming(false);
        setMessages((prev) => {
          const filtered = activeId ? prev.filter((m) => m.id !== activeId) : prev;
          return [
            ...filtered,
            {
              id: crypto.randomUUID(),
              role: "assistant",
              content: ev.text,
              createdAt: Date.now(),
            },
          ];
        });
        return;
      }
      if (ev.event === "attached") {
        setSelectedModelId(ev.selected_model_id ?? null);
        setThinkingSupported(Boolean(ev.thinking_supported));
        setThinkingRecipeReady(Boolean(ev.thinking_recipe_ready));
        setThinkingUnavailableReason(ev.thinking_unavailable_reason ?? null);
        return;
      }
      if (ev.event === "model_selected") {
        if (ev.chat_id === chatId) {
          setSelectedModelId(ev.selected_model_id ?? null);
          setThinkingSupported(Boolean(ev.thinking_supported));
          setThinkingRecipeReady(Boolean(ev.thinking_recipe_ready));
          setThinkingUnavailableReason(ev.thinking_unavailable_reason ?? null);
        }
        return;
      }
      if (ev.event === "thinking_recipe_required") {
        if (ev.chat_id === chatId) {
          setThinkingRecipeRequired(true);
          setThinkingRecipeSaved(false);
          setThinkingRecipePreview(null);
          setThinkingRecipeError(null);
          setThinkingRecipeProgress(null);
          setThinkingRecipeConflict(null);
          setThinkingRecipeReady(false);
          setThinkingUnavailableReason(ev.reason ?? "missing_recipe");
        }
        return;
      }
      if (ev.event === "thinking_recipe_progress") {
        if (ev.chat_id === chatId) {
          setThinkingRecipeProgress({
            stage: ev.stage,
            message: ev.message,
            meta: ev.meta,
          });
        }
        return;
      }
      if (ev.event === "thinking_recipe_conflict_detected") {
        if (ev.chat_id === chatId) {
          setThinkingRecipeConflict({
            modelName: ev.model_name,
            baseUrl: ev.base_url,
            decision: ev.decision,
            scoring: ev.scoring,
            evidence: ev.evidence,
          });
        }
        return;
      }
      if (ev.event === "thinking_recipe_preview") {
        if (ev.chat_id === chatId) {
          setThinkingRecipePreview({
            previewId: ev.preview_id,
            recipe: ev.recipe,
            evidence: ev.evidence,
            modelName: ev.model_name,
            baseUrl: ev.base_url,
          });
          setThinkingRecipeError(null);
          setThinkingRecipeSaved(false);
          setThinkingRecipeProgress(null);
        }
        return;
      }
      if (ev.event === "thinking_recipe_saved") {
        if (ev.chat_id === chatId) {
          setThinkingRecipeSaved(true);
          setThinkingRecipeRequired(false);
          setThinkingRecipePreview(null);
          setThinkingRecipeError(null);
          setThinkingRecipeProgress(null);
          setThinkingRecipeConflict(null);
          setThinkingRecipeReady(true);
          setThinkingUnavailableReason(null);
        }
        return;
      }
      if (ev.event === "thinking_recipe_error") {
        if (!ev.chat_id || ev.chat_id === chatId) {
          setThinkingRecipeError(ev.detail ?? "unknown_error");
          setThinkingRecipeSaved(false);
          setThinkingRecipeProgress(null);
          if (ev.detail === "model_does_not_support_thinking") {
            setThinkingSupported(false);
            setThinkingRecipeReady(false);
            setThinkingUnavailableReason(ev.detail);
          }
        }
        return;
      }
      // ``attached`` / ``error`` frames aren't actionable here; the client
      // shell handles them separately.
    };

    const unsub = client.onChat(chatId, handle);
    return () => {
      unsub();
      buffer.current = null;
    };
  }, [
    chatId,
    client,
    setSelectedModelId,
    setThinkingRecipeReady,
    setThinkingSupported,
    setThinkingUnavailableReason,
  ]);

  const send = useCallback(
    (content: string, images?: SendImage[], options?: SendOptions) => {
      if (!chatId) return;
      const hasImages = !!images && images.length > 0;
      // Text is optional when images are attached — the agent will still see
      // the image blocks via ``media`` paths.
      if (!hasImages && !content.trim()) return;

      const previews = hasImages ? images!.map((i) => i.preview) : undefined;
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "user",
          content,
          createdAt: Date.now(),
          ...(previews ? { images: previews } : {}),
        },
      ]);
      const wireMedia = hasImages ? images!.map((i) => i.media) : undefined;
      const canSendThinking = thinkingEnabled
        && thinkingSupported
        && thinkingRecipeReady
        && !options?.forceDisableThinking;
      const thinkingParam = canSendThinking
        ? { enabled: true, effort: reasoningEffort }
        : undefined;
      client.sendMessage(chatId, content, wireMedia, thinkingParam, selectedModelId ?? undefined);
    },
    [
      chatId,
      client,
      reasoningEffort,
      selectedModelId,
      thinkingEnabled,
      thinkingRecipeReady,
      thinkingSupported,
    ],
  );

  return {
    messages,
    isStreaming,
    send,
    setMessages,
    streamError,
    dismissStreamError,
    thinkingRecipeRequired,
    clearThinkingRecipeRequired,
    thinkingRecipePreview,
    clearThinkingRecipePreview,
    thinkingRecipeSaved,
    clearThinkingRecipeSaved,
    thinkingRecipeError,
    clearThinkingRecipeError,
    thinkingRecipeProgress,
    clearThinkingRecipeProgress,
    thinkingRecipeConflict,
    clearThinkingRecipeConflict,
  };
}
