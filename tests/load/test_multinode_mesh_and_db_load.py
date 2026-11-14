"""
Hito 3.4 — Pruebas de carga para malla P2P MultiPeerMeshConnector (12 nodos + 3 seeds)
y concurrencia de 16 hilos sobre CoreKernel + DatabaseManager (pool de 4 + ancla).
"""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from src.core.domain.context import ActorContext
from src.core.factory import ApplicationFactory
from src.p2p.crypto import NodeCryptoManager
from src.p2p.mesh_adapter import MultiPeerMeshConnector
from src.storage.db import DatabaseManager


# =========================================================================
# 1. Malla de 12 nodos + 3 seeds con ráfagas concurrentes de 200+ sobres
# =========================================================================


def test_12_node_3_seed_mesh_burst_with_1_seed_failure_and_zero_storms(tmp_path: Path) -> None:
    node_count = 12
    bursts_per_node = 20  # 12 * 20 = 240 sobres concurrentes (>200)
    seed_urls = [
        "wss://seed-1.mcp2p.test/ws",
        "wss://seed-2.mcp2p.test/ws",
        "wss://seed-3.mcp2p.test/ws",
    ]
    failed_seed = seed_urls[0]

    cryptos: Dict[str, NodeCryptoManager] = {}
    nodes: Dict[str, MultiPeerMeshConnector] = {}
    delivered_per_node: Dict[str, List[str]] = defaultdict(list)
    delivered_lock = threading.Lock()
    relay_calls: Counter[str] = Counter()
    relay_lock = threading.Lock()

    def _router(target: str, envelope: Dict[str, Any]) -> bool:
        with relay_lock:
            relay_calls[target] += 1

        if target == failed_seed:
            # Simular caída total de 1 seed
            return False

        if target in (seed_urls[1], seed_urls[2]):
            # Seed saludable retransmite al conjunto de pares (excluyendo al emisor)
            sender = envelope.get("sender_node_uuid")
            accepted_any = False
            for nid, peer_conn in nodes.items():
                if nid == sender:
                    continue
                res = peer_conn.ingest_envelope(envelope)
                if res.get("accepted"):
                    accepted_any = True
            return accepted_any

        if target.startswith("mesh://"):
            target_id = target.removeprefix("mesh://")
            if target_id in nodes:
                # Simular fallo directo ocasional en pares pares para forzar uso de seeds también
                return False
        return False

    try:
        for i in range(node_count):
            nid = f"mesh_node_{i:02d}"
            c = NodeCryptoManager(key_path=tmp_path / f"{nid}.pem")
            cryptos[nid] = c

        for i in range(node_count):
            nid = f"mesh_node_{i:02d}"
            conn = MultiPeerMeshConnector(
                node_uuid=nid,
                crypto_manager=cryptos[nid],
                transport_mode="lan_mesh",
                transport_sender=_router,
                rate_limit_max=1000,
                rate_limit_window_s=60.0,
                cb_failure_threshold=2,
                cb_recovery_s=60.0,
                seed_nodes=seed_urls,
            )

            def _make_cb(receiver_id: str):
                def _on_msg(env: Dict[str, Any]) -> None:
                    with delivered_lock:
                        delivered_per_node[receiver_id].append(env["envelope_id"])
                return _on_msg

            conn.on_message(_make_cb(nid))
            nodes[nid] = conn

        # Registrar todos los pares en cada nodo
        for nid, conn in nodes.items():
            for other_id, other_crypto in cryptos.items():
                if other_id != nid:
                    conn.register_peer(
                        other_id,
                        public_key=other_crypto.public_key_base64,
                        endpoint=f"mesh://{other_id}",
                    )

        # Ráfaga concurrente de 240 sobres desde los 12 nodos en paralelo
        sent_envelopes: List[Tuple[str, str]] = []
        sent_lock = threading.Lock()
        start_barrier = threading.Barrier(node_count)

        def _worker(sender_id: str) -> List[Dict[str, Any]]:
            start_barrier.wait(timeout=10.0)
            results = []
            sender_conn = nodes[sender_id]
            for seq in range(bursts_per_node):
                r = sender_conn.broadcast_envelope({"sender": sender_id, "seq": seq})
                results.append(r)
                if r.get("envelope_id"):
                    with sent_lock:
                        sent_envelopes.append((sender_id, r["envelope_id"]))
            return results

        all_results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=node_count) as pool:
            futures = [pool.submit(_worker, nid) for nid in nodes]
            for fut in futures:
                all_results.extend(fut.result(timeout=30.0))

        total_sent = node_count * bursts_per_node
        assert total_sent == 240
        assert len(all_results) == total_sent
        assert all(r["success"] is True for r in all_results)
        assert all(r["delivered"] == node_count - 1 for r in all_results)

        # Verificar entrega íntegra en cada nodo y 0 broadcast storms (sin duplicados por envelope_id)
        expected_ids_by_sender: Dict[str, Set[str]] = defaultdict(set)
        for s_id, env_id in sent_envelopes:
            expected_ids_by_sender[s_id].add(env_id)

        all_sent_ids = {env_id for _, env_id in sent_envelopes}
        assert len(all_sent_ids) == total_sent

        for nid in nodes:
            expected_for_node = all_sent_ids - expected_ids_by_sender[nid]
            received_list = delivered_per_node[nid]
            assert len(received_list) == len(expected_for_node), (
                f"{nid} recibió {len(received_list)} en lugar de {len(expected_for_node)}"
            )
            assert set(received_list) == expected_for_node

        # Anti-storm explícito: re-inyectar todos los sobres en un nodo receptor no debe disparar ni 1 entrega nueva
        probe_node = nodes["mesh_node_00"]
        sample_sender = nodes["mesh_node_01"]
        replay_env = sample_sender.build_signed_envelope({"probe": "storm_guard"}, target_node_uuid="*")
        assert probe_node.ingest_envelope(replay_env)["accepted"] is True
        for _ in range(10):
            dup = probe_node.ingest_envelope(replay_env)
            assert dup["accepted"] is False
            assert dup["reason"] == "REPLAY_DETECTED"

        # Tolerancia a caída de 1 seed: seed-1 debe abrir circuit breaker y seed-2/seed-3 seguir saludables
        any_node_seeds = {s["seed_url"]: s for s in nodes["mesh_node_00"].list_seed_nodes()}
        assert any_node_seeds[failed_seed]["circuit_state"] == "open"
        assert any_node_seeds[failed_seed]["healthy"] is False
        assert any_node_seeds[seed_urls[1]]["healthy"] is True
        assert any_node_seeds[seed_urls[1]]["circuit_state"] == "closed"
    finally:
        for conn in nodes.values():
            conn.close()


# =========================================================================
# 2. Concurrencia de 16 hilos sobre CoreKernel + DatabaseManager (Pool 4 + ancla)
# =========================================================================


def test_16_threads_core_kernel_sqlite_pool_zero_locks_and_zero_lost_updates(tmp_path: Path) -> None:
    db_file = tmp_path / "load_pool.db"
    db = DatabaseManager(db_path=str(db_file))
    kernel = ApplicationFactory.create_test_kernel(db_manager=db)

    try:
        proj = db.create_project(name="LoadPoolProj", init_git=False)
        p_uuid = proj["project_uuid"]

        thread_count = 16
        cards_per_thread = 10  # 16 * 10 = 160 tarjetas creadas, reclamadas, actualizadas y liberadas

        for tid in range(thread_count):
            n_id = f"load_worker_{tid:02d}"
            db.register_or_update_node(n_id, f"Worker {tid}", f"ag_{tid}", status="online", node_kind="human")
            db.add_or_request_collaborator(p_uuid, n_id, role="collaborator", status="approved")

        # Tarjeta compartida para carrera atómica entre los 16 hilos simultáneos
        db.create_card(p_uuid, "SHARED-HOT-CARD", "Contention target")

        start_barrier = threading.Barrier(thread_count)
        errors: List[str] = []
        errors_lock = threading.Lock()
        hot_winners: List[str] = []
        hot_collisions: List[str] = []
        hot_lock = threading.Lock()

        def _thread_workload(tid: int) -> None:
            node_id = f"load_worker_{tid:02d}"
            actor = ActorContext(kind="human_ui", node_uuid=node_id, human_presence_verified=True)
            try:
                start_barrier.wait(timeout=10.0)

                # A. Carrera por lease único de la tarjeta compartida
                try:
                    kernel.kanban.claim_lease(p_uuid, "SHARED-HOT-CARD", actor, duration_minutes=30)
                    with hot_lock:
                        hot_winners.append(node_id)
                except PermissionError as pe:
                    with hot_lock:
                        hot_collisions.append(str(pe))

                # B. Ciclo completo paralelo de creación, claim, update y release por hilo
                for idx in range(cards_per_thread):
                    c_id = f"LOAD-{tid:02d}-{idx:02d}"
                    res_c = kernel.kanban.create_task(
                        project_uuid=p_uuid,
                        card_id=c_id,
                        title=f"Initial {c_id}",
                        description="load test",
                        actor=actor,
                    )
                    assert res_c["success"] is True

                    res_claim = kernel.kanban.claim_lease(p_uuid, c_id, actor, duration_minutes=15)
                    assert res_claim["success"] is True

                    res_upd = kernel.kanban.update_task(
                        project_uuid=p_uuid,
                        card_id=c_id,
                        actor=actor,
                        title=f"Updated {c_id}",
                        branch_ref=f"feat/{c_id}",
                    )
                    assert res_upd["success"] is True

                    res_rel = kernel.kanban.release_lease(
                        project_uuid=p_uuid,
                        card_id=c_id,
                        actor=actor,
                        target_status="in_review",
                        branch_ref=f"feat/{c_id}",
                    )
                    assert res_rel["success"] is True

                    # Lecturas intercaladas concurrentes en el pool
                    fp = kernel.views.synthesize_sync_fingerprint(p_uuid)
                    assert fp.get("success") is True
            except Exception as exc:
                with errors_lock:
                    errors.append(f"thread-{tid}: {type(exc).__name__}: {exc}")

        with ThreadPoolExecutor(max_workers=thread_count) as pool:
            futs = [pool.submit(_thread_workload, tid) for tid in range(thread_count)]
            for fut in futs:
                fut.result(timeout=60.0)

        assert errors == [], f"Errores bajo concurrencia (incluyendo database is locked): {errors}"
        assert len(hot_winners) == 1, f"Se esperaba exactamente 1 ganador del lease atómico, obtuvo {hot_winners}"
        assert len(hot_collisions) == thread_count - 1

        # Verificar 0 updates perdidos
        board = kernel.kanban.get_board(p_uuid)
        cards_by_id = {c["card_id"]: c for c in board["cards"]}
        assert len(cards_by_id) == 1 + (thread_count * cards_per_thread)

        for tid in range(thread_count):
            for idx in range(cards_per_thread):
                c_id = f"LOAD-{tid:02d}-{idx:02d}"
                assert c_id in cards_by_id
                card = cards_by_id[c_id]
                assert card["title"] == f"Updated {c_id}"
                assert card["status"] == "in_review"
                assert card["branch_ref"] == f"feat/{c_id}"
                assert card["assignee_node_uuid"] == f"load_worker_{tid:02d}"
    finally:
        kernel.close(close_db=True)
