"""
SUPERRICH - MT5 Bridge Module
Handles all communication with MetaTrader 5 terminal
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 package not installed. Install with: pip install MetaTrader5")


class MT5Bridge:
    """Bridge between the web application and MT5 terminal"""
    
    def __init__(self):
        self.connected = False
        self.account_info = None
    
    def connect(self, login: int = None, password: str = None, server: str = None) -> bool:
        """Initialize connection to MT5 terminal"""
        if not MT5_AVAILABLE:
            logger.warning("MT5 package not available")
            return False
        
        try:
            if not mt5.initialize():
                logger.error(f"MT5 initialize failed: {mt5.last_error()}")
                return False
            
            if login and password and server:
                authorized = mt5.login(login=login, password=password, server=server)
                if not authorized:
                    logger.error(f"MT5 login failed: {mt5.last_error()}")
                    return False
            
            self.connected = True
            self.account_info = mt5.account_info()
            logger.info(f"Connected to MT5: Account #{self.account_info.login if self.account_info else 'unknown'}")
            return True
            
        except Exception as e:
            logger.error(f"MT5 connection error: {e}")
            return False
    
    def disconnect(self):
        """Shutdown MT5 connection"""
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()
            self.connected = False
            logger.info("MT5 disconnected")
    
    def get_account_info(self) -> Optional[dict]:
        """Get current account information"""
        if not self.connected:
            return None
        try:
            info = mt5.account_info()
            if info is None:
                return None
            return {
                "login": info.login,
                "name": info.name,
                "server": info.server,
                "balance": info.balance,
                "equity": info.equity,
                "margin": info.margin,
                "free_margin": info.margin_free,
                "margin_level": info.margin_level if info.margin_level else 0,
                "leverage": info.leverage,
                "currency": info.currency,
                "profit": info.profit
            }
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return None
    
    def get_positions(self) -> list:
        """Get all open positions"""
        if not self.connected:
            return []
        try:
            positions = mt5.positions_get()
            if positions is None:
                return []
            return [{
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": p.volume,
                "open_price": p.price_open,
                "current_price": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "pnl": p.profit,
                "swap": p.swap,
                "commission": p.comment,
                "open_time": datetime.fromtimestamp(p.time).isoformat(),
                "magic": p.magic
            } for p in positions]
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []
    
    def get_orders(self) -> list:
        """Get all pending orders"""
        if not self.connected:
            return []
        try:
            orders = mt5.orders_get()
            if orders is None:
                return []
            return [{
                "ticket": o.ticket,
                "symbol": o.symbol,
                "type": o.type,
                "volume": o.volume_current,
                "price": o.price_open,
                "sl": o.sl,
                "tp": o.tp,
                "time_setup": datetime.fromtimestamp(o.time_setup).isoformat()
            } for o in orders]
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []
    
    def place_order(self, symbol: str, order_type: str, volume: float,
                    price: float = 0, sl: float = 0, tp: float = 0,
                    comment: str = "", magic: int = 234000) -> dict:
        """Place a new order"""
        if not self.connected:
            return {"success": False, "error": "Not connected to MT5"}
        
        try:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                return {"success": False, "error": f"Symbol {symbol} not found"}
            
            if not symbol_info.visible:
                mt5.symbol_select(symbol, True)
            
            point = symbol_info.point
            
            if order_type.upper() == "BUY":
                trade_type = mt5.ORDER_TYPE_BUY
                price = mt5.symbol_info_tick(symbol).ask if price == 0 else price
            else:
                trade_type = mt5.ORDER_TYPE_SELL
                price = mt5.symbol_info_tick(symbol).bid if price == 0 else price
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": trade_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": magic,
                "comment": comment or "SUPERRICH",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return {"success": False, "error": f"Order failed: {result.comment}", "retcode": result.retcode}
            
            return {
                "success": True,
                "order": result.order,
                "volume": result.volume,
                "price": result.price,
                "comment": result.comment
            }
            
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {"success": False, "error": str(e)}

    def modify_position_sl(self, ticket: int, sl: float, tp: float = 0) -> dict:
        """Modify stop loss for an open position."""
        if not self.connected:
            return {"success": False, "error": "Not connected to MT5"}

        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                return {"success": False, "error": f"Position {ticket} not found"}

            pos = position[0]
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol": pos.symbol,
                "sl": sl,
                "tp": tp or pos.tp,
                "magic": pos.magic,
                "comment": "SUPERRICH SL update",
            }

            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return {"success": False, "error": f"SL modify failed: {result.comment}", "retcode": result.retcode}

            return {"success": True, "ticket": ticket, "sl": sl, "tp": tp or pos.tp}

        except Exception as e:
            logger.error(f"Error modifying SL: {e}")
            return {"success": False, "error": str(e)}
    
    def close_position(self, ticket: int) -> dict:
        """Close a specific position by ticket"""
        if not self.connected:
            return {"success": False, "error": "Not connected to MT5"}
        
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                return {"success": False, "error": f"Position {ticket} not found"}
            
            pos = position[0]
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(pos.symbol).bid if pos.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(pos.symbol).ask
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": ticket,
                "price": price,
                "deviation": 20,
                "magic": 234000,
                "comment": "SUPERRICH close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return {"success": False, "error": f"Close failed: {result.comment}"}
            
            return {"success": True, "ticket": ticket, "price": result.price}
            
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return {"success": False, "error": str(e)}
    
    def get_history(self, days: int = 30) -> list:
        """Get trade history for the specified number of days"""
        if not self.connected:
            return []
        try:
            from_date = datetime.now() - timedelta(days=days)
            to_date = datetime.now()
            deals = mt5.history_deals_get(from_date, to_date)
            if deals is None:
                return []
            return [{
                "ticket": d.ticket,
                "order": d.order,
                "symbol": d.symbol,
                "type": "BUY" if d.type == 0 else "SELL" if d.type == 1 else str(d.type),
                "volume": d.volume,
                "price": d.price,
                "profit": d.profit,
                "commission": d.commission,
                "swap": d.swap,
                "time": datetime.fromtimestamp(d.time).isoformat(),
                "comment": d.comment
            } for d in deals]
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []
    
    def get_symbol_tick(self, symbol: str) -> Optional[dict]:
        """Get latest tick data for a symbol"""
        if not self.connected:
            return None
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return None
            return {
                "symbol": symbol,
                "bid": tick.bid,
                "ask": tick.ask,
                "last": tick.last,
                "volume": tick.volume,
                "time": datetime.fromtimestamp(tick.time).isoformat()
            }
        except Exception as e:
            logger.error(f"Error getting tick: {e}")
            return None

    def get_rates(self, symbol: str, timeframe: str = "M15", count: int = 220) -> list:
        """Get recent OHLCV rates for a symbol and timeframe."""
        if not self.connected:
            return []
        try:
            timeframe_map = {
                "M1": mt5.TIMEFRAME_M1,
                "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "M30": mt5.TIMEFRAME_M30,
                "H1": mt5.TIMEFRAME_H1,
                "H4": mt5.TIMEFRAME_H4,
                "D1": mt5.TIMEFRAME_D1,
            }
            tf = timeframe_map.get(timeframe.upper())
            if tf is None:
                return []
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
            if rates is None:
                return []
            return [dict(r) for r in rates]
        except Exception as e:
            logger.error(f"Error getting rates: {e}")
            return []
    
    def get_symbols(self) -> list:
        """Get available trading symbols"""
        if not self.connected:
            return []
        try:
            symbols = mt5.symbols_get()
            if symbols is None:
                return []
            return [{
                "name": s.name,
                "description": s.description,
                "point": s.point,
                "digits": s.digits,
                "spread": s.spread,
                "trade_mode": s.trade_mode
            } for s in symbols if s.visible]
        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
            return []
