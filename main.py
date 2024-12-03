import logging
import math
import asyncio
import traceback
import os
from telethon import TelegramClient, events
from pybit.unified_trading import HTTP
from flask import Flask, request, jsonify
from threading import Thread

# Logging configuration
logging.basicConfig(
    filename='pybit_telegram.log',
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s'
)

# Configurations (Replace with your values)
import config  # Assuming `config.py` contains your credentials
api_key = config.api_key
api_secret = config.api_secret
api_id = config.api_id
api_hash = config.api_hash
bot_username = config.bot_username
phone_number = config.phone_number
session_file = 'my_session.session'

# Initialize Bybit session
session = HTTP(api_key=api_key, api_secret=api_secret, testnet=False, demo=True)

# Flask application for receiving OTP via POST request
app = Flask(__name__)
otp_data = None  # Global variable for storing OTP

@app.route('/receive_otp', methods=['POST'])
def receive_otp():
    global otp_data
    data = request.json
    otp = data.get('otp')
    otp_data = otp
    return jsonify({"status": "OTP received"}), 200

# Helper functions
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
    """Handles bot response to extract trading parameters and place an order."""
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

        step_size = get_step_size(symbol)

        # Fetch account balance
        account_balance = session.get_wallet_balance(accountType="UNIFIED")
        wallet_list = account_balance["result"]["list"]

        usdt_data = next(
            (coin for account in wallet_list for coin in account.get("coin", []) if coin.get("coin") == "USDT"),
            None
        )
        wallet_balance = float(usdt_data.get("walletBalance", 0))
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
                logging.info(f"Order placed successfully: {order}")
            else:
                logging.error(f"Error placing order: {order['retMsg']}")
        else:
            logging.error("Insufficient balance for minimum order quantity")
    except Exception:
        logging.error("Error handling bot response: %s", traceback.format_exc())

# Initialize Telegram client
client = TelegramClient(session_file, api_id, api_hash)

@client.on(events.NewMessage(from_users=bot_username))
async def bot_message_handler(event):
    print(f"Bot response received: {event.raw_text}")
    await handle_bot_response(event)

async def login_with_phone(client, phone_number):
    """Handles login using phone number and OTP."""
    global otp_data
    if not os.path.exists(session_file):
        await client.start(phone_number)
    else:
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(phone_number)
            while otp_data is None:
                await asyncio.sleep(1)  # Wait for OTP
            await client.sign_in(phone_number, otp_data)

async def main():
    """Main function for managing the Telegram client."""
    await login_with_phone(client, phone_number)
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    # Run Flask in a separate thread
    def run_flask():
        app.run(host="0.0.0.0", port=5000)

    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    # Use a single event loop for restarts
    loop = asyncio.get_event_loop()

    async def restartable_main():
        while True:
            try:
                await main()
            except Exception as e:
                logging.error(f"Error: {e}, restarting...")
                await asyncio.sleep(5)  # Wait before retrying

    try:
        loop.run_until_complete(restartable_main())
    except KeyboardInterrupt:
        print("Shutting down...")
