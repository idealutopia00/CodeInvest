import mplfinance as mpf
import pandas as pd
import os

class ChartFactory:
    """
    可视化工厂：接收带有指标的DataFrame，生成图表。
    """
    def __init__(self, save_dir="Data/charts"):
        self.save_dir = save_dir
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
            
        # 自定义图表风格
        self.style = mpf.make_mpf_style(
            base_mpf_style='yahoo',
            rc={'font.family': 'Arial'}, # 适配中文，如果乱码可改为 'Arial'
            marketcolors=mpf.make_marketcolors(up='red', down='green', edge='inherit')
        )

    def plot_candle_with_indicators(self, code: str, df: pd.DataFrame, title_suffix=""):
        """
        绘制：K线(主图) + MA(主图) + Volume(副图1) + MACD(副图2)
        """
        # 截取最近 N 天数据，避免图表过于密集看不清
        plot_data = df.tail(100) 

        # 构建额外的图层 (Add Plots)
        apds = [
            # 主图 MA
            mpf.make_addplot(plot_data['MA20'], color='orange', width=1.5),
            mpf.make_addplot(plot_data['MA60'], color='blue', width=1.5),
            
            # 主图 BOLL
            mpf.make_addplot(plot_data['BOLL_UPPER'], color='gray', linestyle='dashed', alpha=0.5),
            mpf.make_addplot(plot_data['BOLL_LOWER'], color='gray', linestyle='dashed', alpha=0.5),

            # 副图 MACD (Panel 2, 因为Panel 1默认是Volume)
            mpf.make_addplot(plot_data['MACD_DIF'], panel=2, color='fuchsia', ylabel='MACD'),
            mpf.make_addplot(plot_data['MACD_DEA'], panel=2, color='black'),
            mpf.make_addplot(plot_data['MACD_HIST'], panel=2, type='bar', color='dimgray', alpha=0.5),
        ]

        filename = f"{code}_tech_analysis.png"
        filepath = os.path.join(self.save_dir, filename)

        mpf.plot(
            plot_data,
            type='candle',
            style=self.style,
            title=f"{code} {title_suffix}",
            volume=True,
            addplot=apds,
            panel_ratios=(3, 1, 1), # K线:成交量:MACD 高度比
            figsize=(12, 8),
            savefig=filepath
        )
        print(f"[Chart] Saved to: {filepath}")
        return filepath