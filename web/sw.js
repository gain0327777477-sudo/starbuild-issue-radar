/* 서비스워커 — 화면 파일은 저장본을 먼저 보여주고(빠르게 열림·오프라인) 뒤에서 새 파일을 받아 둠
 * (다음에 열 때 새 화면). 뉴스 데이터(data/)는 손대지 않음 — app.js 가 항상 네트워크로 먼저 받고,
 * 실패할 때만 저장본(radar-data-*)을 씀. VERSION 은 6단계 배포에서 자동으로 바꿈.
 */
const VERSION = "dev";
const SHELL = `radar-shell-${VERSION}`;
const FILES = ["./", "index.html", "style.css", "app.js", "manifest.webmanifest",
  "icons/icon-192.png", "icons/icon-512.png", "icons/maskable-512.png", "icons/favicon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("radar-shell-") && k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;           // 원문 링크 등 바깥 주소는 그대로
  if (url.pathname.includes("/data/")) return;               // 뉴스 데이터: 네트워크 우선(app.js 가 처리)
  event.respondWith(caches.open(SHELL).then(async (cache) => {
    const hit = await cache.match(req, { ignoreSearch: true });
    const fresh = fetch(req).then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    }).catch(() => null);
    if (hit) {
      event.waitUntil(fresh);                                 // 저장본을 바로 주고, 뒤에서 새 파일로 바꿔 둠
      return hit;
    }
    return (await fresh) || (await cache.match("index.html")) || Response.error();
  }));
});
