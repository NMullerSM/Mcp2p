"""Worker de proceso real para T01: reclama un lease con un retardo inyectado tras el SELECT,
para abrir de forma determinista la ventana SELECT->UPDATE entre procesos."""
import json
import os
import sys
import time
from contextlib import contextmanager

from src.storage.db import DatabaseManager

db_path, project_uuid, card_id, node_uuid, go_file = sys.argv[1:6]
DELAY = float(os.environ.get("MCP2P_TEST_SELECT_DELAY", "0.3"))


class _SlowConn:
    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, *args):
        cur = self._c.execute(sql, *args)
        if "SELECT assignee_node_uuid" in sql:
            time.sleep(DELAY)
        return cur

    def __enter__(self):
        self._c.__enter__()
        return self

    def __exit__(self, *exc):
        return self._c.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._c, name)


db = DatabaseManager(db_path)
_orig = DatabaseManager.get_connection


@contextmanager
def _slow(self):
    with _orig(self) as c:
        yield _SlowConn(c)


DatabaseManager.get_connection = _slow

while not os.path.exists(go_file):
    time.sleep(0.005)
try:
    card = db.claim_card_lease(project_uuid, card_id, node_uuid, 30)
    print(json.dumps({"node": node_uuid, "result": "ok", "assignee": card.get("assignee_node_uuid")}))
except PermissionError:
    print(json.dumps({"node": node_uuid, "result": "permission_error"}))
except Exception as e:
    print(json.dumps({"node": node_uuid, "result": "error", "type": type(e).__name__, "msg": str(e)}))
