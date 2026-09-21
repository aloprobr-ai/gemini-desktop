/* Gemini Desktop — фронтенд. Общается с Python через window.pywebview.api. */

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

const S = {
  settings: {},
  version: "",     // своя версия — показываем в настройках
  attach: [],      // картинки, приложенные к ещё не отправленному сообщению
  chats: [],
  models: [],
  groups: [],
  fallbackModels: [],
  downloads: "",
  dataDir: "",
  chat: null,
  busy: false,
  buf: "",
  thoughtBuf: "",
  node: null,
  raf: 0,
};

/* ------------------------------------------------------------- markdown */

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inlineMd(src) {
  const codes = [];
  let s = src.replace(/`([^`\n]+)`/g, (m, c) => {
    codes.push(c);
    return "@@code" + (codes.length - 1) + "@@";
  });
  s = s
    .replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>")
    .replace(/!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank"><img class="md-img" src="$2" alt="$1" loading="lazy"></a>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank">$1</a>')
    .replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank">$2</a>');
  return s.replace(/@@code(\d+)@@/g, (m, i) => '<code class="inline">' + codes[+i] + "</code>");
}

function renderMarkdown(text) {
  const lines = escapeHtml(text || "").split("\n");
  const out = [];
  let i = 0;

  const flushList = (items, ordered) => {
    const tag = ordered ? "ol" : "ul";
    out.push("<" + tag + ">" + items.map((t) => "<li>" + inlineMd(t) + "</li>").join("") + "</" + tag + ">");
  };

  while (i < lines.length) {
    const line = lines[i];

    // блок кода
    const fence = line.match(/^\s*```+\s*([\w+#-]*)\s*$/);
    if (fence) {
      const lang = fence[1] || "";
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```+\s*$/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push(
        '<div class="code-block"><div class="code-head"><span>' + (lang || "код") +
        '</span><button class="copy-code">Копировать</button></div><pre><code>' +
        body.join("\n") + "</code></pre></div>");
      continue;
    }

    if (/^\s*$/.test(line)) { i++; continue; }

    // заголовок
    const head = line.match(/^(#{1,6})\s+(.*)$/);
    if (head) {
      const lvl = Math.min(head[1].length, 3);
      out.push("<h" + lvl + ">" + inlineMd(head[2]) + "</h" + lvl + ">");
      i++;
      continue;
    }

    // горизонтальная черта
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

    // таблица
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length &&
        /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const header = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      out.push(
        "<table><thead><tr>" + header.map((c) => "<th>" + inlineMd(c) + "</th>").join("") +
        "</tr></thead><tbody>" +
        rows.map((r) => "<tr>" + r.map((c) => "<td>" + inlineMd(c) + "</td>").join("") + "</tr>").join("") +
        "</tbody></table>");
      continue;
    }

    // цитата
    if (/^\s*&gt;\s?/.test(line)) {
      const body = [];
      while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*&gt;\s?/, ""));
        i++;
      }
      out.push("<blockquote>" + inlineMd(body.join(" ")) + "</blockquote>");
      continue;
    }

    // списки
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      flushList(items, false);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i++;
      }
      flushList(items, true);
      continue;
    }

    // абзац
    const para = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) &&
           !/^\s*```/.test(lines[i]) && !/^#{1,6}\s/.test(lines[i]) &&
           !/^\s*[-*+]\s/.test(lines[i]) && !/^\s*\d+[.)]\s/.test(lines[i]) &&
           !/^\s*&gt;\s?/.test(lines[i]) && !/^\s*\|.*\|\s*$/.test(lines[i])) {
      para.push(lines[i++]);
    }
    out.push("<p>" + inlineMd(para.join("<br>")) + "</p>");
  }
  return out.join("");
}

/* ---------------------------------------------------------------- утилиты */

/* Свой диалог вместо prompt/confirm: родные показывают шапку
   «Сообщение с 127.0.0.1:порт» и выбиваются из оформления.
   Возвращает строку (для ввода), true/false (для подтверждения) или null. */
function dialog({ title, text = "", value = null, ok = "ОК", cancel = "Отмена", danger = false }) {
  const overlay = $("dialogOverlay");
  const input = $("dialogInput");
  const okBtn = $("dialogOk");
  const cancelBtn = $("dialogCancel");
  const isInput = value !== null;

  $("dialogTitle").textContent = title;
  $("dialogText").textContent = text;
  okBtn.textContent = ok;
  cancelBtn.textContent = cancel;
  okBtn.classList.toggle("danger", danger);
  input.classList.toggle("shown", isInput);
  if (isInput) input.value = value;

  overlay.classList.add("open");
  setTimeout(() => {
    if (isInput) { input.focus(); input.select(); } else { okBtn.focus(); }
  }, 60);

  return new Promise((resolve) => {
    const close = (result) => {
      overlay.classList.remove("open");
      okBtn.onclick = cancelBtn.onclick = overlay.onclick = null;
      document.removeEventListener("keydown", onKey, true);
      input.onkeydown = null;
      resolve(result);
    };
    const accept = () => close(isInput ? input.value : true);
    const reject = () => close(isInput ? null : false);

    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); reject(); }
      else if (e.key === "Enter" && !isInput) { e.preventDefault(); accept(); }
    }

    okBtn.onclick = accept;
    cancelBtn.onclick = reject;
    overlay.onclick = (e) => { if (e.target === overlay) reject(); };
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); accept(); } };
    document.addEventListener("keydown", onKey, true);
  });
}

const askText = (opts) => dialog({ value: "", ...opts });
const askConfirm = (opts) => dialog(opts);

let toastTimer = 0;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

function icon(path) {
  return '<svg viewBox="0 0 24 24">' + path + "</svg>";
}

/* искра Gemini в фирменных цветах — аватар ответов модели */
const SPARK =
  '<svg viewBox="0 0 24 24" class="spark">' +
    '<defs><linearGradient id="sparkGrad" x1="0" y1="1" x2="1" y2="0">' +
      '<stop offset="0%" stop-color="#34a853"/>' +
      '<stop offset="34%" stop-color="#fbbc05"/>' +
      '<stop offset="67%" stop-color="#ea4335"/>' +
      '<stop offset="100%" stop-color="#4285f4"/>' +
    "</linearGradient></defs>" +
    '<path fill="url(#sparkGrad)" stroke="none" d="M12 1.6c.7 5.4 4.4 9.1 9.8 9.8' +
      'c.4.1.4.7 0 .8c-5.4.7-9.1 4.4-9.8 9.8c-.1.4-.7.4-.8 0c-.7-5.4-4.4-9.1-9.8-9.8' +
      'c-.4-.1-.4-.7 0-.8c5.4-.7 9.1-4.4 9.8-9.8c.1-.4.7-.4.8 0z"/>' +
  "</svg>";
const ICONS = {
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.2a8.4 8.4 0 1 1 16.1-4.3z"/>',
  pencil: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 1-5 5L4 17v3h3l5.7-5.7a4 4 0 0 1 5-5l-2.5-2.5z"/>',
  pin: '<path d="M9 3h6l-1 6 3 3v2H7v-2l3-3-1-6z"/><path d="M12 14v7"/>',
};

const scrollEl = () => $("scroll");
function atBottom() {
  const el = scrollEl();
  return el.scrollHeight - el.scrollTop - el.clientHeight < 130;
}
function toBottom(force) {
  const el = scrollEl();
  if (force || atBottom()) el.scrollTop = el.scrollHeight;
}

/* ----------------------------------------------------------- список чатов */

/* Тот же порядок, что и в Python: закреплённые сверху, внутри группы —
   свежие первыми. Иначе только что созданный чат прыгает выше закреплённых. */
function sortChats() {
  S.chats.sort((a, b) =>
    (a.pinned ? 0 : 1) - (b.pinned ? 0 : 1) ||
    (b.updatedAt || 0) - (a.updatedAt || 0));
}

function renderChatList() {
  const q = ($("chatSearch").value || "").toLowerCase().trim();
  const list = $("chatList");
  const items = S.chats.filter((c) => !q || (c.title || "").toLowerCase().includes(q));
  list.innerHTML = "";

  if (!items.length) {
    $("recentLabel").style.display = "";
    list.innerHTML = '<div class="side-label">' + (q ? "Ничего не найдено" : "Пока пусто") + "</div>";
    return;
  }

  // Закреплённые идут отдельной группой сверху. Заголовок ставим на переходе
  // между группами, а не по номеру строки: иначе он терялся, стоило новому
  // чату оказаться выше закреплённых.
  const hasPinned = items.some((c) => c.pinned);
  $("recentLabel").style.display = hasPinned ? "none" : "";
  let section = null;
  items.forEach((chat) => {
    const here = chat.pinned ? "pinned" : "rest";
    if (hasPinned && here !== section) {
      list.appendChild(label(here === "pinned" ? "Закреплённые" : "Остальные"));
      section = here;
    }

    const row = document.createElement("div");
    row.className = "chat-item" + (S.chat && S.chat.id === chat.id ? " active" : "")
                  + (chat.pinned ? " pinned" : "");
    row.innerHTML =
      icon(chat.pinned ? ICONS.pin : ICONS.chat) +
      '<span class="title"></span>' +
      '<span class="row-actions">' +
        '<button data-act="pin"></button>' +
        '<button data-act="rename" title="Переименовать">' + icon(ICONS.pencil) + "</button>" +
        '<button data-act="delete" title="Удалить">' + icon(ICONS.trash) + "</button>" +
      "</span>";
    row.querySelector(".title").textContent = chat.title || "Новый чат";
    const pinBtn = row.querySelector('[data-act="pin"]');
    pinBtn.innerHTML = icon(ICONS.pin);
    pinBtn.title = chat.pinned ? "Открепить" : "Закрепить";

    row.addEventListener("click", (e) => {
      const act = e.target.closest("button");
      if (act) {
        e.stopPropagation();
        if (act.dataset.act === "rename") renameChat(chat);
        else if (act.dataset.act === "pin") togglePin(chat);
        else deleteChat(chat);
        return;
      }
      openChat(chat.id);
    });
    list.appendChild(row);
  });
}

function label(text) {
  const el = document.createElement("div");
  el.className = "side-label";
  el.textContent = text;
  return el;
}

async function togglePin(chat) {
  const res = await api().pin_chat(chat.id, !chat.pinned);
  if (res && res.chats) S.chats = res.chats;
  renderChatList();
}

async function renameChat(chat) {
  const title = await askText({
    title: "Переименовать чат",
    value: chat.title || "",
    ok: "Сохранить",
  });
  if (title === null) return;
  await api().rename_chat(chat.id, title);
  chat.title = title.trim() || "Новый чат";
  renderChatList();
}

async function deleteChat(chat) {
  const yes = await askConfirm({
    title: "Удалить чат?",
    text: "«" + (chat.title || "Новый чат") + "» исчезнет вместе с перепиской. Отменить будет нельзя.",
    ok: "Удалить",
    danger: true,
  });
  if (!yes) return;
  await api().delete_chat(chat.id);
  S.chats = S.chats.filter((c) => c.id !== chat.id);
  if (S.chat && S.chat.id === chat.id) {
    S.chat = null;
    $("thread").innerHTML = "";
    $("welcome").classList.remove("hidden");
  }
  renderChatList();
}

/* ------------------------------------------------------------ сообщения */

function messageNode(msg) {
  if (msg.role === "user") {
    const el = document.createElement("div");
    el.className = "msg user";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const shots = msg.images || [];
    if (shots.length) {
      const box = document.createElement("div");
      box.className = "bubble-images";
      shots.forEach((uri) => {
        const im = document.createElement("img");
        im.src = uri;
        box.appendChild(im);
      });
      bubble.appendChild(box);
    }
    if (msg.text) {
      const line = document.createElement("span");
      line.textContent = msg.text;
      bubble.appendChild(line);
    }
    el.appendChild(bubble);
    return el;
  }

  const el = document.createElement("div");
  el.className = "msg model";
  el.innerHTML =
    '<div class="avatar">' + SPARK + "</div>" +
    '<div class="body">' +
      '<details class="thoughts" hidden>' +
        '<summary><span class="thought-title">Размышления</span></summary>' +
        '<div class="thought-text"></div></details>' +
      '<div class="tools"></div>' +
      '<div class="md"></div>' +
      '<div class="err-box" hidden></div>' +
      '<div class="msg-actions">' +
        '<button data-act="copy" title="Копировать">' + icon(ICONS.copy) + "</button>" +
        '<button data-act="regen" title="Перегенерировать">' + icon(ICONS.refresh) + "</button>" +
      "</div>" +
    "</div>";

  const body = el.querySelector(".body");
  if (msg.thoughts) {
    const th = body.querySelector(".thoughts");
    th.hidden = false;
    th.querySelector(".thought-text").textContent = msg.thoughts;
  }
  (msg.calls || []).forEach((call) => body.querySelector(".tools").appendChild(toolNode(call)));
  body.querySelector(".md").innerHTML = renderMarkdown(msg.text || "");
  if (msg.error) {
    const box = body.querySelector(".err-box");
    box.hidden = false;
    box.textContent = msg.error;
  }

  body.querySelector('[data-act="copy"]').addEventListener("click", () => {
    navigator.clipboard.writeText(msg.text || "").then(() => toast("Скопировано"));
  });
  body.querySelector('[data-act="regen"]').addEventListener("click", regenerate);
  return el;
}

/* Модель написала инструмент. Запускать его до того, как человек увидел код,
   нельзя: модель читает файлы и страницы, а значит ей можно и подсказать,
   что написать. Поэтому здесь не отчёт, а вопрос. */
function newToolNode(call) {
  const res = call.result || {};
  const el = document.createElement("div");
  el.className = "tool-card ask";
  el.innerHTML =
    icon(ICONS.wrench) +
    '<div class="tc-body">' +
      '<div class="tc-title"></div>' +
      '<div class="tc-path"></div>' +
      '<pre hidden></pre>' +
      '<div class="ot-actions">' +
        '<button class="primary" data-act="ok">Разрешить</button>' +
        '<button class="mini-btn" data-act="code">Показать код</button>' +
        '<button class="mini-btn" data-act="no">Удалить</button>' +
      "</div>" +
    "</div>";

  const title = el.querySelector(".tc-title");
  const note = el.querySelector(".tc-path");
  title.textContent = "Разрешить инструмент «" + res.name + "»?";
  note.textContent = res.description || "";

  const done = (text) => {
    el.classList.remove("ask");
    el.querySelector(".ot-actions").remove();
    title.textContent = text;
  };

  el.querySelector('[data-act="ok"]').onclick = async () => {
    await api().approve_tool(res.name, true);
    done("Инструмент «" + res.name + "» разрешён");
    toast("Теперь модель может им пользоваться");
  };
  el.querySelector('[data-act="no"]').onclick = async () => {
    await api().delete_tool(res.name);
    done("Инструмент «" + res.name + "» удалён");
  };
  const pre = el.querySelector("pre");
  const codeBtn = el.querySelector('[data-act="code"]');
  codeBtn.onclick = async () => {
    if (!pre.hidden) { pre.hidden = true; codeBtn.textContent = "Показать код"; return; }
    if (!pre.textContent) {
      const list = await api().list_tools();
      const meta = ((list && list.tools) || []).find((t) => t.name === res.name);
      pre.textContent = (meta && meta.code) || "Код не найден";
    }
    pre.hidden = false;
    codeBtn.textContent = "Скрыть код";
  };
  return el;
}

function toolNode(call) {
  const res = call.result || {};
  const ok = res.status === "ok";
  const el = document.createElement("div");
  el.className = "tool-card" + (ok ? "" : " err");
  el.innerHTML =
    icon(ICONS.file) +
    '<div class="tc-body"><div class="tc-title"></div><div class="tc-path"></div></div>' +
    '<div class="tc-actions"></div>';

  if (call.name === "create_tool" && ok) return newToolNode(call);

  const TITLES = {
    create_file:     ["Файл создан", "Не удалось создать файл"],
    create_tool:     ["Инструмент создан", "Не удалось создать инструмент"],
    create_txt_file: ["Файл создан", "Не удалось создать файл"],
    read_file:       ["Файл прочитан", "Не удалось прочитать файл"],
    open_folder:     ["Папка открыта", "Не удалось открыть папку"],
  };
  const pair = TITLES[call.name] || TITLES.create_file;
  let title = ok ? pair[0] : pair[1];
  if (ok && call.name === "read_file" && res.truncated) title += " (начало)";

  el.querySelector(".tc-title").textContent = title;
  el.querySelector(".tc-path").textContent = ok ? res.path : (res.error || "неизвестная ошибка");
  el.querySelector(".tc-path").title = ok ? res.path : "";

  if (ok && call.name !== "open_folder") {
    const actions = el.querySelector(".tc-actions");
    const open = document.createElement("button");
    open.className = "mini-btn";
    open.textContent = "Открыть";
    open.onclick = () => api().open_path(res.path);
    const show = document.createElement("button");
    show.className = "mini-btn";
    show.textContent = "В папке";
    show.onclick = () => api().reveal(res.path);
    actions.append(open, show);
  }
  return el;
}

function pendingNode() {
  const el = messageNode({ role: "model", text: "" });
  el.querySelector(".md").innerHTML = '<div class="typing"><i></i><i></i><i></i></div>';
  return el;
}

/* ------------------------------------------------ приложенные картинки */

const MAX_ATTACH = 4;
const ATTACH_SIDE = 1600;   // больше модели всё равно не нужно

/* Большой снимок ужимаем: он хранится в переписке целиком, а от лишних
   пикселей распознавание не улучшается. Маленькие отдаём как есть. */
function shrink(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("файл не прочитался"));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("это не картинка"));
      img.onload = () => {
        const k = Math.min(1, ATTACH_SIDE / Math.max(img.width, img.height));
        if (k === 1 && reader.result.length < 1500000) { resolve(reader.result); return; }
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * k);
        c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL("image/jpeg", 0.85));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

async function addImages(files) {
  for (const file of files) {
    if (!file || !/^image\//.test(file.type || "")) continue;
    if (S.attach.length >= MAX_ATTACH) {
      toast("За раз можно приложить не больше " + MAX_ATTACH);
      break;
    }
    try {
      S.attach.push(await shrink(file));
    } catch (e) {
      toast("Не получилось приложить картинку");
    }
  }
  renderAttach();
}

function renderAttach() {
  const strip = $("attachStrip");
  strip.innerHTML = "";
  strip.hidden = S.attach.length === 0;
  S.attach.forEach((uri, i) => {
    const item = document.createElement("div");
    item.className = "attach-item";
    const img = document.createElement("img");
    img.src = uri;
    const del = document.createElement("button");
    del.textContent = "×";
    del.title = "Убрать";
    del.onclick = () => { S.attach.splice(i, 1); renderAttach(); };
    item.append(img, del);
    strip.appendChild(item);
  });
}

/* Файл, брошенный мимо строки ввода, иначе открылся бы прямо в окне
   приложения вместо чата. */
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => e.preventDefault());

/* ------------------------------------------- полноэкранный режим (F11) */

let fsOn = false;

async function toggleFullscreen(want) {
  const target = want === undefined ? !fsOn : !!want;
  if (target === fsOn) return;
  try {
    fsOn = !!(await api().toggle_fullscreen());
  } catch (e) {
    fsOn = target;   // окна нет только в браузерной отладке
  }
  document.body.classList.toggle("fs", fsOn);
  if (!fsOn) document.body.classList.remove("peek");
}

/* Панель показываем, когда курсор у самой кромки, и убираем, когда он ушёл
   ниже неё. Порог возврата больше высоты панели, иначе она мигала бы, стоит
   мыши шевельнуться над кнопками.

   Слушатель вешаем сразу, а не при запуске интерфейса: ему нечего ждать,
   а в общей функции запуска он оказался бы заложником всего, что там выше. */
document.addEventListener("mousemove", (e) => {
  if (!fsOn) return;
  if (e.clientY <= 4) document.body.classList.add("peek");
  else if (e.clientY > 48) document.body.classList.remove("peek");
});

/* --------------------------------------------------------------- чаты */

async function openChat(id, force) {
  if (!force && S.chat && S.chat.id === id) return;
  const chat = await api().get_chat(id);
  if (!chat) return;
  S.chat = chat;
  $("welcome").classList.add("hidden");

  const thread = $("thread");
  thread.innerHTML = "";
  chat.messages.forEach((m, i) => {
    const node = messageNode(m);
    node.classList.add("enter");
    // лёгкая лесенка: сообщения проявляются друг за другом, но без затягивания
    node.style.animationDelay = Math.min(i, 6) * 30 + "ms";
    thread.appendChild(node);
  });

  syncModelUi();
  renderChatList();
  toBottom(true);
  $("input").focus();
}

/* Кнопка «Новый чат» только очищает экран: сам чат заводится в ensureChat()
   при первом сообщении, чтобы в списке не копились пустышки. */
function startNewChat() {
  S.chat = null;
  $("thread").innerHTML = "";
  $("welcome").classList.remove("hidden");
  syncModelUi();
  renderChatList();
  $("input").focus();
}

async function ensureChat() {
  if (S.chat) return S.chat;
  const chat = await api().new_chat();
  S.chats.push({ id: chat.id, title: chat.title, model: chat.model,
                 updatedAt: chat.updatedAt, pinned: false });
  sortChats();
  S.chat = chat;
  renderChatList();
  return chat;
}

/* --------------------------------------------------------- отправка */

async function send(text) {
  text = (text !== undefined ? text : $("input").value).trim();
  const shots = S.attach.slice();
  if ((!text && !shots.length) || S.busy) return;

  await ensureChat();
  $("welcome").classList.add("hidden");
  $("input").value = "";
  autoGrow();

  S.attach = [];
  renderAttach();

  const thread = $("thread");
  const userNode = messageNode({ role: "user", text, images: shots });
  userNode.classList.add("enter");
  thread.appendChild(userNode);
  S.node = pendingNode();
  S.node.classList.add("enter");
  thread.appendChild(S.node);
  toBottom(true);

  S.busy = true;
  S.buf = "";
  S.thoughtBuf = "";
  document.body.classList.add("busy");

  const res = await api().send(S.chat.id, text, shots);
  if (!res || !res.ok) {
    finishWithError((res && res.error) || "Не удалось отправить");
  }
}

async function regenerate() {
  if (S.busy || !S.chat) return;
  const res = await api().regenerate(S.chat.id);
  if (!res || !res.ok) toast((res && res.error) || "Недоступно");
}

/* Пока идёт работа, в заголовке свёрнутого блока виден текущий шаг:
   чтобы понять, чем занята модель, разворачивать его не надо. */
function setThoughtTitle(details, buf) {
  const lines = buf.split("\n").map((s) => s.trim()).filter(Boolean);
  const last = lines[lines.length - 1] || "Размышления";
  const title = details.querySelector(".thought-title");
  if (title) title.textContent = last.length > 60 ? last.slice(0, 60) + "…" : last;
}

function finishWithError(text) {
  if (S.node) {
    const box = S.node.querySelector(".err-box");
    box.hidden = false;
    box.textContent = text;
    const md = S.node.querySelector(".md");
    if (md.querySelector(".typing")) md.innerHTML = "";
  } else {
    toast(text);
  }
  S.busy = false;
  document.body.classList.remove("busy");
  S.node = null;
}

function scheduleRender() {
  if (S.raf) return;
  S.raf = requestAnimationFrame(() => {
    S.raf = 0;
    if (!S.node) return;
    S.node.querySelector(".md").innerHTML = renderMarkdown(S.buf);
    toBottom(false);
  });
}

/* ------------------------------------------------- события из Python */

window.__ev = function (payload) {
  let ev;
  try { ev = JSON.parse(payload); } catch (e) { return; }
  if (S.chat && ev.chatId && ev.chatId !== S.chat.id) return;

  switch (ev.type) {
    case "start":
      S.buf = "";
      S.thoughtBuf = "";
      break;

    case "delta":
      S.buf += ev.delta;
      scheduleRender();
      break;

    case "replace_text":
      S.buf = ev.text || "";
      scheduleRender();
      break;

    case "update_progress": {
      const bar = $("updBar").querySelector("i");
      if (bar) bar.style.width = (ev.pct || 0) + "%";
      break;
    }

    case "thought": {
      S.thoughtBuf += ev.delta;
      if (!S.node) break;
      const th = S.node.querySelector(".thoughts");
      th.hidden = false;
      th.classList.add("live");
      th.querySelector(".thought-text").textContent = S.thoughtBuf;
      setThoughtTitle(th, S.thoughtBuf);
      toBottom(false);
      break;
    }

    case "tool_start":
      if (S.node) {
        const md = S.node.querySelector(".md");
        if (md.querySelector(".typing")) md.innerHTML = "";
        const el = document.createElement("div");
        el.className = "tool-card pending";
        el.innerHTML = icon(ICONS.file) +
          '<div class="tc-body"><div class="tc-title">Создаю файл…</div>' +
          '<div class="tc-path">' + (ev.args && ev.args.filename ? ev.args.filename : "") + "</div></div>";
        S.node.querySelector(".tools").appendChild(el);
        toBottom(false);
      }
      break;

    case "tool_done":
      if (S.node) {
        const tools = S.node.querySelector(".tools");
        const pending = tools.querySelector(".tool-card.pending");
        const node = toolNode(ev.call);
        if (pending) tools.replaceChild(node, pending);
        else tools.appendChild(node);
        toBottom(false);
      }
      break;

    case "title": {
      const meta = S.chats.find((c) => c.id === ev.chatId);
      if (meta) meta.title = ev.title;
      if (S.chat && S.chat.id === ev.chatId) S.chat.title = ev.title;
      renderChatList();
      break;
    }

    case "reload":
      if (S.chat) {
        openChat(S.chat.id, true).then(() => {
          const thread = $("thread");
          S.node = pendingNode();
          S.node.classList.add("enter");
          thread.appendChild(S.node);
          S.busy = true;
          S.buf = "";
          S.thoughtBuf = "";
          document.body.classList.add("busy");
          toBottom(true);
        });
      }
      break;

    case "error":
      if (S.node) {
        const box = S.node.querySelector(".err-box");
        box.hidden = false;
        box.textContent = ev.error;
        const md = S.node.querySelector(".md");
        if (md.querySelector(".typing")) md.innerHTML = "";
      }
      break;

    case "done": {
      S.busy = false;
      document.body.classList.remove("busy");
      if (S.node) {
        const fresh = messageNode(ev.message);
        S.node.replaceWith(fresh);
      }
      S.node = null;
      if (S.chat) {
        api().get_chat(S.chat.id).then((c) => { if (c) S.chat = c; });
      }
      toBottom(false);
      break;
    }
  }
};

/* ------------------------------------------------------------- модели */

/* gemini-3.7-flash-medium -> «Gemini 3.7 Flash · Medium».
   Готовый заголовок берём из загруженного списка, а пока он не пришёл —
   собираем сами по тем же правилам, что и на стороне Python. */
const MODEL_LEVELS = ["high", "medium", "low", "thinking"];

function prettyModel(id) {
  if (!id) return "—";
  const known = [].concat(S.models, S.fallbackModels).find((m) => m && m.id === id);
  if (known && known.title) return known.title;

  const parts = id.split("-");
  let level = null;
  if (MODEL_LEVELS.includes(parts[parts.length - 1])) level = parts.pop();

  const merged = [];
  parts.forEach((p) => {
    if (/^\d+$/.test(p) && merged.length && /^\d+(\.\d+)*$/.test(merged[merged.length - 1])) {
      merged[merged.length - 1] += "." + p;
    } else {
      merged.push(p);
    }
  });

  const words = merged.map((p) => {
    if (["gpt", "oss", "ai"].includes(p.toLowerCase())) return p.toUpperCase();
    if (/^\d+(\.\d+)*$/.test(p) || /^\d+[a-z]+$/i.test(p)) return p.toUpperCase();
    return p.charAt(0).toUpperCase() + p.slice(1);
  });

  const title = words.join(" ");
  return level ? title + " · " + level.charAt(0).toUpperCase() + level.slice(1) : title;
}

/* В каждом чате своя модель: в одном разговор шёл с 3.8, в другом с 3.7.
   Открыли чат — и шапка, и меню, и переключатель уровня показывают его модель,
   а не ту, что была выбрана в предыдущем. */
function syncModelUi() {
  setModelLabel(currentModelId());
  renderModelMenu();
  renderLevelPicker();
}

function setModelLabel(id) {
  const group = findGroup(id);
  if (group) {
    $("modelName").textContent = group.title;
    return;
  }
  // группы ещё не пришли — собираем название сами, отбросив уровень
  const parts = (id || "").split("-");
  if (MODEL_LEVELS.includes(parts[parts.length - 1])) parts.pop();
  $("modelName").textContent = prettyModel(parts.join("-"));
}

const LEVEL_LABELS = { low: "Low", medium: "Medium", high: "High", thinking: "Thinking" };

function currentModelId() {
  return (S.chat && S.chat.model) || S.settings.model;
}

function findGroup(id) {
  return S.groups.find((g) => g.levels.some((lv) => lv.id === id));
}

function currentLevel() {
  const id = currentModelId();
  const parts = (id || "").split("-");
  return MODEL_LEVELS.includes(parts[parts.length - 1]) ? parts[parts.length - 1] : "";
}

/* при смене модели стараемся сохранить выбранный уровень, иначе берём средний */
function preferredLevel(group, wanted) {
  return group.levels.find((lv) => lv.level === wanted)
      || group.levels.find((lv) => lv.level === "medium")
      || group.levels.find((lv) => lv.level === "high")
      || group.levels[0];
}

async function applyModel(id) {
  if (S.chat) {
    await api().set_chat_model(S.chat.id, id);
    S.chat.model = id;
  } else {
    await api().save_settings({ model: id });
  }
  S.settings.model = id;
  setModelLabel(id);
  renderModelMenu();
  renderLevelPicker();
}

/* Пока список не открывали, справа от названия модели горит синяя точка —
   значит, на шлюзе появилось что-то новое. */
function showNewMark(on) {
  $("modelPicker").classList.toggle("has-new", !!on);
}

async function markModelsSeen() {
  if (!$("modelPicker").classList.contains("has-new")) return;
  // Гасим только точку в шапке. Пометки в самом списке оставляем видимыми —
  // иначе они исчезали бы ровно в тот момент, когда список открывают.
  // Пропадут при следующей загрузке: шлюзу уже сказано, что список посмотрели.
  showNewMark(false);
  try { await api().mark_models_seen(); } catch (e) {}
}

function renderModelMenu() {
  const list = $("modelMenuList");
  list.innerHTML = "";
  const group = findGroup(currentModelId());

  S.groups.forEach((g) => {
    const btn = document.createElement("button");
    btn.className = "model-opt" + (group && g.base === group.base ? " active" : "")
                  + (g.isNew ? " is-new" : "");
    btn.innerHTML = '<span class="dot"></span><span class="name"></span>' +
                    (g.isNew ? '<span class="new-mark" title="Новая модель"></span>' : "");
    btn.querySelector(".name").textContent = g.title;
    btn.onclick = async () => {
      $("modelMenu").classList.remove("open");
      await applyModel(preferredLevel(g, currentLevel()).id);
    };
    list.appendChild(btn);
  });
}

/* Уровень думания живёт отдельно от модели. Если вариант всего один
   (у GPT только medium, у Claude Opus только thinking) — выбирать нечего. */
function renderLevelPicker() {
  const box = $("levelPicker");
  const group = findGroup(currentModelId());
  box.innerHTML = "";

  if (!group || group.levels.length < 2) {
    box.classList.remove("shown");
    return;
  }
  box.classList.add("shown");

  const active = currentLevel();
  group.levels.forEach((lv) => {
    const btn = document.createElement("button");
    btn.className = "level-opt" + (lv.level === active ? " active" : "");
    btn.textContent = LEVEL_LABELS[lv.level] || lv.level;
    btn.onclick = () => applyModel(lv.id);
    box.appendChild(btn);
  });
}

async function loadModels(notify) {
  const res = await api().list_models();
  if (res && res.models) S.models = res.models;
  if (res && res.groups) S.groups = res.groups;
  renderModelMenu();
  renderLevelPicker();
  showNewMark(res && res.hasNew);
  // список пришёл — в шапке можно показать нормальное название вместо id
  setModelLabel(currentModelId());
  if (notify) {
    if (res && res.ok) toast("Моделей загружено: " + S.groups.length);
    else toast("Не удалось получить список: " + ((res && res.error) || "ошибка"));
  }
  return res;
}

/* ---------------------------------------------------------- настройки */

function fillSettings() {
  const s = S.settings;
  $("setProvider").value = s.provider || "openai";
  $("setBaseUrl").value = s.baseUrl || "";
  showKeyState();
  $("setGlobalPrompt").value = s.globalPrompt || "";
  $("setToolsPrompt").value = s.toolsPrompt || "";
  $("setToolsEnabled").checked = !!s.toolsEnabled;
  $("setDefaultDir").value = s.defaultDir || "";
  $("setDefaultDir").placeholder = S.downloads;
  $("dataDirHint").textContent = "Чаты и настройки: " + S.dataDir;
  $("verHint").textContent = "Gemini Desktop " + (S.version || "");
  $("fieldBaseUrl").style.display = (s.provider === "google") ? "none" : "";
  $("testResult").textContent = "";
  $("testResult").className = "hint";
  fillAutostart();
  fillUsage();
  fillOwnTools();
}

/* Свои инструменты: ждущие разрешения показываем сразу с кодом,
   разрешённые — строкой, код по кнопке. */
async function fillOwnTools() {
  const box = $("ownTools");
  const res = await api().list_tools();
  const tools = (res && res.tools) || [];
  box.innerHTML = "";

  if (!tools.length) {
    box.innerHTML = '<em class="hint">Пока ни одного. Модель создаёт их сама, ' +
      "когда имеющихся не хватает — и каждый ждёт вашего разрешения.</em>";
    return;
  }

  tools.forEach((t) => {
    const el = document.createElement("div");
    el.className = "own-tool" + (t.approved ? "" : " pending");
    el.innerHTML =
      '<div class="ot-head"><span class="ot-name"></span>' +
        '<span class="ot-state"></span></div>' +
      '<div class="ot-desc"></div>' +
      "<pre></pre>" +
      '<div class="ot-actions">' +
        '<button class="mini-btn" data-act="toggle"></button>' +
        '<button class="mini-btn warn" data-act="del">Удалить</button>' +
      "</div>";

    el.querySelector(".ot-name").textContent = t.name;
    el.querySelector(".ot-state").textContent = t.approved ? "разрешён" : "ждёт разрешения";
    el.querySelector(".ot-desc").textContent = t.description || "";
    const pre = el.querySelector("pre");
    pre.textContent = t.code || "";
    pre.style.display = t.approved ? "none" : "";

    const toggle = el.querySelector('[data-act="toggle"]');
    toggle.textContent = t.approved ? "Показать код" : "Разрешить";
    toggle.onclick = async () => {
      if (t.approved) {
        pre.style.display = pre.style.display === "none" ? "" : "none";
        toggle.textContent = pre.style.display === "none" ? "Показать код" : "Скрыть код";
        return;
      }
      await api().approve_tool(t.name, true);
      fillOwnTools();
    };

    el.querySelector('[data-act="del"]').onclick = async () => {
      const yes = await askConfirm({
        title: "Удалить инструмент?",
        text: "«" + t.name + "» исчезнет вместе с кодом.",
        ok: "Удалить",
        danger: true,
      });
      if (!yes) return;
      await api().delete_tool(t.name);
      fillOwnTools();
    };

    box.appendChild(el);
  });
}

/* Остатка квоты у Antigravity нет — CLI его не сообщает. Поэтому показываем
   то, что можно посчитать честно: сколько уже израсходовано этим ключом. */
function bigNum(v) {
  return (v || 0).toLocaleString("ru-RU");
}

async function fillUsage() {
  const box = $("usageBox");
  box.innerHTML = '<em class="hint">Загружаю…</em>';
  const res = await api().usage();
  if (!res || !res.ok) {
    box.innerHTML = '<em class="hint"></em>';
    box.querySelector(".hint").textContent = (res && res.error) || "Не удалось получить";
    return;
  }

  const u = res.usage || {};
  const card = (value, caption) =>
    '<div class="u-card"><b>' + value + "</b><span>" + caption + "</span></div>";

  let html =
    card(bigNum(u.today && u.today.requests), "запросов сегодня") +
    card(bigNum(u.today && u.today.total), "токенов сегодня") +
    card(bigNum(u.month && u.month.requests), "запросов с " + (u.since || "начала месяца")) +
    card(bigNum(u.month && u.month.total), "токенов за месяц");

  const rl = u.rate_limit || {};
  if (rl.enabled) {
    html += card(rl.left + " из " + rl.limit_per_min, "осталось в этой минуте");
  }

  const top = Object.keys(u.models || {}).slice(0, 3)
    .map((k) => k + " — " + u.models[k]).join(", ");
  html += '<div class="u-note">' +
    (top ? "Чаще всего: " + top + ". " : "") +
    "Остаток квоты шлюз не показывает: Antigravity его не сообщает." +
    "</div>";

  box.innerHTML = html;
}

async function fillAutostart() {
  const box = $("setAutostart");
  const res = await api().get_autostart();
  box.checked = !!(res && res.on);
  box.disabled = !(res && res.available);
  $("autostartHint").textContent = box.disabled
    ? "Доступно только в собранном приложении."
    : "";
}

/* Ключ хранится только в Python и наружу не отдаётся — показывать нечего.
   Поле пустое: что-то введено значит «заменить», пусто — «оставить». */
function keyIsSet() {
  const s = S.settings;
  return $("setProvider").value === "google" ? !!s.googleKeySet : !!s.apiKeySet;
}

function showKeyState() {
  const input = $("setApiKey");
  input.value = "";
  input.placeholder = keyIsSet() ? "ключ сохранён" : "sk-...";
  $("keyHint").textContent = keyIsSet()
    ? "Ключ сохранён и не показывается. Введите новый, чтобы заменить."
    : "Ключ хранится только на этом компьютере и в интерфейсе не отображается.";
  $("btnClearKey").style.display = keyIsSet() ? "" : "none";
}

async function saveSettings() {
  const provider = $("setProvider").value;
  const patch = {
    provider,
    baseUrl: $("setBaseUrl").value.trim(),
    globalPrompt: $("setGlobalPrompt").value,
    toolsPrompt: $("setToolsPrompt").value,
    toolsEnabled: $("setToolsEnabled").checked,
    defaultDir: $("setDefaultDir").value.trim(),
  };
  const key = $("setApiKey").value.trim();
  if (key) { if (provider === "google") patch.googleKey = key; else patch.apiKey = key; }

  S.settings = await api().save_settings(patch);
  updateToolsChip();
  $("overlay").classList.remove("open");
  toast("Настройки сохранены");
  loadModels(false);
}

function updateToolsChip() {
  const on = !!S.settings.toolsEnabled;
  $("toolsChip").classList.toggle("on", on);
  $("toolsChipText").textContent = on ? "Инструменты вкл." : "Инструменты выкл.";
}

/* ------------------------------------------------------------- ввод */

function autoGrow() {
  const ta = $("input");
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
}

/* --------------------------------------------------------- инициализация */

function bindUi() {
  $("btnNewChat").onclick = startNewChat;
  $("btnCollapse").onclick = () => document.querySelector(".app").classList.add("collapsed");
  $("btnExpand").onclick = () => document.querySelector(".app").classList.remove("collapsed");
  $("chatSearch").oninput = renderChatList;
  $("btnDownloads").onclick = () => api().open_path(S.settings.defaultDir || S.downloads);
  $("hintDir").onclick = () => api().open_path(S.settings.defaultDir || S.downloads);

  $("btnTheme").onclick = async () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    S.settings.theme = next;
    await api().save_settings({ theme: next });
  };

  $("modelBtn").onclick = (e) => {
    e.stopPropagation();
    const opening = !$("modelMenu").classList.contains("open");
    $("modelMenu").classList.toggle("open");
    renderModelMenu();
    if (opening) markModelsSeen();   // посмотрели список — точка гаснет
  };
  $("btnRefreshModels").onclick = (e) => { e.stopPropagation(); loadModels(true); };
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#modelPicker")) $("modelMenu").classList.remove("open");
  });

  $("btnSend").onclick = () => send();
  $("btnStop").onclick = () => { if (S.chat) api().stop(S.chat.id); };
  $("input").addEventListener("input", autoGrow);
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });

  document.querySelectorAll(".card").forEach((card) => {
    card.onclick = () => send(card.dataset.prompt);
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".copy-code");
    if (!btn) return;
    const code = btn.closest(".code-block").querySelector("code").textContent;
    navigator.clipboard.writeText(code).then(() => {
      btn.textContent = "Скопировано";
      setTimeout(() => (btn.textContent = "Копировать"), 1600);
    });
  });

  /* Горячие клавиши. Когда открыт свой диалог, он забирает клавиши себе —
     там свой обработчик с перехватом, поэтому здесь ничего не делаем. */
  document.addEventListener("keydown", (e) => {
    if ($("dialogOverlay").classList.contains("open")) return;
    const settingsOpen = $("overlay").classList.contains("open");

    if (e.key === "F11" || e.code === "F11") {
      e.preventDefault();
      toggleFullscreen();
      return;
    }

    if (e.key === "Escape") {
      if (settingsOpen) { $("overlay").classList.remove("open"); e.preventDefault(); }
      else if ($("modelMenu").classList.contains("open")) {
        $("modelMenu").classList.remove("open"); e.preventDefault();
      } else if (fsOn) { toggleFullscreen(false); e.preventDefault(); }
      return;
    }
    if (!e.ctrlKey || e.altKey || e.shiftKey) return;

    /* Смотрим и на физическую клавишу, и на букву. Только по букве нельзя:
       на русской раскладке Ctrl+N приходит как «т». Только по коду — тоже:
       некоторые программы удалённого доступа шлют события без него. */
    const hit = (code, ...keys) =>
      e.code === code || keys.includes((e.key || "").toLowerCase());

    if (hit("KeyN", "n", "т")) { e.preventDefault(); if (!settingsOpen) startNewChat(); }
    else if (hit("KeyF", "f", "а")) {
      e.preventDefault();
      if (!settingsOpen) { $("chatSearch").focus(); $("chatSearch").select(); }
    } else if (hit("Comma", ",", "б")) {
      e.preventDefault();
      if (settingsOpen) $("overlay").classList.remove("open");
      else { fillSettings(); $("overlay").classList.add("open"); }
    }
  });

  // о программе
  $("btnCheckUpd").onclick = () => checkUpdate(true);

  // картинки: кнопка, вставка из буфера и перетаскивание
  $("btnAttach").onclick = () => $("attachInput").click();
  $("attachInput").onchange = (e) => {
    addImages(Array.from(e.target.files || []));
    e.target.value = "";
  };
  $("input").addEventListener("paste", (e) => {
    const files = Array.from((e.clipboardData && e.clipboardData.items) || [])
      .filter((it) => it.kind === "file" && /^image\//.test(it.type || ""))
      .map((it) => it.getAsFile());
    if (files.length) { e.preventDefault(); addImages(files); }
  });
  const composer = document.querySelector(".composer");
  ["dragenter", "dragover"].forEach((ev) => composer.addEventListener(ev, (e) => {
    e.preventDefault();
    composer.classList.add("drop");
  }));
  ["dragleave", "drop"].forEach((ev) => composer.addEventListener(ev, () => {
    composer.classList.remove("drop");
  }));
  composer.addEventListener("drop", (e) => {
    e.preventDefault();
    addImages(Array.from((e.dataTransfer && e.dataTransfer.files) || []));
  });

  // панель, которая выезжает сверху в полноэкранном режиме
  $("fsMin").onclick = () => api().minimize_window();
  $("fsExit").onclick = () => toggleFullscreen(false);
  $("fsClose").onclick = () => api().hide_window();

  // настройки
  $("btnSettings").onclick = () => { fillSettings(); $("overlay").classList.add("open"); };
  $("btnCloseSettings").onclick = () => $("overlay").classList.remove("open");
  $("overlay").addEventListener("click", (e) => {
    if (e.target === $("overlay")) $("overlay").classList.remove("open");
  });
  $("btnSaveSettings").onclick = saveSettings;

  $("setAutostart").onchange = async () => {
    const box = $("setAutostart");
    const res = await api().set_autostart(box.checked);
    if (!res || !res.ok) {
      box.checked = !box.checked;
      $("autostartHint").textContent = (res && res.error) || "Не удалось изменить";
      return;
    }
    $("autostartHint").textContent = res.on
      ? "Приложение будет стартовать вместе с Windows и ждать в трее."
      : "Автозапуск выключен.";
  };

  $("setProvider").onchange = () => {
    const isGoogle = $("setProvider").value === "google";
    $("fieldBaseUrl").style.display = isGoogle ? "none" : "";
    showKeyState();
  };
  $("btnClearKey").onclick = async () => {
    const which = $("setProvider").value;
    const yes = await askConfirm({
      title: "Удалить ключ?",
      text: "Сохранённый ключ будет стёрт, и запросы перестанут уходить.",
      ok: "Удалить",
      danger: true,
    });
    if (!yes) return;
    S.settings = await api().clear_key(which);
    showKeyState();
    toast("Ключ удалён");
  };
  $("btnTest").onclick = async () => {
    const out = $("testResult");
    out.className = "hint";
    out.textContent = "Проверяю…";
    await saveSettingsQuiet();
    const res = await api().test_connection();
    if (res && res.ok) {
      out.className = "hint ok";
      out.textContent = "Подключение работает, моделей: " + res.count;
    } else {
      out.className = "hint err";
      out.textContent = (res && res.error) || "Не удалось подключиться";
    }
  };
  $("btnPickDir").onclick = async () => {
    const dir = await api().pick_folder();
    if (dir) $("setDefaultDir").value = dir;
  };
  $("btnResetDir").onclick = () => { $("setDefaultDir").value = ""; };
  $("btnClearAll").onclick = async () => {
    const yes = await askConfirm({
      title: "Удалить все чаты?",
      text: "Вся переписка будет стёрта без возможности восстановления. Настройки и промты останутся.",
      ok: "Удалить всё",
      danger: true,
    });
    if (!yes) return;
    await api().clear_all();
    S.chats = [];
    S.chat = null;
    $("thread").innerHTML = "";
    $("welcome").classList.remove("hidden");
    renderChatList();
    toast("Чаты удалены");
  };
  document.querySelectorAll("[data-reset]").forEach((btn) => {
    btn.onclick = async () => {
      const which = btn.dataset.reset;
      const text = await api().default_prompt(which);
      $(which === "global" ? "setGlobalPrompt" : "setToolsPrompt").value = text;
    };
  });
}

async function saveSettingsQuiet() {
  const provider = $("setProvider").value;
  const patch = { provider, baseUrl: $("setBaseUrl").value.trim() };
  const key = $("setApiKey").value.trim();
  if (key) { if (provider === "google") patch.googleKey = key; else patch.apiKey = key; }
  S.settings = await api().save_settings(patch);
}

/* Заставка показывает настоящий ход загрузки: мост pywebview поднимается
   около секунды, список моделей приходит по сети ещё позже. Полоска движется
   по фактически пройденным шагам, а не по таймеру. */
const SPLASH_MAX_MS = 20000; // страховка на случай, если мост не поднимется

function setProgress(pct) {
  const bar = document.querySelector(".splash-bar i");
  if (bar) bar.style.width = pct + "%";
}

function hideSplash() {
  const el = $("splash");
  if (!el || el.classList.contains("hide")) return;
  setProgress(100);
  // даём полоске доехать до конца, и только потом убираем занавес
  setTimeout(() => el.classList.add("hide"), 260);
}

async function init() {
  try {
    setProgress(30);                       // мост поднялся
    const data = await api().bootstrap();
    S.settings = data.settings;
    S.chats = data.chats;
    S.downloads = data.downloads;
    S.dataDir = data.dataDir;
    S.version = data.version || "";
    S.fallbackModels = data.fallbackModels || [];

    document.documentElement.dataset.theme = S.settings.theme || "dark";
    setModelLabel(S.settings.model);
    updateToolsChip();
    renderChatList();
    bindUi();
    autoGrow();
    setProgress(60);                       // настройки и чаты на экране

    bindWizard();
    if (!(S.settings.apiKeySet || S.settings.googleKeySet)) {
      // первый запуск: не бросаем человека в настройки, а проводим по шагам
      openWizard();
    } else {
      await loadModels(false);             // ждём модели: это последний шаг
      checkUpdate();                       // тихо, в фоне, окно покажется само
    }
    $("input").focus();
  } catch (err) {
    try { api().log("init() упал: " + err); } catch (e) {}
  } finally {
    hideSplash();
  }
}

/* Сообщаем в Python, видно ли окно: свёрнутое окно WebView2 отмечает как
   скрытое. По этому признаку решаем, показывать ли уведомление о готовом ответе. */
function reportVisibility() {
  try { api().set_visible(!document.hidden); } catch (e) {}
}
document.addEventListener("visibilitychange", reportVisibility);
window.addEventListener("focus", reportVisibility);

window.addEventListener("pywebviewready", init);
setProgress(12);                           // страница разобрана
setTimeout(hideSplash, SPLASH_MAX_MS);

/* ------------------------------------------------ первый запуск */

// Свой шлюз на этом же компьютере: agy-gateway по умолчанию слушает 8080.
// Чужого адреса тут нет намеренно — приложение не должно ходить неизвестно
// куда только потому, что так было удобно автору.
const LOCAL_GATEWAY = "http://127.0.0.1:8080/v1";

const W = { page: 1, checking: false };

function wizShow(page, back) {
  const pages = document.querySelectorAll(".wiz-page[data-page]");
  pages.forEach((el) => {
    const mine = Number(el.dataset.page) === page;
    el.classList.toggle("shown", mine);
    // уходящая страница отъезжает в ту сторону, откуда пришла новая
    el.classList.toggle("back", !mine && back);
  });
  document.querySelectorAll(".wiz-dots i").forEach((dot, i) => {
    dot.classList.toggle("on", i === page - 1);
  });
  $("wizBack").classList.toggle("hidden", page === 1);
  $("wizErr").textContent = "";
  W.page = page;
  wizButton();
}

function wizButton() {
  const btn = $("wizNext");
  if (W.checking) { btn.textContent = "Проверяю…"; btn.disabled = true; return; }
  btn.disabled = false;
  btn.textContent = W.page === 1 ? "Дальше" : W.page === 2 ? "Проверить и продолжить" : "Начать";
}

function openWizard() {
  $("wizProvider").value = "openai";
  $("wizBase").value = LOCAL_GATEWAY;
  $("wizKey").value = "";
  wizShow(1, false);
  $("wizard").classList.add("open");
}

async function wizCheckKey() {
  const provider = $("wizProvider").value;
  const key = $("wizKey").value.trim();
  // Google без ключа не работает никак. А свой шлюз ключа может и не спрашивать:
  // пока в его настройках нет ни одного, он пускает как есть. Поэтому здесь
  // не отказываем заранее, а даём шлюзу ответить самому.
  if (!key && provider === "google") {
    $("wizErr").textContent = "Без ключа Google не обойтись";
    return false;
  }

  const patch = { provider, baseUrl: $("wizBase").value.trim() || LOCAL_GATEWAY };
  if (provider === "google") patch.googleKey = key; else patch.apiKey = key;

  W.checking = true; wizButton();
  S.settings = await api().save_settings(patch);
  const res = await api().test_connection();
  W.checking = false; wizButton();

  if (!res || !res.ok) {
    $("wizErr").textContent = (res && res.error) || "Шлюз не ответил";
    return false;
  }
  $("wizReady").textContent = (key ? "Шлюз ответил, ключ принят." : "Шлюз ответил.")
    + " Моделей доступно: " + (res.count || 0) + ".";
  return true;
}

async function wizFinish() {
  $("wizard").classList.remove("open");
  await loadModels(false);
  send("Что это за приложение и что ты умеешь? Расскажи коротко, по пунктам.");
}

function bindWizard() {
  $("wizProvider").onchange = () => {
    const google = $("wizProvider").value === "google";
    $("wizBaseWrap").classList.toggle("hidden", google);
    // адрес своего шлюза известен заранее — подставляем, чтобы не набирать
    if (!google && !$("wizBase").value.trim()) $("wizBase").value = LOCAL_GATEWAY;
    $("wizKey").placeholder = google ? "AIza..." : "sk-...";
  };

  $("wizBack").onclick = () => { if (W.page > 1) wizShow(W.page - 1, true); };

  $("wizNext").onclick = async () => {
    if (W.page === 1) { wizShow(2, false); return; }
    if (W.page === 2) { if (await wizCheckKey()) wizShow(3, false); return; }
    wizFinish();
  };

  $("wizKey").onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); $("wizNext").click(); }
  };
}

/* ------------------------------------------------ обновление */

async function checkUpdate(byHand) {
  if (byHand) toast("Смотрю, есть ли обновление…");
  const res = await api().check_update();
  if (!res || !res.update || !res.url) {
    // При запуске молчим: нет сети — не повод тревожить. А если человек
    // нажал сам, ответить надо обязательно, иначе кнопка выглядит сломанной.
    if (byHand) toast(res && res.error ? "Не удалось проверить обновление" : "У вас последняя версия");
    return;
  }

  $("updFrom").textContent = res.current || "";
  $("updTo").textContent = res.version || "";
  $("updImp").hidden = !res.important;
  $("updNotes").textContent = res.notes || "Что изменилось — автор не написал.";
  $("updErr").textContent = "";
  $("updBar").hidden = true;
  $("updBar").querySelector("i").style.width = "0%";
  $("updBox").classList.add("open");

  $("updLater").onclick = () => $("updBox").classList.remove("open");
  $("updGo").onclick = async () => {
    $("updGo").disabled = true;
    $("updGo").textContent = "Скачиваю…";
    $("updBar").hidden = false;
    const out = await api().install_update(res.url, res.sha256 || "");
    if (!out || !out.ok) {
      $("updGo").disabled = false;
      $("updGo").textContent = "Обновить";
      $("updErr").textContent = (out && out.error) || "Не удалось";
      return;
    }
    $("updGo").textContent = "Запускаю установку";
  };
}
