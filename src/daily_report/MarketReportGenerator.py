# utils/reporter.py

import os
import yaml
import re
import datetime
import pandas as pd
import traceback
from openai import OpenAI

class MarketReportGenerator:
    
    def __init__(self, config_path, data_provider_class, save_dir=None, output_format="md"):
        """
        :param config_path: 配置文件路径
        :param data_provider_class: 实现了 FinancialDataSource 接口的类
        """
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.data_provider_class = data_provider_class
        self.date = None
        self.output_format = output_format.lower()
        
        if save_dir:
            self.output_dir = os.path.abspath(save_dir)
        else:
            base_dir = os.path.dirname(config_path)
            self.output_dir = os.path.join(base_dir, "reports")
        
        self.data_context = {}

    def _load_config(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config not found: {path}")
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _get_latest_trading_date(self, client):
        """
        基于 get_trade_dates 接口推算最近的有数据的交易日。
        如果今天的数据没出来，就自动回退到上一个交易日。
        """
        # 向前多取几天，防止节假日或数据延迟
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        start_lookback = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        
        try:
            # 1. 获取最近的交易日历
            df = client.get_trade_dates(start_date=start_lookback, end_date=today)
            if df.empty:
                return today
            
            # 2. 筛选出 is_trading_day = '1' 的日期
            if 'is_trading_day' in df.columns:
                trading_days_df = df[df['is_trading_day'] == '1']
            else:
                trading_days_df = df
            
            if trading_days_df.empty:
                return today
                
            # 获取日期列表（倒序，从今天往回找）
            # 假设日期在第一列 'calendar_date'
            valid_dates = trading_days_df.iloc[:, 0].tolist()
            valid_dates.sort(reverse=True) # ['2026-01-20', '2026-01-19', ...]

            # 3. 逐个检查数据是否存在 (核心改进)
            # 我们拿上证指数 (sh.000001) 试探一下，如果有数据，说明这一天的数据已更新
            for date_candidate in valid_dates:
                # 试探性获取一下上证指数数据
                try:
                    k_data = client.get_historical_k_data(
                        code='sh.000001', 
                        start_date=date_candidate, 
                        end_date=date_candidate,
                        frequency='d'
                    )
                    if not k_data.empty:
                        print(f"[System] Date Check: {date_candidate} data is READY.")
                        return date_candidate
                    else:
                        print(f"[System] Date Check: {date_candidate} data is NOT ready yet.")
                except:
                    continue
            
            # 如果都失败了，只好返回最近的一个交易日碰运气
            return valid_dates[0]

        except Exception as e:
            print(f"[Warn] 获取日期失败: {e}, 将使用今日日期。")
        
        return today

    def _prepare_data(self):
        """
        准备数据阶段：从数据源获取行情、估值、宏观等多维度数据。
        """
        print(f"[System] Connecting to Data Provider: {self.data_provider_class.__name__}...")
        
        # 1. 实例化客户端 (无 with 上下文)
        client = self.data_provider_class()
            
        # 2. 确定目标日期
        # (此时 client 只是一个普通对象，调用方法时会在内部自动 Login)
        self.date = self._get_latest_trading_date(client)
        print(f"[System] Target Date: {self.date}")
        
        # --- A. 获取指数数据 (增加估值维度) ---
        indices_config = self.config['market'].get('indices', [])
        indices_data = []
        
        for item in indices_config:
            code = item['code']
            name = item['name']
            try:
                # 获取日K线 + 估值指标 (peTTM, pbMRQ)
                # adjustflag="3" 不复权，看真实的指数点位
                k_data = client.get_historical_k_data(
                    code=code, start_date=self.date, end_date=self.date,
                    frequency="d", adjust_flag="3", 
                    fields=["close", "pctChg", "amount", "peTTM", "pbMRQ", "volume"]
                )
                
                if not k_data.empty:
                    row = k_data.iloc[0]
                    # 数据清洗与格式化
                    close = float(row['close'])
                    pct_chg = float(row['pctChg']) if row['pctChg'] else 0.0
                    amount = float(row['amount']) / 1e8 # 转为亿
                    pe_ttm = float(row['peTTM']) if row['peTTM'] else 0.0
                    pb_mrq = float(row['pbMRQ']) if row['pbMRQ'] else 0.0
                    
                    indices_data.append({
                        "名称": name,
                        "收盘点位": f"{close:.2f}",
                        "涨跌幅": f"{pct_chg:+.2f}%",
                        "成交额(亿)": f"{amount:.1f}",
                        "PE(TTM)": f"{pe_ttm:.1f}",
                        "PB(MRQ)": f"{pb_mrq:.2f}"
                    })
            except Exception as e:
                print(f"[Warn] Index {code} fetch failed: {e}")

        # 将指数数据转为 Markdown 表格字符串，方便 LLM 理解
        if indices_data:
            df_indices = pd.DataFrame(indices_data)
            self.data_context['market_indices'] = df_indices.to_markdown(index=False)
        else:
            self.data_context['market_indices'] = "暂无指数数据"

        # --- B. 获取重点个股数据 (作为市场情绪风向标) ---
        key_stocks = self.config['market'].get('focus_stocks', [])
        stocks_data_list = []
        
        for code in key_stocks:
            try:
                # 1. 获取基础信息 (名称)
                stock_name = code
                try:
                    base_info = client.get_stock_basic_info(code)
                    if not base_info.empty:
                        # 兼容不同字段名
                        if 'code_name' in base_info.columns:
                            stock_name = base_info.iloc[0]['code_name']
                        elif 'name' in base_info.columns:
                            stock_name = base_info.iloc[0]['name']
                except: pass

                # 2. 获取行情 (多拿一些字段，把决定权交给 Prompt)
                # fields 增加了 'amount' (成交额), 'turn' (换手率), 'peTTM' (估值)
                k_data = client.get_historical_k_data(
                    code=code, start_date=self.date, end_date=self.date,
                    fields=["close", "pctChg", "amount", "turn", "peTTM"]
                )
                
                if not k_data.empty:
                    row = k_data.iloc[0]
                    # 构建原始数据字典
                    stocks_data_list.append({
                        "代码": code,
                        "名称": stock_name,
                        "现价": f"{float(row['close']):.2f}",
                        "涨跌幅": f"{float(row['pctChg']):+.2f}%",
                        "换手率": f"{float(row['turn']):.2f}%" if row['turn'] else "-",
                        "成交额(亿)": f"{float(row['amount'])/1e8:.2f}",
                        "PE(TTM)": f"{float(row['peTTM']):.1f}" if row['peTTM'] else "-"
                    })
                    
            except Exception as e:
                print(f"[Warn] Stock {code} fetch failed: {e}")

        # --- 核心改变：生成 Markdown 表格 ---
        if stocks_data_list:
            df_stocks = pd.DataFrame(stocks_data_list)
            # index=False 去掉 pandas 的行号
            # tablefmt="pipe" 生成标准的 Markdown 表格
            try:
                self.data_context['key_stocks'] = df_stocks.to_markdown(index=False, tablefmt="pipe")
            except ImportError:
                # 兜底
                self.data_context['key_stocks'] = str(stocks_data_list)
        else:
            self.data_context['key_stocks'] = "（暂无重点个股数据）"

        # --- C. 获取宏观数据 (新增：利率环境) ---
        # 宏观数据不一定每天都有，所以我们找最近的一个值
        macro_info = "暂无宏观数据"
        if self.config['market'].get('macro', {}).get('enable', False):
            try:
                lookback = self.config['market']['macro'].get('lookback_days', 30)
                start_dt = (datetime.datetime.strptime(self.date, "%Y-%m-%d") - datetime.timedelta(days=lookback)).strftime("%Y-%m-%d")
                
                # 获取贷款市场报价利率 (LPR) 作为一个宏观锚点
                loan_rate = client.get_loan_rate_data(start_date=start_dt, end_date=self.date)
                if not loan_rate.empty:
                    latest = loan_rate.iloc[-1] # 取最近一次
                    macro_info = f"最近一次报价日期: {latest['pubDate']}, 1年期LPR: {latest.get('loan_rate_1y', 'N/A')}%, 5年期LPR: {latest.get('loan_rate_5y', 'N/A')}%"
            except Exception as e:
                print(f"[Warn] Macro data fetch failed: {e}")
        
        self.data_context['macro_env'] = macro_info

    def generate_llm_report(self):
        """
        生成报告：组装 Content + Format
        """
        # 1. 基础校验
        if not self.data_context.get('market_indices') or "暂无" in self.data_context['market_indices']:
            print("[Gen Error] Market data incomplete.")
            return None

        # 2. 获取模板配置
        try:
            report_cfg = self.config['report']
            content_tmpl = report_cfg['content_template']
            
            # 3. 确定格式指令
            # 这里的逻辑是：Python 知道用户选了什么格式，然后去 Config 里拿对应的“说明书”
            target_format = 'latex' if self.output_format in ['tex', 'latex'] else 'md'
            format_tmpl = report_cfg['formats'].get(target_format)
            
            if not format_tmpl:
                print(f"[Error] Config 中未定义格式 '{target_format}' 的模板")
                return None

            # 4. 组装 Prompt (Decoupling 核心)
            # 先告诉 LLM “分析什么”，再告诉它 “用什么格式输出”
            full_prompt_template = f"{content_tmpl}\n\n{format_tmpl}"

        except KeyError as e:
            print(f"[Error] Config 结构缺失: {e}")
            return None

        # 5. 填充数据 (Data Injection)
        try:
            self.data_context['date'] = self.date
            final_prompt = full_prompt_template.format(**self.data_context)
            
        except KeyError as e:
            print(f"[Error] 模板占位符缺失: {e}")
            return None
        except ValueError as e:
            print(f"[Error] 格式化错误 (可能是 LaTeX 大括号未转义): {e}")
            return None
        
        # 6. 调试预览
        print(f"--- Final Prompt (Format: {target_format}) ---")
        print(final_prompt[:500] + "\n...")

        # 7. 调用 LLM
        llm_cfg = self.config['llm']
        try:
            client = OpenAI(api_key=llm_cfg['api_key'], base_url=llm_cfg['base_url'])
            response = client.chat.completions.create(
                model=llm_cfg['model_name'],
                messages=[
                    {"role": "system", "content": "你是一个专业的量化金融分析系统。"},
                    {"role": "user", "content": final_prompt}
                ],
                temperature=llm_cfg.get('temperature', 0.3)
            )
            return self._clean_llm_output(response.choices[0].message.content)
        except Exception as e:
            print(f"[LLM Error] {e}")
            traceback.print_exc()
            return None

    def _clean_llm_output(self, content):
        # 保持原有逻辑
        if self.output_format in ['tex', 'latex']:
            pattern = r"^```(latex|tex)?\s*|\s*```$"
            return re.sub(pattern, "", content, flags=re.MULTILINE).strip()
        return content

    def save_report(self, content):
        # 保持原有逻辑
        if not content: return
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        ext = "tex" if self.output_format in ['tex', 'latex'] else "md"
        filename = f"{self.date}_Market_Report.{ext}"
        file_path = os.path.join(self.output_dir, filename)
        with open(file_path, 'w', encoding='utf-8') as f: f.write(content)
        print(f"[Module] Report saved: {file_path}")
        return file_path

    def run(self):
        print(f"--- Start Report Generation (Format: {self.output_format}) ---")
        self._prepare_data()
        content = self.generate_llm_report()
        if content:
            return self.save_report(content)
        return None