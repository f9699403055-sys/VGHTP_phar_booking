# VGHTP_phar_booking

這是一個用來整理台北榮總臨床試驗藥局 MV 訪視預約時段的 GitHub 專案。

## 目前做的事

- 讀取 Appointy 的 `workschedules`
- 讀取 `bookedslots`
- 預留 `blocktimes` / `exceptions`
- 算出可預約時段
- 可選擇寄 Email

## 本機執行

```bash
pip install -r requirements.txt
python check.py
```

預設會使用你目前抓到的 Appointy API URL。

## 常用參數

```bash
python check.py --start 2026-08-10 --days 21
```

## GitHub Actions 自動執行

把下面 secrets 加到 repository：

- `EMAIL_SMTP_HOST`
- `EMAIL_SMTP_PORT`
- `EMAIL_SMTP_USERNAME`
- `EMAIL_SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

如果要改收件人或信箱，也可以改 workflow 裡的環境變數。

## 時區

workflow 預設排在 UTC 01:00，也就是台北時間上午 09:00，週一到週五執行。
