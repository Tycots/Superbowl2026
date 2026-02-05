from algopy import ARC4Contract, UInt64, arc4, Global, Txn, gtxn, itxn, BoxMap, Account

class VoiSuperBowlWhaleMarket(ARC4Contract):
    def __init__(self) -> None:
        # User Balances (Stored in Boxes)
        self.balances_sea = BoxMap(Account, UInt64)
        self.balances_pat = BoxMap(Account, UInt64)
        
        # Market State
        self.total_sea_sold = UInt64(0)
        self.total_pat_sold = UInt64(0)
        self.is_resolved = False
        self.market_paused = False
        self.winner = UInt64(0) # 1 = Seahawks, 2 = Patriots
        
        # Configuration (MicroVoi: 1,000,000 = 1 VOI)
        self.base_price = UInt64(510_000)      # 0.51 VOI (Creates 2% House Spread)
        self.skew_sensitivity = UInt64(10_000)  # Price +0.01 for every 10k share lead
        self.max_bet_voi = UInt64(100_000_000_000) # Max 100,000 VOI per transaction

    @arc4.abimethod
    def toggle_pause(self) -> None:
        """Admin emergency stop."""
        assert Txn.sender == Global.creator_address
        self.market_paused = not self.market_paused

    @arc4.abimethod(readonly=True)
    def get_price(self, want_sea: arc4.Bool) -> UInt64:
        """Calculates current ticket price based on market skew."""
        is_sea = want_sea.native
        if is_sea:
            if self.total_sea_sold > self.total_pat_sold:
                lead = self.total_sea_sold - self.total_pat_sold
                # Price increases as demand for SEA increases
                return self.base_price + (lead // self.skew_sensitivity * 10_000)
            return self.base_price
        else:
            if self.total_pat_sold > self.total_sea_sold:
                lead = self.total_pat_sold - self.total_sea_sold
                return self.base_price + (lead // self.skew_sensitivity * 10_000)
            return self.base_price

    @arc4.abimethod
    def buy_shares(self, payment: gtxn.PaymentTransaction, want_sea: arc4.Bool) -> None:
        """Allows users to buy tickets. Ensures house is always fully collateralized."""
        assert not self.is_resolved, "Market already ended"
        assert not self.market_paused, "Market is currently paused"
        assert payment.amount <= self.max_bet_voi, "Trade exceeds Max Bet limit"
        assert payment.receiver == Global.current_application_address, "Wrong receiver"
        
        is_sea = want_sea.native
        price = self.get_price(want_sea)
        
        # Calculate shares (Payment / Price)
        shares = (payment.amount * 1_000_000) // price
        assert shares > 0, "Payment too small"
        
        if is_sea:
            self.total_sea_sold += shares
            self.balances_sea[Txn.sender] = self.balances_sea.get(Txn.sender, default=UInt64(0)) + shares
        else:
            self.total_pat_sold += shares
            self.balances_pat[Txn.sender] = self.balances_pat.get(Txn.sender, default=UInt64(0)) + shares

    @arc4.abimethod
    def resolve_market(self, winner_code: UInt64) -> None:
        """Admin calls this to settle the game. 1=SEA, 2=PAT."""
        assert Txn.sender == Global.creator_address
        assert not self.is_resolved
        self.is_resolved = True
        self.winner = winner_code

    @arc4.abimethod
    def withdraw_house_profit(self) -> None:
        """
        Creator withdraws the 'Spread' and any remaining seed principal.
        Ensures winners can always be paid 1.00 VOI per share first.
        """
        assert Txn.sender == Global.creator_address
        assert self.is_resolved
        
        # Payout liability = 1 VOI per winning share held by traders
        winning_shares = self.total_sea_sold if self.winner == 1 else self.total_pat_sold
        reserve_required = winning_shares * 1_000_000
        
        # Everything above the winning payout reserve belongs to you
        total_balance = Global.current_application_address.balance
        assert total_balance >= reserve_required, "Math error: Insufficient reserves"
        
        profit = total_balance - reserve_required
        if profit > 0:
            itxn.Payment(receiver=Txn.sender, amount=profit, fee=0).submit()

    @arc4.abimethod
    def claim_winnings(self) -> None:
        """Users call this after resolution to get their 1 VOI per share."""
        assert self.is_resolved
        
        if self.winner == 1:
            shares = self.balances_sea.get(Txn.sender, default=UInt64(0))
            self.balances_sea[Txn.sender] = UInt64(0)
        else:
            shares = self.balances_pat.get(Txn.sender, default=UInt64(0))
            self.balances_pat[Txn.sender] = UInt64(0)
            
        assert shares > 0, "No winning shares found"
        
        # Pay out 1.00 VOI per winning share
        itxn.Payment(receiver=Txn.sender, amount=shares, fee=0).submit()