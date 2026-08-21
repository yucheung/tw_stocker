"""Unit tests for strategy switching in independent_sim.py."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import pytest
import exchange_calendars as xcals
import independent_sim as sim


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def test_resolve_strategy_id():
    assert sim.resolve_strategy_id(None) == "top2_score_v1"
    assert sim.resolve_strategy_id("top2") == "top2_score_v1"
    assert sim.resolve_strategy_id("top2_score_v1") == "top2_score_v1"
    assert sim.resolve_strategy_id("mr20") == "mr20"
    assert sim.resolve_strategy_id("mr20_pullback_v1") == "mr20"
    assert sim.resolve_strategy_id("rsi_reversal") == "rsi_reversal"
    assert sim.resolve_strategy_id("rsi_reversal_v1") == "rsi_reversal"


def test_init_simulation_with_mr20(temp_dir):
    mr20_dir = temp_dir / "mr20_data"
    state = sim.init_simulation(data_dir=mr20_dir, strategy="mr20")
    assert state["strategy_id"] == "mr20"
    assert state["config"]["initial_capital"] == 1_000_000.0
    assert state["config"]["max_positions"] == 2
    assert state["config"]["position_size"] == 0.45
    assert state["config"]["max_hold_days"] == 20


def test_init_simulation_with_rsi_reversal(temp_dir):
    rsi_dir = temp_dir / "rsi_data"
    state = sim.init_simulation(data_dir=rsi_dir, strategy="rsi_reversal")
    assert state["strategy_id"] == "rsi_reversal"
    assert state["config"]["initial_capital"] == 1_000_000.0
    assert state["config"]["max_positions"] == 5
    assert state["config"]["position_size"] == 0.15
    assert state["config"]["max_hold_days"] == 5


def test_cli_strategy_flag_defaults(temp_dir):
    parser = sim.build_cli_parser()
    # Test --strategy mr20 init
    args = parser.parse_args(["--strategy", "mr20", "init"])
    assert args.strategy == "mr20"
    assert args.command == "init"

    # Test subcommand level --strategy
    args_sub = parser.parse_args(["init", "--strategy", "rsi_reversal"])
    assert args_sub.strategy == "rsi_reversal"
    assert args_sub.command == "init"


def test_cli_execution_with_strategy_mr20(temp_dir):
    cmd = [
        sys.executable, "independent_sim.py",
        "--strategy", "mr20",
        "init",
        "--data-dir", str(temp_dir / "mr20_test"),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent.parent))
    assert res.returncode == 0
    assert "Initialized simulation state for [mr20]" in res.stdout
    state = sim.load_state(temp_dir / "mr20_test")
    assert state["strategy_id"] == "mr20"


def test_cli_execution_with_strategy_rsi_reversal(temp_dir):
    cmd = [
        sys.executable, "independent_sim.py",
        "--strategy", "rsi_reversal",
        "init",
        "--data-dir", str(temp_dir / "rsi_test"),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent.parent))
    assert res.returncode == 0
    assert "Initialized simulation state for [rsi_reversal]" in res.stdout
    state = sim.load_state(temp_dir / "rsi_test")
    assert state["strategy_id"] == "rsi_reversal"
    assert state["config"]["max_positions"] == 5
