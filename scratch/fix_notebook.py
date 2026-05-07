import json
from pathlib import Path

notebook_path = Path(r"c:\Users\User\Documents\GitHub\Information-Management-Finals-E-Commerce-Dashboard\MarketMate\sana-ai-hub\notebook\rf_vs_xgboost_comparison_3.ipynb")

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

# Add New Cell: Save Models
save_models_code = [
    "import joblib\n",
    "import os\n",
    "\n",
    "# Create models directory if it doesn't exist\n",
    "os.makedirs('../models', exist_ok=True)\n",
    "\n",
    "# Save Random Forest Pipeline (includes Scaler + Model)\n",
    "joblib.dump(rf_pipeline, '../models/random_forest_model.joblib')\n",
    "\n",
    "# Save XGBoost Pipeline (includes Scaler + Model)\n",
    "joblib.dump(xgb_pipeline, '../models/xgboost_model.joblib')\n",
    "\n",
    "print(\"Models saved successfully in the 'models/' directory! ✅\")"
]

# Check if save cell already exists to avoid duplication
if not any('joblib.dump' in "".join(c['source']) for c in nb['cells']):
    nb['cells'].append({
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": save_models_code
    })
    found_save = True
else:
    found_save = False

if found_leakage or found_xgb or found_save:
    with open(notebook_path, 'w', encoding='utf-8') as f:
        json.dump(nb, f, indent=1)
    print(f"Successfully updated notebook: leakage={found_leakage}, xgb={found_xgb}, save={found_save}")
else:
    print("No changes needed.")
