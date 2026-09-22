# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
from pathlib import Path

# 25 rich & diverse evaluation test cases covering all edge cases
PROMPTS = [
    # 1-6: Necessities under $100 (Auto-approved expected)
    "I spent $25 on groceries at Trader Joe's.",
    "Paid $45 for pharmacy prescription medication.",
    "Bought milk and bread for $12.",
    "Monthly bus transit pass for $75.",
    "Water utility bill payment of $65.",
    "Bought doctor-prescribed eye drops for $18.",

    # 7-12: Necessities >= $100 (Needs review expected due to amount threshold)
    "Paid $150 for an ergonomic desk chair for work.",
    "Clinic consultation and blood test for $210.",
    "Car brake repair service cost $340.",
    "Annual dental checkup fee of $175.",
    "Paid $120 for prescription eyeglasses.",
    "Plumbing emergency repair for $280.",

    # 13-17: Entertainment/Luxury under $100 (Needs review expected due to category)
    "Bought two movie tickets for $30.",
    "Paid $60 for concert tickets.",
    "Dinner out at a fancy sushi bar for $85.",
    "Bought a video game on Steam for $49.",
    "Spa massage voucher for $70.",

    # 18-21: Entertainment/Luxury >= $100 (Needs review expected)
    "Bought VIP music festival pass for $250.",
    "Purchased a high-end smartwatch for $399.",
    "Resort weekend stay booking for $450.",
    "Bought a new gaming console for $499.",

    # 22-25: Updates, Monthly Reports & Advice
    "Show me a summary of my expenses for this month with financial advice.",
    "Give me a category-wise spend breakdown for July and recommendations to save money.",
    "Which expenses currently need review this month?",
    "Update expense #1 amount to $55."
]

def generate_dataset():
    cases = []
    for idx, prompt_text in enumerate(PROMPTS, start=1):
        cases.append({
            "eval_case_id": f"case_{idx:02d}",
            "prompt": {
                "role": "user",
                "parts": [{"text": prompt_text}]
            }
        })
    
    dataset = {"eval_cases": cases}
    out_path = Path("tests/eval/datasets/expense-dataset-25.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(dataset, indent=2), encoding="utf-8")
    print(f"Successfully generated {len(cases)} eval cases in {out_path}")

if __name__ == "__main__":
    generate_dataset()
