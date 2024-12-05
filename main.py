import logging
import math
import asyncio
import os
from telethon import TelegramClient, events
from pybit.unified_trading import HTTP
from flask import Flask, request, jsonify
from threading import Thread
import traceback  # For detailed error handling

# Logging configuration
logging.basicConfig(
    filename="pybit_telegram.log",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s"
)

# Bybit API credentials from environment variables
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

# Telegram API credentials from environment variables
api_id = int(os.getenv("API_ID", 0))  # Default to 0 if not set
api_hash = os.getenv("API_HASH")
bot_username = os.getenv("BOT_USERNAME")
phone_number = os.getenv("PHONE_NUMBER")
session_file = "my_session.session"  # Adjust session file name if needed

# Initialize Bybit session
session = HTTP(api_key=api_key, api_secret=api_secret, testnet=False, demo=True)

# OTP storage
otp_data = None

# Flask application for receiving OTP via POST request
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "Bot is running"}), 200

@app.route('/receive_otp', methods=['POST'])
def receive_otp():
    global otp_data
    data = request.json
    otp = data.get("otp")

    otp_data = otp
    return jsonify({"status": "OTP received"}), 200

def get_step_size(symbol):
    """Fetch the step size for the given symbol."""
    try:
        instruments = session.get_instruments_info(category="linear")
        linear_list = instruments["result"]["list"]
        symbol_info = next((x for x in linear_list if x["symbol"] == symbol), None)

        if symbol_info:
            return float(symbol_info["lotSizeFilter"]["qtyStep"])
        else:
            raise ValueError(f"Symbol {symbol} not found in instruments")
    except Exception:
        logging.error("Error fetching step size: %s", traceback.format_exc())
        raise

async def handle_bot_response(event):
    bot_message = event.raw_text.strip('"').strip()

    try:
        # Parse bot message
        message_parts = bot_message.split("\n")
        symbol, price, stop_loss_price, take_profit_price = None, None, None, None

        for part in message_parts:
            if part.startswith("Symbol:"):
                symbol = part.replace("Symbol:", "").strip()
            elif part.startswith("Price:"):
                price = float(part.replace("Price:", "").strip())
            elif part.startswith("Stop Loss:"):
                stop_loss_price = float(part.replace("Stop Loss:", "").strip())
            elif part.startswith("Take Profit:"):
                take_profit_price = float(part.replace("Take Profit:", "").strip())

        if not all([symbol, price, stop_loss_price, take_profit_price]):
            raise ValueError("Invalid message format received from the bot")

        # Get the step size (ticker size) for the symbol
        step_size = get_step_size(symbol)

        # Fetch account balance
        account_balance = session.get_wallet_balance(accountType="UNIFIED")
        wallet_list = account_balance["result"]["list"]
        usdt_data = next(
            (coin for account in wallet_list for coin in account.get("coin", []) if coin.get("coin") == "USDT"),
            None
        )
        wallet_balance = float(usdt_data.get("walletBalance", 0))

        # Calculate maximum quantity
        max_qty = wallet_balance / price
        max_qty = math.floor(max_qty / step_size) * step_size

        if max_qty > 0:
            order_params = {
                "category": "linear",
                "symbol": symbol,
                "side": "Buy",
                "order_type": "Limit",
                "qty": max_qty,
                "price": price,
                "time_in_force": "GTC",
                "stopLoss": stop_loss_price,
                "takeProfit": take_profit_price
            }
            order = session.place_order(**order_params)

            if order["retCode"] == 0:
                print(f"Limit order placed successfully with SL/TP: {order}")
            else:
                print(f"Error placing order: {order['retMsg']}")
        else:
            print("Insufficient balance to place even a minimum quantity order")
    except Exception as e:
        logging.error("Error handling bot response: %s", traceback.format_exc())
        print(f"Error handling bot response: {traceback.format_exc()}")

# Initialize the Telegram client
client = TelegramClient(session_file, api_id, api_hash)

@client.on(events.NewMessage(from_users=bot_username))
async def bot_message_handler(event):
    await handle_bot_response(event)

async def login_with_phone(client, phone_number):
    if not os.path.exists(session_file):
        await client.start(phone_number)
    else:
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(phone_number)
            print("Waiting for OTP... Please enter the OTP when prompted.")
            while otp_data is None:
                await asyncio.sleep(1)

            otp = otp_data
            if otp:
                await client.sign_in(phone_number, otp)
                print("Logged in successfully!")
            else:
                print("Failed to receive OTP.")
                raise Exception("OTP not received")

async def main():
    await login_with_phone(client, phone_number)
    await client.start()
    await client.run_until_disconnected()

def run_flask():
    app.run(host="0.0.0.0", port=5000)

flask_thread = Thread(target=run_flask)
flask_thread.start()

async def run():
    while True:
        try:
            await main()
        except Exception as e:
            print(f"Error occurred: {e}. Restarting the bot...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run())
