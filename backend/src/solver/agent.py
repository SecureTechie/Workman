import json
import logging
import os
from pathlib import Path

import anthropic
from docker.models.containers import Container

import config
from src.docker.manager import DockerManager

logger = logging.getLogger(__name__)

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repo root"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write (create or overwrite) a file in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repo root"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_files",
        "description": "List files and directories at a given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to repo root. Use '.' for root.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": "Search for a text pattern in files (grep). Returns matching lines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text or regex to search for"},
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (default: '.')",
                    "default": ".",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command inside the Docker container (e.g. run tests, "
            "install packages, build the project). The working directory is /workspace."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"}
            },
            "required": ["command"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Call this when the fix is complete and all tests pass. "
            "Provide a summary of the changes made."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Human-readable summary of what was changed and why",
                }
            },
            "required": ["summary"],
        },
    },
]

SYSTEM_PROMPT = """\
You are Workman, an autonomous software engineer. You have been assigned a GitHub issue to fix.

Your workflow:
1. Read the issue description carefully
2. Explore the repository structure to understand the codebase
3. Find the relevant code that needs to change
4. Make the necessary edits
5. Run the project's tests to verify your fix works
6. If tests fail, debug and iterate
7. When confident the fix is correct and tests pass, call `finish` with a clear summary

Rules:
- Only change what is necessary to fix the issue — do not refactor unrelated code
- Always run tests before calling `finish`
- If there are no tests, at least verify the code runs without errors
- Write clean, idiomatic code matching the project's style
- Do not add comments unless the logic is genuinely non-obvious
"""


class IssueSolver:
    def __init__(self, docker_manager: DockerManager, container: Container, repo_path: Path):
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.docker = docker_manager
        self.container = container
        self.repo_path = repo_path

    def solve(self, issue_title: str, issue_body: str) -> str:
        """Run the solver and return a summary of what was changed."""
        user_message = (
            f"# Issue: {issue_title}\n\n"
            f"{issue_body}\n\n"
            "Please fix this issue. Start by exploring the repository structure."
        )

        messages: list[dict] = [{"role": "user", "content": user_message}]
        iterations = 0

        while iterations < config.MAX_SOLVER_ITERATIONS:
            iterations += 1
            logger.info(f"Solver iteration {iterations}")

            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            # Add assistant message to history
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Extract text response
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return "Fix completed."

            if response.stop_reason != "tool_use":
                logger.warning(f"Unexpected stop reason: {response.stop_reason}")
                break

            # Process tool calls
            tool_results = []
            done = False

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                logger.info(f"Tool: {tool_name}({json.dumps(tool_input)[:120]})")

                if tool_name == "finish":
                    summary = tool_input.get("summary", "Issue fixed.")
                    logger.info(f"Solver finished: {summary}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Done.",
                    })
                    messages.append({"role": "user", "content": tool_results})
                    return summary

                result = self._dispatch_tool(tool_name, tool_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result[:8000],  # cap to avoid token overflow
                })

            messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(f"Solver hit max iterations ({config.MAX_SOLVER_ITERATIONS}) without finishing")

    # ------------------------------------------------------------------ #
    # Tool implementations                                                  #
    # ------------------------------------------------------------------ #

    def _dispatch_tool(self, name: str, inp: dict) -> str:
        if name == "read_file":
            return self._read_file(inp["path"])
        if name == "write_file":
            return self._write_file(inp["path"], inp["content"])
        if name == "list_files":
            return self._list_files(inp.get("path", "."))
        if name == "search_code":
            return self._search_code(inp["pattern"], inp.get("path", "."))
        if name == "run_command":
            return self._run_command(inp["command"])
        return f"Unknown tool: {name}"

    def _read_file(self, rel_path: str) -> str:
        abs_path = self.repo_path / rel_path
        try:
            return abs_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return f"ERROR: File not found: {rel_path}"
        except Exception as e:
            return f"ERROR reading {rel_path}: {e}"

    def _write_file(self, rel_path: str, content: str) -> str:
        abs_path = self.repo_path / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            abs_path.write_text(content, encoding="utf-8")
            return f"Written: {rel_path}"
        except Exception as e:
            return f"ERROR writing {rel_path}: {e}"

    def _list_files(self, rel_path: str) -> str:
        abs_path = self.repo_path / rel_path
        if not abs_path.exists():
            return f"ERROR: Path does not exist: {rel_path}"
        entries = []
        for item in sorted(abs_path.iterdir()):
            prefix = "/" if item.is_dir() else ""
            entries.append(f"{item.name}{prefix}")
        return "\n".join(entries) if entries else "(empty)"

    def _search_code(self, pattern: str, path: str) -> str:
        result = self.docker.exec(
            self.container,
            f"grep -rn --include='*' -l {json.dumps(pattern)} {path} 2>/dev/null | head -20 && "
            f"grep -rn {json.dumps(pattern)} {path} 2>/dev/null | head -50",
        )
        out = result["stdout"].strip()
        return out if out else f"No matches for '{pattern}' in {path}"

    def _run_command(self, command: str) -> str:
        result = self.docker.exec(self.container, command)
        parts = []
        if result["stdout"].strip():
            parts.append(result["stdout"].strip())
        if result["stderr"].strip():
            parts.append(f"[stderr]\n{result['stderr'].strip()}")
        parts.append(f"[exit code: {result['exit_code']}]")
        return "\n".join(parts)
