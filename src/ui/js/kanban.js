/**
 * MCP2P Kanban Controller
 * Gestiona el ciclo de vida de las tarjetas, estados y candados de lease.
 */

class KanbanController {
  constructor() {
    this.columns = {
      backlog: document.getElementById("col-backlog"),
      in_progress: document.getElementById("col-progress"),
      in_review: document.getElementById("col-review"),
      consolidated: document.getElementById("col-consolidated"),
    };
    this.counters = {
      backlog: document.getElementById("count-backlog"),
      in_progress: document.getElementById("count-progress"),
      in_review: document.getElementById("count-review"),
      consolidated: document.getElementById("count-consolidated"),
    };
    this.setupDropZones();
  }

  setupDropZones() {
    const statusMap = {
      "col-backlog": "backlog",
      "col-progress": "in_progress",
      "col-review": "in_review",
      "col-consolidated": "consolidated"
    };

    Object.entries(statusMap).forEach(([id, targetStatus]) => {
      const col = document.getElementById(id);
      if (!col) return;

      col.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        col.classList.add("drag-over");
      });

      col.addEventListener("dragleave", (e) => {
        // Solo remover si salimos del contenedor
        if (!col.contains(e.relatedTarget)) {
          col.classList.remove("drag-over");
        }
      });

      col.addEventListener("drop", async (e) => {
        e.preventDefault();
        col.classList.remove("drag-over");
        const cardId = e.dataTransfer.getData("text/plain");
        if (cardId) {
          await this.moveCard(cardId, targetStatus);
        }
      });
    });
  }

  async moveCard(cardId, targetStatus) {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      if (targetStatus === "in_progress") {
        const sys = await window.pywebview.api.get_system_info();
        const node = sys.active_node || "node_user";
        const res = await window.pywebview.api.claim_task_lease(cardId, node, 30);
        if (!res.success) {
          alert("Aviso: " + (res.error || "Colisión de lease"));
        }
      } else {
        const res = await window.pywebview.api.update_task_status(cardId, targetStatus);
        if (!res.success) {
          alert("Error al mover tarea: " + (res.error || "Operación rechazada"));
        }
      }
      await this.loadBoard();
    } catch (err) {
      console.error("[Kanban Move Error]", err);
    }
  }

  async loadBoard() {
    if (!window.pywebview || !window.pywebview.api) {
      console.warn("[Kanban] PyWebView API aún no está disponible.");
      return;
    }

    try {
      const res = await window.pywebview.api.get_kanban_board();
      if (res.success) {
        this.renderCards(res.cards);
      } else {
        console.error("[Kanban Error]", res.error);
      }
    } catch (err) {
      console.error("[Kanban Fetch Error]", err);
    }
  }

  renderCards(cards) {
    // Limpiar columnas
    Object.values(this.columns).forEach((col) => {
      if (col) col.innerHTML = "";
    });

    const counts = { backlog: 0, in_progress: 0, in_review: 0, consolidated: 0 };

    cards.forEach((card) => {
      const status = card.status || "backlog";
      if (counts[status] !== undefined) {
        counts[status]++;
      }

      const cardEl = document.createElement("div");
      cardEl.className = "kanban-card";
      cardEl.draggable = true;
      cardEl.dataset.cardId = card.card_id;
      cardEl.dataset.status = status;

      cardEl.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", card.card_id);
        e.dataTransfer.effectAllowed = "move";
        cardEl.classList.add("dragging");
      });

      cardEl.addEventListener("dragend", () => {
        cardEl.classList.remove("dragging");
      });

      cardEl.onclick = () => this.onCardClick(card);

      const hasLease = card.lease_expires_at && status === "in_progress";
      const leaseBadge = hasLease
        ? `<span class="lease-badge">🔒 Lease Activo</span>`
        : "";

      const assigneePill = card.assignee_node_uuid
        ? `<span class="assignee-pill">👤 ${card.assignee_node_uuid}</span>`
        : `<span class="assignee-pill">Sin asignar</span>`;

      cardEl.innerHTML = `
        <div class="card-top">
          <span class="card-id">${this.escapeHtml(card.card_id)}</span>
          ${leaseBadge}
        </div>
        <div class="card-heading">${this.escapeHtml(card.title)}</div>
        ${card.description ? `<div class="card-desc">${this.escapeHtml(card.description)}</div>` : ""}
        <div class="card-footer">
          ${assigneePill}
          <span style="font-size: 10px; font-family: var(--font-mono); color: var(--text-muted)">
            ${card.branch_ref ? this.escapeHtml(card.branch_ref) : ""}
          </span>
        </div>
      `;

      if (this.columns[status]) {
        this.columns[status].appendChild(cardEl);
      }
    });

    // Actualizar contadores
    Object.keys(counts).forEach((key) => {
      if (this.counters[key]) {
        this.counters[key].textContent = counts[key];
      }
    });
  }

  onCardClick(card) {
    console.log("[Kanban] Tarjeta seleccionada:", card);
  }

  escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
}

function initKanban() {
  window.kanbanController = new KanbanController();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initKanban);
} else {
  initKanban();
}
