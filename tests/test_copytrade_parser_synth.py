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


def test_symbol_next_to_the_side_beats_a_promo_footer():
    from backend.copytrade.parser import parse_message
    msg = ("🔵🔵09 NOVEMBER FREE signal forecast: GBPCHF \n\nGBPCHF SELL \n\nENTRY @ 1.09802 \nSL:\xa0\xa0 1.09989 \n\n"
           "TP1:\xa0\xa0\xa01.09625 \nTP2:\xa0\xa01.09409\n\n⭐️⭐️⭐️Become a VIP member  > easyforexpips.com\n💻📱Our XAUUSD Channel👉 CLICK")
    p = parse_message(msg)
    assert p.kind == "OPEN" and p.symbol == "GBPCHF" and p.side == "SELL"
    assert p.entry == 1.09802 and p.sl == 1.09989 and p.tps[:2] == [1.09625, 1.09409]


def test_first_symbol_wins_without_a_side_next_to_it():
    from backend.copytrade.parser import find_symbol
    assert find_symbol("EURCAD: SELL @1.57910 SL 1.58209 TP 1.57627. Our XAUUSD channel") == "EURCAD"
    assert find_symbol("US TECH 100 sell 21000 sl 21100 tp 20800") == "NAS100"
    assert find_symbol("BUY XAU/USD 2350") == "XAUUSD"


def test_vip_teaser_with_withheld_target_is_not_a_signal():
    from backend.copytrade.parser import parse_message
    msg = ("XAUUSD SELL NOW 3656\nSL. 3660\nTp \nONLY 30 PEOPLE ALLOWED😮\n➖➖➖➖➖\nGold GROUP \n➖➖➖➖➖➖➖➖\n"
           "https://t.me/+AbCdEf123\n\nJOIN VIP GROUP")
    p = parse_message(msg)
    assert p.kind == "NONE" and any("teaser" in n for n in p.notes)


def test_real_signal_with_a_sales_footer_is_still_a_signal():
    from backend.copytrade.parser import parse_message
    msg = ("GBPCHF SELL\nENTRY @ 1.09802\nSL: 1.09989\nTP1: 1.09625\nTP2: 1.09409\n"
           "Become a VIP member > easyforexpips.com\nJOIN VIP GROUP https://t.me/+AbCdEf123")
    assert parse_message(msg).kind == "OPEN"


def test_signal_without_any_target_label_is_still_a_signal():
    from backend.copytrade.parser import parse_message
    p = parse_message("GOLD SELL NOW 3656 SL 3660 JOIN VIP GROUP")
    assert p.kind == "OPEN" and p.sl == 3660
