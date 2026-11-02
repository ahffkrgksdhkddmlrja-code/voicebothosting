import asyncio
import json
import os
import random
import re
import sys
import uuid
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()  # .env 파일에서 환경변수 불러오기

# --------------------------------------------------
# [ 봇 기본 설정 ]
# --------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class LoggingCommandTree(discord.app_commands.CommandTree):
    async def _call(self, interaction: discord.Interaction) -> None:
        await super()._call(interaction)
        # 아무 작업도 하지 않음


bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents, tree_cls=LoggingCommandTree)

_data_lock = asyncio.Lock()
_chat_locks: dict[int, asyncio.Lock] = {}
_chat_locks_guard = asyncio.Lock()
_logged_interaction_ids: set[int] = set()
_startup_logged = False

# 한국 시간대(KST) 및 일일 상한선 설정
KST = timezone(timedelta(hours=9))
DAILY_MAX_EYEBALLS = 7500

# 층별 눈알 한도 설정
FLOOR_LIMITS = {
    "1층": 500,
    "2층": 1000,
    "3층": 1500,
    "4층": 2000,
    "5층": 2500,
}

# 등급 순서 및 눈알 소비 요구량(해당 구간에서 소비해야 하는 눈알 수)
GRADE_REQUIREMENTS = {
    "이방인": 0,
    "토파즈": 2000,
    "아메시스트": 4000,
    "앰버": 6000,
    "에메랄드": 8000,
    "골드": 10000,
    "루비": 12000,
    "사파이어": 14000,
    "오닉스": 16000,
    "다이아몬드": 18000,
}
GRADE_ORDER = list(GRADE_REQUIREMENTS.keys())

# 등급별 맞춤 상점 구조 (정해진 수 만큼 눈알을 사용하면 자동으로 등급 상승)
TIER_SHOPS = {
    "이방인 상점 (일반 상점)": {
        "tier_name": "이방인 상점 (일반 상점)",
        "grades": ["이방인"],
        "description": "이방인 등급을 위한 일반 상점입니다. (눈알 2,000개 사용 시 토파즈 승급)",
        "items": {
            "운동화": {"price": 100},
            "구두": {"price": 400},
            "양말": {"price": 40},
            "랭콜로르-네이키드와 교환하기": {"price": 0},
            "재킷": {"price": 500},
            "티셔츠": {"price": 300},
            "나뭇잎": {"price": 1000},
            "바지": {"price": 500},
        },
    },
    "토파즈~앰버 상점 (일반 상점)": {
        "tier_name": "토파즈~앰버 상점 (일반 상점)",
        "grades": ["토파즈", "아메시스트", "앰버"],
        "description": "토파즈, 아메시스트, 앰버 등급을 위한 일반 상점입니다.",
        "items": {
            "안경": {"price": 450},
            "장갑": {"price": 500},
            "모자": {"price": 600},
            "손수건": {"price": 800},
            "머리띠": {"price": 10},
        },
    },
    "에메랄드~골드 상점 (악세서리 상점)": {
        "tier_name": "에메랄드~골드 상점 (악세서리 상점)",
        "grades": ["에메랄드", "골드"],
        "description": "에메랄드, 골드 등급을 위한 악세서리 상점입니다.",
        "items": {
            "철반지": {"price": 3000},
            "귀걸이": {"price": 400},
            "팔찌": {"price": 800},
            "지갑": {"price": 1000},
            "가방": {"price": 2000},
        },
    },
    "루비~사파이어 상점 (가구 상점)": {
        "tier_name": "루비~사파이어 상점 (가구 상점)",
        "grades": ["루비", "사파이어"],
        "description": "루비, 사파이어 등급을 위한 가구 상점입니다.",
        "items": {
            "의자": {"price": 2000},
            "책상": {"price": 4000},
            "서랍": {"price": 3500},
            "옷장": {"price": 5000},
            "침대": {"price": 6000},
        },
    },
    "오닉스 상점 (보석 상점)": {
        "tier_name": "오닉스 상점 (보석 상점)",
        "grades": ["오닉스"],
        "description": "오닉스 등급을 위한 보석 상점입니다. (눈알 18,000개 사용 시 다이아몬드 승급 승인 요청)",
        "items": {
            "토파즈": {"price": 2000},
            "자수정": {"price": 4000},
            "호박": {"price": 6000},
            "에메랄드": {"price": 8000},
            "금": {"price": 10000},
            "루비": {"price": 12000},
            "사파이어": {"price": 14000},
            "오닉스": {"price": 16000},
            "다이아몬드": {"price": 18000},
        },
    },
    "다이아몬드 상점 (칭호 상점)": {
        "tier_name": "다이아몬드 상점 (칭호 상점)",
        "grades": ["다이아몬드"],
        "description": "다이아몬드 등급을 위한 칭호 상점입니다.",
        "items": {
            "시즌 칭호": {"price": 20000, "desc": "한달마다 칭호 변경"},
            "시지프의 거물": {"price": 280000, "desc": "최고의 칭호"},
        },
    },
}


def get_tier_key_for_grade(grade: str) -> str:
    """해당 등급이 이용 가능한 상점 키 반환"""
    for tier_key, tier in TIER_SHOPS.items():
        if grade in tier["grades"]:
            return tier_key
    return "이방인 상점 (일반 상점)"


def find_item_info(item_name: str):
    """아이템 이름으로 소속 상점 키, 상점 정보, 아이템 정보 반환"""
    for tier_key, tier in TIER_SHOPS.items():
        if item_name in tier["items"]:
            return tier_key, tier, tier["items"][item_name]
    return None, None, None

# 팀 설정
TEAMS = ["르누아르", "르카키", "르네앙", "르루주"]

# --------------------------------------------------
# [ 파일 및 데이터 관리 경로 ]
# --------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "eyeballs.json")
SHOP_FILE = os.path.join(BASE_DIR, "shop.json")
MARKET_FILE = os.path.join(BASE_DIR, "market.json")
SHOP_CHANNELS_FILE = os.path.join(BASE_DIR, "shop_channels.json")
ADMIN_ROLES_FILE = os.path.join(BASE_DIR, "admin_roles.json")
LOG_CHANNEL_FILE = os.path.join(BASE_DIR, "log_channel.json")
ADMIN_CHANNEL_FILE = os.path.join(BASE_DIR, "admin_channel.json")
DEFAULT_ROLE_FILE = os.path.join(BASE_DIR, "default_role.json")
FLOOR_CHANNELS_FILE = os.path.join(BASE_DIR, "floor_channels.json")
TEAM_ROLES_FILE = os.path.join(BASE_DIR, "team_roles.json")
GRADE_ROLES_FILE = os.path.join(BASE_DIR, "grade_roles.json")
PROMOTION_REQUESTS_FILE = os.path.join(BASE_DIR, "promotion_requests.json")
REWARD_OPTIONS = [40, 45, 50, 55, 60, 65, 70]


def get_today_str():
    """현재 한국 날짜 문자열 반환 (YYYY-MM-DD)"""
    return datetime.now(KST).strftime("%Y-%m-%d")


def load_default_roles() -> dict:
    """서버별 기본 지급 역할 ID 불러오기"""
    if os.path.exists(DEFAULT_ROLE_FILE):
        try:
            with open(DEFAULT_ROLE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_default_role(guild_id: int, role_id: int):
    """서버별 기본 지급 역할 ID 저장하기"""
    data = load_default_roles()
    data[str(guild_id)] = role_id
    with open(DEFAULT_ROLE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def delete_default_role(guild_id: int):
    """서버별 기본 지급 역할 설정 삭제하기"""
    data = load_default_roles()
    data.pop(str(guild_id), None)
    with open(DEFAULT_ROLE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_floor_channels() -> dict:
    """층별 채널 ID 불러오기"""
    if os.path.exists(FLOOR_CHANNELS_FILE):
        try:
            with open(FLOOR_CHANNELS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_floor_channels(data: dict):
    """층별 채널 ID 저장하기"""
    with open(FLOOR_CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_team_roles() -> dict:
    """팀별 역할 ID 불러오기"""
    if os.path.exists(TEAM_ROLES_FILE):
        try:
            with open(TEAM_ROLES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_team_roles(data: dict):
    """팀별 역할 ID 저장하기"""
    with open(TEAM_ROLES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_grade_roles() -> dict:
    """등급별 역할 ID 불러오기"""
    if os.path.exists(GRADE_ROLES_FILE):
        try:
            with open(GRADE_ROLES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_grade_roles(data: dict):
    """등급별 역할 ID 저장하기"""
    with open(GRADE_ROLES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_promotion_requests() -> dict:
    """승급 요청 데이터 불러오기"""
    if os.path.exists(PROMOTION_REQUESTS_FILE):
        try:
            with open(PROMOTION_REQUESTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_promotion_requests(data: dict):
    """승급 요청 데이터 저장하기"""
    with open(PROMOTION_REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_log_channels() -> dict:
    """서버별 로그 채널 ID 불러오기"""
    if os.path.exists(LOG_CHANNEL_FILE):
        try:
            with open(LOG_CHANNEL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_log_channel(guild_id: int, channel_id: int):
    """서버별 로그 채널 ID 저장하기"""
    data = load_log_channels()
    data[str(guild_id)] = channel_id
    with open(LOG_CHANNEL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_admin_channels() -> dict:
    """서버별 관리자 채널 ID 불러오기"""
    if os.path.exists(ADMIN_CHANNEL_FILE):
        try:
            with open(ADMIN_CHANNEL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_admin_channel(guild_id: int, channel_id: int):
    """서버별 관리자 채널 ID 저장하기"""
    data = load_admin_channels()
    data[str(guild_id)] = channel_id
    with open(ADMIN_CHANNEL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


async def get_admin_channel(guild: discord.Guild):
    """관리자 채널 객체 가져오기 (지정되어 있지 않으면 로그 채널 또는 기본 채널 확인)"""
    if not guild:
        return None
    data = load_admin_channels()
    ch_id = data.get(str(guild.id))
    if ch_id:
        ch = guild.get_channel(int(ch_id)) or bot.get_channel(int(ch_id))
        if ch:
            return ch
    # 관리자 채널이 별도로 없으면 로그 채널 확인
    return await get_or_create_log_channel(guild)


def _is_loggable_channel(channel) -> bool:
    """메시지를 보낼 수 있는 채널인지 확인 (텍스트/공지/스레드/음성 채팅)"""
    if channel is None or isinstance(channel, (discord.ForumChannel, discord.CategoryChannel)):
        return False
    return hasattr(channel, "send") and callable(getattr(channel, "send"))


def _channel_sort_key(channel) -> int:
    return 0 if "로그" in (getattr(channel, "name", "") or "") else 1


async def _resolve_log_channel(guild: discord.Guild, channel_id: int):
    channel = guild.get_channel(channel_id) or bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    if isinstance(channel, discord.ForumChannel):
        for thread in getattr(channel, "threads", []):
            if "로그" in (thread.name or ""):
                return thread
        try:
            created = await channel.create_thread(
                name="명령어-로그",
                content="명령어 사용 로그가 이 글에 기록됩니다.",
            )
            thread = created[0] if isinstance(created, tuple) else created
            return thread
        except Exception as e:
            print(f"포럼 로그 스레드 생성 실패: {e}")
            return None
    if _is_loggable_channel(channel):
        return channel
    return None


async def get_or_create_log_channel(guild: discord.Guild):
    """로그 채널을 가져오거나 없으면 자동으로 '명령어-로그' 채널 생성"""
    if not guild:
        return None
    data = load_log_channels()
    ch_id = data.get(str(guild.id))
    if ch_id:
        ch = await _resolve_log_channel(guild, int(ch_id))
        if ch:
            return ch

    candidates = []
    for ch in guild.text_channels:
        name = ch.name or ""
        if name in ["명령어-로그", "명령어로그", "봇-로그", "봇로그"] or "로그" in name:
            candidates.append(ch)
    if candidates:
        candidates.sort(key=_channel_sort_key)
        save_log_channel(guild.id, candidates[0].id)
        return candidates[0]

    try:
        new_ch = await guild.create_text_channel(
            name="명령어-로그",
            topic="봇 명령어 사용 및 거래 내역 로그 채널",
            reason="명령어 로그 채널 자동 생성",
        )
        save_log_channel(guild.id, new_ch.id)
        return new_ch
    except Exception as e:
        print(f"로그 채널 자동 생성 실패: {e}")
        return None


async def _try_send_log_message(channel, text: str) -> bool:
    me = getattr(channel.guild, "me", None) if getattr(channel, "guild", None) else None
    if me is not None:
        perms = channel.permissions_for(me)
        if not perms.send_messages or not perms.view_channel:
            try:
                await channel.set_permissions(
                    me,
                    send_messages=True,
                    view_channel=True,
                    embed_links=True,
                    read_message_history=True,
                )
            except Exception as e:
                print(f"로그 채널 권한 부여 실패 ({getattr(channel, 'name', channel.id)}): {e}")
    try:
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception as e:
        print(f"로그 전송 실패 ({getattr(channel, 'name', channel.id)}): {e}")
        return False


async def send_log(guild: discord.Guild, text: str):
    """지정된 또는 자동 생성된 로그 채널로 로그 메시지 전송"""
    if not guild:
        print(f"로그 전송 중단: guild 없음\n{text}")
        return

    tried_ids = set()
    primary = await get_or_create_log_channel(guild)
    if primary and await _try_send_log_message(primary, text):
        return
    if primary:
        tried_ids.add(primary.id)

    fallbacks = [
        ch for ch in guild.text_channels
        if ch.id not in tried_ids and "로그" in (ch.name or "")
    ]
    fallbacks.sort(key=_channel_sort_key)
    for ch in fallbacks:
        if await _try_send_log_message(ch, text):
            save_log_channel(guild.id, ch.id)
            return

    print(f"로그 채널에 메시지를 보내지 못했습니다. (서버: {guild.name}) /로그채널설정 으로 텍스트 채널을 지정해 주세요.")


def load_shop_channels() -> list[int]:
    """등록된 상점 채널/포럼 ID 목록 불러오기"""
    if os.path.exists(SHOP_CHANNELS_FILE):
        try:
            with open(SHOP_CHANNELS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_shop_channels(channels: list[int]):
    """상점 채널/포럼 ID 목록 저장하기"""
    with open(SHOP_CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=4)


def is_shop_channel(channel) -> bool:
    """해당 채널 또는 부모 채널(포럼 등)이 등록된 상점 채널인지 확인"""
    allowed_ids = load_shop_channels()
    if not allowed_ids:
        return False
    # 채널 자체 또는 포럼 글(스레드)의 부모 포럼 채널 ID 확인
    if channel.id in allowed_ids:
        return True
    parent_id = getattr(channel, "parent_id", None)
    if parent_id and parent_id in allowed_ids:
        return True
    return False


def load_admin_roles() -> list[int]:
    """등록된 관리자 직업(역할) ID 목록 불러오기"""
    if os.path.exists(ADMIN_ROLES_FILE):
        try:
            with open(ADMIN_ROLES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_admin_roles(roles: list[int]):
    """관리자 직업(역할) ID 목록 저장하기"""
    with open(ADMIN_ROLES_FILE, "w", encoding="utf-8") as f:
        json.dump(roles, f, ensure_ascii=False, indent=4)


def is_admin_user(user, guild: discord.Guild = None) -> bool:
    """유저가 서버 관리자이거나 관리자 직업(역할)을 소지하고 있는지 확인"""
    member = user
    if not isinstance(member, discord.Member) and guild:
        member = guild.get_member(user.id)
    if isinstance(member, discord.Member):
        if member.guild_permissions.administrator:
            return True
        admin_roles = load_admin_roles()
        if any(role.id in admin_roles for role in member.roles):
            return True
    return False


def is_infinite_money(user, guild: discord.Guild = None) -> bool:
    """유저가 무한 재화(돈 무한) 대상인지 확인 (거인 직업 소지자)"""
    return is_admin_user(user, guild)


def admin_only():
    """관리자 또는 거인 직업 소지자 전용 커맨드 체크 데코레이터"""
    async def predicate(ctx: commands.Context):
        if not is_admin_user(ctx.author, ctx.guild):
            raise commands.CheckFailure("[오류] 이 명령어는 관리자 또는 거인 직업 역할을 가진 유저만 사용할 수 있습니다.")
        return True
    return commands.check(predicate)


def load_market_data():
    """유저간 거래 매물 데이터 불러오기"""
    if os.path.exists(MARKET_FILE):
        try:
            with open(MARKET_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_market_data(data):
    """유저간 거래 매물 데이터 저장하기"""
    with open(MARKET_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


DEFAULT_SHOP_ITEMS = {}
GENERAL_SHOP_ITEMS = {
    name: {"price": info["price"], "description": info.get("desc", "")}
    for tier in TIER_SHOPS.values()
    for name, info in tier["items"].items()
}


def load_shop_data():
    """기본 상점 아이템 목록 불러오기"""
    if os.path.exists(SHOP_FILE):
        try:
            with open(SHOP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return GENERAL_SHOP_ITEMS.copy()
    else:
        save_shop_data(GENERAL_SHOP_ITEMS)
        return GENERAL_SHOP_ITEMS.copy()


def save_shop_data(data):
    with open(SHOP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_data():
    """유저 데이터 불러오기"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                today = get_today_str()
                for uid, val in list(data.items()):
                    if isinstance(val, int):
                        data[uid] = {
                            "eyeballs": val,
                            "eyeballs_spent": 0,
                            "chat_count": 0,
                            "daily_earned": 0,
                            "last_earned_date": today,
                            "inventory": {},
                            "grade": "이방인",
                            "team": None,
                            "floor_earned": {},
                        }
                    elif isinstance(val, dict):
                        if "eyeballs_spent" not in val:
                            val["eyeballs_spent"] = 0
                        if "daily_earned" not in val or "last_earned_date" not in val:
                            val["daily_earned"] = 0
                            val["last_earned_date"] = today
                        if "inventory" not in val or not isinstance(val["inventory"], dict):
                            val["inventory"] = {}
                        if "grade" not in val:
                            val["grade"] = "이방인"
                        if "team" not in val:
                            val["team"] = None
                        if "floor_earned" not in val or not isinstance(val["floor_earned"], dict):
                            val["floor_earned"] = {}
                return data
        except Exception:
            return {}
    return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def get_user_data(user_id):
    data = load_data()
    user_key = str(user_id)
    today = get_today_str()

    if user_key not in data:
        data[user_key] = {
            "eyeballs": 0,
            "eyeballs_spent": 0,
            "chat_count": 0,
            "daily_earned": 0,
            "last_earned_date": today,
            "inventory": {},
            "grade": "이방인",
            "team": None,
            "floor_earned": {},
        }
        save_data(data)
    else:
        changed = False
        if "eyeballs_spent" not in data[user_key]:
            data[user_key]["eyeballs_spent"] = 0
            changed = True
        if data[user_key].get("last_earned_date") != today:
            data[user_key]["daily_earned"] = 0
            data[user_key]["last_earned_date"] = today
            data[user_key]["floor_earned"] = {}
            changed = True
        if "inventory" not in data[user_key]:
            data[user_key]["inventory"] = {}
            changed = True
        if "grade" not in data[user_key]:
            data[user_key]["grade"] = "이방인"
            changed = True
        if "team" not in data[user_key]:
            data[user_key]["team"] = None
            changed = True
        if "floor_earned" not in data[user_key]:
            data[user_key]["floor_earned"] = {}
            changed = True
        if changed:
            save_data(data)

    return data[user_key]


async def add_eyeballs(user_id, amount, guild: discord.Guild = None, member: discord.Member = None):
    """눈알을 더하거나 뺀다. 차감 시 단계별 소비량을 누적하여 정해진 요구량 달성 시 자동 등급업을 처리한다.
    오닉스에서 다이아몬드로 승급 시에는 관리자 채널에 승인 요청을 보낸다.
    반환값: (현재 눈알, 달성한 새 등급 or None, 승인 대기 여부 bool)
    """
    data = load_data()
    user_key = str(user_id)
    today = get_today_str()

    if user_key not in data:
        data[user_key] = {
            "eyeballs": 0,
            "eyeballs_spent": 0,
            "chat_count": 0,
            "daily_earned": 0,
            "last_earned_date": today,
            "inventory": {},
            "grade": "이방인",
            "team": None,
            "floor_earned": {},
        }

    data[user_key]["eyeballs"] = data[user_key].get("eyeballs", 0) + amount

    new_grade = None
    is_pending = False

    if amount < 0:
        spent = data[user_key].get("eyeballs_spent", 0) + abs(amount)
        current_grade = data[user_key].get("grade", "이방인")
        grade_list = GRADE_ORDER

        while True:
            curr_idx = grade_list.index(current_grade) if current_grade in grade_list else 0
            if curr_idx >= len(grade_list) - 1:
                break
            next_grade_name = grade_list[curr_idx + 1]
            req = GRADE_REQUIREMENTS[next_grade_name]

            if spent >= req:
                # 다이아몬드 승급은 관리자 수락/승인 필요
                if next_grade_name == "다이아몬드":
                    promotion_requests = load_promotion_requests()
                    has_pending = any(
                        r.get("user_id") == user_id and r.get("status") == "pending"
                        for r in promotion_requests.values()
                    )
                    if not has_pending:
                        request_id = f"{user_id}_{next_grade_name}_{datetime.now(KST).strftime('%Y%m%d%H%M%S')}"
                        user_name = member.display_name if member else user_key
                        promotion_requests[request_id] = {
                            "user_id": user_id,
                            "user_name": user_name,
                            "current_grade": current_grade,
                            "target_grade": next_grade_name,
                            "required_eyeballs": req,
                            "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                            "status": "pending",
                        }
                        save_promotion_requests(promotion_requests)

                        # 관리자 채널에 수락/유지 버튼 메시지 전송
                        if guild:
                            admin_ch = await get_admin_channel(guild)
                            if admin_ch:
                                embed = discord.Embed(
                                    title=f"👑 [다이아몬드 등급업 승인 요청] {user_name}",
                                    description=f"**{current_grade}**에서 눈알 **{req:,}개**를 사용하여 **{next_grade_name}** 승급 조건을 충족했습니다!",
                                    color=discord.Color.blue(),
                                )
                                embed.add_field(name="유저", value=f"<@{user_id}> (`{user_id}`)", inline=True)
                                embed.add_field(name="현재 등급", value=f"**{current_grade}**", inline=True)
                                embed.add_field(name="목표 등급", value=f"👑 **{next_grade_name}**", inline=True)
                                embed.add_field(name="누적 소비 눈알", value=f"`{req:,}`개 충족", inline=True)
                                embed.add_field(name="요청 일시", value=promotion_requests[request_id]["created_at"], inline=False)
                                embed.set_footer(text=f"요청 ID: {request_id}\n관리자는 아래 버튼을 눌러 승급을 수락하거나 유지할 수 있습니다.")

                                view = PromotionApprovalView(request_id, user_id, next_grade_name)
                                try:
                                    await admin_ch.send(embed=embed, view=view)
                                except Exception as e:
                                    print(f"관리자 채널 승급 요청 전송 실패: {e}")

                    is_pending = True
                    new_grade = next_grade_name
                    break
                else:
                    # 일반 등급(토파즈 ~ 오닉스)은 자동 승급
                    spent -= req
                    current_grade = next_grade_name
                    new_grade = next_grade_name
                    if member and guild:
                        await apply_grade_upgrade(guild, member, new_grade)
            else:
                break

        data[user_key]["grade"] = current_grade
        data[user_key]["eyeballs_spent"] = spent

    save_data(data)
    return data[user_key]["eyeballs"], new_grade, is_pending


async def apply_grade_upgrade(guild: discord.Guild, member: discord.Member, new_grade: str):
    """등급업 시 역할을 지급하는 헬퍼 (비동기). 봇 권한 및 역할 순위 검증 포함."""
    if guild is None or member is None:
        return
    grade_roles = load_grade_roles()
    if new_grade in grade_roles:
        role = guild.get_role(grade_roles[new_grade])
        if role:
            # 봇의 최고 역할보다 지정된 역할이 높은 위치에 있는지 확인
            bot_member = guild.me
            if bot_member and bot_member.top_role <= role:
                print(
                    f"[권한 부족 경고] 역할 '{role.name}'의 위치가 봇 역할({bot_member.top_role.name})보다 높거나 같습니다. "
                    f"서버 설정 > 역할에서 봇의 역할을 '{role.name}' 위로 올려주세요."
                )
            try:
                await member.add_roles(role, reason=f"자동 등급업: {new_grade}")
            except discord.Forbidden:
                print(
                    f"[403 Forbidden] '{role.name}' 역할 지급 실패: 봇에게 '역할 관리(Manage Roles)' 권한이 없거나, "
                    f"지급하려는 역할이 봇 역할보다 상위에 위치해 있습니다."
                )
            except Exception as e:
                print(f"역할 지급 실패: {e}")


def add_item_to_inventory(user_id, item_name, count):
    data = load_data()
    user_key = str(user_id)
    today = get_today_str()

    if user_key not in data:
        data[user_key] = {
            "eyeballs": 0,
            "chat_count": 0,
            "daily_earned": 0,
            "last_earned_date": today,
            "inventory": {},
        }

    inv = data[user_key].setdefault("inventory", {})
    current_cnt = inv.get(item_name, 0)
    new_cnt = current_cnt + count

    if new_cnt <= 0:
        inv.pop(item_name, None)
        new_cnt = 0
    else:
        inv[item_name] = new_cnt

    save_data(data)
    return new_cnt


async def _get_chat_lock(user_id: int) -> asyncio.Lock:
    async with _chat_locks_guard:
        lock = _chat_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _chat_locks[user_id] = lock
        return lock


async def increment_chat_count(user_id, channel_id=None):
    """채팅 1회를 원자적으로 반영하고, 50회마다 층별 눈알 지급"""
    lock = await _get_chat_lock(user_id)
    async with lock:
        async with _data_lock:
            data = load_data()
            user_key = str(user_id)
            today = get_today_str()

            if user_key not in data:
                data[user_key] = {
                    "eyeballs": 0,
                    "chat_count": 0,
                    "daily_earned": 0,
                    "last_earned_date": today,
                    "inventory": {},
                    "grade": "토파즈",
                    "team": None,
                    "floor_earned": {},
                }

            if data[user_key].get("last_earned_date") != today:
                data[user_key]["daily_earned"] = 0
                data[user_key]["last_earned_date"] = today
                data[user_key]["floor_earned"] = {}

            # 1. 먼저 카운트를 1 증가시킵니다.
            data[user_key]["chat_count"] = data[user_key].get("chat_count", 0) + 1

            status = None
            reward = 0
            floor_name = None

            # 층별 채널 확인
            if channel_id:
                floor_channels = load_floor_channels()
                for floor, ch_id in floor_channels.items():
                    if ch_id == channel_id:
                        floor_name = floor
                        break

            # 2. 50번째 채팅이 도달했는지 확인합니다.
            if data[user_key]["chat_count"] >= 50:
                if floor_name:
                    floor_limit = FLOOR_LIMITS.get(floor_name, 0)
                    floor_earned = data[user_key].get("floor_earned", {}).get(floor_name, 0)
                    current_daily = data[user_key].get("daily_earned", 0)

                    # 보상 지급이 가능한 상태인지 확인
                    if floor_earned < floor_limit and current_daily < DAILY_MAX_EYEBALLS:
                        raw_reward = random.choice(REWARD_OPTIONS)
                        floor_available = floor_limit - floor_earned
                        daily_available = DAILY_MAX_EYEBALLS - current_daily
                        reward = min(raw_reward, floor_available, daily_available)

                        if reward > 0:
                            status = "REWARD"
                            data[user_key]["chat_count"] = data[user_key]["chat_count"] % 50  # 성공 시 카운트 리셋
                            data[user_key]["daily_earned"] = current_daily + reward
                            data[user_key]["eyeballs"] = data[user_key].get("eyeballs", 0) + reward
                            data[user_key]["floor_earned"][floor_name] = floor_earned + reward
                        else:
                            status = "CAP_REACHED"
                            # 한도 초과로 리워드가 0이면 카운트를 리셋하지 않고 50으로 유지하여 다음 채팅 때 다시 시도하게 함
                    else:
                        status = "CAP_REACHED"
                else:
                    # 층 채널이 아닌 곳에서 50회를 채운 경우 카운트만 리셋
                    data[user_key]["chat_count"] = data[user_key]["chat_count"] % 50
            else:
                # 50회가 안 되었을 때의 기본 상태
                status = "COUNT_INCREMENTED"

            save_data(data)
            return (
                status,
                reward,
                data[user_key]["eyeballs"],
                data[user_key].get("daily_earned", 0),
                floor_name,
            )

# --------------------------------------------------
# [ 유저간 거래: 모달 및 동적 버튼 (DynamicItem) ]
# --------------------------------------------------
class MarketBuyQuantityModal(discord.ui.Modal):
    """유저간 거래 구매 수량 입력 모달"""

    def __init__(self, listing_id: str, item_name: str, price: int, max_qty: int):
        super().__init__(title=f"'{item_name}' 구매")
        self.listing_id = listing_id
        self.item_name = item_name
        self.price = price
        self.max_qty = max_qty

        self.quantity = discord.ui.TextInput(
            label=f"구매 수량 (개당 {price:,} 눈알 / 남은 수량: {max_qty}개)",
            placeholder=f"1 ~ {max_qty} 사이의 숫자를 입력하세요",
            default="1",
            min_length=1,
            max_length=5,
            required=True,
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qty = int(self.quantity.value.strip())
            if qty <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "[오류] 올바른 수량(1 이상의 숫자)을 입력해 주세요.", ephemeral=True
            )
            return

        market_data = load_market_data()
        listing = market_data.get(self.listing_id)

        if not listing or listing.get("status") != "active":
            await interaction.response.send_message(
                "[오류] 이미 판매가 완료되었거나 취소된 거래입니다.", ephemeral=True
            )
            return

        if interaction.user.id == listing["seller_id"]:
            await interaction.response.send_message(
                "[오류] 본인이 등록한 판매 물품은 구매할 수 없습니다.", ephemeral=True
            )
            return

        remaining_stock = listing["quantity"]
        if qty > remaining_stock:
            await interaction.response.send_message(
                f"[오류] 구매 요청 수량이 남은 재고보다 많습니다. (남은 수량: `{remaining_stock}`개)",
                ephemeral=True,
            )
            return

        total_price = self.price * qty
        buyer_is_infinite = is_infinite_money(interaction.user, interaction.guild)

        if not buyer_is_infinite:
            buyer_data = get_user_data(interaction.user.id)
            buyer_eyeballs = buyer_data.get("eyeballs", 0)

            if buyer_eyeballs < total_price:
                await interaction.response.send_message(
                    f"[오류] 눈알이 부족합니다.\n"
                    f"• 필요 눈알: `{total_price:,}`개 (개당 `{self.price:,}`개 x {qty}개)\n"
                    f"• 현재 소지: `{buyer_eyeballs:,}`개",
                    ephemeral=True,
                )
                return

            # 1. 구매자 눈알 차감
            rem_buyer_eyeballs, new_grade, is_pending = await add_eyeballs(
                interaction.user.id, -total_price, guild=interaction.guild, member=interaction.user
            )
            rem_str = f"`{rem_buyer_eyeballs:,}`개"
            pay_str = f"`{total_price:,}`개"
        else:
            rem_str = "무한 (∞)"
            pay_str = f"`{total_price:,}`개 (거인 직업 무한 재화 적용)"
            new_grade = None
            is_pending = False

        buyer_new_item_cnt = add_item_to_inventory(interaction.user.id, self.item_name, qty)

        # 구매자 자동 등급업 처리
        grade_msg = ""
        if new_grade:
            if is_pending:
                grade_msg = f"\n👑 **[승급 승인 요청]** **{new_grade}** 등급 달성 조건을 충족했습니다!\n*(루비 이상 등급은 관리자 채팅방에 승급 승인 요청이 전송되며, 관리자 수락 후 승급됩니다)*"
            else:
                if isinstance(interaction.user, discord.Member):
                    await apply_grade_upgrade(interaction.guild, interaction.user, new_grade)
                grade_msg = f"\n🎉 **[등급업 축하]** 눈알 누적 소비로 **{new_grade}** 등급으로 자동 승급되었습니다!"

        # 2. 판매자에게 눈알 지급
        seller_id = listing["seller_id"]
        seller_new_eyeballs, _, _ = await add_eyeballs(seller_id, total_price)

        # 3. 매물 재고 차감 및 상태 갱신
        listing["quantity"] -= qty
        is_sold_out = listing["quantity"] <= 0
        if is_sold_out:
            listing["status"] = "sold_out"

        save_market_data(market_data)

        # 4. 구매자에게 완료 메시지 (서버 내에서 구매자 본인만 확인)
        await interaction.response.send_message(
            f"**[구매 완료]**\n"
            f"• 아이템: **{self.item_name}** `{qty}`개 (현재 소지: `{buyer_new_item_cnt}`개)\n"
            f"• 판매자: <@{seller_id}>\n"
            f"• 지불 눈알: {pay_str}\n"
            f"• 남은 눈알: {rem_str}"
            f"{grade_msg}\n"
            f"*(소지품은 `/인벤토리`에서 확인하실 수 있습니다)*",
            ephemeral=True,
        )

        # 5. 거래소 구매 로그 전송
        await send_log(
            interaction.guild,
            f"[거래소 매물 구매]\n"
            f"• 구매자: {interaction.user.display_name}\n"
            f"• 판매자: {seller_id}\n"
            f"• 아이템: {self.item_name} {qty}개\n"
            f"• 지불 눈알: {pay_str}\n"
            f"• 채널: #{getattr(interaction.channel, 'name', '알 수 없음')}\n"
            f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
        )

        # 6. 포럼 판매 카드 메시지 UI 갱신
        try:
            msg = interaction.message
            if msg:
                if is_sold_out:
                    embed = msg.embeds[0]
                    embed.title = f"[판매 완료] {self.item_name}"
                    embed.color = discord.Color.dark_grey()
                    embed.set_field_at(
                        1, name="남은 수량", value="**품절 (판매 완료)**", inline=True
                    )
                    # 버튼 비활성화
                    disabled_view = discord.ui.View()
                    disabled_btn = discord.ui.Button(
                        label="판매 완료", style=discord.ButtonStyle.secondary, disabled=True
                    )
                    disabled_view.add_item(disabled_btn)
                    await msg.edit(embed=embed, view=disabled_view)
                else:
                    embed = msg.embeds[0]
                    embed.set_field_at(
                        1, name="남은 수량", value=f"**{listing['quantity']}개**", inline=True
                    )
                    await msg.edit(embed=embed)
        except Exception:
            pass


class MarketBuyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"m_buy:(?P<listing_id>[a-zA-Z0-9]+)",
):
    """유저간 거래 매물 구매 버튼"""

    def __init__(self, listing_id: str):
        super().__init__(
            discord.ui.Button(
                label="구매하기",
                style=discord.ButtonStyle.success,
                custom_id=f"m_buy:{listing_id}",
            )
        )
        self.listing_id = listing_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        return cls(match.group("listing_id"))

    async def callback(self, interaction: discord.Interaction):
        market_data = load_market_data()
        listing = market_data.get(self.listing_id)

        if not listing or listing.get("status") != "active":
            await interaction.response.send_message(
                "[오류] 이미 판매 완료되었거나 취소된 거래입니다.", ephemeral=True
            )
            return

        if interaction.user.id == listing["seller_id"]:
            await interaction.response.send_message(
                "[오류] 본인이 등록한 판매 물품은 직접 구매할 수 없습니다.", ephemeral=True
            )
            return

        modal = MarketBuyQuantityModal(
            listing_id=self.listing_id,
            item_name=listing["item_name"],
            price=listing["price"],
            max_qty=listing["quantity"],
        )
        await interaction.response.send_modal(modal)


class MarketCancelButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"m_cnc:(?P<listing_id>[a-zA-Z0-9]+)",
):
    """유저간 거래 매물 판매 취소 버튼 (판매자 또는 관리자 전용)"""

    def __init__(self, listing_id: str):
        super().__init__(
            discord.ui.Button(
                label="판매 취소",
                style=discord.ButtonStyle.danger,
                custom_id=f"m_cnc:{listing_id}",
            )
        )
        self.listing_id = listing_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        return cls(match.group("listing_id"))

    async def callback(self, interaction: discord.Interaction):
        market_data = load_market_data()
        listing = market_data.get(self.listing_id)

        if not listing or listing.get("status") != "active":
            await interaction.response.send_message(
                "[오류] 이미 취소되었거나 마감된 거래입니다.", ephemeral=True
            )
            return

        is_seller = interaction.user.id == listing["seller_id"]
        is_admin = is_admin_user(interaction.user, interaction.guild)

        if not (is_seller or is_admin):
            await interaction.response.send_message(
                "[오류] 판매 등록자 본인 또는 관리자(거인 직업)만 판매를 취소할 수 있습니다.", ephemeral=True
            )
            return

        remaining_qty = listing["quantity"]
        seller_id = listing["seller_id"]
        item_name = listing["item_name"]

        new_inv_cnt = add_item_to_inventory(seller_id, item_name, remaining_qty)
        listing["status"] = "cancelled"
        save_market_data(market_data)

        # 로그 전송
        await send_log(
            interaction.guild,
            f"[거래소 판매 취소]\n"
            f"• 취소 실행자: {interaction.user.display_name}\n"
            f"• 판매 등록자: {seller_id}\n"
            f"• 회수 아이템: {item_name} {remaining_qty}개\n"
            f"• 채널: #{getattr(interaction.channel, 'name', '알 수 없음')}\n"
            f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
        )

        try:
            msg = interaction.message
            if msg:
                embed = msg.embeds[0]
                embed.title = f"[판매 취소됨] {item_name}"
                embed.color = discord.Color.red()
                embed.set_field_at(
                    1, name="남은 수량", value="**판매 취소 (회수 완료)**", inline=True
                )
                disabled_view = discord.ui.View()
                disabled_btn = discord.ui.Button(
                    label="판매 취소됨", style=discord.ButtonStyle.secondary, disabled=True
                )
                disabled_view.add_item(disabled_btn)
                await msg.edit(embed=embed, view=disabled_view)
        except Exception:
            pass

        await interaction.response.send_message(
            f"[판매 취소 완료]\n"
            f"• 남은 **{item_name}** `{remaining_qty}`개가 인벤토리로 반환되었습니다. (현재 소지: `{new_inv_cnt}`개)",
            ephemeral=True,
        )


# --------------------------------------------------
# [ 유저간 구매 희망: 모달 및 동적 버튼 ]
# --------------------------------------------------
class MarketFulfillQuantityModal(discord.ui.Modal):
    """구매 희망 글에 아이템을 판매(전달)하는 수량 입력 모달"""

    def __init__(self, listing_id: str, item_name: str, price: int, max_qty: int):
        super().__init__(title=f"'{item_name}' 판매하기")
        self.listing_id = listing_id
        self.item_name = item_name
        self.price = price
        self.max_qty = max_qty

        self.quantity = discord.ui.TextInput(
            label=f"판매 수량 (개당 {price:,} 눈알 / 최대: {max_qty}개)",
            placeholder=f"1 ~ {max_qty} 사이의 숫자를 입력하세요",
            default="1",
            min_length=1,
            max_length=5,
            required=True,
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qty = int(self.quantity.value.strip())
            if qty <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "[오류] 올바른 수량(1 이상의 숫자)을 입력해 주세요.", ephemeral=True
            )
            return

        market_data = load_market_data()
        listing = market_data.get(self.listing_id)

        if not listing or listing.get("status") != "active":
            await interaction.response.send_message(
                "[오류] 이미 완료되었거나 취소된 구매 요청입니다.", ephemeral=True
            )
            return

        buyer_id = listing["buyer_id"]
        if interaction.user.id == buyer_id:
            await interaction.response.send_message(
                "[오류] 본인이 등록한 구매 요청 글에는 직접 판매할 수 없습니다.", ephemeral=True
            )
            return

        # 판매자 소지 아이템 확인
        seller_data = get_user_data(interaction.user.id)
        seller_inv = seller_data.get("inventory", {})
        owned_cnt = seller_inv.get(self.item_name, 0)

        if owned_cnt < qty:
            await interaction.response.send_message(
                f"[오류] 소지한 아이템이 부족합니다. (현재 소지: `{owned_cnt}`개 / 입력 수량: `{qty}`개)",
                ephemeral=True,
            )
            return

        needed_stock = listing["quantity"]
        if qty > needed_stock:
            await interaction.response.send_message(
                f"[오류] 구매자가 요청한 남은 수량보다 많습니다. (요청 잔여량: `{needed_stock}`개)",
                ephemeral=True,
            )
            return

        # 1. 판매자 인벤토리에서 아이템 차감
        rem_seller_inv = add_item_to_inventory(interaction.user.id, self.item_name, -qty)

        # 2. 구매자 인벤토리에 아이템 지급
        buyer_new_inv = add_item_to_inventory(buyer_id, self.item_name, qty)

        # 3. 판매자에게 눈알 지급 (구매자가 예치해둔 눈알)
        total_payout = self.price * qty
        seller_new_eyeballs, _, _ = await add_eyeballs(interaction.user.id, total_payout)

        # 4. 구매글 잔여 수량 갱신
        listing["quantity"] -= qty
        is_completed = listing["quantity"] <= 0
        if is_completed:
            listing["status"] = "completed"

        save_market_data(market_data)

        # 5. 판매자에게 성공 안내 (서버 내에서 판매자 본인만 확인)
        seller_infinite = is_infinite_money(interaction.user, interaction.guild)
        seller_eye_str = "무한 (∞)" if seller_infinite else f"`{seller_new_eyeballs:,}`개"
        await interaction.response.send_message(
            f"**[판매 완료]**\n"
            f"• 넘긴 아이템: **{self.item_name}** `{qty}`개 (남은 소지: `{rem_seller_inv}`개)\n"
            f"• 수령 눈알: `+{total_payout:,}`개 (현재 소지: {seller_eye_str})\n"
            f"• 구매자: <@{buyer_id}>",
            ephemeral=True,
        )

        # 6. 거래소 판매 로그 전송
        await send_log(
            interaction.guild,
            f"[거래소 구매요청 응답(판매)]\n"
            f"• 판매자: {interaction.user.display_name}\n"
            f"• 구매자: {buyer_id}\n"
            f"• 넘긴 아이템: {self.item_name} {qty}개\n"
            f"• 수령 눈알: {total_payout:,}개\n"
            f"• 채널: #{getattr(interaction.channel, 'name', '알 수 없음')}\n"
            f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
        )

        # 7. 포럼 카드 UI 갱신
        try:
            msg = interaction.message
            if msg:
                embed = msg.embeds[0]
                if is_completed:
                    embed.title = f"[구매 완료] {self.item_name}"
                    embed.color = discord.Color.dark_grey()
                    embed.set_field_at(
                        1, name="필요 수량", value="**구매 완료 (마감)**", inline=True
                    )
                    disabled_view = discord.ui.View()
                    disabled_btn = discord.ui.Button(
                        label="구매 완료됨", style=discord.ButtonStyle.secondary, disabled=True
                    )
                    disabled_view.add_item(disabled_btn)
                    await msg.edit(embed=embed, view=disabled_view)
                else:
                    embed.set_field_at(
                        1, name="필요 수량", value=f"**{listing['quantity']}개**", inline=True
                    )
                    await msg.edit(embed=embed)
        except Exception:
            pass


class MarketFulfillButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"m_ful:(?P<listing_id>[a-zA-Z0-9]+)",
):
    """구매 희망 글에 아이템을 판매(넘기기)하는 버튼"""

    def __init__(self, listing_id: str):
        super().__init__(
            discord.ui.Button(
                label="판매하기 (아이템 넘기기)",
                style=discord.ButtonStyle.primary,
                custom_id=f"m_ful:{listing_id}",
            )
        )
        self.listing_id = listing_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        return cls(match.group("listing_id"))

    async def callback(self, interaction: discord.Interaction):
        market_data = load_market_data()
        listing = market_data.get(self.listing_id)

        if not listing or listing.get("status") != "active":
            await interaction.response.send_message(
                "[오류] 이미 완료되었거나 취소된 구매 요청입니다.", ephemeral=True
            )
            return

        if interaction.user.id == listing["buyer_id"]:
            await interaction.response.send_message(
                "[오류] 본인이 등록한 구매 요청에는 직접 판매할 수 없습니다.", ephemeral=True
            )
            return

        # 소지 아이템 검사
        user_data = get_user_data(interaction.user.id)
        inv = user_data.get("inventory", {})
        owned_cnt = inv.get(listing["item_name"], 0)

        if owned_cnt <= 0:
            await interaction.response.send_message(
                f"[오류] 현재 **{listing['item_name']}** 아이템을 소지하고 있지 않습니다.",
                ephemeral=True,
            )
            return

        max_can_sell = min(listing["quantity"], owned_cnt)
        modal = MarketFulfillQuantityModal(
            listing_id=self.listing_id,
            item_name=listing["item_name"],
            price=listing["price"],
            max_qty=max_can_sell,
        )
        await interaction.response.send_modal(modal)


class MarketBuyCancelButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"m_bcnc:(?P<listing_id>[a-zA-Z0-9]+)",
):
    """구매 희망 글 취소 및 예치 눈알 환불 버튼 (구매자 또는 관리자 전용)"""

    def __init__(self, listing_id: str):
        super().__init__(
            discord.ui.Button(
                label="구매 취소",
                style=discord.ButtonStyle.danger,
                custom_id=f"m_bcnc:{listing_id}",
            )
        )
        self.listing_id = listing_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        return cls(match.group("listing_id"))

    async def callback(self, interaction: discord.Interaction):
        market_data = load_market_data()
        listing = market_data.get(self.listing_id)

        if not listing or listing.get("status") != "active":
            await interaction.response.send_message(
                "[오류] 이미 취소되었거나 마감된 구매 요청입니다.", ephemeral=True
            )
            return

        is_buyer = interaction.user.id == listing["buyer_id"]
        is_admin = is_admin_user(interaction.user, interaction.guild)

        if not (is_buyer or is_admin):
            await interaction.response.send_message(
                "[오류] 구매 등록자 본인 또는 관리자(거인 직업)만 취소할 수 있습니다.", ephemeral=True
            )
            return

        remaining_qty = listing["quantity"]
        refund_amount = remaining_qty * listing["price"]
        buyer_id = listing["buyer_id"]
        item_name = listing["item_name"]
        buyer_infinite = listing.get("buyer_infinite", False)

        if not buyer_infinite:
            # 예치 눈알 환불
            new_eyeballs, _, _ = await add_eyeballs(buyer_id, refund_amount)
            refund_msg = f"• 예치되어 있던 눈알 `+{refund_amount:,}`개가 환불되었습니다. (현재 보유: `{new_eyeballs:,}`개)"
        else:
            refund_msg = "• 거인 직업(무한 재화) 등록 요청이었으므로 별도의 환불 처리 없이 취소되었습니다."

        listing["status"] = "cancelled"
        save_market_data(market_data)

        # 로그 전송
        await send_log(
            interaction.guild,
            f"[거래소 구매요청 취소]\n"
            f"• 취소 실행자: {interaction.user.display_name}\n"
            f"• 구매 등록자: {buyer_id}\n"
            f"• 취소 아이템: {item_name} {remaining_qty}개\n"
            f"• 채널: #{getattr(interaction.channel, 'name', '알 수 없음')}\n"
            f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
        )

        try:
            msg = interaction.message
            if msg:
                embed = msg.embeds[0]
                embed.title = f"[구매 취소됨] {item_name}"
                embed.color = discord.Color.red()
                embed.set_field_at(
                    1, name="필요 수량", value="**구매 취소 (마감)**", inline=True
                )
                disabled_view = discord.ui.View()
                disabled_btn = discord.ui.Button(
                    label="구매 취소됨", style=discord.ButtonStyle.secondary, disabled=True
                )
                disabled_view.add_item(disabled_btn)
                await msg.edit(embed=embed, view=disabled_view)
        except Exception:
            pass

        await interaction.response.send_message(
            f"[구매 취소 완료]\n{refund_msg}",
            ephemeral=True,
        )


# --------------------------------------------------
# [ 등급업 승인 버튼 ]
# --------------------------------------------------


class PromotionApprovalView(discord.ui.View):
    def __init__(self, request_id: str = "placeholder", user_id: int = 0, target_grade: str = "다이아몬드"):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.user_id = user_id
        self.target_grade = target_grade

    def _resolve_request(self, interaction: discord.Interaction):
        promotion_requests = load_promotion_requests()
        # 1. Direct ID match
        if self.request_id != "placeholder" and self.request_id in promotion_requests:
            return self.request_id, promotion_requests[self.request_id]

        # 2. Extract from embed footer
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            if embed.footer and embed.footer.text:
                for line in embed.footer.text.split("\n"):
                    if "요청 ID:" in line:
                        req_id = line.split("요청 ID:")[-1].strip()
                        if req_id in promotion_requests:
                            return req_id, promotion_requests[req_id]

        # 3. Fallback: match by pending status
        for req_id, req in promotion_requests.items():
            if req.get("status") == "pending":
                if str(req.get("user_id")) in str(interaction.message.content) or (
                    interaction.message.embeds
                    and str(req.get("user_id")) in str(interaction.message.embeds[0].to_dict())
                ):
                    return req_id, req

        return None, None

    @discord.ui.button(label="승급", style=discord.ButtonStyle.success, custom_id="promotion_approve")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_user(interaction.user, interaction.guild):
            await interaction.response.send_message("[오류] 관리자만 승급할 수 있습니다.", ephemeral=True)
            return

        request_id, request = self._resolve_request(interaction)
        if not request_id or not request or request.get("status") != "pending":
            await interaction.response.send_message("[오류] 이미 처리되었거나 찾을 수 없는 요청입니다.", ephemeral=True)
            return

        user_id = request["user_id"]
        target_grade = request.get("target_grade", "다이아몬드")

        # 등급업 처리
        data = load_data()
        user_key = str(user_id)
        if user_key not in data:
            await interaction.response.send_message("[오류] 유저 데이터를 찾을 수 없습니다.", ephemeral=True)
            return

        data[user_key]["grade"] = target_grade
        save_data(data)

        # 역할 변경
        user = interaction.guild.get_member(user_id)
        if user:
            await apply_grade_upgrade(interaction.guild, user, target_grade)

        promotion_requests = load_promotion_requests()
        if request_id in promotion_requests:
            promotion_requests[request_id]["status"] = "approved"
            save_promotion_requests(promotion_requests)

        button.disabled = True
        self.children[1].disabled = True
        await interaction.response.edit_message(
            content=f"[승급 완료] {request['user_name']}님의 **{target_grade}** 등급업이 승인되었습니다.",
            view=self,
        )

        if user:
            try:
                await user.send(f"🎉 축하합니다! 관리자가 **{target_grade}** 등급업을 승인하였습니다. 이제 다이아몬드 상점을 이용하실 수 있습니다!")
            except Exception:
                pass

    @discord.ui.button(label="유지", style=discord.ButtonStyle.danger, custom_id="promotion_reject")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_user(interaction.user, interaction.guild):
            await interaction.response.send_message("[오류] 관리자만 처리할 수 있습니다.", ephemeral=True)
            return

        request_id, request = self._resolve_request(interaction)
        if not request_id or not request or request.get("status") != "pending":
            await interaction.response.send_message("[오류] 이미 처리되었거나 찾을 수 없는 요청입니다.", ephemeral=True)
            return

        user_id = request["user_id"]
        target_grade = request.get("target_grade", "다이아몬드")

        promotion_requests = load_promotion_requests()
        if request_id in promotion_requests:
            promotion_requests[request_id]["status"] = "rejected"
            save_promotion_requests(promotion_requests)

        button.disabled = True
        self.children[0].disabled = True
        await interaction.response.edit_message(
            content=f"[유지 완료] {request['user_name']}님의 {target_grade} 등급업 요청이 거절되어 현재 등급이 유지되었습니다.",
            view=self,
        )

        user = interaction.guild.get_member(user_id)
        if user:
            try:
                await user.send(f"안내: 관리자가 **{target_grade}** 등급업 요청을 거절하여 현재 등급이 유지되었습니다.")
            except Exception:
                pass



# --------------------------------------------------
# [ 봇 이벤트 ]
# --------------------------------------------------
@bot.event
async def on_ready():
    load_shop_data()

    # 영구 뷰 등록 (다이아몬드 승급 승인 버튼 + 각 등급별 상점 뷰)
    bot.add_view(PromotionApprovalView("placeholder", 0, "다이아몬드"))
    for tier_key in TIER_SHOPS.keys():
        bot.add_view(ShopView(tier_key))

    # 영구 동적 버튼 등록 (판매글용 + 구매글용)
    try:
        bot.add_dynamic_items(MarketBuyButton)
        bot.add_dynamic_items(MarketCancelButton)
        bot.add_dynamic_items(MarketFulfillButton)
        bot.add_dynamic_items(MarketBuyCancelButton)
    except Exception as e:
        print(f"동적 버튼 등록: {e}")

    try:
        for guild in bot.guilds:
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            try:
                await get_or_create_log_channel(guild)
            except Exception as e:
                print(f"로그 채널 준비 실패 ({guild.name}): {e}")

        synced = await bot.tree.sync()
        print(f"[동기화 완료] 슬래시 커맨드: {len(synced)}개 등록")
    except Exception as e:
        print(f"슬래시 커맨드 동기화 실패: {e}")

    global _startup_logged
    if not _startup_logged:
        for guild in bot.guilds:
            log_ch = await get_or_create_log_channel(guild)
            await send_log(
                guild,
                f"[시스템] 봇이 시작되었습니다. 이후 명령어 사용 기록이 이 채널에 남습니다.\n"
                f"• 봇: {bot.user} ({bot.user.id})\n"
                f"• 로그 채널: #{getattr(log_ch, 'name', '없음')} ({getattr(log_ch, 'id', '없음')})\n"
                f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
            )
        _startup_logged = True

    print(f"로그인 완료: {bot.user.name} (ID: {bot.user.id})")
    print("시스템이 정상적으로 작동 중입니다.")
    print("--------------------------------------------------")


# 관리자용 중복 정리 및 동기화 명령어 (!동기화)
@bot.command(name="동기화")
@admin_only()
async def 동기화(ctx: commands.Context):
    msg = await ctx.send("중복 명령어를 정리하고 동기화하는 중입니다...", ephemeral=True)
    try:
        # 현재 서버의 중복 길드 커맨드 제거
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)

        # 전역 커맨드 1벌만 동기화
        synced = await bot.tree.sync()
        await msg.edit(
            content=f"[동기화 완료] 총 {len(synced)}개의 명령어가 적용되었습니다.\n"
            f"디스코드에서 Ctrl + R (새로고침)을 누르시면 반영됩니다."
        )
    except Exception as e:
        await msg.edit(content=f"[오류] 동기화 실패: {e}")


@bot.event
async def on_member_join(member: discord.Member):
    """신규 유저 입장 시 기본 역할 자동 지급"""
    guild = member.guild
    data = load_default_roles()
    rid = data.get(str(guild.id))
    if rid:
        role = guild.get_role(rid)
        if role:
            try:
                await member.add_roles(role, reason="기본 역할 자동 지급")
                await send_log(
                    guild,
                    f"[기본 역할 자동 지급]\n"
                    f"• 대상 유저: {member.display_name}\n"
                    f"• 지급 역할: {role.name}\n"
                    f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
                )
            except Exception as e:
                print(f"기본 역할 지급 실패 ({member.name}): {e}")


def _mark_logged(entry_id: int) -> bool:
    """이미 같은 상호작용을 기록했으면 True"""
    if not entry_id:
        return False
    if entry_id in _logged_interaction_ids:
        return True
    _logged_interaction_ids.add(entry_id)
    if len(_logged_interaction_ids) > 2000:
        _logged_interaction_ids.clear()
    return False


def _format_slash_options(options) -> str:
    if not options:
        return ""
    parts = []
    for opt in options:
        nested = opt.get("options")
        if nested:
            nested_text = _format_slash_options(nested)
            parts.append(f"{opt.get('name')}: {nested_text}" if nested_text else str(opt.get("name")))
        else:
            parts.append(f"{opt.get('name')}: {opt.get('value', '')}")
    return ", ".join(parts)


def _format_ctx_params(ctx: commands.Context) -> str:
    params = []
    for k, v in (ctx.kwargs or {}).items():
        if isinstance(v, (discord.Member, discord.User, discord.Role, discord.abc.GuildChannel)):
            params.append(f"{k}: {getattr(v, 'name', v)} ({v.id})")
        else:
            params.append(f"{k}: {v}")
    if params:
        return ", ".join(params)
    if ctx.interaction and ctx.interaction.data:
        slash_params = _format_slash_options(ctx.interaction.data.get("options"))
        if slash_params:
            return slash_params
    return "없음"


def _resolve_guild(guild, guild_id=None):
    if guild:
        return guild
    if guild_id:
        return bot.get_guild(guild_id)
    return None


async def log_command_usage(guild, user, command_name: str, param_text: str, channel, entry_id: int = None):
    guild = _resolve_guild(guild, getattr(guild, "id", None))
    if not guild:
        print(f"명령어 로그 중단: 서버 정보를 찾지 못함 (/{command_name})")
        return
    if _mark_logged(entry_id):
        return
    try:
        now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        if channel is not None:
            channel_text = f"#{getattr(channel, 'name', None) or '알 수 없음'}"
        else:
            channel_text = "알 수 없음"
        log_msg = (
            f"[명령어 사용 로그]\n"
            f"• 실행자: {user.display_name}\n"
            f"• 명령어: /{command_name}\n"
            f"• 입력값: {param_text}\n"
            f"• 실행 채널: {channel_text}\n"
            f"• 일시: {now_str}"
        )
        await send_log(guild, log_msg)
    except Exception as e:
        print(f"명령어 로그 전송 실패: {e}")


async def log_slash_interaction(interaction: discord.Interaction):
    if interaction.type is not discord.InteractionType.application_command:
        return
    guild = _resolve_guild(interaction.guild, interaction.guild_id)
    command_name = "알 수 없음"
    param_text = "없음"
    if interaction.command:
        command_name = interaction.command.qualified_name
    elif interaction.data:
        command_name = interaction.data.get("name", command_name)
    if interaction.data:
        parsed = _format_slash_options(interaction.data.get("options"))
        if parsed:
            param_text = parsed
    await log_command_usage(
        guild,
        interaction.user,
        command_name,
        param_text,
        interaction.channel,
        entry_id=interaction.id,
    )


@bot.event
async def on_message(message):
    # 봇의 메시지는 무시
    if message.author.bot:
        return

    ctx = await bot.get_context(message)
    # 접두사 명령어는 채팅 횟수에 넣지 않고 명령만 실행
    if ctx.valid:
        await bot.invoke(ctx)
        return

    await bot.process_commands(message)

    # 채팅 카운트 증가 및 보상 처리
    status, reward, total_eyeballs, daily_earned, floor_name = await increment_chat_count(
        message.author.id, message.channel.id
    )

    if status == "REWARD":
        await message.channel.send(
            f"{message.author.mention}님, {floor_name}에서 채팅 50회 달성 보상으로 눈알 {reward}개를 획득하셨습니다. (보유: {total_eyeballs}개)",
            delete_after=5,
        )

    elif status == "CAP_REACHED":
        await message.channel.send(
            f"{message.author.mention}님, 오늘은 이미 하루 최대 획득량({DAILY_MAX_EYEBALLS}개)을 모두 달성하셨습니다.",
            delete_after=5,
        )


@bot.event
async def on_interaction(interaction: discord.Interaction):
    # 아무 작업도 하지 않음
    pass


@bot.event
async def on_command_completion(ctx: commands.Context):
    """접두사 명령어만 로그 기록"""
    if not ctx.command:
        return
    # 슬래시 명령어는 on_app_command_completion에서 기록
    if ctx.interaction:
        return
    entry_id = ctx.message.id if ctx.message else None
    await log_command_usage(
        ctx.guild,
        ctx.author,
        ctx.command.qualified_name,
        _format_ctx_params(ctx),
        ctx.channel,
        entry_id=entry_id,
    )


@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: discord.app_commands.Command):
    """슬래시 명령어만 로그 기록"""
    entry_id = interaction.id
    if _mark_logged(entry_id):
        return
    await log_slash_interaction(interaction)


# --------------------------------------------------
# [ 자동완성 함수: 인벤토리 아이템 & 상점 아이템 ]
# --------------------------------------------------
async def user_inventory_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[discord.app_commands.Choice[str]]:
    """유저가 가진 인벤토리 아이템 이름 자동완성"""
    user_data = get_user_data(interaction.user.id)
    inv = user_data.get("inventory", {})
    choices = []
    for name, cnt in inv.items():
        if cnt > 0 and (current.lower() in name.lower() or not current):
            choices.append(
                discord.app_commands.Choice(name=f"{name} ({cnt}개 소지)", value=name)
            )
    return choices[:25]


async def shop_item_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[discord.app_commands.Choice[str]]:
    """상점에 등록된 아이템 이름 자동완성"""
    choices = []
    for tier in TIER_SHOPS.values():
        for name, info in tier["items"].items():
            if current.lower() in name.lower() or not current:
                price = info.get("price", 0)
                if not any(c.value == name for c in choices):
                    choices.append(
                        discord.app_commands.Choice(name=f"{name} ({price:,} 눈알)", value=name)
                    )
    return choices[:25]


# --------------------------------------------------
# [ 명령어: 일반 상점 (NPC 상점) ]
# --------------------------------------------------


def create_shop_embed(tier_key: str, user: discord.User, user_data: dict) -> discord.Embed:
    """해당 등급 상점의 상세 안내 임베드 생성 (본인 등급 전용)"""
    tier = TIER_SHOPS.get(tier_key, TIER_SHOPS["이방인 상점 (일반 상점)"])
    grade = user_data.get("grade", "이방인")
    eyeballs = user_data.get("eyeballs", 0)
    spent = user_data.get("eyeballs_spent", 0)

    embed = discord.Embed(
        title=f"🏬 {tier['tier_name']}",
        description=tier["description"],
        color=discord.Color.gold(),
    )
    if user:
        embed.set_author(name=f"{user.display_name}님의 상점", icon_url=user.display_avatar.url)
    embed.add_field(name="내 등급", value=f"**{grade}**", inline=True)
    embed.add_field(name="소지 눈알", value=f"`{eyeballs:,}`개", inline=True)

    # 다음 등급 소비 진행도
    curr_idx = GRADE_ORDER.index(grade) if grade in GRADE_ORDER else 0
    if curr_idx < len(GRADE_ORDER) - 1:
        next_grade = GRADE_ORDER[curr_idx + 1]
        req = GRADE_REQUIREMENTS[next_grade]
        rem_req = max(0, req - spent)
        progress_text = f"**{next_grade}** ({spent:,} / {req:,}개 사용 - `{rem_req:,}`개 남음)"
    else:
        progress_text = "👑 최고 등급 달성"
    embed.add_field(name="다음 등급 진행도", value=progress_text, inline=False)

    item_lines = []
    for item_name, info in tier["items"].items():
        price = info.get("price", 0)
        price_str = f"`{price:,}` 눈알" if price > 0 else "`무료 (0 눈알)`"
        desc = info.get("desc", "")
        note = f" ({desc})" if desc else ""
        item_lines.append(f"• **{item_name}**: {price_str}{note}")

    if item_lines:
        embed.add_field(name="📦 판매 물품 목록", value="\n".join(item_lines), inline=False)

    embed.set_footer(text="아래 버튼을 눌러 아이템을 구매하세요. 눈알을 사용하면 자동으로 누적되어 등급이 상승합니다.")
    return embed


class ShopItemButton(discord.ui.Button):
    def __init__(self, item_name: str, price: int, row: int = 0):
        price_label = f"{price:,}눈알" if price > 0 else "0눈알"
        super().__init__(
            label=f"{item_name} ({price_label})",
            style=discord.ButtonStyle.primary,
            custom_id=f"shop_buy_{item_name}",
            row=row,
        )
        self.item_name = item_name
        self.price = price

    async def callback(self, interaction: discord.Interaction):
        tier_key, tier, item_info = find_item_info(self.item_name)
        if not item_info:
            await interaction.response.send_message("[오류] 상점 아이템 정보를 찾을 수 없습니다.", ephemeral=True)
            return

        buyer = interaction.user
        buyer_is_infinite = is_infinite_money(buyer, interaction.guild)
        user_data = get_user_data(buyer.id)
        current_grade = user_data.get("grade", "이방인")
        current_eyeballs = user_data.get("eyeballs", 0)

        # 등급 상점 자격 확인 (본인 등급 상점 물품만 구매 가능)
        tier_grades = tier.get("grades", [])
        if current_grade not in tier_grades:
            await interaction.response.send_message(
                f"[구매 불가] 이 상점은 **{tier['tier_name']}**입니다. 본인 등급(**{current_grade}**)에 맞는 상점을 열려면 `/상점` 명령어를 입력해 주세요!",
                ephemeral=True,
            )
            return

        # 눈알 잔액 확인
        if not buyer_is_infinite and current_eyeballs < self.price:
            await interaction.response.send_message(
                f"[오류] 눈알이 부족합니다.\n"
                f"• 필요 눈알: `{self.price:,}`개\n"
                f"• 현재 소지: `{current_eyeballs:,}`개",
                ephemeral=True,
            )
            return

        # 눈알 차감 및 자동 등급업 처리
        if not buyer_is_infinite:
            rem_eyeballs, new_grade, is_pending = await add_eyeballs(
                buyer.id, -self.price, guild=interaction.guild, member=interaction.user
            )
            rem_str = f"`{rem_eyeballs:,}`개"
        else:
            rem_eyeballs, new_grade, is_pending = await add_eyeballs(
                buyer.id, 0, guild=interaction.guild, member=interaction.user
            )
            rem_str = "무한 (∞)"

        new_item_cnt = add_item_to_inventory(buyer.id, self.item_name, 1)

        # 등급업 및 진행 안내 메시지
        grade_msg = ""
        if new_grade:
            if is_pending:
                grade_msg = f"\n\n👑 **[다이아몬드 승급 승인 요청]**\n눈알 소비 요구치를 달성하여 관리자 채팅방에 승급 승인 요청이 전송되었습니다!\n관리자 수락 시 다이아몬드로 승급됩니다."
            else:
                old_shop = get_tier_key_for_grade(current_grade)
                new_shop = get_tier_key_for_grade(new_grade)
                shop_note = f"\n🏬 상점이 **{new_shop}**으로 업그레이드되었습니다! `/상점`을 다시 입력하여 확인해 보세요." if old_shop != new_shop else ""
                grade_msg = f"\n\n🎉 **[등급업 축하]** 눈알 누적 소비로 **{new_grade}** 등급으로 자동 승급되었습니다!{shop_note}"
        else:
            data = load_data()
            user_key = str(buyer.id)
            spent = data[user_key].get("eyeballs_spent", 0)
            curr_idx = GRADE_ORDER.index(current_grade) if current_grade in GRADE_ORDER else 0
            if curr_idx < len(GRADE_ORDER) - 1:
                next_g = GRADE_ORDER[curr_idx + 1]
                req = GRADE_REQUIREMENTS[next_g]
                rem_req = max(0, req - spent)
                grade_msg = f"\n*(다음 등급인 **{next_g}**까지 눈알 `{rem_req:,}`개 더 사용 필요)*"

        await interaction.response.send_message(
            f"[구매 완료]\n"
            f"• 아이템: **{self.item_name}** 1개 (현재 소지: `{new_item_cnt}`개)\n"
            f"• 지불 눈알: `{self.price:,}`개\n"
            f"• 남은 눈알: {rem_str}"
            f"{grade_msg}",
            ephemeral=True,
        )

        await send_log(
            interaction.guild,
            f"[상점 구매]\n"
            f"• 구매자: {buyer.display_name} ({buyer.id})\n"
            f"• 상점: {tier_key}\n"
            f"• 아이템: {self.item_name}\n"
            f"• 지불 눈알: {self.price:,}개\n"
            f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
        )


class ShopView(discord.ui.View):
    def __init__(self, tier_key: str = "이방인 상점 (일반 상점)"):
        super().__init__(timeout=None)
        self.tier_key = tier_key

        tier_items = TIER_SHOPS.get(tier_key, {}).get("items", {})
        row = 0
        col_count = 0
        for item_name, info in tier_items.items():
            if col_count >= 5:
                row += 1
                col_count = 0
            if row > 4:
                break
            btn = ShopItemButton(
                item_name=item_name,
                price=info.get("price", 0),
                row=row,
            )
            self.add_item(btn)
            col_count += 1


# 일반 상점 목록 표시 (/상점)
@bot.hybrid_command(name="상점", description="본인 등급에 맞는 상점 확인 및 물품 구매")
async def 상점(ctx: commands.Context):
    user_data = get_user_data(ctx.author.id)
    grade = user_data.get("grade", "이방인")
    tier_key = get_tier_key_for_grade(grade)

    embed = create_shop_embed(tier_key, ctx.author, user_data)
    view = ShopView(tier_key)

    await ctx.send(embed=embed, view=view, ephemeral=True)


# --------------------------------------------------
# [ 명령어: 눈알 및 인벤토리 ]
# --------------------------------------------------


# 1. 프로필 확인 (/프로필)
@bot.hybrid_command(name="프로필", description="프로필")
async def 프로필(ctx: commands.Context):
    user_data = get_user_data(ctx.author.id)
    eyeballs = user_data.get("eyeballs", 0)
    grade = user_data.get("grade", "이방인")
    infinite = is_infinite_money(ctx.author, ctx.guild)

    # 팀 자동 감지
    team = "없음"
    if isinstance(ctx.author, discord.Member):
        team_roles = load_team_roles()
        for team_name, role_id in team_roles.items():
            if any(role.id == role_id for role in ctx.author.roles):
                team = team_name
                break

    # 다음 등급 및 눈알 소비 진행도 안내
    eyeballs_spent = user_data.get("eyeballs_spent", 0)
    current_index = GRADE_ORDER.index(grade) if grade in GRADE_ORDER else 0
    if current_index < len(GRADE_ORDER) - 1:
        next_grade = GRADE_ORDER[current_index + 1]
        req = GRADE_REQUIREMENTS.get(next_grade, 0)
        rem_req = max(0, req - eyeballs_spent)
        next_grade_text = f"{next_grade} ({eyeballs_spent:,} / {req:,}개 사용 - {rem_req:,}개 남음)"
    else:
        next_grade_text = "최고 등급 달성"

    eyeball_str = "무한 (∞)" if infinite else f"{eyeballs:,}개"

    # 장착중인 역할
    if isinstance(ctx.author, discord.Member):
        roles = [role.name for role in ctx.author.roles if role.name != "@everyone"]
        roles_text = "\n".join([f"• {role}" for role in roles]) if roles else "없음"
    else:
        roles_text = "확인 불가"

    # 임베드 생성
    embed = discord.Embed(
        title=f"{ctx.author.display_name}의 프로필",
        color=discord.Color.blue(),
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)

    embed.add_field(name="이름", value=ctx.author.display_name, inline=True)
    embed.add_field(name="팀", value=team, inline=True)
    embed.add_field(name="등급", value=grade, inline=True)
    embed.add_field(name="현재 보유 눈알", value=eyeball_str, inline=True)
    embed.add_field(name="누적 소비 눈알", value=f"{eyeballs_spent:,}개", inline=True)
    embed.add_field(name="다음 등급", value=next_grade_text, inline=True)
    embed.add_field(name="장착중인 모든 역할", value=roles_text, inline=False)

    await ctx.send(embed=embed, ephemeral=True)


# 2. 유저간 눈알 송금 (/송금 또는 /눈알송금 @유저 수량)
@bot.hybrid_command(
    name="송금",
    aliases=["눈알송금"],
    description="송금",
)
async def 송금(ctx: commands.Context, 받는사람: discord.Member, 수량: int):
    if 받는사람.id == ctx.author.id:
        await ctx.send("[오류] 자신에게는 눈알을 송금할 수 없습니다.", ephemeral=True)
        return

    if 받는사람.bot:
        await ctx.send("[오류] 봇에게는 눈알을 송금할 수 없습니다.", ephemeral=True)
        return

    if 수량 <= 0:
        await ctx.send("[오류] 송금할 수량은 최소 1개 이상이어야 합니다.", ephemeral=True)
        return

    author_is_infinite = is_infinite_money(ctx.author, ctx.guild)

    if not author_is_infinite:
        user_data = get_user_data(ctx.author.id)
        my_eyeballs = user_data.get("eyeballs", 0)

        if my_eyeballs < 수량:
            await ctx.send(
                f"[오류] 소지한 눈알이 부족합니다.\n"
                f"• 현재 소지: `{my_eyeballs:,}`개\n"
                f"• 송금 요청: `{수량:,}`개",
                ephemeral=True,
            )
            return

        rem_my_eyeballs, _, _ = await add_eyeballs(ctx.author.id, -수량)
        rem_str = f"`{rem_my_eyeballs:,}`개"
    else:
        rem_str = "무한 (∞)"

    await add_eyeballs(받는사람.id, 수량)

    pay_note = " (거인 직업 무한 재화)" if author_is_infinite else ""
    await ctx.send(
        f"[송금 완료]\n"
        f"• 받는 사람: {받는사람.mention}\n"
        f"• 보낸 눈알: `{수량:,}`개{pay_note}\n"
        f"• 남은 내 눈알: {rem_str}",
        ephemeral=True,
    )


# 3. 인벤토리 확인 (/인벤토리)
@bot.hybrid_command(name="인벤토리", description="인벤토리")
async def 인벤토리(ctx: commands.Context):
    user_data = get_user_data(ctx.author.id)
    eyeballs = user_data.get("eyeballs", 0)
    inv = user_data.get("inventory", {})
    infinite = is_infinite_money(ctx.author, ctx.guild)

    eyeball_str = "무한 (∞)" if infinite else f"`{eyeballs:,}`개"

    lines = [f"[{ctx.author.display_name}님의 인벤토리]", f"• 소지 눈알: {eyeball_str}", "──────────────────"]

    active_items = {k: v for k, v in inv.items() if v > 0}
    if not active_items:
        lines.append("소지하고 있는 아이템이 없습니다.")
    else:
        for item_name, count in active_items.items():
            lines.append(f"• {item_name}: `{count}`개")
        lines.append("──────────────────")
        lines.append(f"총 `{len(active_items)}`종류의 아이템을 보유 중입니다.")

    msg = "\n".join(lines)

    await ctx.send(msg, ephemeral=True)


# 3. 유저간 아이템 구매 희망 글 등록 (/구매 [아이템이름] [수량] [가격])
@bot.hybrid_command(
    name="구매",
    description="구매",
)
@discord.app_commands.autocomplete(아이템이름=shop_item_autocomplete)
async def 구매(ctx: commands.Context, 아이템이름: str, 수량: int, 가격: int):
    # 상점 채널 검사
    if not is_shop_channel(ctx.channel):
        await ctx.send(
            "[오류] 이곳은 상점으로 지정된 채널(포럼)이 아닙니다.\n"
            "지정된 상점 포럼/채널에서만 구매 글을 올리실 수 있습니다.",
            ephemeral=True,
        )
        return

    if 수량 <= 0:
        await ctx.send("[오류] 구매 수량은 최소 1개 이상이어야 합니다.", ephemeral=True)
        return

    if 가격 <= 0:
        await ctx.send("[오류] 매입 희망 가격은 1 눈알 이상이어야 합니다.", ephemeral=True)
        return

    total_cost = 가격 * 수량
    author_is_infinite = is_infinite_money(ctx.author, ctx.guild)

    if not author_is_infinite:
        user_data = get_user_data(ctx.author.id)
        current_eyeballs = user_data.get("eyeballs", 0)

        if current_eyeballs < total_cost:
            await ctx.send(
                f"[오류] 눈알이 부족합니다.\n"
                f"• 총 필요 눈알: `{total_cost:,}`개 (개당 `{가격:,}`개 x {수량}개)\n"
                f"• 현재 소지: `{current_eyeballs:,}`개",
                ephemeral=True,
            )
            return

        # 구매자의 눈알 선 차감 (거래 완료 전까지 에스크로 보관)
        remaining_eyeballs, _, _ = await add_eyeballs(ctx.author.id, -total_cost)
        rem_str = f"`{remaining_eyeballs:,}`개"
    else:
        rem_str = "무한 (∞)"

    listing_id = uuid.uuid4().hex[:8]
    market_data = load_market_data()

    channel_name = getattr(ctx.channel, "name", "상점")
    embed = discord.Embed(
        title=f"[구매 희망] {아이템이름}",
        color=discord.Color.green(),
    )
    embed.add_field(name="매입 가격", value=f"**개당 {가격:,} 눈알**", inline=True)
    embed.add_field(name="필요 수량", value=f"**{수량:,}개**", inline=True)
    embed.add_field(name="구매자", value=ctx.author.mention, inline=True)
    embed.set_footer(
        text=f"{channel_name} | [판매하기]를 누르면 아이템을 넘기고 눈알을 수령할 수 있습니다."
    )

    view = discord.ui.View(timeout=None)
    view.add_item(MarketFulfillButton(listing_id))
    view.add_item(MarketBuyCancelButton(listing_id))

    sent_msg = await ctx.channel.send(embed=embed, view=view)

    market_data[listing_id] = {
        "listing_id": listing_id,
        "type": "buy",
        "buyer_id": ctx.author.id,
        "buyer_name": ctx.author.display_name,
        "item_name": 아이템이름,
        "price": 가격,
        "quantity": 수량,
        "channel_id": sent_msg.channel.id,
        "message_id": sent_msg.id,
        "status": "active",
        "created_at": get_today_str(),
        "buyer_infinite": author_is_infinite,
    }
    save_market_data(market_data)

    escrow_note = " (거인 직업 무한 재화 적용)" if author_is_infinite else ""
    await ctx.send(
        f"[구매 등록 완료] **{아이템이름}** `{수량}`개 구매 희망 글이 등록되었습니다.\n"
        f"• 총 예치 눈알: `{total_cost:,}`개{escrow_note} (남은 보유: {rem_str})\n"
        f"• 다른 유저가 [판매하기] 버튼을 눌러 아이템을 넘기면 즉시 인벤토리에 들어옵니다.",
        ephemeral=True,
    )


# 4. 유저간 아이템 판매 등록 (/판매 [아이템이름] [가격] [수량])
@bot.hybrid_command(
    name="판매",
    description="판매",
)
@discord.app_commands.autocomplete(아이템이름=user_inventory_autocomplete)
async def 판매(
    ctx: commands.Context,
    아이템이름: str,
    가격: int,
    수량: int = 1,
):
    # 상점 채널 검사
    if not is_shop_channel(ctx.channel):
        await ctx.send(
            "[오류] 이곳은 상점으로 지정된 채널(포럼)이 아닙니다.\n"
            "지정된 상점 포럼/채널에서만 판매 글을 올리실 수 있습니다.",
            ephemeral=True,
        )
        return

    if 가격 <= 0:
        await ctx.send("[오류] 판매 가격은 1 눈알 이상이어야 합니다.", ephemeral=True)
        return

    if 수량 <= 0:
        await ctx.send("[오류] 판매 수량은 최소 1개 이상이어야 합니다.", ephemeral=True)
        return

    user_data = get_user_data(ctx.author.id)
    inv = user_data.get("inventory", {})
    owned_count = inv.get(아이템이름, 0)

    if owned_count < 수량:
        await ctx.send(
            f"[오류] 소지한 아이템이 부족합니다.\n"
            f"• **{아이템이름}** 현재 소지: `{owned_count}`개 (등록 요청: `{수량}`개)",
            ephemeral=True,
        )
        return

    # 판매자 인벤토리에서 아이템 차감(매물 보관)
    rem_inv_cnt = add_item_to_inventory(ctx.author.id, 아이템이름, -수량)

    # 고유 매물 ID 생성 및 데이터 저장
    listing_id = uuid.uuid4().hex[:8]
    market_data = load_market_data()

    channel_name = getattr(ctx.channel, "name", "상점")
    embed = discord.Embed(
        title=f"[판매 중] {아이템이름}",
        color=discord.Color.blue(),
    )
    embed.add_field(name="개당 가격", value=f"**{가격:,} 눈알**", inline=True)
    embed.add_field(name="남은 수량", value=f"**{수량:,}개**", inline=True)
    embed.add_field(name="판매자", value=ctx.author.mention, inline=True)
    embed.set_footer(
        text=f"{channel_name} | [구매하기]를 누르면 원하는 수량만큼 구매하실 수 있습니다."
    )

    # 버튼 뷰 부착 (구매 버튼 + 취소 버튼)
    view = discord.ui.View(timeout=None)
    view.add_item(MarketBuyButton(listing_id))
    view.add_item(MarketCancelButton(listing_id))

    sent_msg = await ctx.channel.send(embed=embed, view=view)

    # 매물 등록 정보 기록
    market_data[listing_id] = {
        "listing_id": listing_id,
        "seller_id": ctx.author.id,
        "seller_name": ctx.author.display_name,
        "item_name": 아이템이름,
        "price": 가격,
        "quantity": 수량,
        "channel_id": sent_msg.channel.id,
        "message_id": sent_msg.id,
        "status": "active",
        "created_at": get_today_str(),
    }
    save_market_data(market_data)

    await ctx.send(
        f"[판매 등록 완료] **{아이템이름}** `{수량}`개가 개당 `{가격:,}` 눈알에 판매 등록되었습니다.\n"
        f"• 남은 소지 수량: `{rem_inv_cnt}`개\n"
        f"• 상점 채널에 판매 카드가 게시되어 다른 유저들이 구매할 수 있습니다.",
        ephemeral=True,
    )


# --------------------------------------------------
# [ 관리자 명령어: 팀 역할 지정 ]
# --------------------------------------------------
# [ 관리자 명령어: 역할 지정 ]
# --------------------------------------------------


# 관리자용: 역할 지정 (/역할지정 [타입] [이름] [역할])
@bot.hybrid_command(
    name="역할지정",
    description="역할 지정 (등급/팀)",
)
@admin_only()
async def 역할지정(
    ctx: commands.Context,
    타입: str,
    이름: str,
    역할: discord.Role,
):
    if 타입 == "등급":
        if 이름 not in GRADE_REQUIREMENTS:
            await ctx.send(
                f"[오류] 유효한 등급 이름이 아닙니다. 가능한 등급: {', '.join(GRADE_REQUIREMENTS.keys())}",
                ephemeral=True,
            )
            return

        grade_roles = load_grade_roles()
        grade_roles[이름] = 역할.id
        save_grade_roles(grade_roles)

        await ctx.send(
            f"[등급 역할 지정 완료] {이름} 등급이 {역할.mention} 역할로 지정되었습니다.",
            ephemeral=True,
        )
    elif 타입 == "팀":
        if 이름 not in TEAMS:
            await ctx.send(
                f"[오류] 유효한 팀 이름이 아닙니다. 가능한 팀: {', '.join(TEAMS)}",
                ephemeral=True,
            )
            return

        team_roles = load_team_roles()
        team_roles[이름] = 역할.id
        save_team_roles(team_roles)

        await ctx.send(
            f"[팀 역할 지정 완료] {이름} 팀이 {역할.mention} 역할로 지정되었습니다.\n"
            f"• 이 역할을 가진 유저는 자동으로 {이름} 팀으로 표시됩니다.",
            ephemeral=True,
        )
    else:
        await ctx.send(
            "[오류] 유효한 타입이 아닙니다. 가능한 타입: 등급, 팀",
            ephemeral=True,
        )


# 관리자용: 역할 목록 확인 (/역할목록 [타입])
@bot.hybrid_command(
    name="역할목록",
    description="역할 목록 확인 (등급/팀)",
)
@admin_only()
async def 역할목록(ctx: commands.Context, 타입: str):
    if 타입 == "등급":
        grade_roles = load_grade_roles()
        if not grade_roles:
            await ctx.send("[등급 역할 목록] 지정된 등급 역할이 없습니다.", ephemeral=True)
            return

        lines = ["[등급별 역할 목록]", "──────────────────"]
        for grade, role_id in grade_roles.items():
            role = ctx.guild.get_role(role_id)
            if role:
                lines.append(f"• {grade}: {role.mention}")
            else:
                lines.append(f"• {grade}: ID {role_id} (삭제되었거나 찾을 수 없는 역할)")
        lines.append("──────────────────")

        await ctx.send("\n".join(lines), ephemeral=True)
    elif 타입 == "팀":
        team_roles = load_team_roles()
        if not team_roles:
            await ctx.send("[팀 역할 목록] 지정된 팀 역할이 없습니다.", ephemeral=True)
            return

        lines = ["[팀별 역할 목록]", "──────────────────"]
        for team, role_id in team_roles.items():
            role = ctx.guild.get_role(role_id)
            if role:
                lines.append(f"• {team}: {role.mention}")
            else:
                lines.append(f"• {team}: ID {role_id} (삭제되었거나 찾을 수 없는 역할)")
        lines.append("──────────────────")
        lines.append("지정된 역할을 가진 유저는 자동으로 해당 팀으로 표시됩니다.")

        await ctx.send("\n".join(lines), ephemeral=True)
    else:
        await ctx.send(
            "[오류] 유효한 타입이 아닙니다. 가능한 타입: 등급, 팀",
            ephemeral=True,
        )




# --------------------------------------------------
# [ 관리자 명령어: 층별 채널 지정 ]
# --------------------------------------------------


# 관리자용: 채널 지정 (/채널지정 [타입] [이름] [채널])
@bot.hybrid_command(
    name="채널지정",
    description="채널 지정 (층)",
)
@admin_only()
async def 채널지정(
    ctx: commands.Context,
    타입: str,
    이름: str,
    채널: discord.abc.GuildChannel = None,
):
    if 타입 == "층":
        if 이름 not in FLOOR_LIMITS:
            await ctx.send(
                f"[오류] 유효한 층 이름이 아닙니다. 가능한 층: {', '.join(FLOOR_LIMITS.keys())}",
                ephemeral=True,
            )
            return

        target = 채널 or ctx.channel
        floor_channels = load_floor_channels()
        floor_channels[이름] = target.id
        save_floor_channels(floor_channels)

        await ctx.send(
            f"[층 채널 지정 완료] {이름} 채널이 {target.mention}으로 지정되었습니다.\n"
            f"• 해당 채널에서 하루 최대 {FLOOR_LIMITS[이름]}개의 눈알을 획득할 수 있습니다.",
            ephemeral=True,
        )
    else:
        await ctx.send(
            "[오류] 유효한 타입이 아닙니다. 가능한 타입: 층",
            ephemeral=True,
        )


# 관리자용: 채널 목록 확인 (/채널목록 [타입])
@bot.hybrid_command(
    name="채널목록",
    description="채널 목록 확인 (층)",
)
@admin_only()
async def 채널목록(ctx: commands.Context, 타입: str):
    if 타입 == "층":
        floor_channels = load_floor_channels()
        if not floor_channels:
            await ctx.send("[층 채널 목록] 지정된 층 채널이 없습니다.", ephemeral=True)
            return

        lines = ["[층별 채널 목록]", "──────────────────"]
        for floor, ch_id in floor_channels.items():
            ch = ctx.guild.get_channel(ch_id)
            if ch:
                lines.append(f"• {floor}: {ch.mention} (한도: {FLOOR_LIMITS[floor]}개)")
            else:
                lines.append(f"• {floor}: ID {ch_id} (삭제되었거나 찾을 수 없는 채널)")
        lines.append("──────────────────")
        lines.append(f"총 일일 눈알 한도: {DAILY_MAX_EYEBALLS}개")

        await ctx.send("\n".join(lines), ephemeral=True)
    else:
        await ctx.send(
            "[오류] 유효한 타입이 아닙니다. 가능한 타입: 층",
            ephemeral=True,
        )


# --------------------------------------------------
# [ 관리자 명령어: 눈알 / 아이템 / 상점 채널 / 관리자 직업 관리 ]
# --------------------------------------------------


# 5. 관리자용: 눈알 지급/차감 명령어 (/눈알지급 @유저 수량)
@bot.hybrid_command(name="눈알지급", description="눈알지급")
@admin_only()
async def 눈알지급(ctx: commands.Context, member: discord.Member, amount: int):
    new_balance, _, _ = await add_eyeballs(member.id, amount)
    if amount >= 0:
        await ctx.send(
            f"[눈알 지급] {member.mention}님에게 눈알 {amount:,}개를 지급했습니다. (현재: {new_balance:,}개)",
            ephemeral=True,
        )
    else:
        await ctx.send(
            f"[눈알 차감] {member.mention}님의 눈알 {abs(amount):,}개를 차감했습니다. (현재: {new_balance:,}개)",
            ephemeral=True,
        )


# 6. 관리자용: 아이템 직접 지급/차감 명령어 (/아이템지급 @유저 아이템이름 수량)
@bot.hybrid_command(name="아이템지급", description="아이템지급")
@admin_only()
async def 아이템지급(ctx: commands.Context, member: discord.Member, 아이템이름: str, 수량: int):
    new_count = add_item_to_inventory(member.id, 아이템이름, 수량)
    if 수량 >= 0:
        await ctx.send(
            f"[아이템 지급] {member.mention}님에게 {아이템이름} {수량:,}개를 지급했습니다. (소지: {new_count:,}개)",
            ephemeral=True,
        )
    else:
        await ctx.send(
            f"[아이템 차감] {member.mention}님의 {아이템이름} {abs(수량):,}개를 차감했습니다. (소지: {new_count:,}개)",
            ephemeral=True,
        )


# 7. 관리자용: 상점 포럼/채널 추가 (/상점채널추가 [채널])
@bot.hybrid_command(
    name="상점채널추가",
    description="상점채널추가",
)
@admin_only()
async def 상점채널추가(
    ctx: commands.Context,
    채널: discord.abc.GuildChannel = None,
):
    target = 채널 or ctx.channel
    channels = load_shop_channels()

    if target.id in channels:
        await ctx.send(f"[안내] {target.mention} 채널은 이미 상점으로 등록되어 있습니다.", ephemeral=True)
        return

    channels.append(target.id)
    save_shop_channels(channels)

    await ctx.send(
        f"[상점 채널 추가 완료] {target.mention} 채널이 상점 채널로 성공적으로 추가되었습니다.\n"
        f"(포럼 채널을 등록한 경우 해당 포럼 안의 모든 글에서 거래가 가능합니다)",
        ephemeral=True,
    )


# 8. 관리자용: 상점 포럼/채널 삭제 (/상점채널삭제 [채널])
@bot.hybrid_command(
    name="상점채널삭제",
    description="상점채널삭제",
)
@admin_only()
async def 상점채널삭제(
    ctx: commands.Context,
    채널: discord.abc.GuildChannel = None,
):
    target = 채널 or ctx.channel
    channels = load_shop_channels()

    if target.id not in channels:
        await ctx.send(f"[안내] {target.mention} 채널은 등록된 상점 채널이 아닙니다.", ephemeral=True)
        return

    channels.remove(target.id)
    save_shop_channels(channels)

    await ctx.send(
        f"[상점 채널 삭제 완료] {target.mention} 채널을 상점 목록에서 해제했습니다.",
        ephemeral=True,
    )


# 9. 상점 채널 목록 확인 (/상점채널목록)
@bot.hybrid_command(name="상점채널목록", description="상점채널목록")
async def 상점채널목록(ctx: commands.Context):
    channels = load_shop_channels()
    if not channels:
        await ctx.send("[상점 채널 목록] 등록된 상점 채널/포럼이 없습니다. 관리자가 /상점채널추가 로 등록할 수 있습니다.", ephemeral=True)
        return

    lines = ["[등록된 상점 채널 및 포럼 목록]", "──────────────────"]
    for cid in channels:
        ch = ctx.guild.get_channel(cid)
        if ch:
            lines.append(f"• {ch.mention} (`{ch.name}`)")
        else:
            lines.append(f"• ID: `{cid}` (삭제되었거나 찾을 수 없는 채널)")
    lines.append("──────────────────")
    lines.append("해당 채널(또는 포럼 내 게시글)에서 /구매 및 /판매 를 이용하실 수 있습니다.")

    await ctx.send("\n".join(lines), ephemeral=True)


# 10. 관리자용: 거인 직업(역할) 추가 (/거인직업추가 [역할])
@bot.hybrid_command(
    name="거인직업추가",
    description="거인직업추가",
)
@admin_only()
async def 거인직업추가(ctx: commands.Context, 역할: discord.Role):
    roles = load_admin_roles()
    if 역할.id in roles:
        await ctx.send(f"[안내] {역할.mention} 역할은 이미 거인 직업으로 등록되어 있습니다.", ephemeral=True)
        return

    roles.append(역할.id)
    save_admin_roles(roles)

    await ctx.send(
        f"[거인 직업 등록 완료] {역할.mention} 역할이 거인 직업으로 성공적으로 등록되었습니다.\n"
        f"• 해당 역할을 가진 멤버는 돈 무한 (눈알 무제한) 및 모든 관리자 명령어를 사용할 수 있습니다.",
        ephemeral=True,
    )


# 11. 관리자용: 거인 직업(역할) 삭제 (/거인직업삭제 [역할])
@bot.hybrid_command(
    name="거인직업삭제",
    description="거인직업삭제",
)
@admin_only()
async def 거인직업삭제(ctx: commands.Context, 역할: discord.Role):
    roles = load_admin_roles()
    if 역할.id not in roles:
        await ctx.send(f"[안내] {역할.mention} 역할은 거인 직업으로 등록되어 있지 않습니다.", ephemeral=True)
        return

    roles.remove(역할.id)
    save_admin_roles(roles)

    await ctx.send(
        f"[거인 직업 삭제 완료] {역할.mention} 역할을 거인 직업 목록에서 해제했습니다.",
        ephemeral=True,
    )


# 12. 거인 직업 목록 확인 (/거인직업목록)
@bot.hybrid_command(
    name="거인직업목록",
    description="거인직업목록",
)
@admin_only()
async def 거인직업목록(ctx: commands.Context):
    roles = load_admin_roles()
    if not roles:
        await ctx.send("[거인 직업 목록] 등록된 거인 직업이 없습니다. 관리자가 /거인직업추가 로 등록할 수 있습니다.", ephemeral=True)
        return

    lines = ["[등록된 거인 직업(역할) 목록]", "──────────────────"]
    for rid in roles:
        role = ctx.guild.get_role(rid) if ctx.guild else None
        if role:
            lines.append(f"• {role.mention} (`{role.name}`)")
        else:
            lines.append(f"• 역할 ID: `{rid}` (삭제되었거나 찾을 수 없는 역할)")
    lines.append("──────────────────")
    lines.append("등록된 역할을 소지한 유저는 돈 무한 및 모든 관리자 명령어를 사용할 수 있습니다.")

    await ctx.send("\n".join(lines), ephemeral=True)


# 13. 관리자용: 로그 채널 설정 (/로그채널설정 [채널])
@bot.hybrid_command(
    name="로그채널설정",
    description="로그채널설정",
)
@admin_only()
async def 로그채널설정(ctx: commands.Context, 채널: discord.abc.GuildChannel):
    save_log_channel(ctx.guild.id, 채널.id)
    target = await _resolve_log_channel(ctx.guild, 채널.id)
    if target is None and _is_loggable_channel(채널):
        target = 채널
    sent = False
    mention = getattr(target, "mention", None) or 채널.mention
    if target is not None:
        sent = await _try_send_log_message(
            target,
            f"[시스템] 로그 채널이 이 채널로 설정되었습니다.\n"
            f"• 설정자: {ctx.author.display_name} ({ctx.author.id})\n"
            f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
        )
    if sent:
        await ctx.send(
            f"[설정 완료] 앞으로 모든 명령어 사용 로그가 {mention} 채널에 기록됩니다.\n"
            f"방금 해당 채널에 시험 메시지를 보냈습니다.",
            ephemeral=True,
        )
    else:
        await ctx.send(
            f"[설정됨, 전송 실패] {채널.mention} 을(를) 저장했지만 메시지를 보내지 못했습니다.\n"
            f"텍스트 채널인지, 봇이 그 채널을 볼 수 있는지 확인해 주세요.",
            ephemeral=True,
        )


# 14. 관리자용: 로그 채널 확인 (/로그채널확인)
@bot.hybrid_command(
    name="로그채널확인",
    description="로그채널확인",
)
@admin_only()
async def 로그채널확인(ctx: commands.Context):
    log_ch = await get_or_create_log_channel(ctx.guild)
    if log_ch:
        await ctx.send(
            f"[로그 채널 안내] 현재 로그가 기록되는 채널: {log_ch.mention} (`{getattr(log_ch, 'name', log_ch.id)}`)",
            ephemeral=True,
        )
    else:
        await ctx.send(
            "[로그 채널 안내] 로그 채널을 찾지 못했습니다. /로그채널설정 으로 텍스트 채널을 지정해 주세요.",
            ephemeral=True,
        )


# 14-1. 관리자용: 로그 시험 전송 (/로그테스트)
@bot.hybrid_command(
    name="로그테스트",
    description="로그테스트",
)
@admin_only()
async def 로그테스트(ctx: commands.Context):
    log_ch = await get_or_create_log_channel(ctx.guild)
    await send_log(
        ctx.guild,
        f"[로그 테스트]\n"
        f"• 실행자: {ctx.author.display_name} ({ctx.author.id})\n"
        f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
    )
    if log_ch:
        await ctx.send(
            f"[테스트 완료] {log_ch.mention} 채널로 시험 로그를 보냈습니다. 해당 채널을 확인해 주세요.",
            ephemeral=True,
        )
    else:
        await ctx.send("[오류] 로그 채널을 찾지 못해 시험 메시지를 보낼 수 없습니다.", ephemeral=True)


# 15. 관리자용: 기본 역할 설정 (/기본역할설정 [역할])
@bot.hybrid_command(
    name="기본역할설정",
    description="기본역할설정",
)
@admin_only()
async def 기본역할설정(ctx: commands.Context, 역할: discord.Role):
    save_default_role(ctx.guild.id, 역할.id)
    await ctx.send(f"[기본 역할 설정 완료] 신규 유저 입장 시 {역할.mention} 역할이 자동 지급됩니다.", ephemeral=True)


# 16. 관리자용: 기본 역할 확인 (/기본역할확인)
@bot.hybrid_command(
    name="기본역할확인",
    description="기본역할확인",
)
@admin_only()
async def 기본역할확인(ctx: commands.Context):
    data = load_default_roles()
    rid = data.get(str(ctx.guild.id))
    role = ctx.guild.get_role(rid) if rid else None
    if role:
        await ctx.send(f"[기본 역할 안내] 현재 기본 역할: {role.mention}", ephemeral=True)
    else:
        await ctx.send("[기본 역할 안내] 현재 설정된 기본 역할이 없습니다. /기본역할설정 으로 지정할 수 있습니다.", ephemeral=True)


# 17. 관리자용: 기본 역할 해제 (/기본역할삭제)
@bot.hybrid_command(
    name="기본역할삭제",
    aliases=["기본역할해제"],
    description="기본역할삭제",
)
@admin_only()
async def 기본역할삭제(ctx: commands.Context):
    delete_default_role(ctx.guild.id)
    await ctx.send("[기본 역할 해제 완료] 기본 역할 자동 지급이 해제되었습니다.", ephemeral=True)


# 18. 관리자용: 관리자 채팅방 설정 (/관리자채널설정 [채널])
@bot.hybrid_command(
    name="관리자채널설정",
    description="루비 이상 등급업 승인 요청을 수신할 관리자 전용 채널 설정",
)
@admin_only()
async def 관리자채널설정(ctx: commands.Context, 채널: discord.abc.GuildChannel):
    save_admin_channel(ctx.guild.id, 채널.id)
    await ctx.send(
        f"[관리자 채널 설정 완료] {채널.mention} 채널이 관리자 전용 채널로 설정되었습니다.\n"
        f"앞으로 유저가 루비 이상의 등급 승급 조건을 달성하면 이 채널로 승급 수락/유지 요청 버튼이 전송됩니다.",
        ephemeral=True,
    )


# 19. 관리자용: 관리자 채팅방 확인 (/관리자채널확인)
@bot.hybrid_command(
    name="관리자채널확인",
    description="현재 설정된 관리자 전용 채널 확인",
)
@admin_only()
async def 관리자채널확인(ctx: commands.Context):
    data = load_admin_channels()
    ch_id = data.get(str(ctx.guild.id))
    ch = ctx.guild.get_channel(int(ch_id)) if ch_id else None
    if ch:
        await ctx.send(f"[관리자 채널 안내] 현재 관리자 채널: {ch.mention} (`{ch.name}`)", ephemeral=True)
    else:
        await ctx.send(
            "[관리자 채널 안내] 별도로 설정된 관리자 채널이 없습니다. `/관리자채널설정`으로 지정하지 않으면 로그 채널로 전송됩니다.",
            ephemeral=True,
        )


# 에러 처리
@눈알지급.error
@아이템지급.error
@역할지정.error
@채널지정.error
@상점채널추가.error
@상점채널삭제.error
@거인직업추가.error
@거인직업삭제.error
@거인직업목록.error
@로그채널설정.error
@로그채널확인.error
@로그테스트.error
@관리자채널설정.error
@관리자채널확인.error
@기본역할설정.error
@기본역할확인.error
@기본역할삭제.error
@동기화.error
async def admin_command_error(ctx: commands.Context, error):
    if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
        await ctx.send("[오류] 이 명령어는 관리자 또는 거인 직업 역할을 가진 유저만 사용할 수 있습니다.", ephemeral=True)
        # 권한 오류도 로그 채널에 기록
        await send_log(
            ctx.guild,
            f"[권한 부족 시도 로그]\n"
            f"• 시도자: {ctx.author.display_name}\n"
            f"• 명령어: /{ctx.command.name if ctx.command else '알 수 없음'}\n"
            f"• 채널: #{getattr(ctx.channel, 'name', '알 수 없음')}\n"
            f"• 일시: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}",
        )
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("[오류] 필수 항목이 누락되었습니다. 명령어를 다시 확인해 주세요.", ephemeral=True)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("[오류] 입력한 값의 형식이 올바르지 않습니다.", ephemeral=True)
    else:
        await ctx.send(f"[오류] 오류가 발생했습니다: {error}", ephemeral=True)


# --------------------------------------------------
# [ 봇 실행 ]
# --------------------------------------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
LOCK_FILE = os.path.join(BASE_DIR, "bot.lock")
_instance_lock_handle = None


def acquire_single_instance_lock():
    """봇이 두 개 켜져 보상이 두 번 나가는 것을 방지"""
    global _instance_lock_handle
    _instance_lock_handle = open(LOCK_FILE, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            _instance_lock_handle.seek(0)
            if _instance_lock_handle.read(1) == b"":
                _instance_lock_handle.write(b"0")
                _instance_lock_handle.flush()
            _instance_lock_handle.seek(0)
            msvcrt.locking(_instance_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_instance_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("\n[오류] 봇이 이미 실행 중입니다. 기존 창을 종료한 뒤 다시 실행해 주세요.\n")
        sys.exit(1)


if __name__ == "__main__":
    acquire_single_instance_lock()
    try:
        bot.run(TOKEN)
    except discord.errors.PrivilegedIntentsRequired:
        print("\n" + "=" * 60)
        print("[오류 발생: Message Content Intent 권한 필요]")
        print("디스코드 개발자 포털에서 권한 설정이 필요합니다:")
        print("1. https://discord.com/developers/applications 접속")
        print("2. 봇 애플리케이션 선택 -> 왼쪽 메뉴의 [Bot] 클릭")
        print("3. 아래로 스크롤하여 [Privileged Gateway Intents] 섹션 찾기")
        print("4. [Message Content Intent] 항목을 활성화(체크/ON)하고 하단 [Save Changes] 저장")
        print("5. 설정을 완료한 후 이 프로그램을 다시 실행해 주세요!")
        print("=" * 60 + "\n")
    except discord.errors.LoginFailure:
        print("\n[오류] 디스코드 봇 토큰이 올바르지 않습니다. 토큰을 다시 확인해 주세요.\n")
    except Exception as e:
        print(f"\n[오류 발생] {e}\n")