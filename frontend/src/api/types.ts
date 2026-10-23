export type Role = "CLIENT" | "ANALYST" | "RISK_MANAGER";

export interface User {
  id: string;
  login: string;
  full_name: string;
  role: Role;
  position_title: string | null;
}

export interface CompanyBrief {
  id: string;
  inn: string;
  short_name: string;
  kind: "LEGAL" | "IP";
  position: string;
  position_label: string;
  can_sign: boolean;
}

export interface Me {
  user: User;
  companies: CompanyBrief[];
}

export interface DemoAccount {
  login: string;
  password: string;
  full_name: string;
  role: Role;
  title: string;
  company: string | null;
  scenario: string;
  scenario_title?: string | null;
  scenario_hint?: string | null;
}

export interface Purpose {
  code: string;
  label: string;
  hint: string;
  icon: string;
}

export interface Dictionaries {
  purposes: Purpose[];
  doc_types: Record<string, string>;
  statuses: Record<string, { client: string; bank: string }>;
  key_rate: number;
  key_rate_note: string;
  demo_mode: boolean;
}

export interface Product {
  code: string;
  name: string;
  tagline: string;
  description: string;
  purposes: string[];
  min_amount: number;
  max_amount: number;
  min_term: number;
  max_term: number;
  base_spread: number;
  repayment_type: "ANNUITY" | "REVOLVING";
  collateral: "NONE" | "OPTIONAL" | "REQUIRED";
  features: string[];
  auto_decision_limit: number;
}

export interface TurnoverPoint {
  month: string;
  credit: number;
  debit: number;
}

export interface CompanyProfile {
  id: string;
  inn: string;
  ogrn: string;
  kind: "LEGAL" | "IP";
  short_name: string;
  full_name: string;
  okved_code: string;
  okved_name: string;
  registration_date: string;
  legal_address: string;
  client_since: string;
  segment: string;
  settlement_account: string;
  headcount: number | null;
  members: { full_name: string; position_label: string; can_sign: boolean }[];
  turnover: { months: number; avg_monthly: number; trend: number; cv: number; series: TurnoverPoint[] };
  preapproved: {
    product_code: string;
    product_name: string;
    amount: number;
    max_term: number;
    rate_from: number;
    note: string;
  } | null;
  revenue_estimate: number;
}

export interface MatchResult {
  product_code: string;
  product_name: string;
  tagline: string;
  features: string[];
  fit: "full" | "partial";
  notes: string[];
  amount: number;
  term_months: number;
  rate_from: number;
  rate_to: number;
  rate_typical: number;
  monthly_payment: number;
  total_interest: number;
  repayment_type: "ANNUITY" | "REVOLVING";
  preliminary_limit: number;
  required_documents: string[];
  collateral: string;
  recommended?: boolean;
}

export interface StatusView {
  title: string;
  now: string;
  next: string;
  tone: "progress" | "action" | "positive" | "negative";
  step: number;
  steps: string[];
  is_final: boolean;
}

export interface ScheduleRow {
  n: number;
  date: string;
  payment: number;
  principal: number;
  interest: number;
  balance: number;
}

export interface Offer {
  id: string;
  product_code: string;
  product_name: string;
  repayment_type: "ANNUITY" | "REVOLVING";
  amount: number;
  term_months: number;
  rate: number;
  monthly_payment: number;
  total_interest: number;
  conditions: string[];
  pricing: { parts: { label: string; value: number }[]; rate: number };
  is_counter: boolean;
  label: string;
  status: string;
  valid_until: string;
  schedule_preview: ScheduleRow[];
}

export interface AppEvent {
  id: number;
  created_at: string;
  event_type: string;
  title: string;
  actor_type: "CLIENT" | "ANALYST" | "SYSTEM";
  actor_name: string | null;
  from_status: string | null;
  to_status: string | null;
  client_visible?: boolean;
}

export interface InfoItem {
  code: string;
  type: "DOCUMENT" | "COMMENT" | "FIELD";
  doc_type?: string;
  field?: string;
  label: string;
  why?: string;
}

export interface InfoRequest {
  id: string;
  created_at: string;
  items: InfoItem[];
  message: string;
  status: "OPEN" | "ANSWERED" | "CANCELLED";
  answered_at: string | null;
  answer_comment: string | null;
  requested_by: string | null;
}

export interface DocumentInfo {
  id: string;
  doc_type: string;
  doc_type_label: string;
  file_name: string;
  content_type: string;
  size_bytes: number;
  uploaded_at: string;
}

export interface Loan {
  id: string;
  application_id: string;
  application_number: string;
  company_name: string;
  product_code: string;
  product_name: string;
  contract_number: string;
  loan_account: string;
  amount: number;
  term_months: number;
  rate: number;
  monthly_payment: number;
  issued_at: string;
  first_payment_date: string;
  status: string;
  repayment_type: "ANNUITY" | "REVOLVING";
  schedule: ScheduleRow[] | null;
}

export interface ApplicationSummary {
  id: string;
  number: string;
  company_id: string;
  company_name: string;
  product_code: string;
  product_name: string;
  amount: number;
  term_months: number;
  purpose: string;
  status: string;
  status_view: StatusView;
  stage_detail: string | null;
  created_at: string;
  status_changed_at: string;
  submitted_at: string | null;
}

export interface ClientDecision {
  outcome: string;
  outcome_label: string;
  kind: "AUTO" | "MANUAL";
  kind_label: string;
  decided_at: string;
  reasons: string[];
  positives: string[];
  negatives: string[];
  message: string | null;
  grade: string | null;
  grade_label: string | null;
}

export interface Application extends ApplicationSummary {
  purpose_details: string | null;
  declared_revenue: number | null;
  has_collateral: boolean;
  collateral_description: string | null;
  owner_guarantee: boolean;
  contact_name: string | null;
  contact_phone: string | null;
  product: Product;
  required_documents: { doc_type: string; label: string; attached: boolean }[];
  documents: DocumentInfo[];
  consents: { type: string; text: string; signed_at: string; signed_by: string }[];
  info_request: InfoRequest | null;
  offers: Offer[];
  decision: ClientDecision | null;
  events: AppEvent[];
  loan: Loan | null;
  permissions: {
    can_edit: boolean;
    can_submit: boolean;
    can_sign: boolean;
    can_cancel: boolean;
    can_answer: boolean;
    can_accept: boolean;
    can_request_signature: boolean;
    can_reapply: boolean;
    signers: string[];
    position_label: string;
  };
  review_due_at: string | null;
  parent_id: string | null;
  next_application: { id: string; number: string; status: string; status_title: string; is_live: boolean } | null;
}

// ------------------------------------------------------------------ конструктор условий

export interface Conditions {
  product_code: string;
  amount: number;
  term_months: number;
  has_collateral: boolean;
  owner_guarantee: boolean;
}

export type SimOutcome = "APPROVE" | "COUNTER_OFFER" | "MANUAL_REVIEW" | "NEEDS_INFO" | "DECLINE";

export interface SimResult extends Conditions {
  product_name: string;
  repayment_type: "ANNUITY" | "REVOLVING";
  collateral_option: "NONE" | "OPTIONAL" | "REQUIRED";
  outcome: SimOutcome;
  verdict: string;
  explanation: string;
  automatic: boolean;
  fixable: boolean;
  grade: string;
  rate: number;
  monthly_payment: number;
  total_interest: number;
  max_amount: number | null;
  payment_share: number | null;
  payment_share_cap: number;
  offers: {
    label: string;
    product_code: string;
    product_name: string;
    amount: number;
    term_months: number;
    rate: number;
    monthly_payment: number;
    is_counter: boolean;
  }[];
  key_rate: number;
}

export interface SimProduct {
  code: string;
  name: string;
  min_amount: number;
  max_amount: number;
  min_term: number;
  max_term: number;
  terms: number[];
  collateral: "NONE" | "OPTIONAL" | "REQUIRED";
  repayment_type: "ANNUITY" | "REVOLVING";
  auto_decision_limit: number;
}

export interface Simulation {
  base: Conditions;
  result: SimResult;
  suggestions: { code: string; title: string; effect: string; params: Conditions; result: SimResult }[];
  products: SimProduct[];
}

export interface SignChallenge {
  challenge_id: string;
  expires_at: string;
  demo_code: string | null;
  documents: string[];
}

export interface Contract {
  contract_number: string;
  company: Record<string, string>;
  offer: Offer;
  schedule: ScheduleRow[];
  documents: { code: string; title: string }[];
  signer: { full_name: string; position_label: string } | null;
}

export interface Message {
  id: number;
  created_at: string;
  author_role: Role;
  author_name: string | null;
  text: string;
}

export interface Notification {
  id: number;
  created_at: string;
  application_id: string | null;
  title: string;
  body: string;
  is_read: boolean;
}

// ------------------------------------------------------------------ банк

export interface BankRow {
  id: string;
  number: string;
  company_name: string;
  company_inn: string;
  company_kind: "LEGAL" | "IP";
  product_code: string;
  product_name: string;
  amount: number;
  term_months: number;
  status: string;
  status_label: string;
  stage_detail: string | null;
  grade: string | null;
  score: number | null;
  assignee: string | null;
  submitted_at: string | null;
  status_changed_at: string;
  review_due_at: string | null;
  sla_overdue: boolean;
  triggers: string[];
  awaiting_reply: boolean;
}

export interface Factor {
  code: string;
  name: string;
  value: string;
  points: number;
  comment: string;
  client_text: string;
}

export interface Reason {
  code: string;
  kind: string;
  text: string;
  client_text: string;
}

export interface BankDecision {
  id: string;
  created_at: string;
  kind: "AUTO" | "MANUAL";
  outcome: string;
  outcome_label: string;
  reasons: Reason[];
  details: Record<string, unknown> & {
    offers?: {
      product_code: string;
      product_name: string;
      amount: number;
      term_months: number;
      rate: number;
      monthly_payment: number;
      label: string;
      is_counter: boolean;
    }[];
    recommendation?: string | null;
    limit_calculation?: Record<string, number>;
    pricing?: { label: string; value: number }[];
  };
  decided_by: string | null;
  comment_internal: string | null;
  message_to_client: string | null;
  policy_version: string;
}

export interface PricingHint {
  product_code: string;
  product_name: string;
  min_amount: number;
  max_amount: number;
  min_term: number;
  max_term: number;
  default_rate: number;
  min_rate: number;
  max_rate: number;
}

export interface BankApplication extends BankRow {
  purpose: string;
  purpose_details: string | null;
  declared_revenue: number | null;
  has_collateral: boolean;
  collateral_description: string | null;
  owner_guarantee: boolean;
  contact_name: string | null;
  contact_phone: string | null;
  info_rounds: number;
  created_by: string;
  product: Product;
  company: Record<string, unknown> & {
    inn: string;
    ogrn: string;
    kind: string;
    short_name: string;
    full_name: string;
    okved_code: string;
    okved_name: string;
    registration_date: string;
    legal_address: string;
    client_since: string;
    segment: string;
    settlement_account: string;
    headcount: number | null;
    members: { full_name: string; position_label: string; can_sign: boolean }[];
  };
  sources: Record<string, { payload: Record<string, any>; fetched_at: string }>;
  scoring: {
    score: number;
    grade: string;
    grade_label: string;
    pd: number;
    model_version: string;
    factors: Factor[];
    metrics: Record<string, any>;
    created_at: string;
  } | null;
  checks: { code: string; severity: string; message: string }[];
  decisions: BankDecision[];
  offers: Offer[];
  info_requests: InfoRequest[];
  events: AppEvent[];
  integration_calls: {
    created_at: string;
    service: string;
    operation: string;
    status: string;
    http_status: number | null;
    duration_ms: number;
    attempt: number;
    error: string | null;
  }[];
  documents: DocumentInfo[];
  consents: { type: string; signed_at: string; signed_by: string }[];
  loan: Loan | null;
  permissions: { can_decide: boolean; can_rerun: boolean; can_retry_disbursement: boolean };
  pricing_hints: PricingHint[];
  key_rate: number;
}

export interface Dashboard {
  generated_at: string;
  total: number;
  by_status: { status: string; label: string; count: number }[];
  funnel: { stage: string; count: number }[];
  declined: number;
  auto_decision_share: number | null;
  avg_minutes_to_decision: number | null;
  avg_minutes_to_issue: number | null;
  manual_queue: number;
  manual_overdue: number;
  issued_amount: number;
  by_product: { product_code: string; count: number; amount: number }[];
  integrations_last_hour: { service: string; calls: number; errors: number; avg_ms: number }[];
  recent_events: (AppEvent & { application_number: string; application_id: string })[];
}

export interface SettingRow {
  key: string;
  value: unknown;
  description: string;
  updated_at: string | null;
  updated_by: string | null;
}
