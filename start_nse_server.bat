@echo off
echo Starting NSE data server (stock-nse-india) on port 3000...
cd /d C:\AlgoTrading\kite_source
start "NSE Server" cmd /k "npx --yes stock-nse-india"
echo NSE server starting — wait ~10 seconds for it to be ready at http://localhost:3000
