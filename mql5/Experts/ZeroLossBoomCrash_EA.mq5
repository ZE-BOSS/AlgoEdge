//+------------------------------------------------------------------+
//| Zero Loss Boom & Crash  v3.2  -  Expert Advisor for MetaTrader 5   |
//| Same rules and default settings as the TradingView strategy v3.2.  |
//|                                                                    |
//| SELL: black line touched 80 within the last 5 candles, has been    |
//|       falling for 3 candles, and the candle opened above the red   |
//|       line (EMA 30) and closed below it.  BUY: the mirror at 20.   |
//| Boom: SELL = video trade, BUY = spike side.                        |
//| Crash: BUY = video trade, SELL = spike side. Each can be switched. |
//| Stop loss: 8 x ATR(14).  Take profit: close when the black line    |
//| reaches the opposite zone (20 for a sell, 80 for a buy) or after   |
//| 100 candles.  Lot = equity x risk % / (stop distance + spread).    |
//|                                                                    |
//| Black line = EMA 5 of close scaled 0-100 to the last 100 candles'  |
//| high/low. Signals are taken on the candle close; the trade opens   |
//| at the next candle's open. Attach to an M5 chart of each index.    |
//|                                                                    |
//| AutoTrade = false  -> signals only: arrows, chart text, pop-up     |
//|                       alerts and phone push, no orders.            |
//+------------------------------------------------------------------+
#property copyright "Zero Loss Boom & Crash v3.2"
#property version   "3.20"
#property description "Zero Loss Boom & Crash v3.2: signals, alerts, lot size from risk %, optional auto-trading."

#include <Trade/Trade.mqh>

enum ENUM_ZL_CROSS  { ZL_CROSS_BODY = 0,  // Candle body crosses the red line
                      ZL_CROSS_CLOSE = 1  // Close crosses the red line
                    };
enum ENUM_ZL_EXIT   { ZL_EXIT_ZONE = 0,   // Hold until black line reaches opposite zone (max N candles)
                      ZL_EXIT_FIXED = 1,  // Fixed: hold N candles
                      ZL_EXIT_RED = 2     // Exit when price closes back across the red line (max N candles)
                    };
enum ENUM_ZL_SIZE   { ZL_SIZE_PCT = 0,    // % of equity
                      ZL_SIZE_USD = 1,    // Fixed $ per trade
                      ZL_SIZE_LOTS = 2    // Fixed lots
                    };
enum ENUM_ZL_SL     { ZL_SL_NONE = 0,     // None (time / zone exit only)
                      ZL_SL_ATR = 1,      // ATR multiple
                      ZL_SL_POINTS = 2    // Fixed price distance
                    };
enum ENUM_ZL_MIN    { ZL_MIN_TRADE = 0,   // Use the broker minimum lot (real risk shown)
                      ZL_MIN_SKIP = 1     // Skip the trade
                    };

input group "Indicators (as in the video)"
input int      InpRedLen      = 30;     // Red MA period (EMA of close)
input int      InpBlackLen    = 5;      // Black MA period (EMA of close)
input double   InpLvlSell     = 80;     // Sell zone level
input double   InpLvlBuy      = 20;     // Buy zone level
input int      InpNBars       = 100;    // Black line scaled to last N candles

input group "Directions (turn each on / off)"
input bool     InpBoomBuy     = true;   // Boom BUY  (spike side)
input bool     InpBoomSell    = true;   // Boom SELL (video trade)
input bool     InpCrashBuy    = true;   // Crash BUY (video trade)
input bool     InpCrashSell   = true;   // Crash SELL (spike side)

input group "Entry and exit"
input int            InpZoneMem  = 5;          // Black line touched the zone within last N candles
input int            InpTurnN    = 3;          // Black line moving back out of the zone for N candles (0 = off)
input ENUM_ZL_CROSS  InpCross    = ZL_CROSS_BODY; // Price-action trigger
input ENUM_ZL_EXIT   InpExit     = ZL_EXIT_ZONE;  // Exit
input int            InpHoldBars = 100;        // Hold N candles (maximum for zone / red-line exits)
input int            InpMinHold  = 1;          // Red-line exit: earliest after N candles

input group "Risk and lot size"
input ENUM_ZL_SIZE   InpSizeMode  = ZL_SIZE_PCT;  // Position size
input double         InpRiskPct   = 1.0;       // Risk % of equity per trade
input double         InpRiskUsd   = 10.0;      // Risk $ per trade (if Fixed $)
input double         InpFixedLots = 0.2;       // Fixed lots (if Fixed lots)
input ENUM_ZL_SL     InpSlMode    = ZL_SL_ATR;    // Stop loss
input int            InpAtrLen    = 14;        // ATR length (Wilder, same as TradingView)
input double         InpSlAtr     = 8.0;       // Stop = ATR x
input double         InpSlPrice   = 20.0;      // Stop distance in price (if Fixed price distance)
input double         InpTpR       = 0.0;       // Fixed take profit at R multiple (0 = off, close on opposite zone)
input ENUM_ZL_MIN    InpBelowMin  = ZL_MIN_TRADE; // If the risk-based lot is below the broker minimum
input double         InpMaxLot    = 50.0;      // Maximum lot

input group "Execution, alerts and display"
input bool     InpAutoTrade   = true;    // AutoTrade (false = signals and alerts only)
input ulong    InpMagic       = 320032;  // Magic number
input int      InpSlippage    = 100;     // Max slippage (points)
input bool     InpPopup       = true;    // Pop-up alert in the terminal
input bool     InpPush        = true;    // Push notification to MT5 on your phone
input bool     InpDraw        = true;    // Draw signals on the chart
input int      InpHistoryBars = 3000;    // Candles of history to show past signals on
input bool     InpAddRedLine  = true;    // Add the red EMA 30 to the chart

//--- globals
CTrade    trade;
string    gPfx = "ZLBC_";
datetime  gLastBar = 0;
bool      gIsBoom = false, gIsCrash = false, gOther = false;

MqlRates  R[];
double    gRed[], gBlk[], gBlack[], gAtr[];
int       gN = 0;

// trade tracker (one trade at a time) - mirrors the TradingView strategy
int       vDir = 0;          // 1 buy, -1 sell, 0 flat
datetime  vSigTime = 0;      // signal candle time
double    vEntry = 0, vStop = 0, vTp = 0, vLot = 0, vRisk = 0;
string    vTag = "";
ulong     vTicket = 0;

// stats of signals shown on this chart
int       sTr = 0, sWin = 0, sLs = 0, sMaxLs = 0, sSkipped = 0;
double    sPnl = 0;
string    gLastEvent = "No signal yet";

//+------------------------------------------------------------------+
string Up(string s) { string u = s; StringToUpper(u); return u; }
string Px(double p) { return DoubleToString(p, _Digits); }
string Usd(double v) { return (v < 0 ? "-$" : "$") + DoubleToString(MathAbs(v), 2); }
string LotStr(double l) { return DoubleToString(l, 2); }
string Short()
{
   string s = _Symbol;
   StringReplace(s, " Index", "");
   return s;
}

double ValuePerPrice()          // account currency per 1.0 price move per 1 lot
{
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tv <= 0) tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(ts <= 0) ts = _Point;
   return tv / ts;
}

double Spread()
{
   double a = SymbolInfoDouble(_Symbol, SYMBOL_ASK), b = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(a > 0 && b > 0 && a >= b) return a - b;
   return SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) * _Point;
}

double NormLot(double lot)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;
   int digits = (int)MathMax(0, MathCeil(-MathLog10(step) - 1e-9));
   return NormalizeDouble(MathFloor(lot / step + 1e-9) * step, digits);
}

// Lot from the risk setting. Returns 0 to skip. riskMoney = what a full stop-out costs (incl. spread).
double CalcLot(double riskDist, double spreadPx, double &riskMoney, bool &raised)
{
   raised = false;
   riskMoney = 0;
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = MathMin(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), InpMaxLot);
   double perLot = (riskDist + spreadPx) * ValuePerPrice();
   double lot = 0;
   if(InpSizeMode == ZL_SIZE_LOTS)
   {
      lot = NormLot(InpFixedLots);
      if(lot < vmin) return 0;
   }
   else
   {
      if(perLot <= 0) return 0;
      double cash = (InpSizeMode == ZL_SIZE_PCT) ? AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPct / 100.0 : InpRiskUsd;
      lot = NormLot(MathMin(cash / perLot, vmax));
      if(lot < vmin)
      {
         if(InpBelowMin == ZL_MIN_SKIP) return 0;
         lot = vmin;
         raised = true;
      }
   }
   riskMoney = lot * perLot;
   return lot;
}

void Notify(string msg)
{
   Print(msg);
   if(InpPopup) Alert(msg);
   if(InpPush && TerminalInfoInteger(TERMINAL_NOTIFICATIONS_ENABLED))
      SendNotification(StringSubstr(msg, 0, 255));
}

//--- drawing
void TextAt(string name, datetime t, double price, string txt, color clr, ENUM_ANCHOR_POINT anchor)
{
   if(ObjectFind(0, name) >= 0) ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_TEXT, 0, t, price);
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
}

void DrawEntry(datetime t, double hi, double lo, int dir, string line1, string line2)
{
   if(!InpDraw) return;
   string n = gPfx + "E" + IntegerToString((long)t);
   if(ObjectFind(0, n) >= 0) ObjectDelete(0, n);
   double y = dir < 0 ? hi : lo;
   ObjectCreate(0, n, dir < 0 ? OBJ_ARROW_SELL : OBJ_ARROW_BUY, 0, t, y);
   ObjectSetInteger(0, n, OBJPROP_COLOR, dir < 0 ? clrRed : clrDodgerBlue);
   ObjectSetInteger(0, n, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
   TextAt(n + "a", t, y, line1, dir < 0 ? clrRed : clrDodgerBlue, dir < 0 ? ANCHOR_LOWER : ANCHOR_UPPER);
   TextAt(n + "b", t, dir < 0 ? y + (hi - lo + _Point) * 1.6 : y - (hi - lo + _Point) * 1.6, line2, clrGray, dir < 0 ? ANCHOR_LOWER : ANCHOR_UPPER);
}

void DrawStop(datetime t0, datetime t1, double price)
{
   if(!InpDraw || price <= 0) return;
   string n = gPfx + "S" + IntegerToString((long)t0);
   if(ObjectFind(0, n) < 0)
   {
      ObjectCreate(0, n, OBJ_TREND, 0, t0, price, t1, price);
      ObjectSetInteger(0, n, OBJPROP_COLOR, clrRed);
      ObjectSetInteger(0, n, OBJPROP_STYLE, STYLE_DOT);
      ObjectSetInteger(0, n, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
   }
   else
      ObjectMove(0, n, 1, t1, price);
}

void DrawExit(datetime t, double hi, double lo, int dir, string txt, bool win)
{
   if(!InpDraw) return;
   string n = gPfx + "X" + IntegerToString((long)t);
   TextAt(n, t, dir > 0 ? hi : lo, txt, win ? clrLimeGreen : clrGray, dir > 0 ? ANCHOR_LOWER : ANCHOR_UPPER);
}

//--- data and indicator values (index 0 = oldest, gN-1 = last closed candle)
bool LoadData(int count)
{
   ArraySetAsSeries(R, false);
   int got = CopyRates(_Symbol, _Period, 1, count, R);
   if(got < InpNBars + InpZoneMem + InpTurnN + 60) return false;
   gN = got;
   ArrayResize(gRed, gN); ArrayResize(gBlk, gN); ArrayResize(gBlack, gN); ArrayResize(gAtr, gN);
   // EMAs seeded with an SMA (same as TradingView ta.ema)
   double aR = 2.0 / (InpRedLen + 1), aB = 2.0 / (InpBlackLen + 1), sR = 0, sB = 0;
   for(int i = 0; i < gN; i++)
   {
      double c = R[i].close;
      if(i < InpRedLen - 1) { sR += c; gRed[i] = EMPTY_VALUE; }
      else if(i == InpRedLen - 1) { sR += c; gRed[i] = sR / InpRedLen; }
      else gRed[i] = aR * c + (1 - aR) * gRed[i - 1];
      if(i < InpBlackLen - 1) { sB += c; gBlk[i] = EMPTY_VALUE; }
      else if(i == InpBlackLen - 1) { sB += c; gBlk[i] = sB / InpBlackLen; }
      else gBlk[i] = aB * c + (1 - aB) * gBlk[i - 1];
   }
   // ATR with Wilder smoothing (same as TradingView ta.atr)
   double sT = 0;
   for(int i = 0; i < gN; i++)
   {
      double tr = (i == 0) ? R[i].high - R[i].low
                  : MathMax(R[i].high - R[i].low, MathMax(MathAbs(R[i].high - R[i - 1].close), MathAbs(R[i].low - R[i - 1].close)));
      if(i < InpAtrLen - 1) { sT += tr; gAtr[i] = EMPTY_VALUE; }
      else if(i == InpAtrLen - 1) { sT += tr; gAtr[i] = sT / InpAtrLen; }
      else gAtr[i] = (gAtr[i - 1] * (InpAtrLen - 1) + tr) / InpAtrLen;
   }
   // black line
   for(int i = 0; i < gN; i++)
   {
      gBlack[i] = EMPTY_VALUE;
      if(i < InpNBars - 1 || gBlk[i] == EMPTY_VALUE) continue;
      double hh = -DBL_MAX, ll = DBL_MAX;
      for(int j = i - InpNBars + 1; j <= i; j++) { if(R[j].high > hh) hh = R[j].high; if(R[j].low < ll) ll = R[j].low; }
      if(hh > ll) gBlack[i] = 100.0 * (gBlk[i] - ll) / (hh - ll);
   }
   return true;
}

int FindIdx(datetime t)
{
   for(int i = gN - 1; i >= 0; i--)
   {
      if(R[i].time == t) return i;
      if(R[i].time < t) return -1;
   }
   return -1;
}

// signal on candle i: returns -1 sell, 1 buy, 0 none; tag = video / spike side
int Signal(int i, string &tag)
{
   tag = "";
   int need = (int)MathMax(InpZoneMem, InpTurnN + 1);
   if(i < InpNBars + need || gRed[i] == EMPTY_VALUE || gBlack[i] == EMPTY_VALUE) return 0;
   bool inS = false, inB = false;
   for(int j = i - InpZoneMem + 1; j <= i; j++)
   {
      if(gBlack[j] == EMPTY_VALUE) continue;
      if(gBlack[j] >= InpLvlSell) inS = true;
      if(gBlack[j] <= InpLvlBuy)  inB = true;
   }
   bool crossDn, crossUp;
   if(InpCross == ZL_CROSS_BODY)
   {
      crossDn = R[i].open > gRed[i] && R[i].close < gRed[i];
      crossUp = R[i].open < gRed[i] && R[i].close > gRed[i];
   }
   else
   {
      crossDn = R[i - 1].close >= gRed[i - 1] && R[i].close < gRed[i];
      crossUp = R[i - 1].close <= gRed[i - 1] && R[i].close > gRed[i];
   }
   bool turnS = true, turnB = true;
   for(int k = 0; k < InpTurnN; k++)
   {
      double a = gBlack[i - k], b = gBlack[i - k - 1];
      if(a == EMPTY_VALUE || b == EMPTY_VALUE) { turnS = false; turnB = false; break; }
      if(!(a < b)) turnS = false;
      if(!(a > b)) turnB = false;
   }
   bool videoSell = inS && crossDn && ((gIsBoom && InpBoomSell) || (gOther && (InpBoomSell || InpCrashSell)));
   bool videoBuy  = inB && crossUp && ((gIsCrash && InpCrashBuy) || (gOther && (InpBoomBuy || InpCrashBuy)));
   bool spikeSell = inS && crossDn && gIsCrash && InpCrashSell;
   bool spikeBuy  = inB && crossUp && gIsBoom && InpBoomBuy;
   if((videoSell || spikeSell) && turnS) { tag = videoSell ? "video trade" : "spike side"; return -1; }
   if((videoBuy || spikeBuy) && turnB)   { tag = videoBuy ? "video trade" : "spike side"; return 1; }
   return 0;
}

double RiskDist(int i)
{
   if(InpSlMode == ZL_SL_POINTS) return InpSlPrice;
   return gAtr[i] * InpSlAtr;          // also used to size the lot when the stop is off
}

//--- broker position for this EA on this symbol
bool GetPos(ulong &ticket, int &dir, datetime &openTime, double &sl, double &vol, double &openPx)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      ticket   = tk;
      dir      = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      openTime = (datetime)PositionGetInteger(POSITION_TIME);
      sl       = PositionGetDouble(POSITION_SL);
      vol      = PositionGetDouble(POSITION_VOLUME);
      openPx   = PositionGetDouble(POSITION_PRICE_OPEN);
      return true;
   }
   return false;
}

// last closing deal of our position (price, profit incl. swap/commission, reason)
bool LastDeal(double &px, double &profit, string &reason)
{
   if(!HistorySelect(vSigTime, TimeCurrent() + 60)) return false;
   for(int k = HistoryDealsTotal() - 1; k >= 0; k--)
   {
      ulong d = HistoryDealGetTicket(k);
      if(d == 0) continue;
      if(HistoryDealGetString(d, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
      if((ulong)HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic && (ulong)HistoryDealGetInteger(d, DEAL_POSITION_ID) != vTicket) continue;
      px     = HistoryDealGetDouble(d, DEAL_PRICE);
      profit = HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP) + HistoryDealGetDouble(d, DEAL_COMMISSION);
      long r = HistoryDealGetInteger(d, DEAL_REASON);
      reason = (r == DEAL_REASON_SL) ? "STOP LOSS HIT" : (r == DEAL_REASON_TP) ? "TAKE PROFIT HIT" : "POSITION CLOSED";
      return true;
   }
   return false;
}

void RecordClose(int i, double px, string why, bool live, double realPnl = EMPTY_VALUE)
{
   double pnl = (realPnl != EMPTY_VALUE) ? realPnl
              : vDir * (px - vEntry) * vLot * ValuePerPrice() - vLot * Spread() * ValuePerPrice();
   sTr++; if(pnl > 0) sWin++;
   sPnl += pnl;
   sLs = (pnl < 0) ? sLs + 1 : 0;
   if(sLs > sMaxLs) sMaxLs = sLs;
   string side = vDir > 0 ? "BUY" : "SELL";
   DrawStop(vSigTime, R[i].time, vStop);
   DrawExit(R[i].time, R[i].high, R[i].low, vDir, why + "  " + Usd(pnl), pnl > 0);
   gLastEvent = why + ": " + side + " " + LotStr(vLot) + " closed ~" + Px(px) + " (" + Usd(pnl) + ")";
   if(live) Notify("ZL " + Short() + ": " + why + " - " + side + " " + LotStr(vLot) + " lot ~" + Px(px) + " P&L ~" + Usd(pnl));
   vDir = 0; vTicket = 0;
}

// one closed candle i, in the same order as the TradingView strategy
void Step(int i, bool live)
{
   bool closedOnClose = false;
   bool isAuto = live && InpAutoTrade;

   if(vDir != 0)
   {
      if(isAuto)
      {
         ulong tk; int d; datetime ot; double sl, vol, op;
         if(!GetPos(tk, d, ot, sl, vol, op))           // closed by the broker (stop loss / take profit) or by hand
         {
            double px = vStop, pr = 0; string why = "STOP LOSS / TP HIT";
            if(LastDeal(px, pr, why)) RecordClose(i, px, why, live, pr);
            else                      RecordClose(i, px, why, live);
         }
      }
      else if(R[i].time > vSigTime)                     // simulated stop / take profit inside the candle
      {
         double px = 0; string why = "";
         if(vDir > 0)
         {
            if(vStop > 0 && R[i].low <= vStop)      { px = MathMin(R[i].open, vStop); why = "STOP LOSS HIT"; }
            else if(vTp > 0 && R[i].high >= vTp)    { px = MathMax(R[i].open, vTp);   why = "TAKE PROFIT HIT"; }
         }
         else
         {
            if(vStop > 0 && R[i].high >= vStop)     { px = MathMax(R[i].open, vStop); why = "STOP LOSS HIT"; }
            else if(vTp > 0 && R[i].low <= vTp)     { px = MathMin(R[i].open, vTp);   why = "TAKE PROFIT HIT"; }
         }
         if(why != "") RecordClose(i, px, why, live);
      }
   }

   if(vDir != 0)
   {
      int si = FindIdx(vSigTime);
      int held = (si >= 0) ? i - si : InpHoldBars;
      bool zoneX = (gBlack[i] != EMPTY_VALUE) && (vDir > 0 ? gBlack[i] >= InpLvlSell : gBlack[i] <= InpLvlBuy);
      bool backX = vDir > 0 ? R[i].close < gRed[i] : R[i].close > gRed[i];
      bool doExit = (InpExit == ZL_EXIT_FIXED) ? held >= InpHoldBars
                  : (InpExit == ZL_EXIT_ZONE)  ? (held >= InpHoldBars || zoneX)
                  : (held >= InpHoldBars || (held >= InpMinHold && backX));
      if(doExit)
      {
         string why = (InpExit == ZL_EXIT_ZONE && zoneX) ? "TAKE PROFIT - black line reached " + DoubleToString(vDir > 0 ? InpLvlSell : InpLvlBuy, 0)
                    : (InpExit == ZL_EXIT_RED && held >= InpMinHold && backX) ? "CLOSE - back across red line"
                    : "CLOSE - " + IntegerToString(InpHoldBars) + "-candle limit";
         double px = R[i].close;
         if(isAuto)
         {
            ulong tk; int d; datetime ot; double sl, vol, op;
            if(GetPos(tk, d, ot, sl, vol, op))
            {
               if(!trade.PositionClose(tk, InpSlippage))
               {
                  Notify("ZL " + Short() + ": could not close position " + IntegerToString((long)tk) + " - " + trade.ResultRetcodeDescription() + " - close it by hand");
                  return;
               }
               double dpx = px, pr = 0; string dwhy;
               if(LastDeal(dpx, pr, dwhy)) { RecordClose(i, dpx, why, live, pr); closedOnClose = true; return; }
            }
         }
         RecordClose(i, px, why, live);
         closedOnClose = true;
      }
      else DrawStop(vSigTime, R[i].time, vStop);
   }

   if(vDir == 0 && !closedOnClose)
   {
      string tag;
      int dir = Signal(i, tag);
      if(dir != 0)
      {
         double rd = RiskDist(i);
         double spr = Spread();
         double riskMoney; bool raised;
         double lot = CalcLot(rd, spr, riskMoney, raised);
         if(lot <= 0)
         {
            sSkipped++;
            if(live) Notify("ZL " + Short() + ": " + (dir < 0 ? "SELL" : "BUY") + " signal skipped - lot below broker minimum");
            return;
         }
         double refPx = R[i].close;                       // bid at the signal candle's close
         if(live)
         {
            double b = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            if(b > 0) refPx = b;
         }
         double stopBid = (InpSlMode != ZL_SL_NONE) ? refPx - dir * rd : 0;
         double tpBid   = (InpTpR > 0) ? refPx + dir * InpTpR * rd : 0;
         string side = dir < 0 ? "SELL" : "BUY";
         double eq = AccountInfoDouble(ACCOUNT_EQUITY);
         string riskTxt = Usd(riskMoney) + " (" + DoubleToString(eq > 0 ? 100.0 * riskMoney / eq : 0, 1) + "%" + (raised ? ", min lot" : "") + ")";
         string tpTxt = (tpBid > 0) ? Px(tpBid) : "close at black " + DoubleToString(dir < 0 ? InpLvlBuy : InpLvlSell, 0);
         string slTxt = (stopBid > 0) ? Px(stopBid) : "none";

         if(isAuto)
         {
            // SL/TP are hit on the bid for a buy and on the ask for a sell: shift sell levels by the spread
            double sl = 0, tp = 0;
            if(stopBid > 0) sl = (dir > 0) ? stopBid : stopBid + spr;
            if(tpBid > 0)   tp = (dir > 0) ? tpBid : tpBid + spr;
            double lvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
            double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK), bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            if(dir > 0) { if(sl > 0 && sl > bid - lvl) sl = bid - lvl; if(tp > 0 && tp < bid + lvl) tp = bid + lvl; }
            else        { if(sl > 0 && sl < ask + lvl) sl = ask + lvl; if(tp > 0 && tp > ask - lvl) tp = ask - lvl; }
            sl = (sl > 0) ? NormalizeDouble(sl, _Digits) : 0;
            tp = (tp > 0) ? NormalizeDouble(tp, _Digits) : 0;
            double marginNeed = 0;
            if(OrderCalcMargin(dir > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, lot, dir > 0 ? ask : bid, marginNeed)
               && marginNeed > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
            {
               Notify("ZL " + Short() + ": " + side + " signal skipped - not enough free margin for " + LotStr(lot) + " lot");
               return;
            }
            bool ok = (dir > 0) ? trade.Buy(lot, _Symbol, 0, sl, tp, "ZL " + tag)
                                : trade.Sell(lot, _Symbol, 0, sl, tp, "ZL " + tag);
            if(!ok || (trade.ResultRetcode() != TRADE_RETCODE_DONE && trade.ResultRetcode() != TRADE_RETCODE_PLACED))
            {
               Notify("ZL " + Short() + ": " + side + " order FAILED - " + trade.ResultRetcodeDescription());
               return;
            }
            vTicket = trade.ResultOrder();
            if(trade.ResultPrice() > 0) refPx = (dir > 0) ? trade.ResultPrice() - spr : trade.ResultPrice();
         }

         vDir = dir; vSigTime = R[i].time; vEntry = refPx; vStop = stopBid; vTp = tpBid; vLot = lot; vRisk = riskMoney; vTag = tag;
         DrawEntry(R[i].time, R[i].high, R[i].low, dir, side + " " + LotStr(lot) + " lot  SL " + slTxt, "TP: " + tpTxt + "  risk " + riskTxt);
         gLastEvent = side + " " + LotStr(lot) + " @" + Px(refPx) + "  SL " + slTxt + "  TP " + tpTxt + "  risk " + riskTxt;
         if(live)
            Notify("ZL " + Short() + " " + side + (isAuto ? " opened " : " NOW ") + LotStr(lot) + " lot @" + Px(refPx) + " SL " + slTxt + " TP " + tpTxt + " risk " + riskTxt);
      }
   }
}

void ShowStatus()
{
   string mode = InpAutoTrade ? "AUTO-TRADING" : "SIGNALS ONLY (no orders)";
   if(InpAutoTrade && !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) mode += "  - turn on Algo Trading!";
   string pos = "No open trade";
   if(vDir != 0)
   {
      int si = FindIdx(vSigTime);
      int held = (si >= 0) ? (gN - 1) - si : InpHoldBars;
      pos = StringFormat("%s %s lot @%s | SL %s | TP: %s | candle %d/%d",
                         vDir > 0 ? "BUY" : "SELL", LotStr(vLot), Px(vEntry), vStop > 0 ? Px(vStop) : "none",
                         vTp > 0 ? Px(vTp) : "black line at " + DoubleToString(vDir > 0 ? InpLvlSell : InpLvlBuy, 0),
                         held, InpHoldBars);
   }
   double blk = (gN > 0) ? gBlack[gN - 1] : EMPTY_VALUE;
   string blkTxt = (blk == EMPTY_VALUE) ? "-" : DoubleToString(blk, 1) + (blk >= InpLvlSell ? " (sell zone)" : blk <= InpLvlBuy ? " (buy zone)" : "");
   double riskMoney = 0; bool raised = false;
   double nextLot = (gN > 0) ? CalcLot(RiskDist(gN - 1), Spread(), riskMoney, raised) : 0;
   string nxt = nextLot > 0 ? LotStr(nextLot) + " lot, risk " + Usd(riskMoney) + (raised ? " (broker minimum)" : "") : "skip (below minimum)";
   Comment(StringFormat("Zero Loss Boom & Crash v3.2 EA  |  %s %s  |  %s\n"
                        "Black line: %s   Red EMA%d: %s\n"
                        "Position: %s\n"
                        "Next trade size: %s\n"
                        "Last event: %s\n"
                        "Signals on this chart: %d closed, %.1f%% won, longest losing run %d, P&L %s at these lots%s",
                        _Symbol, EnumToString(_Period), mode,
                        blkTxt, InpRedLen, (gN > 0 ? Px(gRed[gN - 1]) : "-"),
                        pos, nxt, gLastEvent,
                        sTr, sTr > 0 ? 100.0 * sWin / sTr : 0.0, sMaxLs, Usd(sPnl),
                        sSkipped > 0 ? ", skipped " + IntegerToString(sSkipped) : ""));
}

//+------------------------------------------------------------------+
int OnInit()
{
   string s = Up(_Symbol);
   gIsBoom  = StringFind(s, "BOOM") >= 0;
   gIsCrash = StringFind(s, "CRASH") >= 0;
   gOther   = !gIsBoom && !gIsCrash;

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFillingBySymbol(_Symbol);

   if(InpAddRedLine)
   {
      int h = iMA(_Symbol, _Period, InpRedLen, 0, MODE_EMA, PRICE_CLOSE);
      if(h != INVALID_HANDLE) ChartIndicatorAdd(0, 0, h);
   }

   // replay history so past signals show on the chart (no orders, no alerts)
   ObjectsDeleteAll(0, gPfx);
   vDir = 0; sTr = 0; sWin = 0; sLs = 0; sMaxLs = 0; sSkipped = 0; sPnl = 0;
   if(LoadData(MathMax(InpHistoryBars, InpNBars + 300)))
   {
      for(int i = InpNBars + 5; i < gN; i++) Step(i, false);
   }
   gLastEvent = (sTr > 0) ? "History replayed - waiting for the next signal" : "Waiting for the next signal";

   // live state: in auto-trade mode the broker position is the truth
   if(InpAutoTrade)
   {
      vDir = 0;
      ulong tk; int d; datetime ot; double sl, vol, op;
      if(GetPos(tk, d, ot, sl, vol, op))
      {
         int sh = iBarShift(_Symbol, _Period, ot, false);
         vDir = d; vTicket = tk; vSigTime = iTime(_Symbol, _Period, sh + 1); vEntry = op; vLot = vol;
         vStop = (sl > 0) ? (d > 0 ? sl : sl - Spread()) : 0; vTp = 0;
         gLastEvent = "Managing open " + (d > 0 ? "BUY " : "SELL ") + LotStr(vol) + " lot";
      }
   }
   gLastBar = iTime(_Symbol, _Period, 0);   // act from the next closed candle
   ShowStatus();
   if(_Period != PERIOD_M5) Print("ZL B&C: tested on M5 charts - you are on ", EnumToString(_Period));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   Comment("");
   if(reason == REASON_REMOVE) ObjectsDeleteAll(0, gPfx);
}

void OnTick()
{
   datetime t0 = iTime(_Symbol, _Period, 0);
   if(t0 == 0 || t0 == gLastBar) return;
   gLastBar = t0;
   if(!LoadData(MathMax(1000, InpNBars + 300))) return;
   Step(gN - 1, true);
   ShowStatus();
}
//+------------------------------------------------------------------+
