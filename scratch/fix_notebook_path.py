import json
from pathlib import Path

notebook_path = Path(r"c:\Users\User\Documents\GitHub\Information-Management-Finals-E-Commerce-Dashboard\MarketMate\sana-ai-hub\notebook\rf_vs_xgboost_comparison.ipynb")

if not notebook_path.exists():
    print(f"Error: {notebook_path} not found")
    exit(1)

with open(notebook_path, 'r', encoding='utf-8') as f:
    nb = json.load(f)

updated = False
for cell in nb['cells']:
    if cell['cell_type'] == 'code':
        source = cell['source']
        for i, line in enumerate(source):
            if "pd.read_csv('./data/training_data.csv')" in line:
                source[i] = line.replace("./data/training_data.csv", "../data/training_data.csv")
                updated = True

if updated:
    with open(notebook_path, 'w', encoding='utf-8') as f:
        json.dump(nb, f, indent=1)
    print("Successfully updated notebook path.")
else:
    print("Could not find the target path to update.")
