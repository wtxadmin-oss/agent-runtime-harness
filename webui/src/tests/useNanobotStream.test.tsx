import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useNanobotStream } from "@/hooks/useNanobotStream";
import type { InboundEvent } from "@/lib/types";
import { ClientProvider, useClient } from "@/providers/ClientProvider";

function fakeClient() {
  const handlers = new Map<string, Set<(ev: InboundEvent) => void>>();
  return {
    client: {
      status: "open" as const,
      defaultChatId: null as string | null,
      onStatus: () => () => {},
      onError: () => () => {},
      onChat(chatId: string, h: (ev: InboundEvent) => void) {
        let set = handlers.get(chatId);
        if (!set) {
          set = new Set();
          handlers.set(chatId, set);
        }
        set.add(h);
        return () => set!.delete(h);
      },
      sendMessage: vi.fn(),
      newChat: vi.fn(),
      attach: vi.fn(),
      connect: vi.fn(),
      close: vi.fn(),
      updateUrl: vi.fn(),
    },
    emit(chatId: string, ev: InboundEvent) {
      const set = handlers.get(chatId);
      set?.forEach((h) => h(ev));
    },
  };
}

function wrap(client: ReturnType<typeof fakeClient>["client"]) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <ClientProvider
        client={client as unknown as import("@/lib/nanobot-client").NanobotClient}
        token="tok"
      >
        {children}
      </ClientProvider>
    );
  };
}

describe("useNanobotStream", () => {
  it("collapses consecutive tool_hint frames into one trace row", () => {
    const fake = fakeClient();
    const { result } = renderHook(() => useNanobotStream("chat-t", []), {
      wrapper: wrap(fake.client),
    });

    act(() => {
      fake.emit("chat-t", {
        event: "message",
        chat_id: "chat-t",
        text: 'weather("get")',
        kind: "tool_hint",
      });
      fake.emit("chat-t", {
        event: "message",
        chat_id: "chat-t",
        text: 'search "hk weather"',
        kind: "tool_hint",
      });
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].kind).toBe("trace");
    expect(result.current.messages[0].role).toBe("tool");
    expect(result.current.messages[0].traces).toEqual([
      'weather("get")',
      'search "hk weather"',
    ]);

    act(() => {
      fake.emit("chat-t", {
        event: "message",
        chat_id: "chat-t",
        text: "## Summary",
      });
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].role).toBe("assistant");
    expect(result.current.messages[1].kind).toBeUndefined();
  });

  it("sends thinking params only when model supports thinking and recipe is ready", () => {
    const fake = fakeClient();
    const { result } = renderHook(() => {
      const stream = useNanobotStream("chat-t", []);
      const clientCtx = useClient();
      return { stream, clientCtx };
    }, {
      wrapper: wrap(fake.client),
    });

    act(() => {
      result.current.clientCtx.setThinkingEnabled(true);
    });

    act(() => {
      // Missing recipe: thinking not ready.
      fake.emit("chat-t", {
        event: "attached",
        chat_id: "chat-t",
        thinking_supported: true,
        thinking_recipe_ready: false,
        thinking_unavailable_reason: "missing_recipe",
      });
    });
    // Hook-level assertion: send without thinking metadata when gate not met.
    act(() => {
      result.current.stream.send("hello");
    });
    expect(fake.client.sendMessage).toHaveBeenLastCalledWith(
      "chat-t",
      "hello",
      undefined,
      undefined,
      undefined,
    );

    // Recipe still not ready, so thinking params must not be sent.
    act(() => {
      result.current.stream.send("still plain");
    });
    expect(fake.client.sendMessage).toHaveBeenLastCalledWith(
      "chat-t",
      "still plain",
      undefined,
      undefined,
      undefined,
    );

    act(() => {
      fake.emit("chat-t", {
        event: "model_selected",
        chat_id: "chat-t",
        thinking_supported: true,
        thinking_recipe_ready: true,
        thinking_unavailable_reason: null,
      });
      result.current.clientCtx.setThinkingEnabled(true);
      result.current.clientCtx.setReasoningEffort("max");
    });

    act(() => {
      result.current.stream.send("now think");
    });
    expect(fake.client.sendMessage).toHaveBeenLastCalledWith(
      "chat-t",
      "now think",
      undefined,
      { enabled: true, effort: "max" },
      undefined,
    );
  });

  it("tracks thinking recipe progress/conflict and clears on preview", () => {
    const fake = fakeClient();
    const { result } = renderHook(() => useNanobotStream("chat-t", []), {
      wrapper: wrap(fake.client),
    });

    act(() => {
      fake.emit("chat-t", {
        event: "thinking_recipe_progress",
        chat_id: "chat-t",
        stage: "extracting",
        message: "extracting",
      });
      fake.emit("chat-t", {
        event: "thinking_recipe_conflict_detected",
        chat_id: "chat-t",
        decision: "url",
        scoring: { url: { credibility: 0.9 } },
        evidence: [{ kind: "url", source: "https://example.com/docs" }],
      });
    });

    expect(result.current.thinkingRecipeProgress?.stage).toBe("extracting");
    expect(result.current.thinkingRecipeConflict?.decision).toBe("url");

    act(() => {
      fake.emit("chat-t", {
        event: "thinking_recipe_preview",
        chat_id: "chat-t",
        preview_id: "pv-1",
        recipe: { controls: { enabled_param: "thinking.enabled" } },
      });
    });

    expect(result.current.thinkingRecipeProgress).toBeNull();
    expect(result.current.thinkingRecipeConflict?.decision).toBe("url");
  });
});
