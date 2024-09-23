@echo off
SET USER=Makarov
echo Обновление шаблона из авторского репозитория: 
echo "    https://github.com/AndreyAkinshin/Russian-Phd-LaTeX-Dissertation-Template"
git remote add upstream https://github.com/AndreyAkinshin/Russian-Phd-LaTeX-Dissertation-Template>nul 2>&1
git checkout master
git pull
git fetch upstream
choice /C YNC /T 77 /D Y /M "Показать изменения в шаблоне? [C] - закончить."
if %errorlevel% == 3 goto FIN
if %errorlevel% == 2 goto CONT
if %errorlevel% == 1 goto DIFF
if %errorlevel% == 0 goto FIN

:DIFF
echo Посмотрим, что изменилось. Команда выхода ':q'
git diff upstream/master
choice /C YN /T 77 /D Y /M "Обновляем шаблон: "
if %errorlevel% == 2 goto FIN
if %errorlevel% == 1 goto CONT
if %errorlevel% == 0 goto FIN
:CONT
git merge upstream/master
git push

:FIN
echo ===================== Выполнено =====================
echo.
choice /C YN /T 77 /D Y /M "%USER% - обновить шаблон пользователя "
if %errorlevel% == 2 goto HALT
if %errorlevel% == 1 goto UPDATE
if %errorlevel% == 0 goto HALT
:UPDATE
git checkout %USER%
git diff master
git merge master

:HALT
echo ===================== Завешено =====================
echo.