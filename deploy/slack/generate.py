#!/usr/bin/env python3
"""Generate a ready-to-paste Slack app manifest per persona from _template.yaml.

Reads persona names/roles from crew.yaml so the manifests stay in sync. Run:
    .venv/bin/python deploy/slack/generate.py
Produces deploy/slack/<persona>.yaml for every persona (enabled or not).
"""

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

NOTE = (
    "# Paste this at api.slack.com/apps -> Create New App -> \"From a manifest\"\n"
    "# (pick the loquina workspace). After creating, you still need two tokens:\n"
    "#   1. Install App -> Install to workspace        -> Bot token (xoxb-)\n"
    "#   2. Basic Information -> App-Level Tokens -> Generate (connections:write)\n"
    "#                                                 -> App token (xapp-)\n"
    "# Put both in crew/.env. See deploy/slack/_template.yaml for details.\n"
)


def main() -> None:
    template = (HERE / "_template.yaml").read_text()
    # Keep only the manifest body (drop the template's instructional comment header).
    body = template[template.index("display_information:") :]

    personas = yaml.safe_load((ROOT / "crew.yaml").read_text())["personas"]
    for name, entry in personas.items():
        entry = entry or {}
        display = entry.get("display_name", name.title())
        role = entry.get("role", "")
        desc = f"{display} — {role} on the Loquina crew" if role else f"{display} on the Loquina crew"

        out = (
            body.replace("name: <NAME>", f"name: {display}")
            .replace("display_name: <NAME>", f"display_name: {display}")
            .replace("description: <NAME> on the Loquina crew", f"description: {desc}")
        )
        (HERE / f"{name}.yaml").write_text(NOTE + "\n" + out)
        print(f"wrote {name}.yaml ({display} — {role or 'no role'})")


if __name__ == "__main__":
    main()
