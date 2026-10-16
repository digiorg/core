# Issue 372 — initial SonarQube enrollment

Authority: https://github.com/digiorg/core/issues/372 and approval comments
6224265210, 6224482184, 6224551835. Large, standard-lane local implementation;
publication, merge, credentials and shared-runtime actions are not authorized.

R1: Every gitea.enabled application, including existing claims and cicd=false,
waits for exact repository identity before one private project is enrolled at
`digiorg:gitea:DigiOrg:<appName>`. No scanner, ALM binding or continuous repair.
R2: Require trusted HTTPS, authenticated API UP and approved platform defaults.
Only verified absence permits one form-encoded create with mainBranch=main;
exact private TRK readback is mandatory. Never retry a provisioning POST after
an ambiguous result. Read back, then require explicit adoption if creation
ownership cannot be established. This favors safe failure over takeover.
R3: An existing project always requires an operator-provided approval bound to
its exact key AND current XR UID. Completed Jobs are historical receipts; no
TTL, generation/token-derived names or automatic retries after terminal failure.
Recreated claims and replaced Jobs do not silently adopt retained projects.
R4: Dedicated digiorg-provisioner, never Administer System. Chris accepts global
Create Projects and inherited Execute Analysis plus sonar-users project Browse,
See Source Code, Administer Issues and Administer Security Hotspots for this
prototype. Preserve defaults; do not infer groups from team or change templates.
Reject unexpected account/template privileges before preparing the credential.
Use default built-in Sonar way gate/profiles; a changed default is a stop gate,
not authority to modify policy. Image defaults are not live policy evidence.
R5: Bootstrap prepares account/token using existing Secret persistence; operator
owns rotation. Runner mounts crossplane-sonarqube-credentials/token and
crossplane-system/digiorg-local-ca/ca.crt read-only. Never put secret bytes in
KCL, command arguments, output, XR status or CI. Provisioning is not scanning.
R6: Keep successful Job desired; derive readiness from Complete without Failed.
Emit sanitized SonarQubeEnrollmentReady condition. No SonarQube DELETE ever;
application disable/delete removes only composed Kubernetes resources. Existing
repository/scaffold/Harbor gates remain unchanged. Duplicate app identities must
fail closed before enrollment.

Adoption: Chris approves exact key and XR UID in an issue comment. An operator
then supplies optional Secret crossplane-sonarqube-adoptions, key approvals.json,
containing [{"key":"...","xrUID":"...","approvedBy":"Chris","approvalRef":"https://github.com/digiorg/core/issues/372#issuecomment-..."}].
No Composition input grants adoption. Treat write access to this Secret as
approval authority. Do not add automatic approval/cleanup/rotation.

Security limitations: Create Projects is instance-wide; the existing Kubernetes
provider remains broadly privileged. Default sonar-users membership permits the
explicit rights above, not pure create/browse least privilege. Independent review
and separately authorized live effective-policy verification remain required.
