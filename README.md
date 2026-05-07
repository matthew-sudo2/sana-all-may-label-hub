# Sana All May Label

![Sana Logo](frontend/sanallmaylabel.png)

> **Research, reinvented.**  
> Close the gap between data collection and actionable insights. Sana automates the 80% of research prep that students spend fighting their data.

## The Problem

IBM research shows that **data preparation consumes 80% of a data professional's time**. For students, it's worse. Many avoid quantitative research entirely—not because the questions don't matter, but because the tools are too hard to use. They don't skip the data because they're lazy. They skip it because nobody made it accessible.

## The Solution

Sana closes that gap. A student uploads messy, raw data—straight from a survey, experiment, or web scrape—and Sana handles everything else:

- **Data Cleaning** — Remove duplicates, handle missing values, detect anomalies
- **Statistical Analysis** — Compute means, medians, correlations, outliers
- **Auto-Visualization** — Generate 16+ charts automatically, zero configuration
- **Quality Validation** — Confidence scoring across completeness, consistency, and accuracy

**What takes hours in Excel takes minutes in Sana.**

## What You Get

✅ Clean, validated data ready for research  
✅ 16+ auto-generated charts and visualizations  
✅ Full statistical analysis per column  
✅ Confidence scores you can cite  
✅ Exportable quality reports  
✅ All open-source, zero cloud costs, runs locally

## Who This Is For

- **Students** doing quantitative research and avoiding it because "data is hard"
- **Researchers** who spend more time cleaning data than analyzing it
- **Teams** working with survey responses, experimental data, or messy datasets
- **Anyone** who needs to validate their data before submitting results

**If you've ever spent hours in Excel, wondering if your data is actually clean—this is for you.**

---

## Repository layout

```
sana-ai-hub/
  backend/    # Python multi-agent pipeline
  frontend/   # Vite + React dashboard
  docs/       # submission assets
```

## Multi-Agent Architecture

Sana uses **five specialized LangGraph agents** that form a sequential pipeline. Each agent has one responsibility, consumes the output of the previous stage, and passes its results downstream. The entire pipeline shares state through LangGraph—if something upstream changes, downstream agents automatically update.

```
                       ┌─────────────┐
                       │   Upload    │
                       │   CSV File  │
                       └──────┬──────┘
                              │
                      ┌───────▼────────┐
                      │  SCOUT         │
                      │  File Intake & │───→ scout_result.json
                      │  Validation    │───→ raw_data.csv
                      └───────┬────────┘
                              │
                      ┌───────▼────────┐
                      │  LABELER       │
                      │  Data Cleaning │───→ cleaned_data.csv
                      └───────┬────────┘
                              │
      ┌───────────────────────┼───────────────────────┐
      │                       │                       │
  ┌───▼────────┐      ┌───────▼────────┐      ┌──────▼──────┐
  │  ARTIST    │      │  ANALYST       │      │  (Parallel) │
  │ Charts &   │      │  Statistics    │      │             │
  │ Visuals    │      │  Analysis      │      └─────────────┘
  └───┬────────┘      └───────┬────────┘
      │                       │
      └───────────┬───────────┘
                  │
          ┌───────▼────────┐
          │  VALIDATOR     │
          │  Quality Score │───→ validation_report.md
          │  Certification │───→ confidence.json
          └───────┬────────┘
                  │
          ┌───────▼────────┐
          │   Dashboard    │
          │   React UI     │
          │  (Visualize)   │
          └────────────────┘
```

**Key Architecture Features:**
- **Sequential Pipeline**: Scout → Labeler → Artist/Analyst (parallel) → Validator
- **Shared State**: LangGraph maintains full pipeline state across all agents
- **Data Persistence**: Complete raw_data.csv passed through entire pipeline
- **Chainable Outputs**: Each agent reads prior outputs, produces new artifacts
- **Transparent**: Every intermediate file visible and exportable

---

## System Features

### 🔍 Scout Agent - File Intake & Initial Validation

**The Gatekeeper**: Validates uploaded files, discovers schema, and computes initial quality score.

- **File Handling**:
  - Accepts: CSV, XLSX, JSON uploads
  - Rejects: URLs (with guidance to download and upload)
  - No web scraping—file-based intake only
  
- **Data Validation**:
  - Minimum requirements: 3+ rows, 2+ columns
  - Automatic dtype inference (numeric, datetime, categorical)
  - Smart conversion: preserves text fields, converts <<70% matches
  
- **Schema Discovery**:
  - Column names and inferred data types
  - 5-row sample preview for dashboard
  - Detects numeric vs. categorical columns
  
- **Initial Quality Scoring** (5-dimensional):
  - **Completeness**: % non-null cells (goal: 100%)
  - **Consistency**: Values match inferred types
  - **Accuracy**: Dtype inference credibility
  - **Uniqueness**: Opposite of duplicate ratio (goal: 0% dupes)
  - **Outlier Risk**: IQR-based anomaly detection
  - **Weighted Confidence**: `0.25×C + 0.25×K + 0.20×A + 0.15×D + 0.15×O`
  
- **Outputs**:
  - `scout_result.json` — Metadata, schema, initial confidence
  - `raw_data.csv` — **Complete dataset** (read by all downstream agents)

### 🏷️ Labeler Agent - Data Cleaning & Quality Processing
- **Intelligent Cleaning** (90% faster than LLM):
  - Standardizes column names to snake_case
  - Removes empty rows and columns
  - Detects and removes duplicate records
  - IQR-based outlier detection and flagging
  - Median imputation for missing numeric values
  
- **Smart Caching** (60% speedup on re-runs):
  - MD5 fingerprint of raw data for cache validation
  - Profile hash to detect cleaning configuration changes
  - Instant return for identical datasets
  
- **Real-Time Quality Metrics**:
  - Missing value ratio calculation
  - Duplicate detection and reporting
  - Numeric column analysis
  - Constant column identification
  - Variance and skewness computation
  
- **Configurable Cleaning Profiles**:
  - Drop vs. fill strategies for missing values
  - Outlier detection methods (IQR)
  - Custom thresholds and parameters

### 🎨 Artist Agent - Visualization Generation
Auto-generates 16+ publication-ready charts without configuration:
- **Chart Types**:
  - Distribution histograms for each numeric column
  - Box plots with flagged outliers (IQR-based, shown in red)
  - Scatter plots for all numeric relationships
  - Correlation heatmaps showing column relationships
  - Time series plots for temporal data
  - Line charts for trends and patterns
  
- **Smart Defaults**:
  - Axes auto-scaled for readability
  - Outliers automatically flagged and colored
  - All charts labeled and titled
  - Zero manual configuration required
  
- **Custom Charts** (Optional):
  - Generate charts from natural language instructions
  - Fuzzy column matching (handles typos and variations)
  - Automatic fallback with helpful error messages

### 📊 Analyst Agent - Statistical Analysis
Produces full descriptive statistics automatically:
- **Per-Column Statistics** (mean, median, mode, range, variance, standard deviation, min, max)
- **Correlation Analysis** (shows which columns relate to each other)
- **Outlier Bounds** (calculates IQR-based anomaly thresholds)
- **Type-Safe Computation** (automatically excludes non-numeric columns)
- **JSON Export** (structured output for further processing)

### ✅ Validator Agent - Data Quality Certification
The differentiator: Sana doesn't just give you results—it tells you whether you can trust them.
- **Multi-Dimension Quality Scoring**:
  - **Completeness**: Missing value ratio (target: 0%)
  - **Consistency**: Duplicate detection (target: 0% duplicates)
  - **Reliability**: Correlation and statistical validity
  - **Cardinality**: Outlier and anomaly detection
  
- **Confidence Scoring**:
  - Overall data quality percentage (0–100%)
  - Per-check pass/fail reporting
  - Plain-language confidence statements ("97.8% confidence")
  
- **Exportable Validation Reports**:
  - Markdown-formatted detailed breakdown
  - GitHub Flavored Markdown tables
  - Pass/fail summary for each quality dimension
  - Ready to include in research papers

### 💻 Frontend Dashboard - React UI
- **Tab-Based Navigation**:
  - **Visual Gallery Tab**: Chart generation and visualization management
    - Create custom charts from natural language
    - Browse and manage generated visualizations
    - Real-time chart preview
  
  - **Data Viewer Tab**: Comprehensive data inspection interface
    - Quality metrics cards (rows, columns, completeness %, average data quality)
    - Color-coded quality badges (Green/Blue/Yellow/Red based on column completeness)
    - Interactive column search and filtering
    - Column visibility toggle
    - Sortable columns by name and quality metrics
    - 25-row pagination for large datasets
    - Missing value indicators (red "∅" for empty cells)
    - CSV export with proper escaping
    - **Feedback Widget**: Rate quality score accuracy (Poor/Fair/Good/Excellent)
      - Submits feedback with all 8 extracted ML features
      - Triggers auto-retraining at 1, 5, 10, 15, 20... feedbacks
      - Shows cross-validation score when model retrains
  
  - **Validation Report Tab**: Data quality assessment display
    - Markdown-formatted quality reports
    - GitHub Flavored Markdown table rendering
    - Detailed validation results
    - Quality score breakdown

- **UI Features**:
  - Dark theme for comfortable viewing
  - Professional layout with Shadcn/ui components
  - Smooth interactions and animations
  - Responsive design
  - Zero compilation errors

### 🤖 Built-In Machine Learning - Data Quality Classifier
An optional ML model provides automated quality classification on the current pipeline dataset:
- **Production Model**: Random Forest Classifier
- **Dataset**: `raw_data.csv` from the current pipeline output
- **Features**: 8 engineered metrics extracted during validation
- **Evaluation**: Final holdout test split only
- **Use Case**: Rank datasets by reliability or detect quality issues before analysis

### 💬 Continuous Feedback Loop - Model Continuous Learning
Users can still submit feedback on quality scores, and the model stores the extracted features with the dataset hash for future updates.

### ⚡ Performance Optimizations
- **Memory Efficient**: Eliminated unnecessary copying (30-50% savings)
- **Fast Cleaning**: Rule-based cleaning instead of LLM (90% speedup)
- **Intelligent Caching**: Fingerprint-based deduplication (60% speedup)
- **Builtin Defaults**: Charts generated without cloud calls (no API costs)
- **Overall Speed**: ~8-15 seconds total pipeline (was 45–70 seconds) — **80% faster**

---

## The Pipeline - Five Stages

**One Upload. Five Agents. One Confidence Score.**

```
Your CSV → Scout (Validate) → Labeler (Clean) → Artist (Chart) → Analyst (Stats) → Validator (Certify) → Export
```

### Stage 1: Scout (File Intake & Validation)
Upload any CSV/XLSX/JSON. Scout validates and discovers schema.
- ✅ File validation (min 3 rows × 2 cols)
- ✅ Schema discovery (column names & types)
- ✅ Initial quality metrics (completeness, consistency, duplicates, outliers)
- ✅ Confidence score: `0.25×Completeness + 0.25×Consistency + 0.20×Accuracy + 0.15×Duplicates + 0.15×Outliers`
- ✅ Full dataset saved for all downstream processing

### Stage 2: Labeler (Data Cleaning)
Transforms raw into reliable data.
- ✅ Removes duplicates and empty rows
- ✅ Handles missing values (median imputation for numeric, drop for categorical)
- ✅ Detects and flags outliers (IQR method)
- ✅ Standardizes column names to snake_case
- ✅ Computes per-column quality metrics
- ✅ Output: `cleaned_data.csv`

### Stage 3: Artist (Visualization)
Generates publication-ready charts, zero configuration.
- ✅ 16+ automatic charts (distributions, relationships, correlations)
- ✅ Histograms, scatter plots, heatmaps, box plots, line charts
- ✅ Outliers highlighted in red
- ✅ All axes labeled and titled
- ✅ Correlation matrix for numeric columns
- ✅ Time series detection (if applicable)
- ✅ Output: `*.png` image files

### Stage 4: Analyst (Statistics)
Computes full descriptive statistics on cleaned data.
- ✅ Per-column: mean, median, mode, range, variance, std dev, min, max
- ✅ Correlation matrix (all numeric pairs)
- ✅ Outlier bounds per column (IQR method)
- ✅ Skewness and kurtosis analysis
- ✅ Type validation per column
- ✅ Output: `analysis.json`

### Stage 5: Validator (Quality Certification)
**The Differentiator**: Scores data quality and generates confidence statement.
- ✅ Completeness check (missing value percentage)
- ✅ Consistency check (duplicate rows)
- ✅ Reliability assessment (statistical validity)
- ✅ Cardinality analysis (unique values per column)
- ✅ Overall confidence score (0–100%, citable)
- ✅ Output: `validation_report.md` (plain text, exportable) + `confidence.json`

### Final Outputs
- ✅ Cleaned CSV data (`cleaned_data.csv`)
- ✅ 16+ visualization images (`*.png`)
- ✅ Statistical analysis (`analysis.json`)
- ✅ Validation report (`validation_report.md` — attach to paper)
- ✅ Confidence score (`confidence.json` — cite this in your methodology)
- ✅ ML Features for feedback (`features.json` — cached by dataset hash)
- ✅ Feedback database (`feedback.db` — accumulates user feedback for model retraining)

---

## Installation & Usage

### Frontend (React)

From repo root:

```bash
cd frontend
npm install
npm run dev
```

### Backend (Python)

Create a virtualenv, then from repo root:

```bash
pip install -r backend/requirements.txt
```

Create `backend/.env` from `backend/.env.example` if needed for additional configuration.

### Running the Pipeline

The pipeline runs automatically through the dashboard. For direct script usage:

```bash
# Full pipeline from CSV
python backend/main.py <path_to_data.csv>
```

This executes all five agents in sequence:
1. **Scout** — Validates file, discovers schema, computes initial quality
2. **Labeler** — Cleans data (removes duplicates, fills missing, detects outliers)
3. **Artist** — Generates 16+ visualizations
4. **Analyst** — Computes statistics and correlations
5. **Validator** — Produces confidence score and quality report

Outputs produced:
- `scout_result.json` — Initial metadata and confidence
- `raw_data.csv` — Full dataset
- `cleaned_data.csv` — Cleaned dataset  
- `*.png` — Charts and visualizations (16+)
- `analysis.json` — Statistical analysis
- `validation_report.md` — Quality certification report (exportable)
- `confidence.json` — Final confidence score (citable)

### Monitoring Continuous Learning

Track the feedback loop and model improvements:

```bash
# Check feedback database
sqlite3 backend/data/feedback.db "SELECT COUNT(*) FROM feedback;"

# View feedback records with quality ratings
sqlite3 backend/data/feedback.db \
  "SELECT predicted_score, actual_label, timestamp FROM feedback ORDER BY id DESC LIMIT 10;"

# Check model retraining history
cat backend/runs/latest/model_metrics.jsonl

# View specific run features
cat backend/data/{run_id}/features.json
```

**UI Monitoring**:
1. **Data Viewer Tab** → Submit feedback while viewing data
2. **Feedback Widget** → See immediate confirmation and retrain status
3. **API Validation Report** → Check if retrain was triggered

---

## Technology Stack

**Backend**: Built on open-source frameworks, **zero cloud dependencies**
- **LangGraph**: Multi-agent state graph orchestration
- **FastAPI**: High-performance REST API
- **Ollama**: Local LLM inference (optional, not required)
- **pandas**: Data manipulation and analysis
- **NumPy**: Numerical computing
- **scikit-learn**: Machine learning (Random Forest classifier)
- **matplotlib**: Chart generation

**Frontend**: Modern web stack, runs locally
- **React**: UI framework
- **Vite**: Fast build tool
- **Shadcn/ui**: Component library
- **TailwindCSS**: Styling

**Database**: File-based persistence
- **CSV**: Cleaned data storage
- **JSON**: Analysis results, metadata, cached ML features
- **PNG**: Generated visualizations
- **Pickle**: Trained ML models
- **SQLite**: Feedback database for continuous learning

**Deployment**: Local-first, fully offline capable
- No API keys required (Ollama is optional)
- No internet connection needed
- No cloud storage costs
- Docker-ready for containerization

---

## Machine Learning Model: Data Quality Classifier

### Overview

The project includes a machine learning classifier that automatically detects and scores data quality on the current pipeline dataset.

### Model Architecture

**Algorithm**: Random Forest Classifier
- **Estimators**: 50 trees
- **Max Depth**: 3 (prevents overfitting)
- **Max Features**: 'sqrt' (feature subset selection)
- **Random State**: 42 (reproducibility)

### Current Dataset

**Primary Dataset**: `raw_data.csv`
- Complete dataset produced by the pipeline before downstream cleaning and feature extraction
- Validation-time features are computed from this dataset for scoring

### Performance Metrics

**Holdout Test Set Comparison**:

| Model | Accuracy | Precision | Recall | F1 |
|------|----------:|----------:|-------:|---:|
| Random Forest | 94.15% | 92.34% | 97.47% | 94.84% |
| XGBoost | 95.26% | 93.72% | 97.98% | 95.80% |

The test-set results come from the final Random Forest vs. XGBoost comparison notebook and reflect the held-out test split used for the published model artifacts.

### Feature Engineering (8 Features)

The quality classifier extracts **8 core features** that are used for:
1. **ML Model Predictions** - Real-time quality scoring during validation
2. **Continuous Learning** - Stored with each feedback for model retraining

**Features**:
1. `missing_ratio`: Percentage of missing values
2. `duplicate_ratio`: Percentage of duplicate rows
3. `numeric_ratio`: Proportion of numeric columns
4. `constant_cols`: Count of constant-value columns
5. `norm_variance`: Normalized variance across numeric columns
6. `skewness`: Average skewness across numeric columns
7. `cardinality_ratio`: Proportion of unique values per column
8. `mean_kurtosis`: Average kurtosis across numeric columns

**Usage**:
- Extracted during validation phase
- Cached with MD5 hash of dataset for deduplication
- Submitted with user feedback for retraining
- Stored with the dataset hash for future model updates

### Real-World Validation

**Tested Datasets**:

1. **Current Pipeline Dataset** (`raw_data.csv`)
  - Quality Prediction: **GOOD** on the held-out test split
  - Missing Data: 0% | Duplicates: 0%
  - ✓ Correctly classified as clean

### Model Files

- **Production Model**: `models/best_model.pkl`
  - Uses the current production feature set and model metadata

### Usage Example

```python
import pickle
import pandas as pd

# Load trained model
with open('models/best_model.pkl', 'rb') as f:
    model = pickle.load(f)

# Load your dataset
df = pd.read_csv('your_data.csv')

# Extract features (matching the 8-feature pipeline)
features = extract_quality_features(df)  # See feature engineering above

# Predict quality (0 = Bad, 1 = Good)
prediction = model.predict([features])
confidence = model.predict_proba([features])[0]

print(f"Quality: {'GOOD' if prediction[0] == 1 else 'BAD'}")
print(f"Confidence: {max(confidence)*100:.1f}%")
```

### Key Insights

1. **Current Dataset First**: The classifier is documented around the pipeline's `raw_data.csv` output and its validation-time feature extraction.

2. **Holdout Evaluation Only**: The README now reports final test-set metrics instead of older cross-validation summaries.

3. **Benchmark Comparison**: XGBoost edges out Random Forest on the held-out test split, but both remain strong.

---

## Continuous Learning Pipeline: Model Improvement Through User Feedback

### Overview

Sana implements a **complete feedback loop** that allows the quality classifier to improve with real-world usage. Every user provides implicit training data that makes the model smarter for future users.

### Architecture

```
FEEDBACK FLOW
═════════════════════════════════════════════════════════════════════

1. VALIDATION
   User uploads CSV → Pipeline validates → 8 ML features extracted
   └→ Features cached: {run_id}/features.json with MD5 hash

2. FEATURE RETRIEVAL  
   Frontend loads data → Calls GET /api/features/{run_id}
   └→ Receives: {features: [8 floats], dataset_hash: string}

3. FEEDBACK SUBMISSION
   User rates quality: "Poor" | "Fair" | "Good" | "Excellent"
   └→ Submits: {dataset_hash, predicted_score, actual_quality, features}
   └→ All 8 features included in submission

4. STORAGE & TRIGGER CHECK
   FeedbackDB.save() → SQLite database
   └→ Check retrain trigger: (count == 1) or (count >= 5 and count % 5 == 0)
   └→ Triggers at: 1, 5, 10, 15, 20, 25, 30... feedbacks

5. MODEL RETRAINING (on trigger)
   Count reached trigger point:
  ├→ Load current dataset snapshot
   ├→ Load feedback samples (validated, 8-feature only)
  ├→ Combine with validated feedback samples
   ├→ Train: RandomForestClassifier on combined data
  ├→ Validate on the held-out test split
   └→ Save: New model if accuracy improved

6. MODEL DEPLOYMENT
   After successful retrain:
   ├→ Save new model to disk
   ├→ Log metrics (CV score, feedback count, samples used)
   ├→ Clean old feedback (keep last 100 records)
   └→ Next upload uses improved model

7. USER FEEDBACK
   API returns: {status, feedback_count, cv_score, message}
   ├→ "Feedback stored. 4 more feedbacks until next retrain."
   └→ Or: "✓ Model Retrained! CV Score: 78.5%"
```

### Retrain Triggers

The model retrains at optimal points to balance learning and performance:

| Feedback Count | Action | Rationale |
|---|---|---|
| 1 | ✓ Retrain | Cold start learning—learn from first correction |
| 2-4 | Store only | Accumulate more diverse samples |
| 5 | ✓ Retrain | First milestone: 5 diverse feedback points |
| 6-9 | Store only | Continue accumulation |
| 10 | ✓ Retrain | Double the feedback, enough for stable patterns |
| 15, 20, 25, 30... | ✓ Retrain | Every 5 feedbacks thereafter |

**Advantage**: Model improves early (1st feedback) and frequently (every 5), not just at arbitrary thresholds.

### Feature Caching & Deduplication

All 8 features are cached by dataset hash to prevent redundant recomputation:

```python
# During validation
features = [0.15, 0.02, 0.8, 0.3, 0.12, 0.45, 0.55, 0.98]
dataset_hash = MD5(cleaned_data.csv)  # "a1b2c3d4e5f6..."
FeatureCache.save_features(run_dir, features, dataset_hash)

# During feedback
GET /api/features/{run_id}
→ Returns cached features with hash
→ Frontend uses same hash for deduplication
→ Same dataset always produces same features
```

**Benefits**:
- ✅ Consistent feature extraction across pipeline
- ✅ No duplicate training data in feedback database
- ✅ Verifiable: users can audit feedback used for retraining
- ✅ 60% faster feature retrieval (cached from disk)

### Invalid Feedback Handling

The system gracefully handles incomplete or incorrect feedback:

```python
def get_feedback_for_retraining():
    for features_json, user_label in database:
        features = json.loads(features_json)
        
        # Validate: must have exactly 8 features
        if features and len(features) == 8:
            use_for_training()  ✓
        else:
            log_warning(f"Skipping: {len(features)} items (need 8)")  ⚠️
```

**Result**: Invalid records never poison the training data, but are logged for debugging.

### Performance Impact

- **Feedback Submission**: <100ms (SQLite insert)
- **Feature Retrieval**: 5-10ms (JSON file read from cache)
- **Model Retrain**: 5-15 seconds (depends on feedback count)
  - 1-10 feedbacks: ~5 seconds
  - 50+ feedbacks: ~10-15 seconds
- **No Performance Degradation**: Retrain runs async, doesn't block user uploads

### Database Lifecycle

```
Initial State:
  feedback.db (empty)

After 1st Feedback:
  - Record 1 stored with features
  - Retrain triggered
  - Model improved from 1 data point

After 5 Feedbacks:
  - Records 1-5 stored
  - Retrain triggered again
  - Model improved from 5 new points

After 100+ Feedbacks:
  - Database contains up to last 100 records
  - Cleanup triggered after successful retrain
  - Old records deleted to conserve storage
  - Last 100 retained for potential future retraining
```

### API Endpoints for Feedback

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/feedback` | POST | Submit user feedback with features |
| `/api/features/{run_id}` | GET | Retrieve cached ML features for a run |
| `/api/data-hash` | POST | Compute MD5 hash of uploaded file |
| `/api/feedback/stats` | GET | Get feedback loop statistics |
| `/api/feedback/health` | GET | Check feedback system health |

### Example Workflow

**User A uploads messy employee data (1,020 rows)**
- Validator scores: 65.3% quality
- Features extracted and cached
- User rates: "Actually terrible" (label=0)
- Feedback submitted with features
- **Count=1 → RETRAIN TRIGGERED**
- Model retrains on: 80 original + 1 feedback sample
- New CV score: 94.2% (improved understanding of quality patterns)
- Next user B uploads similar data → Gets more accurate score

**User B gets better predictions because User A provided feedback.**

### Monitoring & Analytics

View feedback loop health:

```bash
# Count feedback records
sqlite3 backend/data/feedback.db "SELECT COUNT(*) FROM feedback;"

# View recent feedback
sqlite3 backend/data/feedback.db \
  "SELECT predicted_score, actual_label, timestamp \
   FROM feedback ORDER BY id DESC LIMIT 10;"

# View retrain history
tail backend/runs/latest/model_metrics.jsonl
```

---



## By the Numbers

| Metric | Result |
|--------|--------|
| **Data Quality Scores** | 55–97% (varies by dataset patterns) |
| **Validation Confidence** | 97.8% (on test datasets) |
| **Processing Speed** | 8–15 seconds (full pipeline) |
| **Charts Auto-Generated** | 16+ per dataset |
| **Quality Checks Passed** | 38/38 on clean data |
| **Missing Value Detection** | 100% accuracy |
| **Duplicate Detection** | 100% accuracy |
| **Outlier Flagging** | IQR-based, automatic |
| **ML Model Accuracy (Initial)** | 95.26% (holdout test) |
| **ML Features Extracted** | 8 per validation (cached by hash) |
| **Feedback Latency** | <100ms submission, 5-10ms retrieval |
| **Auto-Retrain Triggers** | At 1st feedback, then every 5th (1,5,10,15,20...) |
| **Feedback Records Kept** | Last 100 (auto-cleanup after retrain) |
| **Data Preparation Time Saved** | 80% of research prep automated + continuous learning |
| **Continuous Improvement** | Model retrains automatically as users provide feedback |

### Real-World Examples

**Accenture Stock History** (20 years, 7,221 rows)
- ✅ Quality Score: 94.6%
- ✅ Confidence: 97.8%
- ✅ Checks Passed: 38/38
- ✅ Processing Time: <15 seconds
- ✅ Charts Generated: 16+

**Messy Survey Data** (1,020 rows)
- ✅ Duplicates Removed: 47
- ✅ Missing Values Filled: 1,020+ values
- ✅ Outliers Flagged: 238 (shown in red)
- ✅ Quality Score: 89.3%
- ✅ Time Saved vs Manual: 240+ minutes

---

## The Bottom Line

**80% of research prep, automated. Continuous learning. Zero learning curve.**

Transform from: *"Ugh, I need to spend the whole weekend cleaning this data"*  
To: *"Okay, my data is validated and ready to use."*

One upload. Five agents. User feedback. Continuous improvement.

**Less time fighting the data. More time doing the research.**

### What Makes This Different

Most data tools clean your data *once*. Sana keeps improving:
- ✅ First feedback triggers immediate retraining
- ✅ Every 5 feedbacks, the model gets smarter
- ✅ Future users benefit from past users' corrections
- ✅ Your data quality scores become more accurate over time

---

Built by students, for students. No paywalls. No cloud lock-in. No BS.

**Array Potter. Sana All May Label. Research, reinvented.**
