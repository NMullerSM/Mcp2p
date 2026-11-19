"""Saneado y formato de lo que la entrega directa escribe en una terminal (WP-6b, 4.6).

Dominio puro: sin E/S. Todo lo que sale de aquí es texto plano sin ningún byte de control salvo los dos
marcadores del pegado entre corchetes (que añade este módulo, nunca el remitente) y el salto de línea.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence, Tuple

BRACKETED_PASTE_START: str = "\x1b[200~"
BRACKETED_PASTE_END: str = "\x1b[201~"

PASTE_MODES: Tuple[str, ...] = ("bracketed", "single_line")
ROLES: Tuple[str, ...] = ("orquestador", "implementador", "auditor", "mediador", "par", "humano", "operador", "remoto")

_BIDI = frozenset(
    [0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)]
)
_INVISIBLES = frozenset([0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF])
_MSG_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
_LABEL_BAD = re.compile(r"[^A-Za-z0-9 _.\-]")
_TOKEN_BAD = re.compile(r"[^A-Za-z0-9_.\-]")
_REPLACEMENT = "�"
_SINGLE_LINE_BREAK = " ⏎ "


@dataclass(frozen=True)
class SanitizeResult:
    text: str
    removed: int


@dataclass(frozen=True)
class InjectionItem:
    msg_id: str
    role_label: str
    sender_label: str
    trust: str
    kind: str
    text: str


@dataclass(frozen=True)
class InjectionPayload:
    paste: str
    submit: str
    msg_ids: Tuple[str, ...]
    removed: int
    pointer_only: bool


def _is_control(code: int) -> bool:
    return code < 0x20 or 0x7F <= code <= 0x9F


def sanitize_for_injection(text: str, *, single_line: bool = False) -> SanitizeResult:
    """Deja solo texto imprimible: ningún C0/C1/DEL/ESC, bidi ni invisibles (salvo `\\n` fuera de `single_line`)."""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    for ch in (" ", " ", "\u0085"):
        t = t.replace(ch, "\n")
    t = t.replace("\t", "    ")
    out = []
    removed = 0
    for c in t:
        code = ord(c)
        if c == "\n":
            out.append(_SINGLE_LINE_BREAK if single_line else "\n")
        elif _is_control(code) or code in _BIDI or 0xD800 <= code <= 0xDFFF:
            out.append(_REPLACEMENT)
            removed += 1
        elif code in _INVISIBLES:
            removed += 1
        else:
            out.append(c)
    result = "".join(out)
    # Postcondición en código (no solo en pruebas): si falla, el mensaje se queda en la bandeja.
    for c in result:
        code = ord(c)
        if c == "\n" and not single_line:
            continue
        if _is_control(code) or code in _BIDI or code in _INVISIBLES:
            raise ValueError("El saneado dejó un carácter de control.")
    return SanitizeResult(text=result, removed=removed)


def header_field(value: object, *, max_len: int = 32) -> str:
    """Etiqueta de la cabecera: solo `[A-Za-z0-9 _.-]`, lo demás pasa a `_`, recortada a `max_len`."""
    if value is None:
        return ""
    return _LABEL_BAD.sub("_", str(value))[:max_len]


def _token(value: object, *, max_len: int = 32) -> str:
    return _TOKEN_BAD.sub("_", str(value if value is not None else ""))[:max_len]


def _sender_label(value: object) -> str:
    """`Terminal 1`, `humano`, `operador` o `<nodo>/Terminal`: cada tramo filtrado, la barra se conserva."""
    parts = str(value if value is not None else "").split("/", 1)
    return "/".join(header_field(part) for part in parts)


def _role(value: object) -> str:
    return value if isinstance(value, str) and value in ROLES else "par"


def _check_msg_id(msg_id: object) -> str:
    if not isinstance(msg_id, str) or not _MSG_ID_RE.match(msg_id):
        raise ValueError("msg_id inválido para la cabecera de entrega.")
    return msg_id


def _meta(item: InjectionItem) -> str:
    return (
        f"msg={_check_msg_id(item.msg_id)} de={_role(item.role_label)}:{_sender_label(item.sender_label)} "
        f"confianza={_token(item.trust)} tipo={_token(item.kind)}"
    )


def _body(text: str, *, single_line: bool) -> SanitizeResult:
    res = sanitize_for_injection(text, single_line=single_line)
    # Una línea del cuerpo no puede hacerse pasar por una cabecera del Hub.
    return SanitizeResult(text=res.text.replace("[MCP2P", "[ MCP2P"), removed=res.removed)


def _wrap(content: str, paste_mode: str) -> str:
    return f"{BRACKETED_PASTE_START}{content}{BRACKETED_PASTE_END}" if paste_mode == "bracketed" else content


def _pointer(item: InjectionItem, size: int, paste_mode: str) -> str:
    line = (
        f"[MCP2P msg={_check_msg_id(item.msg_id)} de={_role(item.role_label)}:{_sender_label(item.sender_label)} "
        f"tipo={_token(item.kind)} largo={size}B: léelo con inbox_next]"
    )
    return _wrap(line, paste_mode)


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def build_injection(messages: Sequence[InjectionItem], *, paste_mode: str, max_bytes: int) -> InjectionPayload:
    """Arma lo que se escribe: un mensaje con cabecera, un lote numerado o solo el aviso de mensaje largo."""
    if paste_mode not in PASTE_MODES:
        raise ValueError(f"paste_mode desconocido: {paste_mode!r}")
    items = list(messages)
    if not items:
        raise ValueError("build_injection necesita al menos un mensaje.")
    single = paste_mode == "single_line"
    bodies = [_body(item.text, single_line=single) for item in items]
    sep_head = " " if single else "\n"

    def single_content(i: int) -> str:
        return f"[MCP2P {_meta(items[i])} responde=inbox_reply]{sep_head}{bodies[i].text}"

    first = single_content(0)
    if len(items) == 1 or _size(first) > max_bytes:
        if _size(first) > max_bytes:
            return InjectionPayload(
                paste=_pointer(items[0], _size(bodies[0].text), paste_mode),
                submit="\r",
                msg_ids=(items[0].msg_id,),
                removed=0,
                pointer_only=True,
            )
        return _finish(first, (items[0].msg_id,), bodies[0].removed, paste_mode)

    # Lote (Q8): se toman por orden los mensajes que caben; el primero ya cabe como mensaje suelto.
    glue = " ⏎ " if single else "\n\n"
    chosen = []
    removed = 0
    total_header = ""
    for idx in range(len(items)):
        line = f"{len(chosen) + 1}) [{_meta(items[idx])}] {bodies[idx].text}"
        candidate = glue.join([*chosen, line])
        head = f"[MCP2P lote={len(chosen) + 1} responde=inbox_reply]{sep_head}"
        if _size(head + candidate) > max_bytes:
            break
        chosen.append(line)
        removed += bodies[idx].removed
        total_header = head
    if len(chosen) <= 1:
        return _finish(first, (items[0].msg_id,), bodies[0].removed, paste_mode)
    content = total_header + glue.join(chosen)
    return _finish(content, tuple(i.msg_id for i in items[: len(chosen)]), removed, paste_mode)


def _finish(content: str, msg_ids: Tuple[str, ...], removed: int, paste_mode: str) -> InjectionPayload:
    paste = _wrap(content, paste_mode)
    expected_esc = 2 if paste_mode == "bracketed" else 0
    if paste.count("\x1b") != expected_esc or "\x9b" in paste:
        raise ValueError("La carga a escribir contiene bytes de control inesperados.")
    if paste_mode == "bracketed" and not (paste.startswith(BRACKETED_PASTE_START) and paste.endswith(BRACKETED_PASTE_END)):
        raise ValueError("La carga a escribir no está acotada por los marcadores de pegado.")
    return InjectionPayload(paste=paste, submit="\r", msg_ids=msg_ids, removed=removed, pointer_only=False)



def build_human_paste(text: str, *, paste_mode: str) -> InjectionPayload:
    """Lo que el humano teclea desde el chat: texto saneado sin cabecera (D12-ter 2a), mismo pegado y Enter aparte."""
    if paste_mode not in PASTE_MODES:
        raise ValueError(f"paste_mode desconocido: {paste_mode!r}")
    body = sanitize_for_injection(text, single_line=paste_mode == "single_line")
    return _finish(body.text, (), body.removed, paste_mode)
