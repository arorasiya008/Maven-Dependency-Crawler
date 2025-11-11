import pandas as pd
import json

# ---- STEP 1: Load datasets ----
# Directory containing JSON files
DATASET_DIRS = {"Maven Central": "mavenCentral_repo_crawler/mavenCentral_dependencies.json", "Cloudera": "cloudera_repo_crawler/cloudera_dependencies.json", "Atlassian": "atlassian_repo_crawler/atlassian_dependencies.json", "Google": "google_repo_crawler/google_repo_dataset.json"}

dfs = []

for repo_name, filename in DATASET_DIRS.items():
    df = pd.read_json(filename)
    df["origin_repository"] = repo_name
    dfs.append(df)

# ---- STEP 2: Combine all datasets ----
combined = pd.concat(dfs, ignore_index=True)

# ---- STEP 3: Merge strictly by _id ----
def merge_by_id(group):
    merged = {}
    merged["_id"] = group["_id"].iloc[0]

    # collect all repositories where this _id appeared
    merged["origin_repository"] = group["origin_repository"].unique().tolist()

    # For other fields: pick the first non-null value if available
    for col in group.columns:
        if col not in ["_id", "origin_repository"]:
            non_null_values = group[col].dropna()
            if len(non_null_values) > 0:
                merged[col] = non_null_values.iloc[0]
            else:
                merged[col] = None
    return pd.Series(merged)

final_df = combined.groupby("_id", as_index=False).apply(merge_by_id)
final_df.reset_index(drop=True, inplace=True)

# ---- STEP 4: Save to output ----
final_df.to_json("MavCrawl_dataset.json", orient="records", indent=4, force_ascii=False)

print("✅ Merging complete! Output saved as dependency_dataset.json")
print("Total unique dependencies in combined dataset:", len(final_df))
