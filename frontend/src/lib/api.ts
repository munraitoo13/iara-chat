import type {
  AppointmentSlotDTO,
  BookedAppointmentDTO,
  ConversationStatus,
  DispatchRemindersResponse,
  HealthResponse,
  ReminderDTO,
  SessionResponse,
  Specialty,
  StaffBatchSlotsRequest,
  StaffCreateSlotRequest,
  StaffExtractResponse,
  StaffSessionSummary,
  StaffUpdatePatientRequest,
  TurnResponse,
} from "@/lib/types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000/api/v1";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  staffKey?: string | null,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (staffKey) {
    headers.set("X-Staff-Key", staffKey);
  }

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!res.ok) {
    let detail = `Erro HTTP ${res.status}`;
    try {
      const data = (await res.json()) as { detail?: string };
      if (data?.detail) detail = data.detail;
    } catch {
      // Ignore non-JSON error payloads
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/health");
}

export function createSession(): Promise<SessionResponse> {
  return requestJson<SessionResponse>("/sessions", { method: "POST" });
}

export function getSession(sessionId: string, afterMessageId?: number): Promise<SessionResponse> {
  const query = afterMessageId !== undefined ? `?after_message_id=${afterMessageId}` : "";
  return requestJson<SessionResponse>(`/sessions/${sessionId}${query}`);
}

export function sendPatientMessage(sessionId: string, message: string): Promise<TurnResponse> {
  return requestJson<TurnResponse>(`/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function deleteSession(sessionId: string): Promise<void> {
  return requestJson<void>(`/sessions/${sessionId}`, { method: "DELETE" });
}

export function listAvailableSlots(specialty?: Specialty): Promise<AppointmentSlotDTO[]> {
  const query = specialty ? `?specialty=${specialty}&limit=20` : "?limit=20";
  return requestJson<AppointmentSlotDTO[]>(`/slots${query}`);
}

export function staffListSessions(
  staffKey: string | null,
  statusFilter?: ConversationStatus,
): Promise<StaffSessionSummary[]> {
  const query = statusFilter ? `?status=${statusFilter}` : "";
  return requestJson<StaffSessionSummary[]>(`/staff/sessions${query}`, {}, staffKey);
}

export function staffListAppointments(staffKey: string | null): Promise<BookedAppointmentDTO[]> {
  return requestJson<BookedAppointmentDTO[]>("/staff/appointments", {}, staffKey);
}

export function staffGetSession(
  sessionId: string,
  staffKey: string | null,
): Promise<SessionResponse> {
  return requestJson<SessionResponse>(`/sessions/${sessionId}`, {}, staffKey);
}

export function staffTakeoverSession(
  sessionId: string,
  staffName: string,
  staffKey: string | null,
): Promise<SessionResponse> {
  return requestJson<SessionResponse>(
    `/staff/sessions/${sessionId}/takeover`,
    {
      method: "POST",
      body: JSON.stringify({ staff_name: staffName }),
    },
    staffKey,
  );
}

export function staffSendMessage(
  sessionId: string,
  staffName: string,
  message: string,
  staffKey: string | null,
): Promise<SessionResponse> {
  return requestJson<SessionResponse>(
    `/staff/sessions/${sessionId}/messages`,
    {
      method: "POST",
      body: JSON.stringify({ staff_name: staffName, message }),
    },
    staffKey,
  );
}

export function staffReleaseToAi(
  sessionId: string,
  staffKey: string | null,
): Promise<SessionResponse> {
  return requestJson<SessionResponse>(
    `/staff/sessions/${sessionId}/release`,
    { method: "POST" },
    staffKey,
  );
}

export function staffCloseSession(
  sessionId: string,
  staffKey: string | null,
  closingNote?: string,
): Promise<SessionResponse> {
  return requestJson<SessionResponse>(
    `/staff/sessions/${sessionId}/close`,
    {
      method: "POST",
      body: JSON.stringify({ closing_note: closingNote ?? null }),
    },
    staffKey,
  );
}

export function staffUpdatePatient(
  sessionId: string,
  payload: StaffUpdatePatientRequest,
  staffKey: string | null,
): Promise<SessionResponse> {
  return requestJson<SessionResponse>(
    `/staff/sessions/${sessionId}/patient`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
    staffKey,
  );
}

export function staffBookAppointment(
  sessionId: string,
  slotId: number,
  staffName: string,
  staffKey: string | null,
): Promise<SessionResponse> {
  return requestJson<SessionResponse>(
    `/staff/sessions/${sessionId}/book`,
    {
      method: "POST",
      body: JSON.stringify({ slot_id: slotId, staff_name: staffName }),
    },
    staffKey,
  );
}

export function staffOfferSlots(
  sessionId: string,
  specialty: Specialty | null,
  staffKey: string | null,
): Promise<SessionResponse> {
  return requestJson<SessionResponse>(
    `/staff/sessions/${sessionId}/offer-slots`,
    {
      method: "POST",
      body: JSON.stringify({ specialty }),
    },
    staffKey,
  );
}

export function staffExtractPatient(
  sessionId: string,
  staffKey: string | null,
): Promise<StaffExtractResponse> {
  return requestJson<StaffExtractResponse>(
    `/staff/sessions/${sessionId}/extract`,
    { method: "POST" },
    staffKey,
  );
}

export function staffCreateSlot(
  payload: StaffCreateSlotRequest,
  staffKey: string | null,
): Promise<AppointmentSlotDTO> {
  return requestJson<AppointmentSlotDTO>(
    "/staff/slots",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    staffKey,
  );
}

export function staffCreateSlotsBatch(
  payload: StaffBatchSlotsRequest,
  staffKey: string | null,
): Promise<AppointmentSlotDTO[]> {
  return requestJson<AppointmentSlotDTO[]>(
    "/staff/slots/batch",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    staffKey,
  );
}

export function staffDeleteSlot(slotId: number, staffKey: string | null): Promise<void> {
  return requestJson<void>(`/staff/slots/${slotId}`, { method: "DELETE" }, staffKey);
}

export function staffCancelAppointment(
  appointmentId: string,
  reason: string | null,
  staffKey: string | null,
): Promise<BookedAppointmentDTO> {
  return requestJson<BookedAppointmentDTO>(
    `/staff/appointments/${appointmentId}/cancel`,
    {
      method: "POST",
      body: JSON.stringify({ reason: reason || null }),
    },
    staffKey,
  );
}

export function staffRescheduleAppointment(
  appointmentId: string,
  newSlotId: number,
  staffKey: string | null,
): Promise<BookedAppointmentDTO> {
  return requestJson<BookedAppointmentDTO>(
    `/staff/appointments/${appointmentId}/reschedule`,
    {
      method: "POST",
      body: JSON.stringify({ new_slot_id: newSlotId }),
    },
    staffKey,
  );
}

export function staffListReminders(
  staffKey: string | null,
  status?: string,
): Promise<ReminderDTO[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return requestJson<ReminderDTO[]>(`/staff/reminders${query}`, {}, staffKey);
}

export function staffSendReminderNow(
  reminderId: number,
  staffKey: string | null,
): Promise<ReminderDTO> {
  return requestJson<ReminderDTO>(
    `/staff/reminders/${reminderId}/send-now`,
    { method: "POST" },
    staffKey,
  );
}

export function staffDispatchDueReminders(
  staffKey: string | null,
): Promise<DispatchRemindersResponse> {
  return requestJson<DispatchRemindersResponse>(
    "/staff/reminders/dispatch-due",
    { method: "POST" },
    staffKey,
  );
}
