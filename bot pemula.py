import asyncio, websockets, json, time, os
import pandas as pd
from tabulate import tabulate

# --- KONFIGURASI PRO ANTI-MANIPULASI ---
SYMBOL = "BTCUSDT"
IMBALANCE_THRESHOLD = 0.7  # Diperketat untuk menghindari spoofing ringan
TP_PERCENT = 0.003         # Target 0.3% (Memberi ruang lebih untuk profit)
SL_PERCENT = 0.0015        # Stop Loss 0.15% (Rasio 2:1 agar NET P/L cepat pulih)
FEE_MAKER = 0.0002 
EMA_PERIOD = 200

# Fitur Baru: Anti-Absorption
MIN_PRICE_MOVE_THRESHOLD = 0.00005 # Harga harus bergerak minimal 0.005% setelah imbalance kuat

# ----------------- STATE -----------------
active_position = None 
price_history = []
volume_history = [] # Menyimpan data volume untuk cek efisiensi
stats = {"trades": 0, "wins": 0, "losses": 0, "net_pl": 0.0, "post_only_rejects": 0}

def calculate_ema(prices, period):
    if len(prices) < period: return None
    return pd.Series(prices).ewm(span=period, adjust=False).mean().iloc[-1]

def update_backtest(signal, entry, exit, success=True):
    global stats
    if not success:
        stats["post_only_rejects"] += 1
        return
    stats["trades"] += 1
    gross = (exit - entry) if signal == "LONG" else (entry - exit)
    fee = (entry + exit) * FEE_MAKER
    net = gross - fee
    stats["net_pl"] += net
    if net > 0: stats["wins"] += 1
    else: stats["losses"] += 1

async def main():
    global active_position, price_history, volume_history
    url = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@depth20@100ms"
    
    async with websockets.connect(url) as ws:
        while True:
            try:
                raw_data = await ws.recv()
                data = json.loads(raw_data)
                
                best_bid = float(data['bids'][0][0])
                best_ask = float(data['asks'][0][0])
                mid_price = (best_bid + best_ask) / 2
                
                price_history.append(mid_price)
                if len(price_history) > 1000: price_history.pop(0)
                ema = calculate_ema(price_history, EMA_PERIOD)

                # Orderbook Deep Analysis
                bid_vol = sum(float(qty) for p, qty in data['bids'])
                ask_vol = sum(float(qty) for p, qty in data['asks'])
                imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)

                # ----------------- LOGIKA ANTI-MANIPULASI -----------------
                
                # 1. MONITOR POSISI (Ditambah Fitur Force Close jika Trend Berbalik)
                if active_position:
                    pos = active_position
                    is_exit = False
                    
                    # Exit jika kena TP/SL
                    if pos['signal'] == "LONG":
                        if best_ask >= pos['tp']: is_exit = True
                        elif best_bid <= pos['sl']: is_exit = True
                        # EXTRA: Force Close jika harga mendadak drop jauh di bawah EMA
                        elif mid_price < ema * 0.9995: is_exit = True 
                    else: # SHORT
                        if best_bid <= pos['tp']: is_exit = True
                        elif best_ask >= pos['sl']: is_exit = True
                        elif mid_price > ema * 1.0005: is_exit = True

                    if is_exit:
                        update_backtest(pos['signal'], pos['entry'], mid_price)
                        active_position = None

                # 2. CARI ENTRI DENGAN FILTER EFISIENSI (ANTI-ABSORPTION)
                elif ema and len(price_history) > 10:
                    prev_price = price_history[-10] # Bandingkan dengan harga 1 detik lalu
                    price_move = (mid_price - prev_price) / prev_price

                    # LONG: Trend Up + Imbalance Kuat + Harga Benar-benar Naik (Tidak diserap)
                    if imbalance >= IMBALANCE_THRESHOLD and mid_price > ema:
                        if price_move > MIN_PRICE_MOVE_THRESHOLD: # Filter Efisiensi
                            active_position = {
                                "entry": best_bid, "signal": "LONG",
                                "tp": best_bid * (1 + TP_PERCENT),
                                "sl": best_bid * (1 - SL_PERCENT)
                            }
                    
                    # SHORT: Trend Down + Imbalance Negatif + Harga Benar-benar Turun
                    elif imbalance <= -IMBALANCE_THRESHOLD and mid_price < ema:
                        if price_move < -MIN_PRICE_MOVE_THRESHOLD: # Filter Efisiensi
                            active_position = {
                                "entry": best_ask, "signal": "SHORT",
                                "tp": best_ask * (1 - TP_PERCENT),
                                "sl": best_ask * (1 + SL_PERCENT)
                            }

                # 3. DASHBOARD
                if int(time.time() * 5) % 5 == 0:
                    os.system('cls' if os.name == 'nt' else 'clear')
                    win_rate = (stats['wins']/stats['trades']*100) if stats['trades'] > 0 else 0
                    print(f"=== {SYMBOL} PRO ADAPTIVE V2 ===")
                    print(f"Price: {mid_price:.2f} | EMA200: {ema if ema else 0:.2f}")
                    print(f"Imbalance: {imbalance:.3f} | Efficiency: {'PASS' if abs(price_move) > MIN_PRICE_MOVE_THRESHOLD else 'LOW'}")
                    print(f"Status: {'HOLDING ' + active_position['signal'] if active_position else 'SCANNING'}")
                    
                    table = [
                        ["Trades", stats['trades']], ["Win Rate", f"{win_rate:.1f}%"],
                        ["NET P/L", f"{stats['net_pl']:.4f} USDT"]
                    ]
                    print(tabulate(table, tablefmt="grid"))

            except Exception as e:
                await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())