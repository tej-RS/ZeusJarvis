from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.core.config import JarvisConfig
from openjarvis.core.registry import ToolRegistry
from openjarvis.hud.session import JarvisSession
from openjarvis.tools.apply_patch import ApplyPatchTool
from openjarvis.tools.calculator import CalculatorTool
from openjarvis.tools.file_read import FileReadTool
from openjarvis.tools.file_write import FileWriteTool


@pytest.fixture(autouse=True)
def _register_tools() -> None:
    # conftest clears every registry before each test.
    for name, cls in (
        ("file_write", FileWriteTool),
        ("apply_patch", ApplyPatchTool),
        ("file_read", FileReadTool),
        ("calculator", CalculatorTool),
    ):
        ToolRegistry.register_value(name, cls)


def _tools(enabled: str) -> dict:
    session = JarvisSession()
    session.config = JarvisConfig()
    session.config.tools.enabled = enabled
    return {tool.spec.name: tool for tool in session._build_tools()}


def test_file_changing_tools_are_added_and_ask_first() -> None:
    tools = _tools("file_read,calculator")
    assert tools["file_write"].spec.requires_confirmation
    assert tools["apply_patch"].spec.requires_confirmation
    assert not tools["file_read"].spec.requires_confirmation
    assert not tools["calculator"].spec.requires_confirmation


def test_confirming_tool_still_works(tmp_path: Path) -> None:
    write = _tools("file_write")["file_write"]
    assert isinstance(write, FileWriteTool)
    assert type(write).__name__ == "FileWriteTool"
    target = tmp_path / "note.txt"
    result = write.execute(path=str(target), content="hello")
    assert result.success, result.content
    assert target.read_text() == "hello"


def test_no_configured_tools_means_no_tools() -> None:
    assert _tools("") == {}
