//+------------------------------------------------------------------+
//| EQH/EQL FVG Breakouts  v1.0  -  Expert Advisor for MetaTrader 5  |
//| Same rules as the TradingView strategy "EQH/EQL FVG Breakouts    |
//| (video test)" (LuxAlgo TikTok video).                            |
//|                                                                  |
//| ZONES: two pivot highs (or lows) within 0.03% of each other and  |
//|   no more than 30 candles apart = equal highs (EQH, red) / equal |
//|   lows (EQL, teal). Pivot = 10 candles left, 2 candles right.    |
//|   Unswept zones are removed after 30 candles.                    |
//|   NY version: a zone is only kept if the later pivot candle is   |
//|   inside 09:30-16:00 New York time, Monday-Friday.               |
//| SIGNAL: a candle sweeps the zone and closes beyond it with a     |
//|   body >= 0.5 x ATR(14), and the NEXT candle leaves a fair value |
//|   gap >= 0.1 x ATR (bull: its low > high two candles back).      |
//|   "No gap, no signal."                                           |
//| TRADE: market order at the open of the candle after the gap      |
//|   candle (= TradingView's "close of the confirm candle").        |
//|   EQH break = BUY, EQL break = SELL. One position at a time.     |
//|   Stop 1 x ATR from entry, target 1R, lot from risk % (defaults  |
//|   = the settings that tested best on Deriv US Tech 100 M1).      |
//|                                                                  |
//| Attach to a "US Tech 100" chart on Deriv MT5. The EA reads the   |
//| timeframe from InpTF (default M1), not from the chart.           |
//| AutoTrade = false -> zones, signals and alerts only, no orders.  |
//|                                                                  |
//| WARNING: the tested edge rests on 26 trades over 2 weeks and     |
//| failed on M2/M3. Run it on a DEMO account for 4-6 weeks first.   |
//+------------------------------------------------------------------+
#property copyright "EQH/EQL FVG Breakouts v1.0"
#property version   "1.00"
#property description "EQH/EQL liquidity zones + FVG breakout signals (LuxAlgo video rules), NY-session version, risk-% lots, optional auto-trading."

#include <Trade/Trade.mqh>

enum ENUM_EF_STOP { EF_STOP_FVG = 0,     // Far edge of the FVG
                    EF_STOP_CANDLE = 1,  // Breakout candle low / high
                    EF_STOP_ZONE = 2,    // Zone level
                    EF_STOP_ATR = 3      // ATR multiple from entry
                  };
enum ENUM_EF_DIR  { EF_DIR_BOTH = 0,     // Buys and sells
                    EF_DIR_LONG = 1,     // Buys only
                    EF_DIR_SHORT = 2     // Sells only
                  };
enum ENUM_EF_MIN  { EF_MIN_SKIP = 0,     // Skip the trade
                    EF_MIN_TRADE = 1     // Use the broker minimum lot (real risk shown)
                  };

input group "General"
input bool            InpAutoTrade   = true;      // AutoTrade (false = signals only)
input ENUM_TIMEFRAMES InpTF          = PERIOD_M1; // Timeframe the rules run on

input group "Liquidity detection (LuxAlgo defaults)"
input int      InpPivotLeft   = 10;     // Pivot left length
input int      InpPivotRight  = 2;      // Pivot right length
input double   InpEqThrPct    = 0.03;   // Equality threshold (%)
input int      InpMaxZones    = 60;     // Max active zones
input int      InpMaxDist     = 30;     // Max EQH/EQL distance (candles)
input int      InpMaxAge      = 30;     // Max EQH/EQL age (candles)

input group "Breakout signals (LuxAlgo defaults)"
input int      InpAtrLen      = 14;     // ATR length (Wilder, same as TradingView)
input double   InpMinBodyAtr  = 0.5;    // Min breakout body (x ATR, 0 = off)
input double   InpMinFvgAtr   = 0.1;    // Min FVG size (x ATR, 0 = any gap)
input int      InpFvgProj     = 5;      // FVG box projection (candles)

input group "New York session version"
input bool     InpNYZonesOnly = true;   // Only keep zones formed in the NY session
input int      InpSessStart   = 930;    // Session start (NY time, HHMM)
input int      InpSessEnd     = 1600;   // Session end (NY time, HHMM)
input bool     InpTradeInSess = false;  // Only trade signals inside the session
input int      InpServerGMT   = 0;      // Broker server time = GMT + this many hours (Deriv = 0)

input group "Trade rules"
input ENUM_EF_DIR  InpDirection = EF_DIR_BOTH;  // Direction
input ENUM_EF_STOP InpStopMode  = EF_STOP_ATR;  // Stop placement
input double   InpAtrStop     = 1.0;    // ATR stop multiple (for ATR mode)
input double   InpStopBufAtr  = 0.0;    // Extra stop buffer (x ATR)
input double   InpTargetR     = 1.0;    // Target (R multiple)
input int      InpMaxHold     = 0;      // Close after N candles (0 = off)
input int      InpMaxPerDay   = 0;      // Max trades per day (0 = no limit)
input double   InpMaxSpread   = 3.0;    // Skip if spread > this many price points (0 = off)

input group "Risk"
input double   InpRiskPct     = 1.0;    // Risk per trade (% of equity)
input ENUM_EF_MIN InpMinLot   = EF_MIN_SKIP; // If risk lot < broker minimum
input double   InpMaxLots     = 10.0;   // Max lots per trade
input ulong    InpMagic       = 26110601; // Magic number
input string   InpComment     = "EQH-EQL-FVG"; // Order comment

input group "Chart & alerts"
input bool     InpDraw        = true;   // Draw zones, FVGs and Break signals
input int      InpReplayBars  = 1500;   // Candles replayed at start (draws recent zones)
input color    InpBullCol     = C'8,153,129';   // Bullish colour (EQL / bull FVG)
input color    InpBearCol     = C'242,54,69';   // Bearish colour (EQH / bear FVG)
input bool     InpAlerts      = true;   // Pop-up alert on signal
input bool     InpPush        = false;  // Phone push notification on signal

//--- types
struct Piv
{
   double   price;
   int      idx;
   long     vol;
   datetime t;
};

struct Zone
{
   bool     isHigh;
   double   top;
   double   btm;
   int      idx2;      // index of the later pivot
   long     vol;
   bool     swept;
   int      sweptIdx;
   bool     alive;
   string   name;
};

//--- state
CTrade   trade;
Piv      g_highs[];
Piv      g_lows[];
Zone     g_zones[];
int      g_idx        = 0;     // running candle counter (like Pine bar_index)
double   g_atr        = 0.0;
double   g_atrPrev    = 0.0;
int      g_atrCount   = 0;
double   g_trSum      = 0.0;
datetime g_lastDone   = 0;     // open time of last processed closed candle
bool     g_ready      = false;
long     g_zoneId     = 0;
long     g_sigId      = 0;
int      g_dayKey     = 0;
int      g_tradesDay  = 0;
string   PFX          = "EQHFVG_";

//+------------------------------------------------------------------+
//| Helpers: prices                                                  |
//+------------------------------------------------------------------+
double Hi(int s) { return iHigh(_Symbol, InpTF, s); }
double Lo(int s) { return iLow(_Symbol, InpTF, s); }
double Op(int s) { return iOpen(_Symbol, InpTF, s); }
double Cl(int s) { return iClose(_Symbol, InpTF, s); }
datetime Tm(int s) { return iTime(_Symbol, InpTF, s); }
long   Vo(int s) { return iTickVolume(_Symbol, InpTF, s); }

double NormPrice(double p)
{
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(ts <= 0) ts = _Point;
   return NormalizeDouble(MathRound(p / ts) * ts, _Digits);
}

string FmtVol(double v)
{
   string s;
   if(v >= 1000000.0)      s = DoubleToString(v / 1000000.0, 3);
   else if(v >= 1000.0)    s = DoubleToString(v / 1000.0, 3);
   else                    return DoubleToString(v, 0);
   // trim trailing zeros like TradingView's format.volume
   while(StringLen(s) > 0 && StringGetCharacter(s, StringLen(s) - 1) == '0')
      s = StringSubstr(s, 0, StringLen(s) - 1);
   if(StringLen(s) > 0 && StringGetCharacter(s, StringLen(s) - 1) == '.')
      s = StringSubstr(s, 0, StringLen(s) - 1);
   return s + (v >= 1000000.0 ? "M" : "K");
}

//+------------------------------------------------------------------+
//| New York time                                                    |
//+------------------------------------------------------------------+
datetime NthSunday(int year, int month, int n)
{
   MqlDateTime d;
   ZeroMemory(d);
   d.year = year; d.mon = month; d.day = 1;
   datetime first = StructToTime(d);
   MqlDateTime f;
   TimeToStruct(first, f);
   int firstSunday = 1 + (7 - f.day_of_week) % 7;
   d.day = firstSunday + 7 * (n - 1);
   return StructToTime(d);
}

bool IsUSDst(datetime utc)
{
   MqlDateTime d;
   TimeToStruct(utc, d);
   datetime start = NthSunday(d.year, 3, 2) + 7 * 3600;   // 2nd Sun March, 02:00 EST
   datetime end   = NthSunday(d.year, 11, 1) + 6 * 3600;  // 1st Sun Nov,   02:00 EDT
   return (utc >= start && utc < end);
}

bool InNYSession(datetime serverTime)
{
   datetime utc = serverTime - InpServerGMT * 3600;
   datetime ny  = utc + (IsUSDst(utc) ? -4 : -5) * 3600;
   MqlDateTime d;
   TimeToStruct(ny, d);
   if(d.day_of_week < 1 || d.day_of_week > 5) return false;
   int hm = d.hour * 100 + d.min;
   return (hm >= InpSessStart && hm < InpSessEnd);
}

//+------------------------------------------------------------------+
//| Drawing                                                          |
//+------------------------------------------------------------------+
void MkRect(string n, datetime t1, double p1, datetime t2, double p2, color c)
{
   if(!InpDraw) return;
   if(ObjectFind(0, n) < 0) ObjectCreate(0, n, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
   ObjectSetInteger(0, n, OBJPROP_COLOR, c);
   ObjectSetInteger(0, n, OBJPROP_FILL, true);
   ObjectSetInteger(0, n, OBJPROP_BACK, true);
   ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, n, OBJPROP_HIDDEN, true);
}

void MkText(string n, datetime t, double p, string txt, color c, int fs, ENUM_ANCHOR_POINT a)
{
   if(!InpDraw) return;
   if(ObjectFind(0, n) < 0) ObjectCreate(0, n, OBJ_TEXT, 0, t, p);
   ObjectSetString(0, n, OBJPROP_TEXT, txt);
   ObjectSetString(0, n, OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, n, OBJPROP_FONTSIZE, fs);
   ObjectSetInteger(0, n, OBJPROP_COLOR, c);
   ObjectSetInteger(0, n, OBJPROP_ANCHOR, a);
   ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, n, OBJPROP_HIDDEN, true);
}

void MkArrow(string n, datetime t, double p, bool up, color c)
{
   if(!InpDraw) return;
   ENUM_OBJECT type = up ? OBJ_ARROW_UP : OBJ_ARROW_DOWN;
   if(ObjectFind(0, n) < 0) ObjectCreate(0, n, type, 0, t, p);
   ObjectSetInteger(0, n, OBJPROP_COLOR, c);
   ObjectSetInteger(0, n, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, n, OBJPROP_ANCHOR, up ? ANCHOR_TOP : ANCHOR_BOTTOM);
   ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, n, OBJPROP_HIDDEN, true);
}

void DelZoneObjects(string base)
{
   ObjectDelete(0, base + "_box");
   ObjectDelete(0, base + "_lbl");
   ObjectDelete(0, base + "_p1");
   ObjectDelete(0, base + "_p2");
}

//+------------------------------------------------------------------+
//| Array helpers                                                    |
//+------------------------------------------------------------------+
void PivPushFront(Piv &arr[], const Piv &p)
{
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1);
   for(int i = n; i > 0; i--) arr[i] = arr[i - 1];
   arr[0] = p;
}

void PivPrune(Piv &arr[], int newestIdx)
{
   int n = ArraySize(arr);
   while(n > 0 && newestIdx - arr[n - 1].idx > InpMaxDist) n--;
   if(n > 64) n = 64;
   ArrayResize(arr, n);
}

void ZoneRemoveAt(int i)
{
   int n = ArraySize(g_zones);
   for(int k = i; k < n - 1; k++) g_zones[k] = g_zones[k + 1];
   ArrayResize(g_zones, n - 1);
}

//+------------------------------------------------------------------+
//| ATR (Wilder / RMA, same as TradingView ta.atr)                   |
//+------------------------------------------------------------------+
void UpdateATR(int s)
{
   double h = Hi(s), l = Lo(s), pc = Cl(s + 1);
   double tr = MathMax(h - l, MathMax(MathAbs(h - pc), MathAbs(l - pc)));
   g_atrPrev = g_atr;
   g_atrCount++;
   if(g_atrCount <= InpAtrLen)
   {
      g_trSum += tr;
      g_atr = g_trSum / g_atrCount;
   }
   else
      g_atr = (g_atr * (InpAtrLen - 1) + tr) / InpAtrLen;
}

//+------------------------------------------------------------------+
//| Pivots: candidate is InpPivotRight candles back from shift s     |
//+------------------------------------------------------------------+
bool PivotHigh(int s, double &price)
{
   int c = s + InpPivotRight;
   double v = Hi(c);
   for(int i = 1; i <= InpPivotLeft; i++)  if(Hi(c + i) > v)  return false;
   for(int i = 1; i <= InpPivotRight; i++) if(Hi(c - i) >= v) return false;
   price = v;
   return true;
}

bool PivotLow(int s, double &price)
{
   int c = s + InpPivotRight;
   double v = Lo(c);
   for(int i = 1; i <= InpPivotLeft; i++)  if(Lo(c + i) < v)  return false;
   for(int i = 1; i <= InpPivotRight; i++) if(Lo(c - i) <= v) return false;
   price = v;
   return true;
}

//+------------------------------------------------------------------+
//| Zone creation                                                    |
//+------------------------------------------------------------------+
void MakeZone(bool isHigh, const Piv &a, const Piv &b, datetime nowT)
{
   Zone z;
   z.isHigh   = isHigh;
   z.top      = MathMax(a.price, b.price);
   z.btm      = MathMin(a.price, b.price);
   z.idx2     = b.idx;
   z.vol      = a.vol + b.vol;
   z.swept    = false;
   z.sweptIdx = -1;
   z.alive    = true;
   g_zoneId++;
   z.name     = PFX + "Z" + IntegerToString(g_zoneId);

   color c = isHigh ? InpBearCol : InpBullCol;
   string nm = isHigh ? "EQH" : "EQL";
   MkRect(z.name + "_box", a.t, z.top, nowT, z.btm, c);
   MkText(z.name + "_lbl", nowT, (z.top + z.btm) / 2.0, " " + nm + " (" + FmtVol((double)z.vol) + ")", c, 8, ANCHOR_LEFT);
   MkText(z.name + "_p1", a.t, a.price, FmtVol((double)a.vol), c, 7, isHigh ? ANCHOR_LOWER : ANCHOR_UPPER);
   MkText(z.name + "_p2", b.t, b.price, FmtVol((double)b.vol), c, 7, isHigh ? ANCHOR_LOWER : ANCHOR_UPPER);

   int n = ArraySize(g_zones);
   ArrayResize(g_zones, n + 1);
   g_zones[n] = z;
}

void TryPairs(bool isHigh, const Piv &p, datetime nowT)
{
   int n = isHigh ? ArraySize(g_highs) : ArraySize(g_lows);
   for(int i = 0; i < n; i++)
   {
      Piv q;
      if(isHigh) q = g_highs[i];
      else       q = g_lows[i];
      if(p.idx - q.idx > InpMaxDist) break;
      if(q.price != 0 && MathAbs(p.price - q.price) / q.price * 100.0 <= InpEqThrPct)
      {
         MakeZone(isHigh, q, p, nowT);
         break;
      }
   }
}

//+------------------------------------------------------------------+
//| Process one closed candle at shift s (s = 1 is the last closed)  |
//+------------------------------------------------------------------+
void ProcessBar(int s, bool live)
{
   g_idx++;
   UpdateATR(s);
   datetime t0 = Tm(s);

   //--- 1) breakout: candle s+1 swept a zone, candle s confirms the gap
   bool   bullSig = false, bearSig = false;
   double bullZone = 0, bearZone = 0;
   double atr1 = (g_atrPrev > 0 ? g_atrPrev : g_atr);
   bool bullFvg = (Lo(s) > Hi(s + 2)) && (Lo(s) - Hi(s + 2) >= InpMinFvgAtr * g_atr);
   bool bearFvg = (Hi(s) < Lo(s + 2)) && (Lo(s + 2) - Hi(s) >= InpMinFvgAtr * g_atr);
   bool bigBody = MathAbs(Cl(s + 1) - Op(s + 1)) >= InpMinBodyAtr * atr1;

   int nz = ArraySize(g_zones);
   for(int i = 0; i < nz; i++)
   {
      if(!g_zones[i].alive || !g_zones[i].swept || g_zones[i].sweptIdx != g_idx - 1) continue;
      if(g_zones[i].isHigh && Cl(s + 1) > g_zones[i].top && Cl(s + 1) > Op(s + 1) && bigBody && bullFvg)
      {
         bullSig = true;
         bullZone = g_zones[i].btm;
      }
      if(!g_zones[i].isHigh && Cl(s + 1) < g_zones[i].btm && Cl(s + 1) < Op(s + 1) && bigBody && bearFvg)
      {
         bearSig = true;
         bearZone = g_zones[i].top;
      }
   }

   datetime tb   = Tm(s + 1);
   datetime tEnd = tb + (datetime)(InpFvgProj * PeriodSeconds(InpTF));
   if(bullSig)
   {
      g_sigId++;
      string b = PFX + "S" + IntegerToString(g_sigId);
      MkArrow(b + "_arr", tb, Lo(s + 1), true, InpBullCol);
      MkText(b + "_txt", tb, Lo(s + 1) - g_atr * 0.6, "Break", clrSilver, 7, ANCHOR_UPPER);
      MkRect(b + "_fvg", tb, Lo(s), tEnd, Hi(s + 2), InpBullCol);
   }
   if(bearSig)
   {
      g_sigId++;
      string b = PFX + "S" + IntegerToString(g_sigId);
      MkArrow(b + "_arr", tb, Hi(s + 1), false, InpBearCol);
      MkText(b + "_txt", tb, Hi(s + 1) + g_atr * 0.6, "Break", clrSilver, 7, ANCHOR_LOWER);
      MkRect(b + "_fvg", tb, Lo(s + 2), tEnd, Hi(s), InpBearCol);
   }

   //--- 2) sweeps / expiry / extension
   for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
   {
      if(!g_zones[i].alive) { ZoneRemoveAt(i); continue; }
      if(g_zones[i].swept)  { ZoneRemoveAt(i); continue; }   // drawings stay
      bool hit = g_zones[i].isHigh ? (Hi(s) > g_zones[i].top) : (Lo(s) < g_zones[i].btm);
      string base = g_zones[i].name;
      if(hit)
      {
         g_zones[i].swept = true;
         g_zones[i].sweptIdx = g_idx;
         if(InpDraw)
         {
            ObjectSetInteger(0, base + "_box", OBJPROP_TIME, 1, t0);
            ObjectSetInteger(0, base + "_box", OBJPROP_COLOR, clrDimGray);
            ObjectSetInteger(0, base + "_lbl", OBJPROP_TIME, 0, t0);
            ObjectSetString(0, base + "_lbl", OBJPROP_TEXT, " Swept " + (g_zones[i].isHigh ? "EQH" : "EQL") + " (" + FmtVol((double)g_zones[i].vol) + ")");
            ObjectSetInteger(0, base + "_lbl", OBJPROP_COLOR, clrGray);
            ObjectSetInteger(0, base + "_p1", OBJPROP_COLOR, clrGray);
            ObjectSetInteger(0, base + "_p2", OBJPROP_COLOR, clrGray);
         }
      }
      else if(g_idx - g_zones[i].idx2 > InpMaxAge)
      {
         DelZoneObjects(base);
         ZoneRemoveAt(i);
      }
      else if(InpDraw)
      {
         ObjectSetInteger(0, base + "_box", OBJPROP_TIME, 1, t0);
         ObjectSetInteger(0, base + "_lbl", OBJPROP_TIME, 0, t0);
      }
   }

   //--- 3) new pivots -> equal highs / lows
   bool sessOk = !InpNYZonesOnly || InNYSession(Tm(s + InpPivotRight));
   double pp;
   if(PivotHigh(s, pp))
   {
      Piv p;
      p.price = pp;
      p.idx   = g_idx - InpPivotRight;
      p.vol   = Vo(s + InpPivotRight);
      p.t     = Tm(s + InpPivotRight);
      if(sessOk) TryPairs(true, p, t0);
      PivPushFront(g_highs, p);
      PivPrune(g_highs, p.idx);
   }
   if(PivotLow(s, pp))
   {
      Piv p;
      p.price = pp;
      p.idx   = g_idx - InpPivotRight;
      p.vol   = Vo(s + InpPivotRight);
      p.t     = Tm(s + InpPivotRight);
      if(sessOk) TryPairs(false, p, t0);
      PivPushFront(g_lows, p);
      PivPrune(g_lows, p.idx);
   }

   //--- 4) cap active zones (oldest go first)
   int active = 0;
   for(int i = 0; i < ArraySize(g_zones); i++) if(!g_zones[i].swept) active++;
   while(active > InpMaxZones && ArraySize(g_zones) > 0)
   {
      if(!g_zones[0].swept) { DelZoneObjects(g_zones[0].name); active--; }
      ZoneRemoveAt(0);
   }

   //--- 5) signals -> alerts / trades (live candles only)
   if(!live || (!bullSig && !bearSig)) return;

   string sigTxt = bullSig ? "BUY (EQH break + FVG)" : "SELL (EQL break + FVG)";
   if(InpAlerts) Alert(_Symbol, " ", EnumToString(InpTF), " EQH/EQL FVG: ", sigTxt);
   if(InpPush)   SendNotification(_Symbol + " EQH/EQL FVG: " + sigTxt);

   if(!InpAutoTrade) return;
   if(HasPosition()) return;
   if(InpTradeInSess && !InNYSession(Tm(0))) return;

   MqlDateTime d;
   TimeToStruct(TimeCurrent(), d);
   int key = d.year * 1000 + d.day_of_year;
   if(key != g_dayKey) { g_dayKey = key; g_tradesDay = 0; }
   if(InpMaxPerDay > 0 && g_tradesDay >= InpMaxPerDay) return;

   if(bullSig && InpDirection != EF_DIR_SHORT)
      OpenTrade(true,  s, bullZone);
   else if(bearSig && InpDirection != EF_DIR_LONG)
      OpenTrade(false, s, bearZone);
}

//+------------------------------------------------------------------+
//| Trading                                                          |
//+------------------------------------------------------------------+
bool HasPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic)
         return true;
   }
   return false;
}

double CalcLots(bool isBuy, double entry, double stop, double &realRiskPct)
{
   realRiskPct = 0;
   double profit = 0;
   if(!OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, 1.0, entry, stop, profit)) return 0;
   double lossPerLot = MathAbs(profit);
   if(lossPerLot <= 0) return 0;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;

   double lots = MathFloor((equity * InpRiskPct / 100.0) / lossPerLot / step) * step;
   if(lots < vmin)
   {
      if(InpMinLot == EF_MIN_SKIP)
      {
         PrintFormat("EQH/EQL FVG: risk lot %.4f < broker minimum %.2f, trade skipped (raise risk %% or equity)", lots, vmin);
         return 0;
      }
      lots = vmin;
   }
   lots = MathMin(lots, MathMin(InpMaxLots, vmax));

   // margin check
   double margin = 0;
   if(OrderCalcMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, lots, entry, margin))
   {
      double free = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      while(margin > free * 0.9 && lots - step >= vmin)
      {
         lots -= step;
         if(!OrderCalcMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, lots, entry, margin)) break;
      }
      if(margin > free * 0.9) { Print("EQH/EQL FVG: not enough free margin, trade skipped"); return 0; }
   }
   lots = NormalizeDouble(lots, 2);
   if(equity > 0) realRiskPct = lots * lossPerLot / equity * 100.0;
   return lots;
}

void OpenTrade(bool isBuy, int s, double zoneLvl)
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0) return;
   if(InpMaxSpread > 0 && ask - bid > InpMaxSpread)
   {
      PrintFormat("EQH/EQL FVG: spread %.2f > max %.2f, trade skipped", ask - bid, InpMaxSpread);
      return;
   }
   double entry = isBuy ? ask : bid;
   double stop;
   if(isBuy)
   {
      if(InpStopMode == EF_STOP_FVG)         stop = Hi(s + 2);
      else if(InpStopMode == EF_STOP_CANDLE) stop = Lo(s + 1);
      else if(InpStopMode == EF_STOP_ZONE)   stop = zoneLvl;
      else                                   stop = entry - InpAtrStop * g_atr;
      stop -= InpStopBufAtr * g_atr;
   }
   else
   {
      if(InpStopMode == EF_STOP_FVG)         stop = Lo(s + 2);
      else if(InpStopMode == EF_STOP_CANDLE) stop = Hi(s + 1);
      else if(InpStopMode == EF_STOP_ZONE)   stop = zoneLvl;
      else                                   stop = entry + InpAtrStop * g_atr;
      stop += InpStopBufAtr * g_atr;
   }
   double risk = isBuy ? entry - stop : stop - entry;
   double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(risk <= 0 || risk < minDist)
   {
      PrintFormat("EQH/EQL FVG: stop distance %.2f too small (min %.2f), trade skipped", risk, minDist);
      return;
   }
   double tp = isBuy ? entry + InpTargetR * risk : entry - InpTargetR * risk;
   stop = NormPrice(stop);
   tp   = NormPrice(tp);

   double realRisk = 0;
   double lots = CalcLots(isBuy, entry, stop, realRisk);
   if(lots <= 0) return;

   bool ok = isBuy ? trade.Buy(lots, _Symbol, 0, stop, tp, InpComment)
                   : trade.Sell(lots, _Symbol, 0, stop, tp, InpComment);
   if(ok && trade.ResultRetcode() == TRADE_RETCODE_DONE)
   {
      g_tradesDay++;
      PrintFormat("EQH/EQL FVG: %s %.2f lots @ %.2f  SL %.2f  TP %.2f  risk %.2f%% of equity",
                  isBuy ? "BUY" : "SELL", lots, entry, stop, tp, realRisk);
   }
   else
      PrintFormat("EQH/EQL FVG: order failed, retcode %d (%s)", trade.ResultRetcode(), trade.ResultRetcodeDescription());
}

void ManageHold()
{
   if(InpMaxHold <= 0) return;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      int held = iBarShift(_Symbol, InpTF, opened);
      if(held >= InpMaxHold) trade.PositionClose(tk);
   }
}

//+------------------------------------------------------------------+
//| Start-up replay so zones exist straight away                     |
//+------------------------------------------------------------------+
bool Replay()
{
   int need = InpPivotLeft + InpPivotRight + 5;
   int bars = Bars(_Symbol, InpTF);
   if(bars < need + 10) return false;
   int n = MathMin(InpReplayBars, bars - need);
   for(int s = n; s >= 1; s--) ProcessBar(s, false);
   g_lastDone = Tm(1);
   return true;
}

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(50);
   if(InpPivotLeft < 1 || InpPivotRight < 1 || InpAtrLen < 1 || InpRiskPct <= 0)
   {
      Print("EQH/EQL FVG: invalid inputs");
      return INIT_PARAMETERS_INCORRECT;
   }
   g_ready = Replay();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, PFX);
}

void OnTick()
{
   if(!g_ready) { g_ready = Replay(); if(!g_ready) return; }

   datetime last = Tm(1);
   if(last == 0 || last == g_lastDone) return;

   // process every closed candle since the last one we handled
   int from = iBarShift(_Symbol, InpTF, g_lastDone, true);
   if(from < 1) from = 2;            // history gap: just process the last closed candle
   for(int s = from - 1; s >= 1; s--) ProcessBar(s, s == 1);
   g_lastDone = last;

   ManageHold();
}
//+------------------------------------------------------------------+
