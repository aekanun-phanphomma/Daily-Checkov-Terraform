Format: INSIDE THE CHECK (700-1500 characters).
- Line 1: a concrete question an engineer might ask about this resource
  (for example: which value of X actually passes?).
- Then explain what {{check_id}}'s source code reads: the attribute(s) it
  inspects and which values pass and which fail. Take this ONLY from
  check_source_code; if the code does not make it clear, write UNKNOWN.
  Say it in Terraform terms (arguments and values); never name Checkov's
  internal classes or functions, which mean nothing to the reader.
- Point out one passing or failing value that surprises people, if the
  code shows one (for example an omitted argument that fails because the
  default is unsafe).
- Then why it matters, condensed from why_it_matters.
- Then the fix in one sentence, naming the exact argument and value.
- Last line: a question about what the reader enforces alongside Checkov.
