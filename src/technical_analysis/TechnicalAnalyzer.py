import pandas as pd
import numpy as np

class TechnicalAnalyzer:
    """
    独立技术分析器：输入K线数据，输出包含技术指标的DataFrame和信号报告。
    """
    def __init__(self, k_line_df: pd.DataFrame):
        # 1. 数据清洗与类型转换 (Baostock 返回的通常是字符串)
        self.df = k_line_df.copy()
        
        # 确保包含必要列
        required_cols = ['date', 'close', 'open', 'high', 'low', 'volume']
        if not all(col in self.df.columns for col in required_cols):
            raise ValueError(f"Input DataFrame missing required columns: {required_cols}")

        # 设置日期索引
        self.df['date'] = pd.to_datetime(self.df['date'])
        self.df.set_index('date', inplace=True)
        self.df.sort_index(inplace=True)

        # 强制转为浮点数
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            self.df[col] = pd.to_numeric(self.df[col], errors='coerce')

    def calculate_all(self):
        """执行所有指标计算"""
        self._add_ma()
        self._add_macd()
        self._add_boll()
        self._add_rsi()
        return self.df

    def _add_ma(self):
        self.df['MA5'] = self.df['close'].rolling(window=5).mean()
        self.df['MA10'] = self.df['close'].rolling(window=10).mean()
        self.df['MA20'] = self.df['close'].rolling(window=20).mean()
        self.df['MA60'] = self.df['close'].rolling(window=60).mean()

    def _add_macd(self):
        # 标准参数 12, 26, 9
        exp12 = self.df['close'].ewm(span=12, adjust=False).mean()
        exp26 = self.df['close'].ewm(span=26, adjust=False).mean()
        self.df['MACD_DIF'] = exp12 - exp26
        self.df['MACD_DEA'] = self.df['MACD_DIF'].ewm(span=9, adjust=False).mean()
        self.df['MACD_HIST'] = 2 * (self.df['MACD_DIF'] - self.df['MACD_DEA'])

    def _add_boll(self):
        # N=20, K=2
        self.df['BOLL_MID'] = self.df['close'].rolling(window=20).mean()
        std = self.df['close'].rolling(window=20).std()
        self.df['BOLL_UPPER'] = self.df['BOLL_MID'] + 2 * std
        self.df['BOLL_LOWER'] = self.df['BOLL_MID'] - 2 * std

    def _add_rsi(self, period=6):
        delta = self.df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        self.df['RSI6'] = 100 - (100 / (1 + rs))

    def get_signal_report(self) -> dict:
        """生成最新的技术面信号摘要"""
        if len(self.df) < 2: return {"status": "数据不足"}
        
        curr = self.df.iloc[-1]
        prev = self.df.iloc[-2]
        
        signals = []
        
        # 均线系统
        if curr['close'] > curr['MA20']:
            signals.append("站稳月线(MA20)")
        elif curr['close'] < curr['MA20']:
            signals.append("跌破月线(MA20)")
            
        # MACD
        if prev['MACD_DIF'] < prev['MACD_DEA'] and curr['MACD_DIF'] > curr['MACD_DEA']:
            signals.append("MACD金叉")
        elif curr['MACD_DIF'] > 0 and curr['MACD_DEA'] > 0:
            signals.append("MACD水上运行")
            
        # RSI
        if curr['RSI6'] > 80: signals.append("RSI超买(警惕)")
        if curr['RSI6'] < 20: signals.append("RSI超卖(反弹)")

        return {
            "date": str(curr.name.date()),
            "close": curr['close'],
            "signals": signals,
            "summary": " | ".join(signals) if signals else "盘整无明显信号"
        }