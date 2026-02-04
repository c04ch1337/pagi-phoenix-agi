/**
 * Phoenix AGI — Frontend/Desktop contract types.
 * Kept in sync with docs/Boilerplate-Contract.md and backend mock_provider.py.
 * Contract version: 1.0.0
 */

// ---------------------------------------------------------------------------
// 1. Knowledge Base Names (8 KBs)
// ---------------------------------------------------------------------------

export type KnowledgeBaseName =
  | "kb_core"
  | "kb_skills"
  | "kb_1"
  | "kb_2"
  | "kb_3"
  | "kb_4"
  | "kb_5"
  | "kb_6";

export const KNOWLEDGE_BASE_NAMES: readonly KnowledgeBaseName[] = [
  "kb_core",
  "kb_skills",
  "kb_1",
  "kb_2",
  "kb_3",
  "kb_4",
  "kb_5",
  "kb_6",
] as const;

export function isKnowledgeBaseName(s: string): s is KnowledgeBaseName {
  return (KNOWLEDGE_BASE_NAMES as readonly string[]).includes(s);
}

// ---------------------------------------------------------------------------
// 2. Memory (Short-Term L1/L2)
// ---------------------------------------------------------------------------

export type MemoryLayer = 1 | 2 | 3 | 4 | 5 | 6 | 7;

export const MEMORY_LAYER_NAMES: Record<MemoryLayer, string> = {
  1: "sensory",
  2: "working",
  3: "episodic",
  4: "semantic",
  5: "procedural",
  6: "conceptual",
  7: "identity",
};

/** Request: read (omit value) or write (set value) */
export interface MemoryAccessRequest {
  layer: MemoryLayer;
  key: string;
  value?: string;
}

export interface MemoryAccessResponse {
  data: string;
  success: boolean;
}

// ---------------------------------------------------------------------------
// 3. Semantic Search (L4, 8 KBs)
// ---------------------------------------------------------------------------

export interface SearchRequest {
  query: string;
  kb_name: KnowledgeBaseName;
  limit: number;
  query_vector?: number[];
}

export interface SearchHit {
  document_id: string;
  score: number;
  content_snippet: string;
}

export interface SearchResponse {
  hits: SearchHit[];
}

// ---------------------------------------------------------------------------
// 4. Upsert Vectors (L4, 8 KBs)
// ---------------------------------------------------------------------------

export interface VectorPoint {
  id: string;
  vector: number[];
  payload: Record<string, string>;
}

export interface UpsertVectorsRequest {
  kb_name: KnowledgeBaseName;
  points: VectorPoint[];
}

export interface UpsertVectorsResponse {
  success: boolean;
  upserted_count: number;
}

// ---------------------------------------------------------------------------
// 5. Execute Action (Skills)
// ---------------------------------------------------------------------------

export interface ExecuteActionRequest {
  skill_name: string;
  params: Record<string, string>;
  depth: number;
  reasoning_id: string;
  mock_mode?: boolean;
  timeout_ms?: number;
}

export interface ExecuteActionResponse {
  observation: string;
  success: boolean;
  error: string;
}

// ---------------------------------------------------------------------------
// 6. RLM (Single Step)
// ---------------------------------------------------------------------------

export interface RLMRequest {
  query: string;
  context?: string;
  depth?: number;
}

export interface RLMResponse {
  summary: string;
  converged: boolean;
}

// ---------------------------------------------------------------------------
// 7. WebSocket Agent Events (Real-Time Reasoning)
// ---------------------------------------------------------------------------

export type AgentEventKind =
  | "session_started"
  | "thought"
  | "action_planned"
  | "action_started"
  | "action_completed"
  | "memory_read"
  | "memory_written"
  | "search_issued"
  | "search_result"
  | "converged"
  | "error"
  | "session_ended";

export interface AgentEventBase {
  event: AgentEventKind;
  timestamp: string; // ISO 8601
  reasoning_id?: string;
}

export interface SessionStartedEvent extends AgentEventBase {
  event: "session_started";
  session_id: string;
  query: string;
  depth: number;
}

export interface ThoughtEvent extends AgentEventBase {
  event: "thought";
  thought: string;
  depth: number;
}

export interface ActionPlannedEvent extends AgentEventBase {
  event: "action_planned";
  skill_name: string;
  params: Record<string, string>;
  depth: number;
}

export interface ActionStartedEvent extends AgentEventBase {
  event: "action_started";
  skill_name: string;
}

export interface ActionCompletedEvent extends AgentEventBase {
  event: "action_completed";
  skill_name: string;
  success: boolean;
  observation: string;
  error?: string;
}

export interface MemoryReadEvent extends AgentEventBase {
  event: "memory_read";
  layer: number;
  key: string;
  data?: string;
}

export interface MemoryWrittenEvent extends AgentEventBase {
  event: "memory_written";
  layer: number;
  key: string;
}

export interface SearchIssuedEvent extends AgentEventBase {
  event: "search_issued";
  kb_name: string;
  query: string;
  limit: number;
}

export interface SearchResultEvent extends AgentEventBase {
  event: "search_result";
  kb_name: string;
  hits_count: number;
  top_snippet?: string;
}

export interface ConvergedEvent extends AgentEventBase {
  event: "converged";
  summary: string;
  final_summary?: string;
}

export interface ErrorEvent extends AgentEventBase {
  event: "error";
  message: string;
  component?: string;
}

export interface SessionEndedEvent extends AgentEventBase {
  event: "session_ended";
  session_id: string;
  converged: boolean;
  summary?: string;
}

export type AgentEvent =
  | SessionStartedEvent
  | ThoughtEvent
  | ActionPlannedEvent
  | ActionStartedEvent
  | ActionCompletedEvent
  | MemoryReadEvent
  | MemoryWrittenEvent
  | SearchIssuedEvent
  | SearchResultEvent
  | ConvergedEvent
  | ErrorEvent
  | SessionEndedEvent;

const AGENT_EVENT_KINDS: readonly AgentEventKind[] = [
  "session_started", "thought", "action_planned", "action_started", "action_completed",
  "memory_read", "memory_written", "search_issued", "search_result", "converged",
  "error", "session_ended",
];

/** Type guard: check event kind */
export function isAgentEvent(obj: unknown): obj is AgentEvent {
  if (obj === null || typeof obj !== "object") return false;
  const o = obj as Record<string, unknown>;
  return (
    typeof o.event === "string" &&
    typeof o.timestamp === "string" &&
    (AGENT_EVENT_KINDS as readonly string[]).includes(o.event as string)
  );
}

/** Narrow by event kind */
export function hasEventKind<E extends AgentEvent["event"]>(
  ev: AgentEvent,
  kind: E
): ev is Extract<AgentEvent, { event: E }> {
  return ev.event === kind;
}
