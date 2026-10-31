"""The unchanged Glimmer executor limits, shared by configuration and the mission."""

MODEL_CALL_LIMIT = 32
CONTEXT_WINDOW = 32768
OUTPUT_TOKENS = 8192

EXECUTOR_CONSTRAINTS = (
    f"The Glimmer executor has at most {MODEL_CALL_LIMIT} admitted model calls per rollout "
    "(not tool calls). Admitted upstream failures count; the broker refuses further requests. "
    f"Its configured context window is {CONTEXT_WINDOW:,} tokens "
    f"(intended serve setting NOMOS_MAX_SEQ={CONTEXT_WINDOW}), including instructions, "
    "conversation history, tool results and generated text. Automatic compaction and retries "
    "are disabled. Keep the harness and tool output compact, bound verify/fix loops, and "
    "reserve calls and context for writing the final deliverable. These are executor limits, "
    "not your improver session's budget."
)
