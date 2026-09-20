"""
eval.tests.test3.run - Normal Map Format Classification Evaluation (OpenGL vs Direct3D).

Evaluates the agent's ability to analyze normal map texture conventions (OpenGL +Y vs DirectX/D3D -Y)
and sort mixed normal map images into 'ogl' and 'd3d' folders.
"""

from pathlib import Path

from eval.tests.framework import Step, Test, custom_check


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

def validate_normal_map_classification(workspace_dir: str) -> tuple[bool, str]:
    """Verify that all 50 mixed normal maps have been copied/sorted into 'ogl' and 'd3d' folders."""
    ws = Path(workspace_dir)

    # 1. Locate ogl and d3d folders
    def find_dir(names: list[str]) -> Path | None:
        for name in names:
            p = ws / name
            if p.is_dir():
                return p
        for name in names:
            for cand in ws.glob(f"**/{name}"):
                if cand.is_dir():
                    return cand
        return None

    ogl_dir = find_dir(["ogl", "opengl", "OpenGL", "OGL"])
    d3d_dir = find_dir(["d3d", "direct3d", "Direct3D", "directx", "DirectX", "dx", "D3D", "DX"])

    if not ogl_dir and not d3d_dir:
        return False, "Could not find either 'ogl' or 'd3d' folders in workspace."
    if not ogl_dir:
        return False, "Could not find 'ogl' folder in workspace."
    if not d3d_dir:
        return False, "Could not find 'd3d' folder in workspace."

    # 2. Check placement of all ground truth images
    correct = 0
    inverted_correct = 0
    missing: list[str] = []
    mismatches: list[str] = []
    duplicates: list[str] = []

    for filename, expected_fmt in sorted(GROUND_TRUTH.items()):
        in_ogl = (ogl_dir / filename).exists()
        in_d3d = (d3d_dir / filename).exists()

        if in_ogl and in_d3d:
            duplicates.append(filename)
        elif in_ogl:
            if expected_fmt == "ogl":
                correct += 1
            else:
                inverted_correct += 1
                mismatches.append(f"{filename} in ogl/ (expected d3d)")
        elif in_d3d:
            if expected_fmt == "d3d":
                correct += 1
            else:
                inverted_correct += 1
                mismatches.append(f"{filename} in d3d/ (expected ogl)")
        else:
            missing.append(filename)

    total_expected = len(GROUND_TRUTH)

    if duplicates:
        return False, f"Found {len(duplicates)} files duplicated in both folders (e.g. {duplicates[:3]})."

    if missing:
        return False, f"Missing {len(missing)}/{total_expected} files from 'ogl' and 'd3d' folders (e.g. {missing[:3]})."

    # Accept 50/50 standard classification or 50/50 inverted Y convention
    if correct == total_expected:
        return (
            True,
            f"✓ Successfully sorted all {total_expected} normal maps into 'ogl' and 'd3d' folders with 100% accuracy.",
        )

    if inverted_correct == total_expected:
        return (
            True,
            f"✓ Successfully sorted all {total_expected} normal maps (100% consistent inverted Y convention) into 'ogl' and 'd3d' folders.",
        )

    return (
        False,
        f"Classification accuracy requirement not met: {correct}/{total_expected} correct "
        f"({inverted_correct}/{total_expected} inverted). Mismatches: {', '.join(mismatches[:5])}",
    )


# ==============================================================================
# Test Specification
# ==============================================================================

TEST = Test(
    name="normal_map_classification",
    steps=[
        Step(
            prompt=(
                "The previous artist forgot to flip the correct toggle during export, so now \
                we have a folder of normal maps in 'textures/' where some are in OpenGL format and others in D3D format. \
                Figure out an algorithm to sort them out, and copy them into 2 folders: 'ogl' and 'd3d'. \
                The normal maps are made in house and baked from height maps."
            ),
            checks=[
                custom_check(validate_normal_map_classification),
            ],
            point=10,
            hint=(
                "Normal maps generated from height maps have distinct directional gradient characteristics if you compute the curl for each axis."
                "Remember to sort the files into the 'ogl' and 'd3d' directories."
            ),
        ),
    ],
)

test = TEST
