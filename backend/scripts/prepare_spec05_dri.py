from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / "datas" / "processed" / "Nutrition" / "DRI2023.csv"

FIELDS = (
    "性别",
    "年龄下限",
    "年龄上限",
    "生理阶段",
    "劳动强度",
    "energy_mj",
    "protein_rni_g",
    "protein_amdr_min_percent",
    "protein_amdr_max_percent",
    "fat_amdr_min_percent",
    "fat_amdr_max_percent",
    "carbohydrate_amdr_min_percent",
    "carbohydrate_amdr_max_percent",
    "fiber_ai_min_g",
    "fiber_ai_max_g",
    "sodium_ai_mg",
    "sodium_pi_mg",
    "calcium_rni_mg",
    "calcium_ul_mg",
    "iron_rni_mg",
    "iron_ul_mg",
    "来源表",
)


def _row(
    *,
    sex: str,
    age_min: int,
    age_max: int | str,
    stage: str,
    activity: str,
    energy_mj: str,
    protein: str,
    protein_min: str = "10",
    fiber_min: str = "25",
    fiber_max: str = "30",
    sodium_ai: str = "1500",
    sodium_pi: str = "2000",
    iron: str,
) -> dict[str, Any]:
    return {
        "性别": sex,
        "年龄下限": age_min,
        "年龄上限": age_max,
        "生理阶段": stage,
        "劳动强度": activity,
        "energy_mj": energy_mj,
        "protein_rni_g": protein,
        "protein_amdr_min_percent": protein_min,
        "protein_amdr_max_percent": "20",
        "fat_amdr_min_percent": "20",
        "fat_amdr_max_percent": "30",
        "carbohydrate_amdr_min_percent": "50",
        "carbohydrate_amdr_max_percent": "65",
        "fiber_ai_min_g": fiber_min,
        "fiber_ai_max_g": fiber_max,
        "sodium_ai_mg": sodium_ai,
        "sodium_pi_mg": sodium_pi,
        "calcium_rni_mg": "800",
        "calcium_ul_mg": "2000",
        "iron_rni_mg": iron,
        "iron_ul_mg": "42",
        "来源表": "中国居民膳食营养素参考摄入量（2023版）表9–18、20–23",
    }


def build_rows() -> list[dict[str, Any]]:
    """生成成人、老年、孕期和哺乳期共41组正式DRI规则。"""

    rows: list[dict[str, Any]] = []
    ordinary_energy = {
        ("男", 18, 29): ("9.00", "10.67", "12.55"),
        ("女", 18, 29): ("7.11", "8.79", "10.25"),
        ("男", 30, 49): ("8.58", "10.46", "12.34"),
        ("女", 30, 49): ("7.11", "8.58", "10.04"),
        ("男", 50, 64): ("8.16", "10.04", "11.72"),
        ("女", 50, 64): ("6.69", "8.16", "9.62"),
    }
    for (sex, age_min, age_max), energies in ordinary_energy.items():
        for activity, energy in zip(("低", "中", "高"), energies, strict=True):
            protein = "65" if sex == "男" else "55"
            iron = "12" if sex == "男" else "18"
            rows.append(
                _row(
                    sex=sex,
                    age_min=age_min,
                    age_max=age_max,
                    stage="普通",
                    activity=activity,
                    energy_mj=energy,
                    protein=protein,
                    iron=iron,
                )
            )
            if sex == "女" and age_min == 50:
                rows.append(
                    _row(
                        sex=sex,
                        age_min=age_min,
                        age_max=age_max,
                        stage="无月经",
                        activity=activity,
                        energy_mj=energy,
                        protein=protein,
                        iron="10",
                    )
                )

    elderly_energy = {
        ("男", 65, 74): ("7.95", "9.62"),
        ("女", 65, 74): ("6.49", "7.74"),
        ("男", 75, ""): ("7.53", "9.20"),
        ("女", 75, ""): ("6.28", "7.32"),
    }
    for (sex, age_min, age_max), energies in elderly_energy.items():
        for activity, energy in zip(("低", "中"), energies, strict=True):
            rows.append(
                _row(
                    sex=sex,
                    age_min=age_min,
                    age_max=age_max,
                    stage="普通",
                    activity=activity,
                    energy_mj=energy,
                    protein="72" if sex == "男" else "62",
                    protein_min="15",
                    sodium_ai="1400",
                    sodium_pi="1900" if age_min == 65 else "1800",
                    iron="12" if sex == "男" else "10",
                )
            )

    pregnancy = {
        "孕早期": (("7.11", "8.79", "10.25"), "55", "25", "30", "18"),
        "孕中期": (("8.16", "9.84", "11.30"), "70", "29", "34", "25"),
        "孕晚期": (("8.78", "10.46", "11.92"), "85", "29", "34", "29"),
        "哺乳期": (("8.78", "10.46", "11.92"), "80", "29", "34", "24"),
    }
    for stage, (energies, protein, fiber_min, fiber_max, iron) in pregnancy.items():
        for activity, energy in zip(("低", "中", "高"), energies, strict=True):
            rows.append(
                _row(
                    sex="女",
                    age_min=18,
                    age_max=49,
                    stage=stage,
                    activity=activity,
                    energy_mj=energy,
                    protein=protein,
                    fiber_min=fiber_min,
                    fiber_max=fiber_max,
                    iron=iron,
                )
            )

    if len(rows) != 41:
        raise RuntimeError(f"DRI规则数量错误：预期41，实际{len(rows)}")
    return rows


def main() -> None:
    rows = build_rows()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"已生成 {len(rows)} 条DRI规则：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
