# Ficha Técnica: Módulo Criptográfico Ed25519 e Integración GitHub
> **Área**: Backend  
> **Identificador**: `crypto_p2p`  
> **Código Fuente**: [`src/p2p/crypto.py`](../../../src/p2p/crypto.py), [`src/storage/github_sync.py`](../../../src/storage/github_sync.py)  
> **Documento OKF de Referencia**: [`02_MAPA_DE_CODIGO_Y_FEATURES.md`](../02_MAPA_DE_CODIGO_Y_FEATURES.md)

---

## 1. Resumen y Propósito
Este módulo provee la capa de identidad criptográfica, verificación de integridad y vinculación con GitHub para el enjambre MCP2P:
1. **Identidad Criptográfica Ed25519**: Cada nodo posee un par de claves asimétricas persistente (`~/.mcp2p/node_identity.key`). Todas las invitaciones y señales P2P son firmadas digitalmente y verificadas en destino a **costo cero de tokens**.
2. **Tokens de Invitación P2P Firmados**: Las invitaciones llevan el payload `{project_uuid, name, urls, sender_node_uuid, sender_pubkey}` firmado por el emisor, previniendo spoofing o adulteración de proyectos.
3. **Control Human-in-the-Loop (HITL)**: Los colaboradores que se unen ingresan en estado `pending_approval`. Solo el dueño humano del proyecto puede aprobar explícitamente su acceso (`approve_collaborator`), impidiendo que agentes de IA tomen decisiones de autorización sin supervisión.
4. **Integración con GitHub**: Detecta credenciales locales mediante `gh auth status` o tokens de entorno para publicar y sincronizar remotos `origin`.

---

## 2. Métodos Clave de la API

| Clase / Método | Argumentos | Retorno | Descripción |
| :--- | :--- | :--- | :--- |
| `NodeCryptoManager.sign_payload` | `payload: dict` | `str` | Firma digitalmente un diccionario JSON con la clave privada Ed25519. |
| `NodeCryptoManager.verify_signature` | `payload, sig, pubkey` | `bool` | Comprueba la validez de la firma digital. |
| `NodeCryptoManager.create_signed_invite_token` | `invite_data: dict` | `str` | Genera el token URL-safe empaquetado con firma y clave pública. |
| `DatabaseManager.add_or_request_collaborator` | `project_uuid, node_uuid, ...` | `dict` | Registra solicitud de colaborador pendiente o aprobada. |
| `DatabaseManager.approve_collaborator` | `project_uuid, node_uuid` | `bool` | Aprobación explícita HITL del colaborador. |
| `GitHubSyncManager.get_auth_status` | - | `dict` | Consulta el usuario y estado de sesión de GitHub. |
