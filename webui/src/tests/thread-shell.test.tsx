import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThreadShell } from "@/components/thread/ThreadShell";
import { ClientProvider } from "@/providers/ClientProvider";

function makeClient() {
  const errorHandlers = new Set<(err: { kind: string }) => void>();
  const chatHandlers = new Map<string, Set<(ev: import("@/lib/types").InboundEvent) => void>>();
  return {
    status: "open" as const,
    defaultChatId: null as string | null,
    onStatus: () => () => {},
    onChat: (chatId: string, handler: (ev: import("@/lib/types").InboundEvent) => void) => {
      let set = chatHandlers.get(chatId);
      if (!set) {
        set = new Set();
        chatHandlers.set(chatId, set);
      }
      set.add(handler);
      return () => {
        set!.delete(handler);
      };
    },
    onError: (handler: (err: { kind: string }) => void) => {
      errorHandlers.add(handler);
      return () => {
        errorHandlers.delete(handler);
      };
    },
    _emitError(err: { kind: string }) {
      for (const h of errorHandlers) h(err);
    },
    _emitChat(chatId: string, ev: import("@/lib/types").InboundEvent) {
      for (const h of chatHandlers.get(chatId) ?? []) h(ev);
    },
    setThinkingToggle: vi.fn(),
    submitThinkingRecipe: vi.fn(),
    confirmThinkingRecipe: vi.fn(),
    sendMessage: vi.fn(),
    newChat: vi.fn(),
    attach: vi.fn(),
    connect: vi.fn(),
    close: vi.fn(),
    updateUrl: vi.fn(),
  };
}

function wrap(client: ReturnType<typeof makeClient>, children: ReactNode) {
  return (
    <ClientProvider
      client={client as unknown as import("@/lib/nanobot-client").NanobotClient}
      token="tok"
    >
      {children}
    </ClientProvider>
  );
}

function session(chatId: string) {
  return {
    key: `websocket:${chatId}`,
    channel: "websocket" as const,
    chatId,
    createdAt: null,
    updatedAt: null,
    preview: "",
  };
}

function httpJson(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

describe("ThreadShell", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({}),
      }),
    );
  });

  it("restores in-memory messages when switching away and back to a session", async () => {
    const client = makeClient();
    const onNewChat = vi.fn().mockResolvedValue("chat-a");

    const { rerender } = render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-a")}
          title="Chat chat-a"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={onNewChat}
        />,
      ),
    );

    fireEvent.change(screen.getByLabelText("Message input"), {
      target: { value: "persist me across tabs" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() =>
      expect(client.sendMessage).toHaveBeenCalledWith(
        "chat-a",
        "persist me across tabs",
        undefined,
        undefined,
        undefined,
      ),
    );
    expect(screen.getByText("persist me across tabs")).toBeInTheDocument();

    await act(async () => {
      rerender(
        wrap(
          client,
          <ThreadShell
            session={session("chat-b")}
            title="Chat chat-b"
            onToggleSidebar={() => {}}
            onGoHome={() => {}}
            onNewChat={onNewChat}
          />,
        ),
      );
    });

    await act(async () => {
      rerender(
        wrap(
          client,
          <ThreadShell
            session={session("chat-a")}
            title="Chat chat-a"
            onToggleSidebar={() => {}}
            onGoHome={() => {}}
            onNewChat={onNewChat}
          />,
        ),
      );
    });

    expect(screen.getByText("persist me across tabs")).toBeInTheDocument();
  });

  it("clears the old thread when the active session is removed", async () => {
    const client = makeClient();
    const onNewChat = vi.fn().mockResolvedValue("chat-a");

    const { rerender } = render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-a")}
          title="Chat chat-a"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={onNewChat}
        />,
      ),
    );

    fireEvent.change(screen.getByLabelText("Message input"), {
      target: { value: "delete me cleanly" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() =>
      expect(client.sendMessage).toHaveBeenCalledWith(
        "chat-a",
        "delete me cleanly",
        undefined,
        undefined,
        undefined,
      ),
    );
    expect(screen.getByText("delete me cleanly")).toBeInTheDocument();

    await act(async () => {
      rerender(
        wrap(
          client,
          <ThreadShell
            session={null}
            title="nanobot"
            onToggleSidebar={() => {}}
            onGoHome={() => {}}
            onNewChat={onNewChat}
          />,
        ),
      );
    });

    await waitFor(() => {
      expect(screen.queryByText("delete me cleanly")).not.toBeInTheDocument();
    });
    expect(screen.getByPlaceholderText("What's on your mind?")).toBeInTheDocument();
  });

  it("does not leak the previous thread when opening a brand-new chat", async () => {
    const client = makeClient();
    const onNewChat = vi.fn().mockResolvedValue("chat-new");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("websocket%3Achat-a/messages")) {
          return httpJson({
            key: "websocket:chat-a",
            created_at: null,
            updated_at: null,
            messages: [
              { role: "user", content: "old question" },
              { role: "assistant", content: "old answer" },
            ],
          });
        }
        return {
          ok: false,
          status: 404,
          json: async () => ({}),
        };
      }),
    );

    const { rerender } = render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-a")}
          title="Chat chat-a"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={onNewChat}
        />,
      ),
    );

    await waitFor(() => expect(screen.getByText("old answer")).toBeInTheDocument());

    await act(async () => {
      rerender(
        wrap(
          client,
          <ThreadShell
            session={session("chat-new")}
            title="Chat chat-new"
            onToggleSidebar={() => {}}
            onGoHome={() => {}}
            onNewChat={onNewChat}
          />,
        ),
      );
    });

    expect(screen.queryByText("old answer")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByPlaceholderText("What's on your mind?")).toBeInTheDocument(),
    );
    const input = screen.getByPlaceholderText("What's on your mind?");
    expect(input.className).toContain("min-h-[96px]");
    expect(screen.queryByText("old answer")).not.toBeInTheDocument();
  });

  it("surfaces a dismissible banner when the stream reports message_too_big", async () => {
    const client = makeClient();
    const onNewChat = vi.fn().mockResolvedValue("chat-a");

    render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-a")}
          title="Chat chat-a"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={onNewChat}
        />,
      ),
    );

    // No banner yet: only appears once the client emits a matching error.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await act(async () => {
      client._emitError({ kind: "message_too_big" });
    });

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Message too large");

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  it("clears the stream error banner when the user switches to another chat", async () => {
    const client = makeClient();
    const onNewChat = vi.fn().mockResolvedValue("chat-a");

    const { rerender } = render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-a")}
          title="Chat chat-a"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={onNewChat}
        />,
      ),
    );

    await act(async () => {
      client._emitError({ kind: "message_too_big" });
    });
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    // Switch to a different chat. The banner was about the *previous* send
    // in chat-a; it must not leak into chat-b's view.
    await act(async () => {
      rerender(
        wrap(
          client,
          <ThreadShell
            session={session("chat-b")}
            title="Chat chat-b"
            onToggleSidebar={() => {}}
            onGoHome={() => {}}
            onNewChat={onNewChat}
          />,
        ),
      );
    });

    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  it("clears the previous thread immediately while the next session loads", async () => {
    const client = makeClient();
    const onNewChat = vi.fn().mockResolvedValue("chat-b");
    let resolveChatB:
      | ((value: { ok: boolean; status: number; json: () => Promise<unknown> }) => void)
      | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("websocket%3Achat-a/messages")) {
          return Promise.resolve(
            httpJson({
              key: "websocket:chat-a",
              created_at: null,
              updated_at: null,
              messages: [{ role: "assistant", content: "from chat a" }],
            }),
          );
        }
        if (url.includes("websocket%3Achat-b/messages")) {
          return new Promise((resolve) => {
            resolveChatB = resolve;
          });
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          json: async () => ({}),
        });
      }),
    );

    const { rerender } = render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-a")}
          title="Chat chat-a"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={onNewChat}
        />,
      ),
    );

    await waitFor(() => expect(screen.getByText("from chat a")).toBeInTheDocument());

    await act(async () => {
      rerender(
        wrap(
          client,
          <ThreadShell
            session={session("chat-b")}
            title="Chat chat-b"
            onToggleSidebar={() => {}}
            onGoHome={() => {}}
            onNewChat={onNewChat}
          />,
        ),
      );
    });

    expect(screen.queryByText("from chat a")).not.toBeInTheDocument();
    expect(screen.getByText("Loading conversation…")).toBeInTheDocument();

    await act(async () => {
      resolveChatB?.(
        httpJson({
          key: "websocket:chat-b",
          created_at: null,
          updated_at: null,
          messages: [{ role: "assistant", content: "from chat b" }],
        }),
      );
    });

    await waitFor(() => expect(screen.getByText("from chat b")).toBeInTheDocument());
    expect(screen.queryByText("from chat a")).not.toBeInTheDocument();
  });

  it("disables thinking toggle when recipe is missing and sends without thinking params", async () => {
    const client = makeClient();
    const onNewChat = vi.fn().mockResolvedValue("chat-a");

    render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-a")}
          title="Chat chat-a"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={onNewChat}
        />,
      ),
    );

    await act(async () => {
      client._emitChat("chat-a", {
        event: "attached",
        chat_id: "chat-a",
        thinking_supported: true,
        thinking_recipe_ready: false,
        thinking_unavailable_reason: "missing_recipe",
      });
    });

    const toggle = screen.getByRole("button", { name: "深度思考模式" });
    expect(toggle).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Message input"), {
      target: { value: "send without thinking" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() =>
      expect(client.sendMessage).toHaveBeenCalledWith(
        "chat-a",
        "send without thinking",
        undefined,
        undefined,
        undefined,
      ),
    );
  });

  it("completes recipe config flow then enables thinking and sends with thinking params", async () => {
    const client = makeClient();
    const onNewChat = vi.fn().mockResolvedValue("chat-a");

    render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-a")}
          title="Chat chat-a"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={onNewChat}
        />,
      ),
    );

    await act(async () => {
      client._emitChat("chat-a", {
        event: "attached",
        chat_id: "chat-a",
        thinking_supported: true,
        thinking_recipe_ready: false,
        thinking_unavailable_reason: "missing_recipe",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "配置" }));
    fireEvent.change(screen.getByLabelText("官方 URL"), {
      target: { value: "https://api.deepseek.com/docs/reasoning" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交并预览" }));
    expect(client.submitThinkingRecipe).toHaveBeenCalledWith("chat-a", {
      doc_url: "https://api.deepseek.com/docs/reasoning",
    });

    await act(async () => {
      client._emitChat("chat-a", {
        event: "thinking_recipe_preview",
        chat_id: "chat-a",
        preview_id: "pv-1",
        model_name: "deepseek-v4-flash",
        base_url: "https://api.deepseek.com",
        recipe: { controls: { enabled_param: "thinking.enabled" } },
        evidence: [{ id: "e1", type: "url", source: "https://api.deepseek.com/docs/reasoning" }],
      });
    });
    expect(await screen.findByText("确认思考参数预览")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认并保存" }));
    expect(client.confirmThinkingRecipe).toHaveBeenCalledWith("chat-a", "pv-1");

    await act(async () => {
      client._emitChat("chat-a", {
        event: "thinking_recipe_saved",
        chat_id: "chat-a",
        model_signature: "https://api.deepseek.com::deepseek-v4-flash",
      });
    });

    const toggle = screen.getByRole("button", { name: "深度思考模式" });
    expect(toggle).not.toBeDisabled();
    fireEvent.click(toggle);
    expect(client.setThinkingToggle).toHaveBeenCalledWith("chat-a", true);

    fireEvent.change(screen.getByLabelText("Message input"), {
      target: { value: "please keep thinking on" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() =>
      expect(client.sendMessage).toHaveBeenCalledWith(
        "chat-a",
        "please keep thinking on",
        undefined,
        { enabled: true, effort: "high" },
        undefined,
      ),
    );
  });

  it("renders recipe progress and conflict hints during config flow", async () => {
    const client = makeClient();
    const onNewChat = vi.fn().mockResolvedValue("chat-a");

    render(
      wrap(
        client,
        <ThreadShell
          session={session("chat-a")}
          title="Chat chat-a"
          onToggleSidebar={() => {}}
          onGoHome={() => {}}
          onNewChat={onNewChat}
        />,
      ),
    );

    await act(async () => {
      client._emitChat("chat-a", {
        event: "attached",
        chat_id: "chat-a",
        thinking_supported: true,
        thinking_recipe_ready: false,
        thinking_unavailable_reason: "missing_recipe",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "配置" }));
    await act(async () => {
      client._emitChat("chat-a", {
        event: "thinking_recipe_progress",
        chat_id: "chat-a",
        stage: "extracting",
        message: "extracting url and snippet evidence",
      });
      client._emitChat("chat-a", {
        event: "thinking_recipe_conflict_detected",
        chat_id: "chat-a",
        decision: "url",
        scoring: { url: { credibility: 0.9 } },
        evidence: [{ kind: "url", source: "https://example.com/docs" }],
      });
    });

    expect(screen.getByText(/提取进度: extracting/)).toBeInTheDocument();
    expect(screen.getByText(/检测到 URL 与 snippet 参数冲突/)).toBeInTheDocument();

    await act(async () => {
      client._emitChat("chat-a", {
        event: "thinking_recipe_preview",
        chat_id: "chat-a",
        preview_id: "pv-2",
        recipe: { controls: { enabled_param: "enable_thinking" } },
        evidence: [{ id: "e1", type: "url", source: "https://example.com/docs" }],
      });
    });

    expect(await screen.findByText("确认思考参数预览")).toBeInTheDocument();
    expect(screen.getByText(/冲突处理决策: url/)).toBeInTheDocument();
  });
});
