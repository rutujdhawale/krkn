#!/usr/bin/env python3

"""
Tests for --dry-run.

    python -m pytest tests/test_dry_run.py -v
"""

import unittest
from unittest.mock import patch, mock_open, MagicMock
from types import SimpleNamespace

from krkn.dry_run import _dry_run_scenarios, _dry_run_pod_disruption, _redact_value


class TestDryRunPodDisruption(unittest.TestCase):

    def setUp(self):
        self.plugin = MagicMock()
        self.kubecli = MagicMock()

    def test_discovers_pods_with_label_selector(self):
        """label_selector picks up matching pods."""
        self.plugin.get_pods.return_value = [
            ("etcd-0", "openshift-etcd"),
            ("etcd-1", "openshift-etcd"),
        ]

        config = [{"config": {
            "namespace_pattern": "^openshift-etcd$",
            "label_selector": "k8s-app=etcd",
            "kill": 1,
        }}]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 1)
        self.assertEqual(errors, 0)
        self.plugin.get_pods.assert_called_once_with(
            name_pattern="",
            label_selector="k8s-app=etcd",
            namespace="^openshift-etcd$",
            kubecli=self.kubecli,
            field_selector="status.phase=Running",
            node_label_selector="",
            node_names=[],
        )

    def test_discovers_pods_with_name_pattern(self):
        """name_pattern picks up matching pods."""
        self.plugin.get_pods.return_value = [
            ("api-server-abc", "kube-system"),
        ]

        config = [{"config": {
            "namespace_pattern": "^kube-system$",
            "name_pattern": "api-server-.*",
            "kill": 1,
        }}]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 1)
        self.assertEqual(errors, 0)
        self.plugin.get_pods.assert_called_once_with(
            name_pattern="api-server-.*",
            label_selector="",
            namespace="^kube-system$",
            kubecli=self.kubecli,
            field_selector="status.phase=Running",
            node_label_selector="",
            node_names=[],
        )

    def test_exclude_label_filters_pods(self):
        """exclude_label removes matching pods from eligible list."""
        self.plugin.get_pods.side_effect = [
            [("pod-1", "ns"), ("pod-2", "ns"), ("pod-3", "ns")],
            [("pod-2", "ns")],
        ]

        config = [{"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": 2,
            "exclude_label": "exclude=true",
        }}]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 2)
        self.assertEqual(errors, 0)
        self.assertEqual(self.plugin.get_pods.call_count, 2)

    def test_kill_count_limits_targets(self):
        """kill count caps reported targets."""
        self.plugin.get_pods.return_value = [
            ("pod-1", "ns"), ("pod-2", "ns"), ("pod-3", "ns"),
            ("pod-4", "ns"), ("pod-5", "ns"),
        ]

        config = [{"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": 2,
        }}]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 2)
        self.assertEqual(errors, 0)

    def test_zero_matching_pods(self):
        self.plugin.get_pods.return_value = []

        config = [{"config": {
            "namespace_pattern": "^nonexistent$",
            "name_pattern": ".*",
            "kill": 1,
        }}]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 0)
        self.assertEqual(errors, 1)

    def test_does_not_call_delete_pod(self):
        self.plugin.get_pods.return_value = [("pod-1", "ns")]

        config = [{"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": 1,
        }}]

        _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.kubecli.delete_pod.assert_not_called()

    def test_multiple_scenario_entries(self):
        self.plugin.get_pods.side_effect = [
            [("etcd-0", "etcd-ns")],
            [("api-0", "api-ns"), ("api-1", "api-ns")],
        ]

        config = [
            {"config": {
                "namespace_pattern": "^etcd-ns$",
                "label_selector": "app=etcd",
                "kill": 1,
            }},
            {"config": {
                "namespace_pattern": "^api-ns$",
                "name_pattern": "api-.*",
                "kill": 1,
            }},
        ]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 2)
        self.assertEqual(errors, 0)
        self.assertEqual(self.plugin.get_pods.call_count, 2)

    def test_node_label_selector_passed_through(self):
        self.plugin.get_pods.return_value = [("pod-1", "ns")]

        config = [{"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": 1,
            "node_label_selector": "node-role.kubernetes.io/worker=",
        }}]

        _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        call_kwargs = self.plugin.get_pods.call_args[1]
        self.assertEqual(
            call_kwargs["node_label_selector"],
            "node-role.kubernetes.io/worker=",
        )

    def test_node_names_passed_through(self):
        self.plugin.get_pods.return_value = [("pod-1", "ns")]

        config = [{"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": 1,
            "node_names": ["worker-0", "worker-1"],
        }}]

        _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        call_kwargs = self.plugin.get_pods.call_args[1]
        self.assertEqual(call_kwargs["node_names"], ["worker-0", "worker-1"])


    def test_insufficient_pods_reports_zero_kills(self):
        self.plugin.get_pods.return_value = [
            ("pod-1", "ns"), ("pod-2", "ns"),
        ]

        config = [{"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": 3,
        }}]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 0)
        self.assertEqual(errors, 1)


    def test_negative_kill_count_rejected(self):
        config = [{"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": -1,
        }}]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 0)
        self.assertEqual(errors, 1)
        self.plugin.get_pods.assert_not_called()

    def test_boolean_kill_count_rejected(self):
        config = [{"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": True,
        }}]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 0)
        self.assertEqual(errors, 1)


    def test_missing_config_key_reports_error(self):
        config = [{"namespace_pattern": "^ns$", "label_selector": "app=x", "kill": 1}]

        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)

        self.assertEqual(targets, 0)
        self.assertEqual(errors, 1)

    def test_single_dict_scenario_config(self):
        """Accepts a single dict instead of a list in scenario_config."""
        self.plugin.get_pods.return_value = [("pod-1", "ns")]
        config = {"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": 1,
        }}
        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)
        self.assertEqual(targets, 1)
        self.assertEqual(errors, 0)


class TestDryRunScenarios(unittest.TestCase):

    def setUp(self):
        self.factory = MagicMock()
        self.kubecli = MagicMock()

    def test_empty_scenarios_returns_zero(self):
        result = _dry_run_scenarios([], self.factory, self.kubecli)
        self.assertEqual(result, 0)

    def test_missing_plugin_continues(self):
        from krkn.scenario_plugins.scenario_plugin_factory import (
            ScenarioPluginNotFound,
        )

        self.factory.create_plugin.side_effect = ScenarioPluginNotFound("nope")

        scenarios = [{"unknown_scenarios": ["scenario.yml"]}]
        result = _dry_run_scenarios(scenarios, self.factory, self.kubecli)
        self.assertEqual(result, 1)

    def test_missing_scenario_file_returns_error(self):
        mock_plugin = MagicMock()
        self.factory.create_plugin.return_value = mock_plugin

        scenarios = [{"hog_scenarios": ["/nonexistent/file.yml"]}]
        result = _dry_run_scenarios(scenarios, self.factory, self.kubecli)
        self.assertEqual(result, 1)

    @patch("krkn.dry_run.os.path.exists", return_value=True)
    def test_pod_disruption_calls_get_pods(self, mock_exists):
        mock_plugin = MagicMock()
        mock_plugin.__class__.__name__ = "PodDisruptionScenarioPlugin"
        mock_plugin.get_pods.return_value = [
            ("pod-1", "default"),
            ("pod-2", "default"),
        ]
        self.factory.create_plugin.return_value = mock_plugin

        scenario_yaml = (
            "- config:\n"
            "    namespace_pattern: ^default$\n"
            "    label_selector: app=test\n"
            "    kill: 2\n"
        )
        scenarios = [{"pod_disruption_scenarios": ["scenarios/test.yml"]}]

        with patch("builtins.open", mock_open(read_data=scenario_yaml)):
            result = _dry_run_scenarios(scenarios, self.factory, self.kubecli)

        self.assertEqual(result, 0)
        mock_plugin.get_pods.assert_called()

    @patch("krkn.dry_run.os.path.exists", return_value=True)
    def test_never_calls_run_scenarios(self, mock_exists):
        mock_plugin = MagicMock()
        mock_plugin.__class__.__name__ = "HogsScenarioPlugin"
        del mock_plugin.get_pods
        self.factory.create_plugin.return_value = mock_plugin

        scenarios = [{"hog_scenarios": ["scenarios/cpu-hog.yml"]}]

        with patch("builtins.open", mock_open(read_data="duration: 10\n")):
            _dry_run_scenarios(scenarios, self.factory, self.kubecli)

        mock_plugin.run_scenarios.assert_not_called()
        mock_plugin.run.assert_not_called()

    @patch("krkn.dry_run.os.path.exists", return_value=True)
    def test_never_calls_delete_pod(self, mock_exists):
        mock_plugin = MagicMock()
        mock_plugin.__class__.__name__ = "PodDisruptionScenarioPlugin"
        mock_plugin.get_pods.return_value = [("pod-1", "ns")]
        self.factory.create_plugin.return_value = mock_plugin

        scenario_yaml = (
            "- config:\n"
            "    namespace_pattern: ^ns$\n"
            "    label_selector: app=test\n"
            "    kill: 1\n"
        )
        scenarios = [{"pod_disruption_scenarios": ["scenarios/test.yml"]}]

        with patch("builtins.open", mock_open(read_data=scenario_yaml)):
            _dry_run_scenarios(scenarios, self.factory, self.kubecli)

        self.kubecli.delete_pod.assert_not_called()

    @patch("krkn.dry_run.os.path.exists", return_value=True)
    def test_generic_scenario_shows_config(self, mock_exists):
        mock_plugin = MagicMock()
        mock_plugin.__class__.__name__ = "ContainerScenarioPlugin"
        del mock_plugin.get_pods
        self.factory.create_plugin.return_value = mock_plugin

        scenario_yaml = (
            "scenarios:\n"
            "  - name: kill-container\n"
            "    namespace: default\n"
        )
        scenarios = [{"container_scenarios": ["scenarios/container.yml"]}]

        with patch("builtins.open", mock_open(read_data=scenario_yaml)):
            result = _dry_run_scenarios(scenarios, self.factory, self.kubecli)

        self.assertEqual(result, 0)


    @patch("krkn.dry_run.os.path.exists", return_value=True)
    def test_unsupported_scenario_not_counted_as_zero(self, mock_exists):
        mock_plugin = MagicMock()
        mock_plugin.__class__.__name__ = "HogsScenarioPlugin"
        del mock_plugin.get_pods
        self.factory.create_plugin.return_value = mock_plugin

        scenarios = [{"hog_scenarios": ["scenarios/cpu-hog.yml"]}]

        with patch("builtins.open", mock_open(read_data="duration: 10\n")):
            result = _dry_run_scenarios(scenarios, self.factory, self.kubecli)

        self.assertEqual(result, 0)

    def test_empty_scenarios_list_continues(self):
        mock_plugin = MagicMock()
        mock_plugin.__class__.__name__ = "PodDisruptionScenarioPlugin"
        self.factory.create_plugin.return_value = mock_plugin
        scenarios = [{"pod_disruption_scenarios": []}]
        result = _dry_run_scenarios(scenarios, self.factory, self.kubecli)
        self.assertEqual(result, 0)

    @patch("krkn.dry_run.os.path.exists", return_value=True)
    def test_invalid_yaml_file_returns_error(self, mock_exists):
        mock_plugin = MagicMock()
        mock_plugin.__class__.__name__ = "PodDisruptionScenarioPlugin"
        self.factory.create_plugin.return_value = mock_plugin
        scenarios = [{"pod_disruption_scenarios": ["scenarios/bad.yml"]}]
        with patch("builtins.open", mock_open(read_data="key: [unclosed list")):
            result = _dry_run_scenarios(scenarios, self.factory, self.kubecli)
        self.assertEqual(result, 1)


class TestDryRunMainIntegration(unittest.TestCase):
    """Integration tests for --dry-run through main()."""

    def test_main_calls_dry_run_when_flag_set(self):
        from run_kraken import main

        config = {
            "kraken": {"kubeconfig_path": "/fake/kubeconfig"},
            "tunings": {"iterations": 1},
            "performance_monitoring": {},
            "elastic": {},
            "cerberus": {},
            "telemetry": {},
        }

        mock_factory = MagicMock()
        mock_factory.loaded_plugins = {}
        mock_factory.failed_plugins = []

        mock_hcf = MagicMock()
        mock_hcf.loaded_plugins = {}
        mock_hcf.failed_plugins = []

        options = SimpleNamespace(cfg="/fake/config.yaml", dry_run=True)

        with patch("run_kraken.os.path.isfile", return_value=True), \
                patch("builtins.open", mock_open()), \
                patch("run_kraken.yaml.safe_load", return_value=config), \
                patch("run_kraken.RollbackConfig.register"), \
                patch("run_kraken.SafeLogger"), \
                patch("run_kraken.KrknKubernetes"), \
                patch("run_kraken.KrknOpenshift") as mock_ocp, \
                patch("run_kraken.KrknTelemetryKubernetes"), \
                patch("run_kraken.KrknTelemetryOpenshift"), \
                patch("run_kraken.ScenarioPluginFactory", return_value=mock_factory), \
                patch("run_kraken.HealthCheckFactory", return_value=mock_hcf), \
                patch("run_kraken._dry_run_scenarios", return_value=0) as mock_dry_run:
            mock_ocp.return_value.is_openshift.return_value = False

            result = main(options, None)

        self.assertEqual(result, 0)
        mock_dry_run.assert_called_once()

    def test_dry_run_skips_status_server(self):
        from run_kraken import main

        config = {
            "kraken": {
                "kubeconfig_path": "/fake/kubeconfig",
                "publish_kraken_status": True,
            },
            "tunings": {"iterations": 1},
            "performance_monitoring": {},
            "elastic": {},
            "cerberus": {},
            "telemetry": {},
        }

        mock_factory = MagicMock()
        mock_factory.loaded_plugins = {}
        mock_factory.failed_plugins = []

        options = SimpleNamespace(cfg="/fake/config.yaml", dry_run=True)

        with patch("run_kraken.os.path.isfile", return_value=True), \
                patch("builtins.open", mock_open()), \
                patch("run_kraken.yaml.safe_load", return_value=config), \
                patch("run_kraken.RollbackConfig.register"), \
                patch("run_kraken.SafeLogger"), \
                patch("run_kraken.KrknKubernetes"), \
                patch("run_kraken.KrknOpenshift") as mock_ocp, \
                patch("run_kraken.KrknTelemetryKubernetes"), \
                patch("run_kraken.KrknTelemetryOpenshift"), \
                patch("run_kraken.ScenarioPluginFactory", return_value=mock_factory), \
                patch("run_kraken.server") as mock_server, \
                patch("run_kraken._dry_run_scenarios", return_value=0):
            mock_ocp.return_value.is_openshift.return_value = False

            main(options, None)

        mock_server.start_server.assert_not_called()

    def test_dry_run_skips_health_checks(self):
        from run_kraken import main

        config = {
            "kraken": {"kubeconfig_path": "/fake/kubeconfig"},
            "tunings": {"iterations": 1},
            "performance_monitoring": {},
            "elastic": {},
            "cerberus": {},
            "telemetry": {},
        }

        mock_factory = MagicMock()
        mock_factory.loaded_plugins = {}
        mock_factory.failed_plugins = []

        mock_hcf = MagicMock()
        mock_hcf.loaded_plugins = {}
        mock_hcf.failed_plugins = []

        options = SimpleNamespace(cfg="/fake/config.yaml", dry_run=True)

        with patch("run_kraken.os.path.isfile", return_value=True), \
                patch("builtins.open", mock_open()), \
                patch("run_kraken.yaml.safe_load", return_value=config), \
                patch("run_kraken.RollbackConfig.register"), \
                patch("run_kraken.SafeLogger"), \
                patch("run_kraken.KrknKubernetes"), \
                patch("run_kraken.KrknOpenshift") as mock_ocp, \
                patch("run_kraken.KrknTelemetryKubernetes"), \
                patch("run_kraken.KrknTelemetryOpenshift"), \
                patch("run_kraken.ScenarioPluginFactory", return_value=mock_factory), \
                patch("run_kraken.HealthCheckFactory", return_value=mock_hcf), \
                patch("run_kraken._dry_run_scenarios", return_value=0):
            mock_ocp.return_value.is_openshift.return_value = False

            main(options, None)

        mock_hcf.run_all_once.assert_not_called()
        mock_hcf.start_all.assert_not_called()

    def test_dry_run_skips_prometheus_token_acquisition(self):
        """dry run on openshift shouldn't call get_prometheus_api_connection_data"""
        from run_kraken import main

        config = {
            "kraken": {"kubeconfig_path": "/fake/kubeconfig"},
            "tunings": {"iterations": 1},
            "performance_monitoring": {},
            "elastic": {},
            "cerberus": {},
            "telemetry": {},
        }

        mock_factory = MagicMock()
        mock_factory.loaded_plugins = {}
        mock_factory.failed_plugins = []

        options = SimpleNamespace(cfg="/fake/config.yaml", dry_run=True)

        with patch("run_kraken.os.path.isfile", return_value=True), \
                patch("builtins.open", mock_open()), \
                patch("run_kraken.yaml.safe_load", return_value=config), \
                patch("run_kraken.RollbackConfig.register"), \
                patch("run_kraken.SafeLogger"), \
                patch("run_kraken.KrknKubernetes"), \
                patch("run_kraken.KrknOpenshift") as mock_ocp, \
                patch("run_kraken.KrknTelemetryKubernetes"), \
                patch("run_kraken.KrknTelemetryOpenshift"), \
                patch("run_kraken.ScenarioPluginFactory", return_value=mock_factory), \
                patch("run_kraken._dry_run_scenarios", return_value=0):
            mock_ocp_inst = mock_ocp.return_value
            mock_ocp_inst.is_openshift.return_value = True

            main(options, None)

        mock_ocp_inst.get_prometheus_api_connection_data.assert_not_called()


    def test_dry_run_rejects_execute_rollback_command(self):
        from run_kraken import main

        options = SimpleNamespace(cfg="/fake/config.yaml", dry_run=True)

        result = main(options, "execute-rollback")

        self.assertEqual(result, -1)

    def test_dry_run_rejects_list_rollback_command(self):
        from run_kraken import main

        options = SimpleNamespace(cfg="/fake/config.yaml", dry_run=True)

        result = main(options, "list-rollback")

        self.assertEqual(result, -1)

    def test_dry_run_rejects_resiliency_score_command(self):
        from run_kraken import main

        options = SimpleNamespace(cfg="/fake/config.yaml", dry_run=True)

        result = main(options, "resiliency-score")

        self.assertEqual(result, -1)

    def test_backward_compatible_without_dry_run_attr(self):
        options = SimpleNamespace(cfg="/fake/config.yaml")
        self.assertFalse(getattr(options, 'dry_run', False))


class TestRedactValue(unittest.TestCase):

    def test_redacts_password_key(self):
        data = {"username": "admin", "password": "s3cret"}
        result = _redact_value(data)
        self.assertEqual(result["username"], "admin")
        self.assertEqual(result["password"], "***REDACTED***")

    def test_redacts_authorization_header(self):
        data = {"Authorization": "Bearer my-token", "url": "https://example.com"}
        result = _redact_value(data)
        self.assertEqual(result["Authorization"], "***REDACTED***")
        self.assertEqual(result["url"], "https://example.com")

    def test_redacts_nested_dicts(self):
        data = {
            "headers": {
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
            "url": "https://api.example.com",
        }
        result = _redact_value(data)
        self.assertEqual(result["headers"]["Authorization"], "***REDACTED***")
        self.assertEqual(result["headers"]["Content-Type"], "application/json")
        self.assertEqual(result["url"], "https://api.example.com")

    def test_redacts_in_list(self):
        data = [
            {"token": "abc123", "name": "step1"},
            {"secret": "xyz", "name": "step2"},
        ]
        result = _redact_value(data)
        self.assertEqual(result[0]["token"], "***REDACTED***")
        self.assertEqual(result[0]["name"], "step1")
        self.assertEqual(result[1]["secret"], "***REDACTED***")

    def test_leaves_non_sensitive_unchanged(self):
        data = {"duration": 10, "namespace": "default", "rate": "50/1s"}
        result = _redact_value(data)
        self.assertEqual(result, data)

    def test_case_insensitive_redaction(self):
        data = {"PASSWORD": "hidden", "Token": "hidden2"}
        result = _redact_value(data)
        self.assertEqual(result["PASSWORD"], "***REDACTED***")
        self.assertEqual(result["Token"], "***REDACTED***")


class TestDryRunEdgeCases(unittest.TestCase):

    def setUp(self):
        self.plugin = MagicMock()
        self.kubecli = MagicMock()

    def test_none_scenario_config(self):
        targets, errors = _dry_run_pod_disruption(None, self.plugin, self.kubecli)
        self.assertEqual(targets, 0)
        self.assertEqual(errors, 1)

    def test_non_dict_entry_in_list(self):
        config = ["not_a_dict"]
        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)
        self.assertEqual(targets, 0)
        self.assertEqual(errors, 1)

    def test_get_pods_exception_counts_as_error(self):
        self.plugin.get_pods.side_effect = RuntimeError("connection lost")
        config = [{"config": {
            "namespace_pattern": "^ns$",
            "label_selector": "app=test",
            "kill": 1,
        }}]
        targets, errors = _dry_run_pod_disruption(config, self.plugin, self.kubecli)
        self.assertEqual(targets, 0)
        self.assertEqual(errors, 1)

    @patch("krkn.dry_run.os.path.exists", return_value=True)
    def test_empty_yaml_file_returns_error(self, mock_exists):
        factory = MagicMock()
        mock_plugin = MagicMock()
        mock_plugin.__class__.__name__ = "PodDisruptionScenarioPlugin"
        factory.create_plugin.return_value = mock_plugin
        kubecli = MagicMock()

        scenarios = [{"pod_disruption_scenarios": ["scenarios/empty.yml"]}]
        with patch("builtins.open", mock_open(read_data="")):
            result = _dry_run_scenarios(scenarios, factory, kubecli)

        self.assertEqual(result, 1)

    def test_dry_run_execute_rollback_never_calls_execute(self):
        """--dry-run + execute-rollback must not reach execute_rollback_command"""
        from run_kraken import main

        options = SimpleNamespace(cfg="/fake/config.yaml", dry_run=True)

        with patch("run_kraken.execute_rollback_command") as mock_exec_rb:
            result = main(options, "execute-rollback")

        self.assertEqual(result, -1)
        mock_exec_rb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
