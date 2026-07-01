import json
import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from rag.retriever import retrieve

load_dotenv()


def _list_repo_files(repo_path: Path) -> list[str]:
    return [str(path.relative_to(repo_path)) for path in repo_path.rglob("*") if path.is_file()]


def discover_services(repo_path: Path) -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []
    root_files = _list_repo_files(repo_path)
    lower_files = {f.lower(): f for f in root_files}

    if any(name in lower_files for name in ["package.json", "pyproject.toml", "requirements.txt", "go.mod"]):
        service = _detect_service(repo_path)
        services.append(service)

    for path in sorted(repo_path.rglob("*")):
        if not path.is_dir():
            continue
        if path == repo_path:
            continue

        files = [p.name.lower() for p in path.iterdir() if p.is_file()]
        if any(name in files for name in ["package.json", "pyproject.toml", "requirements.txt", "go.mod", "dockerfile"]):
            service = _detect_service(path)
            service["path"] = str(path.relative_to(repo_path))
            if not any(existing.get("path") == service["path"] for existing in services):
                services.append(service)

    if not services:
        services.append(_detect_service(repo_path))

    return services


def _detect_service(repo_path: Path) -> dict[str, Any]:
    files = _list_repo_files(repo_path)
    lower_files = {f.lower(): f for f in files}
    default_name = repo_path.name if repo_path.name not in {"", "."} else "app"

    if "package.json" in lower_files:
        package_json_path = repo_path / lower_files["package.json"]
        data: dict[str, Any] = {}
        try:
            data = json.loads(package_json_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
        start_script = scripts.get("start") if isinstance(scripts, dict) else None
        main_entry = data.get("main") if isinstance(data, dict) else None
        entrypoint = None
        for candidate in ["server.js", "app.js", "index.js", "main.js", "dist/server.js", "dist/app.js"]:
            if candidate in lower_files:
                entrypoint = candidate
                break
        if not entrypoint and isinstance(main_entry, str):
            entrypoint = main_entry
        if not entrypoint:
            entrypoint = "node ."
        return {
            "type": "node",
            "entrypoint": entrypoint,
            "name": data.get("name") or default_name,
            "start_script": start_script,
        }

    if "pyproject.toml" in lower_files:
        pyproject_path = repo_path / lower_files["pyproject.toml"]
        try:
            data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        project = data.get("project", {}) if isinstance(data, dict) else {}
        return {
            "type": "python",
            "entrypoint": "app.py",
            "name": project.get("name") or default_name,
        }

    if "requirements.txt" in lower_files:
        return {"type": "python", "entrypoint": "app.py", "name": default_name}

    if "go.mod" in lower_files:
        return {"type": "go", "entrypoint": "main.go", "name": default_name}

    return {"type": "generic", "entrypoint": "app", "name": default_name}


def _generate_fallback_dockerfile(repo_path: Path) -> str:
    service = _detect_service(repo_path)
    service_type = service.get("type")
    entrypoint = service.get("entrypoint")
    app_name = service.get("name")

    if service_type == "node":
        cmd = "npm start" if app_name and service.get("start_script") else entrypoint
        if cmd == "npm start":
            cmd_line = "CMD [\"npm\", \"start\"]"
        elif cmd == "node .":
            cmd_line = "CMD [\"node\", \".\"]"
        else:
            cmd_line = f"CMD [\"/app/{cmd}\"]"

        return f"""FROM node:20-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
RUN if [ -f package-lock.json ]; then \
      npm ci --omit=dev --ignore-scripts; \
    else \
      npm install --omit=dev --ignore-scripts; \
    fi && npm cache clean --force && mkdir -p node_modules
COPY . .
RUN npm prune --omit=dev

FROM gcr.io/distroless/nodejs20-debian12:nonroot
WORKDIR /app
COPY --from=build /app/package*.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app .
ENV NODE_ENV=production
USER 65532:65532
EXPOSE 3000
{cmd_line}
"""

    if service_type == "python":
        return f"""FROM python:3.12-slim AS build
WORKDIR /app
COPY requirements.txt* ./
RUN python -m pip install --upgrade pip && python -m pip install --no-cache-dir -r requirements.txt
COPY . .

FROM gcr.io/distroless/python3-debian12:nonroot
WORKDIR /app
COPY --from=build /app /app
COPY --chown=65532:65532 . /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
USER 65532:65532
EXPOSE 8000
CMD ["/app/{entrypoint}"]
"""

    if service_type == "go":
        return f"""FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o /out/app .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/app /app
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/app"]
"""

    return f"""FROM debian:bookworm-slim AS build
WORKDIR /app
COPY . .

FROM gcr.io/distroless/base-debian12:nonroot
COPY --from=build /app /app
WORKDIR /app
USER 65532:65532
ENTRYPOINT ["/app/{entrypoint}"]
"""


def _repo_summary(repo_path: Path) -> str:
    files = _list_repo_files(repo_path)
    service = _detect_service(repo_path)
    summary_lines = [
        f"Detected service type: {service['type']}",
        f"Entrypoint: {service['entrypoint']}",
        f"Service name: {service['name']}",
        "Important files:",
    ]
    for file in sorted(files):
        if any(file.endswith(ext) for ext in ["package.json", "pyproject.toml", "requirements.txt", "go.mod", "Dockerfile"]):
            summary_lines.append(f"- {file}")
    summary_lines.append(f"Total files: {len(files)}")
    return "\n".join(summary_lines)


def _build_search_query(repo_path: Path) -> str:
    service = _detect_service(repo_path)
    base = "secure production Dockerfile best practices for"
    if service["type"] == "node":
        return f"{base} Node.js application with distroless runtime, non-root user, multi-stage build, and hardened runtime defaults"
    if service["type"] == "python":
        return f"{base} Python application with distroless runtime, non-root user, multi-stage build, and hardened runtime defaults"
    if service["type"] == "go":
        return f"{base} Go application with static binary build, distroless runtime, and hardened runtime defaults"
    return f"{base} generic application with security, multi-stage build, minimal runtime, and least-privilege principles"


def _service_display_name(service: dict[str, Any], fallback: str) -> str:
    raw_name = service.get("name") or fallback
    sanitized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(raw_name))
    sanitized = sanitized.strip("-")
    return sanitized or fallback


def _build_default_compose_content(repo_path: Path, service_name: str, services: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for service in services:
        svc_name = _service_display_name(service, service_name)
        path = service.get("path") or "."
        sections.append(f"""  {svc_name}:
    image: {svc_name}:latest
    build:
      context: {path}
      dockerfile: Dockerfile
    restart: unless-stopped
    user: \"65532:65532\"
    init: true
    security_opt:
      - no-new-privileges:true
    read_only: true
    cap_drop:
      - ALL
    pids_limit: 256
    healthcheck:
      test: [\"CMD\", \"sh\", \"-c\", \"exit 0\"]
      interval: 30s
      timeout: 5s
      retries: 3
    mem_limit: 512m
    cpus: 0.5
    environment:
      NODE_ENV: production
      PYTHONUNBUFFERED: 1
    volumes:
      - {svc_name}-data:/data
    networks:
      - app-net
""")

    return "services:\n" + "\n".join(sections) + "\nnetworks:\n  app-net:\n    driver: bridge\nvolumes:\n" + "\n".join(
        f"  { _service_display_name(service, service_name) }-data:" for service in services
    ) + "\n"


def _extract_tool_query(text: str, tool_name: str) -> str | None:
    lowered = text.lower()
    if tool_name.lower() not in lowered:
        return None
    import re

    match = re.search(r'"query"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1)
    match = re.search(r"'query'\s*:\s*'([^']+)'", text)
    if match:
        return match.group(1)
    match = re.search(r"query\s*[:=]\s*([^\"\s].*)", text)
    if match:
        return match.group(1).strip().strip('"').strip("'")
    return None


def generate_dockerfile(repo_path: str) -> str:
    repo = Path(repo_path)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _generate_fallback_dockerfile(repo)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        repo_summary = _repo_summary(repo)
        default_query = _build_search_query(repo)

        system_prompt = (
            "Tum Dockerfile generator agent ho. Tumhare paas search_docker_knowledge tool hai. "
            "Iska use karo jab bhi security, multi-stage build, ya base-image decisions lene hon. "
            "Dockerfile likhne se pehle kam se kam ek baar zaroor search karo."
        )
        user_task = (
            "Nimnalikhit repository ka analysis karo aur ek production-grade, distroless, "
            "non-root Dockerfile banao. Agar zarurat ho to additional best-practice knowledge retrieve karo.\n"
            "Repository summary:\n"
            f"{repo_summary}\n"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_task},
        ]

        tool_used = False
        for iteration in range(5):
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1200,
                temperature=0,
                messages=messages,
            )

            text = response.content[0].text
            tool_query = _extract_tool_query(text, "search_docker_knowledge")
            if tool_query:
                print(f"Searching: '{tool_query}'...")
                context = retrieve(tool_query, "docker_knowledge", top_k=5)
                tool_used = True
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": "Retrieved context:\n" + "\n\n".join(context)})
                continue

            if not tool_used:
                print(f"Searching: '{default_query}'...")
                context = retrieve(default_query, "docker_knowledge", top_k=5)
                tool_used = True
                messages.append({"role": "assistant", "content": "Retrieving best-practice knowledge before generating."})
                messages.append({"role": "user", "content": "Retrieved context:\n" + "\n\n".join(context)})
                continue

            if "```" in text:
                text = text.split("```", 2)[1]
                if text.startswith("dockerfile") or text.startswith("Dockerfile"):
                    text = text.split("\n", 1)[1]
            return text.strip()

        return _generate_fallback_dockerfile(repo)
    except Exception:
        return _generate_fallback_dockerfile(repo)


def generate_compose(repo_path: str, service_name: str) -> str:
    repo = Path(repo_path)
    services = discover_services(repo)
    files = sorted(p.name for p in repo.iterdir() if p.is_file())

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _build_default_compose_content(repo, service_name, services)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        repo_summary = _repo_summary(repo)
        default_query = _build_compose_search_query(repo)

        system_prompt = (
            "Tum docker-compose generator agent ho. Tumhare paas search_compose_knowledge tool hai. "
            "Iska use karo jab bhi health checks, resource limits, volumes, ya network isolation ke baare mein additional guidance chahiye. "
            "Compose file likhne se pehle kam se kam ek baar zaroor search karo."
        )
        user_task = (
            "Nimnalikhit repository ka analysis karo aur ek production-grade docker-compose.yml banao. "
            "Aapko service ke liye healthcheck, resource limits, named volume, aur network isolation sahi se configure karna hai.\n"
            "Repository summary:\n"
            f"{repo_summary}\n"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_task},
        ]

        tool_used = False
        for iteration in range(5):
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1200,
                temperature=0,
                messages=messages,
            )

            text = response.content[0].text
            tool_query = _extract_tool_query(text, "search_compose_knowledge")
            if tool_query:
                print(f"Searching: '{tool_query}'...")
                context = retrieve(tool_query, "compose", top_k=5)
                tool_used = True
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": "Retrieved context:\n" + "\n\n".join(context)})
                continue

            if not tool_used:
                print(f"Searching: '{default_query}'...")
                context = retrieve(default_query, "compose", top_k=5)
                tool_used = True
                messages.append({"role": "assistant", "content": "Retrieving best-practice compose knowledge before generating."})
                messages.append({"role": "user", "content": "Retrieved context:\n" + "\n\n".join(context)})
                continue

            if "```" in text:
                text = text.split("```", 2)[1]
                if text.startswith("yaml") or text.startswith("yml"):
                    text = text.split("\n", 1)[1]
            return text.strip()
    except Exception:
        return _build_default_compose_content(repo, service_name, services)
