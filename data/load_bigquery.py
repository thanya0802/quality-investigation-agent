"""Copy the loaded real data (local SQLite) into BigQuery with load jobs (free, unlike streaming).
Run after `make real` and `terraform apply`, authenticated as yourself:
  gcloud auth application-default login
  GCP_PROJECT=<your-project> python data/load_bigquery.py
The tables (schema, partitioning, clustering) are created by Terraform; this only replaces rows."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qia import config, warehouse            # noqa: E402

def main():
    from google.cloud import bigquery
    if not config.GCP_PROJECT:
        raise SystemExit("Set GCP_PROJECT")
    client = bigquery.Client(project=config.GCP_PROJECT)
    for table, extra in (("complaints", lambda r: {"received_dt": r["received_date"]}), ("recalls", lambda r: {})):
        rows = warehouse.query(f"SELECT * FROM {table}")      # local SQLite
        rows = [{**r, **extra(r)} for r in rows]
        ref = f"{config.GCP_PROJECT}.{config.BQ_DATASET}.{table}"
        job = client.load_table_from_json(rows, ref, job_config=bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE", schema=client.get_table(ref).schema))
        job.result()
        print(f"loaded {len(rows):,} rows into {ref}")

if __name__ == "__main__":
    main()
