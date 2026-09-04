# Issues #350/#352 Runtime v3 Transition Contract

## Status, authority, and release gate

This candidate does not authorize publication and does not authorize rollout. The source graph remains unauthorized until the runtime commit exists, the annotated tag object and peeled commit are independently bound as described below, exact-SHA CI passes, independent review covers that exact snapshot, and separate Chris approval is recorded for publication and any destructive clean bootstrap.

`scripts/issue350_352_runtime_v3_transition.py` is authoritative for the control-plane transition. This runbook explains its inputs and acceptance rules; operators must not replace it with hand-entered mutation commands. Its only permitted mutations are:

1. CAS scale `StatefulSet/argocd-application-controller` from the recorded replica count to zero and back to that exact count;
2. CAS replace only `Application/root-app.spec.source.targetRevision`; and
3. CAS replace only `Application/argocd.spec.source.targetRevision`.

It never patches, applies, deletes, restarts, or otherwise mutates OpenSearch, Fluentd, Pods, PVCs, indices, templates, mappings, shards, or data. A workload-stage failure has no automatic rollback: preserve state and evidence and return control to Chris.

The retained-transition path and clean-bootstrap acceptance mode are **mutually exclusive**. `--mode retained-transition` is only for the existing old/old retained cluster and performs the three CAS mutations above. It must never be run on a fresh cluster. `--mode clean-bootstrap-accept` is read-only acceptance for an externally recreated empty `digiorg-core-dev`; it performs only bounded reads and never executes a transition patch. The tracked candidate does not provide or authorize that external recreation.

## Immutable revision and graph contract

- old tag: `issue301-runtime-v16-20260817T130820Z`
- old commit: `8e6b8908f99ebf76db47c15613eff523644c23f6`
- v3 tag: `issue350-352-runtime-v3-20260904T195619Z`
- product base: `95b89acfdcca32e348745206c2cce4867f51a6b8`
- v3 runtime commit: the 40-character SHA produced only after exact-snapshot PASS
- core-catalog: `d531180b322dc0128477ecb9bb0fc9071b41d631`

The final 32 Core repository source references are exactly three on v3 and 29 on the old tag: Root `apps`, Argo CD `platform/base/argocd`, and the OpenSearch `ref: values` source are v3; OpenSearch `path: platform/base/opensearch`, Fluentd, and every other Core source remain old. The exact 32-Application inventory is `app-config,argocd,backstage,cert-manager,cnpg,cnpg-cluster,core-catalog,crossplane,crossplane-harbor-bootstrap,crossplane-provider-configs,crossplane-providers,crossplane-xrds,external-secrets,fluentd,gitea,gitea-actions-runner,grafana,harbor,jaeger,keycloak,kyverno,kyverno-policies,landingpage,monitoring-extras,namespaces,nats,nats-jetstream-controller,opencost,opensearch,postgresql,root-app,sonarqube`; missing or extra Applications fail before mutation. OpenSearch chart is exactly `3.7.0`. External app-config remains `https://digiorg.local/gitea/DigiOrg/app-config.git`, path `claims`, target `main`; core-catalog remains `https://github.com/digiorg/core-catalog.git`, path `compositions/local`, at its pinned SHA.

Keeping OpenSearch supplementary old preserves the retained ISM and create-only template owner and avoids promoting the Issue #348 Fluentd schema/writer changes.

## Approved invocation

Prerequisites are Python 3, `git`, and `kubectl`. Create a fresh non-default kubeconfig, make it mode `0600` (or stricter), and independently obtain the expected API URL and immutable `kube-system` namespace UID. Both the kubeconfig and evidence path must be outside the repository root; the evidence path must not exist. The executable creates it atomically with mode `0600`.

Before executing, a reviewer must verify exact-SHA CI for `$RUNTIME_COMMIT` and the remote annotated tag in the hosting UI/API. This one-shot executable requires the remote URL, runtime tag, old tag, and old commit to equal the literals below and rejects option-like or substituted values before any Kubernetes command. It independently runs bounded local `git rev-parse`, `git status --porcelain=v1 --untracked-files=all`, and `git ls-tree` checks: HEAD must equal the runtime commit, the checkout must be clean, and this script plus this runbook must be tracked at HEAD. It then runs bounded `git ls-remote --tags`: the tag-object ref must exist and has its own exact recorded object SHA, while only the peeled `^{}` ref equals the runtime commit. For an annotated tag, the tag-object SHA is distinct from the runtime commit SHA. The executable does not infer CI approval.

```bash
KUBECONFIG_350=/secure/issue350-kubeconfig
CONTEXT_350=issue350-retained
EXPECTED_SERVER=https://api.retained.example:6443
EXPECTED_KUBE_SYSTEM_UID=00000000-0000-0000-0000-000000000000
REMOTE_URL=https://github.com/digiorg/core.git
RUNTIME_TAG=issue350-352-runtime-v3-20260904T195619Z
RUNTIME_COMMIT=<exact-reviewed-40-character-runtime-sha>
OLD_TAG=issue301-runtime-v16-20260817T130820Z
OLD_COMMIT=8e6b8908f99ebf76db47c15613eff523644c23f6
EVIDENCE=/secure/issue350-transition.jsonl
CHECKOUT_350=/secure/issue350-runtime-v3
CURRENT_PRIMARY_INDEX=<exact-baseline-primary-index>
REPRESENTATIVE_LOG_INDEX=<exact-baseline-log-index>
REPRESENTATIVE_JAEGER_INDEX=<exact-baseline-jaeger-index>

git clone --no-checkout "$REMOTE_URL" "$CHECKOUT_350"
cd "$CHECKOUT_350"
git fetch --force origin "refs/tags/$RUNTIME_TAG:refs/tags/$RUNTIME_TAG"
git checkout --detach "refs/tags/$RUNTIME_TAG^{}"
test "$(git rev-parse HEAD)" = "$RUNTIME_COMMIT"
chmod 0600 "$KUBECONFIG_350"
python3 scripts/issue350_352_runtime_v3_transition.py \
  --mode retained-transition \
  --kubeconfig "$KUBECONFIG_350" \
  --context "$CONTEXT_350" \
  --expected-server "$EXPECTED_SERVER" \
  --expected-kube-system-uid "$EXPECTED_KUBE_SYSTEM_UID" \
  --remote-url "$REMOTE_URL" \
  --runtime-tag "$RUNTIME_TAG" \
  --runtime-commit "$RUNTIME_COMMIT" \
  --old-tag "$OLD_TAG" \
  --old-commit "$OLD_COMMIT" \
  --current-primary-index "$CURRENT_PRIMARY_INDEX" \
  --representative-log-index "$REPRESENTATIVE_LOG_INDEX" \
  --representative-jaeger-index "$REPRESENTATIVE_JAEGER_INDEX" \
  --evidence "$EVIDENCE"
```

There is no fast-mode production flag. Every kubectl call includes the explicit kubeconfig, context, request timeout, and namespace where applicable; subprocesses have a separate outer timeout and never use a shell. The five-minute barrier is aggregate, each controller wait is at most two minutes, rollback has a bounded five-minute safety budget, and convergence is at most 20 minutes.

## Executable protocol

### Secret-safe evidence

The JSONL file contains RFC3339 UTC time, monotonic elapsed/deadline context, allowlisted safe scalar identities, canonical SHA-256 hashes, exact revision lists/source counts, replica counts, Pod UID hashes, results, and redacted diagnostics. Every `error` field is redacted inside `Evidence.write`, regardless of caller. Application complete specs remain memory-only; in short, complete specs remain memory-only, along with the controller configuration and raw operation objects; evidence contains only hashes and safe revisions. It contains no operation messages, raw stderr, Secret values, or document payloads.

### Preflight (no mutations)

The executable fails closed unless all of these are exact:

- the annotated remote v3 tag peels to the supplied runtime commit;
- minified kubeconfig server and `Namespace/kube-system.metadata.uid` equal supplied values;
- every Argo CD Application has top-level `operation` absent, no legacy/malformed `spec.operation`, no `Running`/`Terminating` operation, and exact `Synced`/`Healthy` status;
- Root and Argo CD have the expected Core repo, paths, and old targets;
- OpenSearch sources are chart `3.7.0`, old values, old supplementary; Fluentd is old; app-config is `main`; core-catalog is pinned;
- all complete in-memory Application specs, canonical hashes, resource versions/UIDs, and completed-operation identities for all 32 Applications, plus Argo comparison identity and app-config resolved revision, are captured (only hashes/scalars reach evidence); every present prior operation has an exact terminal phase, valid RFC3339 `startedAt`/`finishedAt`, and nonempty requested and syncResult revision lists, while Root/Argo/OpenSearch identities are required; app-config has exactly one nonempty lowercase 40-hex resolved commit (arbitrary status text is rejected and never emitted);
- exactly one StatefulSet matches `app.kubernetes.io/name=argocd-application-controller`, and its name/namespace are exactly `argocd-application-controller`/`argocd`; it has replicas greater than zero, observed generation current, full readiness, and equal current/update revisions;
- its canonical non-replica StatefulSet spec (only `/spec/replicas` excluded) and preflight currentRevision/updateRevision are captured in memory;
- exactly the expected number of Ready controller Pods are owned by its UID, and their exact UIDs are captured; and
- the HPA response is an exact `autoscaling/v2` `HorizontalPodAutoscalerList` with metadata and a bounded items array; every item is a structurally valid `autoscaling/v2` `HorizontalPodAutoscaler` with namespace, name, spec, and complete scale target identity; and no HPA in any namespace targets that StatefulSet.

The retained collector is integrated into the same executable. After control-plane preflight succeeds but before the first mutation, it captures the workload/PVC/index baseline described below in memory and writes only allowlisted scalar/identity evidence.

### Barrier and owner closure

The controller stop patch is RFC 6902 with these exact operations and values taken from preflight:

```json
[
  {"op":"test","path":"/metadata/uid","value":"<controller-uid>"},
  {"op":"test","path":"/metadata/resourceVersion","value":"<controller-rv>"},
  {"op":"test","path":"/spec/replicas","value":"<prior-positive-integer>"},
  {"op":"replace","path":"/spec/replicas","value":0}
]
```

The barrier requires current observed generation, zero desired/observed/ready replicas, and zero Pods owned by the controller UID. It then rechecks strict HPA-list shape and absence (including case-insensitive `apps` group/StatefulSet kind matching only after shape validation), the same controller UID and exact canonical non-replica StatefulSet spec, no `spec.operation`/active operation, all completed-operation identities unchanged across all 32 Applications, all Application UIDs unchanged, and every complete Application spec equal to preflight. No Application—not any of the 31 child Applications, Fluentd, app-config, core-catalog, nor any OpenSearch non-target field—may change at the barrier.

Root and Argo are patched in that order. Each exact patch tests current `metadata.uid`, `metadata.resourceVersion`, `repoURL`, `path`, and old `targetRevision`, then replaces only `targetRevision`:

```json
[
  {"op":"test","path":"/metadata/uid","value":"<current-uid>"},
  {"op":"test","path":"/metadata/resourceVersion","value":"<current-rv>"},
  {"op":"test","path":"/spec/source/repoURL","value":"https://github.com/digiorg/core.git"},
  {"op":"test","path":"/spec/source/path","value":"<apps-or-platform/base/argocd>"},
  {"op":"test","path":"/spec/source/targetRevision","value":"issue301-runtime-v16-20260817T130820Z"},
  {"op":"replace","path":"/spec/source/targetRevision","value":"issue350-352-runtime-v3-20260904T195619Z"}
]
```

Both complete specs and UIDs are read back and compared with preflight, permitting exactly that target change. Immediately before controller restore, the executable performs its last stopped-state gate: the strict HPA list remains valid and absent; the controller has the same UID and non-replica spec, desired/observed/ready replicas zero, and no owned Pod; the exact reviewed 32-name Application inventory and every UID remain unchanged; `spec.operation` and active operations remain absent; every complete spec equals baseline except Root/Argo targets at v3; all 32 operation identities remain baseline-exact; and the validated app-config resolved commit remains unchanged. Any replacement or concurrent mutation enters rollback while reconciliation is still stopped.

### Pre-reconciliation rollback and interruption

On any error or SIGINT/SIGTERM/SIGHUP after the first mutation while the controller is still stopped, the executable attempts one bounded rollback. Before patching, rollback rejects any Application UID replacement. It reads each exact owner target: v3 is CAS-patched v3→old, old is left alone, and any third value is rejected. It requires complete old spec readback and restores the controller. Restoration is still attempted if owner rollback validation fails.

Before restore, the executable again requires the same StatefulSet UID and exact non-replica StatefulSet spec. The restore patch tests that UID, its then-current resourceVersion, and current replicas zero before restoring the exact positive preflight count. Restoration requires current observed generation, exact full readiness, both currentRevision and updateRevision equal the preflight currentRevision, unchanged non-replica spec, exact Ready Pods owned by the same StatefulSet UID, all Pod UIDs new relative to preflight, and the same new UID set in at least two samples.

The instant the restore patch is accepted, reconciliation may have begun: no owner rollback is allowed thereafter. Any restoration or convergence failure preserves the current control-plane/workload state and exits nonzero.

### Twenty-minute control-plane closure

Acceptance requires all Application UIDs unchanged and every complete Application spec equal to preflight except exactly Root target→v3, Argo target→v3, and OpenSearch values-ref target→v3. All other fields across the root and all 31 child Applications remain exact. Non-target completed-operation identities remain baseline-exact, while the existing Root/OpenSearch fresh-operation rules remain mandatory. All Applications also have no requested/active operation and are `Synced`/`Healthy`, plus:

- Root target is v3, status sync revision is the runtime commit, and its new `Succeeded` operation has valid RFC3339 `startedAt` chronologically later than preflight; both requested and syncResult revision lists equal `[runtime commit]` exactly;
- Argo CD is deliberately zero-diff: no sync operation is required, but its post-restoration `reconciledAt` is valid RFC3339 and chronologically later than the captured pre-restore value; target/status revision are v3/runtime commit;
- OpenSearch has exact source identities/order (chart repo+chart `opensearch`+`3.7.0`; Core repo `ref: values` with no path at v3; Core repo `path: platform/base/opensearch` with no ref at old), exact status revisions, and a new `Succeeded` operation whose requested and syncResult revision lists both equal `[3.7.0, runtime commit, old commit]` and whose RFC3339 `startedAt` is chronologically later;
- Fluentd remains the exact Core repo/path/old tuple and structured exact-NotFound is returned only for `Job/logging/fluentd-log-schema`; generic command, credential, context, or transport diagnostics containing `NotFound` are failures;
- all 32 Core refs are exactly 3 v3 / 29 old / 0 other;
- app-config remains its exact repo/path/`main` tuple with its original resolved status revision; and
- core-catalog remains its exact repo/path tuple at `d531180b322dc0128477ecb9bb0fc9071b41d631`.

A timestamp without exact requested and syncResult revision lists, a stale/non-later operation, stale Healthy status, or operation message is never accepted or written as evidence.

## Deterministic clean bootstrap publication boundary

This tracked prepublication candidate intentionally contains no runnable clean-bootstrap, cluster-removal, or deployment sequence. A runtime commit cannot safely contain its own immutable commit and tree identities, so neither this runbook nor the candidate commit authorizes destruction or bootstrap.

Only after independent review and publication as an annotated tag may a separate post-publication launcher be generated outside this repository. That launcher must embed, as literals, the literal exact reviewed commit SHA, the exact tree SHA, the exact tag-object SHA, and the exact peeled SHA. The tag-object ref must exist at its recorded tag-object SHA, while the peeled ref must equal the recorded peeled SHA and runtime commit. The launcher must bind and verify all four identities before any destructive action, must be independently reviewed, and must be target-path-probed before separate user deployment approval. Publication approval, launcher review, path probing, and deployment approval are distinct fail-closed gates; none is granted by this candidate.

The external launcher may invoke this repository's read-only clean acceptance only after its separately approved bootstrap has completed. This section deliberately provides no executable commands or operator-ready substitutions.

Clean acceptance does not require old/old operation identities, a preexisting StatefulSet/PVC/index baseline, or operator-selected index names. Its 15-minute readiness bound followed by 31 samples at `t=0,60,...,1800` validates the literal 32-Application source tuple graph and stable Application UID/complete-spec hash closure; exact Healthy/Synced status; stable controller UID/spec/currentRevision/updateRevision/Ready-Pod closure; and structured exact-NotFound for `Job/logging/fluentd-log-schema` at every observation.

The same window validates a Ready single-node OpenSearch StatefulSet and Pod; immutable image `opensearchproject/opensearch:3.7.0@sha256:44ba7ea58a319adf61c33ab16873f9ef5dbb30b291a832d375172f0b2d24e3c9`; exact 250m/1Gi requests, 1000m/2Gi limits, 512Mi heap, disabled-security setting, image pull policy, complete StatefulSet/Pod spec hashes, and stable Pod UID/restarts/last termination/cgroup `memory.events.max`. It requires cgroup `memory.max=2147483648` and at least 256Mi headroom. The exact bound PVC must have ReadWriteOnce, standard storage class, Filesystem mode, 8Gi request, no selector/dataSource/dataSourceRef, Bound capacity, and a stable hash of every spec field plus UID, volume and capacity.

No traffic is injected, idle traffic growth is not required, and no preexisting log/trace index is selected or required. Every sample uses bounded read-only Kubernetes GET/proxy calls and read-only OpenSearch GETs. It requires yellow/green health, no active recovery, successful cluster-wide search/count telemetry, valid Fluentd queue telemetry from every exact Ready Fluentd Pod, a bounded Jaeger query-service `/api/services` GET with an array result, and the exact ISM policy/explain contract for every currently open index. Missing natural log/trace service, policy, explain, cgroup, queue, identity, spec, count, or health telemetry fails closed. Evidence remains scalar/hash-only and mode `0600`.

## Deterministic retained acceptance (read-only)

The collector is integrated into `scripts/issue350_352_runtime_v3_transition.py`: it takes the durable baseline before any control-plane mutation, begins the bounded recovery gate only after `control-plane-closed`, and then performs the exact 31-sample window. All collector behavior is reviewed here and executable now. This stage is read-only and has no automatic rollback. Commands persist no HTTP bodies, metrics payloads, log content, hits, `_source`, mappings, documents, credentials, or Secret values; only the allowlisted evidence scalars/identity hashes are written.

```bash
OS_NS=platform-db
OS_POD=opensearch-cluster-master-0
OS_CONTAINER=opensearch
OS_PVC=opensearch-cluster-master-opensearch-cluster-master-0
FLUENTD_NS=logging
CURRENT_PRIMARY_INDEX=<preflight-current-primary-index>
REPRESENTATIVE_LOG_INDEX=<preflight-representative-log-index>
REPRESENTATIVE_JAEGER_INDEX=<preflight-representative-jaeger-index>
K=(kubectl --kubeconfig "$KUBECONFIG_350" --context "$CONTEXT_350" --request-timeout=20s)
```

### Durable pre-transition baseline and exact ISM expiry classification

Before the transition preserve only durable StatefulSet/PVC/index identities plus evidence about the old Pod:

1. Record the OpenSearch StatefulSet UID and canonical old workload spec; exact PVC name, UID, bound volume, capacity, and canonical PVC spec hash. The complete PVC closure includes `accessModes`, `storageClassName`, `volumeMode`, `selector`, requested storage, `dataSource`, `dataSourceRef`, and every other PVC spec field; any field drift fails every recovery/sample comparison. Record each open index name+UUID+`creation_date`. Record the old OpenSearch Pod UID, restart count, lastTermination, and `memory.events.max` only as pre-rollout evidence—not as an identity that must survive.
2. Verify policy `digiorg-logs-retention-7d` read-only at `/_plugins/_ism/policies/digiorg-logs-retention-7d`. Parse JSON in memory and require `default_state: "hot"`; exactly one template object with no additional keys, `index_patterns: ["digiorg-logs-*"]`, and `priority: 100`; and exactly two states in the reviewed order and shape: `hot` with no actions and one direct hot→delete transition whose only condition is `min_index_age: "7d"`, then `delete` with exactly one `{ "delete": {} }` action and no transitions. Duplicate names, additional states/templates/actions/transitions, and additional fields inside the reviewed template/state/action/transition shapes fail closed. This rejects hot→warm→delete even if a later warm transition uses seven days.
3. Query the current open-index inventory first, then query `/_plugins/_ism/explain/*?show_policy=true` and parse in memory. OpenSearch 3.7's actual Explain response exposes each managed index under its index-name key with both policy-setting aliases, `index`, `index_uuid`, `policy_id`, `policy_seq_no`, `policy_primary_term`, embedded `policy`, `enabled`, `state: {name,start_time}`, and `action: {name,start_time,index,failed,consumed_retries,last_retry_time}`; it does not expose synthetic `current_state` or `current_action` scalars. The GET-policy wrapper must have exactly `_id`, `_version`, `_seq_no`, `_primary_term`, and `policy`; only the proven API-generated wrapper revision fields plus policy `last_updated_time` and `schema_version` are normalized for comparison to the reviewed source contract, and their exact integer types/ranges are still validated. The reviewed policy content is exact, including policy ID, description, null error notification, default state, states, and template. For each managed index the embedded `policy` must exactly equal the current GET-policy body, including its API-generated metadata, while policy sequence/primary-term fields must equal the GET wrapper; a stale or altered embedded policy fails. Require every currently open `digiorg-logs-*` index, including indices created after baseline, to bind that exact policy, repeat its exact name and UUID, be enabled, and expose the exact state/action field shapes. The only valid closure is `hot` + `transition` action index `-1`, or `delete` + `delete` action index `0`, and `action.failed` must be exactly `false`; missing, additional, malformed, failed, or mismatched state/action fields fail closed. For unmanaged non-log indices, retain a false binding without requiring managed-index state/action metadata. For each baseline index, retain only the index name, UUID, `creation_date`, attached policy ID, and validated current state/action result. An absent baseline index is expiry-eligible at sample time `T` only if all are proven: its baseline name matches `digiorg-logs-*`, its baseline explain binding passed this exact closure, the current exact seven-day policy check passed, and `creation_date + 7 days <= T`. A baseline name still present must retain its UUID even when old enough to expire. A missing index that was not provably eligible by that sample is data loss. Jaeger and other nonmatching indices never receive this exception.

Approved baseline command shapes (OpenSearch has `DISABLE_SECURITY_PLUGIN=true`, so the retained endpoint is HTTP):

```bash
"${K[@]}" -n "$OS_NS" get statefulset.apps opensearch-cluster-master -o json
"${K[@]}" -n "$OS_NS" get pod "$OS_POD" -o jsonpath='{.metadata.uid}{"\t"}{range .status.containerStatuses[?(@.name=="opensearch")]}{.restartCount}{"\t"}{.lastState.terminated.reason}{"\t"}{.lastState.terminated.finishedAt}{end}{"\n"}'
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- sh -c 'grep "^max " /sys/fs/cgroup/memory.events'
"${K[@]}" -n "$OS_NS" get persistentvolumeclaim "$OS_PVC" -o json
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 'http://127.0.0.1:9200/_cat/indices?format=json&h=index,uuid,status,creation.date&expand_wildcards=open'
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 'http://127.0.0.1:9200/_plugins/_ism/policies/digiorg-logs-retention-7d'
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 'http://127.0.0.1:9200/_plugins/_ism/explain/*?show_policy=true'
```

Parse each response immediately into the allowlisted identities/scalars above and discard the raw response. The policy and explain are re-queried and revalidated at every sample before any disappeared index can be excused. Any malformed, incomplete, ambiguous, or unparseable policy/explain result makes every affected index non-expiry-eligible.

### 15-minute recovery-readiness gate

After control-plane closure, expected OpenSearch rollout creates a new Pod. Do not fail merely because recovery is active immediately after Argo closure. Instead run a **15-minute recovery-readiness gate**, polling every 30 seconds with individually bounded calls. Before the deadline require simultaneously:

- cluster status exactly yellow or green;
- `/_cat/recovery?active_only=true&format=json&h=index,stage` parses as an empty JSON array (`[]`), meaning **active recovery count = 0**;
- a size-zero search of `CURRENT_PRIMARY_INDEX` succeeds and yields a numeric `hits.total.value`; and
- the pre-transition durable StatefulSet UID, exact PVC identities/bindings/capacities, and every index UUID not expiry-eligible at the observation time remain exact;
- the candidate has exact requests `memory: 1Gi`, `cpu: 250m`, limits `memory: 2Gi`, `cpu: 1000m`, and heap `-Xmx512M -Xms512M`; every non-resource StatefulSet and Pod-template field—including image, security context, volumes, and storage-affecting fields—equals the canonical old workload spec; and
- cgroup `memory.max` exactly `2147483648`.

```bash
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 'http://127.0.0.1:9200/_cluster/health?filter_path=status'
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 'http://127.0.0.1:9200/_cat/recovery?active_only=true&format=json&h=index,stage'
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 "http://127.0.0.1:9200/$CURRENT_PRIMARY_INDEX/_search?size=0&filter_path=hits.total.value"
```

At gate success, discover the one exact Ready OpenSearch Pod owned by the retained StatefulSet. Its UID must be a **new OpenSearch Pod UID** relative to the old-Pod evidence. Now establish the observation baseline from this new Pod/cgroup: new Pod UID, OpenSearch container restart count, lastTermination reason/time, `memory.current`, `memory.max`, and `memory.events.max`, plus the canonical post-rollout candidate template hash. This instant is **post-rollout t=0**. Never require preservation of the old Pod UID or old template hash, and never compare the new cgroup counter to the old cgroup.

### Thirty-minute observation window

From post-rollout t=0, take **31 samples** for **exactly 30 minutes**, **every 60 seconds**, at `t=0,60,...,1800`. Preserve the new Pod UID for all 31 samples. Every sample rechecks durable identities and ISM eligibility, cluster yellow/green, empty active-recovery array, readable current primary, unchanged new-Pod restart/lastTermination values, `memory.events.max delta = 0` from post-rollout t=0, and `memory.max - memory.current >= 268435456`.

Use these bounded read-only shapes:

```bash
"${K[@]}" -n "$OS_NS" get statefulset.apps opensearch-cluster-master -o json
"${K[@]}" -n "$OS_NS" get pod "$OS_POD" -o jsonpath='{.metadata.uid}{"\t"}{range .status.containerStatuses[?(@.name=="opensearch")]}{.restartCount}{"\t"}{.lastState.terminated.reason}{"\t"}{.lastState.terminated.finishedAt}{end}{"\n"}'
"${K[@]}" -n "$OS_NS" get persistentvolumeclaim "$OS_PVC" -o json
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 'http://127.0.0.1:9200/_cluster/health?filter_path=status'
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 'http://127.0.0.1:9200/_cat/recovery?active_only=true&format=json&h=index,stage'
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 "http://127.0.0.1:9200/$CURRENT_PRIMARY_INDEX/_search?size=0&filter_path=hits.total.value"
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 'http://127.0.0.1:9200/_cat/indices?format=json&h=index,uuid,status,creation.date&expand_wildcards=open'
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 "http://127.0.0.1:9200/$REPRESENTATIVE_LOG_INDEX/_count?filter_path=count"
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- curl --fail --silent --show-error --max-time 15 "http://127.0.0.1:9200/$REPRESENTATIVE_JAEGER_INDEX/_count?filter_path=count"
"${K[@]}" -n "$OS_NS" exec "$OS_POD" -c "$OS_CONTAINER" -- sh -c 'printf "current="; cat /sys/fs/cgroup/memory.current; printf "max="; cat /sys/fs/cgroup/memory.max; grep "^max " /sys/fs/cgroup/memory.events'
```

Fluentd exposes Prometheus on port 24231 `/metrics`; it does not expose monitor_agent at 24220. At every sample read `DaemonSet/logging/fluentd` to bind its UID, then enumerate `app=fluentd` Pods. Parse the exact list in memory and accept every and only Pod whose controller owner reference matches that DaemonSet UID, phase is `Running`, and Ready condition is exactly `True`; require a nonempty set and re-read the DaemonSet UID and Pod set after metrics collection to reject races. For every accepted Pod name `${pod}`, query through Kubernetes pod proxy (not `exec`):

```bash
"${K[@]}" -n logging get daemonset.apps fluentd -o json
"${K[@]}" -n logging get pods -l app=fluentd -o json
"${K[@]}" get --raw "/api/v1/namespaces/logging/pods/${pod}:24231/proxy/metrics"
"${K[@]}" -n logging get daemonset.apps fluentd -o json
"${K[@]}" -n logging get pods -l app=fluentd -o json
```

Parse the response only in memory. Require at least one finite numeric sample matching `fluentd_output_status_buffer_queue_length{plugin_id="out_opensearch"...}` for each exact Ready Pod; retain only Pod-name hashes and numeric queue lengths, then discard the metrics payload. Every queue on every Ready Pod must reach `0` at `t=1800`; absence, duplicate ambiguity, malformed metrics, a failed proxy, or a Pod-set race fails closed.

Observe—not inject—existing representative traffic. **No traffic is injected**. The representative log index count and representative Jaeger index count must each increase between post-rollout t=0 and `t=1800`, while every Fluentd `out_opensearch` buffer queue reaches `0`. No payload/log content is persisted.

## Stop conditions

Stop, preserve secret-safe scalar/identity evidence, perform no workload/data mutation, and return control to Chris if any exact preflight, barrier, owner closure, restore, convergence, recovery gate, or retained criterion fails. In particular: do not delete/close/reindex/rollover, allocate stale primaries, replace PVCs, reset the cluster, restart workloads, promote Fluentd, or create the schema Job.
