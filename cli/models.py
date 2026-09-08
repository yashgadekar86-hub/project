from enum import Enum


class AnalystType(str, Enum):
    MARKET = "market"
    # Wire value stays "social" for saved-config and string-keyed-caller
    # back-compat; the user-facing label is "Sentiment Analyst".
    SOCIAL = "social"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
    # Precious metals / commodities (gold, silver, oil...). The pipeline keeps
    # the fundamentals analyst but repurposes it as a macro-supply-demand
    # analyst (companies have balance sheets; commodities have real yields,
    # a dollar index, and central-bank demand).
    COMMODITY = "commodity"
