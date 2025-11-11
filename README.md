# Maven-Dependency-Crawler — regenerate the dataset

This repository contains crawlers that collect dependency metadata from several public repositories and a small pipeline to combine them into a single dependency dataset. This README focuses on how to run the code to regenerate the dataset and the schema/format of the produced dataset.

IMPORTANT: The canonical final dataset used in this project is `MavCrawl_dataset.json` located at the repository root.

## Quick overview

- Crawlers and their typical outputs:
  - `atlassian_repo_crawler/` → `atlassian_repo_crawler/atlassian_dependencies.json`
  - `cloudera_repo_crawler/` → `cloudera_repo_crawler/cloudera_dependencies.json`
  - `google_repo_crawler/` → `google_repo_crawler/google_repo_dataset.json`
  - `mavenCentral_repo_crawler/` → `mavenCentral_repo_crawler/mavenCentral_dependencies.json`
- `combine_datasets.py` reads those JSON files and writes `MavCrawl_dataset.json`.

## Prerequisites

- Python 3.8+
- A `requirements.txt` file exists at the repo root 
- A `.env` file at the repository root containing a `MONGO_URI` entry (used by the crawlers). Example (don't commit secrets):
   ```properties
   MONGO_URI="mongodb+srv://<username>:<password>@cluster.example.net/?retryWrites=true&w=majority"
   ```
 - Maven — some crawler steps construct a temporary `pom.xml` and run `mvn dependency:tree`.
 - Gradle — used by the Google crawler to run Gradle dependency commands when extracting Gradle artifacts.

## Reproducible run (PowerShell)

Run the following from the repository root.

```powershell
# 1) (optional) create & activate a venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) install dependencies
python -m pip install -r requirements.txt

# 3) run each crawler (order not important). 
# Make sure your `.env` exists and contains `MONGO_URI` before running the crawlers.
python .\atlassian_repo_crawler\atlassianCrawler.py
python .\cloudera_repo_crawler\cloudEraCrawler.py
python .\google_repo_crawler\google_crawler.py
python .\mavenCentral_repo_crawler\mavenCrawler.py

# 4) combine the generated files into a single dataset
python .\combine_datasets.py

```

Notes:
- If the combine script fails because files are missing, ensure each crawler ran successfully and that the JSON files are present at the paths declared in `combine_datasets.py` (see `DATASET_DIRS`).
- The crawlers may depend on network access; check their individual folders for additional settings.

## Files produced by crawlers

- `atlassian_repo_crawler/atlassian_dependencies.json`
- `cloudera_repo_crawler/cloudera_dependencies.json`
- `google_repo_crawler/google_repo_dataset.json`
- `mavenCentral_repo_crawler/mavenCentral_dependencies.json`
- `MavCrawl_dataset.json` (output from `combine_datasets.py` — final dataset used by this repo)

## Dataset schema (each element in `MavCrawl_dataset.json`)

Each record in the final JSON array represents one artifact. Common fields and their interpretation:

- `_id` (string)
  - Unique identifier for the artifact, usually `group:artifact:version`.
  - Example: `activemq:activemq-core:3.2.4`

- `origin_repository` (array of strings)
  - Which source(s) reported this artifact (e.g., "Maven Central", "Cloudera", "Atlassian", "Google"). Multiple crawlers can contribute and this will be a list.
  - Example: `["Maven Central", "Cloudera"]`

- `last_modified` (string)
  - Timestamp reported by the source when available (datetime string). Some entries may be the literal string `"Unknown"`.
  - Example: `"2006-07-18 02:00"` or `"Unknown"`

- `jar_size` (string)
  - The size of the artifact as reported by the source (string). May be `"Unknown"`.
  - Example: `"992898"`

- `description` (string)
  - Short textual description extracted from the source/POM. May be `"Unknown"`.

- `direct_dependencies` (array)
  - An array of strings representing direct dependencies. Each element commonly follows the pattern `group:artifact:version:scope`.
  - Example: `["junit:junit:4.8.2:compile"]`

- `source_code_url` (string)
  - URL pointing to the project's source repository when available. May be `"Unknown"`.

- `parent_module` (string)
  - If the artifact is a child in a multi-module project, the parent module identifier. May be `"Unknown"`.

- `child_modules` (array)
  - List of child module identifiers (if present).

Notes on variations and data quality:
- Sources use the literal value `"Unknown"` for missing data.
- `_id` parsing: split `_id` by `:` to extract group, artifact and version, but be prepared for occasional extra elements (classifiers) or malformed entries.

## Troubleshooting

- If a crawler hangs or fails: run it directly and inspect console output. Check for network timeouts or rate limits.
- If `combine_datasets.py` fails with a `FileNotFoundError`, verify the dataset files exist at the paths defined in the `DATASET_DIRS` dictionary at the top of `combine_datasets.py`.

## Optional steps

- `generate_graphs.py` can create visualizations from a built dataset. Run it after you have `MavCrawl_dataset.json`.

---