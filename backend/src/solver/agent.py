import json
import logging
import re
import shlex
import time
from pathlib import Path

import config
from src.docker.manager import NativeRunner

logger = logging.getLogger(__name__)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to repo root",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write (create or overwrite) a file in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to repo root",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file content",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories at a given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to repo root. Use '.' for root.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for a text pattern across files (grep). Returns matching lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text or regex to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search in (default: '.')",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the repository directory (e.g. run tests, install packages, build).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    }
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Call this when the fix is complete and tests pass. Provide a summary of changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Human-readable summary of what was changed and why",
                    }
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
            "strict": True,
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
5. Verify your fix with whatever the repo supports — tests, a type-check
   (`tsc --noEmit`, `mypy`), a compile check (`cargo check`, `go build`), or
   a lint. Pick the fastest verification available.
6. If verification fails, debug and iterate
7. When the fix looks correct, call `finish` with a clear summary

Rules:
- Only change what is necessary to fix the issue — do not refactor unrelated code
- Verify with the best tool available for the project. If no automated check is
  feasible, rely on careful reading of the relevant code.
- Write clean, idiomatic code matching the project's style
- Do not add comments unless the logic is genuinely non-obvious
- Be decisive: once you have read the relevant code, understood the issue, and
  written a fix, call `finish`. Do not loop retrying the same failing command.
- The `finish` summary describes the code change only. Do not mention the local
  environment, tooling, or whether verification was run.
"""

PRIMARY_MODEL = "gpt-4.1"
FALLBACK_MODEL = "gpt-4.1-mini"


class IssueSolver:
    def __init__(self, runner: NativeRunner, repo_path: Path):
        from openai import OpenAI
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.runner = runner
        self.repo_path = repo_path
        self.model = PRIMARY_MODEL

    def _create(self, messages: list[dict]):
        try:
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOLS,
            )
        except Exception as e:
            if self.model != PRIMARY_MODEL:
                raise
            logger.warning(f"{self.model} unavailable ({e}); falling back to {FALLBACK_MODEL}")
            self.model = FALLBACK_MODEL
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOLS,
            )

    def solve(
        self,
        issue_title: str,
        issue_body: str,
        available_tools: list[str] | None = None,
    ) -> str:
        tools_section = ""
        if available_tools:
            tools_section = (
                f"\n\nVerification binaries available on PATH: {', '.join(available_tools)}."
            )

        user_message = (
            f"# Issue: {issue_title}\n\n"
            f"{issue_body}{tools_section}\n\n"
            "Please fix this issue. Start by exploring the repository structure."
        )

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        iterations = 0

        while iterations < config.MAX_SOLVER_ITERATIONS:
            iterations += 1
            logger.info(f"Solver iteration {iterations} (model={self.model})")

            response = self._create(messages)
            message = response.choices[0].message

            assistant_message = {
                "role": "assistant",
            }

            if message.content:
                assistant_message["content"] = message.content
            else:
                assistant_message["content"] = ""

            if message.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ]

            messages.append(assistant_message)

            if not message.tool_calls:
                return message.content or "Fix completed."

            for tool_call in message.tool_calls:
                logger.info(
                    f"Tool: {tool_call.function.name}({tool_call.function.arguments[:120]})"
                )

                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as e:
                    result = f"ERROR: Invalid JSON tool arguments: {e}"
                else:
                    if tool_call.function.name == "finish":
                        summary = tool_input.get("summary", "Issue fixed.")
                        logger.info(f"Solver finished: {summary}")
                        return summary

                    result = self._dispatch(tool_call.function.name, tool_input)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result[:8000],
                    }
                )

        raise RuntimeError(
            f"Solver hit max iterations ({config.MAX_SOLVER_ITERATIONS}) without finishing"
        )

    def _dispatch(self, name: str, inp: dict) -> str:
        if name == "read_file":
            return self._read(inp["path"])
        if name == "write_file":
            return self._write(inp["path"], inp["content"])
        if name == "list_files":
            return self._list(inp.get("path", "."))
        if name == "search_code":
            return self._search(inp["pattern"], inp.get("path", "."))
        if name == "run_command":
            return self._run(inp["command"])
        return f"Unknown tool: {name}"

    def _read(self, rel_path: str) -> str:
        try:
            return (self.repo_path / rel_path).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return f"ERROR: File not found: {rel_path}"
        except Exception as e:
            return f"ERROR reading {rel_path}: {e}"

    def _write(self, rel_path: str, content: str) -> str:
        try:
            p = self.repo_path / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Written: {rel_path}"
        except Exception as e:
            return f"ERROR writing {rel_path}: {e}"

    def _list(self, rel_path: str) -> str:
        p = self.repo_path / rel_path
        if not p.exists():
            return f"ERROR: Path does not exist: {rel_path}"
        entries = [f"{item.name}{'/' if item.is_dir() else ''}" for item in sorted(p.iterdir())]
        return "\n".join(entries) if entries else "(empty)"

    def _search(self, pattern: str, path: str) -> str:
        r = self.runner.exec(
            f"grep -rn -e {shlex.quote(pattern)} {shlex.quote(path)} 2>/dev/null | head -60"
        )
        return r["stdout"].strip() or f"No matches for '{pattern}' in {path}"

    def _run(self, command: str) -> str:
        r = self.runner.exec(command)
        parts = []
        if r["stdout"].strip():
            parts.append(r["stdout"].strip())
        if r["stderr"].strip():
            parts.append(f"[stderr]\n{r['stderr'].strip()}")
        parts.append(f"[exit code: {r['exit_code']}]")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Gemini solver
# ---------------------------------------------------------------------------

# Map OpenAI tool schema → Gemini FunctionDeclaration dicts
def _to_gemini_tools() -> list[dict]:
    from google.genai import types as gtypes  # noqa: F401 — validate import at call time
    declarations = []
    for t in TOOLS:
        fn = t["function"]
        params = fn.get("parameters", {})
        declarations.append({
            "name": fn["name"],
            "description": fn["description"],
            "parameters": {
                "type": params.get("type", "object"),
                "properties": params.get("properties", {}),
                "required": params.get("required", []),
            },
        })
    return [{"function_declarations": declarations}]


class GeminiSolver:
    def __init__(self, runner: NativeRunner, repo_path: Path):
        from google import genai
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.runner = runner
        self.repo_path = repo_path
        logger.info("Using Gemini provider")
        logger.info(f"Gemini model: {config.GEMINI_MODEL}")

    def _generate(self, contents, gemini_tools, gtypes):
        """Call generate_content with up to 3 retries on 429 RESOURCE_EXHAUSTED."""
        max_retries = 3
        default_wait = 60
        for attempt in range(1, max_retries + 2):  # attempts 1..4, retries 1..3
            try:
                return self.client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=contents,
                    config=gtypes.GenerateContentConfig(tools=gemini_tools),
                )
            except Exception as e:
                err_str = str(e)
                is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                if not is_rate_limit or attempt > max_retries:
                    logger.error(f"Gemini API error: {e}")
                    raise
                # Try to extract retryDelay from the error message, e.g. "retryDelay: '60s'"
                wait = default_wait
                m = re.search(r"retryDelay[^']*'(\d+)s'", err_str)
                if m:
                    wait = int(m.group(1))
                logger.warning(
                    f"Gemini rate limit hit, waiting {wait}s before retry "
                    f"(attempt {attempt}/{max_retries})..."
                )
                time.sleep(wait)

    def solve(
        self,
        issue_title: str,
        issue_body: str,
        available_tools: list[str] | None = None,
    ) -> str:
        from google.genai import types as gtypes

        tools_section = ""
        if available_tools:
            tools_section = (
                f"\n\nVerification binaries available on PATH: {', '.join(available_tools)}."
            )

        user_message = (
            f"# Issue: {issue_title}\n\n"
            f"{issue_body}{tools_section}\n\n"
            "Please fix this issue. Start by exploring the repository structure."
        )

        gemini_tools = _to_gemini_tools()
        # contents is a list of gtypes.Content objects — the SDK owns the types,
        # never use raw dicts or private attributes like p._raw.
        contents: list[gtypes.Content] = [
            gtypes.Content(
                role="user",
                parts=[gtypes.Part(text=SYSTEM_PROMPT + "\n\n" + user_message)],
            )
        ]

        iterations = 0
        while iterations < config.MAX_SOLVER_ITERATIONS:
            iterations += 1
            logger.info(f"Solver iteration {iterations} (model={config.GEMINI_MODEL})")

            try:
                response = self._generate(contents, gemini_tools, gtypes)
            except Exception as e:
                logger.error(f"Gemini API error: {e}")
                raise

            logger.info("Gemini response received successfully")

            # Append the model turn using the Content object the SDK returned —
            # this is the only safe way to carry it forward without touching internals.
            contents.append(response.candidates[0].content)

            # Collect function calls from this turn
            fn_calls = [
                p.function_call
                for p in response.candidates[0].content.parts
                if p.function_call is not None
            ]

            if not fn_calls:
                # Text-only response — response.text is the canonical accessor.
                text = response.text
                logger.info("Gemini response received successfully")
                return text or "Fix completed."

            # Execute each tool call and feed results back as a user turn.
            tool_parts: list[gtypes.Part] = []
            for fc in fn_calls:
                name = fc.name
                args = dict(fc.args)
                logger.info(f"Tool: {name}({str(args)[:120]})")

                if name == "finish":
                    summary = args.get("summary", "Issue fixed.")
                    logger.info(f"Solver finished: {summary}")
                    return summary

                result = self._dispatch(name, args)
                tool_parts.append(
                    gtypes.Part(
                        function_response=gtypes.FunctionResponse(
                            name=name,
                            response={"output": result[:8000]},
                        )
                    )
                )

            contents.append(gtypes.Content(role="user", parts=tool_parts))

        raise RuntimeError(
            f"Solver hit max iterations ({config.MAX_SOLVER_ITERATIONS}) without finishing"
        )

    def _dispatch(self, name: str, inp: dict) -> str:
        if name == "read_file":
            return self._read(inp["path"])
        if name == "write_file":
            return self._write(inp["path"], inp["content"])
        if name == "list_files":
            return self._list(inp.get("path", "."))
        if name == "search_code":
            return self._search(inp["pattern"], inp.get("path", "."))
        if name == "run_command":
            return self._run(inp["command"])
        return f"Unknown tool: {name}"

    def _read(self, rel_path: str) -> str:
        try:
            return (self.repo_path / rel_path).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return f"ERROR: File not found: {rel_path}"
        except Exception as e:
            return f"ERROR reading {rel_path}: {e}"

    def _write(self, rel_path: str, content: str) -> str:
        try:
            p = self.repo_path / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Written: {rel_path}"
        except Exception as e:
            return f"ERROR writing {rel_path}: {e}"

    def _list(self, rel_path: str) -> str:
        p = self.repo_path / rel_path
        if not p.exists():
            return f"ERROR: Path does not exist: {rel_path}"
        entries = [f"{item.name}{'/' if item.is_dir() else ''}" for item in sorted(p.iterdir())]
        return "\n".join(entries) if entries else "(empty)"

    def _search(self, pattern: str, path: str) -> str:
        r = self.runner.exec(
            f"grep -rn -e {shlex.quote(pattern)} {shlex.quote(path)} 2>/dev/null | head -60"
        )
        return r["stdout"].strip() or f"No matches for '{pattern}' in {path}"

    def _run(self, command: str) -> str:
        r = self.runner.exec(command)
        parts = []
        if r["stdout"].strip():
            parts.append(r["stdout"].strip())
        if r["stderr"].strip():
            parts.append(f"[stderr]\n{r['stderr'].strip()}")
        parts.append(f"[exit code: {r['exit_code']}]")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_solver(runner: NativeRunner, repo_path: Path) -> "IssueSolver | GeminiSolver":
    provider = config.AI_PROVIDER
    if provider == "gemini":
        return GeminiSolver(runner, repo_path)
    if provider == "openai":
        return IssueSolver(runner, repo_path)
    raise ValueError(f"Unsupported AI_PROVIDER: '{provider}'")
