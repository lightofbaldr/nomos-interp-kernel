/** Approved bounds, not measured throughput or a claim of serving context fit. */
export const CHAT_OUTPUT_TOKENS=6144;
export const AGENT_BUDGET=Object.freeze({outputTokens:8192,modelRequests:32,toolCalls:64,wallMs:20*60*1000});
export type AgentMode='chat'|'agent';
export type AgentBudgetSettings={version:number;mode:AgentMode};
export type AgentBudgetLimits={outputTokens:number;modelRequests:number|null;toolCalls:number|null;wallMs:number|null};
export type AgentBudgetReason='complete'|'aborted'|'failed'|'model_requests'|'tool_calls'|'wall_time'|'recovered'|'closed';
export type AgentBudgetAdmission={runId:string;mode:AgentMode;outputTokens:number;modelRequest:number;toolCalls:number;
  startedAt:number;deadlineAt:number|null;limits:AgentBudgetLimits;task:number;attempt:number;cutoff:number;key:string};
export type AgentBudgetRun={id:string;mode:AgentMode;settingsVersion:number;limits:AgentBudgetLimits;startedAt:number;
  deadlineAt:number|null;endedAt:number|null;status:'running'|'finished';stopReason:AgentBudgetReason|null;
  modelRequests:number;toolCalls:number;lastRequest:AgentBudgetAdmission|null};
export type AgentBudgetStatus={settings:AgentBudgetSettings;run:AgentBudgetRun|null;owned:boolean};

export function agentMode(value:unknown):AgentMode {
  if(value!=='chat'&&value!=='agent')throw Error('Agent mode must be chat or agent.');
  return value;
}
export function budgetLimits(mode:AgentMode):AgentBudgetLimits {
  agentMode(mode);
  return mode==='agent'?{...AGENT_BUDGET}:{outputTokens:CHAT_OUTPUT_TOKENS,modelRequests:null,toolCalls:null,wallMs:null};
}
