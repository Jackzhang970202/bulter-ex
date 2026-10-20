@echo off
chcp 65001 >nul
cd /d %~dp0
echo 部门知识智能体 启动中… http://127.0.0.1:8633
python run.py
