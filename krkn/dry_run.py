# Copyright 2025 The Krkn Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Dry-run preview helpers.

Target discovery and scenario config preview without running any chaos.
"""

import logging
import os
import copy

import yaml

from krkn.scenario_config_parser import parse_scenario_config
from krkn.scenario_plugins.scenario_plugin_factory import ScenarioPluginNotFound
from krkn.scenario_plugins.pod_disruption.models.models import InputParams


_SENSITIVE_KEYS = frozenset({
    "password", "passwords", "secret", "secrets",
    "token", "tokens", "bearer", "bearer_token",
    "authorization", "auth", "api_key", "apikey",
    "credential", "credentials", "private_key",
    "access_key", "secret_key",
})


def _is_sensitive_key(key: str) -> bool:
    return key.lower().strip() in _SENSITIVE_KEYS


def _redact_value(obj):
    """Mask sensitive fields before logging config."""
    if isinstance(obj, dict):
        redacted = {}
        for k, v in obj.items():
            if _is_sensitive_key(k):
                redacted[k] = "***REDACTED***"
            elif isinstance(v, (dict, list)):
                redacted[k] = _redact_value(v)
            else:
                redacted[k] = v
        return redacted
    elif isinstance(obj, list):
        return [_redact_value(item) for item in obj]
    return obj


def _dry_run_pod_disruption(scenario_config, plugin, kubecli):
    """Preview pod disruption targets without executing scenarios."""
    targets = 0
    errors = 0
    if scenario_config is None:
        logging.error("    Scenario file is empty (parsed as None).")
        return 0, 1
    if not isinstance(scenario_config, list):
        scenario_config = [scenario_config]

    for entry in scenario_config:
        config_dict = entry.get("config") if isinstance(entry, dict) else None
        if config_dict is None:
            logging.error(
                "    Missing 'config' key in scenario entry."
            )
            errors += 1
            continue

        try:
            params = InputParams(config_dict)
        except (ValueError, KeyError, TypeError) as e:
            logging.error(
                "    Invalid scenario configuration: %s", e
            )
            errors += 1
            continue

        logging.info("    Namespace pattern: %s", params.namespace_pattern)
        if params.name_pattern:
            logging.info("    Name pattern: %s", params.name_pattern)
        if params.label_selector:
            logging.info("    Label selector: %s", params.label_selector)
        if params.exclude_label:
            logging.info("    Exclude label: %s", params.exclude_label)
        if params.node_label_selector:
            logging.info("    Node label selector: %s", params.node_label_selector)
        if params.node_names:
            logging.info("    Node names: %s", params.node_names)
        logging.info("    Kill count: %d", params.kill)

        try:
            pods = plugin.get_pods(
                name_pattern=params.name_pattern,
                label_selector=params.label_selector,
                namespace=params.namespace_pattern,
                kubecli=kubecli,
                field_selector="status.phase=Running",
                node_label_selector=params.node_label_selector,
                node_names=params.node_names,
            )

            if params.exclude_label:
                exclude_pods_set = set()
                _exclude = plugin.get_pods(
                    name_pattern="",
                    label_selector=params.exclude_label,
                    namespace=params.namespace_pattern,
                    kubecli=kubecli,
                    field_selector="status.phase=Running",
                    node_label_selector=params.node_label_selector,
                    node_names=params.node_names,
                )
                for pod in _exclude:
                    exclude_pods_set.add((pod[0], pod[1]))
                eligible_pods = [
                    p for p in pods if (p[0], p[1]) not in exclude_pods_set
                ]
            else:
                eligible_pods = list(pods) if pods else []

            logging.info("    Targets:")
            if eligible_pods:
                for pod in eligible_pods:
                    logging.info(
                        "      - namespace: %s  pod: %s", pod[1], pod[0]
                    )
            else:
                logging.info("      (none)")

            logging.info("    Eligible pods: %d", len(eligible_pods))

            if len(eligible_pods) < params.kill:
                logging.error(
                    "    Would FAIL: not enough pods (%d found, %d needed).",
                    len(eligible_pods),
                    params.kill,
                )
                logging.info("    Would kill: 0 pod(s)")
                errors += 1
            else:
                logging.info("    Would kill: %d pod(s)", params.kill)
                targets += params.kill

        except Exception as e:
            logging.error("    Failed to discover targets: %s", e)
            errors += 1

    return targets, errors


def _dry_run_scenarios(chaos_scenarios, scenario_plugin_factory, kubecli):
    """Walk configured scenarios, resolve plugins, and preview targets."""
    logging.info("")
    logging.info("=" * 60)
    logging.info("DRY RUN: No chaos actions will be executed.")
    logging.info("=" * 60)

    total_targets = 0
    total_errors = 0
    unsupported_scenario_types = []

    for scenario in chaos_scenarios:
        scenario_type, scenarios_list, scenario_weight = parse_scenario_config(
            scenario
        )

        logging.info("")
        logging.info("Scenario type: %s", scenario_type)
        if scenario_weight != 1:
            logging.info("  Weight: %s", scenario_weight)

        try:
            scenario_plugin = scenario_plugin_factory.create_plugin(scenario_type)
        except ScenarioPluginNotFound:
            logging.error(
                "  Plugin not found for scenario type: %s", scenario_type
            )
            total_errors += 1
            continue

        logging.info("  Plugin: %s", scenario_plugin.__class__.__name__)

        if not scenarios_list:
            logging.info("  No scenario files configured.")
            continue

        for scenario_file in scenarios_list:
            logging.info("  Scenario file: %s", scenario_file)

            if not os.path.exists(scenario_file):
                logging.error("    File not found: %s", scenario_file)
                total_errors += 1
                continue

            try:
                with open(scenario_file, "r") as f:
                    scenario_config = yaml.safe_load(f)
            except Exception as e:
                logging.error("    Failed to parse scenario file: %s", e)
                total_errors += 1
                continue

            if scenario_config is None:
                logging.error("    Scenario file is empty: %s", scenario_file)
                total_errors += 1
                continue

            if (
                scenario_type == "pod_disruption_scenarios"
                and hasattr(scenario_plugin, "get_pods")
            ):
                found, errs = _dry_run_pod_disruption(
                    scenario_config, scenario_plugin, kubecli
                )
                total_targets += found
                total_errors += errs
            else:
                safe_config = _redact_value(
                    copy.deepcopy(scenario_config)
                )
                logging.info("    Scenario config:")
                if isinstance(safe_config, dict):
                    for key, value in safe_config.items():
                        logging.info("      %s: %s", key, value)
                elif isinstance(safe_config, list):
                    for item in safe_config:
                        if isinstance(item, dict):
                            for key, value in item.items():
                                logging.info("      %s: %s", key, value)
                        else:
                            logging.info("      %s", item)

                unsupported_scenario_types.append(scenario_type)
                logging.info(
                    "    (Detailed target listing not available for %s; "
                    "target count undetermined)",
                    scenario_type,
                )

    logging.info("")
    logging.info("=" * 60)

    if unsupported_scenario_types:
        unique_unsupported = sorted(set(unsupported_scenario_types))
        logging.info(
            "Scenario types with undetermined targets: %s",
            ", ".join(unique_unsupported),
        )

    logging.info("Total targets found (checked scenarios): %d", total_targets)

    if total_targets == 0 and not unsupported_scenario_types:
        logging.info("No matching resources found.")

    if total_errors > 0:
        logging.warning(
            "DRY RUN COMPLETE with %d error(s): "
            "Some previews failed. No changes were made to the cluster.",
            total_errors,
        )
        logging.info("=" * 60)
        return 1

    logging.info("DRY RUN COMPLETE: No changes were made to the cluster.")
    logging.info("=" * 60)

    return 0
