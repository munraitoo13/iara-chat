"use client";

import {
  Activity,
  ArrowLeft,
  BarChart3,
  Bell,
  Bot,
  Calendar,
  CalendarPlus,
  CheckCircle2,
  ClipboardList,
  Clock,
  Cpu,
  ExternalLink,
  Headset,
  KeyRound,
  Lock,
  MessageSquare,
  Plus,
  RefreshCw,
  Save,
  Send,
  Sparkles,
  Trash2,
  UserCheck,
  UserRound,
  Volume2,
  VolumeX,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  getHealth,
  listAvailableSlots,
  staffBookAppointment,
  staffCancelAppointment,
  staffCloseSession,
  staffCreateSlotsBatch,
  staffDeleteSlot,
  staffDispatchDueReminders,
  staffExtractPatient,
  staffGetSession,
  staffListAppointments,
  staffListReminders,
  staffListSessions,
  staffOfferSlots,
  staffReleaseToAi,
  staffRescheduleAppointment,
  staffSendMessage,
  staffSendReminderNow,
  staffTakeoverSession,
  staffUpdatePatient,
} from "@/lib/api";
import { playGentleChime } from "@/lib/sound";
import type {
  AppointmentSlotDTO,
  BookedAppointmentDTO,
  ConversationStatus,
  CoverageType,
  HealthResponse,
  PatientInfo,
  ReminderDTO,
  SessionResponse,
  Specialty,
  StaffSessionSummary,
} from "@/lib/types";

const STAFF_KEY_STORAGE = "iara_staff_api_key";
const STAFF_NAME_STORAGE = "iara_staff_name";
const STAFF_MUTED_STORAGE = "iara_staff_sound_muted";

type DashboardTab = "queue" | "calendar" | "metrics";
type QueueFilter = "all" | "waiting_human" | "human_takeover" | "active_ai" | "completed";

const STATUS_LABELS: Record<
  ConversationStatus,
  { label: string; className: string }
> = {
  collecting: {
    label: "Em triagem",
    className: "bg-teal-50 text-teal-700 border-teal-200/80",
  },
  confirming: {
    label: "Confirmando",
    className: "bg-sky-50 text-sky-700 border-sky-200/80",
  },
  scheduling: {
    label: "Agendando",
    className: "bg-indigo-50 text-indigo-700 border-indigo-200/80",
  },
  waiting_human: {
    label: "Aguardando humano",
    className: "bg-amber-50 text-amber-800 border-amber-200",
  },
  human_takeover: {
    label: "Atendimento humano",
    className: "bg-emerald-50 text-emerald-800 border-emerald-200",
  },
  emergency: {
    label: "Emergência",
    className: "bg-rose-50 text-rose-700 border-rose-200",
  },
  completed: {
    label: "Concluído",
    className: "bg-slate-100 text-slate-700 border-slate-200",
  },
  expired: {
    label: "Expirado",
    className: "bg-slate-100 text-slate-500 border-slate-200",
  },
};

const SPECIALTY_FILTERS: Array<{ value: Specialty | "all"; label: string }> = [
  { value: "all", label: "Todas" },
  { value: "clinica_geral", label: "Clínica Geral" },
  { value: "ortopedia", label: "Ortopedia" },
  { value: "dermatologia", label: "Dermatologia" },
  { value: "cardiologia", label: "Cardiologia" },
];

function formatDateTimeBr(iso: string): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(dt);
}

function formatDobBr(isoDate: string | null): string {
  if (!isoDate) return "-";
  const parts = isoDate.split("-");
  if (parts.length !== 3) return isoDate;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

export function StaffDashboard() {
  const [tab, setTab] = useState<DashboardTab>("queue");
  const [queueFilter, setQueueFilter] = useState<QueueFilter>("all");
  const [specialtyFilter, setSpecialtyFilter] = useState<Specialty | "all">("all");

  const [staffKey, setStaffKey] = useState<string | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [authRequired, setAuthRequired] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const [staffName, setStaffName] = useState("Recepção");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [sessions, setSessions] = useState<StaffSessionSummary[]>([]);
  const [appointments, setAppointments] = useState<BookedAppointmentDTO[]>([]);
  const [slots, setSlots] = useState<AppointmentSlotDTO[]>([]);
  const [reminders, setReminders] = useState<ReminderDTO[]>([]);
  const [agendaNotice, setAgendaNotice] = useState<string | null>(null);

  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [reschedulingId, setReschedulingId] = useState<string | null>(null);
  const [rescheduleSlots, setRescheduleSlots] = useState<AppointmentSlotDTO[]>([]);
  const [rescheduleTargetSlotId, setRescheduleTargetSlotId] = useState<number | "">("");

  const [batchSpecialty, setBatchSpecialty] = useState<Specialty>("clinica_geral");
  const [batchDoctor, setBatchDoctor] = useState("Dra. Camila Rocha");
  const [batchDate, setBatchDate] = useState("");
  const [batchStartHour, setBatchStartHour] = useState(8);
  const [batchEndHour, setBatchEndHour] = useState(12);
  const [batchInterval, setBatchInterval] = useState(30);

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<SessionResponse | null>(null);
  const [replyText, setReplyText] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isMuted, setIsMuted] = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(false);

  const [showDrawer, setShowDrawer] = useState(true);
  const [drawerSessionId, setDrawerSessionId] = useState<string | null>(null);
  const [patientDraft, setPatientDraft] = useState<PatientInfo>({
    full_name: null,
    date_of_birth: null,
    phone: null,
    email: null,
    address: null,
    coverage_type: null,
    insurance_payer: null,
    insurance_id: null,
    chief_complaint: null,
    preferred_specialty: null,
  });
  const [copilotNotice, setCopilotNotice] = useState<string | null>(null);
  const [drawerSlots, setDrawerSlots] = useState<AppointmentSlotDTO[]>([]);
  const [bookSpecialty, setBookSpecialty] = useState<Specialty>("clinica_geral");
  const [selectedSlotId, setSelectedSlotId] = useState<number | "">("");

  useEffect(() => {
    const savedKey = sessionStorage.getItem(STAFF_KEY_STORAGE);
    const savedName = localStorage.getItem(STAFF_NAME_STORAGE);
    const savedMuted = localStorage.getItem(STAFF_MUTED_STORAGE);
    if (savedKey) setStaffKey(savedKey);
    if (savedName) setStaffName(savedName);
    if (savedMuted === "true") setIsMuted(true);
    setBatchDate(new Date(Date.now() + 86_400_000).toISOString().slice(0, 10));
  }, []);

  const loadDashboardData = useCallback(
    async (keyOverride?: string | null) => {
      const activeKey = keyOverride !== undefined ? keyOverride : staffKey;
      try {
        const [healthData, sessionRows, apptRows, slotRows, reminderRows] = await Promise.all([
          getHealth().catch(() => null),
          staffListSessions(activeKey),
          staffListAppointments(activeKey),
          listAvailableSlots(specialtyFilter === "all" ? undefined : specialtyFilter),
          staffListReminders(activeKey),
        ]);

        if (healthData) setHealth(healthData);
        setSessions(sessionRows);
        setAppointments(apptRows);
        setSlots(slotRows);
        setReminders(reminderRows);
        setAuthRequired(false);
        setAuthError(null);
        setActionError(null);

        if (selectedSessionId) {
          const stillExists = sessionRows.some((s) => s.session_id === selectedSessionId);
          if (stillExists) {
            const detail = await staffGetSession(selectedSessionId, activeKey);
            setActiveSession(detail);
          } else {
            setSelectedSessionId(null);
            setActiveSession(null);
          }
        } else if (sessionRows.length > 0) {
          const firstId = sessionRows[0].session_id;
          setSelectedSessionId(firstId);
          const detail = await staffGetSession(firstId, activeKey);
          setActiveSession(detail);
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          setAuthRequired(true);
          if (keyOverride !== undefined) {
            setAuthError("Chave de acesso inválida. Tente novamente.");
          }
        } else {
          setActionError(err instanceof Error ? err.message : "Erro ao carregar painel.");
        }
      } finally {
        setIsLoading(false);
      }
    },
    [staffKey, specialtyFilter, selectedSessionId],
  );

  useEffect(() => {
    void loadDashboardData();
  }, [loadDashboardData]);

  // Live refresh every 3s when unlocked
  useEffect(() => {
    if (authRequired) return;
    const id = window.setInterval(() => {
      void loadDashboardData();
    }, 3000);
    return () => window.clearInterval(id);
  }, [authRequired, loadDashboardData]);

  const handleSelectSession = async (sessionId: string) => {
    setSelectedSessionId(sessionId);
    setActionError(null);
    try {
      const detail = await staffGetSession(sessionId, staffKey);
      setActiveSession(detail);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Erro ao abrir conversa.");
    }
  };

  const handleUnlock = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = keyDraft.trim();
    if (!cleaned) return;
    sessionStorage.setItem(STAFF_KEY_STORAGE, cleaned);
    setStaffKey(cleaned);
    await loadDashboardData(cleaned);
  };

  const handleClearKey = () => {
    sessionStorage.removeItem(STAFF_KEY_STORAGE);
    setStaffKey(null);
    setKeyDraft("");
    void loadDashboardData(null);
  };

  const runStaffAction = async (fn: () => Promise<SessionResponse>) => {
    setActionBusy(true);
    setActionError(null);
    try {
      const updated = await fn();
      setActiveSession(updated);
      await loadDashboardData();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao executar ação.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleSendStaffReply = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeSession || !replyText.trim() || actionBusy) return;
    const text = replyText.trim();
    setReplyText("");
    await runStaffAction(() =>
      staffSendMessage(activeSession.session_id, staffName.trim() || "Recepção", text, staffKey),
    );
  };

  // Sync drawer draft only when switching active session so background polling preserves edits
  useEffect(() => {
    if (!activeSession) {
      setDrawerSessionId(null);
      setCopilotNotice(null);
      return;
    }
    if (activeSession.session_id !== drawerSessionId) {
      setDrawerSessionId(activeSession.session_id);
      setPatientDraft(activeSession.patient_info);
      setBookSpecialty(activeSession.patient_info.preferred_specialty || "clinica_geral");
      setCopilotNotice(null);
    }
  }, [activeSession, drawerSessionId]);

  // Load available slots for the drawer's selected specialty
  useEffect(() => {
    if (!showDrawer || !activeSession || activeSession.booked_appointment) return;
    let cancelled = false;
    void listAvailableSlots(bookSpecialty)
      .then((rows) => {
        if (cancelled) return;
        setDrawerSlots(rows);
        setSelectedSlotId((prev) =>
          rows.some((r) => r.slot_id === prev) ? prev : (rows[0]?.slot_id ?? ""),
        );
      })
      .catch(() => {
        if (!cancelled) setDrawerSlots([]);
      });
    return () => {
      cancelled = true;
    };
  }, [showDrawer, activeSession, bookSpecialty]);

  const handleExtractWithCopilot = async () => {
    if (!activeSession || actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    setCopilotNotice(null);
    try {
      const res = await staffExtractPatient(activeSession.session_id, staffKey);
      setPatientDraft(res.extracted_patient);
      setBookSpecialty(res.suggested_specialty);
      const warnSuffix = res.warnings.length > 0 ? ` (${res.warnings.join(" ")})` : "";
      setCopilotNotice(`Sugestão preenchida pela Iara — revise e salve.${warnSuffix}`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao extrair dados com IA.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleSavePatientDraft = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeSession || actionBusy) return;
    setCopilotNotice(null);
    await runStaffAction(async () => {
      const updated = await staffUpdatePatient(
        activeSession.session_id,
        {
          full_name: patientDraft.full_name?.trim() || null,
          date_of_birth: patientDraft.date_of_birth?.trim() || null,
          phone: patientDraft.phone?.trim() || null,
          email: patientDraft.email?.trim() || null,
          address: patientDraft.address?.trim() || null,
          coverage_type: patientDraft.coverage_type,
          insurance_payer:
            patientDraft.coverage_type === "convenio"
              ? patientDraft.insurance_payer?.trim() || null
              : null,
          insurance_id:
            patientDraft.coverage_type === "convenio"
              ? patientDraft.insurance_id?.trim() || null
              : null,
          chief_complaint: patientDraft.chief_complaint?.trim() || null,
          preferred_specialty: patientDraft.preferred_specialty,
        },
        staffKey,
      );
      setPatientDraft(updated.patient_info);
      setCopilotNotice("Ficha salva com sucesso.");
      return updated;
    });
  };

  const handleDirectBook = async () => {
    if (!activeSession || !selectedSlotId || actionBusy) return;
    await runStaffAction(() =>
      staffBookAppointment(
        activeSession.session_id,
        Number(selectedSlotId),
        staffName.trim() || "Recepção",
        staffKey,
      ),
    );
  };

  const handleOfferSlotsInChat = async () => {
    if (!activeSession || actionBusy) return;
    await runStaffAction(() =>
      staffOfferSlots(activeSession.session_id, bookSpecialty, staffKey),
    );
  };

  const handleCreateSlotsBatch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (actionBusy || !batchDoctor.trim() || !batchDate) return;
    setActionBusy(true);
    setActionError(null);
    setAgendaNotice(null);
    try {
      const created = await staffCreateSlotsBatch(
        {
          specialty: batchSpecialty,
          doctor_name: batchDoctor.trim(),
          target_date: batchDate,
          start_hour: batchStartHour,
          end_hour: batchEndHour,
          interval_minutes: batchInterval,
        },
        staffKey,
      );
      await loadDashboardData();
      setAgendaNotice(
        `${created.length} horário(s) adicionado(s) para ${batchDate.split("-").reverse().join("/")}.`,
      );
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao gerar horários em lote.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleDeleteSlot = async (slotId: number) => {
    if (actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    setAgendaNotice(null);
    try {
      await staffDeleteSlot(slotId, staffKey);
      await loadDashboardData();
      setAgendaNotice("Horário removido da agenda.");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao remover horário.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleConfirmCancelAppointment = async (appointmentId: string) => {
    if (actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    setAgendaNotice(null);
    try {
      await staffCancelAppointment(appointmentId, cancelReason.trim() || null, staffKey);
      setCancellingId(null);
      setCancelReason("");
      await loadDashboardData();
      setAgendaNotice("Consulta cancelada e horário devolvido à agenda.");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao cancelar consulta.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleOpenReschedule = async (appt: BookedAppointmentDTO) => {
    if (actionBusy) return;
    setActionError(null);
    setAgendaNotice(null);
    setCancellingId(null);
    setReschedulingId(appt.appointment_id);
    try {
      const allFree = await listAvailableSlots();
      const candidates = allFree.filter((s) => s.slot_id !== appt.slot_id);
      setRescheduleSlots(candidates);
      const preferred =
        candidates.find((s) => s.specialty === appt.specialty) ?? candidates[0];
      setRescheduleTargetSlotId(preferred ? preferred.slot_id : "");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao carregar horários livres.");
      setReschedulingId(null);
    }
  };

  const handleConfirmReschedule = async (appointmentId: string) => {
    if (actionBusy || !rescheduleTargetSlotId) return;
    setActionBusy(true);
    setActionError(null);
    setAgendaNotice(null);
    try {
      await staffRescheduleAppointment(appointmentId, Number(rescheduleTargetSlotId), staffKey);
      setReschedulingId(null);
      await loadDashboardData();
      setAgendaNotice("Consulta remarcada e novos lembretes programados.");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao remarcar consulta.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleSendReminderNow = async (reminderId: number) => {
    if (actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    setAgendaNotice(null);
    try {
      const sent = await staffSendReminderNow(reminderId, staffKey);
      await loadDashboardData();
      setAgendaNotice(
        sent.status === "sent"
          ? `Lembrete enviado para ${sent.patient_name} via ${sent.channel}.`
          : `Falha no envio: ${sent.last_error || "erro desconhecido"}`,
      );
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao enviar lembrete.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleDispatchDueReminders = async () => {
    if (actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    setAgendaNotice(null);
    try {
      const res = await staffDispatchDueReminders(staffKey);
      await loadDashboardData();
      setAgendaNotice(
        res.dispatched > 0
          ? `${res.dispatched} lembrete(s) vencido(s) enviado(s) via ${res.channel}.`
          : "Nenhum lembrete vencido pendente no momento.",
      );
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao processar lembretes.");
    } finally {
      setActionBusy(false);
    }
  };

  const filteredSessions = useMemo(() => {
    return sessions.filter((s) => {
      if (queueFilter === "waiting_human") return s.status === "waiting_human";
      if (queueFilter === "human_takeover") return s.status === "human_takeover";
      if (queueFilter === "completed") {
        return s.status === "completed" || s.status === "emergency" || s.status === "expired";
      }
      if (queueFilter === "active_ai") {
        return s.status === "collecting" || s.status === "confirming" || s.status === "scheduling";
      }
      return true;
    });
  }, [sessions, queueFilter]);

  const waitingCount = useMemo(
    () => sessions.filter((s) => s.status === "waiting_human").length,
    [sessions],
  );
  const hasWaiting = waitingCount > 0;

  // Repeat gentle chime every 30s while any session is waiting for human attention
  useEffect(() => {
    if (authRequired || isMuted || !hasWaiting) {
      setAudioBlocked(false);
      return;
    }

    let cancelled = false;
    const triggerPing = () => {
      void playGentleChime().then((played) => {
        if (!cancelled) setAudioBlocked(!played);
      });
    };

    triggerPing();
    const intervalId = window.setInterval(triggerPing, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [authRequired, isMuted, hasWaiting]);

  const handleToggleMute = () => {
    const nextMuted = !isMuted;
    setIsMuted(nextMuted);
    localStorage.setItem(STAFF_MUTED_STORAGE, String(nextMuted));
    if (!nextMuted) {
      void playGentleChime().then((played) => setAudioBlocked(!played));
    } else {
      setAudioBlocked(false);
    }
  };

  const metrics = useMemo(() => {
    const totalSessions = sessions.length;
    const totalFast = sessions.reduce((acc, s) => acc + s.fast_turns, 0);
    const totalLlm = sessions.reduce((acc, s) => acc + s.llm_turns, 0);
    const totalTurns = totalFast + totalLlm;
    const savingsPct = totalTurns > 0 ? Math.round((totalFast / totalTurns) * 100) : 0;
    const escalations = sessions.filter(
      (s) => s.status === "waiting_human" || s.status === "human_takeover" || s.handoff_reason,
    ).length;
    const expiredCount = sessions.filter((s) => s.status === "expired").length;

    return {
      totalSessions,
      totalFast,
      totalLlm,
      totalTurns,
      savingsPct,
      escalations,
      expiredCount,
      totalAppointments: appointments.length,
    };
  }, [sessions, appointments]);

  if (authRequired) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div
          className="animate-fade-scale w-full max-w-md rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-7"
          style={{ boxShadow: "var(--shadow-soft)" }}
        >
          <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--primary-soft)] text-[var(--primary)]">
            <KeyRound className="h-6 w-6" />
          </div>
          <h1 className="text-lg font-semibold text-[var(--text)]">Acesso da Equipe</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Este ambiente está protegido por <code className="text-xs">STAFF_API_KEY</code>.
            Informe a chave para acessar a fila, agenda e métricas.
          </p>

          <form onSubmit={(e) => void handleUnlock(e)} className="mt-5 space-y-3">
            <input
              type="password"
              value={keyDraft}
              onChange={(e) => setKeyDraft(e.target.value)}
              placeholder="Cole sua STAFF_API_KEY"
              className="w-full rounded-xl border border-[var(--border)] bg-[var(--bg)] px-3.5 py-2.5 text-sm focus:border-[var(--primary)] focus:outline-none focus:ring-4 focus:ring-[#0f766e]/10"
              required
            />
            {authError && <p className="text-xs font-medium text-rose-600">{authError}</p>}
            <button
              type="submit"
              className="w-full rounded-xl bg-[var(--primary)] py-2.5 text-sm font-semibold text-white transition-colors hover:bg-[var(--primary-hover)]"
            >
              Desbloquear Painel
            </button>
          </form>

          <Link
            href="/"
            className="mt-4 inline-flex items-center gap-1.5 text-xs font-medium text-[var(--muted)] hover:text-[var(--primary)]"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Voltar ao chat do paciente
          </Link>
        </div>
      </div>
    );
  }

  const canTakeover =
    activeSession &&
    activeSession.status !== "human_takeover" &&
    activeSession.status !== "completed" &&
    activeSession.status !== "emergency" &&
    activeSession.status !== "expired";

  const canRelease =
    activeSession &&
    (activeSession.status === "human_takeover" || activeSession.status === "waiting_human");

  const canClose =
    activeSession &&
    activeSession.status !== "completed" &&
    activeSession.status !== "emergency" &&
    activeSession.status !== "expired";

  return (
    <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-5 px-4 py-5 sm:px-6">
      {/* Header */}
      <header
        className="flex flex-wrap items-center justify-between gap-4 rounded-[22px] border border-[var(--border)] bg-[var(--surface)] px-5 py-4"
        style={{ boxShadow: "var(--shadow-card)" }}
      >
        <div className="flex items-center gap-3.5">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[var(--primary)] text-white">
            <Sparkles className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-semibold tracking-tight text-[var(--text)]">
                Iara · Painel da Clínica
              </h1>
              {health && (
                <span className="hidden rounded-full bg-[var(--surface-muted)] px-2.5 py-0.5 text-[11px] font-medium text-[var(--muted)] sm:inline-block">
                  {health.clinic_name} · {health.provider}: {health.model}
                </span>
              )}
            </div>
            <p className="text-xs text-[var(--muted)]">
              Fila de atendimentos, intervenção humanizada e agenda médica
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-1.5 rounded-xl border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5 text-xs">
            <UserRound className="h-3.5 w-3.5 text-[var(--primary)]" />
            <input
              type="text"
              value={staffName}
              onChange={(e) => {
                setStaffName(e.target.value);
                localStorage.setItem(STAFF_NAME_STORAGE, e.target.value);
              }}
              aria-label="Nome do atendente"
              className="w-28 bg-transparent font-medium text-[var(--text)] focus:outline-none"
            />
          </div>

          <button
            type="button"
            onClick={handleToggleMute}
            title={
              isMuted
                ? "Ativar alerta sonoro (a cada 30s)"
                : "Silenciar alerta sonoro"
            }
            className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-medium transition-colors ${
              isMuted
                ? "border-[var(--border)] bg-[var(--surface-muted)] text-[var(--muted)]"
                : audioBlocked && hasWaiting
                  ? "border-amber-300 bg-amber-50 text-amber-800"
                  : "border-[var(--border)] bg-[var(--surface)] text-[var(--text)] hover:bg-[var(--surface-muted)]"
            }`}
          >
            {isMuted ? <VolumeX className="h-3.5 w-3.5" /> : <Volume2 className="h-3.5 w-3.5 text-[var(--primary)]" />}
            {isMuted ? "Mutado" : audioBlocked && hasWaiting ? "Ativar som" : "Som ativo"}
          </button>

          <button
            type="button"
            onClick={() => void loadDashboardData()}
            className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs font-medium text-[var(--text)] transition-colors hover:bg-[var(--surface-muted)]"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Atualizar
          </button>

          <Link
            href="/"
            className="inline-flex items-center gap-1.5 rounded-xl bg-[var(--primary-soft)] px-3 py-2 text-xs font-semibold text-[var(--primary)] transition-colors hover:bg-[var(--primary)] hover:text-white"
          >
            Chat do Paciente
            <ExternalLink className="h-3.5 w-3.5" />
          </Link>

          {staffKey && (
            <button
              type="button"
              onClick={handleClearKey}
              title="Bloquear painel"
              className="rounded-xl border border-[var(--border)] p-2 text-[var(--muted)] hover:bg-rose-50 hover:text-rose-600"
            >
              <Lock className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </header>

      {/* Navigation tabs */}
      <nav className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setTab("queue")}
          className={`inline-flex items-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-medium transition-all duration-200 ${
            tab === "queue"
              ? "bg-[var(--primary)] text-white shadow-sm"
              : "border border-[var(--border)] bg-[var(--surface)] text-[var(--muted)] hover:text-[var(--text)]"
          }`}
        >
          <MessageSquare className="h-4 w-4" />
          Fila & Atendimento
          {waitingCount > 0 && (
            <span className="rounded-full bg-amber-400 px-2 py-0.5 text-[11px] font-bold text-amber-950">
              {waitingCount}
            </span>
          )}
        </button>

        <button
          type="button"
          onClick={() => setTab("calendar")}
          className={`inline-flex items-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-medium transition-all duration-200 ${
            tab === "calendar"
              ? "bg-[var(--primary)] text-white shadow-sm"
              : "border border-[var(--border)] bg-[var(--surface)] text-[var(--muted)] hover:text-[var(--text)]"
          }`}
        >
          <Calendar className="h-4 w-4" />
          Agenda ({appointments.length})
        </button>

        <button
          type="button"
          onClick={() => setTab("metrics")}
          className={`inline-flex items-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-medium transition-all duration-200 ${
            tab === "metrics"
              ? "bg-[var(--primary)] text-white shadow-sm"
              : "border border-[var(--border)] bg-[var(--surface)] text-[var(--muted)] hover:text-[var(--text)]"
          }`}
        >
          <BarChart3 className="h-4 w-4" />
          Métricas da IA
        </button>
      </nav>

      {actionError && (
        <div className="animate-fade-scale rounded-2xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs font-medium text-rose-700">
          {actionError}
        </div>
      )}

      {/* TAB 1: QUEUE & HANDOFF */}
      {tab === "queue" && (
        <div className="animate-fade-scale grid flex-1 gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
          {/* Left Queue */}
          <section
            className="flex h-[calc(100vh-230px)] min-h-[520px] flex-col rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-4"
            style={{ boxShadow: "var(--shadow-card)" }}
          >
            <div className="mb-3 flex flex-wrap gap-1.5">
              {(
                [
                  ["all", "Todas"],
                  ["waiting_human", "Aguardando"],
                  ["human_takeover", "Em atendimento"],
                  ["active_ai", "IA"],
                  ["completed", "Encerradas"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setQueueFilter(key)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                    queueFilter === key
                      ? "bg-[var(--primary-soft)] text-[var(--primary)]"
                      : "bg-[var(--surface-muted)] text-[var(--muted)] hover:text-[var(--text)]"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="soft-scroll flex-1 space-y-2 overflow-y-auto pr-1">
              {isLoading && (
                <p className="py-8 text-center text-xs text-[var(--muted)]">
                  Carregando atendimentos...
                </p>
              )}
              {!isLoading && filteredSessions.length === 0 && (
                <p className="py-10 text-center text-xs text-[var(--muted)]">
                  Nenhuma sessão encontrada neste filtro.
                </p>
              )}
              {filteredSessions.map((row) => {
                const statusInfo = STATUS_LABELS[row.status];
                const isSelected = row.session_id === selectedSessionId;
                return (
                  <button
                    key={row.session_id}
                    type="button"
                    onClick={() => void handleSelectSession(row.session_id)}
                    className={`w-full rounded-2xl border p-3.5 text-left transition-all duration-200 ${
                      isSelected
                        ? "border-[var(--primary)] bg-[var(--primary-soft)]/45"
                        : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)]/60"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-[var(--text)]">
                        {row.patient_name || "Paciente em triagem"}
                      </span>
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold ${statusInfo.className}`}
                      >
                        {statusInfo.label}
                      </span>
                    </div>

                    <p className="mt-1 truncate text-xs text-[var(--muted)]">
                      {row.chief_complaint || "Coletando informações iniciais..."}
                    </p>

                    <div className="mt-2.5 flex items-center justify-between text-[11px] text-[var(--muted)]">
                      <span>
                        {row.fast_turns} fast · {row.llm_turns} LLM
                      </span>
                      <span>{formatDateTimeBr(row.updated_at)}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Right Live Conversation & Takeover */}
          <section
            className="flex h-[calc(100vh-230px)] min-h-[520px] flex-col overflow-hidden rounded-[22px] border border-[var(--border)] bg-[var(--surface)]"
            style={{ boxShadow: "var(--shadow-card)" }}
          >
            {!activeSession ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 text-sm text-[var(--muted)]">
                <MessageSquare className="h-8 w-8 opacity-40" />
                Selecione um atendimento na fila para acompanhar ou assumir.
              </div>
            ) : (
              <>
                {/* Session controls */}
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-3.5">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-semibold text-[var(--text)]">
                        {activeSession.patient_info.full_name || "Paciente sem nome informado"}
                      </h2>
                      <span
                        className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${
                          STATUS_LABELS[activeSession.status].className
                        }`}
                      >
                        {STATUS_LABELS[activeSession.status].label}
                      </span>
                    </div>
                    <p className="mt-0.5 text-xs text-[var(--muted)]">
                      Nasc: {formatDobBr(activeSession.patient_info.date_of_birth)} ·{" "}
                      {activeSession.patient_info.coverage_type === "convenio"
                        ? `Convênio (${activeSession.patient_info.insurance_payer || "-"})`
                        : activeSession.patient_info.coverage_type === "particular"
                          ? "Particular"
                          : "Modalidade pendente"}
                      {activeSession.handoff_reason
                        ? ` · Motivo handoff: ${activeSession.handoff_reason}`
                        : ""}
                    </p>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setShowDrawer((prev) => !prev)}
                      className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-semibold transition-colors ${
                        showDrawer
                          ? "border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary)]"
                          : "border-[var(--border)] bg-[var(--surface)] text-[var(--text)] hover:bg-[var(--surface-muted)]"
                      }`}
                    >
                      <ClipboardList className="h-3.5 w-3.5" />
                      Ficha & Agenda
                    </button>

                    {canTakeover && (
                      <button
                        type="button"
                        disabled={actionBusy}
                        onClick={() =>
                          void runStaffAction(() =>
                            staffTakeoverSession(
                              activeSession.session_id,
                              staffName.trim() || "Recepção",
                              staffKey,
                            ),
                          )
                        }
                        className="inline-flex items-center gap-1.5 rounded-xl bg-[var(--primary)] px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-[var(--primary-hover)] disabled:opacity-50"
                      >
                        <Headset className="h-3.5 w-3.5" />
                        Assumir atendimento
                      </button>
                    )}

                    {canRelease && (
                      <button
                        type="button"
                        disabled={actionBusy}
                        onClick={() =>
                          void runStaffAction(() =>
                            staffReleaseToAi(activeSession.session_id, staffKey),
                          )
                        }
                        className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2 text-xs font-semibold text-[var(--text)] transition-colors hover:bg-[var(--border)]/60 disabled:opacity-50"
                      >
                        <Bot className="h-3.5 w-3.5 text-[var(--primary)]" />
                        Devolver para Iara
                      </button>
                    )}

                    {canClose && (
                      <button
                        type="button"
                        disabled={actionBusy}
                        onClick={() =>
                          void runStaffAction(() =>
                            staffCloseSession(activeSession.session_id, staffKey),
                          )
                        }
                        className="inline-flex items-center gap-1.5 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 transition-colors hover:bg-rose-100 disabled:opacity-50"
                      >
                        <XCircle className="h-3.5 w-3.5" />
                        Encerrar
                      </button>
                    )}
                  </div>
                </div>

                {/* Chat + Collapsible Ficha & Assisted Scheduling Drawer */}
                <div className="flex min-h-0 flex-1 overflow-hidden">
                  <div className="flex min-w-0 flex-1 flex-col">
                    <div className="soft-scroll flex-1 space-y-3 overflow-y-auto px-5 py-4">
                      {activeSession.messages.map((m, idx) => (
                        <div
                          key={`${m.id ?? "msg"}-${idx}`}
                          className={`flex flex-col gap-1 ${
                            m.actor === "user"
                              ? "items-start"
                              : m.actor === "system"
                                ? "items-center"
                                : "items-end"
                          }`}
                        >
                          {m.actor === "system" ? (
                            <span className="rounded-full bg-[var(--surface-muted)] px-3 py-1 text-[11px] font-medium text-[var(--muted)]">
                              {m.message}
                            </span>
                          ) : (
                            <>
                              <div
                                className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm ${
                                  m.actor === "user"
                                    ? "border border-[var(--border)] bg-[var(--surface-muted)] text-[var(--text)]"
                                    : m.actor === "human_agent"
                                      ? "bg-emerald-700 text-white"
                                      : "bg-[var(--primary)] text-white"
                                }`}
                              >
                                {m.message}
                              </div>
                              <div className="flex items-center gap-2 text-[11px] text-[var(--muted)]">
                                <span>
                                  {m.actor === "user"
                                    ? "Paciente"
                                    : m.actor === "human_agent"
                                      ? "Atendente humano"
                                      : "Iara IA"}
                                </span>
                                {m.actor === "assistant" && idx > 0 && (
                                  <span
                                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                                      m.used_llm
                                        ? "bg-indigo-50 text-indigo-700"
                                        : "bg-emerald-50 text-emerald-700"
                                    }`}
                                  >
                                    {m.used_llm ? "1 chamada · LLM" : "0 tokens · fast-path"}
                                  </span>
                                )}
                              </div>
                            </>
                          )}
                        </div>
                      ))}
                    </div>

                    {/* Human reply bar when in takeover mode */}
                    <div className="border-t border-[var(--border)] bg-[var(--surface)] px-4 py-3">
                      {activeSession.status === "human_takeover" ? (
                        <form onSubmit={(e) => void handleSendStaffReply(e)} className="flex gap-2">
                          <input
                            type="text"
                            value={replyText}
                            onChange={(e) => setReplyText(e.target.value)}
                            placeholder={`Responder como ${staffName || "Recepção"}...`}
                            className="flex-1 rounded-xl border border-[var(--border)] bg-[var(--bg)] px-3.5 py-2.5 text-sm focus:border-[var(--primary)] focus:outline-none"
                          />
                          <button
                            type="submit"
                            disabled={!replyText.trim() || actionBusy}
                            className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-700 px-4 py-2.5 text-xs font-semibold text-white transition-colors hover:bg-emerald-800 disabled:opacity-45"
                          >
                            <Send className="h-3.5 w-3.5" />
                            Enviar
                          </button>
                        </form>
                      ) : (
                        <p className="text-center text-xs text-[var(--muted)]">
                          {activeSession.is_finished
                            ? "Sessão finalizada."
                            : "Clique em 'Assumir atendimento' acima para responder diretamente ao paciente."}
                        </p>
                      )}
                    </div>
                  </div>

                  {showDrawer && (
                    <aside className="soft-scroll w-80 shrink-0 overflow-y-auto border-l border-[var(--border)] bg-[var(--bg)]/70 p-4">
                      <div className="mb-3 flex items-center justify-between gap-2">
                        <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                          Ficha do Paciente
                        </h3>
                        {!activeSession.is_finished && (
                          <button
                            type="button"
                            disabled={actionBusy}
                            onClick={() => void handleExtractWithCopilot()}
                            className="inline-flex items-center gap-1 rounded-xl bg-[var(--primary-soft)] px-2.5 py-1.5 text-[11px] font-semibold text-[var(--primary)] transition-colors hover:bg-[var(--primary)] hover:text-white disabled:opacity-50"
                          >
                            <Sparkles className="h-3 w-3" />
                            Preencher com IA
                          </button>
                        )}
                      </div>

                      {copilotNotice && (
                        <div className="animate-fade-scale mb-3 rounded-xl border border-teal-200 bg-teal-50 px-3 py-2 text-[11px] font-medium text-teal-800">
                          {copilotNotice}
                        </div>
                      )}

                      <form onSubmit={(e) => void handleSavePatientDraft(e)} className="space-y-2.5">
                        <div>
                          <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                            Nome completo
                          </label>
                          <input
                            type="text"
                            value={patientDraft.full_name ?? ""}
                            onChange={(e) =>
                              setPatientDraft((p) => ({ ...p, full_name: e.target.value || null }))
                            }
                            disabled={activeSession.is_finished}
                            className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                          />
                        </div>

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                              Nascimento
                            </label>
                            <input
                              type="date"
                              value={patientDraft.date_of_birth ?? ""}
                              onChange={(e) =>
                                setPatientDraft((p) => ({
                                  ...p,
                                  date_of_birth: e.target.value || null,
                                }))
                              }
                              disabled={activeSession.is_finished}
                              className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                            />
                          </div>

                          <div>
                            <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                              Atendimento
                            </label>
                            <select
                              value={patientDraft.coverage_type ?? ""}
                              onChange={(e) =>
                                setPatientDraft((p) => ({
                                  ...p,
                                  coverage_type: (e.target.value || null) as CoverageType | null,
                                }))
                              }
                              disabled={activeSession.is_finished}
                              className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                            >
                              <option value="">Pendente</option>
                              <option value="particular">Particular</option>
                              <option value="convenio">Convênio</option>
                            </select>
                          </div>
                        </div>

                        {patientDraft.coverage_type === "convenio" && (
                          <div className="grid grid-cols-2 gap-2">
                            <div>
                              <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                                Operadora
                              </label>
                              <input
                                type="text"
                                value={patientDraft.insurance_payer ?? ""}
                                onChange={(e) =>
                                  setPatientDraft((p) => ({
                                    ...p,
                                    insurance_payer: e.target.value || null,
                                  }))
                                }
                                disabled={activeSession.is_finished}
                                className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                              />
                            </div>
                            <div>
                              <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                                Carteirinha
                              </label>
                              <input
                                type="text"
                                value={patientDraft.insurance_id ?? ""}
                                onChange={(e) =>
                                  setPatientDraft((p) => ({
                                    ...p,
                                    insurance_id: e.target.value || null,
                                  }))
                                }
                                disabled={activeSession.is_finished}
                                className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                              />
                            </div>
                          </div>
                        )}

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                              Telefone / WhatsApp
                            </label>
                            <input
                              type="tel"
                              placeholder="(11) 99999-9999"
                              value={patientDraft.phone ?? ""}
                              onChange={(e) =>
                                setPatientDraft((p) => ({
                                  ...p,
                                  phone: e.target.value || null,
                                }))
                              }
                              disabled={activeSession.is_finished}
                              className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                            />
                          </div>
                          <div>
                            <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                              E-mail
                            </label>
                            <input
                              type="email"
                              placeholder="opcional@email.com"
                              value={patientDraft.email ?? ""}
                              onChange={(e) =>
                                setPatientDraft((p) => ({
                                  ...p,
                                  email: e.target.value || null,
                                }))
                              }
                              disabled={activeSession.is_finished}
                              className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                            />
                          </div>
                        </div>

                        <div>
                          <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                            Endereço
                          </label>
                          <input
                            type="text"
                            value={patientDraft.address ?? ""}
                            onChange={(e) =>
                              setPatientDraft((p) => ({ ...p, address: e.target.value || null }))
                            }
                            disabled={activeSession.is_finished}
                            className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                          />
                        </div>

                        <div>
                          <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                            Queixa principal
                          </label>
                          <textarea
                            rows={2}
                            value={patientDraft.chief_complaint ?? ""}
                            onChange={(e) =>
                              setPatientDraft((p) => ({
                                ...p,
                                chief_complaint: e.target.value || null,
                              }))
                            }
                            disabled={activeSession.is_finished}
                            className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                          />
                        </div>

                        <div>
                          <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                            Especialidade sugerida
                          </label>
                          <select
                            value={patientDraft.preferred_specialty ?? ""}
                            onChange={(e) => {
                              const nextSpec = (e.target.value || null) as Specialty | null;
                              setPatientDraft((p) => ({ ...p, preferred_specialty: nextSpec }));
                              if (nextSpec) setBookSpecialty(nextSpec);
                            }}
                            disabled={activeSession.is_finished}
                            className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none disabled:opacity-60"
                          >
                            <option value="">Não definida</option>
                            <option value="clinica_geral">Clínica Geral</option>
                            <option value="ortopedia">Ortopedia</option>
                            <option value="dermatologia">Dermatologia</option>
                            <option value="cardiologia">Cardiologia</option>
                          </select>
                        </div>

                        {!activeSession.is_finished && (
                          <button
                            type="submit"
                            disabled={actionBusy}
                            className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] py-2 text-xs font-semibold text-[var(--text)] transition-colors hover:bg-[var(--surface-muted)] disabled:opacity-50"
                          >
                            <Save className="h-3.5 w-3.5 text-[var(--primary)]" />
                            Salvar ficha
                          </button>
                        )}
                      </form>

                      <div className="my-4 h-px bg-[var(--border)]" />

                      <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                        Agendamento Assistido
                      </h3>

                      {activeSession.booked_appointment ? (
                        <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 p-3 text-xs text-emerald-950">
                          <p className="font-semibold text-emerald-900">
                            Confirmado · #
                            {activeSession.booked_appointment.appointment_id
                              .slice(0, 8)
                              .toUpperCase()}
                          </p>
                          <p className="mt-1">
                            {activeSession.booked_appointment.specialty_label} ·{" "}
                            {activeSession.booked_appointment.doctor_name}
                          </p>
                          <p className="mt-0.5 text-emerald-800">
                            {formatDateTimeBr(activeSession.booked_appointment.starts_at)}
                          </p>
                        </div>
                      ) : activeSession.is_finished ? (
                        <p className="text-xs text-[var(--muted)]">
                          Sessão encerrada sem agendamento.
                        </p>
                      ) : (
                        <div className="space-y-2.5">
                          <div>
                            <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                              Filtrar especialidade
                            </label>
                            <select
                              value={bookSpecialty}
                              onChange={(e) => setBookSpecialty(e.target.value as Specialty)}
                              className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none"
                            >
                              <option value="clinica_geral">Clínica Geral</option>
                              <option value="ortopedia">Ortopedia</option>
                              <option value="dermatologia">Dermatologia</option>
                              <option value="cardiologia">Cardiologia</option>
                            </select>
                          </div>

                          <div>
                            <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                              Horário disponível
                            </label>
                            <select
                              value={selectedSlotId}
                              onChange={(e) =>
                                setSelectedSlotId(e.target.value ? Number(e.target.value) : "")
                              }
                              className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs focus:border-[var(--primary)] focus:outline-none"
                            >
                              {drawerSlots.length === 0 ? (
                                <option value="">Sem horários livres</option>
                              ) : (
                                drawerSlots.map((slot) => (
                                  <option key={slot.slot_id} value={slot.slot_id}>
                                    {slot.display_label}
                                  </option>
                                ))
                              )}
                            </select>
                          </div>

                          <button
                            type="button"
                            disabled={!selectedSlotId || actionBusy}
                            onClick={() => void handleDirectBook()}
                            className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl bg-[var(--primary)] py-2 text-xs font-semibold text-white transition-colors hover:bg-[var(--primary-hover)] disabled:opacity-45"
                          >
                            <CalendarPlus className="h-3.5 w-3.5" />
                            Agendar horário
                          </button>

                          <button
                            type="button"
                            disabled={drawerSlots.length === 0 || actionBusy}
                            onClick={() => void handleOfferSlotsInChat()}
                            className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl bg-[var(--primary-soft)] py-2 text-xs font-semibold text-[var(--primary)] transition-colors hover:bg-[var(--primary)] hover:text-white disabled:opacity-45"
                          >
                            <Send className="h-3.5 w-3.5" />
                            Enviar horários no chat
                          </button>
                        </div>
                      )}
                    </aside>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      )}

      {/* TAB 2: CALENDAR */}
      {tab === "calendar" && (
        <div className="animate-fade-scale space-y-5">
          {(actionError || agendaNotice) && (
            <div
              className={`rounded-2xl border px-4 py-3 text-xs font-medium ${
                actionError
                  ? "border-rose-200 bg-rose-50 text-rose-700"
                  : "border-teal-200 bg-teal-50/70 text-teal-800"
              }`}
            >
              {actionError || agendaNotice}
            </div>
          )}

          <section
            className="rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-5"
            style={{ boxShadow: "var(--shadow-card)" }}
          >
            <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--text)]">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              Consultas Confirmadas ({appointments.length})
            </h2>

            {appointments.length === 0 ? (
              <p className="mt-4 text-xs text-[var(--muted)]">
                Nenhuma consulta agendada até o momento.
              </p>
            ) : (
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {appointments.map((appt) => {
                  const isCancelling = cancellingId === appt.appointment_id;
                  const isRescheduling = reschedulingId === appt.appointment_id;
                  return (
                    <div
                      key={appt.appointment_id}
                      className="flex flex-col justify-between rounded-2xl border border-emerald-200/80 bg-emerald-50/40 p-4"
                    >
                      <div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="font-semibold text-emerald-800">
                            #{appt.appointment_id.slice(0, 8).toUpperCase()}
                          </span>
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800">
                            {appt.specialty_label}
                          </span>
                        </div>
                        <p className="mt-2 text-sm font-semibold text-[var(--text)]">
                          {appt.patient_name || "Paciente sem nome"}
                        </p>
                        <p className="mt-0.5 text-xs text-[var(--muted)]">{appt.doctor_name}</p>
                        <p className="mt-1 flex items-center gap-1.5 text-xs text-[var(--muted)]">
                          <Clock className="h-3.5 w-3.5" />
                          {formatDateTimeBr(appt.starts_at)}
                        </p>
                      </div>

                      {!isCancelling && !isRescheduling && (
                        <div className="mt-3 flex gap-2 border-t border-emerald-200/60 pt-3">
                          <button
                            type="button"
                            disabled={actionBusy}
                            onClick={() => void handleOpenReschedule(appt)}
                            className="inline-flex flex-1 items-center justify-center gap-1 rounded-xl border border-emerald-300/80 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-emerald-800 transition-colors hover:bg-emerald-50 disabled:opacity-45"
                          >
                            <CalendarPlus className="h-3 w-3" />
                            Remarcar
                          </button>
                          <button
                            type="button"
                            disabled={actionBusy}
                            onClick={() => {
                              setReschedulingId(null);
                              setCancellingId(appt.appointment_id);
                              setCancelReason("");
                            }}
                            className="inline-flex flex-1 items-center justify-center gap-1 rounded-xl border border-rose-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-rose-700 transition-colors hover:bg-rose-50 disabled:opacity-45"
                          >
                            <XCircle className="h-3 w-3" />
                            Cancelar
                          </button>
                        </div>
                      )}

                      {isRescheduling && (
                        <div className="mt-3 space-y-2 border-t border-emerald-200/60 pt-3">
                          <select
                            value={rescheduleTargetSlotId}
                            onChange={(e) =>
                              setRescheduleTargetSlotId(
                                e.target.value ? Number(e.target.value) : "",
                              )
                            }
                            disabled={actionBusy || rescheduleSlots.length === 0}
                            className="w-full rounded-xl border border-[var(--border)] bg-white px-2.5 py-1.5 text-[11px] focus:border-[var(--primary)] focus:outline-none"
                          >
                            {rescheduleSlots.length === 0 ? (
                              <option value="">Sem horários livres</option>
                            ) : (
                              rescheduleSlots.map((s) => (
                                <option key={s.slot_id} value={s.slot_id}>
                                  {s.specialty_label} · {s.display_label}
                                </option>
                              ))
                            )}
                          </select>
                          <div className="flex gap-1.5">
                            <button
                              type="button"
                              disabled={!rescheduleTargetSlotId || actionBusy}
                              onClick={() => void handleConfirmReschedule(appt.appointment_id)}
                              className="flex-1 rounded-xl bg-[var(--primary)] py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-[var(--primary-hover)] disabled:opacity-45"
                            >
                              Confirmar troca
                            </button>
                            <button
                              type="button"
                              disabled={actionBusy}
                              onClick={() => setReschedulingId(null)}
                              className="rounded-xl border border-[var(--border)] bg-white px-2.5 py-1.5 text-[11px] text-[var(--muted)] hover:text-[var(--text)]"
                            >
                              Voltar
                            </button>
                          </div>
                        </div>
                      )}

                      {isCancelling && (
                        <div className="mt-3 space-y-2 border-t border-rose-200/70 pt-3">
                          <input
                            type="text"
                            placeholder="Motivo (opcional)"
                            value={cancelReason}
                            onChange={(e) => setCancelReason(e.target.value)}
                            disabled={actionBusy}
                            className="w-full rounded-xl border border-rose-200 bg-white px-2.5 py-1.5 text-[11px] focus:border-rose-400 focus:outline-none"
                          />
                          <div className="flex gap-1.5">
                            <button
                              type="button"
                              disabled={actionBusy}
                              onClick={() =>
                                void handleConfirmCancelAppointment(appt.appointment_id)
                              }
                              className="flex-1 rounded-xl bg-rose-600 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-rose-700 disabled:opacity-45"
                            >
                              Confirmar
                            </button>
                            <button
                              type="button"
                              disabled={actionBusy}
                              onClick={() => {
                                setCancellingId(null);
                                setCancelReason("");
                              }}
                              className="rounded-xl border border-[var(--border)] bg-white px-2.5 py-1.5 text-[11px] text-[var(--muted)] hover:text-[var(--text)]"
                            >
                              Voltar
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <section
            className="rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-5"
            style={{ boxShadow: "var(--shadow-card)" }}
          >
            <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--text)]">
              <Plus className="h-4 w-4 text-[var(--primary)]" />
              Gerar Horários em Lote
            </h2>
            <form
              onSubmit={(e) => void handleCreateSlotsBatch(e)}
              className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-6"
            >
              <div>
                <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                  Especialidade
                </label>
                <select
                  value={batchSpecialty}
                  onChange={(e) => setBatchSpecialty(e.target.value as Specialty)}
                  className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-xs focus:border-[var(--primary)] focus:outline-none"
                >
                  <option value="clinica_geral">Clínica Geral</option>
                  <option value="ortopedia">Ortopedia</option>
                  <option value="dermatologia">Dermatologia</option>
                  <option value="cardiologia">Cardiologia</option>
                </select>
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                  Profissional
                </label>
                <input
                  type="text"
                  required
                  value={batchDoctor}
                  onChange={(e) => setBatchDoctor(e.target.value)}
                  className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-xs focus:border-[var(--primary)] focus:outline-none"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                  Data
                </label>
                <input
                  type="date"
                  required
                  value={batchDate}
                  onChange={(e) => setBatchDate(e.target.value)}
                  className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-xs focus:border-[var(--primary)] focus:outline-none"
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                    Início (h)
                  </label>
                  <input
                    type="number"
                    min={6}
                    max={21}
                    value={batchStartHour}
                    onChange={(e) => setBatchStartHour(Number(e.target.value))}
                    className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-xs focus:border-[var(--primary)] focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                    Fim (h)
                  </label>
                  <input
                    type="number"
                    min={7}
                    max={22}
                    value={batchEndHour}
                    onChange={(e) => setBatchEndHour(Number(e.target.value))}
                    className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-xs focus:border-[var(--primary)] focus:outline-none"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium text-[var(--muted)]">
                  Intervalo (min)
                </label>
                <select
                  value={batchInterval}
                  onChange={(e) => setBatchInterval(Number(e.target.value))}
                  className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-xs focus:border-[var(--primary)] focus:outline-none"
                >
                  <option value={15}>15 min</option>
                  <option value={20}>20 min</option>
                  <option value={30}>30 min</option>
                  <option value={45}>45 min</option>
                  <option value={60}>60 min</option>
                </select>
              </div>
              <div className="flex items-end">
                <button
                  type="submit"
                  disabled={actionBusy || !batchDate || !batchDoctor.trim()}
                  className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl bg-[var(--primary)] px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-[var(--primary-hover)] disabled:opacity-45"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Adicionar
                </button>
              </div>
            </form>
          </section>

          <section
            className="rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-5"
            style={{ boxShadow: "var(--shadow-card)" }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--text)]">
                <Calendar className="h-4 w-4 text-[var(--primary)]" />
                Horários Livres na Agenda ({slots.length})
              </h2>
              <div className="flex flex-wrap gap-1.5">
                {SPECIALTY_FILTERS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setSpecialtyFilter(opt.value)}
                    className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                      specialtyFilter === opt.value
                        ? "bg-[var(--primary)] text-white"
                        : "bg-[var(--surface-muted)] text-[var(--muted)] hover:text-[var(--text)]"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {slots.map((slot) => (
                <div
                  key={slot.slot_id}
                  className="flex items-start justify-between gap-2 rounded-2xl border border-[var(--border)] bg-[var(--surface-muted)]/45 p-3.5"
                >
                  <div className="min-w-0">
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--primary)]">
                      {slot.specialty_label}
                    </span>
                    <p className="mt-1 truncate text-sm font-semibold text-[var(--text)]">
                      {slot.doctor_name}
                    </p>
                    <p className="mt-1 text-xs text-[var(--muted)]">
                      {formatDateTimeBr(slot.starts_at)}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={actionBusy}
                    onClick={() => void handleDeleteSlot(slot.slot_id)}
                    title="Remover horário livre"
                    aria-label="Remover horário livre"
                    className="rounded-lg p-1.5 text-[var(--muted)] transition-colors hover:bg-rose-50 hover:text-rose-600 disabled:opacity-45"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </section>

          <section
            className="rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-5"
            style={{ boxShadow: "var(--shadow-card)" }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--text)]">
                  <Bell className="h-4 w-4 text-[var(--primary)]" />
                  Lembretes Programados ({reminders.length})
                </h2>
                <p className="mt-0.5 text-xs text-[var(--muted)]">
                  Avisos automáticos 24h e 2h antes da consulta.
                </p>
              </div>
              <button
                type="button"
                disabled={actionBusy || reminders.length === 0}
                onClick={() => void handleDispatchDueReminders()}
                className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-xs font-semibold text-[var(--text)] transition-colors hover:bg-[var(--surface-muted)] disabled:opacity-45"
              >
                <Send className="h-3.5 w-3.5 text-[var(--primary)]" />
                Processar vencidos
              </button>
            </div>

            {reminders.length === 0 ? (
              <p className="mt-4 text-xs text-[var(--muted)]">
                Nenhum lembrete programado no momento.
              </p>
            ) : (
              <div className="mt-4 space-y-2.5">
                {reminders.map((rem) => {
                  const statusBadge =
                    rem.status === "sent"
                      ? { label: "Enviado", cls: "bg-emerald-100 text-emerald-800" }
                      : rem.status === "cancelled"
                        ? { label: "Cancelado", cls: "bg-slate-100 text-slate-600" }
                        : rem.status === "failed"
                          ? { label: "Falhou", cls: "bg-rose-100 text-rose-700" }
                          : { label: "Pendente", cls: "bg-amber-100 text-amber-800" };
                  return (
                    <div
                      key={rem.reminder_id}
                      className="flex flex-col justify-between gap-3 rounded-2xl border border-[var(--border)] bg-[var(--surface-muted)]/35 p-3.5 sm:flex-row sm:items-center"
                    >
                      <div className="min-w-0 space-y-1">
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <span className="font-semibold text-[var(--text)]">
                            {rem.patient_name}
                          </span>
                          <span className="rounded-full bg-[var(--primary-soft)] px-2 py-0.5 text-[10px] font-semibold uppercase text-[var(--primary)]">
                            {rem.reminder_type}
                          </span>
                          <span
                            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${statusBadge.cls}`}
                          >
                            {statusBadge.label}
                          </span>
                          <span className="rounded-full border border-[var(--border)] bg-white px-2 py-0.5 text-[10px] text-[var(--muted)]">
                            {rem.channel}
                          </span>
                        </div>
                        <p className="text-xs text-[var(--text)]/85">{rem.message_preview}</p>
                        <p className="text-[11px] text-[var(--muted)]">
                          Previsto: {formatDateTimeBr(rem.scheduled_for)}
                          {rem.recipient_phone ? ` · Tel: ${rem.recipient_phone}` : ""}
                          {rem.recipient_email ? ` · E-mail: ${rem.recipient_email}` : ""}
                        </p>
                      </div>
                      {(rem.status === "pending" || rem.status === "failed") && (
                        <button
                          type="button"
                          disabled={actionBusy}
                          onClick={() => void handleSendReminderNow(rem.reminder_id)}
                          className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl bg-[var(--primary-soft)] px-3 py-1.5 text-xs font-semibold text-[var(--primary)] transition-colors hover:bg-[var(--primary)] hover:text-white disabled:opacity-45"
                        >
                          <Send className="h-3.5 w-3.5" />
                          Enviar agora
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      )}

      {/* TAB 3: AI METRICS */}
      {tab === "metrics" && (
        <div className="animate-fade-scale space-y-5">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div
              className="rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-5"
              style={{ boxShadow: "var(--shadow-card)" }}
            >
              <div className="flex items-center justify-between text-xs text-[var(--muted)]">
                <span>Total de Sessões</span>
                <Activity className="h-4 w-4 text-[var(--primary)]" />
              </div>
              <p className="mt-2 text-2xl font-bold text-[var(--text)]">
                {metrics.totalSessions}
              </p>
            </div>

            <div
              className="rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-5"
              style={{ boxShadow: "var(--shadow-card)" }}
            >
              <div className="flex items-center justify-between text-xs text-[var(--muted)]">
                <span>Economia Fast-Path</span>
                <Cpu className="h-4 w-4 text-emerald-600" />
              </div>
              <p className="mt-2 text-2xl font-bold text-emerald-700">{metrics.savingsPct}%</p>
              <p className="mt-1 text-[11px] text-[var(--muted)]">
                {metrics.totalFast} de {metrics.totalTurns} turnos sem custo de LLM
              </p>
            </div>

            <div
              className="rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-5"
              style={{ boxShadow: "var(--shadow-card)" }}
            >
              <div className="flex items-center justify-between text-xs text-[var(--muted)]">
                <span>Consultas Agendadas</span>
                <Calendar className="h-4 w-4 text-[var(--primary)]" />
              </div>
              <p className="mt-2 text-2xl font-bold text-[var(--text)]">
                {metrics.totalAppointments}
              </p>
            </div>

            <div
              className="rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-5"
              style={{ boxShadow: "var(--shadow-card)" }}
            >
              <div className="flex items-center justify-between text-xs text-[var(--muted)]">
                <span>Handoffs Humanos</span>
                <UserCheck className="h-4 w-4 text-amber-600" />
              </div>
              <p className="mt-2 text-2xl font-bold text-[var(--text)]">{metrics.escalations}</p>
              <p className="mt-1 text-[11px] text-[var(--muted)]">
                {metrics.expiredCount} expiradas por inatividade
              </p>
            </div>
          </div>

          <section
            className="rounded-[22px] border border-[var(--border)] bg-[var(--surface)] p-6"
            style={{ boxShadow: "var(--shadow-card)" }}
          >
            <h2 className="text-sm font-semibold text-[var(--text)]">
              Distribuição da Arquitetura Híbrida
            </h2>
            <p className="mt-1 text-xs text-[var(--muted)]">
              Proporção entre respostas determinísticas (0 tokens) e chamadas estruturadas ao
              Vercel AI Gateway.
            </p>
            <div className="mt-4 h-4 w-full overflow-hidden rounded-full bg-indigo-100">
              <div
                className="h-full rounded-full bg-emerald-500 transition-all duration-500"
                style={{ width: `${metrics.savingsPct}%` }}
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-5 text-xs text-[var(--muted)]">
              <span className="inline-flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />0 tokens · fast-path (
                {metrics.totalFast})
              </span>
              <span className="inline-flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full bg-indigo-400" />1 chamada · LLM (
                {metrics.totalLlm})
              </span>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
