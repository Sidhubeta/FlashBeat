"""Minimal stand-ins for pyrogram / pytgcalls / ntgcalls so the Telegram layer can be import-tested offline."""
import sys, types, enum


class _F:
    def __init__(self, name="f"): self.name = name
    def __and__(self, o): return self
    def __rand__(self, o): return self
    def __invert__(self): return self


def install():
    pg = types.ModuleType("pyrogram")
    filters = types.ModuleType("pyrogram.filters")
    for n in ("group", "bot", "new_chat_members"): setattr(filters, n, _F(n))
    for n in ("command", "regex", "user"): setattr(filters, n, lambda *a, **k: _F(n))
    filters.create = lambda fn, name="x": _F(name)

    class Client:
        handlers = []
        def __init__(self, *a, **k): pass
        def on_message(self, *f):
            def deco(fn): Client.handlers.append(("msg", fn)); return fn
            return deco
        on_callback_query = on_message
    pg.Client, pg.filters = Client, filters

    ptypes = types.ModuleType("pyrogram.types")
    class _T:
        def __init__(self, *a, **k): self.a, self.k = a, k
    for n in ("InlineKeyboardButton", "InlineKeyboardMarkup", "Message", "CallbackQuery", "InputMediaPhoto", "LinkPreviewOptions"):
        setattr(ptypes, n, type(n, (_T,), {}))
    enums = types.ModuleType("pyrogram.enums")
    enums.ParseMode = types.SimpleNamespace(HTML="html")
    class E(enum.Enum): ADMINISTRATORS = 1; BANNED = 2; RESTRICTED = 3
    enums.ChatMembersFilter = enums.ChatMemberStatus = E
    errors = types.ModuleType("pyrogram.errors")
    for n in ("ChatAdminRequired", "UserNotParticipant", "UserAlreadyParticipant", "InviteRequestSent", "HideRequesterMissing", "FloodWait"):
        setattr(errors, n, type(n, (Exception,), {}))
    pg.types, pg.enums, pg.errors = ptypes, enums, errors
    for k, v in {"pyrogram": pg, "pyrogram.filters": filters, "pyrogram.types": ptypes, "pyrogram.enums": enums, "pyrogram.errors": errors}.items():
        sys.modules[k] = v

    nt = types.ModuleType("ntgcalls")
    for n in ("ConnectionNotFound", "RTMPStreamingUnsupported", "TelegramServerError", "TransportParseException", "ConnectionError"):
        setattr(nt, n, type(n, (Exception,), {}))
    ptg = types.ModuleType("pytgcalls"); ptg.PyTgCalls = type("PyTgCalls", (), {})
    ex = types.ModuleType("pytgcalls.exceptions")
    for n in ("NoActiveGroupCall", "NoAudioSourceFound", "NotInCallError"): setattr(ex, n, type(n, (Exception,), {}))
    ty = types.ModuleType("pytgcalls.types")
    for n in ("StreamEnded", "ChatUpdate", "MediaStream", "GroupCallConfig", "AudioQuality", "VideoQuality", "Update"):
        setattr(ty, n, type(n, (), {}))
    sess = types.ModuleType("pytgcalls.pytgcalls_session"); sess.PyTgCallsSession = type("S", (), {})
    ptg.exceptions, ptg.types = ex, ty
    for k, v in {"ntgcalls": nt, "pytgcalls": ptg, "pytgcalls.exceptions": ex, "pytgcalls.types": ty, "pytgcalls.pytgcalls_session": sess}.items():
        sys.modules[k] = v
