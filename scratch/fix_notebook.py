import json
from pathlib import Path

notebook_path = Path(r"c:\Users\User\Documents\GitHub\Information-Management-Finals-E-Commerce-Dashboard\MarketMate\sana-ai-hub\notebook\rf_vs_xgboost_comparison.ipynb")

if not notebook_path.exists():
    print(f"Error: {notebook_path} not found")
    exit(1)

with open(notebook_path, 'r', encoding='utf-8') as f:
    nb = json.load(f)

# Fix Cell 2: Data Leakage & Path
found_leakage = False
for cell in nb['cells']:
    if cell['cell_type'] == 'code':
        src = "".join(cell['source'])
        if 'train_df      = pd.read_csv' in src or "pd.read_csv('training_data.csv')" in src:
            cell['source'] = [
                "# Load only the combined dataset to avoid duplication/leakage\n",
                "full_df = pd.read_csv('../data/training_data.csv')\n",
                "\n",
                "print(f'Total samples: {len(full_df):,}')\n",
                "print(f'Columns: {list(full_df.columns)}')\n",
                "full_df.head(3)"
            ]
            found_leakage = True
            break

# Fix Cell 5: XGBoost Hyperparameters
found_xgb = False
for cell in nb['cells']:
    if cell['cell_type'] == 'code' and 'xgb.XGBClassifier' in "".join(cell['source']):
        # Replace max_depth=6 with max_depth=4
        src = "".join(cell['source'])
        if 'max_depth=6' in src:
            new_src = src.replace('max_depth=6', 'max_depth=4')
            cell['source'] = [line + '\n' for line in new_src.split('\n')]
            # Clean up the last newline if it added an extra one
            if cell['source'][-1] == '\n':
                cell['source'].pop()
            found_xgb = True
        break

if found_leakage or found_xgb:
    with open(notebook_path, 'w', encoding='utf-8') as f:
        json.dump(nb, f, indent=1)
    print(f"Successfully updated notebook: leakage={found_leakage}, xgb={found_xgb}")
else:
    print("Could not find cells to update.")
