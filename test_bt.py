import asyncio
from fastapi.testclient import TestClient
from backend.api.routes.backtest import router
from backend.data.models import User
from fastapi import FastAPI, Depends

app = FastAPI()

async def mock_get_current_user():
    return User(email="test@test.com", id=1)

# Override dependencies
app.include_router(router)
app.dependency_overrides[router.dependencies[0].dependency if router.dependencies else mock_get_current_user] = mock_get_current_user

# Need to override get_current_user directly from the module
from backend.api.deps import get_current_user
app.dependency_overrides[get_current_user] = mock_get_current_user

client = TestClient(app)

def test_backtest():
    payload = {
        "strategy_id": "SMC_v1",
        "symbol": "Volatility 75 Index",
        "timeframe": "M15",
        "candle_count": 500,
        "initial_balance": 10000.0,
        "confluence_threshold": 55,
        "swing_length": 5,
        "ob_impulse_ratio": 1.5,
        "fvg_min_gap_pips": 3.0,
        "liq_sweep_min_pips": 2.0,
        "max_spread_pips": 3.0,
        "session_filter_enabled": False,
        "news_filter_enabled": False,
        "risk_per_trade_pct": 1.0,
        "min_rr": 1.0,
        "max_daily_consecutive_losses": 3,
        "max_weekly_consecutive_losses": 5,
        "max_concurrent_positions": 3,
        "tp_count": 3,
        "tp1_rr": 1.0,
        "tp2_rr": 3.0,
        "tp3_rr": 5.0,
        "tp4_rr": 10.0,
        "tp5_rr": 15.0,
        "tp_splits": "40,30,30",
        "be_trigger_rr": 1.0,
        "be_buffer_pips": 2.0,
        "trail_method_tp2": "ATR_TRAIL",
        "trail_method_tp3": "STRUCTURE_TRAIL",
        "trail_method_tp4": "ATR_TRAIL",
        "trail_method_tp5": "STRUCTURE_TRAIL",
        "atr_trail_multiplier": 1.5,
        "trail_pips": 15.0,
        "compounding_enabled": False
    }
    print("Sending request...")
    response = client.post("/api/backtest", json=payload)
    print("Status code:", response.status_code)
    try:
        data = response.json()
        print("Total trades:", len(data.get("trades", [])))
        print("Final balance:", data.get("final_balance"))
    except Exception as e:
        print("Error parsing JSON:", e)
        print("Response text:", response.text)

if __name__ == "__main__":
    test_backtest()
