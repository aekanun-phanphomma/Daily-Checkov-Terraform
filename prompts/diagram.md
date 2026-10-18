The Terraform below has been verified: bad_tf FAILS {{check_id}} and good_tf
PASSES it ({{check_name}}). Produce the spec for a short animated card that
tells one story: an attacker reaches the target through a control, the fix
changes that control, and the same attack is stopped.

<bad_tf>
{{bad_tf}}
</bad_tf>
<good_tf>
{{good_tf}}
</good_tf>

Return JSON with exactly this shape:
{
  "title": "<= 60 chars",
  "attacker": {"type": "internet", "label": "<= 22 chars"},
  "gate": {"label": "<= 22 chars"},
  "target": {"label": "<= 22 chars"},
  "ally": {"label": "<= 22 chars"},
  "attack_label": "<= 28 chars",
  "risk_callout": "<= 90 chars",
  "control_callout": "<= 90 chars",
  "code_diff": ["- <line from bad_tf>", "+ <line from good_tf>"]
}

Roles. The card draws them on one line: attacker -> gate -> target.
- attacker: who reaches the target in bad_tf. "type" is "internet" (anyone,
  anonymous) or "user" (an authenticated principal, a workload, a role).
- gate: the control that {{check_id}} is about -- the thing the fix changes,
  named the way an engineer would ("NSG rule", "Security group",
  "Public access block", "Bucket policy", "Key policy").
- target: what the attacker is after ("Target VM", "S3 bucket",
  "Orders database"). Never the same thing as the gate.
- ally: OPTIONAL. Only when good_tf still lets a legitimate source through
  the gate (for example SSH restricted to an internal range: "Admin subnet").
  Omit the key when the fix simply closes the path.
- attack_label: the protocol, port or API the attacker uses, for example
  "TCP 22 (SSH)", "s3:GetObject", "HTTPS :443".
- All four labels must differ from each other.

Text.
- risk_callout: what the attacker gets in bad_tf, concretely.
- control_callout: what the fix stops, concretely.
- English only, no emoji.

code_diff: 2 to 6 lines. Lines starting with "- " are copied from bad_tf,
lines starting with "+ " from good_tf, without trailing comments. They are
checked against the code character for character (ignoring repeated
spaces). Put the line that matters most first: the card shows it on the
gate.
