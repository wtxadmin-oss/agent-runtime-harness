import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import type { NanobotClient } from "@/lib/nanobot-client";
import type { InboundEvent, ModelOption, ProfileSummary } from "@/lib/types";

interface ClientContextValue {
  client: NanobotClient;
  token: string;
  modelName: string | null;
  profileId: string;
  profiles: ProfileSummary[];
  models: ModelOption[];
  selectedModelId: string | null;
  setSelectedModelId: (id: string | null) => void;
  thinkingSupported: boolean;
  setThinkingSupported: (supported: boolean) => void;
  thinkingRecipeReady: boolean;
  setThinkingRecipeReady: (ready: boolean) => void;
  thinkingUnavailableReason: string | null;
  setThinkingUnavailableReason: (reason: string | null) => void;
  thinkingEnabled: boolean;
  setThinkingEnabled: (enabled: boolean) => void;
  reasoningEffort: "high" | "max";
  setReasoningEffort: (effort: "high" | "max") => void;
}

const ClientContext = createContext<ClientContextValue | null>(null);

export function ClientProvider({
  client,
  token,
  modelName = null,
  profileId = "",
  profiles = [],
  children,
}: {
  client: NanobotClient;
  token: string;
  modelName?: string | null;
  profileId?: string;
  profiles?: ProfileSummary[];
  children: ReactNode;
}) {
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [reasoningEffort, setReasoningEffort] = useState<"high" | "max">("high");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [thinkingSupported, setThinkingSupported] = useState(false);
  const [thinkingRecipeReady, setThinkingRecipeReady] = useState(false);
  const [thinkingUnavailableReason, setThinkingUnavailableReason] = useState<string | null>(null);

  useEffect(() => {
    const clientWithModels = client as unknown as {
      onGlobal?: (handler: (ev: InboundEvent) => void) => () => void;
      requestModelList?: () => void;
    };
    if (typeof clientWithModels.onGlobal !== "function") {
      return;
    }
    const unsub = clientWithModels.onGlobal((ev) => {
      if (ev.event === "model_list") {
        setModels(ev.models);
      } else if (ev.event === "model_saved") {
        setModels((prev) => [...prev.filter((m) => m.id !== ev.model.id), ev.model]);
      } else if (ev.event === "model_deleted") {
        setModels((prev) => prev.filter((m) => m.id !== ev.model_id));
        setSelectedModelId((prev) => (prev === ev.model_id ? null : prev));
      }
    });
    if (typeof clientWithModels.requestModelList === "function") {
      clientWithModels.requestModelList();
    }
    return () => {
      unsub();
    };
  }, [client]);

  return (
    <ClientContext.Provider
      value={{
        client,
        token,
        modelName,
        profileId,
        profiles,
        models,
        selectedModelId,
        setSelectedModelId,
        thinkingSupported,
        setThinkingSupported,
        thinkingRecipeReady,
        setThinkingRecipeReady,
        thinkingUnavailableReason,
        setThinkingUnavailableReason,
        thinkingEnabled,
        setThinkingEnabled,
        reasoningEffort,
        setReasoningEffort,
      }}
    >
      {children}
    </ClientContext.Provider>
  );
}

export function useClient(): ClientContextValue {
  const ctx = useContext(ClientContext);
  if (!ctx) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return ctx;
}
