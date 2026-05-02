import os
import logging
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

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
    data_dir: str = "./datasets"
    output_file: str = "training_data.csv"
    error_log_file: str = "pipeline_errors.csv"
    min_rows: int = 10               # Skip files too small to be meaningful
    null_inject_rate: float = 0.10   # Corruption: null injection rate
    duplicate_frac: float = 0.20     # Corruption: duplicate fraction
    type_scramble_prob: float = 0.30 # Corruption: type scramble probability
    outlier_inject_rate: float = 0.05
    outlier_multiplier: float = 10.0


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
def extract(file_path: str) -> Optional[pl.DataFrame]:
    """
    Lazy scan -> collect. Polars parallelises column reads automatically.
    scan_csv infers schema without loading the full file into RAM first.
    """
    try:
        df = (
            pl.scan_csv(file_path, infer_schema_length=10_000, ignore_errors=True)
            .collect()
        )
        if len(df) < CONFIG.min_rows:
            log.warning("Skipping %s - too small (%d rows).", file_path, len(df))
            return None
        return validate(df)
    except Exception as exc:
        log.error("Failed to read %s: %s", file_path, exc)
        return None


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

    # Duplicates
    features["duplicate_row_ratio"] = float(
        (num_rows - df.unique().height) / max(num_rows, 1)
    )

    # Uniqueness / cardinality
    unique_ratios = [df[c].n_unique() / max(num_rows, 1) for c in df.columns]
    features["avg_unique_ratio"]             = float(np.mean(unique_ratios))
    features["min_unique_ratio"]             = float(np.min(unique_ratios))
    features["num_high_cardinality_columns"] = int(sum(r > 0.9 for r in unique_ratios))

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
        features["avg_outlier_ratio"] = float(np.mean(outlier_ratios))
        features["max_outlier_ratio"] = float(np.max(outlier_ratios))

        # Skewness & kurtosis via native Polars expressions
        skews = [num_df.select(pl.col(c).skew()).item()    for c in numeric_cols]
        kurts = [num_df.select(pl.col(c).kurtosis()).item() for c in numeric_cols]
        skews = [s for s in skews if s is not None]
        kurts = [k for k in kurts if k is not None]
        features["avg_skewness"] = float(np.mean(skews)) if skews else 0.0
        features["avg_kurtosis"] = float(np.mean(kurts)) if kurts else 0.0
    else:
        features.update(
            avg_outlier_ratio=0.0, max_outlier_ratio=0.0,
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
    rng = np.random.default_rng()
    n_rows, _ = df.shape

    # Inject nulls
    null_mask = rng.random((n_rows, len(df.columns))) < CONFIG.null_inject_rate
    corrupted_cols = []
    for i, col in enumerate(df.columns):
        series = df[col].to_list()
        for row_idx in np.where(null_mask[:, i])[0]:
            series[row_idx] = None
        corrupted_cols.append(pl.Series(col, series, dtype=df[col].dtype))
    df = pl.DataFrame(corrupted_cols)

    # Duplicate rows
    n_dupes = max(1, int(n_rows * CONFIG.duplicate_frac))
    dup_idx = rng.choice(n_rows, size=n_dupes, replace=True)
    df = pl.concat([df, df[dup_idx]], rechunk=True)

    # Scramble numeric -> string (simulate type mixing)
    numeric_cols = df.select(cs.numeric()).columns
    scramble_cols = [
        c for c in numeric_cols if rng.random() < CONFIG.type_scramble_prob
    ]
    if scramble_cols:
        df = df.with_columns([pl.col(c).cast(pl.Utf8) for c in scramble_cols])

    # Inject statistical outliers into remaining numeric columns
    remaining_numeric = df.select(cs.numeric()).columns
    for col in remaining_numeric:
        n_out = max(1, int(len(df) * CONFIG.outlier_inject_rate))
        out_idx = rng.choice(len(df), size=n_out, replace=False)
        col_mean = df[col].drop_nulls().mean() or 1.0
        outlier_val = col_mean * CONFIG.outlier_multiplier
        series = df[col].to_list()
        for idx in out_idx:
            series[idx] = outlier_val
        df = df.with_columns(pl.Series(col, series, dtype=df[col].dtype))

    return df


# =========================
# PER-FILE PROCESSOR
# =========================
def process_file(file_path: str) -> tuple[list[dict], Optional[dict]]:
    """
    Extract -> compute features for good + corrupted sample.
    Polars internally saturates all CPU cores per file via its thread pool,
    so no ProcessPoolExecutor overhead is needed.
    """
    samples: list[dict] = []
    source = os.path.basename(file_path)
    ts     = datetime.now(timezone.utc).isoformat()

    df = extract(file_path)
    if df is None:
        return [], {"source": source, "reason": "extract failed or too small"}

    try:
        good = compute_features(df)
        good.update(label=1, source=source, timestamp=ts)
        samples.append(good)

        bad = compute_features(corrupt_data(df))
        bad.update(label=0, source=source, timestamp=ts)
        samples.append(bad)
    except Exception as exc:
        return samples, {"source": source, "reason": str(exc)}

    return samples, None


# =========================
# LOAD
# =========================
def load(features_list: list[dict], errors: list[dict]) -> None:
    out_df = pl.DataFrame(features_list)
    out_df.write_csv(CONFIG.output_file)
    log.info("Saved %d samples -> %s", len(out_df), CONFIG.output_file)

    if errors:
        pl.DataFrame(errors).write_csv(CONFIG.error_log_file)
        log.warning("Logged %d errors -> %s", len(errors), CONFIG.error_log_file)


# =========================
# PIPELINE
# =========================
def run_pipeline() -> None:
    csv_files = [
        os.path.join(CONFIG.data_dir, f)
        for f in os.listdir(CONFIG.data_dir)
        if f.endswith(".csv")
    ]
    if not csv_files:
        log.warning("No CSV files found in %s", CONFIG.data_dir)
        return

    log.info("Processing %d files (Polars engine, parallel columns)...", len(csv_files))

    all_samples: list[dict] = []
    all_errors:  list[dict] = []

    # Polars already saturates all cores per file via its internal thread pool.
    # A simple loop is sufficient - no ProcessPoolExecutor overhead needed.
    for fp in csv_files:
        samples, error = process_file(fp)
        all_samples.extend(samples)
        if error:
            all_errors.append(error)

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