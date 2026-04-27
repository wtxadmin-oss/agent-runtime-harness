import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildSessionKey,
  createProfile,
  deleteProfile,
  deleteSession,
  fetchSessionMessages,
  listProfiles,
  parseSessionKey,
} from "@/lib/api";

describe("webui API helpers", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ deleted: true, key: "websocket:chat-1", messages: [] }),
      }),
    );
  });

  it("percent-encodes websocket keys when fetching session history", async () => {
    await fetchSessionMessages("tok", "websocket:chat-1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/websocket%3Achat-1/messages",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("percent-encodes websocket keys when deleting a session", async () => {
    await deleteSession("tok", "websocket:chat-1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/websocket%3Achat-1/delete",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("parses and builds profile-scoped websocket keys", () => {
    expect(parseSessionKey("websocket:demo_bob:chat-1")).toEqual({
      channel: "websocket",
      profileId: "demo_bob",
      chatId: "chat-1",
    });
    expect(buildSessionKey("demo_bob", "chat-1")).toBe(
      "websocket:demo_bob:chat-1",
    );
  });

  it("calls profile list endpoint with bearer token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          profiles: [{ id: "demo_alice", name: "Alice" }],
          current_profile_id: "demo_alice",
        }),
      }),
    );

    await listProfiles("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/profiles",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("percent-encodes profile name when creating a profile", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          profile: { id: "charlie-user", name: "Charlie User" },
        }),
      }),
    );

    await createProfile("tok", "Charlie User");

    expect(fetch).toHaveBeenCalledWith(
      "/api/profiles/create?name=Charlie%20User",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("percent-encodes profile id when deleting a profile", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          deleted: true,
          profile_id: "charlie-user",
        }),
      }),
    );

    await deleteProfile("tok", "charlie-user");

    expect(fetch).toHaveBeenCalledWith(
      "/api/profiles/delete?profile_id=charlie-user",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });
});
