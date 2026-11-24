import type {
  AppointmentSlotDTO,
  BookedAppointmentDTO,
  ConversationStatus,
  HealthResponse,
  SessionResponse,
  Specialty,
  StaffSessionSummary,
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
