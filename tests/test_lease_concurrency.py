"""F4: la adquisición de un lease debía ser atómica entre PROCESOS distintos (cada agente
MCP corre como un proceso propio vía stdio). Antes de F4, `threading.Lock` solo protegía
hilos del mismo proceso, así que dos procesos podían leer `assignee_node_uuid = NULL` a la
vez y ambos escribir: una condición de carrera real (TOCTOU)."""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.storage.db import DatabaseManager

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER = REPO_ROOT / "tests" / "helpers" / "lease_claim_worker.py"
N_PROCS = 6


@pytest.mark.parametrize("scenario", ["unassigned", "expired_lease"])
def test_claim_card_lease_multiprocess_single_winner(tmp_path, scenario):
    db_path = str(tmp_path / "race.db")
    db = DatabaseManager(db_path)
    proj = db.create_project("Race", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    db.create_card(p_uuid, "CARD-RACE", "race")

    for i in range(N_PROCS):
        db.register_or_update_node(f"node_{i}", f"Node {i}", f"agent_{i}")

    node_pool = list(range(N_PROCS))
    if scenario == "expired_lease":
        expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with db.get_connection() as conn:
            with conn:
                conn.execute(
                    "UPDATE kanban_cards SET status='in_progress', assignee_node_uuid=?, lease_expires_at=? "
                    "WHERE project_uuid=? AND card_id=?;",
                    ("node_0", expired, p_uuid, "CARD-RACE"),
                )
        node_pool = list(range(1, N_PROCS))

    go_file = tmp_path / "go"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "MCP2P_TEST_SELECT_DELAY": "0.3"}
    procs = [
        subprocess.Popen(
            [sys.executable, str(WORKER), db_path, p_uuid, "CARD-RACE", f"node_{i}", str(go_file)],
            cwd=str(REPO_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for i in node_pool
    ]
    time.sleep(1.5)
    go_file.write_text("go", encoding="utf-8")

    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        line = [l for l in out.strip().splitlines() if l.strip()][-1]
        results.append(json.loads(line))

    outcomes = [r["result"] for r in results]
    assert outcomes.count("ok") == 1, f"se esperaba exactamente 1 ganador, hubo {outcomes.count('ok')}: {results}"
    assert all(r in ("ok", "permission_error") for r in outcomes), results

    winner = next(r["node"] for r in results if r["result"] == "ok")
    assert db.get_card_by_id(p_uuid, "CARD-RACE")["assignee_node_uuid"] == winner


def test_claim_card_lease_threads_single_winner(tmp_path):
    """Protege contra la eliminación accidental de _write_lock: 12 hilos sobre el MISMO
    DatabaseManager, reclamando la misma tarjeta a la vez."""
    db_path = str(tmp_path / "race_threads.db")
    db = DatabaseManager(db_path)
    proj = db.create_project("RaceThreads", str(tmp_path / "ws"), init_git=False)
    p_uuid = proj["project_uuid"]
    db.create_card(p_uuid, "CARD-RACE-T", "race-threads")

    n = 12
    for i in range(n):
        db.register_or_update_node(f"tnode_{i}", f"Node {i}", f"agent_t{i}")

    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        barrier.wait()
        try:
            card = db.claim_card_lease(p_uuid, "CARD-RACE-T", f"tnode_{i}", 30)
            results[i] = ("ok", card.get("assignee_node_uuid"))
        except PermissionError:
            results[i] = ("permission_error", None)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    outcomes = [r[0] for r in results]
    assert outcomes.count("ok") == 1, f"se esperaba exactamente 1 ganador, hubo: {results}"
    assert outcomes.count("permission_error") == n - 1

    winner = next(r[1] for r in results if r[0] == "ok")
    assert db.get_card_by_id(p_uuid, "CARD-RACE-T")["assignee_node_uuid"] == winner
