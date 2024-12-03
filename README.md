import config
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

# Bybit API credentials
api_key = config.api_key
api_secret = config.api_secret

# Telegram API credentials
api_id = config.api_id
api_hash = config.api_hash
bot_username = config.bot_username  # Keep the bot username for listening to bot messages
phone_number = config.phone_number  # Using a single phone number for account
session_file = 'my_session.session'  # Session file name

# Initialize Bybit session
session = HTTP(api_key=api_key, api_secret=api_secret, testnet=False, demo=True)

# OTP storage
otp_data = None  # Single OTP data for login

# Flask application for receiving OTP via POST request
app = Flask(__name__)

@app.route('/receive_otp', methods=['POST'])
def receive_otp():
    global otp_data
    data = request.json
    otp = data.get('otp')
    
    # Store the OTP data for login
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

        logging.info(f"Extracted values - Symbol: {symbol}, Price: {price}, Stop Loss: {stop_loss_price}, Take Profit: {take_profit_price}")
        print(f"Extracted values - Symbol: {symbol}, Price: {price}, Stop Loss: {stop_loss_price}, Take Profit: {take_profit_price}")

        step_size = get_step_size(symbol)

        # Fetch account balance
        account_balance = session.get_wallet_balance(accountType="UNIFIED")
        logging.debug("Full account balance response: %s", account_balance)
        print("Account Balance Response:", account_balance)

        # Parse USDT balance
        try:
            wallet_list = account_balance["result"]["list"]
            if not wallet_list or not isinstance(wallet_list, list):
                raise ValueError("Wallet list is empty or not a valid list")

            usdt_data = next(
                (coin for account in wallet_list for coin in account.get("coin", []) if coin.get("coin") == "USDT"),
                None
            )
            
            if not usdt_data:
                raise ValueError("USDT balance not found in the response")
            
            equity = float(usdt_data.get("equity", 0))
            wallet_balance = float(usdt_data.get("walletBalance", 0))
            usd_value = float(usdt_data.get("usdValue", 0))

            logging.info(f"USDT Equity: {equity}, Wallet Balance: {wallet_balance}, USD Value: {usd_value}")
            print(f"USDT Equity: {equity}, Wallet Balance: {wallet_balance}, USD Value: {usd_value}")
        except Exception as e:
            logging.error("Error processing wallet balance: %s", traceback.format_exc())
            raise ValueError("Failed to parse wallet balance") from e

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

            logging.info(f"Placing order with parameters: {order_params}")
            print(f"Placing order with parameters: {order_params}")

            order = session.place_order(**order_params)

            if order["retCode"] == 0:
                logging.info(f"Limit order placed successfully with SL/TP: {order}")
                print(f"Limit order placed successfully with SL/TP: {order}")
            else:
                logging.error(f"Error placing order: {order['retMsg']}")
                print(f"Error placing order: {order['retMsg']}")
        else:
            logging.error("Insufficient balance to place even a minimum quantity order")
            print("Insufficient balance to place even a minimum quantity order")
    except Exception as e:
        logging.error("Error handling bot response: %s", traceback.format_exc())
        print(f"Error handling bot response: {traceback.format_exc()}")

# Initialize the Telegram client
client = TelegramClient(session_file, api_id, api_hash)

@client.on(events.NewMessage(from_users=bot_username))
async def bot_message_handler(event):
    print(f"Bot response received: {event.raw_text}")
    await handle_bot_response(event)

async def login_with_phone(client, phone_number):
    # Check if session file exists; if not, create a new one
    if not os.path.exists(session_file):
        print("Session file not found. Creating a new session...")
        await client.start(phone_number)  # Start the client, creating a new session
    else:
        await client.connect()
        if not await client.is_user_authorized():
            print(f"Logging in with phone number: {phone_number}")
            await client.send_code_request(phone_number)
            
            # Indicate when to enter OTP
            print(f"Enter OTP for the account:")

            # Wait for OTP to be received via Postman
            while otp_data is None:
                await asyncio.sleep(1)  # Wait for the OTP to be posted

            otp = otp_data
            if otp:
                await client.sign_in(phone_number, otp)
                print("Logged in successfully!")
            else:
                print("Failed to receive OTP.")
                raise Exception("OTP not received")

async def main():
    print("Starting Telegram client...")

    # Log in to the Telegram client using the phone number
    await login_with_phone(client, phone_number)

    # Start the client
    await client.start()
    print("Telegram client started. Listening for bot messages...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    # Start the Flask server in a separate thread for OTP reception
    def run_flask():
        app.run(host="0.0.0.0", port=5000)

    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    # Run the main function in a loop to handle restarts on failure
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            logging.error(f"Error occurred: {e}. Restarting the bot...")
            print(f"Error occurred: {e}. Restarting the bot...")
            asyncio.sleep(5)  # Optional: sleep for a while before restarting
