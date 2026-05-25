from ._named import NamedRetailerParser


class StockXParser(NamedRetailerParser):
    name = "stockx"
    retailer = "StockX"
    domains = ("stockx.com",)
    subject_hints = ("shipped", "your order", "on the way", "tracking")
    priority = 50
