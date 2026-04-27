import type { ChatSummary, ProfileSummary } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(
  url: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(url, {
    ...(init ?? {}),
    headers: {
      ...(init?.headers ?? {}),
      Authorization: `Bearer ${token}`,
    },
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw new ApiError(res.status, `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export function parseSessionKey(
  key: string,
): { channel: string; profileId?: string; chatId: string } {
  const parts = key.split(":");
  if (parts.length === 0) return { channel: "", chatId: key };
  if (parts[0] !== "websocket") {
    if (parts.length === 1) return { channel: "", chatId: key };
    return { channel: parts[0], chatId: parts.slice(1).join(":") };
  }
  // websocket:chat-id (legacy)
  if (parts.length === 2) {
    return { channel: "websocket", chatId: parts[1] };
  }
  // websocket:profile-id:chat-id (current)
  return {
    channel: "websocket",
    profileId: parts[1],
    chatId: parts.slice(2).join(":"),
  };
}

export function buildSessionKey(profileId: string, chatId: string): string {
  return `websocket:${profileId}:${chatId}`;
}

export async function listSessions(
  token: string,
  base: string = "",
): Promise<ChatSummary[]> {
  type Row = {
    key: string;
    created_at: string | null;
    updated_at: string | null;
    preview?: string;
  };
  const body = await request<{ sessions: Row[] }>(
    `${base}/api/sessions`,
    token,
  );
  return body.sessions.map((s) => ({
    key: s.key,
    ...parseSessionKey(s.key),
    createdAt: s.created_at,
    updatedAt: s.updated_at,
    preview: s.preview ?? "",
  }));
}

/** Signed image URL attached to a historical user message. The server
 * emits these in place of raw on-disk paths so the client can render
 * previews without learning where media lives on disk. Each URL is a
 * self-authenticating ``/api/media/...`` route (see backend
 * ``_sign_media_path``) safe to drop into an ``<img src>`` attribute. */
export interface SessionMediaUrl {
  url: string;
  name?: string;
}

export async function fetchSessionMessages(
  token: string,
  key: string,
  base: string = "",
): Promise<{
  key: string;
  created_at: string | null;
  updated_at: string | null;
  messages: Array<{
    role: string;
    content: string;
    timestamp?: string;
    tool_calls?: unknown;
    tool_call_id?: string;
    name?: string;
    /** Present on ``user`` turns that attached images. Paths have already
     * been stripped server-side; only the signed fetch URLs survive. */
    media_urls?: SessionMediaUrl[];
  }>;
}> {
  return request(
    `${base}/api/sessions/${encodeURIComponent(key)}/messages`,
    token,
  );
}

export async function deleteSession(
  token: string,
  key: string,
  base: string = "",
): Promise<boolean> {
  const body = await request<{ deleted: boolean }>(
    `${base}/api/sessions/${encodeURIComponent(key)}/delete`,
    token,
  );
  return body.deleted;
}

export async function listProfiles(
  token: string,
  base: string = "",
): Promise<{ profiles: ProfileSummary[]; currentProfileId: string }> {
  const body = await request<{
    profiles: ProfileSummary[];
    current_profile_id: string;
  }>(`${base}/api/profiles`, token);
  return {
    profiles: body.profiles,
    currentProfileId: body.current_profile_id,
  };
}

export async function createProfile(
  token: string,
  name: string,
  base: string = "",
): Promise<ProfileSummary> {
  const clean = name.trim();
  if (!clean) {
    throw new Error("profile name cannot be empty");
  }
  const body = await request<{ profile: ProfileSummary }>(
    `${base}/api/profiles/create?name=${encodeURIComponent(clean)}`,
    token,
  );
  return body.profile;
}

export async function deleteProfile(
  token: string,
  profileId: string,
  base: string = "",
): Promise<{ deleted: boolean; profileId: string }> {
  const clean = profileId.trim();
  if (!clean) {
    throw new Error("profile id cannot be empty");
  }
  const body = await request<{ deleted: boolean; profile_id: string }>(
    `${base}/api/profiles/delete?profile_id=${encodeURIComponent(clean)}`,
    token,
  );
  return { deleted: body.deleted, profileId: body.profile_id };
}
