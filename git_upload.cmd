@Echo Off
::SetLocal EnableDelayedExpansion
set first=%1
if (%first%)==() goto ErrorMessage
: Проверка первого и последнего символов комментария на кавычность
:: Считаем,что идиотов нет и кавычки будут парными.
:: Мы же не няньки, а скрипт -- не программа, чтобы ловить все исключения...
if %first:~0,1%%first:~-1%==^"^" (goto Quoted)
:: комментарий незакавычен
:NotQuoted 
echo Комментарий надо взять в кавычки: [^"]
goto Exit


::коментарий закавычен -- работаем!
:Quoted
:: git pull -- надо выполнять перед началом работы, чтобы втянуть новости...
git fetch
git status
git add .
git commit -m %first% -a
git push origin Makarov
goto Exit

:ErrorMessage
echo ErrorMessage:
echo Необходимо передать однострочный комментарий для коммита:
echo         %~n0 Коментарий для коммита...

:Exit
echo .
