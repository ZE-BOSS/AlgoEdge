from backend.copytrade.parser import parse_message


def test_one_second_volatility_indices_keep_their_name():
    """'VOLATILITY 50 1S INDEX' was read as Volatility 50 Index (a different
    instrument): "1S" is split into "1 S" before the symbol is looked up."""
    cases = {
        "VOLATILITY 50 1S INDEX \n\nBUY NOW \n\nTP. 221980\n\nSL. 219100": "Volatility 50 (1s) Index",
        "Volatility 100 (1s) Index BUY NOW TP 1 SL 0.5": "Volatility 100 (1s) Index",
        "V75 1s sell now tp 1 sl 2": "Volatility 75 (1s) Index",
        "V75 1 SELL NOW TP 1 SL 2": "Volatility 75 Index",
        "VOLATILITY 75 INDEX \n\nBUY NOW \n\nTP. 54800\n\nSL. 53400": "Volatility 75 Index",
    }
    for text, want in cases.items():
        assert parse_message(text).symbol == want, text
