package main

import (
	"context"
	"os"
	"testing"

	v1 "k8s.io/api/admissionregistration/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apiserver/pkg/admission"
	celplugin "k8s.io/apiserver/pkg/admission/plugin/cel"
	"k8s.io/apiserver/pkg/admission/plugin/webhook/matchconditions"
	"k8s.io/apiserver/pkg/cel/environment"
	"sigs.k8s.io/yaml"
)

func TestActualAPIServerMatcher(t *testing.T) {
	data, err := os.ReadFile("../../../policies/kyverno/cluster-policies/protect-grafana-import.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var policy struct {
		Spec struct {
			WebhookConfiguration struct {
				MatchConditions []v1.MatchCondition `json:"matchConditions"`
			} `json:"webhookConfiguration"`
		} `json:"spec"`
	}
	if err := yaml.Unmarshal(data, &policy); err != nil {
		t.Fatal(err)
	}
	conditions := policy.Spec.WebhookConfiguration.MatchConditions
	if len(conditions) != 1 {
		t.Fatal("exactly one webhook condition required")
	}
	expressions := []celplugin.ExpressionAccessor{(*matchconditions.MatchCondition)(&conditions[0])}
	evaluator := celplugin.NewConditionCompiler(environment.MustBaseEnvSet(environment.DefaultCompatibilityVersion())).CompileCondition(expressions, celplugin.OptionalVariableDeclarations{}, environment.NewExpressions)
	if errs := evaluator.CompilationErrors(); len(errs) != 0 {
		t.Fatal(errs)
	}
	fail := v1.Fail
	matcher := matchconditions.NewMatcher(evaluator, &fail, "webhook", "validating", "protect-grafana-import")
	plain := func(ns string) *unstructured.Unstructured {
		return &unstructured.Unstructured{Object: map[string]interface{}{"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]interface{}{"name": "ordinary", "namespace": ns}}}
	}
	label := func(ns string) *unstructured.Unstructured {
		o := plain(ns)
		o.SetLabels(map[string]string{"grafana_dashboard": "1"})
		return o
	}
	folder := plain("alpha")
	folder.SetAnnotations(map[string]string{"grafana_folder": "/tmp/dashboards/DigiOrg Apps"})
	reserved := plain("alpha")
	reserved.SetName("grafana-app-dev-alpha")
	inert := plain("alpha")
	inert.SetLabels(map[string]string{"grafana_dashboard": "0"})
	inert.SetAnnotations(map[string]string{"grafana_folder": ""})
	cases := []struct {
		name     string
		obj, old *unstructured.Unstructured
		op       admission.Operation
		want     bool
	}{
		{"plain-create", plain("alpha"), nil, admission.Create, false},
		{"plain-update", plain("alpha"), plain("alpha"), admission.Update, false},
		{"plain-delete-null-new", nil, plain("alpha"), admission.Delete, false},
		{"null-both", nil, nil, admission.Delete, false},
		{"label-create-null-old", label("alpha"), nil, admission.Create, true},
		{"folder-create", folder, nil, admission.Create, true},
		{"name-only-create", reserved, nil, admission.Create, true},
		{"strip-label", plain("alpha"), label("alpha"), admission.Update, true},
		{"strip-folder", plain("alpha"), folder, admission.Update, true},
		{"label-delete-null-new", nil, label("alpha"), admission.Delete, true},
		{"name-only-delete", nil, reserved, admission.Delete, true},
		{"foreign-replacement-update", inert, reserved, admission.Update, true},
		{"inert-values", inert, nil, admission.Create, false},
		{"ordinary-monitoring", plain("monitoring"), nil, admission.Create, false},
		{"ordinary-kube-system", plain("kube-system"), nil, admission.Create, false},
		{"protected-foreign-namespace", label("foreign"), nil, admission.Create, true},
		{"protected-kube-system-no-local-exemption", label("kube-system"), nil, admission.Create, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var obj, old runtime.Object
			if tc.obj != nil {
				obj = tc.obj
			}
			if tc.old != nil {
				old = tc.old
			}
			gvk := schema.GroupVersionKind{Version: "v1", Kind: "ConfigMap"}
			namespace, name := "", "ordinary"
			if tc.obj != nil {
				namespace, name = tc.obj.GetNamespace(), tc.obj.GetName()
			} else if tc.old != nil {
				namespace, name = tc.old.GetNamespace(), tc.old.GetName()
			}
			attrs := admission.NewAttributesRecord(obj, old, gvk, namespace, name, schema.GroupVersionResource{Version: "v1", Resource: "configmaps"}, "", tc.op, &metav1.CreateOptions{}, false, nil)
			versioned := &admission.VersionedAttributes{Attributes: attrs, VersionedObject: obj, VersionedOldObject: old, VersionedKind: gvk}
			result := matcher.Match(context.Background(), versioned, nil, nil)
			if result.Error != nil {
				t.Fatal(result.Error)
			}
			if result.Matches != tc.want {
				t.Fatalf("Matches=%v want=%v", result.Matches, tc.want)
			}
			t.Logf("actual apiserver matcher: Matches=%v (false skips webhook before invocation)", result.Matches)
		})
	}
}
