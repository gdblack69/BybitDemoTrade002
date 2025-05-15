import logging
import math
import asyncio
import traceback
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import os
import time

# Initialize Flask app
app = Flask(__name__)

# Logging configuration
logging.basicConfig(
    filename='pybit_telegram.log',
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s'
)

# Load environment variables from .env file (for local testing)
load_dotenv()

# Configuration variables from environment
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_USERNAME = os.getenv("BOT_USERNAME")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
SESSION_NAME = os.getenv("SESSION_NAME")

# Initialize Bybit session
session = HTTP(api_key=API_KEY, api_secret=API_SECRET, testnet=False, demo=True)

# Initialize Telegram client
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# Global variables to manage OTP state and rate limiting
otp_received = None
login_event = asyncio.Event()
last_otp_request_time = 0
OTP_REQUEST_INTERVAL = 60  # Minimum interval between OTP requests (in seconds)

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

def format_trade_details(symbol, price, stop_loss_price, take_profit_price, qty, order_response, equity, wallet_balance):
    """Format trade details into a clean, table-like structure."""
    order_id = order_response.get("result", {}).get("orderId", "N/A")
    ret_msg = order_response.get("retMsg", "N/A")
    timestamp = order_response.get("time", "N/A")

    trade_info = "\n===== Trade Details =====\n"
    trade_info += f"{'Symbol':<20}: {symbol}\n"
    trade_info += f"{'Price':<20}: {price:,.2f}\n"
    trade_info += f"{'Stop Loss':<20}: {stop_loss_price:,.2f}\n"
    trade_info += f"{'Take Profit':<20}: {take_profit_price:,.2f}\n"
    trade_info += f"{'Quantity':<20}: {qty:,.8f}\n"
    trade_info += f"{'Order ID':<20}: {order_id}\n"
    trade_info += f"{'Status':<20}: {ret_msg}\n"
    trade_info += f"{'Timestamp':<20}: {timestamp}\n"
    trade_info += f"{'USDT Equity':<20}: {equity:,.2f}\n"
    trade_info += f"{'Wallet Balance':<20}: {wallet_balance:,.2f}\n"
    trade_info += "========================\n"
    return trade_info

async def handle_bot_response(event):
    """Handles bot response to extract trading parameters and place an order."""
    bot_message = event.raw_text.strip('"').strip()

    try:
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

        account_balance = session.get_wallet_balance(accountType="UNIFIED")
        logging.debug("Full account balance response: %s", account_balance)

        try:
            wallet_list = account_balance["result"]["list"]
            if not wallet_list or not isinstance(wallet_list, list):
                raise ValueError("Wallet list is empty or not a valid list")

            usdt_data = None
            for account in wallet_list:
                coins = account.get("coin")
                if isinstance(coins, list):
                    for coin in coins:
                        if coin.get("coin") == "USDT":
                            usdt_data = coin
                            break
                elif isinstance(coins, dict):
                    if coins.get("coin") == "USDT":
                        usdt_data = coins
                if usdt_data:
                    break

            if not usdt_data:
                raise ValueError("USDT balance not found in the response")

            equity = float(usdt_data.get("equity", 0))
            wallet_balance = float(usdt_data.get("walletBalance", 0))

            logging.info(f"USDT Equity: {equity}, Wallet Balance: {wallet_balance}")
        except Exception as e:
            logging.error("Error processing wallet balance: %s", traceback.format_exc())
            raise ValueError("Failed to parse wallet balance") from e

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
            order = session.place_order(**order_params)

            if order["retCode"] == 0:
                trade_details = format_trade_details(
                    symbol, price, stop_loss_price, take_profit_price,
                    max_qty, order, equity, wallet_balance
                )
                logging.info(trade_details)
                print(trade_details)
            else:
                error_msg = f"Error placing order: {order['retMsg']}"
                logging.error(error_msg)
                print(error_msg)
        else:
            error_msg = "Insufficient balance to place even a minimum quantity order"
            logging.error(error_msg)
            print(error_msg)
    except Exception as e:
        error_msg = f"Error handling bot response: {traceback.format_exc()}"
        logging.error(error_msg)
        print(error_msg)

@client.on(events.NewMessage(from_users=BOT_USERNAME))
async def bot_message_handler(event):
    print(f"Bot response received: {event.raw_text}")
    await handle_bot_response(event)

@app.route('/otp', methods=['POST'])
async def receive_otp():
    """Endpoint to receive OTP via POST request."""
    global otp_received
    try:
        data = request.get_json()
        if not data or 'otp' not in data:
            return jsonify({"error": "OTP is required"}), 400

        otp = data['otp']
        otp_received = otp
        login_event.set()  # Signal that OTP has been received
        logging.info(f"OTP received: {otp}")
        return jsonify({"message": "OTP received successfully", "otp": otp}), 200
    except Exception as e:
        logging.error(f"Error receiving OTP: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Render."""
    return jsonify({"status": "healthy"}), 200

async def telegram_login():
    """Handle Telegram login with OTP."""
    global otp_received, last_otp_request_time
    try:
        await client.connect()
        if not await client.is_user_authorized():
            # Rate limit OTP requests
            current_time = time.time()
            time_since_last_request = current_time - last_otp_request_time
            if time_since_last_request < OTP_REQUEST_INTERVAL:
                wait_time = OTP_REQUEST_INTERVAL - time_since_last_request
                logging.info(f"Rate limiting OTP request. Waiting {wait_time:.2f} seconds before next request.")
                await asyncio.sleep(wait_time)

            print("Client not authorized, requesting code...")
            logging.info("Requesting Telegram authentication code for %s", PHONE_NUMBER)
            try:
                await client.send_code_request(PHONE_NUMBER)
                last_otp_request_time = time.time()  # Update last request time
            except FloodWaitError as e:
                logging.error(f"Flood wait error: Must wait {e.seconds} seconds before requesting another OTP")
                await asyncio.sleep(e.seconds)
                return  # Exit to avoid infinite loop

            print("Waiting for OTP via /otp endpoint...")
            logging.info("Waiting for OTP via /otp endpoint...")
            await login_event.wait()  # Wait for OTP to be received
            if otp_received:
                try:
                    await client.sign_in(phone=PHONE_NUMBER, code=otp_received)
                    print("Logged in successfully.")
                    logging.info("Telegram login successful")
                except PhoneCodeInvalidError:
                    logging.error("Invalid OTP provided: %s", otp_received)
                    raise ValueError("Invalid OTP provided")
                except SessionPasswordNeededError:
                    logging.error("Two-factor authentication required, but not supported in this setup")
                    raise ValueError("Two-factor authentication required")
                finally:
                    otp_received = None  # Reset OTP
                    login_event.clear()  # Reset event
            else:
                raise ValueError("OTP not received")
        else:
            print("Client already authorized.")
            logging.info("Telegram client already authorized")
    except Exception as e:
        logging.error(f"Error during Telegram login: {traceback.format_exc()}")
        print(f"Error during Telegram login: {str(e)}")
        # Do not raise, allow Flask to continue running

def run_flask():
    """Run the Flask app in the main thread."""
    port = int(os.getenv("PORT", 5000))
    logging.info(f"Starting Flask app on port {port}")
    print(f"Starting Flask app on port {port}")
    try:
        app.run(host='0.0.0.0', port=port, debug=False)
    except Exception as e:
        logging.error(f"Failed to start Flask app: {traceback.format_exc()}")
        print(f"Failed to start Flask app: {str(e)}")
        raise

async def main():
    """Main function to start Flask and Telegram client."""
    # Start Flask in the main thread to ensure immediate binding
    from threading import Thread
    flask_thread = Thread(target=run_flask, daemon=False)
    flask_thread.start()

    # Wait briefly to ensure Flask has started
    await asyncio.sleep(2)

    # Handle Telegram login concurrently
    try:
        await telegram_login()
        print("Telegram login completed successfully.")
        logging.info("Telegram login completed successfully.")
    except Exception as e:
        logging.error(f"Telegram login failed but Flask is running: {str(e)}")
        print(f"Telegram login failed but Flask is running: {str(e)}")

    # Start Telegram client event loop
    try:
        print("Telegram client started. Listening for bot messages...")
        logging.info("Telegram client started. Listening for bot messages...")
        await client.run_until_disconnected()
    except Exception as e:
        logging.error(f"Telegram client failed: {traceback.format_exc()}")
        print(f"Telegram client failed: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
