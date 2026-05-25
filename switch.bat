@echo off
chcp 65001
cd /d "C:\Users\francishuang\Desktop\Try\鐵匠鋪遊戲設計\blacksmith_widget"
for /f %%i in ('git branch --show-current') do (
    if "%%i"=="main" (
        git checkout exp
        if errorlevel 1 (
            echo 切換失敗，請先 commit 或處理上面的錯誤
        ) else (
            echo 已切換到版本 exp
        )
    ) else (
        git checkout main
        if errorlevel 1 (
            echo 切換失敗，請先 commit 或處理上面的錯誤
        ) else (
            echo 已切換到版本 main
        )
    )
)
pause