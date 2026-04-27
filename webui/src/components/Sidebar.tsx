import { Moon, PanelLeftClose, Plus, RefreshCcw, Sun, Trash2, ChevronsUpDown } from "lucide-react";
import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ChatList } from "@/components/ChatList";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import type { ChatSummary, ProfileSummary } from "@/lib/types";

interface SidebarProps {
  sessions: ChatSummary[];
  activeKey: string | null;
  loading: boolean;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onNewChat: () => void;
  onSelect: (key: string) => void;
  onRefresh: () => void;
  onRequestDelete: (key: string, label: string) => void;
  profiles: ProfileSummary[];
  currentProfileId: string;
  onProfileChange: (profileId: string) => void;
  onCreateProfile: (name: string) => Promise<void>;
  onDeleteProfile: (profileId: string) => Promise<void>;
  onCollapse: () => void;
}

export function Sidebar(props: SidebarProps) {
  const { t } = useTranslation();
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ProfileSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const currentProfile =
    props.profiles.find((p) => p.id === props.currentProfileId) ?? props.profiles[0];
  const deletableProfiles = props.profiles.filter(
    (p) => p.id !== props.currentProfileId,
  );

  const submitCreate = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const clean = createName.trim();
    if (!clean) {
      setCreateError(t("sidebar.createUser.errors.empty"));
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      await props.onCreateProfile(clean);
      setCreateOpen(false);
      setCreateName("");
    } catch {
      setCreateError(t("sidebar.createUser.errors.failed"));
    } finally {
      setCreating(false);
    }
  };

  const beginDelete = (profile: ProfileSummary) => {
    setDeleteTarget(profile);
    setDeleteError(null);
    setDeleteOpen(true);
  };

  const confirmDelete = async (target: ProfileSummary) => {
    setDeleteOpen(false);
    setDeleting(true);
    setDeleteError(null);
    try {
      await props.onDeleteProfile(target.id);
      setDeleteTarget(null);
    } catch {
      setDeleteError(t("sidebar.deleteUser.errors.failed"));
      setDeleteTarget(target);
      setDeleteOpen(true);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <aside className="flex h-full w-full flex-col border-r border-sidebar-border/70 bg-sidebar text-sidebar-foreground">
      <div className="flex items-center justify-between px-2 py-2">
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("sidebar.collapse")}
          onClick={props.onCollapse}
          className="h-7 w-7 rounded-lg text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          <PanelLeftClose className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("sidebar.toggleTheme")}
          onClick={props.onToggleTheme}
          className="h-7 w-7 rounded-lg text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          {props.theme === "dark" ? (
            <Sun className="h-3.5 w-3.5" />
          ) : (
            <Moon className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>
      <div className="px-2 pb-2.5">
        <Button
          onClick={props.onNewChat}
          className="h-8.5 w-full justify-start gap-2 rounded-lg border border-sidebar-border/80 bg-card/25 px-3 text-[13px] font-medium text-sidebar-foreground shadow-none hover:bg-sidebar-accent/80"
          variant="outline"
        >
          <Plus className="h-3.5 w-3.5" />
          {t("sidebar.newChat")}
        </Button>
        <div className="mt-2 flex items-center gap-1 rounded-lg border border-sidebar-border/70 p-1">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 flex-1 justify-between rounded-md px-2 text-sidebar-foreground hover:bg-sidebar-accent"
                aria-label={t("sidebar.profile.select")}
              >
                <span className="truncate">{currentProfile?.name ?? props.currentProfileId}</span>
                <ChevronsUpDown className="h-3.5 w-3.5 text-muted-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-44">
              {props.profiles.map((profile) => (
                <DropdownMenuItem
                  key={profile.id}
                  className="justify-between"
                  onClick={() => props.onProfileChange(profile.id)}
                >
                  <span className="truncate">{profile.name}</span>
                  {profile.id === props.currentProfileId ? (
                    <span className="text-xs text-muted-foreground">•</span>
                  ) : null}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 rounded-md text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
            aria-label={t("sidebar.createUser.open")}
            onClick={() => {
              setCreateError(null);
              setCreateOpen(true);
            }}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0 rounded-md text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
                aria-label={t("sidebar.deleteUser.open")}
                disabled={deletableProfiles.length === 0}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              {deletableProfiles.length === 0 ? (
                <DropdownMenuItem disabled>
                  {t("sidebar.deleteUser.none")}
                </DropdownMenuItem>
              ) : (
                deletableProfiles.map((profile) => (
                  <DropdownMenuItem
                    key={profile.id}
                    onClick={() => beginDelete(profile)}
                  >
                    {profile.name}
                  </DropdownMenuItem>
                ))
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <Separator className="bg-sidebar-border/70" />
      <div className="flex items-center justify-between px-2.5 py-2 text-[11px] font-medium text-muted-foreground">
        <span>{t("sidebar.recent")}</span>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 rounded-md text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
          onClick={props.onRefresh}
          aria-label={t("sidebar.refreshSessions")}
        >
          <RefreshCcw className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="flex-1 overflow-hidden">
        <ChatList
          sessions={props.sessions}
          activeKey={props.activeKey}
          loading={props.loading}
          onSelect={props.onSelect}
          onRequestDelete={props.onRequestDelete}
        />
      </div>
      <Separator className="bg-sidebar-border/70" />
      <div className="flex items-center justify-between gap-2 px-2.5 py-2 text-xs">
        <ConnectionBadge />
        <LanguageSwitcher />
      </div>
      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) {
            setCreateError(null);
            setCreateName("");
          }
        }}
      >
        <DialogContent className="max-w-sm p-5">
          <DialogHeader>
            <DialogTitle>{t("sidebar.createUser.title")}</DialogTitle>
            <DialogDescription>
              {t("sidebar.createUser.description")}
            </DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={(e) => void submitCreate(e)}>
            <Input
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              placeholder={t("sidebar.createUser.placeholder")}
              aria-label={t("sidebar.createUser.placeholder")}
              maxLength={64}
              disabled={creating}
              autoFocus
            />
            {createError ? (
              <p className="text-xs text-destructive">{createError}</p>
            ) : null}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateOpen(false)}
                disabled={creating}
              >
                {t("deleteConfirm.cancel")}
              </Button>
              <Button type="submit" disabled={creating}>
                {creating
                  ? t("sidebar.createUser.submitting")
                  : t("sidebar.createUser.submit")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <AlertDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open);
          if (!open) {
            setDeleteError(null);
            setDeleteTarget(null);
          }
        }}
      >
        <AlertDialogContent className="max-w-sm">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("sidebar.deleteUser.title", {
                name: deleteTarget?.name ?? "",
              })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("sidebar.deleteUser.description")}
            </AlertDialogDescription>
            {deleteError ? (
              <p className="text-xs text-destructive">{deleteError}</p>
            ) : null}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>
              {t("deleteConfirm.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={deleting}
              onClick={() => {
                if (!deleteTarget) return;
                void confirmDelete(deleteTarget);
              }}
            >
              {deleting
                ? t("sidebar.deleteUser.submitting")
                : t("sidebar.deleteUser.submit")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </aside>
  );
}
