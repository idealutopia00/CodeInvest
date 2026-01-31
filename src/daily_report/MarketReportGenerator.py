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
        [单函数完整版] 准备数据阶段：从数据源获取行情、估值、宏观等多维度数据。
        包含了：指数、个股、板块ETF、宏观数据的完整获取与清洗逻辑。
        """
        print(f"[System] Connecting to Data Provider: {self.data_provider_class.__name__}...")
        
        # 1. 实例化客户端
        client = self.data_provider_class()
        
        # 2. 确定目标日期
        self.date = self._get_latest_trading_date(client)
        print(f"[System] Target Date: {self.date}")
        self.data_context['date'] = self.date

        # --- 全局 Pandas 设置：防止输出 "..." 省略号 ---
        # 这一步至关重要，确保后续生成的 Markdown 表格是完整的
        pd.set_option('display.max_rows', None)
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 1000)

        # ==========================================
        # Part A. 获取核心指数数据 (Indices)
        # ==========================================
        indices_config = self.config['market'].get('indices', [])
        indices_data = []
        
        for item in indices_config:
            code = item['code']
            try:
                # adjustflag="3": 不复权，看真实的指数点位
                k_data = client.get_historical_k_data(
                    code=code, start_date=self.date, end_date=self.date,
                    frequency="d", adjust_flag="3", 
                    fields=["close", "pctChg", "amount", "peTTM", "pbMRQ"]
                )
                
                if not k_data.empty:
                    row = k_data.iloc[0]
                    indices_data.append({
                        "名称": item['name'],
                        "收盘": f"{float(row['close']):.2f}",
                        "涨跌幅": f"{float(row['pctChg']):+.2f}%" if row['pctChg'] else "0.00%",
                        "成交额(亿)": f"{float(row['amount'])/1e8:.1f}",
                        "PE(TTM)": f"{float(row['peTTM']):.1f}" if row['peTTM'] else "-",
                        "PB(MRQ)": f"{float(row['pbMRQ']):.2f}" if row['pbMRQ'] else "-"
                    })
            except Exception as e:
                print(f"[Warn] Index {code} fetch failed: {e}")

        # 生成指数表格 (Markdown)
        if indices_data:
            try:
                self.data_context['market_indices'] = pd.DataFrame(indices_data).to_markdown(index=False, tablefmt="pipe")
            except Exception:
                self.data_context['market_indices'] = str(indices_data)
        else:
            self.data_context['market_indices'] = "（暂无指数数据）"

        # ==========================================
        # Part B. 获取重点个股数据 (Key Stocks)
        # ==========================================
        key_stocks = self.config['market'].get('focus_stocks', [])
        stocks_data_list = []
        
        for code in key_stocks:
            try:
                # 1. 获取名称 (优先用 code_name)
                stock_name = code
                try:
                    base_info = client.get_stock_basic_info(code)
                    if not base_info.empty:
                        stock_name = base_info.iloc[0].get('code_name', base_info.iloc[0].get('name', code))
                except: pass

                # 2. 获取行情
                k_data = client.get_historical_k_data(
                    code=code, start_date=self.date, end_date=self.date,
                    fields=["close", "pctChg", "amount", "turn", "peTTM"]
                )
                
                if not k_data.empty:
                    row = k_data.iloc[0]
                    stocks_data_list.append({
                        "代码": code,
                        "名称": stock_name,
                        "现价": f"{float(row['close']):.2f}",
                        "涨跌幅": f"{float(row['pctChg']):+.2f}%" if row['pctChg'] else "0.00%",
                        "换手率": f"{float(row['turn']):.2f}%" if row['turn'] else "-",
                        "成交额(亿)": f"{float(row['amount'])/1e8:.2f}",
                        "PE(TTM)": f"{float(row['peTTM']):.1f}" if row['peTTM'] else "-"
                    })
            except Exception as e:
                print(f"[Warn] Stock {code} fetch failed: {e}")

        # 生成个股表格 (Markdown)
        if stocks_data_list:
            try:
                self.data_context['key_stocks'] = pd.DataFrame(stocks_data_list).to_markdown(index=False, tablefmt="pipe")
            except Exception:
                self.data_context['key_stocks'] = str(stocks_data_list)
        else:
            self.data_context['key_stocks'] = "（暂无重点个股数据）"

        # ==========================================
        # Part C. 获取板块/行业 ETF 数据 (Sectors)
        # ==========================================
        sectors_config = self.config['market'].get('sectors', [])
        sectors_data = []
        
        # 1. 获取基准涨跌幅 (上证指数，用于计算超额收益)
        benchmark_pct = 0.0
        try:
            bench_k = client.get_historical_k_data(code="sh.000001", start_date=self.date, end_date=self.date, fields=["pctChg"])
            if not bench_k.empty:
                benchmark_pct = float(bench_k.iloc[0]['pctChg'])
        except: pass
        
        # 2. 遍历板块配置
        for item in sectors_config:
            code = item['code']
            try:
                k_data = client.get_historical_k_data(
                    code=code, start_date=self.date, end_date=self.date,
                    frequency="d", adjust_flag="3", 
                    fields=["close", "pctChg", "amount", "turn"]
                )
                
                if not k_data.empty:
                    row = k_data.iloc[0]
                    pct = float(row['pctChg']) if row['pctChg'] else 0.0
                    excess = pct - benchmark_pct # 超额收益 = 板块涨幅 - 大盘涨幅
                    
                    sectors_data.append({
                        "板块名称": item['name'],
                        "ETF代码": code,
                        "涨跌幅": f"{pct:+.2f}%",
                        "超额收益": f"{excess:+.2f}%", 
                        "成交(亿)": f"{float(row['amount'])/1e8:.1f}",
                        "换手率": f"{float(row['turn']):.2f}%" if row['turn'] else "-"
                    })
            except Exception as e:
                print(f"[Warn] Sector {code} fetch failed: {e}")
        
        # 生成板块表格 (Markdown)
        if sectors_data:
            # 按涨跌幅降序排列，让热点一目了然
            sectors_data.sort(key=lambda x: float(x['涨跌幅'].strip('%')), reverse=True)
            try:
                self.data_context['sector_performance'] = pd.DataFrame(sectors_data).to_markdown(index=False, tablefmt="pipe")
            except Exception:
                self.data_context['sector_performance'] = str(sectors_data)
        else:
            self.data_context['sector_performance'] = "（暂无板块数据）"

        # ==========================================
        # Part D. 获取宏观数据 (Macro Environment)
        # ==========================================
        macro_lines = []
        if self.config['market'].get('macro', {}).get('enable', False):
            try:
                # 往前查 40 天，因为 LPR/M2 是月频数据
                lookback_days = self.config['market']['macro'].get('lookback_days', 365)
                start_dt = (datetime.datetime.strptime(self.date, "%Y-%m-%d") - datetime.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
                
                # 1. 利率数据 (LPR)
                lpr_df = client.get_loan_rate_data(start_date=start_dt, end_date=self.date)
                if not lpr_df.empty:
                    latest = lpr_df.iloc[-1]
                    lpr_str = f"1. **利率环境 (LPR)**: 最近报价日 {latest['pubDate']}，1年期 {latest.get('loan_rate_1y', '- ')}%，5年期 {latest.get('loan_rate_5y', '- ')}%。"
                    macro_lines.append(lpr_str)
                else:
                    macro_lines.append("1. **利率环境**: 近期无 LPR 数据更新。")

                # 2. 货币供应量 (M2)
                m2_df = client.get_money_supply_data_month(start_date=start_dt, end_date=self.date)
                if not m2_df.empty:
                    latest = m2_df.iloc[-1]
                    m2_str = f"2. **货币供应 (M2)**: {latest['statYear']}-{latest['statMonth']} M2同比增速 {float(latest['m2YOY']):.2f}%。"
                    macro_lines.append(m2_str)
                
                # 3. 如果没抓到数据
                if not macro_lines:
                    macro_lines.append("（Baostock接口近期无宏观数据返回，建议参考央行官网）")

            except Exception as e:
                print(f"[Warn] Macro data fetch failed: {e}")
                macro_lines.append(f"宏观数据获取异常: {e}")
        else:
            macro_lines.append("（宏观数据分析已在配置中关闭）")
        
        self.data_context['macro_env'] = "\n".join(macro_lines)

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