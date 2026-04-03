//+------------------------------------------------------------------+
//|  TradingDashboardEA.mq5                                          |
//|  MT5 Expert Advisor — Trading Dashboard Data Push                 |
//|  Sends account data, positions, and closed deals via HTTPS POST   |
//+------------------------------------------------------------------+
#property copyright "Trading Dashboard"
#property version   "1.0.0"

#include <Trade\Trade.mqh>
#include <Arrays\ArrayInt.mqh>

// ── Input Parameters ──────────────────────────────────────────────────────────
input string   DashboardURL    = "https://yourdomain.com";
input string   ApiKey          = "";
input int      PushIntervalSec = 5;
input bool     SendOpenTrades  = true;
input bool     SendClosedDeals = true;
input bool     DebugMode       = false;

// ── Global Variables ──────────────────────────────────────────────────────────
datetime g_lastPushTime = 0;
CArrayInt g_sentDealIds;
bool g_initialized = false;
string g_accountNumber;
string g_pushEndpoint;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit() {
   g_accountNumber = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   g_pushEndpoint  = DashboardURL + "/api/v1/push";

   if (ApiKey == "") {
      Print("[TDash] ERROR: API Key not set in EA inputs.");
      return INIT_FAILED;
   }

   // Mark existing deal history as already sent
   HistorySelect(0, TimeCurrent());
   for (int i = 0; i < HistoryDealsTotal(); i++) {
      ulong dealId = HistoryDealGetTicket(i);
      long dealType = HistoryDealGetInteger(dealId, DEAL_TYPE);
      if (dealType == DEAL_TYPE_BUY || dealType == DEAL_TYPE_SELL) {
         g_sentDealIds.Add((int)dealId);
      }
   }

   g_initialized = true;
   Print("[TDash] Initialized — Account #", g_accountNumber, " → ", DashboardURL);
   EventSetTimer(1);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   EventKillTimer();
}

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
void DoPush() {
   string openJson   = "";
   string closedJson = "";

   // ── Open Positions ────────────────────────────────────────────────
   if (SendOpenTrades) {
      string parts[];
      ArrayResize(parts, 0);

      for (int i = 0; i < PositionsTotal(); i++) {
         string entry = BuildPositionJson(i);
         if (entry != "") {
            int n = ArraySize(parts);
            ArrayResize(parts, n + 1);
            parts[n] = entry;
         }
      }
      openJson = ArrayJoin(parts, ",");
   }

   // ── Closed Deals Since Last Push ──────────────────────────────────
   if (SendClosedDeals) {
      string parts[];
      ArrayResize(parts, 0);

      HistorySelect(0, TimeCurrent());
      for (int i = 0; i < HistoryDealsTotal(); i++) {
         ulong dealId   = HistoryDealGetTicket(i);
         long dealType  = (long)HistoryDealGetInteger(dealId, DEAL_TYPE);
         long dealEntry = (long)HistoryDealGetInteger(dealId, DEAL_ENTRY);

         if ((dealType != DEAL_TYPE_BUY && dealType != DEAL_TYPE_SELL)) continue;
         if (dealEntry != DEAL_ENTRY_OUT) continue;  // only closed trades

         if (!g_sentDealIds.Contains((int)dealId)) {
            string entry = BuildClosedDealJson(dealId);
            if (entry != "") {
               int n = ArraySize(parts);
               ArrayResize(parts, n + 1);
               parts[n] = entry;
               g_sentDealIds.Add((int)dealId);
            }
         }
      }
      closedJson = ArrayJoin(parts, ",");
   }

   // ── Account metrics ───────────────────────────────────────────────
   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity     = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin     = AccountInfoDouble(ACCOUNT_MARGIN);
   double freeMarg   = AccountInfoDouble(ACCOUNT_FREEMARGIN);
   double margLevel  = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   double floatPL    = equity - balance;

   string serverTime = TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES | TIME_SECONDS);
   StringReplace(serverTime, ".", "-");
   serverTime += "Z";

   string ea_version = "1.0.0";
   string ea_build   = "MT5";

   string payload = StringFormat(
      "{"
      "\"account_number\":\"%s\","
      "\"api_key\":\"%s\","
      "\"server_time\":\"%s\","
      "\"balance\":%.2f,"
      "\"equity\":%.2f,"
      "\"margin\":%.2f,"
      "\"free_margin\":%.2f,"
      "\"margin_level\":%.4f,"
      "\"floating_pl\":%.2f,"
      "\"open_positions\":[%s],"
      "\"closed_since_last_push\":[%s],"
      "\"ea_version\":\"%s\","
      "\"ea_build\":\"%s\""
      "}",
      g_accountNumber, ApiKey, serverTime,
      balance, equity, margin, freeMarg, margLevel, floatPL,
      openJson, closedJson, ea_version, ea_build
   );

   if (DebugMode) Print("[TDash] Sending payload (", StringLen(payload), " bytes)");

   // ── HTTP POST ─────────────────────────────────────────────────────
   char   postData[], resultData[];
   string resultHeaders;
   string headers = "Content-Type: application/json\r\n";

   StringToCharArray(payload, postData, 0, StringLen(payload), CP_UTF8);
   ArrayResize(postData, ArraySize(postData) - 1);  // remove null terminator

   int res = WebRequest("POST", g_pushEndpoint, headers, 5000, postData, resultData, resultHeaders);

   if (res == -1) {
      int err = GetLastError();
      if (err == 4060) {
         Print("[TDash] ERROR 4060: Add '", DashboardURL, "' to Tools > Options > Expert Advisors > Allowed URLs");
      } else {
         Print("[TDash] WebRequest error: ", err);
      }
   } else if (res == 200 || res == 201) {
      if (DebugMode) Print("[TDash] Push OK — HTTP ", res);
   } else {
      string resp = CharArrayToString(resultData, 0, WHOLE_ARRAY, CP_UTF8);
      Print("[TDash] HTTP ", res, " — ", StringSubstr(resp, 0, 300));
   }
}

//+------------------------------------------------------------------+
string BuildPositionJson(int index) {
   if (!PositionGetTicket(index)) return "";

   string symbol   = PositionGetString(POSITION_SYMBOL);
   double volume   = PositionGetDouble(POSITION_VOLUME);
   double openPx   = PositionGetDouble(POSITION_PRICE_OPEN);
   double curPx    = PositionGetDouble(POSITION_PRICE_CURRENT);
   double sl       = PositionGetDouble(POSITION_SL);
   double tp       = PositionGetDouble(POSITION_TP);
   double profit   = PositionGetDouble(POSITION_PROFIT);
   double swap     = PositionGetDouble(POSITION_SWAP);
   long   type     = PositionGetInteger(POSITION_TYPE);
   long   magic    = PositionGetInteger(POSITION_MAGIC);
   long   ticket   = PositionGetInteger(POSITION_TICKET);
   string comment  = PositionGetString(POSITION_COMMENT);
   datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);

   string typeStr = (type == POSITION_TYPE_BUY) ? "buy" : "sell";
   string openTs  = TimeToString(openTime, TIME_DATE|TIME_MINUTES|TIME_SECONDS);
   StringReplace(openTs, ".", "-");

   return StringFormat(
      "{"
      "\"ticket\":%d,\"symbol\":\"%s\",\"type\":\"%s\","
      "\"lots\":%.2f,\"open_price\":%.5f,\"current_price\":%.5f,"
      "\"sl\":%.5f,\"tp\":%.5f,\"floating_pl\":%.2f,\"swap\":%.2f,"
      "\"open_time\":\"%sZ\",\"magic_number\":%d,\"comment\":\"%s\""
      "}",
      ticket, symbol, typeStr, volume, openPx, curPx,
      sl, tp, profit, swap, openTs, magic, EscapeJson(comment)
   );
}

//+------------------------------------------------------------------+
string BuildClosedDealJson(ulong dealId) {
   string symbol    = HistoryDealGetString(dealId, DEAL_SYMBOL);
   double volume    = HistoryDealGetDouble(dealId, DEAL_VOLUME);
   double price     = HistoryDealGetDouble(dealId, DEAL_PRICE);
   double profit    = HistoryDealGetDouble(dealId, DEAL_PROFIT);
   double commission= HistoryDealGetDouble(dealId, DEAL_COMMISSION);
   double swap      = HistoryDealGetDouble(dealId, DEAL_SWAP);
   long   type      = (long)HistoryDealGetInteger(dealId, DEAL_TYPE);
   long   magic     = (long)HistoryDealGetInteger(dealId, DEAL_MAGIC);
   long   orderId   = (long)HistoryDealGetInteger(dealId, DEAL_ORDER);
   string comment   = HistoryDealGetString(dealId, DEAL_COMMENT);
   datetime dealTime= (datetime)HistoryDealGetInteger(dealId, DEAL_TIME);

   string typeStr = (type == DEAL_TYPE_BUY) ? "buy" : "sell";
   string closeTs = TimeToString(dealTime, TIME_DATE|TIME_MINUTES|TIME_SECONDS);
   StringReplace(closeTs, ".", "-");

   // Get open price from corresponding order
   double openPrice = 0;
   datetime openTime = dealTime;
   if (HistoryOrderSelect(orderId)) {
      openPrice = HistoryOrderGetDouble(orderId, ORDER_PRICE_OPEN);
      openTime  = (datetime)HistoryOrderGetInteger(orderId, ORDER_TIME_SETUP);
   }

   string openTs = TimeToString(openTime, TIME_DATE|TIME_MINUTES|TIME_SECONDS);
   StringReplace(openTs, ".", "-");

   return StringFormat(
      "{"
      "\"ticket\":%d,\"symbol\":\"%s\",\"type\":\"%s\","
      "\"lots\":%.2f,\"open_price\":%.5f,\"close_price\":%.5f,"
      "\"sl\":0.0,\"tp\":0.0,"
      "\"profit\":%.2f,\"commission\":%.2f,\"swap\":%.2f,"
      "\"open_time\":\"%sZ\",\"close_time\":\"%sZ\","
      "\"magic_number\":%d,\"comment\":\"%s\""
      "}",
      (int)dealId, symbol, typeStr, volume, openPrice, price,
      profit, commission, swap, openTs, closeTs, magic, EscapeJson(comment)
   );
}

//+------------------------------------------------------------------+
string EscapeJson(string s) {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
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
//+------------------------------------------------------------------+
