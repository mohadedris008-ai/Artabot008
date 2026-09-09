import random
from enum import Enum
from typing import List, Dict, Optional, Tuple

# ---------------------------------------------------------
# ۱. تعاریف اولیه و انوم‌ها (Enums)
# ---------------------------------------------------------
class Suit(Enum):
    SPADES = "♠"    # پیک
    HEARTS = "♥️"    # دل
    DIAMONDS = "♦️"  # خشت
    CLUBS = "♣"     # گشنیز

class Card:
    def __init__(self, suit: Suit, rank: int):
        self.suit = suit
        self.rank = rank  # 2 تا 14 (11: سرباز، 12: بی‌بی، 13: شاه، 14: تک/آس)

    def value(self, lead_suit: Suit, trump_suit: Optional[Suit]) -> int:
        """محاسبه ارزش کارت بر اساس خال حکم و خال زمین"""
        if self.suit == trump_suit:
            return self.rank + 100  # کارت‌های حکم همیشه از همه کارت‌ها قوی‌ترند
        elif self.suit == lead_suit:
            return self.rank + 10   # کارت‌های خال زمینه
        return 0                   # سایر خال‌ها ارزش برنده شدن ندارند

    def __repr__(self):
        ranks = {11: "J", 12: "Q", 13: "K", 14: "A"}
        rank_str = ranks.get(self.rank, str(self.rank))
        return f"{self.suit.value}{rank_str}"

    def to_dict(self):
        return {"suit": self.suit.name, "rank": self.rank, "display": str(self)}

# ---------------------------------------------------------
# ۲. کلاس دست‌ورزی با پاسور (Deck)
# ---------------------------------------------------------
class Deck:
    def __init__(self):
        self.cards: List[Card] = []
        self.reset()

    def reset(self):
        self.cards = [Card(suit, rank) for suit in Suit for rank in range(2, 15)]

    def shuffle(self):
        random.shuffle(self.cards)

    def deal(self, count: int) -> List[Card]:
        hand = self.cards[:count]
        self.cards = self.cards[count:]
        return hand

# ---------------------------------------------------------
# ۳. کلاس بازیکن (Player)
# ---------------------------------------------------------
class Player:
    def __init__(self, player_id: int, name: str, team_id: int):
        self.player_id = player_id
        self.name = name
        self.team_id = team_id  # 1 یا 2 (بازیکنان ۰ و ۲ تیم ۱، بازیکنان ۱ و ۳ تیم ۲)
        self.hand: List[Card] = []

    def sort_hand(self):
        """مرتب‌سازی کارت‌های دست بر اساس خال و ارزش"""
        self.hand.sort(key=lambda c: (c.suit.name, c.rank), reverse=True)

    def remove_card(self, card: Card):
        self.hand = [c for c in self.hand if not (c.suit == card.suit and c.rank == card.rank)]

# ---------------------------------------------------------
# ۴. موتور اصلی مدیریت بازی حکم (HokmGame)
# ---------------------------------------------------------
class HokmGame:
    def __init__(self, player_names: List[str]):
        if len(player_names) != 4:
            raise ValueError("بازی حکم حتماً نیاز به ۴ بازیکن دارد.")

        self.players: List[Player] = [
            Player(0, player_names[0], team_id=1),
            Player(1, player_names[1], team_id=2),
            Player(2, player_names[2], team_id=1),
            Player(3, player_names[3], team_id=2)
        ]
        
        self.team_scores = {1: 0, 2: 0} # امتیاز کلی بازی (مثلاً رسیدن به ۷)
        self.hakem_id: Optional[int] = None
        self.deck = Deck()
        
        # وضعیت‌های راند فعلی
        self.trump_suit: Optional[Suit] = None
        self.tricks_won = {1: 0, 2: 0}   # تعداد دست‌های برده شده در راند فعلی (تا ۷)
        self.current_trick: List[Tuple[int, Card]] = [] # کارت‌های روی میز [(player_id, Card)]
        self.lead_suit: Optional[Suit] = None
        self.turn_id: Optional[int] = None
        self.deal_phase = 0              # مراحل پخش کارت: 0 (تعیین حکم)، 1 (تکمیل دست)، 2 (در حال بازی)

    # ---------------------------------------------------------
    # تعیین حاکم اولیه (با تک کشیدن)
    # ---------------------------------------------------------
    def determine_first_hakem(self) -> int:
        """بر زدن و تک کشیدن برای تعیین حاکم اولیه"""
        temp_deck = Deck()
        temp_deck.shuffle()
        
        for card in temp_deck.cards:
            if card.rank == 14: # اولین تک (آس)
                # حاکم بر اساس نمایه‌ای که تک آمده تعیین می‌شود
                idx = temp_deck.cards.index(card) % 4
                self.hakem_id = idx
                self.turn_id = idx
                return self.hakem_id
        
        self.hakem_id = 0
        self.turn_id = 0
        return 0

    # ---------------------------------------------------------
    # شروع راند جدید و پخش کارت مرحله اول (۵ برگ به حاکم)
    # ---------------------------------------------------------
    def start_new_round(self):
        """آماده‌سازی راند و پخش ۵ برگ اول به همه"""
        self.deck.reset()
        self.deck.shuffle()
        self.trump_suit = None
        self.tricks_won = {1: 0, 2: 0}
        self.current_trick = []
        self.lead_suit = None
        
        for player in self.players:
            player.hand = []

        # پخش ۵ برگ اول
        for i in range(4):
            p_id = (self.hakem_id + i) % 4
            self.players[p_id].hand.extend(self.deck.deal(5))
            self.players[p_id].sort_hand()

        self.deal_phase = 1 # منتظر تعیین حکم توسط حاکم

    # ---------------------------------------------------------
    # انتخاب حکم توسط حاکم
    # ---------------------------------------------------------
    def set_trump(self, suit_name: str) -> bool:
        """تعیین خال حکم توسط حاکم"""
        if self.deal_phase != 1:
            return False
            
        try:
            self.trump_suit = Suit[suit_name.upper()]
        except KeyError:
            return False

        # پخش مابقی کارت‌ها (دو مرحله ۴ تایی برای هر بازیکن)
        for _ in range(2):
            for i in range(4):
                p_id = (self.hakem_id + i) % 4
                self.players[p_id].hand.extend(self.deck.deal(4))
                self.players[p_id].sort_hand()

        self.deal_phase = 2
        self.turn_id = self.hakem_id # حاکم بازی را شروع می‌کند
        return True

    # ---------------------------------------------------------
    # منطق بازی کردن یک کارت توسط بازیکن
    # ---------------------------------------------------------
    def play_card(self, player_id: int, card_suit: str, card_rank: int) -> Dict:
        """بررسی صحت و بازی کردن یک کارت"""
        if self.deal_phase != 2:
            return {"status": "error", "message": "بازی هنوز شروع نشده است."}

        if player_id != self.turn_id:
            return {"status": "error", "message": "نوبت شما نیست!"}

        player = self.players[player_id]
        target_card = next((c for c in player.hand if c.suit.name == card_suit.upper() and c.rank == card_rank), None)

        if not target_card:
            return {"status": "error", "message": "این کارت در دست شما وجود ندارد."}

        # اعتبارسنجی رد کردن / خال زمینه (Follow Suit)
        if len(self.current_trick) == 0:
            self.lead_suit = target_card.suit
        else:
            if target_card.suit != self.lead_suit:
                # اگر کارت هم‌خال زمین را بازی نکرده، آیا اصلاً از خال زمینه دارد؟
                has_lead_suit = any(c.suit == self.lead_suit for c in player.hand)
                if has_lead_suit:
                    return {"status": "error", "message": f"شما باید خال زمینه ({self.lead_suit.value}) را بازی کنید!"}

        # ثبت کارت بازی شده
        player.remove_card(target_card)
        self.current_trick.append((player_id, target_card))

        # اگر دست کامل شد (۴ کارت روی میز آمد)
        if len(self.current_trick) == 4:
            winner_id = self._evaluate_trick()
            winner_team = self.players[winner_id].team_id
            self.tricks_won[winner_team] += 1

            trick_cards = self.current_trick
            self.current_trick = []
            self.lead_suit = None
            self.turn_id = winner_id # برنده دست، آغازکننده دست بعدی است

            # بررسی پایان راند (رسیدن یکی از تیم‌ها به ۷ دست)
            round_finished = False
            round_result = None
            if self.tricks_won[winner_team] == 7:
                round_finished = True
                round_result = self._end_round(winning_team=winner_team)

            return {
                "status": "success",
                "action": "TRICK_FINISHED",
                "winner_id": winner_id,
                "winner_team": winner_team,
                "trick_cards": trick_cards,
                "tricks_won": self.tricks_won,
                "round_finished": round_finished,
                "round_result": round_result
            }

        # چرخش نوبت به نفر بعدی
        self.turn_id = (self.turn_id + 1) % 4
        return {"status": "success", "action": "CARD_PLAYED", "next_turn": self.turn_id}

    # ---------------------------------------------------------
    # ارزیابی برنده دست (Trick Winner)
    # ---------------------------------------------------------
    def _evaluate_trick(self) -> int:
        """مشخص کردن قوی‌ترین کارت روی میز و بازگرداندن ID برنده"""
        winning_player_id = self.current_trick[0][0]
        highest_card = self.current_trick[0][1]

        for player_id, card in self.current_trick[1:]:
            current_best_val = highest_card.value(self.lead_suit, self.trump_suit)
            new_card_val = card.value(self.lead_suit, self.trump_suit)

            if new_card_val > current_best_val:
                highest_card = card
                winning_player_id = player_id

        return winning_player_id

    # ---------------------------------------------------------
    # محاسبه امتیازات نهایی راند (کوت، بامداد و تغییر حاکم)
    # ---------------------------------------------------------
    def _end_round(self, winning_team: int) -> Dict:
        losing_team = 3 - winning_team  # اگر برنده ۱ باشد بازنده ۲ است و بالعکس
        hakem_team = self.players[self.hakem_id].team_id

        score_awarded = 1
        is_kot = False
        is_hakem_kot = False

        # شرط کوت (اگر تیم بازنده هیچ دستی نبرده باشد: 0-7)
        if self.tricks_won[losing_team] == 0:
            is_kot = True
            if winning_team != hakem_team:
                score_awarded = 3  # حاکم کوت (تیم حاکم باخته و ۰ گرفته)
                is_hakem_kot = True
            else:
                score_awarded = 2  # کوت معمولی

        self.team_scores[winning_team] += score_awarded

        # تعیین حاکم برای دست بعدی
        old_hakem = self.hakem_id
        if winning_team != hakem_team:
            # اگر تیم حاکم ببازد، چرخش حاکم به نفر سمت چپ
            self.hakem_id = (self.hakem_id + 1) % 4

        return {
            "winning_team": winning_team,
            "score_awarded": score_awarded,
            "is_kot": is_kot,
            "is_hakem_kot": is_hakem_kot,
            "team_scores": self.team_scores,
            "old_hakem": old_hakem,
            "new_hakem": self.hakem_id,
            "game_over": any(score >= 7 for score in self.team_scores.values())
        }

# ---------------------------------------------------------
# ۵. بخش تست و اجرای مستقیم فایل
# ---------------------------------------------------------
if __name__ == "__main__":
    # ۱. ایجاد بازی ۴ نفره
    game = HokmGame(["علی", "رضا", "محمد", "امین"])

    # ۲. تعیین حاکم اولیه
    hakem_id = game.determine_first_hakem()
    print(f"حاکم اولیه مشخص شد: {game.players[hakem_id].name} (بازیکن شماره {hakem_id})")

    # ۳. شروع راند جدید و پخش ۵ برگ اول
    game.start_new_round()
    print(f"دست ۵ برگی حاکم: {game.players[hakem_id].hand}")

    # ۴. تعیین حکم توسط حاکم
    game.set_trump("SPADES") # حکم: پیک
    print(f"حکم تعیین شد: {game.trump_suit.value}")

    # ۵. نمونه بازی کردن اولین کارت توسط حاکم
    first_card = game.players[hakem_id].hand[0]
    res = game.play_card(hakem_id, first_card.suit.name, first_card.rank)
    print("نتیجه بازی کارت:", res)
