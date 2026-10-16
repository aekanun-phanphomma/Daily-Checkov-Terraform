The Terraform below has been verified: bad_tf FAILS {{check_id}} and good_tf
PASSES it ({{check_name}}). Produce a diagram spec for a before/after image.

<bad_tf>
{{bad_tf}}
</bad_tf>
<good_tf>
{{good_tf}}
</good_tf>

Return JSON with exactly this shape:
{
  "title": "<= 60 chars",
  "before": {
    "nodes": [{"id": "a", "type": "internet", "label": "<= 24 chars"}],
    "edges": [{"from": "a", "to": "b", "label": "<= 28 chars"}],
    "risk_callout": "<= 90 chars"
  },
  "after": {
    "nodes": [...],
    "edges": [{"from": "a", "to": "b", "label": "...", "blocked": true}],
    "control_callout": "<= 90 chars"
  },
  "code_diff": ["- <line from bad_tf>", "+ <line from good_tf>"]
}

Rules:
- Node "type" is one of: internet, user, service, resource, control.
- 2 to 4 nodes per panel. Node ids are unique within a panel.
- Every edge has "from" and "to" naming node ids in the same panel, and a
  "label" naming the protocol, port or API (for example "TCP 22 (SSH)" or
  "s3:GetObject").
- 1 to 3 edges per panel. In "after", an edge the fix stops must set
  "blocked": true, and at least one edge must be blocked.
- code_diff: 2 to 6 lines. Lines starting with "- " are copied from bad_tf,
  lines starting with "+ " are copied from good_tf, without trailing
  comments. They are checked against the code character for character
  (ignoring repeated spaces).
- English only, no emoji.
