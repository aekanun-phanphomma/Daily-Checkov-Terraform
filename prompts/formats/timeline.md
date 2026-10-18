Format: ATTACKER'S TIMELINE (500-1100 characters).
- Line 1: the exact misconfigured value from bad_tf, as something a team
  just applied (for example: You apply one rule with source = "*").
- Then 3 or 4 short steps, one per line, starting "Step 1:", "Step 2:" ...
  Each step is what an attacker can do next because of that value, through
  a named protocol or API. It is a hypothetical illustration: no durations,
  counts, statistics, real incidents or company names.
- Then one line: what {{check_id}} fails, in plain words.
- Then the fix in one sentence, naming the exact argument and value.
- Last line: one short question asking whether the reader's pipeline would
  have stopped it before apply.
