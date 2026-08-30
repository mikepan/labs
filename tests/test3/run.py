"""
tests.test3.run - Normal Map Format Classification Evaluation (OpenGL vs Direct3D).

Evaluates the agent's ability to analyze normal map texture conventions (OpenGL +Y vs DirectX/D3D -Y)
and produce an accurate, reusable Python classification script that classifies normal map images into 'd3d' or 'ogl'.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from tests.framework import Step, Test, custom_check


# ==============================================================================
# Ground Truth Definitions (50 Mixed Normal Maps)
# ==============================================================================

GROUND_TRUTH: dict[str, str] = {
    "Fabric_Craft_01_height_normal.png": "ogl",
    "Fabric_Craft_02_height_normal.png": "ogl",
    "Fabric_Laces_02_height_normal.png": "ogl",
    "Fabric_Mattress_01_height_normal.png": "ogl",
    "Fabric_Mattress_02_height_normal.png": "d3d",
    "Fabric_Mattress_03_height_normal.png": "d3d",
    "Fabric_Mattress_04_height_normal.png": "ogl",
    "Fabric_Pattern_01_height_normal.png": "ogl",
    "Fabric_Pattern_03_height_normal.png": "d3d",
    "Fabric_Pattern_04_height_normal.png": "d3d",
    "Fabric_Pattern_05_height_normal.png": "d3d",
    "Fabric_Pattern_06_height_normal.png": "d3d",
    "Fabric_Pattern_07_height_normal.png": "ogl",
    "Fabric_Pattern_08_height_normal.png": "d3d",
    "Fabric_Towel_01_height_normal.png": "d3d",
    "Fabric_carpet_01_height_normal.png": "ogl",
    "Fabric_carpet_02_height_normal.png": "ogl",
    "Fabric_laces_01_height_normal.png": "ogl",
    "Fabric_linen_01_height_normal.png": "ogl",
    "Fabric_roughPattern_01_height_normal.png": "d3d",
    "Fabric_wool_01_height_normal.png": "d3d",
    "Fabric_wool_02_height_normal.png": "ogl",
    "Facades_01_height_normal.png": "d3d",
    "Facades_02_height_normal.png": "ogl",
    "Facades_03v2_height_normal.png": "d3d",
    "Facades_04_height_normal.png": "d3d",
    "Facades_07_height_normal.png": "ogl",
    "Facades_08_height_normal.png": "ogl",
    "Metal_Feathers_01_height_normal.png": "d3d",
    "Metal_Grid_01_height_normal.png": "ogl",
    "Metal_Panels_01_height_normal.png": "ogl",
    "Metal_Tiles_01_height_normal.png": "ogl",
    "Metal_Tiles_02_height_normal.png": "d3d",
    "Tiles_01_height_normal.png": "d3d",
    "Tiles_02_height_normal.png": "ogl",
    "Tiles_03_height_normal.png": "d3d",
    "Tiles_04_height_normal.png": "ogl",
    "Tiles_05_height_normal.png": "d3d",
    "Tiles_06_height_normal.png": "ogl",
    "Tiles_07_height_normal.png": "d3d",
    "Tiles_08_height_normal.png": "d3d",
    "Tiles_09_height_normal.png": "ogl",
    "Tiles_Floor_02_height_normal.png": "ogl",
    "Tiles_Wall_01_height_normal.png": "ogl",
    "Wood02_Height_normal.png": "d3d",
    "WoodLogs_Height_normal.png": "d3d",
    "WoodPlanksPainted_Height_normal.png": "ogl",
    "WoodPlanks_Height_normal.png": "d3d",
    "WoodRoof_Height_normal.png": "d3d",
    "Wood_wall_Slits_Height_normal.png": "d3d",
}


# ==============================================================================
# Validators
# ==============================================================================

def _normalize_format_str(val: str) -> str:
    """Normalize format strings like 'opengl', 'ogl', 'directx', 'dx', 'd3d'."""
    v = val.strip().lower()
    # Check for direct matches or keywords
    if re.search(r"\b(ogl|opengl|gl|\+y|up)\b", v) or v.endswith("ogl") or v.endswith("opengl"):
        return "ogl"
    if re.search(r"\b(d3d|directx|dx|\-y|down)\b", v) or v.endswith("d3d") or v.endswith("directx"):
        return "d3d"
    if "ogl" in v or "opengl" in v:
        return "ogl"
    if "d3d" in v or "directx" in v or "dx" in v:
        return "d3d"
    return v


def validate_normal_map_classification(workspace_dir: str) -> tuple[bool, str]:
    """Execute the agent's Python script on all 50 mixed normal maps and verify accuracy."""
    ws = Path(workspace_dir)

    # 1. Locate the Python classification script
    py_candidates = [
        p for p in ws.glob("*.py")
        if p.name not in ("run.py", "eval.py", "test.py") and not p.name.startswith(".")
    ]
    if not py_candidates:
        py_candidates = [
            p for p in ws.glob("**/*.py")
            if p.name not in ("run.py", "eval.py", "test.py") and not p.name.startswith(".")
        ]

    if not py_candidates:
        return False, "No Python classification script (*.py) found in workspace."

    # Prefer scripts named classify, sort, detect, normal, etc.
    chosen_py = None
    for cand in py_candidates:
        cand_lower = cand.name.lower()
        if any(k in cand_lower for k in ("class", "sort", "detect", "normal", "predict")):
            chosen_py = cand
            break
    if not chosen_py:
        chosen_py = py_candidates[0]

    # 2. Locate the mixed/ directory containing test images
    mixed_dir = ws / "mixed"
    if not mixed_dir.exists():
        for cand_dir in [ws] + list(ws.glob("**/")):
            if any((cand_dir / fn).exists() for fn in list(GROUND_TRUTH.keys())[:3]):
                mixed_dir = cand_dir
                break

    if not mixed_dir.exists():
        return False, "Could not find 'mixed/' directory containing normal map textures."

    # 3. Test the script against all ground truth images
    correct = 0
    total = 0
    mismatches: list[str] = []

    for filename, expected_fmt in sorted(GROUND_TRUTH.items()):
        img_path = mixed_dir / filename
        if not img_path.exists():
            continue

        total += 1

        res = subprocess.run(
            [sys.executable, str(chosen_py), str(img_path)],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )

        detected_fmt = _normalize_format_str(res.stdout.strip())

        if detected_fmt == expected_fmt:
            correct += 1
        else:
            mismatches.append(f"{filename}: expected={expected_fmt}, got={detected_fmt or 'unknown'}")

    if total == 0:
        return False, "No test normal map images were found to evaluate."

    accuracy = (correct / total) * 100
    inverted_correct = sum(
        1 for filename, expected_fmt in GROUND_TRUTH.items()
        if (mixed_dir / filename).exists() and mismatches and any(
            filename in m and f"expected={expected_fmt}" in m and f"got={'d3d' if expected_fmt == 'ogl' else 'ogl'}" in m
            for m in mismatches
        )
    )
    inverted_pct = (inverted_correct / total) * 100

    if accuracy < 80.0:
        if inverted_pct >= 80.0:
            return (
                False,
                f"Classification results are completely flipped from reality ({correct}/{total} correct, "
                f"{inverted_correct}/{total} ({inverted_pct:.1f}%) inverted). "
                f"Expected at least 80% accuracy.",
            )
        return (
            False,
            f"Classification accuracy too low: {correct}/{total} ({accuracy:.1f}%). "
            f"Expected at least 80% accuracy.",
        )

    return (
        True,
        f"✓ Successfully classified normal maps with {accuracy:.1f}% accuracy ({correct}/{total}) "
        f"using '{chosen_py.name}'.",
    )


# ==============================================================================
# Test Specification
# ==============================================================================

TEST = Test(
    name="normal_map_format_classification",
    description="Analyze mixed normal map textures to determine OpenGL (+Y) vs Direct3D (-Y) format via algorithmic analysis.",
    steps=[
        Step(
            name="Classify Normal Maps (OpenGL vs Direct3D)",
            prompt=(
                "i have a folder of normal maps in 'mixed/', but some are in OpenGL and some in D3D format. "
                "figure out an algorithm to sort them out. create a python script that takes the file input as name/path, "
                "and returns or prints 'd3d' or 'ogl' as the answer."
            ),
            checks=[
                custom_check(validate_normal_map_classification),
            ],
            point=20,
            hint = "Keep in mind these normal maps are generated from height maps. And there should be a way to tell them apart. \
                Ensure you create a python script that takes the file input, \
                and returns or prints 'd3d' or 'ogl' as the answer."
        ),
        
    ],
)

test = TEST
