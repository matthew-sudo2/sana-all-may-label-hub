import os
import logging
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

import numpy as np
import polars as pl
import polars.selectors as cs

warnings.filterwarnings("ignore")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# =========================
# CONFIG
# =========================
@dataclass
class PipelineConfig:
    # Use paths relative to the project root
    data_dir: str = "./datasets"
    output_file: str = "training_data.csv"
    error_log_file: str = "pipeline_errors.csv"
    train_file: str = "train_data.csv"
    val_file: str = "val_data.csv"
    test_file: str = "test_data.csv"
    
    min_rows: int = 10               # Skip files too small to be meaningful
    
    # Corruption settings (expressed as ranges/probs for stochasticity)
    null_inject_range: tuple = (0.02, 0.15)
    duplicate_frac_range: tuple = (0.05, 0.25)
    type_scramble_prob: float = 0.40 
    outlier_inject_range: tuple = (0.01, 0.08)
    outlier_multiplier_range: tuple = (5.0, 20.0)
    
    # Probability that a specific corruption type is applied at all
    corruption_apply_prob: float = 0.7 
    
    split_ratios: tuple = (0.7, 0.15, 0.15) # Train, Val, Test


CONFIG = PipelineConfig()


# =========================
# SCHEMA VALIDATION  (native Polars — no Pandera)
# =========================
def validate(df: pl.DataFrame) -> pl.DataFrame:
    """
    Drop rows where ALL values are null (empty rows).
    For every numeric column, replace non-finite values (inf / -inf) with null
    so downstream stats are never corrupted by sentinel floats.
    Returns a cleaned DataFrame.
    """
    original_len = len(df)

    # Replace +-inf with null in numeric columns
    num_cols = [c for c in df.columns if df[c].dtype in (pl.Float32, pl.Float64)]
    if num_cols:
        df = df.with_columns([
            pl.when(pl.col(c).is_infinite()).then(None).otherwise(pl.col(c)).alias(c)
            for c in num_cols
        ])

    # Drop fully-null rows
    df = df.filter(~pl.all_horizontal(pl.all().is_null()))

    dropped = original_len - len(df)
    if dropped:
        log.warning("Validation dropped %d rows (empty or non-finite).", dropped)

    return df


# =========================
# EXTRACT
# =========================
def process_file_batched(file_path: str) -> tuple[list[dict], list[dict]]:
    """
    Streams through a file in batches to avoid RAM issues.
    Each batch yields one 'Good' and one 'Bad' sample.
    """
    samples: list[dict] = []
    errors: list[dict] = []
    source = os.path.basename(file_path)
    ts     = datetime.now(timezone.utc).isoformat()
    
    try:
        # Use batched reader to stream through the file
        reader = pl.read_csv_batched(
            file_path, 
            batch_size=100_000, 
            ignore_errors=True, 
            truncate_ragged_lines=True,
            infer_schema_length=10000
        )
        
        batch_num = 1
        while True:
            batches = reader.next_batches(1)
            if batches is None:
                break
                
            df = batches[0]
            if len(df) < CONFIG.min_rows:
                continue
                
            df = validate(df)
            
            try:
                # Process Good Sample
                good = compute_features(df)
                good.update(label=1, source=f"{source}_b{batch_num}", timestamp=ts)
                samples.append(good)

                # Process Bad Sample
                bad = compute_features(corrupt_data(df))
                bad.update(label=0, source=f"{source}_b{batch_num}", timestamp=ts)
                samples.append(bad)
            except Exception as exc:
                errors.append({"source": f"{source}_b{batch_num}", "reason": str(exc)})
            
            batch_num += 1
            # For massive files, don't take INFINITE batches to keep it reasonable
            if batch_num > 50: # Limit to 5 million rows per file to maintain variety
                break
                
    except Exception as exc:
        log.error("Batch stream failed for %s: %s", file_path, exc)
        errors.append({"source": source, "reason": f"stream_init_failed: {exc}"})
        
    return samples, errors


# =========================
# TRANSFORM (FEATURE ENGINEERING)
# =========================
def compute_features(df: pl.DataFrame) -> dict:
    features: dict = {}
    num_rows, num_cols = df.shape

    features["num_rows"] = num_rows
    features["num_columns"] = num_cols

    # Column type breakdown
    numeric_cols = df.select(cs.numeric()).columns
    string_cols  = df.select(cs.string()).columns
    features["num_numeric_columns"] = len(numeric_cols)
    features["num_string_columns"]  = len(string_cols)

    # Missing values
    null_counts  = df.null_count().row(0)
    null_ratios  = [n / max(num_rows, 1) for n in null_counts]
    features["null_ratio_mean"]         = float(np.mean(null_ratios))
    features["null_ratio_max"]          = float(np.max(null_ratios))
    features["null_ratio_std"]          = float(np.std(null_ratios))
    features["num_high_null_columns"]   = int(sum(r > 0.5 for r in null_ratios))


    unique_ratios = [df[c].n_unique() / max(num_rows, 1) for c in df.columns]
    features["avg_unique_ratio"]             = float(np.mean(unique_ratios))
    features["min_unique_ratio"]             = float(np.min(unique_ratios))

    # Constant / near-constant columns
    features["num_constant_columns"] = int(
        sum(df[c].n_unique() <= 1 for c in df.columns)
    )

    # Mixed-type detection
    # In Polars each column has a strict dtype; mixed types surface as Utf8 after
    # inference. Flag string columns that contain parseable numbers (true mixing).
    mixed = 0
    for col in string_cols:
        sample = df[col].drop_nulls().head(500)
        try:
            parsed = sample.cast(pl.Float64, strict=False)
            non_null_orig   = sample.is_not_null().sum()
            non_null_parsed = parsed.is_not_null().sum()
            if 0 < non_null_parsed < non_null_orig:
                mixed += 1
        except Exception:
            pass
    features["num_mixed_type_columns"] = mixed

    # String length variance
    if string_cols:
        str_len_vars = [
            float(df[c].drop_nulls().str.len_chars().cast(pl.Float64).var() or 0.0)
            for c in string_cols
        ]
        features["avg_string_length_variance"] = float(np.mean(str_len_vars))
        features["max_string_length_variance"] = float(np.max(str_len_vars))
    else:
        features["avg_string_length_variance"] = 0.0
        features["max_string_length_variance"] = 0.0

    # Numeric distribution stats
    if numeric_cols:
        num_df = df.select(numeric_cols)

        # IQR outlier ratio - fully vectorised via Polars expressions
        outlier_exprs = []
        for col in numeric_cols:
            q1 = num_df[col].quantile(0.25, interpolation="linear") or 0.0
            q3 = num_df[col].quantile(0.75, interpolation="linear") or 0.0
            iqr = q3 - q1
            lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            outlier_exprs.append(
                ((pl.col(col) < lower) | (pl.col(col) > upper))
                .cast(pl.Float64)
                .mean()
                .alias(col)
            )
        outlier_ratios = num_df.select(outlier_exprs).row(0)
        # We drop avg/max_outlier_ratio as they are direct proxies for corruption

        # Skewness & kurtosis via native Polars expressions
        skews = [num_df.select(pl.col(c).skew()).item()    for c in numeric_cols]
        kurts = [num_df.select(pl.col(c).kurtosis()).item() for c in numeric_cols]
        skews = [s for s in skews if s is not None]
        kurts = [k for k in kurts if k is not None]
        features["avg_skewness"] = float(np.mean(skews)) if skews else 0.0
        features["avg_kurtosis"] = float(np.mean(kurts)) if kurts else 0.0
    else:
        features.update(
            avg_skewness=0.0, avg_kurtosis=0.0,
        )

    # Empty rows
    features["empty_row_ratio"] = float(
        df.filter(pl.all_horizontal(pl.all().is_null())).height / max(num_rows, 1)
    )

    return features


# =========================
# CORRUPTION ENGINE
# =========================
def corrupt_data(df: pl.DataFrame) -> pl.DataFrame:
    """
    Applies stochastic corruptions to the dataframe. 
    Each corruption type has a probability of being skipped to prevent 
    deterministic markers that lead to data leakage.
    """
    rng = np.random.default_rng()
    n_rows, _ = df.shape
    if n_rows == 0:
        return df

    # 1. Inject nulls
    if rng.random() < CONFIG.corruption_apply_prob:
        rate = rng.uniform(*CONFIG.null_inject_range)
        null_mask_cols = []
        for col in df.columns:
            mask = rng.random(n_rows) < rate
            null_mask_cols.append(
                pl.when(pl.lit(mask)).then(None).otherwise(pl.col(col)).alias(col)
            )
        df = df.with_columns(null_mask_cols)

    # 2. Duplicate rows
    if rng.random() < CONFIG.corruption_apply_prob:
        frac = rng.uniform(*CONFIG.duplicate_frac_range)
        n_dupes = max(1, int(n_rows * frac))
        dup_idx = rng.choice(n_rows, size=n_dupes, replace=True)
        df = pl.concat([df, df[dup_idx]], rechunk=True)
        n_rows = df.height # Update n_rows for subsequent steps

    # 3. Scramble numeric -> string
    if rng.random() < CONFIG.type_scramble_prob:
        numeric_cols = df.select(cs.numeric()).columns
        if numeric_cols:
            # Only scramble a random subset of numeric columns
            scramble_cols = [c for c in numeric_cols if rng.random() < 0.5]
            if scramble_cols:
                df = df.with_columns([pl.col(c).cast(pl.Utf8) for c in scramble_cols])

    # 4. Inject statistical outliers
    if rng.random() < CONFIG.corruption_apply_prob:
        remaining_numeric = df.select(cs.numeric()).columns
        if remaining_numeric:
            rate = rng.uniform(*CONFIG.outlier_inject_range)
            mult = rng.uniform(*CONFIG.outlier_multiplier_range)
            for col in remaining_numeric:
                # Use a robust mean (median) for outlier base
                col_base = df[col].drop_nulls().median() or 1.0
                outlier_val = col_base * mult * (1 if rng.random() > 0.5 else -1)
                
                mask = rng.random(len(df)) < rate
                df = df.with_columns(
                    pl.when(pl.lit(mask)).then(pl.lit(outlier_val)).otherwise(pl.col(col)).alias(col)
                )

    return df


# =========================
# PER-FILE PROCESSOR
# =========================


# =========================
# LOAD
# =========================
def load(features_list: list[dict], errors: list[dict]) -> None:
    if not features_list:
        log.warning("No samples to save.")
        return
        
    df = pl.DataFrame(features_list)
    
    # 1. Ensure Balance (Though design is 1:1, we enforce it here just in case)
    good_df = df.filter(pl.col("label") == 1)
    bad_df  = df.filter(pl.col("label") == 0)
    
    min_size = min(len(good_df), len(bad_df))
    if min_size == 0:
        log.error("Cannot create balanced dataset: missing one class entirely.")
        return
        
    df = pl.concat([
        good_df.sample(n=min_size, seed=42),
        bad_df.sample(n=min_size, seed=42)
    ]).sample(fraction=1.0, shuffle=True, seed=42) # Shuffle entire set
    
    log.info("Balanced dataset created: %d Good, %d Bad samples.", min_size, min_size)

    # 2. Split (Train / Val / Test)
    n = len(df)
    r_train, r_val, r_test = CONFIG.split_ratios
    
    idx_train = int(n * r_train)
    idx_val   = int(n * (r_train + r_val))
    
    train_df = df[:idx_train]
    val_df   = df[idx_train:idx_val]
    test_df  = df[idx_val:]
    
    # 3. Save
    train_df.write_csv(CONFIG.train_file)
    val_df.write_csv(CONFIG.val_file)
    test_df.write_csv(CONFIG.test_file)
    df.write_csv(CONFIG.output_file) # Also save the full combined set
    
    log.info("Saved splits:")
    log.info("  - Train: %d rows -> %s", len(train_df), CONFIG.train_file)
    log.info("  - Val:   %d rows -> %s", len(val_df), CONFIG.val_file)
    log.info("  - Test:  %d rows -> %s", len(test_df), CONFIG.test_file)
    log.info("  - Full:  %d rows -> %s", len(df), CONFIG.output_file)

    if errors:
        pl.DataFrame(errors).write_csv(CONFIG.error_log_file)
        log.warning("Logged %d errors -> %s", len(errors), CONFIG.error_log_file)


# =========================
# PIPELINE
# =========================
def run_pipeline() -> None:
    # Ensure datasets directory exists
    os.makedirs(CONFIG.data_dir, exist_ok=True)
    
    # Recursive discovery of CSV files in subdirectories
    csv_files = [
        str(p)
        for p in Path(CONFIG.data_dir).rglob("*.csv")
    ]
    
    if not csv_files:
        log.warning("No CSV files found in %s (checked recursively)", CONFIG.data_dir)
        return

    # Shuffle files to process to ensure diversity
    rng = np.random.default_rng(42)
    rng.shuffle(csv_files)

    log.info("Processing %d files (Polars engine, parallel columns)...", len(csv_files))

    all_samples: list[dict] = []
    all_errors:  list[dict] = []

    # Polars already saturates all cores per file via its internal thread pool.
    # A simple loop is sufficient - no ProcessPoolExecutor overhead needed.
    for i, fp in enumerate(csv_files):
        if i % 10 == 0:
            log.info("Progress: %d/%d files processed...", i, len(csv_files))
            
        samples, errors = process_file_batched(fp)
        all_samples.extend(samples)
        all_errors.extend(errors)

    load(all_samples, all_errors)
    log.info(
        "Pipeline complete. %d good + %d bad samples.",
        sum(1 for s in all_samples if s.get("label") == 1),
        sum(1 for s in all_samples if s.get("label") == 0),
    )


# =========================
# ENTRY POINT
# =========================
if __name__ == "__main__":
    run_pipeline()