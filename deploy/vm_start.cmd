@echo off
rem Start the cloud VM (pbr-web) and wait until the web page answers. A running VM is billed.
rem Stop it with: deploy\vm_start.cmd stop
rem This file is ASCII only on purpose: cmd misreads UTF-8 Korean in .cmd files.
rem The static IP (pbr-web-ip) keeps geol.cloud pointing at the VM.

set "GCLOUD=gcloud"
where gcloud >nul 2>nul || set "GCLOUD=%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
set ZONE=asia-northeast3-a

if /i "%1"=="stop" (
  call "%GCLOUD%" compute instances stop pbr-web --zone=%ZONE%
  echo VM stopped. Billing for the VM has stopped.
  goto :end
)

call "%GCLOUD%" compute instances start pbr-web --zone=%ZONE%
echo.
echo Waiting for the web page (up to about 2 minutes)...
for /l %%i in (1,1,24) do (
  curl -s -o nul -m 5 -w "%%{http_code}" https://geol.cloud/kpi | findstr /b 200 >nul && goto :up
  timeout /t 5 /nobreak >nul
)
echo No answer yet. Open https://geol.cloud again in a moment.
goto :end

:up
echo.
echo Up: https://geol.cloud
echo The server restarted, so the live-compare record is empty.
echo Press the live-compare button on /orders again.

:end
if "%1"=="" pause
