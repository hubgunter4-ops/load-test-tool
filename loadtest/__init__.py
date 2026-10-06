"""Herramienta ligera de pruebas de carga HTTP."""

from .charts import generate_charts, load_report
from .gui_config import GuiValues, config_from_values, parse_headers_text
from .runner import LoadConfig, LoadReport, default_headers_for_target, detect_target_type, report_json, run_load, target_header_preset
from .scenarios import SCENARIO_MODES, ScenarioConfig, ScenarioReport, run_scenario, scenario_json

__all__ = [
    "GuiValues",
    "LoadConfig",
    "LoadReport",
    "config_from_values",
    "default_headers_for_target",
    "detect_target_type",
    "target_header_preset",
    "generate_charts",
    "load_report",
    "parse_headers_text",
    "report_json",
    "run_load",
    "SCENARIO_MODES",
    "ScenarioConfig",
    "ScenarioReport",
    "run_scenario",
    "scenario_json",
]
