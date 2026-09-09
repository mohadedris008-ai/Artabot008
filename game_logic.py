"""
Combined Game Logic Engine for Telegram Mini App Platform
Includes: Pasur, Sequence, and fully-featured Hokm Game Engine.
"""

import random
import time
import uuid
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple

SUITS = ['♠', '♥', '♦', '♣']
SUIT_NAMES = {'♠': 'spades', '♥': 'hearts', '♦': 'diamonds', '♣': 'clubs'}
SUIT_COLORS = {'♠': 'black', '♥': 'red', '♦': 'red', '♣': 'black'}
RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
RANK_VALUES = {
    'A': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,
    '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13
}

GAME_CATALOG = [
    {
        "id": "pasur",
        "title": "پاسور (چهاربرگ)",
        "category": "card",
        "min_players": 2,
        "max_players": 2,
        "icon": "🃏",
        "badge": "محبوب‌ترین",
        "description": "بازی اصیل ایرانی چهاربرگ، جمع عدد ۱۱، سور زدن و تصاحب خاج‌ها",
        "banner_gradient": "linear-gradient(135deg, #1e3a8a, #3b82f6)",
        "rules_summary": "جمع ۱۱ امتیاز می‌برد. سرباز کل کارت‌های عددی را جمع می‌کند.",
        "min_bet": 100,
        "active": True
    },
    {
        "id": "sequence",
        "title": "سکوئنس (Sequence)",
        "category": "board",
        "min_players": 2,
        "max_players": 4,
        "icon": "🎲",
        "badge": "استراتژیک",
        "description": "ترکیب استراتژی کارت و بورد، تشکیل خط ۵ تایی با جوکرهای یک‌چشم و دوچشم",
        "banner_gradient": "linear-gradient(135deg, #065f46, #10b981)",
        "rules_summary": "کارت بگذارید، مهره بچینید. ۵ مهره پشت سر هم بسازید.",
        "min_bet": 200,
        "active": True
    },
    {
        "id": "hokm",
        "title": "حکم کلاسیک",
        "category": "card",
        "min_players": 4,
        "max_players": 4,
        "icon": "👑",
        "badge": "۴ نفره تیمی",
        "description": "حکم ۴ نفره دونفره روبرو، تعیین حاکم، خال حکم، کوت و حاکم‌کوت",
        "banner_gradient": "linear-gradient(135deg, #831843, #ec4899)",
        "rules_summary": "رسیدن به ۷ دست برای برد هر دور. رعایت خال بازی اجباری است.",
        "min_bet": 500,
        "active": True
    }
]

def create_standard_deck() -> List[Dict[str, Any]]:
    # از uuid برای id استفاده می‌کنیم (نه یک شمارنده‌ی محلی) چون بعضی
    # بازی‌ها (مثل سکوئنس) دو دسته کارت استاندارد را با هم ترکیب می‌کنند؛
    # یک شمارنده‌ی از صفر شروع‌شونده در هر فراخوانی باعث تکراری شدن id
    # بین دو دسته می‌شد و انتخاب کارت با id در دست بازیکن را مبهم می‌کرد.
    deck = []
    for s in SUITS:
        for r in RANKS:
            deck.append({
                "id": f"{s}_{r}_{uuid.uuid4().hex[:8]}",
                "suit": s,
                "rank": r,
                "suit_name": SUIT_NAMES[s],
                "color": SUIT_COLORS[s],
                "value": RANK_VALUES[r]
            })
    random.shuffle(deck)
    return deck

class BaseGame:
    def __init__(self, room_id: str, game_type: str, config: Dict[str, Any] = None):
        self.room_id = room_id
        self.game_type = game_type
        self.config = config or {}
        self.players: List[str] = []
        self.player_info: Dict[str, Dict[str, Any]] = {}
        self.current_turn_index: int = 0
        self.is_started: bool = False
        self.is_finished: bool = False
        self.winner: Optional[str] = None
        self.turn_deadline = time.time() + 30
        # وقتی بازیکنی وسط بازی قطع اتصال می‌شود، به‌جای اینکه بازی برای
        # همیشه منتظر نوبت او بماند، به‌صورت خودکار توسط یک هوش مصنوعی
        # ساده‌ی قانون‌محور (rule-based) کنترل می‌شود تا بازی ادامه پیدا کند.
        self.bot_controlled: Dict[str, bool] = {}

    def add_player(self, user_id: str, username: str, avatar: str = "👤") -> bool:
        if user_id in self.players:
            return True
        max_p = self.config.get("max_players", 4)
        if len(self.players) >= max_p:
            return False
        self.players.append(user_id)
        self.player_info[user_id] = {
            "id": user_id,
            "username": username,
            "avatar": avatar,
            "score": 0,
            "is_connected": True
        }
        self.bot_controlled[user_id] = False
        return True

    def get_current_turn_player(self) -> Optional[str]:
        if not self.players:
            return None
        return self.players[self.current_turn_index % len(self.players)]

    def advance_turn(self):
        if self.players:
            self.current_turn_index = (self.current_turn_index + 1) % len(self.players)
            self.turn_deadline = time.time() + 30

    def get_bot_action(self, user_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        وقتی نوبتِ بازیکنی است که الان bot_controlled شده، این متد باید یک
        حرکتِ قانونی (action, payload) برای او برگرداند. پیاده‌سازی پیش‌فرض
        هیچ حرکتی ندارد؛ هر زیرکلاس بازی این را با منطق خودش override می‌کند.
        """
        return None

    def get_player_view(self, user_id: str) -> Dict[str, Any]:
        return {
            "room_id": self.room_id,
            "game_type": self.game_type,
            "is_started": self.is_started,
            "is_finished": self.is_finished,
            "current_turn": self.get_current_turn_player(),
            "turn_deadline": self.turn_deadline,
            "players": [self.player_info[p] for p in self.players if p in self.player_info],
            "winner": self.winner
        }

    def handle_action(self, user_id: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

# ==================== ۱. پاسور (چهاربرگ) ====================
class PasurGame(BaseGame):
    def __init__(self, room_id: str, config: Dict[str, Any] = None):
        cfg = {"min_players": 2, "max_players": 2, "target_score": 62}
        if config: cfg.update(config)
        super().__init__(room_id, "pasur", cfg)
        self.deck: List[Dict[str, Any]] = []
        self.table_cards: List[Dict[str, Any]] = []
        self.hands: Dict[str, List[Dict[str, Any]]] = {}
        self.collected: Dict[str, List[Dict[str, Any]]] = {}
        self.sur_count: Dict[str, int] = {}
        self.last_collector: Optional[str] = None
        self.round_number: int = 0
        self.is_final_round: bool = False

    def start_game(self) -> bool:
        if len(self.players) < 2: return False
        self.deck = create_standard_deck()
        self.is_started = True
        self.round_number = 1
        self.table_cards = []

        for p in self.players:
            self.hands[p] = []
            self.collected[p] = []
            self.sur_count[p] = 0

        while len(self.table_cards) < 4:
            card = self.deck.pop()
            if card["rank"] == "J" and len(self.deck) > 4:
                self.deck.insert(len(self.deck) // 2, card)
            else:
                self.table_cards.append(card)

        self._deal_hands()
        self.current_turn_index = 0
        return True

    def _deal_hands(self):
        for p in self.players:
            self.hands[p] = []
            for _ in range(4):
                if self.deck:
                    self.hands[p].append(self.deck.pop())
        if len(self.deck) == 0:
            self.is_final_round = True

    def _find_sum_combinations(self, target: int, candidates: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        valid = []
        n = len(candidates)
        for i in range(1, 1 << n):
            subset = [candidates[j] for j in range(n) if (i & (1 << j))]
            if sum(c["value"] for c in subset) == target:
                valid.append(subset)
        return valid

    def handle_action(self, user_id: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_started or self.is_finished:
            return {"status": "error", "message": "وضعیت نامعتبر بازی"}
        if self.get_current_turn_player() != user_id:
            return {"status": "error", "message": "نوبت شما نیست!"}

        if action == "play_card":
            card_id = payload.get("card_id")
            hand = self.hands.get(user_id, [])
            played_card = next((c for c in hand if c["id"] == card_id), None)
            if not played_card:
                return {"status": "error", "message": "کارت در دست شما یافت نشد"}

            hand.remove(played_card)
            captured = []
            is_sur = False

            if played_card["rank"] == "J":
                non_court = [c for c in self.table_cards if c["rank"] not in ("K", "Q")]
                if non_court:
                    captured = non_court + [played_card]
                    self.table_cards = [c for c in self.table_cards if c["rank"] in ("K", "Q")]
                    self.collected[user_id].extend(captured)
                    self.last_collector = user_id
                else:
                    self.table_cards.append(played_card)
            elif played_card["rank"] in ("Q", "K"):
                matches = [c for c in self.table_cards if c["rank"] == played_card["rank"]]
                if matches:
                    captured = [matches[0], played_card]
                    self.table_cards.remove(matches[0])
                    self.collected[user_id].extend(captured)
                    self.last_collector = user_id
                else:
                    self.table_cards.append(played_card)
            else:
                needed = 11 - played_card["value"]
                numeric_cards = [c for c in self.table_cards if c["rank"] not in ("J", "Q", "K")]
                combos = self._find_sum_combinations(needed, numeric_cards)
                if combos:
                    best = max(combos, key=len)
                    captured = list(best) + [played_card]
                    for c in best:
                        self.table_cards.remove(c)
                    self.collected[user_id].extend(captured)
                    self.last_collector = user_id
                    if len(self.table_cards) == 0 and not self.is_final_round:
                        self.sur_count[user_id] += 1
                        is_sur = True
                else:
                    self.table_cards.append(played_card)

            if all(len(self.hands[p]) == 0 for p in self.players):
                if len(self.deck) > 0:
                    self._deal_hands()
                    self.round_number += 1
                else:
                    if self.last_collector and self.table_cards:
                        self.collected[self.last_collector].extend(self.table_cards)
                        self.table_cards = []
                    self._calculate_final_scores()
                    self.is_finished = True

            self.advance_turn()
            return {
                "status": "ok",
                "played_card": played_card,
                "captured": captured,
                "is_sur": is_sur,
                "next_turn": self.get_current_turn_player(),
                "is_finished": self.is_finished
            }
        return {"status": "error", "message": f"اکشن ناشناخته: {action}"}

    def _calculate_final_scores(self):
        scores = {p: 0 for p in self.players}
        clubs = {p: 0 for p in self.players}
        for p in self.players:
            for c in self.collected[p]:
                if c["suit"] == "♣": clubs[p] += 1
                if c["rank"] == "10" and c["suit"] == "♦": scores[p] += 3
                if c["rank"] == "2" and c["suit"] == "♣": scores[p] += 2
                if c["rank"] == "A": scores[p] += 1
                if c["rank"] == "J": scores[p] += 1
            scores[p] += self.sur_count[p] * 5
        best_clubs = max(clubs, key=clubs.get)
        if clubs[best_clubs] >= 7: scores[best_clubs] += 7
        for p in self.players:
            self.player_info[p]["score"] = scores[p]
        self.winner = max(scores, key=scores.get)

    def get_player_view(self, user_id: str) -> Dict[str, Any]:
        data = super().get_player_view(user_id)
        data.update({
            "table_cards": self.table_cards,
            "my_hand": self.hands.get(user_id, []),
            "my_collected_count": len(self.collected.get(user_id, [])),
            "my_surs": self.sur_count.get(user_id, 0),
            "deck_remaining": len(self.deck)
        })
        return data

    def get_bot_action(self, user_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        hand = self.hands.get(user_id, [])
        if not hand:
            return None
        # اولویت با کارتی که چیزی از میز جمع می‌کند (سرباز/جفت هم‌ارزش/جمع ۱۱)؛
        # در غیر این صورت یک کارت تصادفی بازی می‌شود (حرکت قانونی همیشه هست).
        for card in hand:
            if card["rank"] == "J":
                non_court = [c for c in self.table_cards if c["rank"] not in ("K", "Q")]
                if non_court:
                    return "play_card", {"card_id": card["id"]}
            elif card["rank"] in ("Q", "K"):
                if any(c["rank"] == card["rank"] for c in self.table_cards):
                    return "play_card", {"card_id": card["id"]}
            else:
                needed = 11 - card["value"]
                numeric_cards = [c for c in self.table_cards if c["rank"] not in ("J", "Q", "K")]
                if self._find_sum_combinations(needed, numeric_cards):
                    return "play_card", {"card_id": card["id"]}
        return "play_card", {"card_id": hand[0]["id"]}

# ==================== ۲. سکوئنس (Sequence) ====================
SEQUENCE_LAYOUT = [
    ["W", "2♠", "3♠", "4♠", "5♠", "6♠", "7♠", "8♠", "9♠", "W"],
    ["6♣", "5♣", "4♣", "3♣", "2♣", "A♥", "K♥", "Q♥", "10♥", "10♠"],
    ["7♣", "A♠", "2♦", "3♦", "4♦", "5♦", "6♦", "7♦", "9♥", "Q♠"],
    ["8♣", "K♠", "6♣", "5♣", "4♣", "3♣", "2♣", "8♦", "8♥", "K♠"],
    ["9♣", "Q♠", "7♣", "6♥", "5♥", "4♥", "A♥", "9♦", "7♥", "A♠"],
    ["10♣", "10♠", "8♣", "7♥", "2♥", "3♥", "K♥", "10♦", "6♥", "2♦"],
    ["Q♣", "9♠", "9♣", "8♥", "9♥", "10♥", "Q♥", "Q♦", "5♥", "3♦"],
    ["K♣", "8♠", "10♣", "Q♣", "K♣", "A♣", "A♦", "K♦", "4♥", "4♦"],
    ["A♣", "7♠", "6♠", "5♠", "4♠", "3♠", "2♠", "2♥", "3♥", "5♦"],
    ["W", "A♦", "K♦", "Q♦", "10♦", "9♦", "8♦", "7♦", "6♦", "W"]
]

class SequenceGame(BaseGame):
    def __init__(self, room_id: str, config: Dict[str, Any] = None):
        cfg = {"min_players": 2, "max_players": 4, "hand_size": 6}
        if config: cfg.update(config)
        super().__init__(room_id, "sequence", cfg)
        self.board: List[List[Optional[str]]] = [[None]*10 for _ in range(10)]
        self.board[0][0] = self.board[0][9] = self.board[9][0] = self.board[9][9] = "W"
        self.deck: List[Dict[str, Any]] = []
        self.hands: Dict[str, List[Dict[str, Any]]] = {}
        self.player_teams: Dict[str, int] = {}

    ONE_EYED_JACK_SUITS = ('♠', '♣')   # جوکر یک‌چشم: برداشتن مهره‌ی حریف
    TWO_EYED_JACK_SUITS = ('♥', '♦')   # جوکر دوچشم: گذاشتن مهره در هر خانه‌ی خالی
    SEQUENCE_LENGTH = 5
    SEQUENCES_TO_WIN = 2  # طبق قوانین اصلی، دو نفره/چهارنفره با ۲ توالی می‌برند

    def start_game(self) -> bool:
        if len(self.players) < 2: return False
        self.deck = create_standard_deck() + create_standard_deck()
        random.shuffle(self.deck)
        num_teams = 3 if len(self.players) == 3 else 2
        self.num_teams = num_teams
        self.team_sequences: Dict[int, int] = {t: 0 for t in range(num_teams)}
        self._used_sequence_cells: set = set()
        for i, p in enumerate(self.players):
            self.player_teams[p] = i % num_teams
            self.hands[p] = [self.deck.pop() for _ in range(6)]
        self.is_started = True
        return True

    @staticmethod
    def _card_key(card: Dict[str, Any]) -> str:
        return f"{card['rank']}{card['suit']}"

    def _refill_hand(self, user_id: str, used_card: Dict[str, Any]):
        hand = self.hands[user_id]
        hand.remove(used_card)
        if self.deck:
            hand.append(self.deck.pop())

    def _check_new_sequences(self, team: int) -> int:
        """بعد از هر حرکت، شمار توالی‌های ۵تایی جدید این تیم را برمی‌گرداند."""
        marker = f"team_{team}"

        def cell_belongs(r: int, c: int) -> bool:
            if not (0 <= r < 10 and 0 <= c < 10):
                return False
            val = self.board[r][c]
            return val == marker or val == "W"

        directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
        new_sequences = 0
        for dr, dc in directions:
            for r in range(10):
                for c in range(10):
                    cells = [(r + dr * k, c + dc * k) for k in range(self.SEQUENCE_LENGTH)]
                    if not all(cell_belongs(rr, cc) for rr, cc in cells):
                        continue
                    # از شمردن دوباره‌ی همان ۵تایی که قبلاً برای این تیم امتیاز گرفته جلوگیری می‌کنیم
                    cell_set = tuple(sorted(cells))
                    sig = (team, dr, dc, cell_set)
                    if sig in self._used_sequence_cells:
                        continue
                    # اجازه نمی‌دهیم دو توالیِ این تیم بیش از یک خانه‌ی مشترک داشته باشند
                    overlap_ok = True
                    for existing in self._used_sequence_cells:
                        if existing[0] != team:
                            continue
                        existing_cells = set(existing[3])
                        if len(existing_cells & set(cells)) > 1:
                            overlap_ok = False
                            break
                    if not overlap_ok:
                        continue
                    self._used_sequence_cells.add(sig)
                    new_sequences += 1
        return new_sequences

    def handle_action(self, user_id: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_started or self.is_finished:
            return {"status": "error", "message": "بازی فعال نیست"}
        if self.get_current_turn_player() != user_id:
            return {"status": "error", "message": "نوبت شما نیست!"}

        if action != "place_chip":
            return {"status": "error", "message": "اکشن نامعتبر"}

        r, c = payload.get("row"), payload.get("col")
        card_id = payload.get("card_id")
        if not isinstance(r, int) or not isinstance(c, int) or not (0 <= r < 10 and 0 <= c < 10):
            return {"status": "error", "message": "خانه‌ی نامعتبر"}

        hand = self.hands.get(user_id, [])
        card = next((x for x in hand if x["id"] == card_id), None)
        if not card:
            return {"status": "error", "message": "کارت در دست شما نیست"}

        team = self.player_teams[user_id]
        cell = self.board[r][c]
        is_one_eyed_jack = card["rank"] == "J" and card["suit"] in self.ONE_EYED_JACK_SUITS
        is_two_eyed_jack = card["rank"] == "J" and card["suit"] in self.TWO_EYED_JACK_SUITS

        if is_one_eyed_jack:
            # برداشتن یک مهره‌ی حریف (نه مهره‌ی خودی و نه گوشه‌ی wild)
            if cell is None or cell == "W":
                return {"status": "error", "message": "این خانه مهره‌ای برای برداشتن ندارد"}
            if cell == f"team_{team}":
                return {"status": "error", "message": "نمی‌توانید مهره‌ی تیم خودتان را بردارید"}
            self.board[r][c] = None
        else:
            if cell is not None:
                return {"status": "error", "message": "این خانه قبلاً پر شده است"}
            if not is_two_eyed_jack:
                board_key = SEQUENCE_LAYOUT[r][c]
                if board_key == "W" or board_key != self._card_key(card):
                    return {"status": "error", "message": "این کارت با این خانه مطابقت ندارد"}
            self.board[r][c] = f"team_{team}"

        self._refill_hand(user_id, card)

        if not is_one_eyed_jack:
            gained = self._check_new_sequences(team)
            if gained:
                self.team_sequences[team] = self.team_sequences.get(team, 0) + gained
                if self.team_sequences[team] >= self.SEQUENCES_TO_WIN:
                    self.is_finished = True
                    self.winner = user_id
                    for p in self.players:
                        if self.player_teams[p] == team:
                            self.player_info[p]["score"] = self.team_sequences[team]

        if not self.is_finished:
            self.advance_turn()

        return {
            "status": "ok",
            "board": self.board,
            "team_sequences": self.team_sequences,
            "next_turn": self.get_current_turn_player(),
            "is_finished": self.is_finished,
            "winner": self.winner,
        }

    def get_player_view(self, user_id: str) -> Dict[str, Any]:
        data = super().get_player_view(user_id)
        data.update({
            "board": self.board,
            "my_team": self.player_teams.get(user_id, 0),
            "my_hand": self.hands.get(user_id, []),
            "team_sequences": getattr(self, "team_sequences", {}),
        })
        return data

    def get_bot_action(self, user_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        hand = self.hands.get(user_id, [])
        if not hand:
            return None
        team = self.player_teams.get(user_id, 0)

        def cell_owner(r, c):
            return self.board[r][c]

        # اول تلاش برای گذاشتن یک مهره‌ی معمولی/جوکر دوچشم در یک خانه‌ی خالی
        for card in hand:
            is_two_eyed = card["rank"] == "J" and card["suit"] in self.TWO_EYED_JACK_SUITS
            is_one_eyed = card["rank"] == "J" and card["suit"] in self.ONE_EYED_JACK_SUITS
            if is_one_eyed:
                continue  # اول انواع دیگر را امتحان می‌کنیم
            if is_two_eyed:
                for r in range(10):
                    for c in range(10):
                        if cell_owner(r, c) is None:
                            return "place_chip", {"card_id": card["id"], "row": r, "col": c}
                continue
            key = self._card_key(card)
            for r in range(10):
                for c in range(10):
                    if SEQUENCE_LAYOUT[r][c] == key and cell_owner(r, c) is None:
                        return "place_chip", {"card_id": card["id"], "row": r, "col": c}

        # اگر هیچ حرکت عادی ممکن نبود، جوکر یک‌چشم را برای برداشتن یک مهره‌ی حریف امتحان کن
        for card in hand:
            if card["rank"] == "J" and card["suit"] in self.ONE_EYED_JACK_SUITS:
                for r in range(10):
                    for c in range(10):
                        owner = cell_owner(r, c)
                        if owner is not None and owner != "W" and owner != f"team_{team}":
                            return "place_chip", {"card_id": card["id"], "row": r, "col": c}
        return None

# ==================== ۳. حکم کامل (ترکیب کد اول درون ساختار استاندارد) ====================
class HokmCard:
    def __init__(self, suit_char: str, rank: int):
        self.suit_char = suit_char
        self.rank = rank  # 2 تا 14

    def value(self, lead_suit: Optional[str], trump_suit: Optional[str]) -> int:
        if self.suit_char == trump_suit:
            return self.rank + 100
        elif self.suit_char == lead_suit:
            return self.rank + 10
        return 0

    def to_dict(self):
        ranks = {11: "J", 12: "Q", 13: "K", 14: "A"}
        return {"suit": self.suit_char, "rank": self.rank, "display": f"{self.suit_char}{ranks.get(self.rank, str(self.rank))}"}

class HokmGame(BaseGame):
    def __init__(self, room_id: str, config: Dict[str, Any] = None):
        cfg = {"min_players": 4, "max_players": 4, "target_points": 7}
        if config: cfg.update(config)
        super().__init__(room_id, "hokm", cfg)
        self.hands: Dict[str, List[HokmCard]] = {}
        self.hakim: Optional[str] = None
        self.trump_suit: Optional[str] = None
        self.team_scores = {0: 0, 1: 0} # تیم ۰ (بازیکنان ۰ و ۲) و تیم ۱ (بازیکنان ۱ و ۳)
        self.tricks_won = {0: 0, 1: 0}
        self.current_trick: List[Tuple[str, HokmCard]] = []
        self.lead_suit: Optional[str] = None
        self.deal_phase = 0 # 0: تعیین حکم، 1: بازی

    def start_game(self) -> bool:
        if len(self.players) != 4: return False
        self.is_started = True
        self.hakim = self.players[0]
        self._deal_initial()
        self.current_turn_index = self.players.index(self.hakim)
        return True

    def _deal_initial(self):
        deck = [HokmCard(s, r) for s in SUITS for r in range(2, 15)]
        random.shuffle(deck)
        for p in self.players:
            self.hands[p] = deck[:5]
            del deck[:5]
        self.deck_remaining = deck
        self.deal_phase = 0

    def declare_trump(self, user_id: str, suit: str) -> Dict[str, Any]:
        if user_id != self.hakim or self.deal_phase != 0 or suit not in SUITS:
            return {"status": "error", "message": "مجوز تعیین حکم را ندارید."}
        self.trump_suit = suit
        # پخش مابقی کارت‌ها
        deck = self.deck_remaining
        for p in self.players:
            self.hands[p].extend(deck[:8])
            del deck[:8]
        self.deal_phase = 1
        return {"status": "ok", "trump_suit": suit}

    def handle_action(self, user_id: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if action == "declare_trump":
            return self.declare_trump(user_id, payload.get("suit"))
        elif action == "play_card":
            if self.deal_phase != 1 or self.get_current_turn_player() != user_id:
                return {"status": "error", "message": "نوبت شما نیست یا بازی در مرحله تعیین حکم است."}
            
            card_rank = payload.get("rank")
            card_suit = payload.get("suit")
            hand = self.hands.get(user_id, [])
            target = next((c for c in hand if c.rank == card_rank and c.suit_char == card_suit), None)
            
            if not target:
                return {"status": "error", "message": "کارت در دست شما نیست."}
            
            if len(self.current_trick) == 0:
                self.lead_suit = target.suit_char
            else:
                if target.suit_char != self.lead_suit and any(c.suit_char == self.lead_suit for c in hand):
                    return {"status": "error", "message": f"باید خال زمینه ({self.lead_suit}) را بازی کنید."}

            hand.remove(target)
            self.current_trick.append((user_id, target))

            if len(self.current_trick) == 4:
                # ارزیابی برنده دست
                best_player = self.current_trick[0][0]
                best_val = self.current_trick[0][1].value(self.lead_suit, self.trump_suit)
                for pid, c in self.current_trick[1:]:
                    val = c.value(self.lead_suit, self.trump_suit)
                    if val > best_val:
                        best_val = val
                        best_player = pid
                
                team = self.players.index(best_player) % 2
                self.tricks_won[team] += 1
                self.current_trick = []
                self.lead_suit = None
                self.current_turn_index = self.players.index(best_player)
                
                if self.tricks_won[team] >= 7:
                    self.team_scores[team] += 1
                    self.is_finished = True
            else:
                self.advance_turn()

            return {"status": "ok", "next_turn": self.get_current_turn_player()}
        return {"status": "error", "message": "اکشن نامعتبر"}

    def get_player_view(self, user_id: str) -> Dict[str, Any]:
        data = super().get_player_view(user_id)
        data.update({
            "hakim": self.hakim,
            "trump_suit": self.trump_suit,
            "my_hand": [c.to_dict() for c in self.hands.get(user_id, [])],
            "trick_cards": [{"player": p, "card": c.to_dict()} for p, c in self.current_trick],
            "tricks_won": self.tricks_won,
            "deal_phase": self.deal_phase
        })
        return data

    def get_bot_action(self, user_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        if self.deal_phase == 0:
            if user_id == self.hakim:
                return "declare_trump", {"suit": random.choice(SUITS)}
            return None
        hand = self.hands.get(user_id, [])
        if not hand:
            return None
        if self.lead_suit:
            same_suit = [c for c in hand if c.suit_char == self.lead_suit]
            chosen = same_suit[0] if same_suit else hand[0]
        else:
            chosen = hand[0]
        return "play_card", {"suit": chosen.suit_char, "rank": chosen.rank}

class GameEngineFactory:
    REGISTRY = {"pasur": PasurGame, "sequence": SequenceGame, "hokm": HokmGame}

    @classmethod
    def create_game(cls, game_type: str, room_id: str, config: Dict[str, Any] = None) -> BaseGame:
        engine_cls = cls.REGISTRY.get(game_type, PasurGame)
        return engine_cls(room_id, config)
