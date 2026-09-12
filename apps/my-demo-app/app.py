import os
import sys
import logging
from databricks import sql
from databricks.sdk.core import Config
from flask import Flask, jsonify, render_template

DATABRICKS_APP_NAME = os.environ.get("DATABRICKS_APP_NAME", "grid-app")
DATABRICKS_WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SITE_OVERVIEW_VIEW = os.environ.get("SITE_OVERVIEW_VIEW", "")
SITE_INCIDENT_DETAIL_VIEW = os.environ.get("SITE_INCIDENT_DETAIL_VIEW", "")
SITE_MAINTENANCE_DETAIL_VIEW = os.environ.get("SITE_MAINTENANCE_DETAIL_VIEW", "")

app = Flask(__name__)
cfg = Config()

logger = logging.getLogger(DATABRICKS_APP_NAME)
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s")
    )
    logger.addHandler(handler)


def get_connection():
    return sql.connect(
        server_hostname=cfg.host,
        http_path=f"/sql/1.0/warehouses/{DATABRICKS_WAREHOUSE_ID}",
        credentials_provider=lambda: cfg.authenticate
    )

def query_all(statement: str, params: tuple = ()):
    def rows_to_dicts(cursor):
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(statement, params)
            return rows_to_dicts(cursor)


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/sites")
def get_sites():
    logger.info("site list requested")

    rows = query_all(f"""
        SELECT site_id, site_name, region, site_type,
          city, state_province, capacity_mw, criticality,
          measured_at, load_mw, load_pct, voltage_kv,
          temperature_c, health_score, status, active_incidents, next_maintenance
        FROM {SITE_OVERVIEW_VIEW}
        ORDER BY
          CASE status
            WHEN 'Critical' THEN 1
            WHEN 'Warning' THEN 2
            WHEN 'Elevated' THEN 3
            ELSE 4
          END,
          load_pct DESC
    """)

    return jsonify(rows)

@app.route("/api/summary")
def get_summary():
    logger.info("summary requested")

    rows = query_all(f"""
        SELECT
          COUNT(*) AS sites_monitored,
          SUM(CASE WHEN status = 'Critical' THEN 1 ELSE 0 END) AS critical_sites,
          SUM(active_incidents) AS active_incidents,
          ROUND(AVG(load_pct), 1) AS average_load_pct
        FROM {SITE_OVERVIEW_VIEW}
    """)

    return jsonify(rows[0])

@app.route("/api/sites/<site_id>")
def get_site(site_id):
    logger.info("site detail requested site_id=%s", site_id)

    rows = query_all(
        f"""
        SELECT *
        FROM {SITE_OVERVIEW_VIEW}
        WHERE site_id = ?
        """,
        (site_id,),
    )

    if not rows:
        return jsonify({"error": "Site not found"}), 404

    return jsonify(rows[0])

@app.route("/api/sites/<site_id>/details")
def get_site_details(site_id):
    logger.info("site detail bundle requested site_id=%s", site_id)

    site_rows = query_all(
        f"""
        SELECT *
        FROM {SITE_OVERVIEW_VIEW}
        WHERE site_id = ?
        """,
        (site_id,),
    )

    if not site_rows:
        return jsonify({"error": "Site not found"}), 404

    incident_rows = query_all(
        f"""
        SELECT
          incident_id,
          opened_at,
          severity,
          incident_type,
          description,
          status
        FROM {SITE_INCIDENT_DETAIL_VIEW}
        WHERE site_id = ?
        ORDER BY
          CASE severity
            WHEN 'Critical' THEN 1
            WHEN 'High' THEN 2
            WHEN 'Medium' THEN 3
            ELSE 4
          END,
          opened_at DESC
        """,
        (site_id,),
    )

    maintenance_rows = query_all(
        f"""
        SELECT
          event_id,
          scheduled_start,
          scheduled_end,
          maintenance_type,
          assigned_team,
          status
        FROM {SITE_MAINTENANCE_DETAIL_VIEW}
        WHERE site_id = ?
        ORDER BY scheduled_start ASC
        """,
        (site_id,),
    )

    return jsonify({
        "site": site_rows[0],
        "incidents": incident_rows,
        "maintenance": maintenance_rows,
    })
    
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
    )