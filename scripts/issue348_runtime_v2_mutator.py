#!/usr/bin/env python3
"""The sole write-capable boundary for Issue #348 runtime-v2 plans."""

import json


class MutationRejected(RuntimeError):
    """The requested operation is absent from the approved immutable plan."""


class Mutator:
    """Execute only exact operations carried by an approved MutationPlan.

    In production ``kubectl_adapter=True`` means the injected callable is the
    transition's bounded kubectl transport. Tests may inject an operation sink;
    neither mode permits a caller-supplied target or payload.
    """

    def __init__(self, plan, adapter, *, kubectl_adapter=False):
        self._plan = plan
        self._adapter = adapter
        self._kubectl_adapter = kubectl_adapter
        self._next = 0
        self._rollback_names = {item.name: item for item in plan.rollback}

    @staticmethod
    def _resolve(operation, observed):
        resource_version = operation.resource_version
        if resource_version.startswith("$"):
            try:
                resource_version = observed["metadata"]["resourceVersion"]
            except (KeyError, TypeError) as error:
                raise MutationRejected(
                    "dynamic CAS resourceVersion requires an exact readback"
                ) from error
        return operation._replace(resource_version=resource_version)

    @staticmethod
    def _payload(operation):
        tests = [
            {"op": "test", "path": "/metadata/uid", "value": operation.uid},
            {"op": "test", "path": "/metadata/resourceVersion",
             "value": operation.resource_version},
        ]
        tests.extend(
            {"op": "test", "path": path, "value": value}
            for path, value in operation.extra_preconditions
        )
        tests.extend([
            {"op": "test", "path": operation.path,
             "value": operation.current_value},
            {"op": "replace", "path": operation.path, "value": operation.after},
        ])
        return tests

    def _apply(self, operation, deadline, observed=None):
        if not self._kubectl_adapter:
            self._adapter(operation, deadline)
            return operation
        resolved = self._resolve(operation, observed)
        payload = self._payload(resolved)
        output = self._adapter(
            ["patch", resolved.kind, resolved.resource_name, "--type=json", "-p",
             json.dumps(payload, sort_keys=True, separators=(",", ":")), "-o", "json"],
            deadline,
            resolved.namespace,
        )
        try:
            value = json.loads(output)
        except (TypeError, json.JSONDecodeError) as error:
            raise MutationRejected("patch returned invalid JSON") from error
        if not isinstance(value, dict):
            raise MutationRejected("patch returned wrong JSON shape")
        return value

    def execute(self, operation_name, deadline, observed=None):
        if self._next >= len(self._plan.normal):
            raise MutationRejected("operation is not in the approved plan")
        operation = self._plan.normal[self._next]
        if operation.name != operation_name:
            raise MutationRejected(
                "operation is not the next operation in the approved plan"
            )
        result = self._apply(operation, deadline, observed)
        self._next += 1
        return result

    def execute_operation(self, operation, deadline, observed=None):
        if self._next >= len(self._plan.normal) or operation != self._plan.normal[self._next]:
            raise MutationRejected("operation is not in the approved plan")
        result = self._apply(operation, deadline, observed)
        self._next += 1
        return result

    def rollback(self, operation_name, deadline, observed=None):
        operation = self._rollback_names.pop(operation_name, None)
        if operation is None:
            raise MutationRejected("rollback operation is not in the approved plan")
        return self._apply(operation, deadline, observed)
