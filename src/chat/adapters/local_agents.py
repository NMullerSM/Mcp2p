"""Adaptador de los agentes locales para el módulo de chat.

Implementa `ChatSource`, `RosterProvider` y `ChatTransport` con FUNCIONES inyectadas (vista de mensajes del relevo,
lista de terminales y envío del humano); no importa ningún servicio concreto. Quien lo cablea (`chat_ui`) pasa las
funciones. Un adaptador de otro nodo o del servidor sería un archivo hermano con la misma forma.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from src.chat.domain import ChatEntry, Participant, SendResult, tick_for

HUMAN_LABEL = "Tú"
OPERATOR_LABEL = "Operador"

ChatRowsFn = Callable[[str, int, int], List[Dict[str, Any]]]
RosterFn = Callable[[str], List[Dict[str, Any]]]
SendFn = Callable[[str, str, str, str], Dict[str, Any]]


def _terminal_label(pane_id: Any) -> str:
    raw = str(pane_id or "")
    match = re.fullmatch(r"pane_(\d+)", raw)
    return f"Terminal {match.group(1)}" if match else (raw or "Terminal")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


class LocalAgentsAdapter:
    id = "local_agents"

    def __init__(self, *, chat_rows: ChatRowsFn, roster: RosterFn, send: SendFn) -> None:
        self._chat_rows = chat_rows
        self._roster = roster
        self._send = send

    # -- RosterProvider ----------------------------------------------------------------------------------------------

    def participants(self, project_uuid: str) -> List[Participant]:
        members = self._roster(project_uuid)
        profiles: Dict[str, int] = {}
        for m in members:
            profiles[str(m.get("cli_profile") or "")] = profiles.get(str(m.get("cli_profile") or ""), 0) + 1
        out: List[Participant] = []
        for m in members:
            pane_id = str(m.get("pane_id") or "")
            node = str(m.get("node_uuid") or "")
            if not pane_id or not node:
                continue
            cli = str(m.get("cli_profile") or "")
            label = str(m.get("label") or _terminal_label(pane_id))
            aliases = (_slug(cli),) if cli and profiles.get(cli) == 1 else ()
            out.append(
                Participant(
                    principal=f"pane:{node}",
                    label=f"{label} · {cli}" if cli else label,
                    kind="agent",
                    mention="@" + _slug(label),
                    role=m.get("role"),
                    role_label=m.get("role_label"),
                    cli_profile=cli or None,
                    aliases=aliases,
                )
            )
        return out

    # -- ChatSource --------------------------------------------------------------------------------------------------

    def list_since(self, project_uuid: str, since_rev_ms: int, limit: int) -> List[ChatEntry]:
        rows = self._chat_rows(project_uuid, since_rev_ms, limit)
        known = {p.principal: p for p in self.participants(project_uuid)}
        return [self._entry(project_uuid, row, known) for row in rows]

    def _participant(self, label: Mapping[str, Any], known: Mapping[str, Participant]) -> Participant:
        principal = str(label.get("principal") or "")
        if principal in known:
            return known[principal]
        kind = str(label.get("kind") or "")
        if kind == "human":
            return Participant(principal, HUMAN_LABEL, kind="human")
        if kind == "operator":
            name = str(label.get("agent_name") or label.get("name") or OPERATOR_LABEL)
            return Participant(principal, name, kind="agent", role=label.get("role"), role_label=label.get("role_label"))
        if kind == "pane":
            return Participant(
                principal,
                _terminal_label(label.get("pane_id")) if label.get("pane_id") else "Terminal cerrada",
                kind="agent",
                role=label.get("role"),
                role_label=label.get("role_label"),
            )
        return Participant(principal or "desconocido", str(label.get("agent_name") or principal or "Desconocido"),
                           kind="system")

    def _entry(self, project_uuid: str, row: Mapping[str, Any], known: Mapping[str, Participant]) -> ChatEntry:
        tick, tick_label = tick_for(str(row.get("state") or ""), row.get("delivery_state"), int(row.get("reply_count") or 0))
        remote: Optional[Mapping[str, Any]] = row.get("remote") if isinstance(row.get("remote"), Mapping) else None
        origin = "local"
        if remote:
            origin = "remote:" + str(remote.get("display_name") or remote.get("node_uuid") or "nodo")
        created = int(row.get("created_at") or 0)
        return ChatEntry(
            id=str(row["msg_id"]),
            ts_ms=created,
            rev_ms=int(row.get("rev_ms") or created),
            project_uuid=project_uuid,
            source=self.id,
            sender=self._participant(row.get("from") or {}, known),
            recipients=(self._participant(row.get("to") or {}, known),),
            text=str(row.get("text") or ""),
            kind=str(row.get("kind") or "note"),
            thread_id=str(row.get("chain_id") or ""),
            parent_id=row.get("parent_msg_id"),
            hop=int(row.get("hop") or 0),
            tick=tick,
            tick_label=tick_label,
            origin=origin,
        )

    # -- ChatTransport -----------------------------------------------------------------------------------------------

    def send(self, project_uuid: str, targets: Sequence[Participant], text: str, kind: str) -> List[SendResult]:
        pane_by_principal = {
            f"pane:{m.get('node_uuid')}": str(m.get("pane_id") or "") for m in self._roster(project_uuid)
        }
        results: List[SendResult] = []
        for target in targets:
            pane_id = pane_by_principal.get(target.principal)
            if not pane_id:
                results.append(SendResult(target.principal, False, "TARGET_NOT_FOUND", "Esa terminal ya no está abierta."))
                continue
            res = self._send(project_uuid, pane_id, text, kind)
            ok = res.get("success") is not False
            results.append(
                SendResult(
                    target.principal,
                    ok,
                    "OK" if ok else str(res.get("code") or "SEND_FAILED"),
                    "" if ok else str(res.get("message") or res.get("error") or "No se pudo enviar."),
                    str(res.get("msg_id")) if ok and res.get("msg_id") else None,
                )
            )
        return results
