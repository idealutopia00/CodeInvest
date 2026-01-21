# baostock_data_source.py
import baostock as bs
import pandas as pd
from typing import List, Optional, Callable, Any, Dict
import logging
from .data_source_interface import FinancialDataSource, DataSourceError, NoDataFoundError
from .utils import baostock_login_context

logger = logging.getLogger(__name__)

# --- Constants & Configuration ---
class BaostockConfig:
    SUCCESS_CODE = '0'
    NO_RECORD_CODE = '10002' # Sometimes Baostock returns this for empty data
    DEFAULT_K_FIELDS = [
        "date", "code", "open", "high", "low", "close", "preclose",
        "volume", "amount", "adjustflag", "turn", "tradestatus",
        "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST"
    ]

class BaostockDataSource(FinancialDataSource):
    """
    使用 Baostock 库实现的具体金融数据源类。
    """
    
    
    def _execute_query(self, query_func: Callable, func_name_for_log: str, **kwargs) -> pd.DataFrame:
        """
        统一执行 Baostock 查询的引擎。
        处理：登录上下文、错误检查、数据解析、异常映射。
        """
        try:
            with baostock_login_context():
                logger.info(f"Fetching {func_name_for_log} with params: {kwargs}")
                
                # Execute the Baostock API call
                rs = query_func(**kwargs)

                # Error Handling
                if rs.error_code != BaostockConfig.SUCCESS_CODE:
                    err_msg = f"{rs.error_msg} (code: {rs.error_code})"
                    logger.error(f"Baostock API error ({func_name_for_log}): {err_msg}")
                    
                    if "no record found" in rs.error_msg.lower() or rs.error_code == BaostockConfig.NO_RECORD_CODE:
                        raise NoDataFoundError(f"No {func_name_for_log} found. Msg: {err_msg}")
                    else:
                        raise DataSourceError(f"Baostock API failure ({func_name_for_log}): {err_msg}")

                # Data Extraction
                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())

                # Empty Result Check
                if not data_list:
                    logger.warning(f"Empty result set for {func_name_for_log} with params: {kwargs}")
                    raise NoDataFoundError(f"No {func_name_for_log} found (empty result).")

                # DataFrame Construction
                result_df = pd.DataFrame(data_list, columns=rs.fields)
                logger.info(f"Retrieved {len(result_df)} records for {func_name_for_log}.")
                return result_df

        except (DataSourceError, ValueError) as e:
            # Pass through known custom errors
            raise e
        except Exception as e:
            # Catch unexpected system errors
            logger.exception(f"Unexpected error in {func_name_for_log}: {e}")
            raise DataSourceError(f"Unexpected error in {func_name_for_log}: {e}") from e

    
    def _format_fields(self, fields: Optional[List[str]], default_fields: List[str]) -> str:
        """Helper to format field lists into strings."""
        if not fields:
            return ",".join(default_fields)
        return ",".join(fields)

    # --- Implementation of Interface Methods ---
    
    def get_historical_k_data(self, code: str, start_date: str, end_date: str, frequency: str = "d", adjust_flag: str = "3", fields: Optional[List[str]] = None) -> pd.DataFrame:
        """
        获取历史 K 线数据。
        
        [输入]
        code: 股票代码，如 'sh.600000' 或 'sz.000001'。
        start_date: 开始日期 (YYYY-MM-DD)。
        end_date: 结束日期 (YYYY-MM-DD)。
        frequency: 数据频度。'd'=日k线, 'w'=周, 'm'=月, '5'=5分钟, '15'=15分钟, '30'=30分钟, '60'=60分钟。
        adjust_flag: 复权类型。'3'=不复权(默认), '1'=后复权, '2'=前复权。
        fields: 返回指标列表。为空则返回所有默认指标。
        
        [输出]
        DataFrame包含字段(视fields而定):
        date(日期), code(代码), open(开盘), high(最高), low(最低), close(收盘),
        preclose(前收), volume(成交量), amount(成交额), adjustflag(复权状态),
        turn(换手率), tradestatus(交易状态), pctChg(涨跌幅), peTTM(滚动市盈率),
        pbMRQ(市净率), psTTM(滚动市销率), pcfNcfTTM(滚动市现率), isST(是否ST)。
        """
        formatted_fields = self._format_fields(fields, BaostockConfig.DEFAULT_K_FIELDS)
        return self._execute_query(bs.query_history_k_data_plus, "Historical K-Data", code=code, fields=formatted_fields, start_date=start_date, end_date=end_date, frequency=frequency, adjustflag=adjust_flag)

    def get_stock_basic_info(self, code: str) -> pd.DataFrame:
        """
        获取证券基本资料。
        
        [输入]
        code: 股票代码，如 'sh.600000'。如果为空，则默认返回所有股票的基础信息（视接口版本而定）。
        
        [输出]
        DataFrame包含字段:
        code(代码), code_name(名称), ipoDate(上市日期), outDate(退市日期),
        type(类型), status(上市状态)。
        """
        return self._execute_query(bs.query_stock_basic, "Stock Basic Info", code=code)

    def get_dividend_data(self, code: str, year: str, year_type: str = "report") -> pd.DataFrame:
        """
        获取除权除息信息（分红送转）。
        
        [输入]
        code: 股票代码，如 'sh.600000'。
        year: 年份，如 '2017'。
        year_type: 年份类别。'report'=预案公告年份, 'operate'=除权除息年份。
        
        [输出]
        DataFrame包含字段:
        code(代码), dividPreNoticeDate(预案公告日), dividAgmPumDate(股东大会公告日),
        dividStkDate(股权登记日), dividStockMarketDate(除权除息日),
        dividPayDate(派息日), planDiviCash(派息金额), planDiviStock(送股比例), ...等。
        """
        return self._execute_query(bs.query_dividend_data, "Dividend Data", code=code, year=year, yearType=year_type)

    def get_adjust_factor_data(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取复权因子信息。
        
        [输入]
        code: 股票代码。
        start_date: 开始日期。
        end_date: 结束日期。
        
        [输出]
        DataFrame包含字段:
        code(代码), dividDate(除权除息日), foreAdjustFactor(前复权因子),
        backAdjustFactor(后复权因子), adjustFactor(复权因子)。
        """
        return self._execute_query(bs.query_adjust_factor, "Adjust Factor", code=code, start_date=start_date, end_date=end_date)

    # --- Financial Reports (Previously handled by global helper) ---
    
    def get_profit_data(self, code: str, year: str, quarter: int) -> pd.DataFrame:
        """
        获取季频盈利能力数据。
        
        [输入]
        code: 股票代码。
        year: 统计年份。
        quarter: 统计季度 (1, 2, 3, 4)。
        
        [输出]
        DataFrame包含字段:
        code, pubDate(发布日期), statDate(统计截止日), roeAvg(净资产收益率),
        npMargin(净利率), gpMargin(毛利率), netProfit(净利润), epsTTM(每股收益),
        mbRevenue(主营营业收入), totalShare(总股本), liqaShare(流通股本)。
        """
        return self._execute_query(bs.query_profit_data, "Profitability", code=code, year=year, quarter=quarter)

    def get_operation_data(self, code: str, year: str, quarter: int) -> pd.DataFrame:
        """
        获取季频营运能力数据。
        
        [输入]
        code: 股票代码。
        year: 统计年份。
        quarter: 统计季度 (1, 2, 3, 4)。
        
        [输出]
        DataFrame包含字段:
        code, pubDate, statDate, NRTurnRatio(应收账款周转率), NRTurnDays(应收账款周转天数),
        INVTurnRatio(存货周转率), INVTurnDays(存货周转天数), 
        AssetTurnRatio(总资产周转率), ToaTurnRatio(流动资产周转率)。
        """
        return self._execute_query(bs.query_operation_data, "Operation Capability", code=code, year=year, quarter=quarter)

    def get_growth_data(self, code: str, year: str, quarter: int) -> pd.DataFrame:
        """
        获取季频成长能力数据。
        
        [输入]
        code: 股票代码。
        year: 统计年份。
        quarter: 统计季度 (1, 2, 3, 4)。
        
        [输出]
        DataFrame包含字段:
        code, pubDate, statDate, YOYEquity(净资产同比增长率), 
        YOYAsset(总资产同比增长率), YOYNI(净利润同比增长率),
        YOYEPSBasic(基本每股收益同比增长率), YOYPNI(归属母公司股东净利润同比增长率)。
        """
        return self._execute_query(bs.query_growth_data, "Growth Capability", code=code, year=year, quarter=quarter)

    def get_balance_data(self, code: str, year: str, quarter: int) -> pd.DataFrame:
        """
        获取季频偿债能力数据。
        
        [输入]
        code: 股票代码。
        year: 统计年份。
        quarter: 统计季度 (1, 2, 3, 4)。
        
        [输出]
        DataFrame包含字段:
        code, pubDate, statDate, currentRatio(流动比率), quickRatio(速动比率),
        cashRatio(现金比率), YOYLiability(总负债同比增长率), 
        liabilityToAsset(资产负债率), assetToEquity(权益乘数)。
        """
        return self._execute_query(bs.query_balance_data, "Balance Sheet", code=code, year=year, quarter=quarter)

    def get_cash_flow_data(self, code: str, year: str, quarter: int) -> pd.DataFrame:
        """
        获取季频现金流量数据。
        
        [输入]
        code: 股票代码。
        year: 统计年份。
        quarter: 统计季度 (1, 2, 3, 4)。
        
        [输出]
        DataFrame包含字段: 
        code, pubDate, statDate, CAToAsset(流动资产除以总资产), 
        NCAToAsset(非流动资产除以总资产), tangibleAssetToAsset(有形资产除以总资产),
        ebitToInterest(已获利息倍数), CFOToOR(经营活动产生的现金流量净额/营业收入),
        CFOToNP(经营性现金净流量/净利润), CFOToGr(经营性现金净流量/营业总收入)。
        """
        return self._execute_query(bs.query_cash_flow_data, "Cash Flow", code=code, year=year, quarter=quarter)

    def get_dupont_data(self, code: str, year: str, quarter: int) -> pd.DataFrame:
        """
        获取季频杜邦指数数据。
        
        [输入]
        code: 股票代码。
        year: 统计年份。
        quarter: 统计季度 (1, 2, 3, 4)。
        
        [输出]
        DataFrame包含字段:
        code, pubDate, statDate, dupontROE(净资产收益率), 
        dupontAssetStoEquity(权益乘数), dupontAssetTurn(总资产周转率),
        dupontPnitoni(归母净利润/净利润), dupontNitogr(净利润/营业总收入),
        dupontTaxBurden(净利润/利润总额), dupontIntburden(利润总额/息税前利润),
        dupontEbittogr(息税前利润/营业总收入)。
        """
        return self._execute_query(bs.query_dupont_data, "DuPont Analysis", code=code, year=year, quarter=quarter)

    # --- Reports & Forecasts ---

    def get_performance_express_report(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取业绩快报信息。
        
        [输入]
        code: 股票代码。
        start_date: 发布日期开始范围。
        end_date: 发布日期结束范围。
        
        [输出]
        DataFrame包含字段:
        code, performanceExpPubDate(发布日期), performanceExpStatDate(统计截止日),
        performanceExpUpdateDate(更新日期), performanceExpEPS(业绩快报EPS),
        performanceExpTotalAsset(业绩快报总资产), performanceExpNetAsset(业绩快报净资产),
        performanceExpRevenue(业绩快报营业收入), performanceExpNetProfit(业绩快报净利润), ...等。
        """
        return self._execute_query(bs.query_performance_express_report, "Performance Express", code=code, start_date=start_date, end_date=end_date)

    def get_forecast_report(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取业绩预告信息。
        
        [输入]
        code: 股票代码。
        start_date: 发布日期开始范围。
        end_date: 发布日期结束范围。
        
        [输出]
        DataFrame包含字段:
        code, profitForcastExpPubDate(发布日期), profitForcastExpStatDate(统计截止日),
        profitForcastType(业绩预告类型: 预增/预减/扭亏等),
        profitForcastAbstract(业绩预告摘要), profitForcastChgPctUp(预告涨幅上限),
        profitForcastChgPctDwn(预告涨幅下限)。
        """
        return self._execute_query(bs.query_forecast_report, "Forecast Report", code=code, start_date=start_date, end_date=end_date)

    # --- Market & Industry ---

    def get_stock_industry(self, code: Optional[str] = None, date: Optional[str] = None) -> pd.DataFrame:
        """
        获取行业分类数据。
        
        [输入]
        code: 股票代码 (可选)。
        date: 查询日期 (可选)。
        
        [输出]
        DataFrame包含字段:
        updateDate(更新日期), code(代码), code_name(名称), 
        industry(所属行业), industryClassification(行业分类标准)。
        """
        return self._execute_query(bs.query_stock_industry, "Stock Industry", code=code, date=date)

    def get_sz50_stocks(self, date: Optional[str] = None) -> pd.DataFrame:
        """
        获取上证50成分股信息。
        
        [输入]
        date: 查询日期 (YYYY-MM-DD)。
        
        [输出]
        DataFrame包含字段:
        updateDate(更新日期), code(代码), code_name(名称)。
        """
        return self._execute_query(bs.query_sz50_stocks, "SZSE 50 Constituents", date=date)

    def get_hs300_stocks(self, date: Optional[str] = None) -> pd.DataFrame:
        """
        获取沪深300成分股信息。
        
        [输入]
        date: 查询日期 (YYYY-MM-DD)。
        
        [输出]
        DataFrame包含字段:
        updateDate(更新日期), code(代码), code_name(名称)。
        """
        return self._execute_query(bs.query_hs300_stocks, "CSI 300 Constituents", date=date)

    def get_zz500_stocks(self, date: Optional[str] = None) -> pd.DataFrame:
        """
        获取中证500成分股信息。
        
        [输入]
        date: 查询日期 (YYYY-MM-DD)。
        
        [输出]
        DataFrame包含字段:
        updateDate(更新日期), code(代码), code_name(名称)。
        """
        return self._execute_query(bs.query_zz500_stocks, "CSI 500 Constituents", date=date)

    def get_all_stock(self, date: Optional[str] = None) -> pd.DataFrame:
        """
        获取指定日期的全证券代码。
        
        [输入]
        date: 查询日期 (YYYY-MM-DD)。
        
        [输出]
        DataFrame包含字段:
        code(代码), tradeStatus(交易状态 1:交易, 0:停牌), code_name(名称)。
        """
        return self._execute_query(bs.query_all_stock, "All Stock List", day=date)

    def get_trade_dates(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取交易日信息。
        
        [输入]
        start_date: 开始日期。
        end_date: 结束日期。
        
        [输出]
        DataFrame包含字段:
        calendar_date(日期), is_trading_day(是否交易日 '1'或'0')。
        """
        return self._execute_query(bs.query_trade_dates, "Trade Dates", start_date=start_date, end_date=end_date)

    # --- Macro Data ---
    
    def get_deposit_rate_data(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取存款利率数据。
        
        [输入]
        start_date: 开始日期。
        end_date: 结束日期。
        
        [输出]
        DataFrame包含字段:
        pubDate(发布日期), 
        deposit_rate_3m(3个月存款利率), deposit_rate_6m(6个月), 
        deposit_rate_1y(1年), deposit_rate_2y(2年), 
        deposit_rate_3y(3年), deposit_rate_5y(5年)。
        """
        return self._execute_query(bs.query_deposit_rate_data, "Deposit Rate", start_date=start_date, end_date=end_date)

    def get_loan_rate_data(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取贷款利率数据。
        
        [输入]
        start_date: 开始日期。
        end_date: 结束日期。
        
        [输出]
        DataFrame包含字段:
        pubDate(发布日期), loan_rate_6m(6个月贷款利率), 
        loan_rate_6m_1y(6个月-1年), loan_rate_1y_3y(1-3年), 
        loan_rate_3y_5y(3-5年), loan_rate_5y(5年以上)。
        """
        return self._execute_query(bs.query_loan_rate_data, "Loan Rate", start_date=start_date, end_date=end_date)

    def get_required_reserve_ratio_data(self, start_date: Optional[str] = None, end_date: Optional[str] = None, year_type: str = '0') -> pd.DataFrame:
        """
        获取存款准备金率数据。
        
        [输入]
        start_date: 开始日期。
        end_date: 结束日期。
        year_type: 年份类型 ('0' 默认)。
        
        [输出]
        DataFrame包含字段:
        pubDate(发布日期), effectiveDate(生效日期), 
        bigSmall(大型金融机构), mediumSmall(中小金融机构)。
        """
        return self._execute_query(bs.query_required_reserve_ratio_data, "Required Reserve Ratio", start_date=start_date, end_date=end_date, yearType=year_type)

    def get_money_supply_data_month(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取货币供应量 (月频)。
        
        [输入]
        start_date: 开始日期。
        end_date: 结束日期。
        
        [输出]
        DataFrame包含字段:
        statYear(统计年份), statMonth(统计月份), 
        m0Month(M0余额), m0YOY(M0同比), 
        m1Month(M1余额), m1YOY(M1同比), 
        m2Month(M2余额), m2YOY(M2同比)。
        """
        return self._execute_query(bs.query_money_supply_data_month, "Monthly Money Supply", start_date=start_date, end_date=end_date)

    def get_money_supply_data_year(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        """
        获取货币供应量 (年频)。
        
        [输入]
        start_date: 开始日期。
        end_date: 结束日期。
        
        [输出]
        DataFrame包含字段:
        statYear(统计年份), m0Year(M0余额), m0YOY(M0同比), 
        m1Year(M1余额), m1YOY(M1同比), m2Year(M2余额), m2YOY(M2同比)。
        """
        return self._execute_query(bs.query_money_supply_data_year, "Yearly Money Supply", start_date=start_date, end_date=end_date)