"""Concept taxonomy — our canonical financial-line-item codes.

The fundamental-analysis module computes ratios like P/E, ROE, debt /
equity, op-margin, growth. Those ratios all reduce to ~25 underlying
line items. We define a canonical code for each one and map both
DART (K-IFRS account_id) and SEC (us-gaap XBRL tag) to it.

Keeping this in ONE place means:
- A new ratio in the analysis layer never has to learn DART/SEC details.
- Adding a new concept (e.g. R&D expense) is a single registry edit
  plus one mapping per market.
- Audit: ``financial_facts.raw_concept`` always carries the source
  name so we can spot mismatches.

Sources for the mapping:
- DART: account_id values seen in ``OpenDartReader.finstate`` and the
  ifrs/k-gaap taxonomies (https://opendart.fss.or.kr/guide/main.do).
- SEC: standard us-gaap tags published in the FRAB/SEC taxonomy guide
  (https://www.sec.gov/info/edgar/edgartaxonomies).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Statement(str, Enum):
    INCOME = "income"
    BALANCE = "balance"
    CASHFLOW = "cashflow"


@dataclass(frozen=True)
class Concept:
    code: str
    statement: Statement
    description: str


# Canonical concept registry — these strings appear in the
# `financial_facts.concept` column.
REGISTRY: dict[str, Concept] = {c.code: c for c in [
    # Income statement
    Concept("REVENUE", Statement.INCOME, "Total revenue / sales"),
    Concept("COGS", Statement.INCOME, "Cost of goods sold"),
    Concept("GROSS_PROFIT", Statement.INCOME, "Gross profit = Revenue - COGS"),
    Concept("OPERATING_INCOME", Statement.INCOME, "Operating income (EBIT)"),
    Concept("NET_INCOME", Statement.INCOME, "Net income attributable to parent shareholders"),
    Concept("EPS_BASIC", Statement.INCOME, "Basic earnings per share"),
    Concept("EPS_DILUTED", Statement.INCOME, "Diluted earnings per share"),
    # Balance sheet
    Concept("TOTAL_ASSETS", Statement.BALANCE, "Total assets"),
    Concept("CURRENT_ASSETS", Statement.BALANCE, "Current assets"),
    Concept("CASH", Statement.BALANCE, "Cash and cash equivalents"),
    Concept("TOTAL_LIABILITIES", Statement.BALANCE, "Total liabilities"),
    Concept("CURRENT_LIABILITIES", Statement.BALANCE, "Current liabilities"),
    Concept("LONG_TERM_DEBT", Statement.BALANCE, "Long-term debt"),
    Concept("TOTAL_EQUITY", Statement.BALANCE, "Total stockholders' equity"),
    Concept("SHARES_OUTSTANDING", Statement.BALANCE, "Common shares outstanding"),
    # Cash flow
    Concept("CFO", Statement.CASHFLOW, "Cash from operations"),
    Concept("CFI", Statement.CASHFLOW, "Cash from investing"),
    Concept("CFF", Statement.CASHFLOW, "Cash from financing"),
    Concept("CAPEX", Statement.CASHFLOW, "Capital expenditures (usually negative on CF statement)"),
    Concept("FREE_CASH_FLOW", Statement.CASHFLOW, "FCF = CFO - |CAPEX|"),
    Concept("DIVIDENDS_PAID", Statement.CASHFLOW, "Dividends paid"),
    # Wave 2 — extended concepts
    Concept("EBITDA", Statement.INCOME, "Earnings Before Interest, Taxes, Depreciation, Amortization"),
    Concept("DEPRECIATION_AMORT", Statement.CASHFLOW, "Depreciation and amortization"),
    Concept("INTEREST_EXPENSE", Statement.INCOME, "Interest expense"),
    Concept("TAX_EXPENSE", Statement.INCOME, "Income tax expense"),
    Concept("SGA", Statement.INCOME, "Selling, general & administrative expense"),
    Concept("RND_EXPENSE", Statement.INCOME, "Research & development expense"),
    Concept("INVENTORY", Statement.BALANCE, "Inventory"),
    Concept("RECEIVABLES", Statement.BALANCE, "Accounts receivable"),
    Concept("PAYABLES", Statement.BALANCE, "Accounts payable"),
    Concept("SHORT_TERM_DEBT", Statement.BALANCE, "Short-term debt / current borrowings"),
    Concept("PROPERTY_PLANT_EQ", Statement.BALANCE, "Property, plant and equipment (net)"),
    Concept("RETAINED_EARNINGS", Statement.BALANCE, "Retained earnings"),
    Concept("GOODWILL", Statement.BALANCE, "Goodwill"),
    Concept("INTANGIBLES", Statement.BALANCE, "Intangible assets (excluding goodwill)"),
    Concept("MINORITY_INTEREST", Statement.BALANCE, "Non-controlling interest"),
    Concept("PREFERRED_STOCK", Statement.BALANCE, "Preferred stock par"),
    Concept("STOCK_BUYBACK", Statement.CASHFLOW, "Repurchase of common stock"),
    Concept("STOCK_ISSUED", Statement.CASHFLOW, "Proceeds from issuance of common stock"),
    Concept("WORKING_CAPITAL_CHG", Statement.CASHFLOW, "Change in working capital (from CFO adj.)"),
]}


# ── DART (K-IFRS) account_id mapping ──
# Values seen in OpenDartReader.finstate("ALL" detail). Multiple
# account_ids can map to one canonical concept (different statement
# formats / consolidation levels). The loader takes the first match
# whose values look sane.
DART_MAP: dict[str, list[str]] = {
    "REVENUE": ["ifrs-full_Revenue", "ifrs_Revenue", "매출액"],
    "COGS": ["ifrs-full_CostOfSales", "매출원가"],
    "GROSS_PROFIT": ["ifrs-full_GrossProfit", "매출총이익"],
    "OPERATING_INCOME": ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities", "영업이익"],
    "NET_INCOME": [
        "ifrs-full_ProfitLossAttributableToOwnersOfParent",
        "ifrs-full_ProfitLoss",
        "당기순이익(손실)",
    ],
    "EPS_BASIC": ["ifrs-full_BasicEarningsLossPerShare", "기본주당이익(손실)"],
    "EPS_DILUTED": ["ifrs-full_DilutedEarningsLossPerShare"],
    "TOTAL_ASSETS": ["ifrs-full_Assets", "자산총계"],
    "CURRENT_ASSETS": ["ifrs-full_CurrentAssets", "유동자산"],
    "CASH": ["ifrs-full_CashAndCashEquivalents", "현금및현금성자산"],
    "TOTAL_LIABILITIES": ["ifrs-full_Liabilities", "부채총계"],
    "CURRENT_LIABILITIES": ["ifrs-full_CurrentLiabilities", "유동부채"],
    "LONG_TERM_DEBT": ["ifrs-full_NoncurrentBorrowings"],
    "TOTAL_EQUITY": ["ifrs-full_Equity", "자본총계"],
    "CFO": ["ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름"],
    "CFI": ["ifrs-full_CashFlowsFromUsedInInvestingActivities", "투자활동현금흐름"],
    "CFF": ["ifrs-full_CashFlowsFromUsedInFinancingActivities", "재무활동현금흐름"],
    "CAPEX": ["dart_PaymentsForPropertyPlantAndEquipment"],
    "DIVIDENDS_PAID": ["ifrs-full_DividendsPaidClassifiedAsFinancingActivities"],
    "DEPRECIATION_AMORT": [
        "ifrs-full_DepreciationAndAmortisationExpense",
        "dart_DepreciationAndAmortisationExpense",
        "감가상각비와상각비",
    ],
    "INTEREST_EXPENSE": [
        "ifrs-full_InterestExpense", "이자비용",
    ],
    "TAX_EXPENSE": [
        "ifrs-full_IncomeTaxExpenseContinuingOperations",
        "ifrs-full_TaxExpenseContinuingOperations",
        "법인세비용",
    ],
    "SGA": [
        "ifrs-full_SellingGeneralAndAdministrativeExpense",
        "판매비와관리비",
    ],
    "RND_EXPENSE": [
        "ifrs-full_ResearchAndDevelopmentExpense", "연구개발비",
    ],
    "INVENTORY": ["ifrs-full_Inventories", "재고자산"],
    "RECEIVABLES": [
        "ifrs-full_TradeAndOtherCurrentReceivables", "매출채권및기타채권",
    ],
    "PAYABLES": [
        "ifrs-full_TradeAndOtherCurrentPayables", "매입채무및기타채무",
    ],
    "SHORT_TERM_DEBT": [
        "ifrs-full_CurrentBorrowings", "단기차입금",
    ],
    "PROPERTY_PLANT_EQ": ["ifrs-full_PropertyPlantAndEquipment", "유형자산"],
    "RETAINED_EARNINGS": ["ifrs-full_RetainedEarnings", "이익잉여금"],
    "GOODWILL": ["ifrs-full_Goodwill", "영업권"],
    "INTANGIBLES": ["ifrs-full_IntangibleAssetsOtherThanGoodwill", "무형자산"],
    "MINORITY_INTEREST": [
        "ifrs-full_NoncontrollingInterests", "비지배지분",
    ],
    "STOCK_BUYBACK": [
        "ifrs-full_PaymentsForRepurchaseOfTreasuryShares", "자기주식의취득",
    ],
    "STOCK_ISSUED": [
        "ifrs-full_ProceedsFromIssuingShares", "주식의발행",
    ],
}


# ── SEC EDGAR us-gaap XBRL tag mapping ──
SEC_MAP: dict[str, list[str]] = {
    "REVENUE": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "COGS": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "GROSS_PROFIT": ["GrossProfit"],
    "OPERATING_INCOME": ["OperatingIncomeLoss"],
    "NET_INCOME": ["NetIncomeLoss", "ProfitLoss"],
    "EPS_BASIC": ["EarningsPerShareBasic"],
    "EPS_DILUTED": ["EarningsPerShareDiluted"],
    "TOTAL_ASSETS": ["Assets"],
    "CURRENT_ASSETS": ["AssetsCurrent"],
    "CASH": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "TOTAL_LIABILITIES": ["Liabilities"],
    "CURRENT_LIABILITIES": ["LiabilitiesCurrent"],
    "LONG_TERM_DEBT": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "TOTAL_EQUITY": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "SHARES_OUTSTANDING": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
    "CFO": ["NetCashProvidedByUsedInOperatingActivities"],
    "CFI": ["NetCashProvidedByUsedInInvestingActivities"],
    "CFF": ["NetCashProvidedByUsedInFinancingActivities"],
    "CAPEX": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "DIVIDENDS_PAID": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "DEPRECIATION_AMORT": [
        "DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
        "Depreciation",
    ],
    "INTEREST_EXPENSE": ["InterestExpense", "InterestExpenseDebt"],
    "TAX_EXPENSE": ["IncomeTaxExpenseBenefit"],
    "SGA": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    ],
    "RND_EXPENSE": ["ResearchAndDevelopmentExpense"],
    "INVENTORY": ["InventoryNet"],
    "RECEIVABLES": [
        "AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
    ],
    "PAYABLES": [
        "AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent",
    ],
    "SHORT_TERM_DEBT": [
        "ShortTermBorrowings", "DebtCurrent", "LongTermDebtCurrent",
    ],
    "PROPERTY_PLANT_EQ": ["PropertyPlantAndEquipmentNet"],
    "RETAINED_EARNINGS": ["RetainedEarningsAccumulatedDeficit"],
    "GOODWILL": ["Goodwill"],
    "INTANGIBLES": [
        "IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet",
    ],
    "MINORITY_INTEREST": [
        "MinorityInterest", "StockholdersEquityAttributableToNoncontrollingInterest",
    ],
    "PREFERRED_STOCK": ["PreferredStockValue"],
    "STOCK_BUYBACK": [
        "PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity",
    ],
    "STOCK_ISSUED": [
        "ProceedsFromIssuanceOfCommonStock", "StockIssuedDuringPeriodValueNewIssues",
    ],
}


def kr_concept_for(raw_account_id: str) -> str | None:
    """Inverse lookup: DART raw account_id → canonical concept.
    Returns None when the account_id isn't in our taxonomy."""
    for canonical, raws in DART_MAP.items():
        if raw_account_id in raws:
            return canonical
    return None


def us_concept_for(raw_tag: str) -> str | None:
    for canonical, raws in SEC_MAP.items():
        if raw_tag in raws:
            return canonical
    return None


def required_concepts_for_ratios() -> set[str]:
    """The minimum concept set Phase 2 fundamental ratios need.

    Used by data-quality checks: if any of these is missing for a
    ticker in a period, the fundamental score for that period is
    flagged low-confidence."""
    return {
        "REVENUE", "OPERATING_INCOME", "NET_INCOME",
        "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY",
        "CFO", "CAPEX",
        "EPS_BASIC", "SHARES_OUTSTANDING",
    }
