#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

ENV_OFFSETS = {
    "aaron": 1000,
    "agamya": 1100,
    "naveen": 1200,
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def service_port(service_text: str) -> int:
    match = re.search(r"--port\s+(\d+)", service_text)
    if not match:
        raise ValueError("service is missing an ExecStart --port value")
    return int(match.group(1))


def render_systemd_units(systemd_dir: Path, output_dir: Path) -> None:
    for service_path in sorted(systemd_dir.glob("*-agent.service")):
        base_text = read(service_path)
        base_port = service_port(base_text)
        service_name = service_path.name.removesuffix(".service")

        for env_name, offset in ENV_OFFSETS.items():
            port = base_port + offset
            text = base_text.replace("/home/ubuntu/app", f"/home/ubuntu/app-{env_name}")
            text = re.sub(r"--port\s+\d+", f"--port {port}", text)

            if "Environment=PORT=" in text:
                text = re.sub(r"Environment=PORT=\d+", f"Environment=PORT={port}", text)
            else:
                text = text.replace(
                    "Environment=PYTHONUNBUFFERED=1\n",
                    f"Environment=PYTHONUNBUFFERED=1\nEnvironment=PORT={port}\n",
                )

            text = text.replace(
                f"Description={service_name.replace('-', ' ').title()} FastAPI Server",
                f"Description={env_name.title()} {service_name.replace('-', ' ').title()} FastAPI Server",
            )

            write(output_dir / f"{env_name}-{service_path.name}", text)


def upstream_ports(nginx_text: str) -> dict[str, int]:
    ports: dict[str, int] = {}
    for match in re.finditer(
        r"upstream\s+([a-z0-9_]+)\s*\{\s*server\s+127\.0\.0\.1:(\d+);",
        nginx_text,
        re.MULTILINE,
    ):
        ports[match.group(1)] = int(match.group(2))
    return ports


def location_blocks(nginx_text: str) -> list[tuple[str, str]]:
    return [
        (match.group("selector").strip(), match.group("body"))
        for match in re.finditer(
            r"\n\s{4}location\s+(?P<selector>[^{]+)\{\n(?P<body>.*?)\n\s{4}\}",
            nginx_text,
            re.DOTALL,
        )
    ]


def prefixed_selector(selector: str, env_name: str) -> str:
    if selector.startswith("="):
        return selector.replace("= /", f"= /api/{env_name}/", 1)
    if selector.startswith("~"):
        raise ValueError(f"regex locations are not supported: {selector}")
    return selector.replace("/", f"/api/{env_name}/", 1)


def render_dev_location(
    selector: str,
    body: str,
    env_name: str,
    offset: int,
    ports: dict[str, int],
) -> str | None:
    proxy_match = re.search(r"proxy_pass\s+http://([a-z0-9_]+)([^;]*);", body)
    if not proxy_match:
        return None

    upstream_name = proxy_match.group(1)
    if upstream_name not in ports:
        return None

    port = ports[upstream_name] + offset
    suffix = proxy_match.group(2)
    dev_body = body.replace(
        proxy_match.group(0),
        f"proxy_pass http://127.0.0.1:{port}{suffix};",
        1,
    )
    dev_body = re.sub(r"rewrite\s+\^/", f"rewrite ^/api/{env_name}/", dev_body)
    return f"    location {prefixed_selector(selector, env_name)} {{\n{dev_body}\n    }}"


def render_nginx(source_path: Path, output_path: Path) -> None:
    nginx_text = read(source_path)
    ports = upstream_ports(nginx_text)
    blocks = location_blocks(nginx_text)
    rendered: list[str] = [
        "    # Multi-developer preview routes. Generated from production locations.",
        "    # Branch prefixes in the frontend route to /api/{developer}/...",
    ]

    for env_name, offset in ENV_OFFSETS.items():
        rendered.append("")
        rendered.append(f"    # {env_name.title()} agent environment")
        for selector, body in blocks:
            location = render_dev_location(selector, body, env_name, offset, ports)
            if location:
                rendered.append(location)

    insert_at = nginx_text.rfind("\n}")
    if insert_at == -1:
        raise ValueError("could not find final server block closing brace")

    combined = f"{nginx_text[:insert_at]}\n\n{chr(10).join(rendered)}{nginx_text[insert_at:]}\n"
    write(output_path, combined)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("/home/ubuntu/app"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/ei-everyone-multi-dev"))
    args = parser.parse_args()

    render_systemd_units(args.repo / "systemd", args.output / "systemd")
    render_nginx(
        args.repo / "nginx" / "sites-available" / "agents",
        args.output / "nginx" / "agents",
    )


if __name__ == "__main__":
    main()
