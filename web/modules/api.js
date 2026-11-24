const accessKey = "vacancy-access";
let accessToken;
try {
  accessToken = sessionStorage.getItem(accessKey);
} catch {
  // Доступ к странице по ссылке по-прежнему возможен.
}

function readAccessLink() {
  const token = new URLSearchParams(location.hash.slice(1)).get("access");
  if (!token || !/^[A-Za-z0-9_-]{43}$/.test(token)) return;
  accessToken = token;
  try {
    sessionStorage.setItem(accessKey, token);
  } catch {
    // Токен остаётся доступен на этой странице.
  }
  history.replaceState(null, "", location.pathname + location.search);
}

readAccessLink();
window.addEventListener("hashchange", readAccessLink);

export function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(path, { ...options, headers, redirect: "error" });
}
