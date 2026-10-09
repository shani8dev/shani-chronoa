#!/usr/bin/env python3
"""
Analyze existing skills against the matrix to find CLI command enrichment opportunities.

This script finds:
1. What CLI commands each skill currently uses
2. Which package those commands belong to
3. Other commands in the same package that could extend the skill
4. Rank opportunities by relevance and potential impact
"""

import ast
import json
import pathlib
import collections
import sys
from typing import Dict, Set, List, Tuple

# Load both matrices
mx = {}
for p, f in (
    ("gnome", "../shani-install-media/test-env/mout/matrix-gnome.json"),
    ("plasma", "../shani-install-media/test-env/mout-plasma/shanios-matrix.json"),
):
    d = json.loads(pathlib.Path(f).read_text())
    pkg = collections.defaultdict(set)
    for c in d["commands"]:
        pkg[c.get("package", "_")].add(c["command"])
    mx[p] = pkg

# Map command to package (prefer GNOME if both have it)
cmd_to_pkg = {}
for pkg, commands in mx["gnome"].items():
    for cmd in commands:
        cmd_to_pkg[cmd] = pkg
for pkg, commands in mx["plasma"].items():
    for cmd in commands:
        if cmd not in cmd_to_pkg:
            cmd_to_pkg[cmd] = pkg

# Parse skill files to extract used CLI commands
skills_dir = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa/skills")
used_commands = collections.defaultdict(set)  # skill -> set of commands used

for skill_file in skills_dir.glob("*.py"):
    if skill_file.name.startswith("_") or skill_file.name == "__init__.py":
        continue
    
    skill_name = skill_file.stem
    content = skill_file.read_text(errors="replace")
    
    # AST parsing to find CLI calls
    try:
        tree = ast.parse(content)
        
        class CLIExtractor(ast.NodeVisitor):
            def __init__(self):
                self.commands = set()
            
            def visit_Call(self, node):
                # Look for calls like: run(["command", ...]), Popen(["command", ...])
                if isinstance(node.func, ast.Attribute) and node.func.attr in ("run", "Popen", "call", "check_output", "check_call"):
                    if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
                        if node.args[0].elts and isinstance(node.args[0].elts[0], ast.Constant):
                            self.commands.add(node.args[0].elts[0].value)
                self.generic_visit(node)
        
        extractor = CLIExtractor()
        extractor.visit(tree)
        used_commands[skill_name] = extractor.commands
        
    except Exception as e:
        print(f"Warning: Could not parse {skill_file}: {e}", file=sys.stderr)

# Map commands to packages
def get_package_for_command(cmd: str) -> str:
    return cmd_to_pkg.get(cmd, "_")

# For each skill, find packages and extension opportunities
analysis_results = []
for skill, commands in used_commands.items():
    if not commands:
        continue
        
    # Get all packages for this skill's commands
    packages = {get_package_for_command(cmd) for cmd in commands if cmd != "_"}
    
    # Find other commands in each package
    extension_opportunities = collections.Counter()
    for pkg in packages:
        all_cmds = mx["gnome"].get(pkg, set()) | mx["plasma"].get(pkg, set())
        
        for cmd in all_cmds:
            if cmd not in commands:
                extension_opportunities[cmd] += 1
    
    if extension_opportunities:
        analysis_results.append({
            "skill": skill,
            "packages": sorted(packages),
            "used_commands": sorted(commands),
            "extension_opportunities": dict(sorted(extension_opportunities.items(), key=lambda x: (-x[1], x[0]))),
            "total_opportunities": sum(extension_opportunities.values())
        })

# Sort by opportunity count (descending)
analysis_results.sort(key=lambda x: x["total_opportunities"], reverse=True)

# Output results to stdout
print("=" * 100)
print(f"{"Skill":<20} {"Packages":<15} {"Used Commands":<25} {"Extension Opportunities":<30} Total")
print("=" * 100)
for result in analysis_results:
    pkgs_str = ", ".join(result["packages"][:3])
    if len(result["packages"]) > 3:
        pkgs_str += f" ... (+{len(result['packages']) - 3})"
    
    used_str = ", ".join(result["used_commands"][:3])
    if len(result["used_commands"]) > 3:
        used_str += f" ... (+{len(result['used_commands']) - 3})"
    
    opp_str = ", ".join(f"{k}({v})" for k, v in list(result["extension_opportunities"].items())[:3])
    if len(result["extension_opportunities"]) > 3:
        opp_str += f" ... (+{len(result['extension_opportunities']) - 3})"
    
    print(f"{result['skill']:<20} {pkgs_str:<15} {used_str:<25} {opp_str:<30} {result['total_opportunities']}")

print("\n" + "=" * 100)
print("Top 10 skills by extension opportunities:")
for i, result in enumerate(analysis_results[:10]):
    print(f"\n{i+1}. {result['skill']}:")
    print(f"   Packages: {', '.join(result['packages'])}")
    print(f"   Currently uses: {', '.join(result['used_commands'])}")
    print(f"   Extension opportunities ({result['total_opportunities']} total):")
    for cmd, count in list(result["extension_opportunities"].items())[:5]:
        print(f"     - {cmd} (appears in {count} contexts)")

# Also count skills by package for insight
print("\n" + "=" * 100)
print("Skills by package:")
pkg_skills = collections.defaultdict(list)
for result in analysis_results:
    for pkg in result["packages"]:
        pkg_skills[pkg].append(result["skill"])

for pkg, skills in sorted(pkg_skills.items(), key=lambda x: -len(x[1])):
    print(f"\n{pkg}: {len(skills)} skills")
    print(f"  {', '.join(sorted(skills)[:10])}")
    if len(skills) > 10:
        print(f"  ... and {len(skills) - 10} more")