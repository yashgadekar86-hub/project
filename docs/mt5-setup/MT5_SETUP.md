# MetaTrader 5 Setup Guide

## Windows (Recommended)

1. Install MetaTrader 5 from your broker (e.g. FortressFX) or from
   https://www.metatrader5.com/.
2. Log in to your demo/live account through the MT5 terminal once, so the terminal
   saves credentials locally and allows algorithmic trading.
3. Enable **Algorithmic Trading** in MT5 (Tools → Options → Expert Advisors →
   "Allow algorithmic trading").
4. In the AI Forex Command Center, open `/mt5-connection`:
   - Login: your MT5 account number (e.g. `12345678`)
   - Password: your MT5 password
   - Server: the server name exactly as it appears in MT5 (e.g. `FortressFX-Live`)
   - Terminal path: usually auto-detected, otherwise
     `C:\Program Files\MetaTrader 5\terminal64.exe`
5. Click **Connect**. The terminal's broker, balance, equity, margin, leverage,
   and symbols will auto-populate.

## Linux (Experimental via Wine)

MT5 does not officially support Linux. You can run it under Wine:

```bash
# Install Wine, then install MT5 via winetricks or the installer
# Example terminal path after installation:
~/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe
```

Note: we test primarily on Windows. Linux deployment uses a Windows VM or the
MQL5 bridge EA.

## macOS

Run MT5 via a Windows VM or use the broker's web/Mac MT5 build if available.
Connect from a Windows machine running the backend.

## FortressFX

FortressFX works if it provides standard MT5 server access:
- Enter the **exact** server name as shown in MT5 login screen
- Symbol suffixes, contract specs, min lot, leverage, stops level are all
  auto-detected; nothing is hard-coded
- Verify symbols appear in `/mt5-connection` → Detected Symbols table

## Troubleshooting

| Symptom                            | Fix                                                       |
|------------------------------------|-----------------------------------------------------------|
| "MT5 initialize failed"            | Start the MT5 terminal manually and try again             |
| "symbol_select failed"             | Add the symbol to MarketWatch in MT5                      |
| "Unsupported retcode 100xx"        | Check `mt5_comment`; most are "Invalid stops" / "Off quotes" / "Market closed" |
| "Trade is disabled"                | Account may be read-only, or server has trading disabled  |
| Linux: `MetaTrader5` import error  | MT5 is Windows-only; run backend on Windows or use the MQL5 bridge |
