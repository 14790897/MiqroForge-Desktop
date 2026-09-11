"""Feishu (Lark) channel — WebSocket long-connection inbound, HTTP API outbound."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from loguru import logger

from miqi.bus.events import OutboundMessage
from miqi.bus.queue import MessageBus
from miqi.channels.base import BaseChannel
from miqi.config.schema import FeishuChannelConfig


class FeishuChannel(BaseChannel):
    """
    Feishu channel using lark-oapi WebSocket long connection.

    - **Inbound**: Feishu server pushes events over WebSocket (no public IP needed).
    - **Outbound**: Feishu HTTP API via lark-oapi Client (for progress/hint messages).

    Main agent responses should be sent via the feishu-mcp MCP tool
    ``mcp_feishu_send_message`` / ``mcp_feishu_reply_message`` so that the
    agent itself controls formatting and threading.
    """

    name = "feishu"

    def __init__(self, config: FeishuChannelConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: FeishuChannelConfig = config
        self._ws_thread: threading.Thread | None = None
        self._ws_client: Any = None   # lark.ws.Client — assigned on start()
        self._api_client: Any = None  # lark.Client   — assigned on start()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bot_open_id: str = ""   # bot's own open_id, fetched at startup
        # Per-chat debounce state (keyed by chat_id)
        self._debounce_tasks: dict[str, asyncio.Task] = {}
        self._debounce_buffers: dict[str, list[dict]] = {}

    # ------------------------------------------------------------------
    # BaseChannel interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the WebSocket long connection in a daemon background thread."""
        import lark_oapi as lark

        self._loop = asyncio.get_event_loop()
        self._running = True

        # HTTP API client reused for all outbound sends
        self._api_client = (
            lark.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .build()
        )

        # Capture values for the thread closure
        app_id = self.config.app_id
        app_secret = self.config.app_secret
        on_msg = self._on_message_receive

        def _run_ws() -> None:
            """
            Run the Feishu WebSocket client in a dedicated thread with its
            own event loop.

            lark_oapi/ws/client.py stores a module-level global variable
            ``loop`` that is captured once at import time.  Every internal
            method (start, _connect, _receive_message_loop, _ping_loop, …)
            references that same global ``loop``.  If the module was first
            imported while the main asyncio loop was already running, ``loop``
            points to the main loop, and ALL internal operations fail with
            "This event loop is already running" or "Future attached to a
            different loop".

            The only viable fix is to **monkey-patch** that module-level
            ``loop`` to a fresh event loop created in this thread, then call
            ``start()`` normally so all internal code paths use it.
            """
            import lark_oapi as _lark
            import lark_oapi.ws.client as _ws_mod

            # 1. Create a brand-new loop for this thread
            _new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_new_loop)

            # 2. Monkey-patch the module-level loop so every internal use
            #    (run_until_complete, create_task, etc.) targets THIS loop
            _ws_mod.loop = _new_loop

            # 3. Build handler & client inside this thread
            def _noop(data):
                return None
            _handler = (
                _lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(on_msg)
                .register_p2_im_message_reaction_created_v1(_noop)
                .register_p2_im_message_reaction_deleted_v1(_noop)
                .build()
            )
            _ws = _lark.ws.Client(
                app_id,
                app_secret,
                event_handler=_handler,
                log_level=_lark.LogLevel.INFO,
            )
            self._ws_client = _ws

            # 4. start() is blocking — it uses the (now-patched) module loop
            _ws.start()

        # Fetch bot's own open_id via REST API so we can detect @mentions reliably.
        # The lark-oapi Client does not expose a .bot namespace in all versions,
        # so we call the Feishu HTTP API directly with requests.
        try:
            import requests as _requests
            # Step 1: get app_access_token
            _token_resp = _requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
                json={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
                timeout=10,
            )
            _token = _token_resp.json().get("app_access_token", "")
            if _token:
                # Step 2: get bot info
                _bot_resp = _requests.get(
                    "https://open.feishu.cn/open-apis/bot/v3/info",
                    headers={"Authorization": f"Bearer {_token}"},
                    timeout=10,
                )
                _bot_data = _bot_resp.json()
                self._bot_open_id = _bot_data.get("bot", {}).get("open_id", "") or ""
                if self._bot_open_id:
                    logger.info("Feishu bot open_id: {}", self._bot_open_id)
                else:
                    logger.warning("Feishu: bot open_id not found in response: {}", _bot_data)
            else:
                logger.warning("Feishu: failed to obtain app_access_token")
        except Exception as _e:
            logger.warning("Feishu: error fetching bot open_id: {}", _e)

        self._ws_thread = threading.Thread(
            target=_run_ws,
            daemon=True,
            name="feishu-ws",
        )
        self._ws_thread.start()
        logger.info("Feishu WebSocket long connection started (app_id={}...)", self.config.app_id[:12])

        # Keep the async coroutine alive until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the channel. The daemon WS thread will exit with the process."""
        self._running = False
        logger.info("Feishu channel stopped")

    # Feishu text message hard limit is 30 720 bytes.
    # We use a conservative character limit (Chinese chars are 3 bytes each).
    _MAX_MSG_CHARS = 8000

    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message to the Feishu chat via the HTTP API.

        Used for progress updates and tool hints (controlled by
        ``channels.sendProgress`` / ``channels.sendToolHints`` config).
        Long messages are automatically split into chunks.
        """
        if not self._api_client:
            return
        text = msg.content
        # Split into chunks to stay within Feishu's 30 720-byte hard limit.
        chunks = [text[i:i + self._MAX_MSG_CHARS] for i in range(0, max(len(text), 1), self._MAX_MSG_CHARS)]
        for chunk in chunks:
            await self._send_text_chunk(msg.chat_id, chunk)

    async def _send_text_chunk(self, chat_id: str, text: str) -> None:
        """Send a single text chunk via the Feishu HTTP API."""
        try:
            import lark_oapi as lark

            content_json = json.dumps({"text": text}, ensure_ascii=False)
            request = (
                lark.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(content_json)
                    .build()
                )
                .build()
            )
            # run_in_executor so we don't block the event loop
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._api_client.im.v1.message.create(request),
            )
        except Exception as e:
            logger.error("Feishu send error: {}", e)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_non_text_content(msg_type: str, content: dict, message_id: str) -> str:
        """Build a descriptive text string for non-text message types.

        The returned text tells the agent what was received and how to
        retrieve the actual file via feishu-mcp tools.
        """
        if msg_type == "file":
            file_key = content.get("file_key", "")
            file_name = content.get("file_name", "unknown")
            file_size = content.get("file_size")
            size_str = f"，大小 {int(file_size)} 字节" if file_size else ""
            return (
                f"[收到文件] 文件名: {file_name}{size_str}\n"
                f"message_id: {message_id}, file_key: {file_key}\n"
                f"请使用 feishu-mcp 的 download_message_file 工具下载该文件 "
                f"(message_id=\"{message_id}\", file_key=\"{file_key}\", type=\"file\")。"
            )

        if msg_type == "image":
            image_key = content.get("image_key", "")
            return (
                f"[收到图片] image_key: {image_key}\n"
                f"message_id: {message_id}\n"
                f"请使用 feishu-mcp 的 download_message_file 工具下载该图片 "
                f"(message_id=\"{message_id}\", file_key=\"{image_key}\", type=\"image\")。"
            )

        if msg_type == "audio":
            file_key = content.get("file_key", "")
            duration = content.get("duration")
            dur_str = f"，时长 {duration}ms" if duration else ""
            return (
                f"[收到语音] file_key: {file_key}{dur_str}\n"
                f"message_id: {message_id}\n"
                f"请使用 feishu-mcp 的 download_message_file 工具下载该语音 "
                f"(message_id=\"{message_id}\", file_key=\"{file_key}\", type=\"audio\")。"
            )

        if msg_type == "video":
            file_key = content.get("file_key", "")
            image_key = content.get("image_key", "")
            duration = content.get("duration")
            dur_str = f"，时长 {duration}ms" if duration else ""
            return (
                f"[收到视频] file_key: {file_key}{dur_str}\n"
                f"message_id: {message_id}\n"
                f"请使用 feishu-mcp 的 download_message_file 工具下载该视频 "
                f"(message_id=\"{message_id}\", file_key=\"{file_key}\", type=\"video\")。"
            )

        if msg_type == "media":
            file_key = content.get("file_key", "")
            file_name = content.get("file_name", "unknown")
            return (
                f"[收到媒体文件] 文件名: {file_name}, file_key: {file_key}\n"
                f"message_id: {message_id}\n"
                f"请使用 feishu-mcp 的 download_message_file 工具下载该文件 "
                f"(message_id=\"{message_id}\", file_key=\"{file_key}\", type=\"file\")。"
            )

        # Fallback for unknown non-text types (sticker, share_chat, etc.)
        return (
            f"[收到 {msg_type} 类型消息]\n"
            f"message_id: {message_id}\n"
            f"请使用 feishu-mcp 的 get_message 工具查看完整消息内容 "
            f"(message_id=\"{message_id}\")。"
        )

    def _on_message_receive(self, data: Any) -> None:
        """
        lark-oapi event callback (runs in the WS daemon thread).

        Parses the Feishu message event and forwards it to the MessageBus
        via ``asyncio.run_coroutine_threadsafe`` so it executes on the
        main event loop.

        Supports text messages (original behaviour) as well as non-text
        messages (file, image, audio, video, media, etc.).  For non-text
        types a descriptive prompt is constructed so the agent knows what
        was received and which feishu-mcp tools to use to fetch the file.

        Group chat filtering: when ``require_mention_in_groups`` is True
        (default), messages in group chats are only forwarded if the bot
        is @mentioned.  The @mention placeholder is stripped from the
        text so the agent sees clean user intent.
        """
        try:
            msg = data.event.message
            sender = data.event.sender

            chat_id: str = msg.chat_id or ""
            sender_id: str = sender.sender_id.open_id or ""
            message_id: str = msg.message_id or ""
            msg_type: str = msg.message_type or "text"
            chat_type: str = getattr(msg, "chat_type", "") or ""

            # --- Group chat @mention filtering ---
            mentions: list[Any] = []
            try:
                raw_mentions = getattr(msg, "mentions", None)
                if raw_mentions:
                    mentions = list(raw_mentions)
            except Exception:
                pass

            bot_mentioned = False
            mention_names: list[str] = []
            for m in mentions:
                # Each mention has .id.open_id (or .id.union_id) and .name
                try:
                    m_id_type = getattr(m, "id_type", "") or ""
                    m_key = getattr(m, "key", "") or ""
                    m_name = getattr(m, "name", "") or ""
                    # The mention key in content is like "@_user_1"
                    mention_names.append((m_key, m_name))
                    # Feishu sets id_type="open_id" and id for user mentions;
                    # for app/bot mentions the id matches the bot's open_id.
                    # We check the name field as a fallback — lark-oapi populates
                    # a special "id_type" for the bot, or we detect via app_id.
                    m_id = getattr(m, "id", None)
                    if m_id:
                        m_id_val = getattr(m_id, "open_id", "") or ""
                    else:
                        m_id_val = ""
                    # Feishu sends bot @mentions with id_type="open_id" and
                    # id.open_id equal to the bot's own open_id (NOT id_type="app").
                    # We also keep the id_type=="app" check as a fallback.
                    if m_id_type == "app":
                        bot_mentioned = True
                    elif self._bot_open_id and m_id_val == self._bot_open_id:
                        bot_mentioned = True
                except Exception:
                    pass

            # Also detect bot mention via content text pattern:
            # Feishu text content contains @_user_N placeholder for mentions.
            # If any mention has id_type != "app", fall back to checking if
            # the text starts with an @mention pattern (common usage).
            if not bot_mentioned and mentions:
                # Heuristic: if the message has any mentions at all in a group,
                # check each mention — for bot @mentions, Feishu uses the user_id
                # in the mention object. We already checked id_type=="app" above.
                # As a final fallback, if the first mention key appears at position 0
                # in the text, treat it as directed at the bot.
                pass

            if chat_type == "group" and self.config.require_mention_in_groups:
                if not bot_mentioned:
                    logger.debug(
                        "Feishu: ignoring group message from {} in {} (bot not @mentioned)",
                        sender_id, chat_id,
                    )
                    return

            raw: str = msg.content or "{}"
            try:
                content_dict: dict = json.loads(raw)
            except json.JSONDecodeError:
                content_dict = {}

            if msg_type == "text":
                # Original text handling — extract "text" field
                text = content_dict.get("text", raw).strip()
                if not text:
                    return
                # Strip @mention placeholders from text for cleaner agent input
                if mentions and chat_type == "group":
                    for m_key, m_name in mention_names:
                        if m_key:
                            text = text.replace(m_key, "").strip()
            else:
                # Non-text message — build descriptive text for the agent
                text = self._extract_non_text_content(msg_type, content_dict, message_id)
                if not text:
                    return

            # Try to extract a display name for the sender
            sender_name = ""
            try:
                sender_obj = getattr(sender, "sender_id", None)
                if sender_obj:
                    sender_name = getattr(sender_obj, "name", "") or ""
            except Exception:
                pass

            metadata: dict[str, Any] = {
                "msg_type": msg_type,
                "message_id": message_id,
                "chat_type": chat_type,
            }
            if sender_name:
                metadata["sender_name"] = sender_name
            # Attach raw content for non-text messages so the agent can
            # parse file_key / image_key etc. directly if needed.
            if msg_type != "text":
                metadata["raw_content"] = raw

            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._enqueue_debounced(
                        sender_id=sender_id,
                        chat_id=chat_id,
                        content=text,
                        metadata=metadata,
                        sender_name=sender_name,
                    ),
                    self._loop,
                )
        except Exception as e:
            logger.error("Feishu _on_message_receive error: {}", e)

    async def _enqueue_debounced(
        self,
        *,
        sender_id: str,
        chat_id: str,
        content: str,
        metadata: dict,
        sender_name: str = "",
    ) -> None:
        """Debounce rapid messages from the same chat before forwarding to the bus.

        Within the ``reply_delay_ms`` window, all messages from the same chat
        are buffered.  When the window expires the buffered messages are merged
        (content joined with a newline separator) and forwarded as a single
        inbound message.  This prevents the agent from responding to a file
        attachment before the user has finished typing the accompanying text.

        Set ``channels.feishu.replyDelayMs`` to 0 to disable debouncing.
        """
        delay = self.config.reply_delay_ms / 1000.0
        if delay <= 0:
            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                metadata=metadata,
                sender_name=sender_name,
            )
            return

        # Append to per-chat buffer
        buf = self._debounce_buffers.setdefault(chat_id, [])
        buf.append({"sender_id": sender_id, "content": content, "metadata": metadata, "sender_name": sender_name})

        # Cancel any existing timer for this chat
        existing = self._debounce_tasks.get(chat_id)
        if existing and not existing.done():
            existing.cancel()

        async def _fire() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return  # reset by a later message — do nothing

            msgs = self._debounce_buffers.pop(chat_id, [])
            self._debounce_tasks.pop(chat_id, None)
            if not msgs:
                return

            # Merge: sender + metadata from first message, content joined
            first = msgs[0]
            if len(msgs) == 1:
                merged_content = first["content"]
                merged_metadata = first["metadata"]
            else:
                merged_content = "\n".join(m["content"] for m in msgs)
                # Use metadata of the last message (typically the follow-up text)
                merged_metadata = dict(msgs[-1]["metadata"])
                # Preserve raw_content from any file/image/etc. messages
                raw_list = [
                    m["metadata"]["raw_content"]
                    for m in msgs
                    if m["metadata"].get("raw_content")
                ]
                if raw_list:
                    merged_metadata["raw_contents"] = raw_list

            await self._handle_message(
                sender_id=first["sender_id"],
                chat_id=chat_id,
                content=merged_content,
                metadata=merged_metadata,
                sender_name=first.get("sender_name", ""),
            )

        self._debounce_tasks[chat_id] = asyncio.ensure_future(_fire())
