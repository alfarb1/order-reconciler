from ._named import NamedRetailerParser


class GoatParser(NamedRetailerParser):
    name = "goat"
    retailer = "GOAT"
    domains = ("goat.com",)
    subject_hints = ("shipped", "tracking", "on its way", "your order")
    priority = 50
