// Test-only adapter: unchanged Kyverno v1.18.1 full policy validator and
// CLI PolicyProcessor. Not the Kyverno CLI and not an installed webhook test.
package main

import (
	"context"
	"fmt"
	kyvernov1 "github.com/kyverno/kyverno/api/kyverno/v1"
	policyloader "github.com/kyverno/kyverno/cmd/cli/kubectl-kyverno/policy"
	"github.com/kyverno/kyverno/cmd/cli/kubectl-kyverno/processor"
	"github.com/kyverno/kyverno/cmd/cli/kubectl-kyverno/store"
	"github.com/kyverno/kyverno/cmd/cli/kubectl-kyverno/userinfo"
	"github.com/kyverno/kyverno/cmd/cli/kubectl-kyverno/variables"
	"github.com/kyverno/kyverno/pkg/clients/dclient"
	policyvalidation "github.com/kyverno/kyverno/pkg/validation/policy"
	"github.com/spf13/pflag"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/version"
	discoveryfake "k8s.io/client-go/discovery/fake"
	"k8s.io/client-go/dynamic"
	dynamicfake "k8s.io/client-go/dynamic/fake"
	"k8s.io/client-go/kubernetes"
	kubefake "k8s.io/client-go/kubernetes/fake"
	"k8s.io/client-go/tools/clientcmd"
	"net/url"
	"os"
	"sigs.k8s.io/yaml"
	"time"
)

func validatePolicy(p *kyvernov1.ClusterPolicy) error {
	kube := kubefake.NewSimpleClientset()
	kube.Discovery().(*discoveryfake.FakeDiscovery).FakedServerVersion = &version.Info{Major: "1", Minor: "35", GitVersion: "v1.35.4"}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	client, err := dclient.NewClient(ctx, dynamicfake.NewSimpleDynamicClient(runtime.NewScheme()), kube, time.Hour, false, nil)
	if err != nil {
		return err
	}
	warnings, err := policyvalidation.Validate(p, nil, client, true, "fixture", "fixture")
	for _, w := range warnings {
		fmt.Fprintln(os.Stderr, "validator warning:", w)
	}
	return err
}

func run(args []string) (int, error) {
	if len(args) == 0 || args[0] != "apply" {
		return 2, fmt.Errorf("test adapter supports only apply")
	}
	flags := pflag.NewFlagSet("component", pflag.ContinueOnError)
	resource := flags.String("resource", "", "fixture resource")
	values := flags.StringP("values-file", "f", "", "fixture values")
	user := flags.String("userinfo", "", "fixture user")
	cluster := flags.Bool("cluster", false, "synthetic loopback API only")
	kubeconfig := flags.String("kubeconfig", "", "explicit fixture config")
	flags.Bool("remove-color", false, "CLI test compatibility")
	if err := flags.Parse(args[1:]); err != nil {
		return 2, err
	}
	if flags.NArg() != 1 {
		return 2, fmt.Errorf("exactly one policy required")
	}
	loaded, err := policyloader.Load(nil, "", flags.Arg(0))
	if err != nil {
		return 2, err
	}
	if len(loaded.NonFatalErrors) != 0 || len(loaded.Policies) != 1 {
		return 2, fmt.Errorf("full policy loader: %v", loaded.NonFatalErrors)
	}
	p, ok := loaded.Policies[0].(*kyvernov1.ClusterPolicy)
	if !ok {
		return 2, fmt.Errorf("ClusterPolicy required")
	}
	// Validate the FULL policy after unchanged upstream schema/default loading.
	if err = validatePolicy(p); err != nil {
		return 2, err
	}
	vars, err := variables.New(os.Stdout, nil, "", *values, nil)
	if err != nil {
		return 2, err
	}
	info, err := userinfo.Load(nil, *user, "")
	if err != nil {
		return 2, err
	}
	s := &store.Store{}
	s.SetLocal(true)
	s.AllowApiCall(*cluster)
	vars.SetInStore(s)
	var client dclient.Interface
	var resources []unstructured.Unstructured
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if *cluster {
		// Load explicit fixture bytes, never ambient kubeconfig or credential plugins.
		b, err := os.ReadFile(*kubeconfig)
		if err != nil {
			return 2, err
		}
		config, err := clientcmd.Load(b)
		if err != nil {
			return 2, err
		}
		for _, auth := range config.AuthInfos {
			if auth.Exec != nil || auth.AuthProvider != nil || auth.Token != "" || auth.TokenFile != "" || auth.ClientKey != "" || len(auth.ClientKeyData) != 0 || auth.Password != "" {
				return 2, fmt.Errorf("credentials forbidden in fixture")
			}
		}
		for _, c := range config.Clusters {
			u, err := url.Parse(c.Server)
			if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.User != nil {
				return 2, fmt.Errorf("only HTTP IPv4 loopback fixture allowed")
			}
		}
		cfg, err := clientcmd.NewNonInteractiveClientConfig(*config, config.CurrentContext, &clientcmd.ConfigOverrides{}, nil).ClientConfig()
		if err != nil {
			return 2, err
		}
		cfg.Timeout = 10 * time.Second
		kube, err := kubernetes.NewForConfig(cfg)
		if err != nil {
			return 2, err
		}
		dyn, err := dynamic.NewForConfig(cfg)
		if err != nil {
			return 2, err
		}
		client, err = dclient.NewClient(ctx, dyn, kube, time.Hour, false, nil)
		if err != nil {
			return 2, err
		}
		list, err := client.ListResource(ctx, "v1", "ConfigMap", "alpha", nil)
		if err != nil {
			return 2, err
		}
		resources = list.Items
	} else {
		b, err := os.ReadFile(*resource)
		if err != nil {
			return 2, err
		}
		var obj unstructured.Unstructured
		if err = yaml.Unmarshal(b, &obj.Object); err != nil {
			return 2, err
		}
		resources = []unstructured.Unstructured{obj}
	}
	if len(resources) != 1 {
		return 2, fmt.Errorf("exactly one fixture resource required")
	}
	rc := &processor.ResultCounts{}
	proc := processor.PolicyProcessor{Store: s, Policies: []kyvernov1.PolicyInterface{p}, Resource: resources[0], Variables: vars, UserInfo: &info.RequestInfo, NamespaceSelectorMap: vars.NamespaceSelectors(), Rc: rc, Client: client, Cluster: *cluster, Out: os.Stdout, NamespaceCache: map[string]*unstructured.Unstructured{}}
	responses, err := proc.ApplyPoliciesOnResource()
	if err != nil {
		return 2, err
	}
	for _, response := range responses {
		for _, rule := range response.PolicyResponse.Rules {
			fmt.Fprintf(os.Stdout, "rule=%s status=%s message=%s\n", rule.Name(), rule.Status(), rule.Message())
		}
	}
	fmt.Printf("component Kyverno v1.18.1: pass: %d, fail: %d, warn: %d, error: %d, skip: %d\n", rc.Pass, rc.Fail, rc.Warn, rc.Error, rc.Skip)
	if rc.Fail+rc.Error != 0 {
		return 1, nil
	}
	// Like CLI apply, no failed/error rule means allowed. In particular the
	// unchanged engine returns no rules when webhook matchConditions exclude an
	// ordinary ConfigMap. The separate apiserver matcher tests assert exclusion.
	return 0, nil
}
func main() {
	code, err := run(os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
	os.Exit(code)
}
