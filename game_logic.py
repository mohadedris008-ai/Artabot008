import random

SUITS = ['Spades', 'Hearts', 'Diamonds', 'Clubs']
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']

class Card:
    def __init__(self, suit, rank, is_joker=False, joker_type=None):
        self.suit = suit
        self.rank = rank
        self.is_joker = is_joker
        self.joker_type = joker_type  # 'TWO_EYED' (Place chip anywhere), 'ONE_EYED' (Remove opponent chip)

def create_deck(include_jokers=True):
    deck = []
    for suit in SUITS:
        for rank in RANKS:
            deck.append({"suit": suit, "rank": rank, "is_joker": False, "joker_type": None})
    
    if include_jokers:
        deck.append({"suit": "Joker", "rank": "BigJoker", "is_joker": True, "joker_type": "TWO_EYED"})
        deck.append({"suit": "Joker", "rank": "SmallJoker", "is_joker": True, "joker_type": "ONE_EYED"})
        
    random.shuffle(deck)
    return deck

class GameRoom:
    def __init__(self, room_id: str, game_type: str = "sequence"):
        self.room_id = room_id
        self.game_type = game_type
        self.players = []  # List of Telegram user_ids
        self.deck = create_deck(include_jokers=True)
        self.board_state = {}  # {pos: user_id}
        self.current_turn = 0
        self.is_started = False

    def add_player(self, user_id: int) -> bool:
        if len(self.players) < 4 and user_id not in self.players:
            self.players.append(user_id)
            if len(self.players) == 4:
                self.is_started = True
            return True
        return False

    def remove_player(self, user_id: int):
        if user_id in self.players:
            self.players.remove(user_id)

    def play_card(self, user_id: int, card: dict, position: str = None):
        if not self.players or self.players[self.current_turn] != user_id:
            return {"status": "error", "message": "Not your turn!"}
        
        # Sequence Joker Logics
        if card.get("is_joker"):
            if card.get("joker_type") == "TWO_EYED" and position:
                self.board_state[position] = user_id
            elif card.get("joker_type") == "ONE_EYED" and position:
                if position in self.board_state:
                    del self.board_state[position]

        self.current_turn = (self.current_turn + 1) % len(self.players)
        return {
            "status": "ok", 
            "next_turn": self.players[self.current_turn],
            "board_state": self.board_state
        }
