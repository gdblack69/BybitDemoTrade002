try:
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
except Exception as e:
    error_msg = f"Error placing order: {traceback.format_exc()}"
    logging.error(error_msg)
    print(error_msg)
