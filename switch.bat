@echo off
chcp 65001
cd /d "C:\Users\francishuang\Desktop\Try\鐵匠鋪遊戲設計\blacksmith_widget"
for /f %%i in ('git branch --show-current') do (
    if "%%i"=="main" (
        git checkout exp
        echo 已切換到版本 exp
    ) else (
        git checkout main
        echo 已切換到版本 main
    )
)
pause