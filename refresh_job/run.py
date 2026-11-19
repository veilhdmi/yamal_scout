"""Scheduled data refresh: ESPN -> BigQuery raw table (ingest_espn.py) -> dbt build. Runs as a Cloud Run Job.

The image holds only the dependencies. The code comes from the latest `main` of both GitHub repos, downloaded at
the start of every run, so a merge to main takes effect on the next run without rebuilding the image.
Any failure (ingest error, dbt error-severity test) exits non-zero, so the execution shows as failed in Cloud Run.
"""

import io
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPOS = {
    "app": "https://github.com/veilhdmi/yamal_scout/archive/refs/heads/main.tar.gz",
    "dbt": "https://github.com/veilhdmi/dbt_yamal_scout/archive/refs/heads/main.tar.gz",
}

PROFILES = """yamal_scout:
  target: prod
  outputs:
    prod:
      type: bigquery
      method: oauth            # on Cloud Run this is the job's service account
      project: {project}
      dataset: yamal_dev       # default schema; generate_schema_name routes models to yamal_staging / yamal_marts
      location: US
      threads: 4
      job_execution_timeout_seconds: 300
      job_retries: 1
      priority: interactive
      maximum_bytes_billed: 1000000000
"""


def fetch(url: str, dest: Path) -> Path:
    """Download a GitHub tarball and return the extracted top-level folder."""
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    return next(dest.iterdir())


def run(cmd: list[str], cwd: Path, env: dict) -> None:
    print(f"\n$ {' '.join(cmd)}   (in {cwd.name})", flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def main() -> None:
    project = os.environ["GCP_PROJECT"]
    work = Path(tempfile.mkdtemp())
    app_dir = fetch(REPOS["app"], work / "app")
    dbt_dir = fetch(REPOS["dbt"], work / "dbt")
    profiles = work / "profiles"
    profiles.mkdir()
    (profiles / "profiles.yml").write_text(PROFILES.format(project=project))
    env = {**os.environ, "DBT_PROFILES_DIR": str(profiles), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}

    run([sys.executable, "ingest_espn.py"], app_dir, env)
    run(["dbt", "deps"], dbt_dir, env)
    run(["dbt", "build"], dbt_dir, env)  # warn-severity tests don't fail the run; error-severity ones do
    print("\nRefresh finished: raw table reloaded and dbt marts rebuilt.", flush=True)


if __name__ == "__main__":
    main()
