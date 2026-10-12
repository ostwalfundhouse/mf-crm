"""Database access.

SupabaseDB talks to Supabase over HTTPS with the service_role key (used by the
GitHub Action). PsqlDB talks to a local PostgreSQL through the psql command
(used for testing on a developer machine).
"""
import json
import os
import subprocess
import tempfile
import urllib.parse

import requests


class SupabaseDB:
    def __init__(self, url, key, bucket="inbox"):
        self.url = url.rstrip("/")
        self.bucket = bucket
        self.s = requests.Session()
        self.s.headers.update({"apikey": key})
        if key.startswith("eyJ"):
            # Legacy JWT keys also go in the Authorization header. The newer
            # "sb_secret_..." keys must only be sent as apikey.
            self.s.headers["Authorization"] = f"Bearer {key}"

    def rpc(self, name, **params):
        r = self.s.post(f"{self.url}/rest/v1/rpc/{name}", json=params, timeout=300)
        if r.status_code >= 300:
            raise RuntimeError(f"{name} failed: {r.status_code} {r.text[:500]}")
        return r.json()

    def select(self, table, query="select=*"):
        out, start, page = [], 0, 1000
        while True:
            r = self.s.get(f"{self.url}/rest/v1/{table}?{query}",
                           headers={"Range-Unit": "items", "Range": f"{start}-{start + page - 1}"},
                           timeout=120)
            r.raise_for_status()
            rows = r.json()
            out.extend(rows)
            if len(rows) < page:
                return out
            start += page

    def log_start(self, file_name):
        r = self.s.post(f"{self.url}/rest/v1/import_log",
                        json={"file_name": file_name, "status": "running"},
                        headers={"Prefer": "return=representation"}, timeout=60)
        r.raise_for_status()
        return r.json()[0]["id"]

    def log_finish(self, log_id, **fields):
        fields["finished_at"] = _utc_now()
        r = self.s.patch(f"{self.url}/rest/v1/import_log?id=eq.{log_id}", json=fields, timeout=60)
        r.raise_for_status()

    # Storage -------------------------------------------------------------
    def list_files(self, prefix):
        r = self.s.post(f"{self.url}/storage/v1/object/list/{self.bucket}",
                        json={"prefix": prefix, "limit": 1000, "offset": 0,
                              "sortBy": {"column": "name", "order": "asc"}}, timeout=60)
        r.raise_for_status()
        return [f"{prefix}{o['name']}" for o in r.json() if o.get("id")]

    def download(self, path):
        r = self.s.get(f"{self.url}/storage/v1/object/{self.bucket}/{urllib.parse.quote(path)}",
                       timeout=300)
        r.raise_for_status()
        return r.content

    def move(self, src, dst):
        r = self.s.post(f"{self.url}/storage/v1/object/move",
                        json={"bucketId": self.bucket, "sourceKey": src, "destinationKey": dst},
                        timeout=60)
        r.raise_for_status()


class PsqlDB:
    """Local PostgreSQL through psql, for tests."""

    def __init__(self, dbname):
        self.dbname = dbname

    def _sql(self, sql):
        res = subprocess.run(["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-d", self.dbname,
                              "-c", sql], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr)
        return res.stdout.strip()

    def _json_file(self, obj):
        fd, path = tempfile.mkstemp(suffix=".json", dir="/tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, default=str)
        os.chmod(path, 0o644)
        return path

    def rpc(self, name, **params):
        if name == "transactions_for_keys":
            path = self._json_file(params["keys"])
            try:
                out = self._sql(
                    "select coalesce(json_agg(t), '[]') from transactions_for_keys("
                    f"array(select jsonb_array_elements_text(pg_read_file('{path}')::jsonb))) t")
            finally:
                os.remove(path)
            return json.loads(out)
        path = self._json_file(params["rows"])
        try:
            out = self._sql(f"select {name}(pg_read_file('{path}')::jsonb)")
        finally:
            os.remove(path)
        return json.loads(out)

    def select(self, table, query="select=*"):
        return json.loads(self._sql(f"select coalesce(json_agg(t), '[]') from {table} t"))

    def log_start(self, file_name):
        safe = file_name.replace("'", "''")
        return int(self._sql(
            f"insert into import_log (file_name, status) values ('{safe}', 'running') returning id"))

    def log_finish(self, log_id, **fields):
        sets = ", ".join(f"{k} = {_lit(v)}" for k, v in fields.items())
        self._sql(f"update import_log set {sets}, finished_at = now() where id = {int(log_id)}")


def _lit(v):
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _utc_now():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat()
