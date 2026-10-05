"use strict";

const DAY = 864e5;
const POLL_MS = 60_000;
const EMOJI = {
  eyes: "👀",
  eyesintensify: "👀",
  memo: "📝",
  pencil: "📝",
  white_check_mark: "✅",
  heavy_check_mark: "✔️",
  merged: "⛙",
  x: "❌",
  no_entry_sign: "🚫",
  zzz: "💤",
  hourglass_flowing_sand: "⏳",
  hourglass: "⌛",
  partying_face: "🥳",
  raised_hands: "🙌",
  pray: "🙏",
};
const COLS = [
  ["needs_review", "🙋 Needs review"],
  ["in_review", "👀 In review"],
  ["commented", "📝 Back to author"],
  ["approved", "✅ Needs merge"],
  ["merged", "⛙ Merged"], // title gets "(N days)" from the snapshot
];
const HOLD = new Set(["zzz", "hourglass_flowing_sand", "hourglass"]);

const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) {
    try {
      return JSON.parse(localStorage.getItem("mrboard." + k)) ?? d;
    } catch {
      return d;
    }
  },
  set(k, v) {
    try {
      localStorage.setItem("mrboard." + k, JSON.stringify(v));
    } catch {}
  },
};

let DATA = null;
let collapsed = new Set(store.get("collapsed", []));

const U = (id) => DATA.users[id] || id.slice(0, 6);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
const short = (p) => p.split("/").at(-1);
// Mattermost resolves the team itself from a /_redirect permalink.
const postUrl = (mr) => `${DATA.mm_url}/_redirect/pl/${mr.post_id}`;
const author = (mr) => mr.gitlab?.author || U(mr.author_id);
const since = (mr) =>
  mr.reactions.length ? mr.reactions.at(-1).at : mr.posted_at;
const touches = (mr, me) =>
  author(mr) === me || mr.reactions.some((r) => U(r.user_id) === me);

// A ❌ on a post means it was withdrawn (e.g. reposted under a new title): the post is done,
// whatever happens to the MR afterwards, and any repost carries the MR's state from then on.
const withdrawn = (mr) => mr.emoji_state === "closed";

// GitLab is the source of truth for merged/closed; emoji for everything in between.
function effective(mr) {
  const gl = mr.gitlab;
  if (withdrawn(mr)) return "closed";
  if (gl?.state === "merged") return "merged";
  if (gl?.state === "closed") return "closed";
  if (gl && mr.emoji_state === "merged") return "approved";
  return mr.emoji_state;
}

function lies(mr) {
  const gl = mr.gitlab,
    out = [];
  if (!gl || withdrawn(mr)) return out;
  if (gl.state === "merged" && mr.emoji_state !== "merged")
    out.push(["forgot ⛙", "warn"]);
  if (gl.state === "opened" && mr.emoji_state === "merged")
    out.push(["⛙ but still open", "bad"]);
  if (
    gl.state === "opened" &&
    mr.emoji_state === "approved" &&
    !gl.approved_by.length
  )
    out.push(["✅ but 0 GitLab approvals", "bad"]);
  if (gl.state === "closed") out.push(["closed, no ❌", "warn"]);
  return out;
}

function age(ms) {
  const d = (Date.now() - ms) / DAY;
  const txt =
    d < 1 ? `${Math.max(1, Math.round(d * 24))}h` : `${Math.round(d)}d`;
  const cls = d > 14 ? "ancient" : d > 4 ? "old" : "";
  return `<span class="age ${cls}" title="${new Date(ms).toLocaleString()}">${txt}</span>`;
}

function card(mr, me) {
  const gl = mr.gitlab,
    chips = [];
  if (mr.ticket) chips.push(`<span class="chip">${esc(mr.ticket)}</span>`);
  if (mr.size)
    chips.push(
      `<span class="chip">${esc(mr.size.toLowerCase())} +${mr.added}/−${mr.removed}</span>`,
    );
  if (gl?.draft) chips.push(`<span class="chip warn">draft</span>`);
  if (gl?.conflicts) chips.push(`<span class="chip bad">conflicts</span>`);
  if (gl?.pipeline === "failed")
    chips.push(`<span class="chip bad">pipeline ✗</span>`);
  if (gl?.approved_by?.length)
    chips.push(
      `<span class="chip ok">approved: ${gl.approved_by.map(esc).join(", ")}</span>`,
    );
  if (mr.reactions.some((r) => HOLD.has(r.emoji)))
    chips.push(`<span class="chip warn">on hold</span>`);
  for (const [txt, cls] of lies(mr))
    chips.push(`<span class="chip ${cls}">${txt}</span>`);

  const rx = mr.reactions
    .map(
      (r) =>
        `<span title="${new Date(r.at).toLocaleString()}">${EMOJI[r.emoji] || ":" + esc(r.emoji) + ":"} ${esc(U(r.user_id))}</span>`,
    )
    .join("");
  // The GitLab link stretches over the whole card; the Mattermost link sits on top of it.
  return `<div class="card${me && touches(mr, me) ? " me" : ""}">
    <div class="top"><span>${esc(short(mr.project))} !${mr.mr_iid} · ${esc(author(mr))}</span>${age(since(mr))}</div>
    <div class="t"><a class="gl" href="${esc(mr.mr_url)}" target="_blank" rel="noopener">${esc(mr.title)}</a></div>
    <div class="meta">${chips.join("")}</div>
    ${rx ? `<div class="rx">${rx}</div>` : ""}
    ${mr.replies ? `<div class="rx">💬 ${mr.replies} in thread</div>` : ""}
    <div class="when">posted ${age(mr.posted_at)} ago${mr.target && mr.target !== "master" ? ` · → ${esc(mr.target)}` : ""}
      · <a class="mm" href="${esc(postUrl(mr))}" target="_blank" rel="noopener">Mattermost →</a></div>
  </div>`;
}

function filtered() {
  const q = $("q").value.toLowerCase().trim();
  const proj = $("proj").value;
  const me = $("me").value.trim();
  return DATA.mrs.filter((mr) => {
    if (proj && mr.project !== proj) return false;
    if ($("mine").checked && me && !touches(mr, me)) return false;
    if ($("hideDrafts").checked && mr.gitlab?.draft) return false;
    if (
      q &&
      ![mr.title, mr.ticket, mr.app, mr.source, mr.project, author(mr)]
        .join(" ")
        .toLowerCase()
        .includes(q)
    )
      return false;
    return true;
  });
}

function render() {
  if (!DATA) return;
  const me = $("me").value.trim();
  store.set("me", me);
  store.set("filters", {
    mine: $("mine").checked,
    hideDrafts: $("hideDrafts").checked,
    proj: $("proj").value,
  });
  const mrs = filtered();
  const now = Date.now();

  const by = Object.fromEntries(COLS.map(([k]) => [k, []]));
  for (const mr of mrs) {
    const s = effective(mr);
    if (s === "merged") {
      const at = mr.gitlab?.merged_at
        ? Date.parse(mr.gitlab.merged_at)
        : since(mr);
      if (now - at < DATA.merged_days * DAY) by.merged.push(mr);
    } else if (by[s]) by[s].push(mr);
  }
  for (const k in by)
    by[k].sort((a, b) =>
      k === "merged" ? since(b) - since(a) : since(a) - since(b),
    );

  $("board").innerHTML = COLS.map(
    ([k, label]) => `
    <div class="col${collapsed.has(k) ? " collapsed" : ""}" data-col="${k}">
      <h2>${k === "merged" ? `${label} (${DATA.merged_days} days)` : label}<span class="n">${by[k].length}</span></h2>
      ${by[k].map((mr) => card(mr, me)).join("") || `<div class="empty">Nothing here 🎉</div>`}
    </div>`,
  ).join("");

  const open = mrs.filter(
    (mr) => !["merged", "closed"].includes(effective(mr)),
  );
  const waits = open
    .map((mr) => (now - mr.posted_at) / DAY)
    .sort((a, b) => a - b);
  const median = waits.length ? waits[Math.floor(waits.length / 2)] : 0;
  const stat = (n, label) =>
    `<div class="stat"><b>${n}</b><span>${label}</span></div>`;
  $("stats").innerHTML =
    stat(open.length, "open MRs") +
    stat(by.needs_review.length, "nobody's looking") +
    stat(by.approved.length, "approved, not merged") +
    stat(`${median.toFixed(1)}d`, "median open age") +
    stat(mrs.filter((mr) => lies(mr).length).length, "forgotten updates");

  renderForgotten(mrs);
}

function renderForgotten(mrs) {
  // Oldest first: the longer the emoji has been wrong, the more it needs fixing.
  const stale = mrs
    .filter((mr) => lies(mr).length)
    .sort((a, b) => a.posted_at - b.posted_at);
  $("forgottenN").textContent = DATA.gitlab_checked ? stale.length : "";
  $("forgotten").innerHTML = !DATA.gitlab_checked
    ? `<li class="empty">GitLab check disabled (no GITLAB_TOKEN).</li>`
    : stale.length
      ? stale
          .map(
            (mr) => `<li>
      <a href="${esc(mr.mr_url)}" target="_blank" rel="noopener">${esc(short(mr.project))} !${mr.mr_iid}</a>
      <span class="t">${esc(mr.title)}</span>
      <span class="who">${esc(author(mr))} · ${age(mr.posted_at)}</span>
      <span class="meta">${lies(mr)
        .map(([txt, cls]) => `<span class="chip ${cls}">${txt}</span>`)
        .join("")}</span>
      <a class="fix" href="${esc(postUrl(mr))}" target="_blank" rel="noopener">fix the emoji →</a>
    </li>`,
          )
          .join("")
      : `<li class="empty">Every emoji matches GitLab 🎉</li>`;
}

async function load() {
  try {
    const resp = await fetch("api/board");
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || resp.statusText);
    const first = !DATA;
    DATA = body;
    $("banner").textContent = body.error
      ? `Last harvest failed, showing older data: ${body.error}`
      : "";
    $("sub").textContent =
      `#${DATA.channel} · posts from the last ${DATA.days} days · ${DATA.mrs.length} MRs · harvested ${new Date(DATA.generated_at).toLocaleTimeString()}`;
    if (first) fillProjects();
    render();
  } catch (err) {
    $("banner").textContent = `Could not load the board: ${err.message}`;
    if (!DATA) setTimeout(load, 5000);
  }
}

function fillProjects() {
  const sel = $("proj"),
    saved = store.get("filters", {});
  for (const p of [...new Set(DATA.mrs.map((m) => m.project))].sort())
    sel.insertAdjacentHTML(
      "beforeend",
      `<option value="${esc(p)}">${esc(short(p))}</option>`,
    );
  sel.value = saved.proj || "";
}

async function refresh() {
  const btn = $("refresh");
  btn.disabled = true;
  btn.textContent = "↻ Harvesting…";
  try {
    const resp = await fetch("api/refresh", { method: "POST" });
    if (!resp.ok) $("banner").textContent = (await resp.json()).detail;
    await load();
  } finally {
    btn.disabled = false;
    btn.textContent = "↻ Refresh";
  }
}

function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}

// Init
const saved = store.get("filters", {});
$("me").value = store.get("me", "");
$("mine").checked = !!saved.mine;
$("hideDrafts").checked = !!saved.hideDrafts;
applyTheme(store.get("theme", null));

for (const id of ["q", "proj", "me", "mine", "hideDrafts"])
  $(id).addEventListener("input", render);
$("refresh").addEventListener("click", refresh);
$("theme").addEventListener("click", () => {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const cur =
    document.documentElement.dataset.theme || (dark ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  applyTheme(next);
  store.set("theme", next);
});
$("board").addEventListener("click", (e) => {
  const h = e.target.closest("h2");
  if (!h) return;
  const k = h.parentElement.dataset.col;
  collapsed.has(k) ? collapsed.delete(k) : collapsed.add(k);
  store.set("collapsed", [...collapsed]);
  render();
});

load();
setInterval(load, POLL_MS);
