import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ThreadComposer } from "@/components/thread/ThreadComposer";
import { ThreadHeader } from "@/components/thread/ThreadHeader";
import { StreamErrorNotice } from "@/components/thread/StreamErrorNotice";
import { ThreadViewport } from "@/components/thread/ThreadViewport";
import { useNanobotStream } from "@/hooks/useNanobotStream";
import type { SendImage } from "@/hooks/useNanobotStream";
import { useSessionHistory } from "@/hooks/useSessions";
import type { ChatSummary, UIMessage } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface ThreadShellProps {
  session: ChatSummary | null;
  title: string;
  onToggleSidebar: () => void;
  onGoHome: () => void;
  onNewChat: () => Promise<string | null>;
  hideSidebarToggleOnDesktop?: boolean;
}

function toModelBadgeLabel(modelName: string | null): string | null {
  if (!modelName) return null;
  const trimmed = modelName.trim();
  if (!trimmed) return null;
  const leaf = trimmed.split("/").pop() ?? trimmed;
  return leaf || trimmed;
}

export function ThreadShell({
  session,
  title,
  onToggleSidebar,
  onGoHome,
  onNewChat,
  hideSidebarToggleOnDesktop = false,
}: ThreadShellProps) {
  const { t } = useTranslation();
  const chatId = session?.chatId ?? null;
  const historyKey = session?.key ?? null;
  const { messages: historical, loading } = useSessionHistory(historyKey);
  const {
    client,
    modelName,
    models,
    selectedModelId,
    thinkingEnabled,
    thinkingSupported,
    thinkingRecipeReady,
    setThinkingEnabled,
  } = useClient();
  const [booting, setBooting] = useState(false);
  const [pauseDialogOpen, setPauseDialogOpen] = useState(false);
  const [pauseDialogStep, setPauseDialogStep] = useState<"required" | "config" | "preview">("required");
  const [suppressReminderForChat, setSuppressReminderForChat] = useState(false);
  const [recipeDocUrl, setRecipeDocUrl] = useState("");
  const [recipeSnippet, setRecipeSnippet] = useState("");
  const [recipeSubmitting, setRecipeSubmitting] = useState(false);
  const [recipeConfirming, setRecipeConfirming] = useState(false);
  const [recipeLocalError, setRecipeLocalError] = useState<string | null>(null);
  const [pendingPausedSend, setPendingPausedSend] = useState<{
    content: string;
    images?: SendImage[];
  } | null>(null);
  const pendingFirstRef = useRef<string | null>(null);
  const messageCacheRef = useRef<Map<string, UIMessage[]>>(new Map());

  const initial = useMemo(() => {
    if (!chatId) return historical;
    return messageCacheRef.current.get(chatId) ?? historical;
  }, [chatId, historical]);
  const {
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
  } = useNanobotStream(chatId, initial);
  const showHeroComposer = messages.length === 0 && !loading;
  const selectedModelLabel = useMemo(() => {
    if (!selectedModelId) return null;
    const hit = models.find((m) => m.id === selectedModelId);
    return hit?.name ?? null;
  }, [models, selectedModelId]);

  useEffect(() => {
    if (!chatId || loading) return;
    const cached = messageCacheRef.current.get(chatId);
    // When the user switches away and back, keep the local in-memory thread
    // state (including not-yet-persisted messages) instead of replacing it with
    // whatever the history endpoint currently knows about.
    setMessages(cached && cached.length > 0 ? cached : historical);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, chatId, historical]);

  useEffect(() => {
    setSuppressReminderForChat(false);
    setPauseDialogOpen(false);
    setPauseDialogStep("required");
    setRecipeDocUrl("");
    setRecipeSnippet("");
    setRecipeSubmitting(false);
    setRecipeConfirming(false);
    setRecipeLocalError(null);
    setPendingPausedSend(null);
  }, [chatId]);

  useEffect(() => {
    if (!pauseDialogOpen) return;
    if (!thinkingRecipePreview) return;
    setPauseDialogStep("preview");
    setRecipeSubmitting(false);
    setRecipeLocalError(null);
  }, [pauseDialogOpen, thinkingRecipePreview]);

  useEffect(() => {
    if (!thinkingRecipeError) return;
    setRecipeSubmitting(false);
    setRecipeConfirming(false);
  }, [thinkingRecipeError]);

  useEffect(() => {
    if (!thinkingRecipeSaved) return;
    if (pendingPausedSend) {
      send(pendingPausedSend.content, pendingPausedSend.images);
    }
    setPendingPausedSend(null);
    setPauseDialogOpen(false);
    setPauseDialogStep("required");
    clearThinkingRecipeRequired();
    clearThinkingRecipePreview();
    clearThinkingRecipeSaved();
    clearThinkingRecipeError();
    clearThinkingRecipeProgress();
    clearThinkingRecipeConflict();
    setRecipeDocUrl("");
    setRecipeSnippet("");
    setRecipeSubmitting(false);
    setRecipeConfirming(false);
    setRecipeLocalError(null);
  }, [
    clearThinkingRecipeError,
    clearThinkingRecipePreview,
    clearThinkingRecipeRequired,
    clearThinkingRecipeSaved,
    clearThinkingRecipeProgress,
    clearThinkingRecipeConflict,
    pendingPausedSend,
    send,
    thinkingRecipeSaved,
  ]);

  useEffect(() => {
    if (chatId) return;
    setMessages(historical);
  }, [chatId, historical, setMessages]);

  useEffect(() => {
    if (!chatId) return;
    messageCacheRef.current.set(chatId, messages);
  }, [chatId, messages]);

  useEffect(() => {
    if (thinkingSupported && thinkingRecipeReady) return;
    if (!thinkingEnabled) return;
    setThinkingEnabled(false);
  }, [
    setThinkingEnabled,
    thinkingEnabled,
    thinkingRecipeReady,
    thinkingSupported,
  ]);

  useEffect(() => {
    if (!chatId) return;
    const pending = pendingFirstRef.current;
    if (!pending) return;
    pendingFirstRef.current = null;
    client.sendMessage(chatId, pending);
    setMessages((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: "user",
        content: pending,
        createdAt: Date.now(),
      },
    ]);
    setBooting(false);
  }, [chatId, client, setMessages]);

  const handleWelcomeSend = useCallback(
    async (content: string) => {
      if (booting) return;
      setBooting(true);
      pendingFirstRef.current = content;
      const newId = await onNewChat();
      if (!newId) {
        pendingFirstRef.current = null;
        setBooting(false);
      }
    },
    [booting, onNewChat],
  );

  const proceedPausedSend = useCallback(() => {
    if (pendingPausedSend) {
      send(pendingPausedSend.content, pendingPausedSend.images, { forceDisableThinking: true });
    }
    setPendingPausedSend(null);
    setPauseDialogOpen(false);
    setPauseDialogStep("required");
    clearThinkingRecipeRequired();
    clearThinkingRecipePreview();
    clearThinkingRecipeSaved();
    clearThinkingRecipeError();
    clearThinkingRecipeProgress();
    clearThinkingRecipeConflict();
    setRecipeSubmitting(false);
    setRecipeConfirming(false);
    setRecipeLocalError(null);
    setRecipeDocUrl("");
    setRecipeSnippet("");
  }, [
    clearThinkingRecipeError,
    clearThinkingRecipePreview,
    clearThinkingRecipeRequired,
    clearThinkingRecipeSaved,
    clearThinkingRecipeProgress,
    clearThinkingRecipeConflict,
    pendingPausedSend,
    send,
  ]);

  const submitThinkingRecipe = useCallback(() => {
    if (!chatId) return;
    const c = client as unknown as {
      submitThinkingRecipe?: (
        id: string,
        payload: { model_id?: string; doc_url?: string; snippet?: string },
      ) => void;
    };
    if (typeof c.submitThinkingRecipe !== "function") return;
    const docUrl = recipeDocUrl.trim();
    const snippet = recipeSnippet.trim();
    if (!docUrl && !snippet) {
      setRecipeLocalError("请至少填写官方 URL 或样例代码/参数格式其中一项。");
      return;
    }
    if (docUrl && !/^https?:\/\//i.test(docUrl)) {
      setRecipeLocalError("官方 URL 必须以 http:// 或 https:// 开头。");
      return;
    }
    setRecipeSubmitting(true);
    setRecipeLocalError(null);
    clearThinkingRecipeError();
    clearThinkingRecipePreview();
    clearThinkingRecipeSaved();
    clearThinkingRecipeProgress();
    clearThinkingRecipeConflict();
    c.submitThinkingRecipe(chatId, {
      ...(selectedModelId ? { model_id: selectedModelId } : {}),
      ...(docUrl ? { doc_url: docUrl } : {}),
      ...(snippet ? { snippet } : {}),
    });
  }, [
    chatId,
    clearThinkingRecipeError,
    clearThinkingRecipePreview,
    clearThinkingRecipeSaved,
    clearThinkingRecipeProgress,
    clearThinkingRecipeConflict,
    client,
    recipeDocUrl,
    recipeSnippet,
    selectedModelId,
  ]);

  const confirmThinkingRecipe = useCallback(() => {
    if (!chatId || !thinkingRecipePreview?.previewId) return;
    const c = client as unknown as {
      confirmThinkingRecipe?: (id: string, previewId: string) => void;
    };
    if (typeof c.confirmThinkingRecipe !== "function") return;
    setRecipeConfirming(true);
    setRecipeLocalError(null);
    clearThinkingRecipeError();
    c.confirmThinkingRecipe(chatId, thinkingRecipePreview.previewId);
  }, [chatId, clearThinkingRecipeError, client, thinkingRecipePreview?.previewId]);

  const handleThreadSend = useCallback(
    (content: string, images?: SendImage[]) => {
      if (
        thinkingEnabled
        && thinkingRecipeRequired
        && !suppressReminderForChat
      ) {
        setPendingPausedSend({ content, images });
        setPauseDialogStep("required");
        setPauseDialogOpen(true);
        return;
      }
      send(content, images);
    },
    [send, suppressReminderForChat, thinkingEnabled, thinkingRecipeRequired],
  );

  const handleOpenThinkingConfig = useCallback(() => {
    if (!chatId) return;
    setPauseDialogOpen(true);
    setPauseDialogStep("config");
    setRecipeLocalError(null);
    clearThinkingRecipeError();
    clearThinkingRecipePreview();
  }, [
    chatId,
    clearThinkingRecipeError,
    clearThinkingRecipePreview,
  ]);

  const emptyState = loading ? (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      {t("thread.loadingConversation")}
    </div>
  ) : (
    <div className="flex w-full max-w-[40rem] flex-col gap-2 text-left animate-in fade-in-0 slide-in-from-bottom-2 duration-500">
      <div className="inline-flex items-center gap-2 text-[11px] font-medium text-muted-foreground">
        <img
          src="/brand/nanobot_icon.png"
          alt=""
          aria-hidden
          draggable={false}
          className="h-4 w-4 rounded-sm opacity-90"
        />
        <span className="text-foreground/82">nanobot</span>
      </div>
      <p className="max-w-[28rem] text-[13px] leading-6 text-muted-foreground">
        {t("thread.empty.description")}
      </p>
    </div>
  );

  return (
    <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      <ThreadHeader
        title={title}
        chatId={chatId}
        onOpenThinkingConfig={handleOpenThinkingConfig}
        onToggleSidebar={onToggleSidebar}
        onGoHome={onGoHome}
        hideSidebarToggleOnDesktop={hideSidebarToggleOnDesktop}
      />
      <ThreadViewport
        messages={messages}
        isStreaming={isStreaming}
        emptyState={emptyState}
        composer={
          <>
            {streamError ? (
              <StreamErrorNotice
                error={streamError}
                onDismiss={dismissStreamError}
              />
            ) : null}
            {session ? (
              <ThreadComposer
                onSend={handleThreadSend}
                disabled={!chatId}
                placeholder={
                  showHeroComposer
                    ? t("thread.composer.placeholderHero")
                    : t("thread.composer.placeholderThread")
                }
                modelLabel={toModelBadgeLabel(selectedModelLabel ?? modelName)}
                variant={showHeroComposer ? "hero" : "thread"}
              />
            ) : (
              <ThreadComposer
                onSend={handleWelcomeSend}
                disabled={booting}
                placeholder={
                  booting
                    ? t("thread.composer.placeholderOpening")
                    : t("thread.composer.placeholderHero")
                }
                modelLabel={toModelBadgeLabel(selectedModelLabel ?? modelName)}
                variant="hero"
              />
            )}
          </>
        }
      />
      <AlertDialog
        open={pauseDialogOpen}
        onOpenChange={(open) => {
          if (!open) {
            setPauseDialogOpen(false);
          }
        }}
      >
        <AlertDialogContent className="max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pauseDialogStep === "required"
                ? "需要配置思考参数"
                : pauseDialogStep === "config"
                  ? "配置思考参数来源"
                  : "确认思考参数预览"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pauseDialogStep === "required"
                ? "当前已开启思考模式，但该模型尚未配置思考参数。你可以先配置，再继续本次消息。"
                : pauseDialogStep === "config"
                  ? "仅支持两种输入：官方 URL，或官方样例代码/参数格式。可填一项或两项。"
                  : "请确认后端提取的参数预览；确认后将保存为全局可复用配置。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {pauseDialogStep === "config" ? (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <label htmlFor="thinking-doc-url" className="text-xs text-muted-foreground">
                  官方 URL
                </label>
                <Input
                  id="thinking-doc-url"
                  placeholder="https://..."
                  value={recipeDocUrl}
                  onChange={(e) => setRecipeDocUrl(e.target.value)}
                  disabled={recipeSubmitting || recipeConfirming}
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="thinking-snippet" className="text-xs text-muted-foreground">
                  官方样例代码或控制参数格式
                </label>
                <Textarea
                  id="thinking-snippet"
                  placeholder={"thinking: { enabled: true, effort: \"high\" }"}
                  value={recipeSnippet}
                  onChange={(e) => setRecipeSnippet(e.target.value)}
                  disabled={recipeSubmitting || recipeConfirming}
                  rows={6}
                />
              </div>
              {recipeLocalError ? (
                <p className="text-xs text-destructive">{recipeLocalError}</p>
              ) : null}
              {thinkingRecipeError ? (
                <p className="text-xs text-destructive">{thinkingRecipeError}</p>
              ) : null}
              {thinkingRecipeProgress ? (
                <p className="text-xs text-muted-foreground">
                  提取进度: {thinkingRecipeProgress.stage}
                  {thinkingRecipeProgress.message ? ` · ${thinkingRecipeProgress.message}` : ""}
                </p>
              ) : null}
              {thinkingRecipeConflict ? (
                <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-[11px] text-amber-700 dark:text-amber-300">
                  <div>检测到 URL 与 snippet 参数冲突，已自动选择: {thinkingRecipeConflict.decision ?? "hybrid"}</div>
                  <div>evidence: {(thinkingRecipeConflict.evidence ?? []).length}</div>
                </div>
              ) : null}
            </div>
          ) : null}
          {pauseDialogStep === "preview" ? (
            <div className="space-y-3">
              <div className="rounded-md border border-border bg-muted/35 p-2 text-[11px] text-muted-foreground">
                <div>model: {thinkingRecipePreview?.modelName ?? "-"}</div>
                <div>base_url: {thinkingRecipePreview?.baseUrl ?? "-"}</div>
                <div>evidence: {(thinkingRecipePreview?.evidence ?? []).length}</div>
              </div>
              <pre className="max-h-44 overflow-auto rounded-md border border-border bg-muted/25 p-2 text-[11px] leading-5 text-foreground/90">
                {JSON.stringify(thinkingRecipePreview?.recipe ?? {}, null, 2)}
              </pre>
              {thinkingRecipeError ? (
                <p className="text-xs text-destructive">{thinkingRecipeError}</p>
              ) : null}
              {thinkingRecipeConflict ? (
                <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-[11px] text-amber-700 dark:text-amber-300">
                  <div>冲突处理决策: {thinkingRecipeConflict.decision ?? "hybrid"}</div>
                  <div>evidence: {(thinkingRecipeConflict.evidence ?? []).length}</div>
                </div>
              ) : null}
            </div>
          ) : null}
          <AlertDialogFooter>
            {pauseDialogStep === "required" ? (
              <>
                <Button
                  variant="outline"
                  onClick={() => {
                    setSuppressReminderForChat(true);
                    proceedPausedSend();
                  }}
                >
                  关闭提醒并继续
                </Button>
                <Button
                  onClick={() => {
                    setPauseDialogStep("config");
                    setRecipeLocalError(null);
                    clearThinkingRecipeError();
                  }}
                >
                  去配置
                </Button>
              </>
            ) : null}
            {pauseDialogStep === "config" ? (
              <>
                <Button
                  variant="outline"
                  onClick={() => {
                    setPauseDialogStep("required");
                    setRecipeLocalError(null);
                    setRecipeSubmitting(false);
                  }}
                >
                  返回
                </Button>
                <Button
                  onClick={submitThinkingRecipe}
                  disabled={recipeSubmitting || recipeConfirming}
                >
                  {recipeSubmitting ? "提交中..." : "提交并预览"}
                </Button>
              </>
            ) : null}
            {pauseDialogStep === "preview" ? (
              <>
                <Button
                  variant="outline"
                  onClick={() => {
                    setPauseDialogStep("config");
                    setRecipeConfirming(false);
                  }}
                >
                  返回修改
                </Button>
                <Button
                  onClick={confirmThinkingRecipe}
                  disabled={recipeSubmitting || recipeConfirming}
                >
                  {recipeConfirming ? "保存中..." : "确认并保存"}
                </Button>
              </>
            ) : null}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
