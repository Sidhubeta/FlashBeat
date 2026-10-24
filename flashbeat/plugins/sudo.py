import asyncio
import os
import sys
import time

import psutil
from pyrogram import errors, filters, types

from flashbeat import __version__
from flashbeat import runtime as rt
from flashbeat.config import config
from flashbeat.core.cache import dir_size
from flashbeat.helpers import text as T
from flashbeat.helpers.filters import sudo_only
from flashbeat.helpers.guard import arg

bot = rt.bot


def _ms(v):
    return "–" if v is None else (f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.1f} s")


@bot.on_message(filters.command("ping") & ~filters.bot)
async def ping_cmd(_, m: types.Message):
    t0 = time.perf_counter()
    sent = await m.reply_text("🏓 …")
    api = (time.perf_counter() - t0) * 1000
    s = rt.resolver.stats()
    lat = list(rt.latency)
    start = f"{sum(lat) / len(lat):.1f} s" if lat else "–"
    await sent.edit_text(
        f"🏓 <b>Pong</b> {api:.0f} ms\n"
        f"🎙 Voice: {rt.engine.ping()} ms\n\n"
        f"⚡ <b>Speed</b>\n"
        f"Search avg: {_ms(s['search_ms'])}\n"
        f"Stream resolve avg: {_ms(s['resolve_ms'])}\n"
        f"Play start avg: {start}\n"
        f"Cache hits: search {s['search_hit']:.0%} · streams {s['stream_hit']:.0%}")


@bot.on_message(filters.command("stats") & sudo_only)
async def stats_cmd(_, m: types.Message):
    up = T.fmt_time(time.time() - rt.boot_time)
    vm, disk = psutil.virtual_memory(), psutil.disk_usage(".")
    cache = dir_size("downloads") if os.path.isdir("downloads") else 0
    s = rt.resolver.stats()
    await m.reply_text(
        f"📊 <b>FlashBeat {__version__}</b>\n\n"
        f"⏱ Uptime: {up}\n🖥 CPU {psutil.cpu_percent()}% · RAM {vm.percent}% · Disk {disk.percent}%\n"
        f"💾 Song cache: {T.fmt_size(cache)} / {config.CACHE_MAX_MB} MB\n\n"
        f"💬 Chats: {len(rt.store.group_ids())} · 👤 Users: {len(rt.store.user_ids())}\n"
        f"🎧 Active calls: {len(rt.queue.active())} · ▶️ Plays: {rt.store.total_plays()}\n"
        f"🤖 Assistants: {len(rt.assistants.clients)} · Storage: {rt.store.backend.name}\n\n"
        f"⚡ search {_ms(s['search_ms'])} · resolve {_ms(s['resolve_ms'])} · "
        f"cached {s['search_cached']} searches / {s['streams_cached']} streams")


@bot.on_message(filters.command("active") & sudo_only)
async def active_cmd(_, m: types.Message):
    ids = rt.queue.active()
    if not ids:
        return await m.reply_text("Nothing is playing anywhere.")
    lines = []
    for cid in ids:
        t = rt.player.current(cid)
        lines.append(f"<code>{cid}</code> · {T.esc(T.short(t.title, 40))}")
    await m.reply_text(f"🎧 <b>{len(ids)} active</b>\n\n" + "\n".join(lines))


@bot.on_message(filters.command(["ban", "blacklist"]) & sudo_only)
async def ban_cmd(_, m: types.Message):
    a = arg(m)
    if not a.lstrip("-").isdigit():
        return await m.reply_text("Usage: <code>/ban user_or_chat_id</code>")
    rt.store.ban(int(a))
    await m.reply_text(f"🚫 Blacklisted <code>{a}</code>.")


@bot.on_message(filters.command("unban") & sudo_only)
async def unban_cmd(_, m: types.Message):
    a = arg(m)
    if not a.lstrip("-").isdigit():
        return await m.reply_text("Usage: <code>/unban id</code>")
    rt.store.unban(int(a))
    await m.reply_text(f"✅ Removed <code>{a}</code> from the blacklist.")


async def _uid(m: types.Message):
    if m.reply_to_message and m.reply_to_message.from_user:
        return m.reply_to_message.from_user.id
    a = arg(m)
    return int(a) if a.isdigit() else None


@bot.on_message(filters.command("addsudo") & filters.user(config.OWNER_ID))
async def addsudo_cmd(_, m: types.Message):
    uid = await _uid(m)
    if not uid:
        return await m.reply_text("Reply to a user or send <code>/addsudo id</code>.")
    rt.store.add_sudo(uid)
    await m.reply_text(f"✅ <code>{uid}</code> is now sudo.")


@bot.on_message(filters.command("rmsudo") & filters.user(config.OWNER_ID))
async def rmsudo_cmd(_, m: types.Message):
    uid = await _uid(m)
    if not uid:
        return await m.reply_text("Reply to a user or send <code>/rmsudo id</code>.")
    rt.store.rm_sudo(uid)
    await m.reply_text(f"🗑 <code>{uid}</code> is no longer sudo.")


@bot.on_message(filters.command("sudolist") & sudo_only)
async def sudolist_cmd(_, m: types.Message):
    ids = "\n".join(f"• <code>{i}</code>" for i in rt.store.sudoers) or "• (none)"
    await m.reply_text(f"👑 <b>Owner:</b> <code>{config.OWNER_ID}</code>\n\n<b>Sudo users</b>\n{ids}")


@bot.on_message(filters.command("broadcast") & sudo_only)
async def broadcast_cmd(_, m: types.Message):
    src = m.reply_to_message
    if not src:
        return await m.reply_text("Reply to a message and send <code>/broadcast [-user] [-nochat]</code>.")
    flags = set((m.text or "").split()[1:])
    targets: list[int] = [] if "-nochat" in flags else rt.store.group_ids()
    if "-user" in flags:
        targets += rt.store.user_ids()
    status = await m.reply_text(f"📣 Sending to {len(targets)} chats …")
    ok = bad = 0
    sem = asyncio.Semaphore(8)

    async def send(cid: int):
        nonlocal ok, bad
        async with sem:
            try:
                await src.copy(cid)
                ok += 1
            except errors.FloodWait as e:
                await asyncio.sleep(e.value + 1)
                try:
                    await src.copy(cid)
                    ok += 1
                except Exception:
                    bad += 1
            except Exception:
                bad += 1
            await asyncio.sleep(0.05)

    await asyncio.gather(*(send(c) for c in targets))
    await status.edit_text(f"📣 Broadcast done: ✅ {ok} · ❌ {bad}")


@bot.on_message(filters.command("logs") & sudo_only)
async def logs_cmd(_, m: types.Message):
    if os.path.exists("flashbeat.log"):
        await m.reply_document("flashbeat.log")
    else:
        await m.reply_text("No log file yet.")


@bot.on_message(filters.command("clearcache") & sudo_only)
async def clearcache_cmd(_, m: types.Message):
    freed = 0
    if os.path.isdir("downloads"):
        keep = set(rt.resolver.protect)
        for f in os.scandir("downloads"):
            if f.is_file() and f.path not in keep:
                freed += f.stat().st_size
                os.remove(f.path)
    rt.resolver.stream_cache._d.clear()
    rt.resolver.search_cache._d.clear()
    await m.reply_text(f"🧹 Freed {T.fmt_size(freed)} and cleared lookup caches.")


@bot.on_message(filters.command("restart") & sudo_only)
async def restart_cmd(_, m: types.Message):
    await m.reply_text("♻️ Restarting …")
    await rt.store.flush()
    os.execl(sys.executable, sys.executable, "-m", "flashbeat")
