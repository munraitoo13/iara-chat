"use client";

import {
  AlertTriangle,
  CalendarCheck2,
  Headset,
  MoreHorizontal,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRound,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  createSession,
  deleteSession,
  getHealth,
  getSession,
  sendPatientMessage,
} from "@/lib/api";
import type {
  BookedAppointmentDTO,
  ConversationStatus,
  MessageDTO,
  QuickReplyOption,
} from "@/lib/types";

const SESSION_KEY = "iara_patient_session_id";

function getStatusBadge(
  status: ConversationStatus,
  assignedStaffName: string | null,
): { label: string; dotClass: string } {
  switch (status) {
    case "waiting_human":
      return { label: "Conectando à recepção...", dotClass: "bg-amber-400 animate-pulse" };
    case "human_takeover":
      return {
        label: assignedStaffName ? `Atendimento · ${assignedStaffName}` : "Atendente conectado",
        dotClass: "bg-emerald-500 animate-soft-halo",
      };
    case "completed":
      return { label: "Atendimento concluído", dotClass: "bg-emerald-500" };
    case "expired":
      return { label: "Encerrado por inatividade", dotClass: "bg-slate-400" };
    case "emergency":
      return { label: "Orientação de urgência", dotClass: "bg-rose-500" };
    default:
      return { label: "Online · Iara", dotClass: "bg-[#14b8a6] animate-soft-halo" };
  }
}

function formatAppointmentDate(iso: string): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(dt);
}

export function PatientChat() {
  const [clinicName, setClinicName] = useState("Clínica Modelo");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<ConversationStatus>("collecting");
  const [assignedStaffName, setAssignedStaffName] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageDTO[]>([]);
  const [quickReplies, setQuickReplies] = useState<QuickReplyOption[]>([]);
  const [bookedAppointment, setBookedAppointment] = useState<BookedAppointmentDTO | null>(null);
  const [isFinished, setIsFinished] = useState(false);

  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isInitializing, setIsInitializing] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const feedEndRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const syncedMessageIdRef = useRef<number | undefined>(undefined);

  const scrollToBottom = useCallback(() => {
    feedEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, isSending, quickReplies, bookedAppointment, scrollToBottom]);

  useEffect(() => {
    function handleOutsideClick(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("mousedown", handleOutsideClick);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleOutsideClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, []);

  const hydrateFromSession = useCallback((data: Awaited<ReturnType<typeof getSession>>) => {
    setSessionId(data.session_id);
    setClinicName(data.clinic_name);
    setStatus(data.status);
    setAssignedStaffName(data.assigned_staff_name);
    setMessages(data.messages);
    setQuickReplies(data.quick_replies);
    setBookedAppointment(data.booked_appointment);
    setIsFinished(data.is_finished);
    localStorage.setItem(SESSION_KEY, data.session_id);

    const lastWithId = [...data.messages].reverse().find((m) => m.id !== null);
    syncedMessageIdRef.current = lastWithId?.id ?? undefined;
  }, []);

  const startFreshSession = useCallback(async () => {
    setIsInitializing(true);
    setConnectionError(null);
    setMenuOpen(false);
    syncedMessageIdRef.current = undefined;
    try {
      const created = await createSession();
      hydrateFromSession(created);
    } catch {
      setConnectionError("Não foi possível conectar ao atendimento agora. Tente novamente.");
    } finally {
      setIsInitializing(false);
    }
  }, [hydrateFromSession]);

  useEffect(() => {
    let active = true;
    async function bootstrap() {
      try {
        const health = await getHealth();
        if (active) setClinicName(health.clinic_name);
      } catch {
        // Ignore non-fatal health probe failure
      }

      const savedId = localStorage.getItem(SESSION_KEY);
      if (savedId) {
        try {
          const restored = await getSession(savedId);
          if (active) {
            hydrateFromSession(restored);
            setIsInitializing(false);
          }
          return;
        } catch {
          localStorage.removeItem(SESSION_KEY);
        }
      }
      if (active) {
        await startFreshSession();
      }
    }
    void bootstrap();
    return () => {
      active = false;
    };
  }, [hydrateFromSession, startFreshSession]);

  // Lightweight polling while waiting for or talking to a human attendant
  useEffect(() => {
    if (!sessionId || (status !== "waiting_human" && status !== "human_takeover")) {
      return;
    }

    let cancelled = false;
    const poll = async () => {
      try {
        const afterId = syncedMessageIdRef.current;
        const latest = await getSession(sessionId, afterId);
        if (cancelled) return;

        setStatus(latest.status);
        setAssignedStaffName(latest.assigned_staff_name);
        setQuickReplies(latest.quick_replies);
        setBookedAppointment(latest.booked_appointment);
        setIsFinished(latest.is_finished);

        if (afterId === undefined) {
          setMessages(latest.messages);
        } else if (latest.messages.length > 0) {
          setMessages((prev) => {
            const existingIds = new Set(prev.map((m) => m.id).filter((id) => id !== null));
            const fresh = latest.messages.filter(
              (m) => m.id === null || !existingIds.has(m.id),
            );
            // Replace optimistic local bubbles (id === null) once DB-backed messages arrive
            const withoutOptimistic = prev.filter((m) => m.id !== null);
            return fresh.length > 0 ? [...withoutOptimistic, ...fresh] : prev;
          });
        }

        const lastWithId = [...latest.messages].reverse().find((m) => m.id !== null);
        if (lastWithId?.id !== null && lastWithId?.id !== undefined) {
          syncedMessageIdRef.current = lastWithId.id;
        }
      } catch {
        // Keep polling quietly on transient hiccups
      }
    };

    const intervalId = window.setInterval(() => {
      void poll();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [sessionId, status]);

  const handleSend = async (rawText: string) => {
    const trimmed = rawText.trim();
    if (!trimmed || !sessionId || isSending || isFinished) return;

    setMenuOpen(false);
    setConnectionError(null);
    setInput("");
    setQuickReplies([]);

    // Invalidate incremental cursor so next human poll reconciles DB IDs cleanly
    syncedMessageIdRef.current = undefined;

    const optimisticUser: MessageDTO = {
      id: null,
      actor: "user",
      message: trimmed,
      used_llm: false,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUser]);
    setIsSending(true);

    try {
      const res = await sendPatientMessage(sessionId, trimmed);
      const { reply } = res;

      setStatus(reply.status);
      setQuickReplies(res.quick_replies);
      setBookedAppointment(reply.booked_appointment);
      setIsFinished(reply.is_finished);

      if (reply.message) {
        setMessages((prev) => [
          ...prev,
          {
            id: null,
            actor: reply.error ? "system" : "assistant",
            message: reply.message,
            used_llm: reply.used_llm,
            timestamp: new Date().toISOString(),
          },
        ]);
      }
    } catch {
      setConnectionError("Não foi possível enviar sua mensagem. Verifique sua conexão.");
    } finally {
      setIsSending(false);
      window.setTimeout(() => inputRef.current?.focus(), 40);
    }
  };

  const handleEraseData = async () => {
    if (!sessionId || isSending) return;
    setMenuOpen(false);
    try {
      await deleteSession(sessionId);
    } catch {
      // Proceed to reset local state even if already expired
    }
    localStorage.removeItem(SESSION_KEY);
    await startFreshSession();
  };

  const statusMeta = getStatusBadge(status, assignedStaffName);
  const canRequestHuman =
    !isFinished && status !== "waiting_human" && status !== "human_takeover";

  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-3 py-4 sm:px-6 sm:py-8">
      <div
        className="flex h-[calc(100vh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-[24px] border border-[var(--border)] bg-[var(--surface)] sm:h-[min(860px,calc(100vh-4rem))]"
        style={{ boxShadow: "var(--shadow-soft)" }}
      >
        {/* Minimalist, calm header */}
        <header className="flex items-center justify-between border-b border-[var(--border)] bg-[var(--surface)]/90 px-5 py-4 backdrop-blur-md">
          <div className="flex items-center gap-3.5">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[var(--primary-soft)] text-[var(--primary)]">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-[15px] font-semibold tracking-tight text-[var(--text)]">
                {clinicName}
              </h1>
              <div className="mt-0.5 flex items-center gap-2 text-xs text-[var(--muted)]">
                <span className={`inline-block h-2 w-2 rounded-full ${statusMeta.dotClass}`} />
                <span>{statusMeta.label}</span>
              </div>
            </div>
          </div>

          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((prev) => !prev)}
              aria-label="Opções do atendimento"
              aria-expanded={menuOpen}
              className="flex h-9 w-9 items-center justify-center rounded-xl text-[var(--muted)] transition-colors duration-200 hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
            >
              <MoreHorizontal className="h-5 w-5" />
            </button>

            {menuOpen && (
              <div
                className="animate-fade-scale absolute right-0 z-20 mt-2 w-56 rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-1.5"
                style={{ boxShadow: "var(--shadow-soft)" }}
              >
                {canRequestHuman && (
                  <button
                    type="button"
                    onClick={() => void handleSend("Quero falar com um atendente")}
                    className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-[13px] font-medium text-[var(--text)] transition-colors hover:bg-[var(--surface-muted)]"
                  >
                    <Headset className="h-4 w-4 text-[var(--primary)]" />
                    Falar com atendente
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void startFreshSession()}
                  className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-[13px] font-medium text-[var(--text)] transition-colors hover:bg-[var(--surface-muted)]"
                >
                  <RotateCcw className="h-4 w-4 text-[var(--muted)]" />
                  Nova triagem
                </button>
                <div className="my-1 h-px bg-[var(--border)]" />
                <button
                  type="button"
                  onClick={() => void handleEraseData()}
                  className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-[13px] font-medium text-rose-600 transition-colors hover:bg-rose-50"
                >
                  <Trash2 className="h-4 w-4" />
                  Apagar meus dados (LGPD)
                </button>
              </div>
            )}
          </div>
        </header>

        {/* Conversation feed */}
        <div className="soft-scroll flex-1 space-y-3.5 overflow-y-auto px-4 py-5 sm:px-6">
          {isInitializing && (
            <div className="flex h-full items-center justify-center text-sm text-[var(--muted)]">
              Iniciando atendimento...
            </div>
          )}

          {!isInitializing &&
            messages.map((item, idx) => {
              if (item.actor === "user") {
                return (
                  <div
                    key={`${item.id ?? "u"}-${idx}`}
                    className="animate-message-in flex justify-end"
                  >
                    <div className="max-w-[82%] whitespace-pre-wrap rounded-[20px] rounded-br-md bg-[var(--primary)] px-4 py-3 text-[14.5px] leading-relaxed text-white shadow-sm">
                      {item.message}
                    </div>
                  </div>
                );
              }

              if (item.actor === "system") {
                return (
                  <div
                    key={`${item.id ?? "s"}-${idx}`}
                    className="animate-message-in flex justify-center py-1"
                  >
                    <div className="rounded-full bg-[var(--surface-muted)] px-3.5 py-1 text-xs font-medium text-[var(--muted)]">
                      {item.message}
                    </div>
                  </div>
                );
              }

              if (item.actor === "human_agent") {
                return (
                  <div
                    key={`${item.id ?? "h"}-${idx}`}
                    className="animate-message-in flex flex-col items-start gap-1"
                  >
                    <span className="ml-1 inline-flex items-center gap-1.5 text-[11px] font-medium text-emerald-700">
                      <UserRound className="h-3 w-3" />
                      {assignedStaffName || "Atendimento humano"}
                    </span>
                    <div className="max-w-[84%] whitespace-pre-wrap rounded-[20px] rounded-bl-md border border-emerald-200 bg-emerald-50/80 px-4 py-3 text-[14.5px] leading-relaxed text-emerald-950">
                      {item.message}
                    </div>
                  </div>
                );
              }

              const isEmergencyBubble =
                status === "emergency" && idx === messages.length - 1;

              return (
                <div
                  key={`${item.id ?? "a"}-${idx}`}
                  className="animate-message-in flex justify-start"
                >
                  <div
                    className={`max-w-[84%] whitespace-pre-wrap rounded-[20px] rounded-bl-md px-4 py-3 text-[14.5px] leading-relaxed ${
                      isEmergencyBubble
                        ? "border border-rose-200 bg-rose-50/90 text-rose-900"
                        : "border border-[var(--border)] bg-[var(--surface-muted)]/60 text-[var(--text)]"
                    }`}
                  >
                    {isEmergencyBubble && (
                      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-rose-700">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        Orientação importante
                      </div>
                    )}
                    {item.message}
                  </div>
                </div>
              );
            })}

          {isSending && status !== "human_takeover" && (
            <div className="animate-fade-scale flex justify-start">
              <div className="flex items-center gap-2 rounded-[20px] rounded-bl-md border border-[var(--border)] bg-[var(--surface-muted)]/60 px-4 py-3 text-xs text-[var(--muted)]">
                <span className="flex items-center gap-1">
                  <span className="typing-dot h-1.5 w-1.5 rounded-full bg-[var(--primary)]" />
                  <span className="typing-dot typing-dot-2 h-1.5 w-1.5 rounded-full bg-[var(--primary)]" />
                  <span className="typing-dot typing-dot-3 h-1.5 w-1.5 rounded-full bg-[var(--primary)]" />
                </span>
                <span>Iara está digitando</span>
              </div>
            </div>
          )}

          {bookedAppointment && (
            <div className="animate-fade-scale rounded-2xl border border-emerald-200/80 bg-emerald-50/60 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-emerald-900">
                <CalendarCheck2 className="h-4 w-4 text-emerald-700" />
                Consulta agendada
              </div>
              <div className="mt-2.5 grid grid-cols-2 gap-2 text-xs text-emerald-950/85">
                <div>
                  <span className="block text-[11px] text-emerald-700">Protocolo</span>
                  <span className="font-semibold">
                    #{bookedAppointment.appointment_id.slice(0, 8).toUpperCase()}
                  </span>
                </div>
                <div>
                  <span className="block text-[11px] text-emerald-700">Especialidade</span>
                  <span className="font-semibold">{bookedAppointment.specialty_label}</span>
                </div>
                <div>
                  <span className="block text-[11px] text-emerald-700">Profissional</span>
                  <span className="font-semibold">{bookedAppointment.doctor_name}</span>
                </div>
                <div>
                  <span className="block text-[11px] text-emerald-700">Data e horário</span>
                  <span className="font-semibold">
                    {formatAppointmentDate(bookedAppointment.starts_at)}
                  </span>
                </div>
              </div>
              <p className="mt-3 border-t border-emerald-200/70 pt-2.5 text-[11px] text-emerald-800/80">
                Para cancelar ou remarcar depois, informe o código{" "}
                <span className="font-semibold">
                  #{bookedAppointment.appointment_id.slice(0, 8).toUpperCase()}
                </span>{" "}
                em uma nova conversa.
              </p>
            </div>
          )}

          {connectionError && (
            <div className="animate-fade-scale rounded-2xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-center text-xs text-rose-700">
              {connectionError}
            </div>
          )}

          <div ref={feedEndRef} />
        </div>

        {/* Quick replies + composer */}
        <footer className="border-t border-[var(--border)] bg-[var(--surface)] px-4 py-3.5 sm:px-5">
          {quickReplies.length > 0 && !isFinished && (
            <div className="animate-fade-scale mb-3 flex flex-wrap gap-2">
              {quickReplies.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  disabled={isSending}
                  onClick={() => void handleSend(opt.value)}
                  className="rounded-full border border-[var(--primary)]/25 bg-[var(--primary-soft)] px-3.5 py-1.5 text-[13px] font-medium text-[var(--primary)] transition-all duration-200 hover:-translate-y-0.5 hover:border-[var(--primary)] hover:bg-[var(--primary)] hover:text-white disabled:opacity-50"
                >
                  {opt.label}
                </button>
              ))}
            </div>
          )}

          {isFinished ? (
            <div className="flex items-center justify-between gap-3 rounded-2xl bg-[var(--surface-muted)] px-4 py-2.5 text-xs text-[var(--muted)]">
              <span className="inline-flex items-center gap-1.5">
                <ShieldCheck className="h-4 w-4 text-[var(--primary)]" />
                Este atendimento foi encerrado.
              </span>
              <button
                type="button"
                onClick={() => void startFreshSession()}
                className="rounded-xl bg-[var(--primary)] px-3.5 py-1.5 font-medium text-white transition-colors hover:bg-[var(--primary-hover)]"
              >
                Iniciar novo
              </button>
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void handleSend(input);
              }}
              className="flex items-center gap-2 rounded-2xl border border-[var(--border)] bg-[var(--bg)] p-1.5 transition-shadow duration-200 focus-within:border-[var(--primary)]/50 focus-within:ring-4 focus-within:ring-[#0f766e]/10"
            >
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                maxLength={1000}
                disabled={isInitializing || (isSending && status !== "human_takeover")}
                placeholder={
                  status === "human_takeover"
                    ? "Escreva para o atendente..."
                    : "Digite sua resposta..."
                }
                className="flex-1 bg-transparent px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--muted)]/75 focus:outline-none disabled:opacity-60"
              />
              <button
                type="submit"
                disabled={
                  !input.trim() ||
                  isInitializing ||
                  (isSending && status !== "human_takeover")
                }
                aria-label="Enviar mensagem"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--primary)] text-white transition-all duration-200 hover:bg-[var(--primary-hover)] disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            </form>
          )}
        </footer>
      </div>
    </div>
  );
}
