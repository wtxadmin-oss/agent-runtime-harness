import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatSummary } from "@/lib/types";

const connectSpy = vi.fn();
const refreshSpy = vi.fn();
const createChatSpy = vi.fn().mockResolvedValue("chat-1");
const deleteChatSpy = vi.fn();
let mockSessions: ChatSummary[] = [];

vi.mock("@/hooks/useSessions", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("@/hooks/useSessions")>();
  return {
    ...actual,
    useSessions: () => {
      const [sessions, setSessions] = React.useState(mockSessions);
      return {
        sessions,
        loading: false,
        error: null,
        refresh: refreshSpy,
        createChat: createChatSpy,
        deleteChat: async (key: string) => {
          await deleteChatSpy(key);
          setSessions((prev: ChatSummary[]) => prev.filter((s) => s.key !== key));
        },
      };
    },
  };
});

vi.mock("@/hooks/useTheme", () => ({
  useTheme: () => ({
    theme: "light" as const,
    toggle: vi.fn(),
  }),
}));

vi.mock("@/lib/bootstrap", () => ({
  fetchBootstrap: vi.fn().mockResolvedValue({
    token: "tok",
    ws_path: "/",
    expires_in: 300,
  }),
  deriveWsUrl: vi.fn(() => "ws://test"),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    createProfile: vi.fn(),
    deleteProfile: vi.fn(),
  };
});

vi.mock("@/lib/nanobot-client", () => {
  class MockClient {
    status = "idle" as const;
    defaultChatId: string | null = null;
    connect = connectSpy;
    onStatus = () => () => {};
    onError = () => () => {};
    onChat = () => () => {};
    sendMessage = vi.fn();
    newChat = vi.fn();
    attach = vi.fn();
    close = vi.fn();
    updateUrl = vi.fn();
  }

  return { NanobotClient: MockClient };
});

import App from "@/App";
import { fetchBootstrap } from "@/lib/bootstrap";
import { createProfile, deleteProfile } from "@/lib/api";

const fetchBootstrapMock = vi.mocked(fetchBootstrap);
const createProfileMock = vi.mocked(createProfile);
const deleteProfileMock = vi.mocked(deleteProfile);

describe("App layout", () => {
  beforeEach(() => {
    mockSessions = [];
    connectSpy.mockClear();
    refreshSpy.mockReset();
    createChatSpy.mockClear();
    deleteChatSpy.mockReset();
    createProfileMock.mockReset();
    deleteProfileMock.mockReset();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
      }),
    );
  });

  it("keeps sidebar layout out of the main thread width contract", async () => {
    const { container } = render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());

    const main = container.querySelector("main");
    expect(main).toBeInTheDocument();
    expect(main).not.toHaveAttribute("style");

    const asideClassNames = Array.from(container.querySelectorAll("aside")).map(
      (el) => el.className,
    );
    expect(asideClassNames.some((cls) => cls.includes("lg:block"))).toBe(true);
  });

  it("switches to the next session when deleting the active chat", async () => {
    mockSessions = [
      {
        key: "websocket:chat-a",
        channel: "websocket",
        chatId: "chat-a",
        createdAt: "2026-04-16T10:00:00Z",
        updatedAt: "2026-04-16T10:00:00Z",
        preview: "First chat",
      },
      {
        key: "websocket:chat-b",
        channel: "websocket",
        chatId: "chat-b",
        createdAt: "2026-04-16T11:00:00Z",
        updatedAt: "2026-04-16T11:00:00Z",
        preview: "Second chat",
      },
    ];

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^First chat$/ })).toBeInTheDocument(),
    );

    fireEvent.pointerDown(screen.getByLabelText("Chat actions for First chat"), {
      button: 0,
    });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));

    await waitFor(() =>
      expect(screen.getByText('Delete “First chat”?')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(deleteChatSpy).toHaveBeenCalledWith("websocket:chat-a"),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /^Second chat$/ }),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText('Delete “First chat”?')).not.toBeInTheDocument();
    expect(document.body.style.pointerEvents).not.toBe("none");
  }, 15_000);

  it("can switch bob then switch back to alice", async () => {
    fetchBootstrapMock.mockImplementation(async (_base, profileId) => ({
      token: profileId === "demo_bob" ? "tok-bob" : "tok-alice",
      ws_path: "/",
      expires_in: 300,
      profile_id: profileId ?? "demo_alice",
      profiles: [
        { id: "demo_alice", name: "Alice" },
        { id: "demo_bob", name: "Bob" },
      ],
    }));

    render(<App />);
    await waitFor(() => expect(connectSpy).toHaveBeenCalled());

    fireEvent.pointerDown(screen.getByLabelText("Select profile"), {
      button: 0,
    });
    fireEvent.click(screen.getByRole("menuitem", { name: "Bob" }));
    await waitFor(() =>
      expect(fetchBootstrapMock).toHaveBeenCalledWith("", "demo_bob"),
    );

    fireEvent.pointerDown(screen.getByLabelText("Select profile"), {
      button: 0,
    });
    fireEvent.click(screen.getByRole("menuitem", { name: "Alice" }));
    await waitFor(() =>
      expect(fetchBootstrapMock).toHaveBeenCalledWith("", "demo_alice"),
    );
  });

  it("creates a user profile and switches to it", async () => {
    createProfileMock.mockResolvedValue({ id: "charlie-user", name: "Charlie" });
    fetchBootstrapMock.mockImplementation(async (_base, profileId) => ({
      token:
        profileId === "charlie-user"
          ? "tok-charlie"
          : profileId === "demo_bob"
            ? "tok-bob"
            : "tok-alice",
      ws_path: "/",
      expires_in: 300,
      profile_id: profileId ?? "demo_alice",
      profiles:
        profileId === "charlie-user"
          ? [
            { id: "demo_alice", name: "Alice" },
            { id: "demo_bob", name: "Bob" },
            { id: "charlie-user", name: "Charlie" },
          ]
          : [
            { id: "demo_alice", name: "Alice" },
            { id: "demo_bob", name: "Bob" },
          ],
    }));

    render(<App />);
    await waitFor(() => expect(connectSpy).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText("Create user"));
    fireEvent.change(screen.getByPlaceholderText("Username"), {
      target: { value: "Charlie" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(createProfileMock).toHaveBeenCalledWith("tok-alice", "Charlie"));
    await waitFor(() =>
      expect(fetchBootstrapMock).toHaveBeenCalledWith("", "charlie-user"),
    );
  });

  it("deletes another profile and reconnects current profile", async () => {
    deleteProfileMock.mockResolvedValue({ deleted: true, profileId: "demo_bob" });
    fetchBootstrapMock.mockImplementation(async (_base, profileId) => ({
      token: "tok-alice",
      ws_path: "/",
      expires_in: 300,
      profile_id: profileId ?? "demo_alice",
      profiles: [
        { id: "demo_alice", name: "Alice" },
        { id: "demo_bob", name: "Bob" },
      ],
    }));

    render(<App />);
    await waitFor(() => expect(connectSpy).toHaveBeenCalled());

    fireEvent.pointerDown(screen.getByLabelText("Delete user"), {
      button: 0,
    });
    fireEvent.click(screen.getByRole("menuitem", { name: "Bob" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(deleteProfileMock).toHaveBeenCalledWith("tok-alice", "demo_bob"),
    );
    await waitFor(() =>
      expect(fetchBootstrapMock).toHaveBeenCalledWith("", "demo_alice"),
    );
  });
});
