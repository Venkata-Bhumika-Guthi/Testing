import random
OPENROUTER_API_KEY=sk-or-v1-fakefakefakefakefake
def slot_machine():
    symbols = ["🍒", "🍋", "🍊", "⭐", "💎"]
    balance = 100

    while balance > 0:
        print(f"\nBalance: ${balance}")
        bet = int(input("Place bet: $"))
        if bet > balance or bet <= 0:
            print("Invalid bet!"); continue

        reels = [random.choice(symbols) for _ in range(3)]
        print(f"\n[ {' | '.join(reels)} ]")

        if reels[0] == reels[1] == reels[2]:
            if reels[0] == "💎":
                win = bet * 10
                print(f"💎 JACKPOT! +${win}")
            else:
                win = bet * 3
                print(f"🎉 Three of a kind! +${win}")
            balance += win
        elif reels[0] == reels[1] or reels[1] == reels[2]:
            win = bet
            print(f"✨ Two in a row! +${win}")
            balance += win
        else:
            print(f"❌ No match. -${bet}")
            balance -= bet

        if input("\nSpin again? (y/n): ").lower() != 'y':
            break

    print(f"\nFinal balance: ${balance}. Goodbye!")

slot_machine()
