// ClinicFlow front desk. Plain JavaScript, no dependencies. All text is added with
// textContent, never as HTML, so nothing a patient types can run as script.
(() => {
  const KEY_STORE = "clinicflow.apiKey";
  const root = document.getElementById("root");
  const toastEl = document.getElementById("toast");

  const state = { key: null, view: "schedule", tab: "upcoming", patients: null };

  try { state.key = sessionStorage.getItem(KEY_STORE); } catch (_) { /* storage blocked */ }

  const STATUS = { booked: "Booked", completed: "Completed", no_show: "No-show", cancelled: "Cancelled" };
  const REMINDER = {
    pending: "Reminder queued",
    scheduled: "Reminder scheduled",
    cancelled: "Reminder cancelled",
  };

  // ---------- helpers ----------

  function el(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === false || v == null) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null) node.append(kid);
    return node;
  }

  const svgPlus = () => {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 32 32");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M16 7v18M7 16h18");
    path.setAttribute("stroke", "white");
    path.setAttribute("stroke-width", "4");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("fill", "none");
    svg.append(path);
    return svg;
  };

  const mark = () => el("span", { class: "mark" }, svgPlus());

  let toastTimer;
  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2600);
  }

  function signOut() {
    state.key = null;
    state.patients = null;
    try { sessionStorage.removeItem(KEY_STORE); } catch (_) { /* ignore */ }
    render();
  }

  async function api(path, options = {}) {
    const response = await fetch("/api/v1" + path, {
      method: options.method || "GET",
      headers: { "X-API-Key": state.key, "Content-Type": "application/json" },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (response.status === 401) {
      signOut();
      throw new Error("That API key was not accepted. Sign in again.");
    }
    if (!response.ok) {
      let message = "Something went wrong. Try again.";
      try {
        const data = await response.json();
        if (typeof data.detail === "string") message = data.detail;
        else if (Array.isArray(data.detail) && data.detail[0]) message = data.detail[0].msg;
      } catch (_) { /* keep the default */ }
      throw new Error(message);
    }
    return response.json();
  }

  async function ensurePatients() {
    if (!state.patients) state.patients = await api("/patients?limit=200");
    return state.patients;
  }

  const dayKey = (d) => d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate();

  function dayLabel(d) {
    const today = new Date();
    const tomorrow = new Date();
    tomorrow.setDate(today.getDate() + 1);
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);
    const long = d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
    if (dayKey(d) === dayKey(today)) return "Today, " + long;
    if (dayKey(d) === dayKey(tomorrow)) return "Tomorrow, " + long;
    if (dayKey(d) === dayKey(yesterday)) return "Yesterday, " + long;
    return long;
  }

  function timeNodes(d) {
    const parts = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).formatToParts(d);
    const meridiem = parts.find((p) => p.type === "dayPeriod");
    const clock = parts.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim();
    return el("span", { class: "time" }, clock, meridiem ? el("small", { text: meridiem.value }) : null);
  }

  // ---------- sign in ----------

  function renderGate(message) {
    const input = el("input", { id: "key", type: "password", autocomplete: "off", required: true, spellcheck: "false" });
    const error = el("p", { class: "error", role: "alert", hidden: !message, text: message || "" });
    const button = el("button", { class: "btn primary", type: "submit", text: "Open schedule" });

    const form = el("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        button.disabled = true;
        state.key = input.value.trim();
        try {
          await api("/patients?limit=1"); // proves the key works
          try { sessionStorage.setItem(KEY_STORE, state.key); } catch (_) { /* ignore */ }
          state.patients = null;
          render();
        } catch (err) {
          state.key = null;
          renderGate(err.message);
        }
      },
    },
      el("h2", { text: "Sign in to your clinic" }),
      el("p", { text: "Enter the API key your administrator gave you." }),
      error,
      el("div", { class: "field" }, el("label", { for: "key", text: "API key" }), input),
      button,
    );

    root.replaceChildren(
      el("div", { class: "gate" },
        el("div", { class: "gate-brand" },
          el("div", { class: "brand" }, mark(), "ClinicFlow"),
          el("h1", { text: "Every visit, on time." }),
          el("p", { text: "Book appointments and send reminders without chasing patients by phone." }),
        ),
        el("div", { class: "gate-form" }, form),
      ),
    );
    input.focus();
  }

  // ---------- shell ----------

  function renderShell(content) {
    const nav = (view, label) =>
      el("button", {
        type: "button",
        "aria-current": state.view === view ? "page" : false,
        text: label,
        onclick: () => { state.view = view; render(); },
      });

    root.replaceChildren(
      el("div", { class: "shell" },
        el("aside", { class: "rail" },
          el("div", { class: "brand" }, mark(), "ClinicFlow"),
          el("nav", { class: "nav", "aria-label": "Main" }, nav("schedule", "Schedule"), nav("patients", "Patients")),
          el("div", { class: "rail-foot" },
            el("small", { text: "Signed in with an API key" }),
            el("button", { class: "btn ghost-light small", type: "button", text: "Sign out", onclick: signOut }),
          ),
        ),
        el("main", {}, content),
      ),
    );
  }

  // ---------- schedule ----------

  async function renderSchedule() {
    const content = el("div", {});
    renderShell(content);

    const bookButton = el("button", { class: "btn primary", type: "button", text: "Book appointment", onclick: openBooking });
    content.append(
      el("div", { class: "page-head" },
        el("div", {}, el("h1", { text: "Schedule" }), el("p", { text: "Visits for your clinic, grouped by day." })),
        bookButton,
      ),
    );

    const tabs = [["upcoming", "Upcoming"], ["earlier", "Earlier"], ["cancelled", "Cancelled"]];
    content.append(
      el("div", { class: "tabs", role: "tablist" },
        tabs.map(([id, label]) =>
          el("button", {
            type: "button", role: "tab", "aria-selected": String(state.tab === id), text: label,
            onclick: () => { state.tab = id; renderSchedule(); },
          })),
      ),
    );

    const list = el("div", {}, el("p", { class: "hint", text: "Loading visits…" }));
    content.append(list);

    try {
      const params = new URLSearchParams({ limit: "200" });
      const now = new Date().toISOString();
      if (state.tab === "upcoming") { params.set("status", "booked"); params.set("starts_after", now); }
      else if (state.tab === "earlier") params.set("starts_before", now);
      else params.set("status", "cancelled");

      const [appointments, patients] = await Promise.all([api("/appointments?" + params), ensurePatients()]);
      const byId = new Map(patients.map((p) => [p.id, p]));
      if (state.tab === "earlier") appointments.reverse();

      if (!appointments.length) {
        const emptyText = {
          upcoming: ["No upcoming visits", "Book the first appointment to see it here."],
          earlier: ["No earlier visits", "Visits that have already happened appear here."],
          cancelled: ["No cancelled visits", "Cancelled appointments appear here."],
        }[state.tab];
        list.replaceChildren(
          el("div", { class: "empty" }, el("strong", { text: emptyText[0] }), emptyText[1],
            state.tab === "upcoming" ? el("div", {}, el("button", { class: "btn primary", type: "button", text: "Book appointment", onclick: openBooking })) : null),
        );
        return;
      }

      const groups = new Map();
      for (const appt of appointments) {
        const when = new Date(appt.starts_at);
        const key = dayKey(when);
        if (!groups.has(key)) groups.set(key, { when, items: [] });
        groups.get(key).items.push(appt);
      }

      list.replaceChildren(
        ...[...groups.values()].map((group) =>
          el("section", { class: "day" },
            el("h2", { text: dayLabel(group.when) }),
            el("ol", { class: "sheet" }, group.items.map((appt) => visitRow(appt, byId.get(appt.patient_id)))),
          )),
      );
    } catch (err) {
      list.replaceChildren(el("p", { class: "error", role: "alert", text: err.message }));
    }
  }

  function visitRow(appt, patient) {
    const reminderText = appt.reminder ? REMINDER[appt.reminder] : "No reminder, no email on file";

    const change = async (status, button) => {
      button.disabled = true;
      try {
        await api("/appointments/" + appt.id, { method: "PATCH", body: { status } });
        toast(status === "cancelled" ? "Appointment cancelled." : "Appointment marked " + STATUS[status].toLowerCase() + ".");
        renderSchedule();
      } catch (err) {
        button.disabled = false;
        toast(err.message);
      }
    };

    const cancelButton = el("button", { class: "btn small danger", type: "button", text: "Cancel" });
    cancelButton.addEventListener("click", () => {
      if (!cancelButton.classList.contains("armed")) {
        cancelButton.classList.add("armed");
        cancelButton.textContent = "Confirm cancel";
        setTimeout(() => { cancelButton.classList.remove("armed"); cancelButton.textContent = "Cancel"; }, 4000);
        return;
      }
      change("cancelled", cancelButton);
    });

    const actions = appt.status === "booked"
      ? el("div", { class: "actions" },
          (() => { const b = el("button", { class: "btn small", type: "button", text: "Complete" }); b.addEventListener("click", () => change("completed", b)); return b; })(),
          (() => { const b = el("button", { class: "btn small", type: "button", text: "No-show" }); b.addEventListener("click", () => change("no_show", b)); return b; })(),
          cancelButton,
        )
      : el("div", { class: "actions" });

    return el("li", { class: "visit", "data-status": appt.status },
      timeNodes(new Date(appt.starts_at)),
      el("div", { class: "who" },
        el("strong", { text: patient ? patient.full_name : "Unknown patient" }),
        el("span", { text: reminderText }),
      ),
      el("span", { class: "state status", text: STATUS[appt.status] || appt.status }),
      actions,
    );
  }

  // ---------- booking dialog ----------

  async function openBooking() {
    let patients;
    try { patients = await ensurePatients(); } catch (err) { toast(err.message); return; }

    const dialog = el("dialog", { "aria-labelledby": "book-title" });
    const close = () => { dialog.close(); dialog.remove(); };
    dialog.addEventListener("cancel", () => setTimeout(() => dialog.remove()));

    if (!patients.length) {
      dialog.append(
        el("form", { method: "dialog" },
          el("h2", { id: "book-title", text: "Add a patient first" }),
          el("p", { class: "hint", text: "An appointment needs a patient. Add one, then come back to book." }),
          el("div", { class: "dialog-actions" },
            el("button", { class: "btn", type: "button", text: "Close", onclick: close }),
            el("button", { class: "btn primary", type: "button", text: "Go to patients", onclick: () => { close(); state.view = "patients"; render(); } }),
          ),
        ),
      );
    } else {
      const select = el("select", { id: "book-patient", required: true },
        patients.map((p) => el("option", { value: p.id, text: p.full_name + (p.email ? "" : " (no email)") })));
      const when = el("input", { id: "book-when", type: "datetime-local", required: true });
      const error = el("p", { class: "error", role: "alert", hidden: true });
      const submit = el("button", { class: "btn primary", type: "submit", text: "Book appointment" });

      const start = new Date(Date.now() + 24 * 3600 * 1000);
      start.setMinutes(0, 0, 0);
      const pad = (n) => String(n).padStart(2, "0");
      when.value = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}T${pad(start.getHours())}:00`;

      dialog.append(
        el("form", {
          onsubmit: async (event) => {
            event.preventDefault();
            submit.disabled = true;
            error.hidden = true;
            try {
              const created = await api("/appointments", {
                method: "POST",
                body: { patient_id: select.value, starts_at: new Date(when.value).toISOString() },
              });
              close();
              state.tab = "upcoming";
              toast(created.reminder ? "Appointment booked. A reminder is queued." : "Appointment booked.");
              state.view = "schedule";
              render();
            } catch (err) {
              submit.disabled = false;
              error.textContent = err.message;
              error.hidden = false;
            }
          },
        },
          el("h2", { id: "book-title", text: "Book appointment" }),
          error,
          el("div", { class: "field" }, el("label", { for: "book-patient", text: "Patient" }), select),
          el("div", { class: "field" },
            el("label", { for: "book-when", text: "Date and time" }), when,
            el("p", { class: "hint", text: "In your local time. Patients with an email address get a reminder 24 hours before." })),
          el("div", { class: "dialog-actions" },
            el("button", { class: "btn", type: "button", text: "Close", onclick: close }), submit),
        ),
      );
    }

    document.body.append(dialog);
    dialog.showModal();
  }

  // ---------- patients ----------

  async function renderPatients() {
    const content = el("div", {});
    renderShell(content);

    const name = el("input", { id: "p-name", required: true, maxlength: "200", autocomplete: "off" });
    const email = el("input", { id: "p-email", type: "email", maxlength: "254", autocomplete: "off" });
    const phone = el("input", { id: "p-phone", type: "tel", maxlength: "40", autocomplete: "off" });
    const error = el("p", { class: "error", role: "alert", hidden: true });
    const submit = el("button", { class: "btn primary", type: "submit", text: "Add patient" });

    const form = el("form", {
      class: "add-patient",
      onsubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        error.hidden = true;
        try {
          const body = { full_name: name.value.trim() };
          if (email.value.trim()) body.email = email.value.trim();
          if (phone.value.trim()) body.phone = phone.value.trim();
          await api("/patients", { method: "POST", body });
          state.patients = null;
          toast("Patient added.");
          renderPatients();
        } catch (err) {
          submit.disabled = false;
          error.textContent = err.message;
          error.hidden = false;
        }
      },
    },
      el("div", { class: "field" }, el("label", { for: "p-name", text: "Full name" }), name),
      el("div", { class: "field" }, el("label", { for: "p-email", text: "Email (for reminders)" }), email),
      el("div", { class: "field" }, el("label", { for: "p-phone", text: "Phone" }), phone),
      submit,
    );

    const list = el("div", {}, el("p", { class: "hint", text: "Loading patients…" }));
    content.append(
      el("div", { class: "page-head" },
        el("div", {}, el("h1", { text: "Patients" }), el("p", { text: "Only patients of your clinic appear here." }))),
      error, form, list,
    );

    try {
      const patients = await ensurePatients();
      if (!patients.length) {
        list.replaceChildren(el("div", { class: "empty" }, el("strong", { text: "No patients yet" }), "Add a patient above to start booking visits."));
        return;
      }
      list.replaceChildren(
        el("ul", { class: "people" },
          patients.map((p) =>
            el("li", {},
              el("span", { class: "name", text: p.full_name }),
              el("span", { class: "contact", text: p.email || "No email on file" }),
              el("span", { class: "contact", text: p.phone || "" }),
            ))),
      );
    } catch (err) {
      list.replaceChildren(el("p", { class: "error", role: "alert", text: err.message }));
    }
  }

  // ---------- start ----------

  function render() {
    if (!state.key) return renderGate();
    if (state.view === "patients") return renderPatients();
    return renderSchedule();
  }

  render();
})();
