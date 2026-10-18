package main

import (
	kyvernov1 "github.com/kyverno/kyverno/api/kyverno/v1"
	"os"
	"sigs.k8s.io/yaml"
	"testing"
)

func TestFullPolicyValidation(t *testing.T) {
	b, err := os.ReadFile("../../../policies/kyverno/cluster-policies/protect-grafana-import.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var p kyvernov1.ClusterPolicy
	if err = yaml.UnmarshalStrict(b, &p); err != nil {
		t.Fatal(err)
	}
	if err = validatePolicy(&p); err != nil {
		t.Fatal(err)
	}
	p.Spec.WebhookConfiguration.MatchConditions[0].Expression = "this is not CEL"
	if err = validatePolicy(&p); err == nil {
		t.Fatal("invalid condition accepted: validation was bypassed")
	}
}
