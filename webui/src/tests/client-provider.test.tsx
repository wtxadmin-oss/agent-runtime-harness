import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { InboundEvent } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

class BoundClientLike {
  status = "open" as const;
  defaultChatId: string | null = null;
  globalHandlers = new Set<(ev: InboundEvent) => void>();
  requestModelList = vi.fn();

  onStatus() {
    return () => {};
  }

  onError() {
    return () => {};
  }

  onChat() {
    return () => {};
  }

  onGlobal(handler: (ev: InboundEvent) => void) {
    this.globalHandlers.add(handler);
    return () => {
      this.globalHandlers.delete(handler);
    };
  }

  sendMessage = vi.fn();
  newChat = vi.fn();
  attach = vi.fn();
  connect = vi.fn();
  close = vi.fn();
  updateUrl = vi.fn();
}

describe("ClientProvider", () => {
  it("subscribes model global events without losing client method binding", () => {
    const client = new BoundClientLike();
    const { unmount } = render(
      <ClientProvider
        client={client as unknown as import("@/lib/nanobot-client").NanobotClient}
        token="tok"
      >
        <div>ok</div>
      </ClientProvider>,
    );

    expect(client.requestModelList).toHaveBeenCalledTimes(1);
    expect(client.globalHandlers.size).toBe(1);
    unmount();
    expect(client.globalHandlers.size).toBe(0);
  });
});
