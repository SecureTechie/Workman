import logging
import shutil
import subprocess
from pathlib import Path

import docker
from docker.models.containers import Container

logger = logging.getLogger(__name__)

# Maps detected language to Docker image
LANG_IMAGES = {
    "python": "python:3.11-slim",
    "node": "node:20-slim",
    "rust": "rust:1.75-slim",
    "go": "golang:1.21-bookworm",
    "java": "eclipse-temurin:21-jdk-jammy",
    "ruby": "ruby:3.2-slim",
    "php": "php:8.2-cli",
    "default": "ubuntu:22.04",
}

LANG_SETUP_CMDS: dict[str, list[str]] = {
    "python": [
        "apt-get update -qq && apt-get install -y git curl -qq",
        "pip install --upgrade pip -q",
        "[ -f requirements.txt ] && pip install -r requirements.txt -q || true",
        "[ -f pyproject.toml ] && pip install -e . -q || true",
        "[ -f setup.py ] && pip install -e . -q || true",
    ],
    "node": [
        "apt-get update -qq && apt-get install -y git -qq",
        "[ -f package.json ] && npm install --silent || true",
    ],
    "rust": [
        "apt-get update -qq && apt-get install -y git -qq",
    ],
    "go": [
        "apt-get update -qq && apt-get install -y git -qq",
        "[ -f go.mod ] && go mod download || true",
    ],
    "default": [
        "apt-get update -qq && apt-get install -y git curl -qq",
    ],
}


def detect_language(repo_path: Path) -> str:
    indicators = {
        "python": ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"],
        "node": ["package.json"],
        "rust": ["Cargo.toml"],
        "go": ["go.mod"],
        "java": ["pom.xml", "build.gradle"],
        "ruby": ["Gemfile"],
        "php": ["composer.json"],
    }
    for lang, files in indicators.items():
        if any((repo_path / f).exists() for f in files):
            return lang
    return "default"


class DockerManager:
    def __init__(self):
        self.client = docker.from_env()
        self._containers: list[Container] = []

    def create_workspace(self, repo_path: Path, language: str) -> Container:
        image = LANG_IMAGES.get(language, LANG_IMAGES["default"])
        logger.info(f"Pulling image {image}...")

        try:
            self.client.images.pull(image)
        except Exception as e:
            logger.warning(f"Could not pull {image}: {e} — using cached if available")

        container = self.client.containers.run(
            image,
            command="sleep infinity",
            detach=True,
            remove=False,
            volumes={
                str(repo_path.resolve()): {"bind": "/workspace", "mode": "rw"}
            },
            working_dir="/workspace",
            environment={
                "DEBIAN_FRONTEND": "noninteractive",
                "CI": "true",
            },
        )
        self._containers.append(container)
        logger.info(f"Container started: {container.short_id}")
        return container

    def setup_environment(self, container: Container, language: str) -> None:
        cmds = LANG_SETUP_CMDS.get(language, LANG_SETUP_CMDS["default"])
        for cmd in cmds:
            logger.debug(f"Setup: {cmd}")
            result = self.exec(container, cmd)
            if result["exit_code"] != 0:
                logger.warning(f"Setup command returned {result['exit_code']}: {result['stderr'][:200]}")

    def exec(self, container: Container, command: str, workdir: str = "/workspace") -> dict:
        try:
            exit_code, output = container.exec_run(
                cmd=["bash", "-c", command],
                workdir=workdir,
                demux=True,
            )
            stdout_bytes, stderr_bytes = output if output else (b"", b"")
            return {
                "exit_code": exit_code,
                "stdout": (stdout_bytes or b"").decode("utf-8", errors="replace"),
                "stderr": (stderr_bytes or b"").decode("utf-8", errors="replace"),
            }
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e)}

    def cleanup(self, container: Container) -> None:
        try:
            container.stop(timeout=5)
            container.remove(force=True)
            logger.info(f"Container {container.short_id} removed")
        except Exception as e:
            logger.warning(f"Error cleaning up container: {e}")

    def cleanup_all(self) -> None:
        for c in self._containers:
            self.cleanup(c)
        self._containers.clear()


def clone_repo(clone_url: str, dest: Path, branch: str | None = None) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "clone", "--depth=1", clone_url, str(dest)]
    logger.info(f"Cloning repo to {dest}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr}")

    if branch:
        # Create and checkout new branch
        result = subprocess.run(
            ["git", "checkout", "-b", branch],
            capture_output=True,
            text=True,
            cwd=str(dest),
        )
        if result.returncode != 0:
            raise RuntimeError(f"git checkout -b failed: {result.stderr}")

    logger.info("Clone complete")


def push_and_commit(repo_path: Path, branch: str, commit_message: str) -> None:
    cmds = [
        ["git", "config", "user.email", "workman@bot.local"],
        ["git", "config", "user.name", "Workman Bot"],
        ["git", "add", "-A"],
        ["git", "commit", "-m", commit_message],
        ["git", "push", "origin", branch],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_path))
        if result.returncode != 0:
            # "nothing to commit" is fine
            if "nothing to commit" in result.stdout + result.stderr:
                logger.info("Nothing new to commit")
                continue
            raise RuntimeError(f"{' '.join(cmd)} failed: {result.stderr}")
    logger.info(f"Pushed branch {branch}")
