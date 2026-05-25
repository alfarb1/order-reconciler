from ._named import NamedRetailerParser


class FinishLineParser(NamedRetailerParser):
    name = "finishline"
    retailer = "Finish Line"
    domains = ("finishline.com", "email.finishline.com")
    subject_hints = ("shipped", "your order", "on the way")
    priority = 50
