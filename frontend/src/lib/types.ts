export type ConversationStatus =
  | "collecting"
  | "confirming"
  | "scheduling"
  | "waiting_human"
  | "human_takeover"
  | "emergency"
  | "completed";

export type Specialty = "clinica_geral" | "ortopedia" | "dermatologia" | "cardiologia";

export type CoverageType = "convenio" | "particular";

export type MessageActor = "user" | "assistant" | "human_agent" | "system";

export interface PatientInfo {
  full_name: string | null;
  date_of_birth: string | null;
  address: string | null;
  coverage_type: CoverageType | null;
  insurance_payer: string | null;
  insurance_id: string | null;
  chief_complaint: string | null;
  preferred_specialty: Specialty | null;
}

export interface AppointmentSlotDTO {
  slot_id: number;
  specialty: Specialty;
  specialty_label: string;
  doctor_name: string;
  starts_at: string;
  display_label: string;
}

export interface BookedAppointmentDTO {
  appointment_id: string;
  slot_id: number;
  specialty: Specialty;
  specialty_label: string;
  doctor_name: string;
  starts_at: string;
  status: string;
}

export interface QuickReplyOption {
  label: string;
  value: string;
}

export interface MessageDTO {
  id: number | null;
  actor: MessageActor;
  message: string;
  used_llm: boolean;
  timestamp: string;
}

export interface AgentReply {
  message: string;
  status: ConversationStatus;
  patient_info: PatientInfo;
  is_finished: boolean;
  used_llm: boolean;
  booked_appointment: BookedAppointmentDTO | null;
  error: string | null;
}

export interface SessionResponse {
  session_id: string;
  clinic_name: string;
  status: ConversationStatus;
  patient_info: PatientInfo;
  missing_fields: string[];
  available_slots: AppointmentSlotDTO[];
  booked_appointment: BookedAppointmentDTO | null;
  handoff_reason: string | null;
  assigned_staff_name: string | null;
  quick_replies: QuickReplyOption[];
  messages: MessageDTO[];
  is_finished: boolean;
}

export interface TurnResponse {
  session_id: string;
  reply: AgentReply;
  missing_fields: string[];
  available_slots: AppointmentSlotDTO[];
  quick_replies: QuickReplyOption[];
}

export interface HealthResponse {
  status: string;
  provider: string;
  model: string;
  clinic_name: string;
}

export interface StaffSessionSummary {
  session_id: string;
  clinic_name: string;
  status: ConversationStatus;
  patient_name: string | null;
  chief_complaint: string | null;
  preferred_specialty: Specialty | null;
  handoff_reason: string | null;
  assigned_staff_name: string | null;
  turn_count: number;
  fast_turns: number;
  llm_turns: number;
  booked_appointment: BookedAppointmentDTO | null;
  updated_at: string;
}
