import os
import time
import ccxt.async_support as ccxt
from typing import List
from tqdm.auto import tqdm
from itertools import combinations
from dataclasses import dataclass

import octobot_commons.symbols as symbols
import octobot_commons.constants as constants

from triangular_arbitrage import REDIS_HOST_ENV, REDIS_PASSWORD_ENV, REDIS_PORT_ENV, REDIS_KEY_ENV, EXCHANGE_NAME_ENV

@dataclass
class ShortTicker:
    symbol: symbols.Symbol
    last_price: float


async def fetch_tickers(exchange):
    if (exchange.has['fetchTickers']):
        return await exchange.fetch_tickers()
    return []

def get_symbol_from_key(key_symbol: str) -> symbols.Symbol:
    try:
        return symbols.parse_symbol(key_symbol)
    except:
        return None

def is_delisted_symbols(exchange_time, ticker, threshold = 1 * constants.DAYS_TO_SECONDS * constants.MSECONDS_TO_SECONDS) -> bool:
    ticker_time = ticker['timestamp']
    if (exchange_time - ticker_time <= threshold):
        return False
    # print(f"Detected delisted symbol {ticker['symbol']}")
    return True

def get_last_prices(exchange_time, tickers):
    return [
        ShortTicker(symbol=get_symbol_from_key(key), 
        last_price=tickers[key]['close']) 
        for key, _ in tickers.items()
        if tickers[key]['close'] is not None and not is_delisted_symbols(exchange_time, tickers[key])
    ]

def get_best_opportunity(tickers: List[ShortTicker]) -> List[ShortTicker]:
    ticker_dict = {str(ticker.symbol): ticker for ticker in tickers if ticker.symbol is not None}

    currencies = set()
    for ticker in tickers:
        if ticker.symbol is not None:
            currencies.add(ticker.symbol.base)
            currencies.add(ticker.symbol.quote)

    best_profit = 0
    best_triplet = None

    def get_opportunity_symbol(a, b):
        return f"{a}/{b}"

    # Try all combinations of three currencies.
    for a, b, c in tqdm(combinations(currencies, 3)):
        # Look up the tickers in the dictionary instead of searching through the list.
        a_to_b = ticker_dict.get(get_opportunity_symbol(a,b))
        b_to_c = ticker_dict.get(get_opportunity_symbol(b,c))
        c_to_a = ticker_dict.get(get_opportunity_symbol(c,a))

        # If the ticker does not exist, try the inverse
        if not a_to_b:
            b_to_a = ticker_dict.get(get_opportunity_symbol(b,a))
            if b_to_a:
                a_to_b = ShortTicker(get_opportunity_symbol(a,b), 1/b_to_a.last_price)

        if not b_to_c:
            c_to_b = ticker_dict.get(get_opportunity_symbol(c,b))
            if c_to_b:
                b_to_c = ShortTicker(get_opportunity_symbol(b,c), 1/c_to_b.last_price)

        if not c_to_a:
            a_to_c = ticker_dict.get(get_opportunity_symbol(a,c))
            if a_to_c:
                c_to_a = ShortTicker(get_opportunity_symbol(c,a), 1/a_to_c.last_price)

        if not all([a_to_b, b_to_c, c_to_a]):
            continue
        
        profit = a_to_b.last_price * b_to_c.last_price * c_to_a.last_price

        if profit > best_profit:
            best_profit = profit
            best_triplet = [a_to_b, b_to_c, c_to_a]

    return best_triplet, best_profit

async def get_exchange_data(exchange_name):
    exchange_class = getattr(ccxt, os.getenv(EXCHANGE_NAME_ENV, exchange_name))
    exchange = exchange_class()
    tickers = await fetch_tickers(exchange)
    exchange_time = exchange.milliseconds()
    await exchange.close()
    return tickers, exchange_time

async def get_exchange_last_prices(exchange_name):
    tickers, exchange_time = await get_exchange_data(exchange_name)
    last_prices = get_last_prices(exchange_time, tickers)
    return last_prices

async def run_detection(exchange_name = "binance"):
    exchange = os.getenv(EXCHANGE_NAME_ENV, exchange_name)
    last_prices = await get_exchange_last_prices(exchange)
    best_opportunity, best_profit = get_best_opportunity(last_prices)
    if os.getenv(REDIS_HOST_ENV, None) is not None:
        upload_result(best_opportunity, best_profit, exchange)
    
    return best_opportunity, best_profit, exchange

def upload_result(best_opportunities, best_profit, exchange_id):
    import redis
    redis_client = redis.Redis(
        host=os.getenv(REDIS_HOST_ENV, None),
        port=os.getenv(REDIS_PORT_ENV, None),
        password=os.getenv(REDIS_PASSWORD_ENV, None),
        ssl=True
    )

    data = {
        'best_opportunity': [str(best_opportunity.symbol) for best_opportunity in best_opportunities],
        'best_profit': best_profit,
        'exchange_id': exchange_id,
        'timestamp': time.time()
    }
    redis_client.json().set(f"{os.getenv(REDIS_KEY_ENV, None)}:{exchange_id}", '$', data)
