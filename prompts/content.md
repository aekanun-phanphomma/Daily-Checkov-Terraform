<rule_context>
check_id: {{check_id}}
check_name: {{check_name}}
resource_type: {{resource_type}}
cloud: {{cloud}}
check_source_url: {{source_url}}
check_source_code (this exact code decides PASS/FAIL):
{{source_code}}
</rule_context>
<versions>terraform {{tf_version}}, provider {{provider_source}} {{provider_version}}</versions>

Task. Return JSON with exactly these keys:

1. "bad_tf": the smallest Terraform that FAILS {{check_id}}. It must contain
   at least one `{{resource_type}}` resource. Supporting resources are
   allowed only if the resource cannot be declared without them.
2. "good_tf": the same resources, fixed so they PASS {{check_id}}, following
   provider best practice. Keep resource names identical to bad_tf. End
   every line you changed or added with a comment `# FIX: <why, <= 8 words>`.
3. "why_it_matters": exactly 3 strings, each <= 200 characters: the attack
   path or failure mode in technical terms (who can reach what, through
   which API or protocol, and what the fix blocks).
4. "post_en": a LinkedIn post, plain text with line breaks, in TODAY'S
   FORMAT below. The series rotates formats so consecutive posts do not
   read alike; follow this one, not a generic tip structure.

{{post_format}}

   Rules for every format:
     - The first line is concrete: a value, a port, an API or a consequence.
       Do not open with "Leaving", "Exposing", "Publicly", "Unrestricted",
       "Misconfigured", "Imagine", "Did you know" or "In today's".
     - Never write the formula "Checkov check {{check_id}} evaluates/
       inspects/flags ..."; say what it fails in plain words.
     - Do not paste code. Do not include hashtags, URLs or emoji.
