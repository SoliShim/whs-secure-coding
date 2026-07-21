(function () {
  "use strict";

  const app = document.querySelector("[data-chat-app]");
  if (!app) return;

  const token = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const messages = app.querySelector("[data-chat-messages]");
  const placeholder = app.querySelector("[data-chat-placeholder]");
  const form = app.querySelector("[data-chat-form]");
  const input = app.querySelector("[data-chat-input]");
  const sendButton = app.querySelector("[data-chat-send]");
  const title = app.querySelector("[data-chat-title]");
  const kicker = app.querySelector("[data-chat-kicker]");
  const status = app.querySelector("[data-chat-status]");
  const connection = app.querySelector("[data-chat-connection]");
  const channelButtons = Array.from(app.querySelectorAll("[data-chat-channel]"));
  const currentUserId = Number(app.dataset.currentUserId);

  let socket = null;
  let authenticated = false;
  let desiredRecipient = null;
  let reconnectAttempt = 0;
  let reconnectTimer = null;
  let userClosed = false;

  function setStatus(message, mode) {
    status.textContent = message;
    connection.textContent = mode === "online" ? "연결됨" : mode === "error" ? "연결 오류" : "연결 중";
    connection.classList.toggle("is-online", mode === "online");
    connection.classList.toggle("is-error", mode === "error");
  }

  function websocketUrl() {
    const path = app.dataset.wsPath || "/ws/chat";
    const url = new URL(path, window.location.href);
    url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return url.toString();
  }

  function recipientValue(value) {
    if (value === "" || value === null || value === undefined) return null;
    const number = Number(value);
    return Number.isSafeInteger(number) && number > 0 ? number : null;
  }

  function sendFrame(frame) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(frame));
    return true;
  }

  function subscribe() {
    if (!authenticated) return;
    sendFrame({ type: "subscribe", recipient_id: desiredRecipient });
    setStatus("대화 내역을 불러오고 있어요.", "online");
  }

  function clearMessages() {
    messages.replaceChildren();
  }

  function timeLabel(value) {
    if (!value) return "방금";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "방금";
    return new Intl.DateTimeFormat("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    }).format(date);
  }

  function normalizedMessage(payload) {
    const source = payload && payload.message ? payload.message : payload;
    return {
      senderId: Number(source.sender_id),
      senderName: String(source.sender_display_name || source.sender_name || source.sender_username || "이웃").slice(0, 80),
      recipientId: recipientValue(source.recipient_id),
      body: String(source.body || "").slice(0, 500),
      createdAt: source.created_at || source.sent_at || null
    };
  }

  function belongsToCurrentChannel(message) {
    if (desiredRecipient === null) return message.recipientId === null;
    if (!Number.isSafeInteger(currentUserId)) return true;
    return (
      (message.senderId === currentUserId && message.recipientId === desiredRecipient) ||
      (message.senderId === desiredRecipient && message.recipientId === currentUserId)
    );
  }

  function messageNode(raw) {
    const message = normalizedMessage(raw);
    const mine = Number.isSafeInteger(currentUserId) && message.senderId === currentUserId;
    const article = document.createElement("article");
    article.className = mine ? "message message-mine" : "message";

    const avatar = document.createElement("span");
    avatar.className = "avatar avatar-small message-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = mine ? "나" : (message.senderName.trim().charAt(0) || "이");

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    const meta = document.createElement("p");
    meta.className = "message-meta";
    const sender = document.createElement("strong");
    sender.textContent = mine ? "나" : message.senderName;
    const time = document.createElement("time");
    time.textContent = timeLabel(message.createdAt);
    if (message.createdAt) time.dateTime = String(message.createdAt);
    meta.append(sender, time);
    const body = document.createElement("p");
    body.className = "message-body";
    body.textContent = message.body;
    bubble.append(meta, body);
    article.append(avatar, bubble);
    return { node: article, message: message };
  }

  function appendMessage(raw, shouldScroll) {
    const built = messageNode(raw);
    if (!built.message.body || !belongsToCurrentChannel(built.message)) return;
    messages.append(built.node);
    if (shouldScroll) messages.scrollTop = messages.scrollHeight;
  }

  function renderHistory(items) {
    clearMessages();
    const list = Array.isArray(items) ? items.slice(-100) : [];
    if (!list.length) {
      const empty = document.createElement("div");
      empty.className = "chat-empty-state";
      const heading = document.createElement("strong");
      heading.textContent = desiredRecipient === null ? "첫 인사를 건네 보세요" : "아직 나눈 대화가 없어요";
      const copy = document.createElement("p");
      copy.textContent = desiredRecipient === null ? "모든 이웃이 보는 공개 채널입니다." : "안전한 거래를 위해 필요한 내용만 나눠 주세요.";
      empty.append(heading, copy);
      messages.append(empty);
    } else {
      list.forEach(function (item) { appendMessage(item, false); });
    }
    messages.scrollTop = messages.scrollHeight;
    setStatus("메시지를 입력해 대화를 시작하세요.", "online");
  }

  function handleFrame(event) {
    if (typeof event.data !== "string" || event.data.length > 1_000_000) return;
    let frame;
    try {
      frame = JSON.parse(event.data);
    } catch (_error) {
      setStatus("서버 응답을 읽지 못했습니다.", "error");
      return;
    }

    if (frame.type === "ready") {
      authenticated = true;
      reconnectAttempt = 0;
      sendButton.disabled = false;
      subscribe();
      return;
    }
    if (frame.type === "ping") {
      sendFrame({ type: "pong" });
      return;
    }
    if (frame.type === "history") {
      renderHistory(frame.messages || frame.items || []);
      return;
    }
    if (frame.type === "message") {
      if (placeholder?.isConnected) placeholder.remove();
      appendMessage(frame, true);
      setStatus("새 메시지가 도착했습니다.", "online");
      return;
    }
    if (frame.type === "error") {
      setStatus(String(frame.message || "채팅 요청을 처리하지 못했습니다.").slice(0, 200), "error");
    }
  }

  function scheduleReconnect() {
    if (userClosed || reconnectTimer) return;
    reconnectAttempt += 1;
    const delay = Math.min(15000, 700 * Math.pow(2, reconnectAttempt - 1));
    setStatus("연결이 끊어져 다시 연결합니다.", "error");
    reconnectTimer = window.setTimeout(function () {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    if (!token) {
      setStatus("보안 토큰이 없어 채팅을 시작할 수 없습니다. 페이지를 새로고침해 주세요.", "error");
      return;
    }
    authenticated = false;
    sendButton.disabled = true;
    setStatus("서버에 안전하게 연결하고 있어요.", "connecting");
    socket = new WebSocket(websocketUrl());
    socket.addEventListener("open", function () {
      // The authentication frame is deliberately the first frame on every connection.
      sendFrame({ type: "auth", csrf_token: token });
    });
    socket.addEventListener("message", handleFrame);
    socket.addEventListener("error", function () {
      setStatus("채팅 서버에 연결할 수 없습니다.", "error");
    });
    socket.addEventListener("close", function (event) {
      authenticated = false;
      sendButton.disabled = true;
      if (event.code === 4401) {
        userClosed = true;
        setStatus("로그인 세션이 만료되었습니다. 다시 로그인해 주세요.", "error");
        return;
      }
      if (event.code === 4403) {
        userClosed = true;
        setStatus("현재 주소에서는 채팅 연결이 허용되지 않습니다.", "error");
        return;
      }
      if (event.code === 4429) {
        userClosed = true;
        setStatus("연결 요청이 너무 많습니다. 잠시 후 페이지를 새로고침해 주세요.", "error");
        return;
      }
      if (event.code === 1000 || userClosed) return;
      scheduleReconnect();
    });
  }

  function chooseChannel(button) {
    desiredRecipient = recipientValue(button.dataset.recipientId);
    channelButtons.forEach(function (candidate) {
      const selected = candidate === button;
      candidate.classList.toggle("is-active", selected);
      if (selected) candidate.setAttribute("aria-current", "true");
      else candidate.removeAttribute("aria-current");
    });
    const label = button.querySelector("strong")?.textContent || "전체 채팅";
    title.textContent = label;
    kicker.textContent = desiredRecipient === null ? "공개 채널" : "1:1 대화";
    clearMessages();
    const loading = document.createElement("div");
    loading.className = "chat-loading";
    const copy = document.createElement("p");
    copy.textContent = "대화 내역을 불러오는 중입니다.";
    loading.append(copy);
    messages.append(loading);
    subscribe();
    input.focus();
  }

  channelButtons.forEach(function (button) {
    button.addEventListener("click", function () { chooseChannel(button); });
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    const body = input.value.trim();
    if (!body || body.length > 500 || !authenticated) return;
    const nonce = window.crypto && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : String(Date.now()) + "_" + Math.random().toString(36).slice(2, 14);
    if (sendFrame({ type: "message", recipient_id: desiredRecipient, body: body, nonce: nonce })) {
      input.value = "";
      setStatus("메시지를 전송했습니다.", "online");
    }
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  window.addEventListener("beforeunload", function () {
    userClosed = true;
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    if (socket) socket.close(1000, "page unload");
  });

  const requestedRecipient = recipientValue(app.dataset.selectedRecipientId || new URLSearchParams(window.location.search).get("with_user"));
  const requestedButton = requestedRecipient === null ? null : channelButtons.find(function (button) {
    return recipientValue(button.dataset.recipientId) === requestedRecipient;
  });
  if (requestedButton) chooseChannel(requestedButton);
  connect();
})();
