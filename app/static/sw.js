/* 书签库 Service Worker。
 *
 * 只为了让浏览器认为本站"可安装"（Chrome 的安装条件要求有带 fetch 处理的 SW），
 * 故意什么都不缓存：页面都是登录后按需渲染的，缓存下来只会拿到过期的书签列表。
 */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {
  /* 交给浏览器默认行为，不拦截、不回放缓存 */
});
