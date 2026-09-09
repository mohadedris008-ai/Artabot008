import os
import json
import time
import random
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from enum import Enum

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

# ایمپورت‌های دیتابیس و امنیت (فرض بر وجود فایل‌های مربوطه)
from database import get_db, User, Transaction
from telegram_auth import verify_telegram_init_data

app = FastAPI(title="Telegram Game Hub - Hokm Platform")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== 1. موتور بازی حکم (برگرفته از کد اول و بهینه‌سازی شده) ====================

class Suit(Enum):
    SPADES = "♠"
    HEARTS = "♥️"
    DIAMONDS = "♦️"
    CLUBS = "♣"

class Card:
    def __init__(self, suit: Suit, rank: int):
        self.suit = suit
        self.rank = rank  # 2 تا 14

    def value(self, lead_suit: Optional[Suit], trump_suit: Optional[Suit]) -> int:
        if self.suit == trump_suit:
            return self.rank + 100
        elif self.suit == lead_suit:
            return self.rank + 10
        return 0

    def to_dict(self):
        ranks = {11: "J", 12: "Q", 13: "K", 14: "A"}
        return {
            "suit": self.suit.name,
            "display_suit": self.suit.value,
            "rank": self.rank,
            "display_rank": ranks.get(self.rank, str(self.rank))
        }

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

class Player:
    def __init__(self, player_id: int, user_id: str, name: str, team_id: int):
        self.player_id = player_id  # 0 تا 3
        self.user_id = user_id      # شناسه تلگرام
        self.name = name
        self.team_id = team_id      # 1 یا 2
        self.hand: List[Card] = []

    def sort_hand(self):
        self.hand.sort(key=lambda c: (c.suit.name, c.rank), reverse=True)

    def remove_card(self, card: Card):
        self.hand = [c for c in self.hand if not (c.suit == card.suit and c.rank == card.rank)]

class HokmGameInstance:
    def __init__(self, room_id: str, player_data: List[Dict[str, str]]):
        if len(player_data) != 4:
            raise ValueError("بازی حکم حتماً به ۴ بازیکن نیاز دارد.")
        
        self.room_id = room_id
        self.players: List[Player] = [
            Player(0, player_data[0]["user_id"], player_data[0]["name"], 1),
            Player(1, player_data[1]["user_id"], player_data[1]["name"], 2),
            Player(2, player_data[2]["user_id"], player_data[2]["name"], 1),
            Player(3, player_data[3]["user_id"], player_data[3]["name"], 2),
        ]
        
        self.team_scores = {1: 0, 2: 0}
        self.hakem_id: int = 0
        self.deck = Deck()
        self.trump_suit: Optional[Suit] = None
        self.tricks_won = {1: 0, 2: 0}
        self.current_trick: List[Tuple[int, Card]] = []
        self.lead_suit: Optional[Suit] = None
        self.turn_id: Optional[int] = None
        self.deal_phase = 0  # 0: شروع، 1: تعیین حکم، 2: در جریان بازی
        self.start_new_round()

    def get_player_index(self, user_id: str) -> Optional[int]:
        for p in self.players:
            if p.user_id == user_id:
                return p.player_id
        return None

    def start_new_round(self):
        self.deck.reset()
        self.deck.shuffle()
        self.trump_suit = None
        self.tricks_won = {1: 0, 2: 0}
        self.current_trick = []
        self.lead_suit = None
        
        for player in self.players:
            player.hand = []

        # پخش ۵ کارت اول به حاکم و سایرین
        for i in range(4):
            p_id = (self.hakem_id + i) % 4
            self.players[p_id].hand.extend(self.deck.deal(5))
            self.players[p_id].sort_hand()

        self.deal_phase = 1
        self.turn_id = self.hakem_id

    def set_trump(self, user_id: str, suit_name: str) -> bool:
        p_id = self.get_player_index(user_id)
        if p_id != self.hakem_id or self.deal_phase != 1:
            return False
            
        try:
            self.trump_suit = Suit[suit_name.upper()]
        except KeyError:
            return False

        # پخش مابقی کارت‌ها
        for _ in range(2):
            for i in range(4):
                idx = (self.hakem_id + i) % 4
                self.players[idx].hand.extend(self.deck.deal(4))
                self.players[idx].sort_hand()

        self.deal_phase = 2
        self.turn_id = self.hakem_id
        return True

    def play_card(self, user_id: str, card_suit: str, card_rank: int) -> Dict:
        p_id = self.get_player_index(user_id)
        if self.deal_phase != 2 or p_id != self.turn_id:
            return {"status": "error", "message": "نوبت شما نیست یا بازی آماده نیست."}

        player = self.players[p_id]
        target_card = next((c for c in player.hand if c.suit.name == card_suit.upper() and c.rank == card_rank), None)

        if not target_card:
            return {"status": "error", "message": "این کارت در دست شما نیست."}

        if len(self.current_trick) == 0:
            self.lead_suit = target_card.suit
        else:
            if target_card.suit != self.lead_suit:
                has_lead = any(c.suit == self.lead_suit for c in player.hand)
                if has_lead:
                    return {"status": "error", "message": f"باید خال زمینه ({self.lead_suit.value}) را بازی کنید!"}

        player.remove_card(target_card)
        self.current_trick.append((p_id, target_card))

        round_finished = False
        round_result = None

        if len(self.current_trick) == 4:
            winner_id = self._evaluate_trick()
            winner_team = self.players[winner_id].team_id
            self.tricks_won[winner_team] += 1

            trick_cards_data = [(p, c.to_dict()) for p, c in self.current_trick]
            self.current_trick = []
            self.lead_suit = None
            self.turn_id = winner_id

            if self.tricks_won[winner_team] == 7:
                round_finished = True
                round_result = self._end_round(winner_team)
            
            return {
                "status": "success",
                "action": "TRICK_WON",
                "winner_id": winner_id,
                "winner_team": winner_team,
                "trick_cards": trick_cards_data,
                "tricks_won": self.tricks_won,
                "round_finished": round_finished,
                "round_result": round_result
            }

        self.turn_id = (self.turn_id + 1) % 4
        return {"status": "success", "action": "CARD_PLAYED", "next_turn": self.turn_id}

    def _evaluate_trick(self) -> int:
        best_player_id = self.current_trick[0][0]
        highest_card = self.current_trick[0][1]

        for p_id, card in self.current_trick[1:]:
            if card.value(self.lead_suit, self.trump_suit) > highest_card.value(self.lead_suit, self.trump_suit):
                highest_card = card
                best_player_id = p_id
        return best_player_id

    def _end_round(self, winning_team: int) -> Dict:
        losing_team = 3 - winning_team
        hakem_team = self.players[self.hakem_id].team_id
        score_awarded = 1
        is_kot = False

        if self.tricks_won[losing_team] == 0:
            is_kot = True
            score_awarded = 3 if winning_team != hakem_team else 2

        self.team_scores[winning_team] += score_awarded
        if winning_team != hakem_team:
            self.hakem_id = (self.hakem_id + 1) % 4

        return {
            "winning_team": winning_team,
            "score_awarded": score_awarded,
            "is_kot": is_kot,
            "team_scores": self.team_scores,
            "game_over": any(s >= 7 for s in self.team_scores.values())
        }

# ==================== 2. مدیریت اتصال وب‌سوکت و بازی‌ها ====================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.games: Dict[str, HokmGameInstance] = {}

    async def connect(self, room_id: str, user_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, room_id: str, websocket: WebSocket):
        if room_id in self.active_connections:
            if websocket in self.active_connections[room_id]:
                self.active_connections[room_id].remove(websocket)

    async def broadcast(self, room_id: str, message: dict):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_text(json.dumps(message, ensure_ascii=False))

ws_manager = ConnectionManager()

# ==================== 3. روت‌های WebSocket و REST ====================

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/")
async def get_index():
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    return HTMLResponse("<h1>سرور بازی حکم فعال است! فایل UI یافت نشد.</h1>")

@app.websocket("/ws/game/{room_id}/{user_id}")
async def game_websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await ws_manager.connect(room_id, user_id, websocket)
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            action = data.get("action")

            # ایجاد خودکار بازی اگر ۴ نفر کامل باشند
            if room_id not in ws_manager.games and action == "START_GAME":
                players_list = data.get("players", []) # فرض بر ارسال لیست ۴ نفره بازیکنان از کلاینت
                if len(players_list) == 4:
                    ws_manager.games[room_id] = HokmGameInstance(room_id, players_list)

            game = ws_manager.games.get(room_id)

            if action == "SET_TRUMP" and game:
                suit = data.get("suit")
                success = game.set_trump(user_id, suit)
                if success:
                    await ws_manager.broadcast(room_id, {
                        "type": "TRUMP_DECLARED",
                        "trump_suit": game.trump_suit.name,
                        "next_turn": game.turn_id
                    })

            elif action == "PLAY_CARD" and game:
                card_data = data.get("card", {})
                res = game.play_card(user_id, card_data.get("suit"), card_data.get("rank"))
                await ws_manager.broadcast(room_id, {"type": "GAME_ACTION_RESULT", "result": res})

            elif action == "SEND_EMOJI":
                await ws_manager.broadcast(room_id, {"type": "LIVE_EMOJI", "from_user": user_id, "emoji": data.get("emoji")})

    except WebSocketDisconnect:
        ws_manager.disconnect(room_id, websocket)
        await ws_manager.broadcast(room_id, {"type": "PLAYER_DISCONNECTED", "user_id": user_id})

# REST API های اقتصاد و احراز هویت
class AuthPayload(BaseModel):
    initData: str
    referrer_id: Optional[int] = None

@app.post("/api/user/sync")
async def sync_user(payload: AuthPayload, db: Session = Depends(get_db)):
    tg_user = verify_telegram_init_data(payload.initData)
    if not tg_user:
        raise HTTPException(status_code=401, detail="Invalid Telegram Data")

    user = db.query(User).filter(User.id == tg_user["id"]).first()
    if not user:
        user = User(id=tg_user["id"], first_name=tg_user.get("first_name", "Gamer"), username=tg_user.get("username"))
        db.add(user)
        db.commit()

    return {"id": user.id, "coins": user.coins, "gems": user.gems, "card_back_skin": user.card_back_skin}
