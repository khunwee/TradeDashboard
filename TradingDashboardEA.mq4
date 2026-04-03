//+------------------------------------------------------------------+
//|  TradingDashboardEA.mq4                                          |
//|  MT4 Expert Advisor — Trading Dashboard Data Push                 |
//|  Sends account data, positions, and closed trades via HTTP POST   |
//+------------------------------------------------------------------+
#property copyright "Trading Dashboard"
#property version   "1.0.0"
#property strict

// ── Input Parameters ──────────────────────────────────────────────────────────
input string   DashboardURL    = "https://yourdomain.com";  // Dashboard URL
input string   ApiKey          = "";                         // EA API Key (from dashboard)
input int      PushIntervalSec = 5;                          // Push interval (seconds)
input bool     SendOpenTrades  = true;                       // Include open positions
input bool     SendClosedTrades= true;                       // Include closed trades
input bool     EnableHeartbeat = true;                       // Send heartbeat when no change
input bool     DebugMode       = false;                      // Print debug logs

// ── Global Variables ──────────────────────────────────────────────────────────
datetime g_lastPushTime    = 0;
datetime g_lastClosedCheck = 0;
int      g_totalSentTickets[];
int      g_sentCount = 0;
bool     g_initialized = false;
string   g_accountNumber;
string   g_pushEndpoint;
string   g_heartbeatEndpoint;

//+------------------------------------------------------------------+
//| Expert initialization function                                    |
//+------------------------------------------------------------------+
int OnInit() {
   g_accountNumber     = IntegerToString(AccountNumber());
   g_pushEndpoint      = DashboardURL + "/api/v1/push";
   g_heartbeatEndpoint = DashboardURL + "/api/v1/heartbeat";

   if (ApiKey == "") {
      Print("[TDash] ERROR: API Key is empty! Set it in EA inputs.");
      return INIT_FAILED;
   }

   // Build initial sent tickets list from current history
   ArrayResize(g_totalSentTickets, 0);
   InitSentTickets();
   g_initialized = true;

   Print("[TDash] Initialized — Account #", g_accountNumber, " → ", DashboardURL);
   EventSetTimer(1);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   EventKillTimer();
   Print("[TDash] EA stopped.");
}

//+------------------------------------------------------------------+
//| Timer function — called every second                              |
//+------------------------------------------------------------------+
void OnTimer() {
   if (!g_initialized) return;
   datetime now = TimeCurrent();
   if ((now - g_lastPushTime) >= PushIntervalSec) {
      g_lastPushTime = now;
      DoPush();
   }
}

//+------------------------------------------------------------------+
//| Build and send the push payload                                   |
//+------------------------------------------------------------------+
void DoPush() {
   string openJson   = "";
   string closedJson = "";
   int    openCount  = 0;
   int    closedCount= 0;

   // ── Open Positions ────────────────────────────────────────────────
   if (SendOpenTrades) {
      string parts[];
      ArrayResize(parts, 0);

      for (int i = 0; i < OrdersTotal(); i++) {
         if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
         if (OrderMagicNumber() >= 0 && OrderType() > OP_SELL) continue; // skip pending in simple mode

         string entry = BuildOpenPositionJson(i);
         if (entry != "") {
            int n = ArraySize(parts);
            ArrayResize(parts, n + 1);
            parts[n] = entry;
         }
      }

      openJson  = ArrayJoin(parts, ",");
      openCount = ArraySize(parts);
   }

   // ── Closed Trades Since Last Push ─────────────────────────────────
   if (SendClosedTrades) {
      string parts[];
      ArrayResize(parts, 0);

      int histTotal = OrdersHistoryTotal();
      for (int i = 0; i < histTotal; i++) {
         if (!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) continue;
         if (OrderType() != OP_BUY && OrderType() != OP_SELL) continue;

         int ticket = OrderTicket();
         if (!IsTicketSent(ticket)) {
            string entry = BuildClosedTradeJson(i);
            if (entry != "") {
               int n = ArraySize(parts);
               ArrayResize(parts, n + 1);
               parts[n] = entry;
               MarkTicketSent(ticket);
            }
         }
      }

      closedJson  = ArrayJoin(parts, ",");
      closedCount = ArraySize(parts);
   }

   // ── Build Main Payload ────────────────────────────────────────────
   string serverTime = TimeToStr(TimeCurrent(), TIME_DATE | TIME_MINUTES | TIME_SECONDS);
   StringReplace(serverTime, ".", "-");
   serverTime += "Z";  // approximate UTC

   string payload = StringFormat(
      "{"
      "\"account_number\":\"%s\","
      "\"api_key\":\"%s\","
      "\"server_time\":\"%s\","
      "\"balance\":%.2f,"
      "\"equity\":%.2f,"
      "\"margin\":%.2f,"
      "\"free_margin\":%.2f,"
      "\"margin_level\":%.2f,"
      "\"floating_pl\":%.2f,"
      "\"open_positions\":[%s],"
      "\"closed_since_last_push\":[%s],"
      "\"ea_version\":\"1.0.0\","
      "\"ea_build\":\"MT4\""
      "}",
      g_accountNumber,
      ApiKey,
      serverTime,
      AccountBalance(),
      AccountEquity(),
      AccountMargin(),
      AccountFreeMargin(),
      AccountMargin() > 0 ? AccountEquity() / AccountMargin() * 100.0 : 0.0,
      AccountEquity() - AccountBalance(),
      openJson,
      closedJson
   );

   if (DebugMode) Print("[TDash] Payload (", StringLen(payload), " chars) | Open:", openCount, " Closed:", closedCount);

   // ── HTTP POST ─────────────────────────────────────────────────────
   string headers = "Content-Type: application/json\r\n";
   char   postData[], resultData[];
   string resultHeaders;
   StringToCharArray(payload, postData, 0, StringLen(payload), CP_UTF8);

   int timeout = 5000;
   int res = WebRequest("POST", g_pushEndpoint, headers, timeout, postData, resultData, resultHeaders);

   if (res == -1) {
      int err = GetLastError();
      if (err == 4060) {
         Print("[TDash] ERROR: URL not allowed. Add '", DashboardURL, "' to Tools > Options > Expert Advisors > Allowed URLs");
      } else {
         Print("[TDash] WebRequest failed. Error:", err);
      }
   } else if (res == 200 || res == 201) {
      if (DebugMode) Print("[TDash] Push OK | HTTP ", res);
   } else {
      string response = CharArrayToString(resultData, 0, WHOLE_ARRAY, CP_UTF8);
      Print("[TDash] Push failed | HTTP ", res, " | ", StringSubstr(response, 0, 200));
   }
}

//+------------------------------------------------------------------+
//| Build JSON for a single open position                             |
//+------------------------------------------------------------------+
string BuildOpenPositionJson(int pos) {
   if (!OrderSelect(pos, SELECT_BY_POS, MODE_TRADES)) return "";

   string tradeType = TradeTypeStr(OrderType());
   if (tradeType == "") return "";

   string openTime = TimeToStr(OrderOpenTime(), TIME_DATE | TIME_MINUTES | TIME_SECONDS);
   StringReplace(openTime, ".", "-");

   return StringFormat(
      "{"
      "\"ticket\":%d,"
      "\"symbol\":\"%s\","
      "\"type\":\"%s\","
      "\"lots\":%.2f,"
      "\"open_price\":%.5f,"
      "\"current_price\":%.5f,"
      "\"sl\":%.5f,"
      "\"tp\":%.5f,"
      "\"floating_pl\":%.2f,"
      "\"swap\":%.2f,"
      "\"open_time\":\"%sZ\","
      "\"magic_number\":%d,"
      "\"comment\":\"%s\""
      "}",
      OrderTicket(),
      OrderSymbol(),
      tradeType,
      OrderLots(),
      OrderOpenPrice(),
      OrderClosePrice(),
      OrderStopLoss(),
      OrderTakeProfit(),
      OrderProfit(),
      OrderSwap(),
      openTime,
      OrderMagicNumber(),
      EscapeJson(OrderComment())
   );
}

//+------------------------------------------------------------------+
//| Build JSON for a single closed trade                              |
//+------------------------------------------------------------------+
string BuildClosedTradeJson(int pos) {
   if (!OrderSelect(pos, SELECT_BY_POS, MODE_HISTORY)) return "";
   if (OrderType() != OP_BUY && OrderType() != OP_SELL) return "";

   string openTime  = TimeToStr(OrderOpenTime(),  TIME_DATE | TIME_MINUTES | TIME_SECONDS);
   string closeTime = TimeToStr(OrderCloseTime(), TIME_DATE | TIME_MINUTES | TIME_SECONDS);
   StringReplace(openTime,  ".", "-");
   StringReplace(closeTime, ".", "-");

   return StringFormat(
      "{"
      "\"ticket\":%d,"
      "\"symbol\":\"%s\","
      "\"type\":\"%s\","
      "\"lots\":%.2f,"
      "\"open_price\":%.5f,"
      "\"close_price\":%.5f,"
      "\"sl\":%.5f,"
      "\"tp\":%.5f,"
      "\"profit\":%.2f,"
      "\"commission\":%.2f,"
      "\"swap\":%.2f,"
      "\"open_time\":\"%sZ\","
      "\"close_time\":\"%sZ\","
      "\"magic_number\":%d,"
      "\"comment\":\"%s\""
      "}",
      OrderTicket(),
      OrderSymbol(),
      TradeTypeStr(OrderType()),
      OrderLots(),
      OrderOpenPrice(),
      OrderClosePrice(),
      OrderStopLoss(),
      OrderTakeProfit(),
      OrderProfit(),
      OrderCommission(),
      OrderSwap(),
      openTime,
      closeTime,
      OrderMagicNumber(),
      EscapeJson(OrderComment())
   );
}

//+------------------------------------------------------------------+
//| Helpers                                                            |
//+------------------------------------------------------------------+
string TradeTypeStr(int type) {
   switch (type) {
      case OP_BUY:        return "buy";
      case OP_SELL:       return "sell";
      case OP_BUYLIMIT:   return "buy_limit";
      case OP_SELLLIMIT:  return "sell_limit";
      case OP_BUYSTOP:    return "buy_stop";
      case OP_SELLSTOP:   return "sell_stop";
      default:            return "";
   }
}

string EscapeJson(string s) {
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\\",  "\\\\");
   return s;
}

string ArrayJoin(string &arr[], string sep) {
   string result = "";
   for (int i = 0; i < ArraySize(arr); i++) {
      if (i > 0) result += sep;
      result += arr[i];
   }
   return result;
}

void InitSentTickets() {
   int histTotal = OrdersHistoryTotal();
   ArrayResize(g_totalSentTickets, histTotal);
   for (int i = 0; i < histTotal; i++) {
      if (OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) {
         if (OrderType() == OP_BUY || OrderType() == OP_SELL) {
            if (g_sentCount < ArraySize(g_totalSentTickets)) {
               g_totalSentTickets[g_sentCount++] = OrderTicket();
            }
         }
      }
   }
   ArrayResize(g_totalSentTickets, g_sentCount);
}

bool IsTicketSent(int ticket) {
   for (int i = 0; i < g_sentCount; i++) {
      if (g_totalSentTickets[i] == ticket) return true;
   }
   return false;
}

void MarkTicketSent(int ticket) {
   ArrayResize(g_totalSentTickets, g_sentCount + 1);
   g_totalSentTickets[g_sentCount++] = ticket;
}
//+------------------------------------------------------------------+
