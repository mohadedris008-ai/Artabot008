"""
Combined Game Logic Engine for Telegram Mini App Platform
Includes: Pasur, Sequence, and fully-featured Hokm Game Engine.
"""

import random
import time
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
    deck = []
    card_id = 0
    for s in SUITS:
        for r in RANKS:
            card_id += 1
            deck.append({
                "id": f"{s}_{r}_{card_id}",
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
        return True

    def get_current_turn_player(self) -> Optional[str]:
        if not self.players:
            return None
        return self.players[self.current_turn_index % len(self.players)]

    def advance_turn(self):
        if self.players:
            self.current_turn_index = (self.current_turn_index + 1) % len(self.players)
            self.turn_deadline = time.time() + 30

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

    def start_game(self) -> bool:
        if len(self.players) < 2: return False
        self.deck = create_standard_deck() + create_standard_deck()
        random.shuffle(self.deck)
        for i, p in enumerate(self.players):
            self.player_teams[p] = i % 2
            self.hands[p] = [self.deck.pop() for _ in range(6)]
        self.is_started = True
        return True

    def handle_action(self, user_id: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_started or self.is_finished:
            return {"status": "error", "message": "بازی فعال نیست"}
        if self.get_current_turn_player() != user_id:
            return {"status": "error", "message": "نوبت شما نیست!"}

        if action == "place_chip":
            r, c = payload.get("row"), payload.get("col")
            card_id = payload.get("card_id")
            hand = self.hands.get(user_id, [])
            card = next((x for x in hand if x["id"] == card_id), None)
            if not card: return {"status": "error", "message": "کارت نامعتبر"}

            team = self.player_teams[user_id]
            self.board[r][c] = f"team_{team}"
            hand.remove(card)
            if self.deck: hand.append(self.deck.pop())
            self.advance_turn()
            return {"status": "ok", "board": self.board, "next_turn": self.get_current_turn_player()}
        return {"status": "error", "message": "اکشن نامعتبر"}

    def get_player_view(self, user_id: str) -> Dict[str, Any]:
        data = super().get_player_view(user_id)
        data.update({"board": self.board, "my_team": self.player_teams.get(user_id, 0), "my_hand": self.hands.get(user_id, [])})
        return data

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

class GameEngineFactory:
    REGISTRY = {"pasur": PasurGame, "sequence": SequenceGame, "hokm": HokmGame}

    @classmethod
    def create_game(cls, game_type: str, room_id: str, config: Dict[str, Any] = None) -> BaseGame:
        engine_cls = cls.REGISTRY.get(game_type, PasurGame)
        return engine_cls(room_id, config)
