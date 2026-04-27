import { Brain, PanelLeftOpen } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

interface ThreadHeaderProps {
  title: string;
  chatId?: string | null;
  onOpenThinkingConfig?: () => void;
  onToggleSidebar: () => void;
  onGoHome: () => void;
  hideSidebarToggleOnDesktop?: boolean;
}

export function ThreadHeader({
  title,
  chatId = null,
  onOpenThinkingConfig,
  onToggleSidebar,
  onGoHome,
  hideSidebarToggleOnDesktop = false,
}: ThreadHeaderProps) {
  const { t } = useTranslation();
  const {
    client,
    models,
    selectedModelId,
    setSelectedModelId,
    thinkingSupported,
    thinkingRecipeReady,
    thinkingUnavailableReason,
    thinkingEnabled,
    setThinkingEnabled,
    reasoningEffort,
    setReasoningEffort,
  } = useClient();
  const selectedModel = useMemo(
    () => models.find((m) => m.id === selectedModelId) ?? null,
    [models, selectedModelId],
  );
  const [addModelOpen, setAddModelOpen] = useState(false);
  const [addModelName, setAddModelName] = useState("");
  const [addModelModelName, setAddModelModelName] = useState("");
  const [addModelBaseUrl, setAddModelBaseUrl] = useState("");
  const [addModelApiKey, setAddModelApiKey] = useState("");
  const [addModelSupportsThinking, setAddModelSupportsThinking] = useState(false);
  const [addModelThinkingDocUrl, setAddModelThinkingDocUrl] = useState("");
  const [addModelThinkingSnippet, setAddModelThinkingSnippet] = useState("");
  const [addModelError, setAddModelError] = useState<string | null>(null);
  const onPickModel = (nextId: string) => {
    if (!chatId) return;
    const value = nextId || null;
    setSelectedModelId(value);
    client.selectModel(chatId, value);
  };

  const onAddModel = () => {
    const name = addModelName.trim();
    const modelName = addModelModelName.trim();
    const baseUrl = addModelBaseUrl.trim();
    const apiKey = addModelApiKey.trim();
    const thinkingDocUrl = addModelThinkingDocUrl.trim();
    const thinkingSnippet = addModelThinkingSnippet.trim();
    if (!name || !modelName || !baseUrl || !apiKey) {
      setAddModelError("请完整填写模型名称、model_name、base_url、api_key。");
      return;
    }
    if (addModelSupportsThinking && !thinkingDocUrl && !thinkingSnippet) {
      setAddModelError("开启思考模式时，请至少提供官方 URL 或官方样例代码。");
      return;
    }
    if (thinkingDocUrl && !/^https?:\/\//i.test(thinkingDocUrl)) {
      setAddModelError("官方 URL 需以 http:// 或 https:// 开头。");
      return;
    }
    setAddModelError(null);
    client.addModel({
      name,
      model_name: modelName,
      base_url: baseUrl,
      api_key: apiKey,
      supports_thinking: addModelSupportsThinking,
      ...(addModelSupportsThinking && thinkingDocUrl ? { thinking_doc_url: thinkingDocUrl } : {}),
      ...(addModelSupportsThinking && thinkingSnippet ? { thinking_snippet: thinkingSnippet } : {}),
    });
    client.requestModelList();
    setAddModelOpen(false);
    setAddModelName("");
    setAddModelModelName("");
    setAddModelBaseUrl("");
    setAddModelApiKey("");
    setAddModelSupportsThinking(false);
    setAddModelThinkingDocUrl("");
    setAddModelThinkingSnippet("");
    setAddModelError(null);
  };

  const onDeleteModel = () => {
    if (!selectedModelId || !selectedModel || selectedModel.readonly) return;
    if (!window.confirm(`Delete model '${selectedModel.name}'?`)) return;
    client.deleteModel(selectedModelId);
    if (chatId) {
      client.selectModel(chatId, null);
    }
    setSelectedModelId(null);
  };

  const thinkingToggleDisabled = !thinkingSupported || !thinkingRecipeReady;
  const thinkingDisabledHint = (() => {
    if (thinkingSupported && thinkingRecipeReady) return null;
    if (!thinkingSupported) return "当前模型不支持思考模式";
    if (thinkingUnavailableReason === "unvalidated_recipe") return "思考参数未验证，请先完成配置确认";
    return "当前模型未配置思考参数，请先配置";
  })();

  return (
    <div className="relative z-10 flex items-center justify-between gap-3 px-3 py-2">
      <div className="relative flex min-w-0 items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("thread.header.toggleSidebar")}
          onClick={onToggleSidebar}
          className={cn(
            "h-7 w-7 rounded-md text-muted-foreground hover:bg-accent/35 hover:text-foreground",
            hideSidebarToggleOnDesktop && "lg:pointer-events-none lg:opacity-0",
          )}
        >
          <PanelLeftOpen className="h-3.5 w-3.5" />
        </Button>
        <button
          type="button"
          onClick={onGoHome}
          className="flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-accent/35 hover:text-foreground"
        >
          <img
            src="/brand/nanobot_icon.png"
            alt=""
            className="h-4 w-4 rounded-[5px] opacity-85"
            aria-hidden
          />
          <span className="max-w-[min(60vw,32rem)] truncate">{title}</span>
        </button>
      </div>

      {/* Thinking mode controls */}
      <div className="flex items-center gap-1.5">
        {chatId ? (
          <>
            <select
              value={selectedModelId ?? ""}
              onChange={(e) => onPickModel(e.target.value)}
              className="h-7 rounded-md border border-border bg-background px-2 text-[11px] text-foreground"
              aria-label="模型选择"
            >
              <option value="">Base Model</option>
              {models
                .filter((m) => m.source === "user_custom")
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
            </select>
            <button
              type="button"
              onClick={() => setAddModelOpen(true)}
              className="rounded-md border border-border px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-accent/35 hover:text-foreground"
            >
              +
            </button>
            <button
              type="button"
              onClick={onDeleteModel}
              disabled={!selectedModelId || !!selectedModel?.readonly}
              className="rounded-md border border-border px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-accent/35 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45"
            >
              -
            </button>
          </>
        ) : null}
        <button
          type="button"
          disabled={thinkingToggleDisabled}
          onClick={() => {
            if (thinkingToggleDisabled) return;
            const next = !thinkingEnabled;
            setThinkingEnabled(next);
            if (chatId && typeof (client as unknown as { setThinkingToggle?: (id: string, enabled: boolean) => void }).setThinkingToggle === "function") {
              (client as unknown as { setThinkingToggle: (id: string, enabled: boolean) => void }).setThinkingToggle(chatId, next);
            }
          }}
          aria-label="深度思考模式"
          title={thinkingDisabledHint ?? undefined}
          className={cn(
            "flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium transition-all disabled:cursor-not-allowed disabled:opacity-45",
            thinkingEnabled
              ? "bg-violet-500/15 text-violet-500 ring-1 ring-violet-500/30"
              : "text-muted-foreground hover:bg-accent/35 hover:text-foreground",
          )}
        >
          <Brain className="h-3 w-3" />
          <span>思考</span>
        </button>
        {chatId ? (
          <button
            type="button"
            onClick={onOpenThinkingConfig}
            disabled={!thinkingSupported}
            title={!thinkingSupported ? "当前模型不支持思考模式" : undefined}
            className="rounded-md border border-border px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-accent/35 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45"
          >
            配置
          </button>
        ) : null}
        {thinkingDisabledHint ? (
          <span className="max-w-[180px] truncate text-[10px] text-muted-foreground">
            {thinkingDisabledHint}
          </span>
        ) : null}
        {thinkingEnabled && (
          <div className="flex items-center gap-0.5 rounded-md border border-border bg-muted/40 p-0.5">
            <button
              type="button"
              onClick={() => setReasoningEffort("high")}
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors",
                reasoningEffort === "high"
                  ? "bg-violet-500 text-white"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              high
            </button>
            <button
              type="button"
              onClick={() => setReasoningEffort("max")}
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors",
                reasoningEffort === "max"
                  ? "bg-violet-500 text-white"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              max
            </button>
          </div>
        )}
      </div>

      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-full h-4" />
      <AlertDialog open={addModelOpen} onOpenChange={setAddModelOpen}>
        <AlertDialogContent className="max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle>添加模型</AlertDialogTitle>
            <AlertDialogDescription>
              填写模型连接信息；若开启思考模式，请补充官方文档 URL 或官方样例代码。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label htmlFor="add-model-name" className="text-xs text-muted-foreground">
                模型名称
              </label>
              <Input
                id="add-model-name"
                placeholder="My Model"
                value={addModelName}
                onChange={(e) => setAddModelName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="add-model-model-name" className="text-xs text-muted-foreground">
                model_name
              </label>
              <Input
                id="add-model-model-name"
                placeholder="deepseek-v4-flash"
                value={addModelModelName}
                onChange={(e) => setAddModelModelName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="add-model-base-url" className="text-xs text-muted-foreground">
                base_url
              </label>
              <Input
                id="add-model-base-url"
                placeholder="https://api.deepseek.com"
                value={addModelBaseUrl}
                onChange={(e) => setAddModelBaseUrl(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="add-model-api-key" className="text-xs text-muted-foreground">
                api_key
              </label>
              <Input
                id="add-model-api-key"
                type="password"
                placeholder="sk-..."
                value={addModelApiKey}
                onChange={(e) => setAddModelApiKey(e.target.value)}
              />
            </div>
            <label className="flex items-center gap-2 text-xs text-foreground">
              <input
                type="checkbox"
                checked={addModelSupportsThinking}
                onChange={(e) => setAddModelSupportsThinking(e.target.checked)}
              />
              启用思考模式配置
            </label>
            {addModelSupportsThinking ? (
              <div className="space-y-3 rounded-md border border-border/70 bg-muted/20 p-2.5">
                <div className="space-y-1.5">
                  <label htmlFor="add-model-thinking-url" className="text-xs text-muted-foreground">
                    官方 URL
                  </label>
                  <Input
                    id="add-model-thinking-url"
                    placeholder="https://..."
                    value={addModelThinkingDocUrl}
                    onChange={(e) => setAddModelThinkingDocUrl(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <label htmlFor="add-model-thinking-snippet" className="text-xs text-muted-foreground">
                    官方样例代码或控制参数格式
                  </label>
                  <Textarea
                    id="add-model-thinking-snippet"
                    rows={5}
                    placeholder={"thinking: { enabled: true, effort: \"high\" }"}
                    value={addModelThinkingSnippet}
                    onChange={(e) => setAddModelThinkingSnippet(e.target.value)}
                  />
                </div>
              </div>
            ) : null}
            {addModelError ? (
              <p className="text-xs text-destructive">{addModelError}</p>
            ) : null}
          </div>
          <AlertDialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setAddModelOpen(false);
                setAddModelError(null);
              }}
            >
              取消
            </Button>
            <Button onClick={onAddModel}>保存</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
