import json
from pathlib import Path

notebook_path = Path(r"c:\Users\User\Documents\GitHub\Information-Management-Finals-E-Commerce-Dashboard\MarketMate\sana-ai-hub\notebook\rf_vs_xgboost_comparison.ipynb")

with open(notebook_path, 'r', encoding='utf-8') as f:
    nb = json.load(f)

for cell in nb['cells']:
    if cell['cell_type'] == 'code':
        src = "".join(cell['source'])
        
        # 1. Update Imports
        if 'from sklearn.model_selection import StratifiedKFold' in src:
            cell['source'] = [line.replace('StratifiedKFold', 'GroupKFold') for line in cell['source']]
            
        # 2. Don't drop 'source' from X yet
        if "X = full_df[FEATURE_COLS].copy()" in src:
            cell['source'] = [
                "# Keep 'source' for GroupKFold to avoid leakage\n",
                "X = full_df[FEATURE_COLS + ['source']].copy()\n",
                "y = full_df['label'].copy()\n"
            ]
            
        # 3. Update Fold Definition
        if 'skf = StratifiedKFold' in src:
            cell['source'] = [
                "# --- GroupKFold to prevent leakage (Good/Bad pairs stay together) ---\n",
                "gkf = GroupKFold(n_splits=N_SPLITS)\n",
                "groups = X['source']\n",
                "X = X.drop(columns=['source']) # Now we can drop it\n",
                "\n"
            ] + [line for line in cell['source'] if 'xgb_pipeline' in line or 'rf_pipeline' in line or 'print' in line or 'scale_pos_weight' in line]

        # 4. Update CV function signature and loop
        if 'def evaluate_model_cv(pipeline, X, y, skf, model_name):' in src:
             cell['source'] = [line.replace('skf', 'gkf').replace('split(X, y)', 'split(X, y, groups=groups)') for line in cell['source']]

with open(notebook_path, 'w', encoding='utf-8') as f:
    json.dump(nb, f, indent=1)
print("Successfully updated notebook to use GroupKFold to prevent leakage.")
