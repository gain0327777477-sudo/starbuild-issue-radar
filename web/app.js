/* 스타빌드 이슈레이더 — 화면 동작
 * 데이터: data/status.json, data/issues.json, data/today.json, data/packets/<이슈id>.json
 * 뉴스 데이터는 항상 네트워크 우선. 실패하면 마지막 저장본을 보여주고 "오프라인 · 저장된 데이터" 표시.
 * 저장한 소재는 이 기기(브라우저) 안에만 저장 — 서버·저장소로 보내지 않음.
 */
"use strict";
(() => {
  const DATA = "data/";
  const STALE_HOURS = 3;                       // 마지막 수집이 이보다 오래되면 크게 경고
  const DATA_CACHE = "radar-data-v1";
  const PAGE = 40;
  const MAX_SAVED = 200;
  const LS = { saved: "radar.saved.v1", theme: "radar.theme", size: "radar.size", tab: "radar.tab", share: "radar.share" };
  const TABS = ["today", "증시·경제", "금리·대출", "숙박·관광", "부동산·상권", "지역개발"];
  const CAT_SHORT = {
    "대출 가능·불가 판단": "대출 판단", "건물매매전략 매물 접수": "매물 접수", "리모델링 위탁 운영": "리모델링 위탁",
    "모텔호텔": "모텔·호텔", "빌라 아파트": "빌라·아파트",
  };
  const CTA_NAME = { loan: "대출 상담", listing: "매물 접수·매수 상담", remodel: "리모델링 위탁 상담", soft: "부드러운 안내" };

  const state = {
    status: null, issues: [], today: null, byId: new Map(),
    tab: "today", q: "", region: "", sort: "score", shown: PAGE,
    offline: false, errors: [], loadedAt: 0,
  };

  // ── 작은 도구 ───────────────────────────────
  const $ = (sel) => document.querySelector(sel);

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false || kid === "") continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  function lsGet(key, fallback) {
    try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
  }
  function lsSet(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
  }

  function safeUrl(u) {
    try { const x = new URL(u, location.href); return x.protocol === "https:" || x.protocol === "http:" ? x.href : null; }
    catch { return null; }
  }

  function ago(iso) {
    const t = Date.parse(iso);
    if (!t) return "";
    const m = Math.round((Date.now() - t) / 60000);
    if (m < 1) return "방금";
    if (m < 60) return `${m}분 전`;
    const hh = Math.floor(m / 60);
    if (hh < 24) return `${hh}시간 전`;
    return `${Math.floor(hh / 24)}일 전`;
  }
  function clock(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  const short = (cat) => CAT_SHORT[cat] || cat;

  let toastTimer = 0;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 2200);
  }

  // ── 데이터 불러오기 (네트워크 우선, 실패하면 저장본) ──
  async function loadJSON(path) {
    const url = DATA + path;
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 8000) : 0;   // 느린 망에서 무한정 기다리지 않게
    try {
      const res = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store", signal: ctrl ? ctrl.signal : undefined });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      clearTimeout(timer);
      if (window.caches) {
        try {
          const c = await caches.open(DATA_CACHE);
          await c.put(url, new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } }));
        } catch { /* 저장 공간 부족 등 — 화면은 계속 */ }
      }
      return { data, fromCache: false };
    } catch (err) {
      clearTimeout(timer);
      if (window.caches) {
        try {
          const c = await caches.open(DATA_CACHE);
          const hit = await c.match(url);
          if (hit) return { data: await hit.json(), fromCache: true };
        } catch { /* 무시 */ }
      }
      throw err;
    }
  }

  async function loadAll() {
    const names = ["status.json", "issues.json", "today.json"];
    const res = await Promise.allSettled(names.map(loadJSON));
    state.errors = [];
    state.offline = false;
    res.forEach((r, i) => {
      if (r.status === "rejected") state.errors.push(`${names[i]}: ${r.reason && r.reason.message || r.reason}`);
      else if (r.value.fromCache) state.offline = true;
    });
    if (res[0].status === "fulfilled") state.status = res[0].value.data;
    if (res[1].status === "fulfilled") {
      state.issues = res[1].value.data.issues || [];
      state.byId = new Map(state.issues.map((i) => [i.id, i]));
    }
    if (res[2].status === "fulfilled") {
      state.today = res[2].value.data;
      for (const i of state.today.issues || []) if (!state.byId.has(i.id)) state.byId.set(i.id, i);
    }
    state.loadedAt = Date.now();
    try {                                         // 배포 버전·시각 (설정 화면 표시, 반영 지연 확인용)
      const v = await fetch(`version.json?t=${Date.now()}`, { cache: "no-store" });
      state.version = v.ok ? await v.json() : null;
    } catch { state.version = null; }
    fillRegions();
    render();
    pruneDataCache();
  }

  // 열어 본 패킷 저장본 중 목록에서 빠진 이슈의 것은 지움 (저장함에 넣은 소재는 남김)
  // ※ 패킷은 '열어 본 것만' 저장됨 — 500여 개를 한꺼번에 받지 않음
  async function pruneDataCache() {
    if (!window.caches || state.offline || !state.issues.length) return;
    try {
      const keep = new Set(state.issues.map((i) => i.id));
      for (const s of lsGet(LS.saved, [])) keep.add(s.id);
      const c = await caches.open(DATA_CACHE);
      for (const req of await c.keys()) {
        const m = req.url.match(/\/packets\/([^/?]+)\.json$/);
        if (m && !keep.has(decodeURIComponent(m[1]))) await c.delete(req);
      }
    } catch { /* 무시 */ }
  }

  // ── 위쪽 알림(경고·상태) ─────────────────────
  // 일정(한국 시간): status.json 의 schedule — 기본값은 06~22시 2시간마다, 22~07시 야간 휴식, 낮 3시간 경고
  const DEFAULT_SCHEDULE = { kst_hours: [6, 8, 10, 12, 14, 16, 18, 20, 22], quiet_hours: { start: 22, end: 7 }, stale_hours: 3 };
  const kstHour = (d = new Date()) => (d.getUTCHours() + 9) % 24;
  function schedule() { return Object.assign({}, DEFAULT_SCHEDULE, (state.status && state.status.schedule) || {}); }
  function isQuiet(sc) {
    const h = kstHour(), q = sc.quiet_hours;
    return q.start > q.end ? (h >= q.start || h < q.end) : (h >= q.start && h < q.end);
  }
  function nextHour(sc) {
    const h = kstHour(), hours = [...sc.kst_hours].sort((a, b) => a - b);
    return hours.find((x) => x > h) ?? hours[0];
  }
  const two = (n) => String(n).padStart(2, "0");

  function renderBanners() {
    const box = $("#banners");
    const line = $("#status-line");
    box.replaceChildren();
    const st = state.status;
    const sc = schedule();
    const quiet = isQuiet(sc);

    // 제목 줄 안의 한 줄 상태: "13분 전 수집 · 다음 10시" / 밤: "… · 야간 휴식 중 · 06시 재개"
    if (st && st.collected_at) {
      const failed = (st.sources || []).filter((s) => s.status === "failed" || s.status === "partial");
      const parts = [`${ago(st.collected_at)} 수집`];
      parts.push(quiet ? `🌙 야간 휴식 중 · ${two(Math.min(...sc.kst_hours))}시 재개` : `다음 ${two(nextHour(sc))}시`);
      line.replaceChildren(parts.join(" · "), failed.length ? h("span", { class: "warn-dot", text: ` · 실패 ${failed.length}` }) : "");
      line.title = `마지막 수집 ${clock(st.collected_at)}`;
    } else {
      line.textContent = state.errors.length ? "데이터를 불러오지 못함" : "불러오는 중…";
    }

    if (!window.isSecureContext) {
      box.append(h("div", { class: "banner banner--info" },
        h("strong", { text: "미리 보기(HTTP) 모드" }),
        "홈 화면 설치·오프라인 저장·공유 버튼은 HTTPS 주소(GitHub Pages)에서만 동작합니다. 복사는 됩니다."));
    }
    if (state.offline) {
      box.append(h("div", { class: "banner banner--warn" },
        h("strong", { text: "오프라인 · 저장된 데이터" }),
        st ? `마지막으로 받아 둔 ${clock(st.collected_at)} 수집 결과를 보여줍니다.` : "마지막으로 받아 둔 결과를 보여줍니다."));
    }
    if (state.errors.length && !state.issues.length) {
      box.append(h("div", { class: "banner banner--danger" },
        h("strong", { text: "데이터를 불러오지 못했습니다" }),
        state.errors.join(" / "),
        h("div", {}, h("button", { type: "button", onclick: loadAll, text: "다시 시도" }))));
    }
    if (st && st.collected_at) {
      const hours = (Date.now() - Date.parse(st.collected_at)) / 3600000;
      // 낮(06~22시)에만 경고. 밤(22~07시)은 원래 쉬는 시간이라 제목 줄의 '야간 휴식 중' 안내만
      if (hours > sc.stale_hours && !quiet) {
        box.append(h("div", { class: "banner banner--danger", role: "alert" },
          h("strong", { text: "⚠ 수집이 멈췄습니다" }),
          `마지막 수집 ${ago(st.collected_at)} (${clock(st.collected_at)}). 낮에는 2시간마다 수집합니다. ` +
          "GitHub 자동 수집이 꺼졌을 수 있으니 README의 '수집이 멈췄을 때'를 보고 다시 켜 주세요."));
      }
      const failed = (st.sources || []).filter((s) => s.status === "failed" || s.status === "partial");
      if (failed.length) {
        box.append(h("div", { class: "chips" }, failed.map((s) => h("button", { type: "button", class: "chip-fail",
          onclick: () => openStatus(), text: `${s.status === "failed" ? "실패" : "일부 실패"}: ${s.name}` }))));
      }
    }
  }

  // ── 거르기·정렬 ────────────────────────────
  function fillRegions() {
    const sel = $("#region");
    const counts = new Map();
    for (const i of state.issues) for (const t of i.region_tags || []) counts.set(t.sido, (counts.get(t.sido) || 0) + 1);
    const cur = state.region;
    sel.replaceChildren(h("option", { value: "", text: "전체 지역" }),
      ...[...counts.entries()].sort((a, b) => b[1] - a[1]).map(([s, n]) => h("option", { value: s, text: `${s} (${n})` })));
    sel.value = counts.has(cur) ? cur : "";
    state.region = sel.value;
  }

  function matches(i) {
    if (state.region && !(i.region_tags || []).some((t) => t.sido === state.region)) return false;
    if (!state.q) return true;
    const q = state.q.toLowerCase();
    const hay = [i.title, i.field_name, i.press, ...(i.core_keywords || []), ...(i.regions || []),
      ...(i.articles || []).map((a) => a.title)].join(" ").toLowerCase();
    return q.split(/\s+/).every((w) => hay.includes(w));
  }

  function sorted(list) {
    const arr = [...list];
    if (state.sort === "recent") arr.sort((a, b) => (b.latest || "").localeCompare(a.latest || ""));
    else arr.sort((a, b) => b.scores.total - a.scores.total);
    return arr;
  }

  // ── 카드 ───────────────────────────────────
  function stageBadge(st) {
    if (!st) return null;
    const hot = ["start", "done", "open", "flight"].includes(st.id) && !st.upcoming;
    return h("span", { class: `badge ${hot ? "badge--stage-hot" : "badge--stage"}` },
      st.name + (st.delayed ? " · 일정 변경" : ""));
  }
  function polarityBadge(p) {
    if (!p) return null;
    const cls = p === "호재" ? "badge--good" : p === "악재" ? "badge--bad" : "badge--both";
    return h("span", { class: `badge ${cls}`, text: p });
  }
  function directionBadge(d) {
    if (!d) return null;
    return h("span", { class: "badge badge--both", text: d.id === "unclear" ? "방향 불분명" : d.label.replace(/\(.*\)/, "") });
  }

  function isSaved(id) { return lsGet(LS.saved, []).some((s) => s.id === id); }

  function card(issue, slot) {
    const br = issue.bridge || {};
    const sc = issue.scores || {};
    const stars = sc.stars || 1;
    const linkBadge = br.strength === "약함"
      ? h("span", { class: "badge badge--weak", text: "연결 약함" })
      : h("span", { class: "badge badge--link", text: `→ ${short((br.categories || [])[0] || "")}` });

    const regionBtns = (issue.region_tags || []).slice(0, 2).map((t) =>
      h("button", { type: "button", class: "badge", "aria-label": `${t.sido} 지역만 보기`,
        onclick: () => setRegion(t.sido), text: `${t.sido}${t.area ? " " + t.area : ""}` }));

    const trend = issue.trend && issue.trend.rising ? h("span", { class: "badge badge--trend", text: "검색 추이↑" }) : null;
    const written = issue.written ? h("span", { class: "badge badge--written", title: issue.written.title, text: "블로그에 이미 씀" }) : null;

    const url = safeUrl(issue.url);
    const saved = isSaved(issue.id);
    const saveBtn = h("button", { type: "button", class: `btn ${saved ? "btn--saved" : "btn--primary"}`,
      onclick: () => openPacket(issue.id, true), text: saved ? "저장됨 · 보기" : "소재로 저장" });

    const reasons = h("ul", {}, (sc.reasons || []).map((r) =>
      h("li", {}, `${r.part} · ${r.label} `, h("span", { class: `reason-pts${r.points < 0 ? " neg" : ""}`, text: `${r.points > 0 ? "+" : ""}${r.points}` }))));
    const related = h("ul", {}, (issue.articles || []).map((a) => {
      const u = safeUrl(a.url);
      return h("li", {}, u ? h("a", { href: u, target: "_blank", rel: "noopener noreferrer", text: a.title }) : a.title,
        ` — ${a.press}, ${ago(a.published)}`);
    }));

    return h("article", { class: "card" },
      slot ? h("div", { class: "card__slot", text: slot }) : null,
      h("div", { class: "card__top" },
        h("span", { class: "badge badge--field", text: issue.field_name }),
        h("span", { class: "badge badge--stars", "aria-label": `집객 별 ${stars}개`, text: "★".repeat(stars) + "☆".repeat(3 - stars) }),
        linkBadge),
      h("h3", { class: "card__title", text: issue.title }),
      h("div", { class: "card__meta", text: `${issue.press} · ${ago(issue.latest)} · 관련 기사 ${issue.related_count}건` }),
      h("div", { class: "badges" }, directionBadge(br.direction), stageBadge(issue.stage), polarityBadge(br.polarity),
        regionBtns, trend, written),
      h("div", { class: "card__actions" },
        url ? h("a", { class: "btn", href: url, target: "_blank", rel: "noopener noreferrer", text: "원문 열기" }) : null,
        saveBtn),
      h("details", { class: "more" },
        h("summary", { text: `점수 근거 · 연결 논리 (총 ${sc.total}점)` }),
        h("p", {}, h("b", { text: "연결 논리: " }), br.logic || ""),
        br.other_categories && br.other_categories.length ? h("p", { text: `함께 연결: ${br.other_categories.map(short).join(", ")}` }) : null,
        issue.written ? h("p", { text: `최근 블로그 글과 겹침: ${issue.written.title}` }) : null,
        reasons,
        issue.trend ? h("p", { class: "note", text: "검색 추이는 네이버 데이터랩의 상대 추이입니다. 실제 조회수·실시간 순위가 아닙니다." }) : null,
        h("p", { class: "section-title", style: "margin-top:10px", text: "관련 기사" }), related));
  }

  // ── 목록 그리기 ─────────────────────────────
  function render() {
    renderBanners();
    const main = $("#main");
    main.replaceChildren();
    const filtering = state.q || state.region;

    if (!state.issues.length) {
      main.append(h("p", { class: "empty", text: state.errors.length ? "표시할 이슈가 없습니다." : "불러오는 중…" }));
      return;
    }

    if (state.tab === "today" && !filtering) {
      const picks = (state.today && state.today.picks) || [];
      main.append(h("h2", { class: "section-title", text: "오늘 쓸 글 추천" }));
      if (!picks.length) main.append(h("p", { class: "empty", text: "오늘 추천할 이슈가 없습니다." }));
      for (const p of picks) {
        const issue = state.byId.get(p.issue_id);
        if (issue) main.append(card(issue, `${p.slot} · ${p.ranked_by || "총점"} 순${p.from_backup ? " (대체 분야)" : ""}`));
      }
      const pickIds = new Set(picks.map((p) => p.issue_id));
      const rest = sorted(state.issues.filter((i) => !pickIds.has(i.id))).slice(0, 20);
      main.append(h("h2", { class: "section-title", text: "점수 상위 이슈" }));
      rest.forEach((i) => main.append(card(i)));
      return;
    }

    let list = state.issues.filter(matches);
    if (state.tab !== "today" && !state.q) list = list.filter((i) => i.tab === state.tab);
    list = sorted(list);
    const title = state.q ? `검색 결과 ${list.length}건` : `${state.tab === "today" ? "전체" : state.tab} ${list.length}건`;
    main.append(h("h2", { class: "section-title", text: title + (state.region ? ` · ${state.region}` : "") }));
    if (state.q && state.tab !== "today") main.append(h("p", { class: "section-note", text: "검색은 모든 분야에서 찾습니다." }));
    if (!list.length) main.append(h("p", { class: "empty", text: "조건에 맞는 이슈가 없습니다." }));
    list.slice(0, state.shown).forEach((i) => main.append(card(i)));
    if (list.length > state.shown) {
      main.append(h("button", { type: "button", class: "btn", style: "width:100%;margin:8px 0",
        onclick: () => { state.shown += PAGE; render(); }, text: `더 보기 (${list.length - state.shown}건 남음)` }));
    }
  }

  function setTab(tab) {
    state.tab = TABS.includes(tab) ? tab : "today";
    state.shown = PAGE;
    lsSet(LS.tab, state.tab);
    document.querySelectorAll("#tabbar button").forEach((b) => b.classList.toggle("on", b.dataset.tab === state.tab));
    render();
    window.scrollTo({ top: 0 });
  }
  function setRegion(sido) {
    state.region = sido;
    $("#region").value = sido;
    state.shown = PAGE;
    render();
    toast(`${sido} 지역만 봅니다`);
  }

  // ── 전체 화면 시트 ───────────────────────────
  function openSheet(title, bodyKids, actions) {
    $("#sheet-title").textContent = title;
    $("#sheet-body").replaceChildren(...bodyKids);
    const act = $("#sheet-actions");
    act.replaceChildren(...(actions || []));
    act.hidden = !actions || !actions.length;
    const sheet = $("#sheet");
    if (sheet.hidden) {
      sheet.hidden = false;
      document.body.style.overflow = "hidden";
      try { history.pushState({ sheet: true }, ""); } catch { /* 무시 */ }
    }
    $("#sheet-body").scrollTop = 0;
  }
  function closeSheet(fromPop) {
    const sheet = $("#sheet");
    if (sheet.hidden) return;
    sheet.hidden = true;
    document.body.style.overflow = "";
    if (!fromPop && history.state && history.state.sheet) history.back();
    render();
  }

  // ── 복사·공유 ───────────────────────────────
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); return true; } catch { /* 아래 방법으로 */ }
    }
    const ta = h("textarea", { readonly: true, style: "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0" });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }

  const canShare = () => typeof navigator.share === "function" && window.isSecureContext;

  // ── 저장함 (이 기기 안에만) ───────────────────
  function savePacket(issue, packet) {
    const list = lsGet(LS.saved, []).filter((s) => s.id !== issue.id);
    list.unshift({ id: issue.id, title: issue.title, press: issue.press, published: issue.published,
      field: issue.field_name, saved_at: new Date().toISOString(), packet });
    if (!lsSet(LS.saved, list.slice(0, MAX_SAVED))) {
      toast("저장 공간이 부족해 저장하지 못했습니다. 저장함에서 오래된 소재를 지워 주세요.");
      return false;
    }
    updateSavedCount();
    return true;
  }
  function removeSaved(id) {
    lsSet(LS.saved, lsGet(LS.saved, []).filter((s) => s.id !== id));
    updateSavedCount();
  }
  function updateSavedCount() { $("#saved-count").textContent = String(lsGet(LS.saved, []).length); }

  // ── 소재 패킷 화면 ───────────────────────────
  async function openPacket(id, save) {
    const savedEntry = lsGet(LS.saved, []).find((s) => s.id === id);
    const issue = state.byId.get(id) || (savedEntry && { id, title: savedEntry.title, press: savedEntry.press,
      published: savedEntry.published, field_name: savedEntry.field });
    openSheet(issue ? issue.title : "소재 패킷", [h("p", { class: "empty", text: "소재를 불러오는 중…" })]);

    let packet = null;
    let fromSaved = false;
    try {
      packet = (await loadJSON(`packets/${encodeURIComponent(id)}.json`)).data;
    } catch {
      if (savedEntry) { packet = savedEntry.packet; fromSaved = true; }
    }
    if (!packet) {
      openSheet(issue ? issue.title : "소재 패킷", [h("div", { class: "pk" },
        h("p", { class: "warn", text: "소재 패킷을 불러오지 못했습니다. 인터넷 연결을 확인하거나, 목록이 갱신되며 빠진 이슈인지 확인해 주세요." }))]);
      return;
    }
    if (save && issue) { if (savePacket(issue, packet)) toast("저장함에 저장했습니다"); }
    renderPacket(issue || { id, title: "소재 패킷" }, packet, fromSaved);
  }

  function renderPacket(issue, p, fromSaved) {
    const sec = (title, ...kids) => [h("h3", { text: title }), ...kids];
    const sources = (p.sources || []).map((s) => {
      const u = safeUrl(s.url);
      return h("li", {}, `${s.press} · ${s.date} · `, u ? h("a", { href: u, target: "_blank", rel: "noopener noreferrer", text: "원문" }) : "");
    });
    const saved = isSaved(issue.id);
    const body = h("div", { class: "pk" },
      fromSaved ? h("p", { class: "banner banner--warn", text: "오프라인 · 저장해 둔 소재를 보여줍니다." }) : null,
      (p.status || []).length ? h("div", { class: "badges" }, p.status.map((s) => h("span", { class: "badge", text: s }))) : null,
      (p.warnings || []).length ? h("p", { class: "warn", text: `확인 필요: ${p.warnings.join(" / ")}` }) : null,
      ...sec("제목 후보", h("ol", {}, (p.titles || []).map((t) => h("li", { text: t })))),
      ...sec("핵심 키워드", h("div", { class: "kw" }, (p.keywords || []).map((k) => h("span", { class: "badge", text: k })))),
      ...sec("검색 의도 (사람들이 궁금해하는 질문)", h("p", { class: "box", text: p.question })),
      ...sec("글 틀", h("ol", {}, (p.outline || []).map((o) => h("li", {}, h("b", { text: `${o.heading}: ` }), o.guide)))),
      ...sec("연결 카테고리", h("p", { text: (p.categories || []).join(" · ") + ((p.other_categories || []).length ? ` (함께: ${p.other_categories.join(" · ")})` : "") })),
      p.sentence ? sec("연결 문장 예시", h("p", { class: "box", text: p.sentence })) : [],
      ...sec(`마무리 상담 안내 (${CTA_NAME[p.cta] || p.cta})`, h("div", { class: "box" }, h("p", { text: p.cta_text }),
        p.office_footer ? h("pre", { text: p.office_footer }) : h("p", { class: "note", text: "사무소 정보(office.yaml)를 입력하면 여기에 연락처가 붙습니다." }))),
      (p.mandatory || []).length ? sec("반드시 넣을 문구", h("div", { class: "must" }, p.mandatory.map((m) => h("p", { text: m })))) : [],
      ...sec("출처", h("ul", { class: "src" }, sources)),
      h("details", { class: "more" }, h("summary", { text: "원고 작성 요청문 미리 보기" }), h("pre", { class: "box", text: p.request_prompt || "" })),
      h("div", { style: "margin-top:14px" },
        h("button", { type: "button", class: `btn ${saved ? "btn--saved" : ""}`, style: "width:100%",
          onclick: () => {
            if (isSaved(issue.id)) { removeSaved(issue.id); toast("저장함에서 뺐습니다"); }
            else if (savePacket(issue, p)) toast("저장함에 저장했습니다");
            renderPacket(issue, p, fromSaved);
          }, text: saved ? "저장됨 — 저장함에서 빼기" : "저장함에 저장" })),
      h("p", { class: "note", text: "저장한 소재는 이 기기 안에만 보관됩니다. 브라우저 데이터를 지우면 함께 지워집니다." }));

    const shareKind = lsGet(LS.share, "prompt");
    const actions = [
      h("button", { type: "button", class: "btn", onclick: async () => toast(await copyText(p.plain_text) ? "네이버용 텍스트를 복사했습니다" : "복사하지 못했습니다. 길게 눌러 직접 복사해 주세요."), text: "① 네이버용 텍스트 복사" }),
      h("button", { type: "button", class: "btn btn--primary", onclick: async () => toast(await copyText(p.request_prompt) ? "원고 요청문을 복사했습니다. Claude 앱에 붙여 넣으세요." : "복사하지 못했습니다."), text: "② 원고 요청문 복사" }),
    ];
    if (canShare()) {            // 공유를 지원하지 않는 기기(PC 일부, HTTP 미리 보기)에서는 버튼을 숨김
      const pick = h("div", { class: "share-pick" }, "공유할 내용:",
        h("label", {}, h("input", { type: "radio", name: "share", value: "prompt", checked: shareKind === "prompt" }), "원고 요청문"),
        h("label", {}, h("input", { type: "radio", name: "share", value: "plain", checked: shareKind === "plain" }), "네이버용 텍스트"));
      pick.addEventListener("change", (e) => lsSet(LS.share, e.target.value));
      actions.push(pick, h("button", { type: "button", class: "btn btn--wide", onclick: async () => {
        const kind = lsGet(LS.share, "prompt");
        try {
          await navigator.share({ title: issue.title, text: kind === "plain" ? p.plain_text : p.request_prompt });
        } catch (e) {
          if (e && e.name !== "AbortError") toast("공유하지 못했습니다");
        }
      }, text: "③ 공유 (Claude 앱·카카오톡으로 보내기)" }));
    }
    openSheet(issue.title, [body], actions);
  }

  // ── 저장함 화면 ─────────────────────────────
  function openSaved() {
    const list = lsGet(LS.saved, []);
    const items = list.map((s) => h("div", { class: "saved-item" },
      h("div", { class: "saved-item__text" },
        h("div", { class: "saved-item__title", text: s.title }),
        h("div", { class: "saved-item__meta", text: `${s.field || ""} · ${s.press || ""} · 저장 ${clock(s.saved_at)}` })),
      h("button", { type: "button", class: "btn btn--primary", onclick: () => openPacket(s.id, false), text: "보기" }),
      h("button", { type: "button", class: "btn", "aria-label": "지우기", onclick: () => {
        if (confirm("이 소재를 저장함에서 지울까요?")) { removeSaved(s.id); openSaved(); }
      }, text: "지우기" })));
    openSheet(`저장한 소재 ${list.length}개`, [
      h("p", { class: "note", text: "이 기기 안에만 보관됩니다(서버로 보내지 않음). 최대 200개." }),
      ...(items.length ? items : [h("p", { class: "empty", text: "아직 저장한 소재가 없습니다. 카드의 '소재로 저장'을 눌러 보세요." })]),
    ]);
  }

  // ── 설정·수집 상태 화면 ──────────────────────
  function sourceRows() {
    const st = state.status;
    if (!st) return [h("p", { class: "note", text: "수집 상태를 불러오지 못했습니다." })];
    const label = { ok: "정상", partial: "일부 실패", skipped: "건너뜀", failed: "실패" };
    const run = st.run || {};
    const sc = schedule();
    return [
      h("p", { text: `마지막 수집 ${clock(st.collected_at)} (${ago(st.collected_at)})` }),
      h("p", { class: "note", text: `수집 일정: 한국 시간 ${sc.kst_hours.map(two).join("·")}시 정각 (하루 ${sc.kst_hours.length}회). ` +
        `${two(sc.quiet_hours.start)}시~${two(sc.quiet_hours.end)}시는 야간 휴식. 낮에 ${sc.stale_hours}시간 넘게 수집이 없으면 경고.` }),
      st.has_naver_keys === false ? h("p", { class: "note", text: "네이버 키 없음 — 구글 뉴스·정책브리핑·블로그만 수집 중 (README 6-8·6-9)" }) : null,
      run.scheduled_slot ? h("p", { class: "note",
        text: `반영 지연: 예약 ${clock(run.scheduled_slot)} → 실제 시작 ${clock(run.started_at)} (${run.start_delay_min}분 늦게) → 수집 끝 ${clock(run.finished_at)}` +
          (state.version ? ` → 화면 배포 ${clock(state.version.built_at)}` : "") }) : null,
      state.version ? h("p", { class: "note", text: `화면 배포 ${clock(state.version.built_at)} · 버전 ${state.version.version}` }) : null,
      h("ul", {}, (st.sources || []).map((s) => h("li", {},
        h("b", { text: `${s.name}: ${label[s.status] || s.status}` }),
        ` · ${s.count}건`, s.message ? h("div", { class: "note", text: s.message }) : null,
        s.last_success ? h("div", { class: "note", text: `마지막 성공 ${clock(s.last_success)}` }) : null))),
    ];
  }
  function openStatus() { openSheet("수집 상태", [h("div", { class: "pk" }, ...sourceRows())]); }

  function openSettings() {
    const theme = lsGet(LS.theme, "auto");
    const size = lsGet(LS.size, "normal");
    const radios = (name, cur, opts, onPick) => h("div", { class: "seg" }, opts.map(([v, t]) =>
      h("button", { type: "button", class: v === cur ? "on" : "", onclick: () => { onPick(v); openSettings(); }, text: t })));
    openSheet("설정", [h("div", { class: "pk" },
      h("div", { class: "setting" }, h("h3", { text: "화면 밝기" }),
        radios("theme", theme, [["auto", "기기 설정 따름"], ["light", "밝게"], ["dark", "어둡게"]], (v) => { lsSet(LS.theme, v); applyPrefs(); })),
      h("div", { class: "setting" }, h("h3", { text: "글씨 크기" }),
        radios("size", size, [["normal", "보통"], ["large", "크게"]], (v) => { lsSet(LS.size, v); applyPrefs(); })),
      h("div", { class: "setting" }, h("h3", { text: "수집 상태" }), ...sourceRows()),
      h("div", { class: "setting" }, h("h3", { text: "알아두기" }),
        h("p", { class: "note", text: "검색 추이 점수는 네이버 데이터랩의 '상대 추이'입니다. 실제 조회수나 실시간 순위가 아닙니다." }),
        h("p", { class: "note", text: "기사 본문은 저장하지 않습니다. 제목·링크·짧은 요약만 보여줍니다." }),
        h("p", { class: "note", text: window.isSecureContext ? "HTTPS 주소에서 열려 있어 홈 화면 설치·오프라인 저장을 쓸 수 있습니다." :
          "지금은 미리 보기(HTTP)라 홈 화면 설치·오프라인 저장·공유가 동작하지 않습니다." })))]);
  }

  function applyPrefs() {
    const theme = lsGet(LS.theme, "auto");
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("data-size", lsGet(LS.size, "normal"));
  }

  // ── 시작 ───────────────────────────────────
  function init() {
    applyPrefs();
    updateSavedCount();
    state.tab = TABS.includes(lsGet(LS.tab, "today")) ? lsGet(LS.tab, "today") : "today";
    document.querySelectorAll("#tabbar button").forEach((b) => {
      b.classList.toggle("on", b.dataset.tab === state.tab);
      b.addEventListener("click", () => setTab(b.dataset.tab));
    });
    let typing = 0;
    $("#search").addEventListener("input", (e) => {
      clearTimeout(typing);
      typing = setTimeout(() => { state.q = e.target.value.trim(); state.shown = PAGE; render(); }, 200);
    });
    $("#region").addEventListener("change", (e) => { state.region = e.target.value; state.shown = PAGE; render(); });
    $("#sort").addEventListener("click", () => {
      state.sort = state.sort === "score" ? "recent" : "score";
      $("#sort").textContent = state.sort === "score" ? "점수순" : "최신순";
      state.shown = PAGE;
      render();
    });
    $("#btn-refresh").addEventListener("click", () => { toast("새로 불러오는 중…"); loadAll(); });
    $("#btn-saved").addEventListener("click", openSaved);
    $("#btn-settings").addEventListener("click", openSettings);
    $("#sheet-close").addEventListener("click", () => closeSheet(false));
    window.addEventListener("popstate", () => closeSheet(true));          // 안드로이드 뒤로 가기 = 닫기
    window.addEventListener("online", () => { toast("다시 연결됐습니다"); loadAll(); });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && Date.now() - state.loadedAt > 10 * 60 * 1000) loadAll();
    });
    setInterval(renderBanners, 60 * 1000);                                  // 'N분 전'·3시간 경고 갱신

    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker.register("sw.js").catch(() => { /* 미리 보기 등 — 무시 */ });
    }
    loadAll();
  }

  init();
})();
