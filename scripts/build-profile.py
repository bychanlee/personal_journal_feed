#!/usr/bin/env python3
"""Build personal_journal_feed config.yaml profile from LANDFALL values + graph + filters.

Reads from the LANDFALL Obsidian vault folder:
- values.md frontmatter (domains with weights and feed_keywords)
- graph/projects/*.md frontmatter (topics, status)
- graph/methods/*.md frontmatter (topics, status)
- filters.md frontmatter (venue_out, skill_gap_cap_3)

Rewrites only the `profile:` block of config.yaml. Other blocks (feeds, output) preserved.
"""
from pathlib import Path
import sys
import yaml


LANDFALL = Path.home() / "Documents/Obsidian Vault/4_Temporal/Active/Project LANDFALL"
CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"

ACTIVE_PROJECT_STATUSES = {"active", "seed", "submitted", "emerging"}
HAVE_METHOD_STATUSES = {"have", "learning"}

BACKGROUND_TOPICS = [
    "electron-phonon coupling methodology (general EPW developments)",
    "GW / BSE methodology developments",
    "DFT / DFPT methodology advances",
    "Wannier function methods and downfolding",
    "TCAD methodology and tools (Sentaurus, DEVSIM, Victory Process/Device)",
    "semiconductor device scaling trends (GAA / CFET / nanosheet, DTCO, BSPDN)",
    "thermal transport in nanoscale channels (phonon BTE, self-heating)",
    "phonon thermal conductivity in 2D materials",
    "quantum confinement effects in ultrathin channels",
    "first-principles defect levels in wide-bandgap semiconductors",
    "exciton physics in conventional 3D semiconductors",
    "high-throughput first-principles screening (DFT/GW based)",
    "AI for science (general, methods papers)",
    "autonomous experimentation, self-driving labs",
    "HPC / AI infrastructure industry trends (GPU, interconnect, exascale systems)",
]

METHODS = [
    "BerkeleyGW",
    "EPW",
    "GWPT",
    "Quantum ESPRESSO",
    "VASP",
    "Wannier90",
    "DFPT",
    "SIESTA / TranSIESTA",
    "DEVSIM",
    "Sentaurus TCAD",
    "BoltzTraP / BoltzWann",
]


def parse_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter from a markdown file."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        end = text.find("\n---", 4)
        if end == -1:
            return {}
    return yaml.safe_load(text[4:end]) or {}


def collect_domains():
    fm = parse_frontmatter(LANDFALL / "values.md")
    return fm.get("domains", [])


def collect_projects():
    projects = []
    for f in sorted((LANDFALL / "graph/projects").glob("*.md")):
        fm = parse_frontmatter(f)
        if fm.get("status") in ACTIVE_PROJECT_STATUSES:
            projects.append({"slug": f.stem, **fm})
    return projects


def collect_methods():
    methods = []
    for f in sorted((LANDFALL / "graph/methods").glob("*.md")):
        fm = parse_frontmatter(f)
        if fm.get("status") in HAVE_METHOD_STATUSES:
            methods.append({"slug": f.stem, **fm})
    return methods


def collect_filters():
    fm = parse_frontmatter(LANDFALL / "filters.md")
    return fm.get("venue_out", []), fm.get("skill_gap_cap_3", [])


def build_high_priority_topics(domains, projects, methods):
    topics = []

    # Values axis first
    for d in domains:
        name = d.get("display") or d.get("name")
        weight = d.get("weight")
        for kw in d.get("feed_keywords", []):
            topics.append(f"[values:{name} w={weight}] {kw}")

    # Active projects
    for p in projects:
        for kw in p.get("topics", []):
            topics.append(f"[{p['slug']}:{p.get('status')}] {kw}")

    # Methods (have)
    for m in methods:
        for kw in m.get("topics", []):
            topics.append(f"[method:{m['slug']}:{m.get('status')}] {kw}")

    return topics


def build_profile_text(domains, projects, venue_out, skill_gap):
    values_lines = [
        f"- {d.get('display') or d.get('name')} (weight {d.get('weight')}, {d.get('contribution')})"
        for d in domains
    ]

    project_lines = [
        f"- {p['slug']} [{p.get('status')}] — venue: {p.get('target_venue', '-')}"
        for p in projects
    ]

    venue_lines = "\n".join(f"    - {x}" for x in venue_out)
    skill_lines = "\n".join(f"    - {x}" for x in skill_gap)

    return f"""Condensed matter physicist trained in first-principles many-body perturbation theory
(GW/BSE, EPW electron-phonon, GWPT). Currently visiting BerkeleyGW main developer group
(Zhenglu Li, USC, 2026.04~09).

Project LANDFALL — no fixed research tracks. Evaluation axes:

(1) Values (intrinsic motivation, weighted):
{chr(10).join(values_lines)}

(2) Active graph (current projects and methods):
{chr(10).join(project_lines)}

(3) Invariant skill stack: MBPT physics (GW/BSE/EPW/GWPT, DFPT, Wannier) + HPC exascale
    + AI workflow automation. Tool/code is free — constraint is venue.

(4) Venue constraint: physics-adjacent journals up to ACS Nano (inclusive).
    PRB, PRL, PRM, PR Applied, Nat Phys, Nat Mater, Nat Nano, Nat Commun,
    npj Comput Mater, APL, ACS Nano, Nano Lett., 2D Mater., Comp Phys Comm, J Chem Theory Comput — in.

Score papers high (4-5) if they connect to any of:
(a) MBPT methodology advance (GW/BSE/EPW/GWPT)
(b) Active graph materials (Bi2O2Se / Ag3SI / TMD heterostructure) or project topics
(c) Values axis (energy absorber materials, aerospace-adjacent physics)

OUT OF VENUE — cap at 1:
{venue_lines}

SKILL GAP — cap at 3 unless paper has direct MBPT-stack connection OR active-graph connection
OR values (energy/aerospace) connection:
{skill_lines}
"""


def build_profile_dict():
    domains = collect_domains()
    projects = collect_projects()
    methods = collect_methods()
    venue_out, skill_gap = collect_filters()

    return {
        "profile": {
            "text": build_profile_text(domains, projects, venue_out, skill_gap),
            "high_priority_topics": build_high_priority_topics(domains, projects, methods),
            "background_topics": BACKGROUND_TOPICS,
            "methods": METHODS,
        }
    }


def replace_profile_block(config_text: str, new_profile_yaml: str) -> str:
    """Replace only the `profile:` top-level block; preserve everything else."""
    lines = config_text.splitlines(keepends=True)
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if start is None:
            if stripped.startswith("profile:"):
                start = i
        else:
            if stripped and stripped[0].isalpha() and ":" in stripped and not stripped.startswith(" "):
                end = i
                break
    if start is None:
        raise RuntimeError("profile: block not found in config.yaml")

    return "".join(lines[:start]) + new_profile_yaml + "".join(lines[end:])


def dump_profile_yaml(profile_dict: dict) -> str:
    """Dump the profile block with literal block style for the text field."""

    class LiteralDumper(yaml.SafeDumper):
        pass

    def str_representer(dumper, data):
        style = "|" if "\n" in data else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)

    LiteralDumper.add_representer(str, str_representer)

    return yaml.dump(
        profile_dict,
        Dumper=LiteralDumper,
        allow_unicode=True,
        sort_keys=False,
        width=100,
        default_flow_style=False,
    )


def main():
    profile = build_profile_dict()
    profile_yaml = dump_profile_yaml(profile)

    config_text = CONFIG.read_text(encoding="utf-8")
    new_text = replace_profile_block(config_text, profile_yaml)

    # Ensure blank line separation around the injected block
    if not new_text.endswith("\n"):
        new_text += "\n"

    CONFIG.write_text(new_text, encoding="utf-8")
    print(f"Updated profile block in {CONFIG}", file=sys.stderr)
    print(f"  domains: {len(profile['profile']['text'].splitlines())} header lines", file=sys.stderr)
    print(f"  high_priority_topics: {len(profile['profile']['high_priority_topics'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
