"use strict";

const DAY = 864e5;
const POLL_MS = 60_000;
const MERGED_PREVIEW = 6;
// Mirrors the emoji sets in harvest.py.
const REVIEW = new Set(["eyes", "eyesintensify"]);
const COMMENT = new Set(["memo", "pencil", "pencil2"]);
const APPROVE = new Set(["white_check_mark", "heavy_check_mark"]);
const HOLD = new Set(["zzz", "hourglass_flowing_sand", "hourglass"]);
const SIZES = {
  "VERY SMALL": "XS",
  SMALL: "S",
  MEDIUM: "M",
  LARGE: "L",
  "VERY LARGE": "XL",
};
const STATE_WORD = {
  reviewing: "reviewing",
  changes: "asked for changes",
  approved: "approved",
};

const svg = (paths, size = 14) =>
  `<svg aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24">${paths}</svg>`;
const ICON = {
  inbox: svg(
    '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1Z"/>',
  ),
  eye: svg(
    '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  ),
  pencil: svg(
    '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  ),
  check: svg('<path d="M20 6 9 17l-5-5"/>'),
  merge: svg(
    '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>',
  ),
  sync: svg('<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>'),
};
const COLS = [
  ["needs_review", "Needs review", ICON.inbox, "Nothing waiting. Every open MR has a reviewer."],
  ["in_review", "In review", ICON.eye, "No reviews in progress."],
  ["commented", "Back to author", ICON.pencil, "Nothing waiting on authors."],
  ["approved", "Approved", ICON.check, "Nothing approved and unmerged."],
  ["merged", "Merged", ICON.merge, "Nothing merged lately."],
];

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
let allMerged = false;

const U = (id) => DATA.users[id] || id.slice(0, 6);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
const short = (p) => p.split("/").at(-1);
const ref = (mr) => `${short(mr.project)} !${mr.mr_iid}`;
// Mattermost resolves the team itself from a /_redirect permalink.
const postUrl = (mr) => `${DATA.mm_url}/_redirect/pl/${mr.post_id}`;
const author = (mr) => mr.gitlab?.author || U(mr.author_id);
const since = (mr) =>
  mr.reactions.length ? mr.reactions.at(-1).at : mr.posted_at;
const pressed = (id) => $(id).getAttribute("aria-pressed") === "true";
const isDraft = (mr) => !!mr.gitlab?.draft || /^\s*draft:/i.test(mr.title);
const touches = (mr, me) =>
  author(mr) === me || mr.reactions.some((r) => U(r.user_id) === me);
const who = (name, me) =>
  `<span class="who${name === me ? " you" : ""}">${esc(name)}</span>`;

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

// Where the reactions disagree with GitLab: [short label for cards, severity, sentence].
function lies(mr) {
  const gl = mr.gitlab,
    out = [];
  if (!gl || withdrawn(mr)) return out;
  if (gl.state === "merged" && mr.emoji_state !== "merged")
    out.push(["No merged reaction", "warn", "Merged in GitLab, no merged reaction"]);
  if (gl.state === "opened" && mr.emoji_state === "merged")
    out.push(["Merged reaction, still open", "bad", "Merged reaction, but still open in GitLab"]);
  if (
    gl.state === "opened" &&
    mr.emoji_state === "approved" &&
    !gl.approved_by.length
  )
    out.push(["No GitLab approval", "bad", "Approved reaction, but no approval in GitLab"]);
  if (
    gl.state === "opened" &&
    !["approved", "merged"].includes(mr.emoji_state) &&
    gl.approved_by.length
  )
    out.push(["No approved reaction", "warn", "Approved in GitLab, no approved reaction"]);
  if (
    gl.state === "opened" &&
    gl.changes_requested_by?.length &&
    mr.emoji_state !== "commented"
  )
    out.push([
      "Changes requested in GitLab",
      mr.emoji_state === "approved" ? "bad" : "warn",
      "Changes requested in GitLab, no pencil reaction",
    ]);
  if (gl.state === "closed") out.push(["No closed reaction", "warn", "Closed in GitLab, no closed reaction"]);
  return out;
}

// Each reviewer's latest review reaction, plus GitLab reviews nobody reacted for.
function reviewers(mr) {
  const by = new Map(),
    me = author(mr);
  for (const r of mr.reactions) {
    const st = REVIEW.has(r.emoji)
      ? "reviewing"
      : COMMENT.has(r.emoji)
        ? "changes"
        : APPROVE.has(r.emoji)
          ? "approved"
          : null;
    const name = U(r.user_id);
    if (st && name !== me) by.set(name, st);
  }
  for (const name of mr.gitlab?.approved_by || [])
    if (!by.has(name)) by.set(name, "approved");
  for (const name of mr.gitlab?.changes_requested_by || [])
    if (!by.has(name)) by.set(name, "changes");
  return [...by].map(([name, st]) => ({ name, st }));
}

function ageTxt(ms) {
  const d = (Date.now() - ms) / DAY;
  return d < 1 ? `${Math.max(1, Math.round(d * 24))}h` : `${Math.round(d)}d`;
}

function age(ms) {
  const d = (Date.now() - ms) / DAY;
  const cls = d > 14 ? "ancient" : d > 4 ? "old" : "";
  return `<span class="age ${cls}" title="${new Date(ms).toLocaleString()}">${ageTxt(ms)}</span>`;
}

// The title without the "Draft:" prefix and ticket, which the card shows on their own.
function title(mr) {
  let t = mr.title.replace(/^\s*draft:\s*/i, "");
  if (mr.ticket && t.startsWith(mr.ticket))
    t = t.slice(mr.ticket.length).replace(/^[\s:–-]+/, "");
  return t || mr.title;
}

function card(mr, me) {
  const gl = mr.gitlab,
    flags = [];
  if (isDraft(mr)) flags.push(["Draft", "warn"]);
  if (gl?.conflicts) flags.push(["Conflicts", "bad"]);
  if (gl?.pipeline === "failed") flags.push(["Pipeline failed", "bad"]);
  if (mr.reactions.some((r) => HOLD.has(r.emoji))) flags.push(["On hold", "warn"]);
  for (const [txt, cls] of lies(mr)) flags.push([txt, cls]);

  const revs = reviewers(mr);
  const mine = author(mr) === me;
  const reviewing = !mine && revs.some((r) => r.name === me);
  const role = !me ? "" : mine ? "Yours" : reviewing ? "Reviewing" : "";
  const size = mr.size
    ? `<div class="size"><span class="lbl">${esc(SIZES[mr.size] || mr.size.toLowerCase())}</span>
       <span class="add">+${mr.added}</span><span class="del">−${mr.removed}</span></div>`
    : "";
  const kind = !role ? "" : mine ? " own" : " rev";
  return `<article class="card${kind}">
    <div class="top"><span class="ref">${esc(ref(mr))}</span>${role ? `<span class="role${kind}">${role}</span>` : ""}${age(since(mr))}</div>
    <h3><a href="${esc(mr.mr_url)}" target="_blank" rel="noopener">${mr.ticket ? `<span class="ticket">${esc(mr.ticket)}</span> ` : ""}${esc(title(mr))}</a></h3>
    ${size}
    ${flags.length ? `<div class="flags">${flags.map(([t, c]) => `<span class="flag ${c}">${esc(t)}</span>`).join("")}</div>` : ""}
    <div class="foot">
      <div class="people">
        ${who(author(mr), me)}<span class="arrow" aria-hidden="true">→</span>
        ${
          revs
            .map(
              (r) =>
                `<span class="rev">${who(r.name, me)}<span class="st ${r.st}">${STATE_WORD[r.st]}</span></span>`,
            )
            .join("") || `<span class="nobody">no reviewer yet</span>`
        }
      </div>
      <div class="links">
        <span class="branch" title="Target branch">${mr.target && mr.target !== "master" ? `into ${esc(mr.target)}` : ""}</span>
        <a class="mm" href="${esc(postUrl(mr))}" target="_blank" rel="noopener">Mattermost →</a>
      </div>
    </div>
  </article>`;
}

function mergedRow(mr, me) {
  const at = mergedAt(mr);
  return `<a class="merged-row" href="${esc(mr.mr_url)}" target="_blank" rel="noopener">
    <span class="t">${esc(mr.title)}</span>
    <span class="m"><span>${esc(ref(mr))}</span><span>·</span><span class="${author(mr) === me ? "you" : ""}">${esc(author(mr))}</span>
      <span class="when" title="${new Date(at).toLocaleString()}">${ageTxt(at)}</span></span>
  </a>`;
}

const mergedAt = (mr) =>
  mr.gitlab?.merged_at ? Date.parse(mr.gitlab.merged_at) : since(mr);

function filtered() {
  const q = $("q").value.toLowerCase().trim();
  const proj = $("proj").value;
  const me = $("me").value.trim();
  return DATA.mrs.filter((mr) => {
    if (proj && mr.project !== proj) return false;
    if (pressed("mine") && me && !touches(mr, me)) return false;
    if (pressed("hideDrafts") && isDraft(mr)) return false;
    if (
      q &&
      ![mr.title, mr.ticket, mr.app, mr.source, mr.target, mr.project, author(mr)]
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
    mine: pressed("mine"),
    hideDrafts: pressed("hideDrafts"),
    proj: $("proj").value,
  });
  const mrs = filtered();
  const now = Date.now();

  const by = Object.fromEntries(COLS.map(([k]) => [k, []]));
  for (const mr of mrs) {
    const s = effective(mr);
    if (s === "merged") {
      if (now - mergedAt(mr) < DATA.merged_days * DAY) by.merged.push(mr);
    } else if (by[s]) by[s].push(mr);
  }
  for (const k in by)
    by[k].sort((a, b) =>
      k === "merged" ? mergedAt(b) - mergedAt(a) : since(a) - since(b),
    );

  $("board").innerHTML = COLS.map(([k, label, icon, none]) => {
    const list = by[k];
    let body;
    if (!list.length) body = `<div class="empty">${none}</div>`;
    else if (k === "merged") {
      const shown = allMerged ? list : list.slice(0, MERGED_PREVIEW);
      body =
        shown.map((mr) => mergedRow(mr, me)).join("") +
        (list.length > MERGED_PREVIEW
          ? `<button class="more" data-more>${allMerged ? "Show fewer" : `Show all ${list.length}`}</button>`
          : "");
    } else body = list.map((mr) => card(mr, me)).join("");
    return `<section class="col c-${k}${collapsed.has(k) ? " collapsed" : ""}" data-col="${k}">
      <button class="col-head" aria-expanded="${!collapsed.has(k)}" title="Collapse or expand">
        <span class="ico">${icon}</span>
        <h2>${label}${k === "merged" ? ` <small>· ${DATA.merged_days} days</small>` : ""}</h2>
        <span class="n">${list.length}</span>
      </button>
      ${body}
    </section>`;
  }).join("");

  const open = mrs.filter(
    (mr) => !["merged", "closed"].includes(effective(mr)),
  );
  const waits = open
    .map((mr) => (now - mr.posted_at) / DAY)
    .sort((a, b) => a - b);
  const median = waits.length ? waits[Math.floor(waits.length / 2)] : 0;
  const outOfSync = mrs.filter((mr) => lies(mr).length);
  const stat = (n, label, cls = "") =>
    `<div class="stat"><b class="${cls}">${n}</b><span>${label}</span></div>`;
  $("stats").innerHTML =
    stat(open.length, "open MRs") +
    stat(by.needs_review.length, "without a reviewer", by.needs_review.length ? "warn" : "ok") +
    stat(by.approved.length, "approved, not merged") +
    stat(`${median.toFixed(1)}<small>d</small>`, "median open age") +
    stat(
      DATA.gitlab_checked ? outOfSync.length : "–",
      "reactions out of sync",
      outOfSync.length ? "warn" : "",
    );

  renderYours(mrs, me);
  renderForgotten(outOfSync);
}

// What is waiting on `me`, one entry per MR, oldest first.
function yourTurn(mrs, me) {
  const out = [];
  for (const mr of mrs) {
    const s = effective(mr),
      gl = mr.gitlab,
      mine = author(mr) === me;
    const revs = reviewers(mr);
    const myReview = revs.find((r) => r.name === me)?.st;
    let item = null;
    if (gl?.state === "opened" && gl.changes_requested_by?.includes(me) && mr.emoji_state !== "commented")
      item = ["sync", "You requested changes in GitLab: add the pencil reaction", "Open thread", postUrl(mr)];
    else if (gl?.state === "opened" && gl.approved_by?.includes(me) && !["approved", "merged"].includes(mr.emoji_state))
      item = ["sync", "You approved in GitLab: add the approved reaction", "Open thread", postUrl(mr)];
    else if (mine && lies(mr).length && ["merged", "closed"].includes(s))
      item = ["sync", `Your MR is ${s} in GitLab: update its reaction`, "Open thread", postUrl(mr)];
    else if (mine && s === "commented") {
      const asked = revs.filter((r) => r.st === "changes").map((r) => r.name);
      item = ["pencil", `Address ${asked.length ? asked.join(" and ") + "’s" : "the"} review on your MR`, "Open MR", mr.mr_url];
    } else if (mine && s === "approved")
      item = ["check", "Your MR is approved: merge it", "Open MR", mr.mr_url];
    else if (!mine && s === "in_review" && myReview === "reviewing")
      item = ["eye", "Finish your review", "Open MR", mr.mr_url];
    if (item) out.push([mr, ...item]);
  }
  return out.sort((a, b) => since(a[0]) - since(b[0]));
}

function renderYours(mrs, me) {
  if (!me) {
    $("yoursN").textContent = "";
    $("yours").innerHTML = `<div class="none">Enter your username at the top right to see what is waiting on you.</div>`;
    return;
  }
  const items = yourTurn(mrs, me);
  $("yoursN").textContent = items.length;
  const tone = { sync: "", pencil: "", check: "ok", eye: "accent" };
  $("yours").innerHTML = items.length
    ? items
        .map(
          ([mr, kind, what, linkTxt, href]) => `<div class="row">
      <span class="ico ${tone[kind]}">${ICON[kind]}</span>
      <div class="what"><b>${esc(what)}</b>
        <span><span class="mono">${esc(ref(mr))}</span> · ${esc(mr.title)} · ${author(mr) === me ? "" : `by ${esc(author(mr))}, `}${ageTxt(since(mr))}</span></div>
      <a href="${esc(href)}" target="_blank" rel="noopener">${linkTxt} →</a>
    </div>`,
        )
        .join("")
    : `<div class="none">Nothing is waiting on you.</div>`;
}

function renderForgotten(stale) {
  // Oldest first: the longer the reaction has been wrong, the more it needs fixing.
  stale = [...stale].sort((a, b) => a.posted_at - b.posted_at);
  $("forgottenN").textContent = DATA.gitlab_checked ? stale.length : "";
  $("forgotten").innerHTML = !DATA.gitlab_checked
    ? `<div class="none">GitLab check disabled (no GITLAB_TOKEN).</div>`
    : stale.length
      ? stale
          .map(
            (mr) => `<div class="sync">
      <div class="l1"><a class="mono" href="${esc(mr.mr_url)}" target="_blank" rel="noopener">${esc(ref(mr))}</a>
        <span class="t" title="${esc(mr.title)}">${esc(mr.title)}</span></div>
      <div class="l2"><span>${lies(mr)
        .map(([, cls, txt]) => `<span class="${cls}">${esc(txt)}</span>`)
        .join(" · ")} · ${esc(author(mr))} · ${ageTxt(mr.posted_at)}</span>
        <a href="${esc(mr.mr_url)}" target="_blank" rel="noopener">Open in GitLab →</a></div>
    </div>`,
          )
          .join("")
      : `<div class="none">Every reaction matches GitLab.</div>`;
}

function showBanner(text) {
  $("banner").textContent = text;
  if (text.includes("/oauth/login"))
    $("banner").insertAdjacentHTML("beforeend", ' <a href="oauth/login">Connect to Mattermost →</a>');
}

async function load() {
  try {
    const resp = await fetch("api/board");
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || resp.statusText);
    const first = !DATA;
    DATA = body;
    showBanner(body.error ? `Last harvest failed, showing older data: ${body.error}` : "");
    $("sub").textContent = `#${DATA.channel} · ${DATA.mrs.length} MRs · harvested ${new Date(DATA.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    $("sub").title = `Open MRs plus finished ones from the last ${DATA.days} days`;
    if (first) fillProjects();
    render();
  } catch (err) {
    showBanner(`Could not load the board: ${err.message}`);
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
  const btn = $("refresh"),
    label = btn.querySelector("span");
  btn.disabled = true;
  label.textContent = "Harvesting…";
  try {
    const resp = await fetch("api/refresh", { method: "POST" });
    const detail = resp.ok ? null : (await resp.json()).detail;
    await load();
    if (detail) showBanner(detail); // load() resets the banner
  } finally {
    btn.disabled = false;
    label.textContent = "Refresh";
  }
}

function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}

// Init
const saved = store.get("filters", {});
$("me").value = store.get("me", "");
$("mine").setAttribute("aria-pressed", !!saved.mine);
$("hideDrafts").setAttribute("aria-pressed", !!saved.hideDrafts);
applyTheme(store.get("theme", null));

for (const id of ["q", "proj", "me"]) $(id).addEventListener("input", render);
for (const id of ["mine", "hideDrafts"])
  $(id).addEventListener("click", () => {
    $(id).setAttribute("aria-pressed", !pressed(id));
    render();
  });
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
  if (e.target.closest("[data-more]")) {
    allMerged = !allMerged;
    render();
    return;
  }
  const h = e.target.closest(".col-head");
  if (!h) return;
  const k = h.parentElement.dataset.col;
  collapsed.has(k) ? collapsed.delete(k) : collapsed.add(k);
  store.set("collapsed", [...collapsed]);
  render();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.target.closest("input, select, textarea")) return;
  e.preventDefault();
  $("q").focus();
});

load();
setInterval(load, POLL_MS);
