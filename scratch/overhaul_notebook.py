import json
from pathlib import Path

notebook_path = Path(r"c:\Users\User\Documents\GitHub\Information-Management-Finals-E-Commerce-Dashboard\MarketMate\sana-ai-hub\notebook\rf_vs_xgboost_comparison.ipynb")

with open(notebook_path, 'r', encoding='utf-8') as f:
    nb = json.load(f)

# Define the new content for the loading and training cells
new_cells = []

# --- Cell 2: Load Data ---
data_loading_source = [
    "# 2. Load Split Datasets\n",
    "train_df = pd.read_csv('../data/train_data.csv')\n",
    "val_df   = pd.read_csv('../data/val_data.csv')\n",
    "test_df  = pd.read_csv('../data/test_data.csv')\n",
    "\n",
    "print(f'Train samples: {len(train_df):,}')\n",
    "print(f'Val samples:   {len(val_df):,}')\n",
    "print(f'Test samples:  {len(test_df):,}')\n"
]

# --- Cell 4: Preprocessing ---
preprocessing_source = [
    "# 4. Feature Engineering & Preprocessing\n",
    "def prepare_xy(df):\n",
    "    X = df[FEATURE_COLS].copy()\n",
    "    y = df['label'].copy()\n",
    "    X = X.fillna(X.median())\n",
    "    return X, y\n",
    "\n",
    "X_train, y_train = prepare_xy(train_df)\n",
    "X_val,   y_val   = prepare_xy(val_df)\n",
    "X_test,  y_test  = prepare_xy(test_df)\n",
    "\n",
    "print(f'Training shape:   {X_train.shape}')\n",
    "print(f'Validation shape: {X_val.shape}')\n",
    "print(f'Test shape:       {X_test.shape}')\n"
]

# --- Cell 6: Evaluation ---
evaluation_source = [
    "def evaluate_on_sets(pipeline, model_name):\n",
    "    # Fit on training data\n",
    "    pipeline.fit(X_train, y_train)\n",
    "    \n",
    "    # Predict on Val and Test\n",
    "    val_pred = pipeline.predict(X_val)\n",
    "    test_pred = pipeline.predict(X_test)\n",
    "    \n",
    "    val_acc = accuracy_score(y_val, val_pred)\n",
    "    test_acc = accuracy_score(y_test, test_pred)\n",
    "    \n",
    "    print(f'=== {model_name} ===')\n",
    "    print(f'Validation Accuracy: {val_acc:.4f}')\n",
    "    print(f'Test Accuracy:       {test_acc:.4f}')\n",
    "    print(classification_report(y_test, test_pred))\n",
    "    return pipeline\n",
    "\n",
    "rf_model  = evaluate_on_sets(rf_pipeline, 'Random Forest')\n",
    "xgb_model = evaluate_on_sets(xgb_pipeline, 'XGBoost')\n"
]

# Simple replacement logic for this specific notebook structure
for cell in nb['cells']:
    if cell['cell_type'] == 'code':
        src = "".join(cell['source'])
        if 'pd.read_csv' in src:
            cell['source'] = data_loading_source
        elif 'X = full_df' in src or 'prepare_xy' in src:
            cell['source'] = preprocessing_source
        elif 'evaluate_model_cv' in src:
            cell['source'] = evaluation_source

with open(notebook_path, 'w', encoding='utf-8') as f:
    json.dump(nb, f, indent=1)
print("Successfully overhauled notebook to use explicit Train/Val/Test splits.")
