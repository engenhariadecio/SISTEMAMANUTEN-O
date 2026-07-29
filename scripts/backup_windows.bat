@echo off
REM ═══════════════════════════════════════════════════════════════
REM  BACKUP DO SISTEMA DE MANUTENCAO — DECIO METALURGICA
REM
REM  Como usar:
REM   1. Instale o PostgreSQL (basta "Command Line Tools")
REM   2. Edite as duas linhas marcadas com >>> abaixo
REM   3. De dois cliques neste arquivo, ou agende no Agendador de Tarefas
REM ═══════════════════════════════════════════════════════════════

REM >>> Cole aqui a DATABASE_PUBLIC_URL do Railway (a que tem proxy.rlwy.net)
set URL=postgresql://postgres:SENHA@xxxxx.proxy.rlwy.net:12345/railway

REM >>> Pasta onde os backups serao guardados
set DESTINO=C:\Backups\Manutencao

REM >>> Caminho do pg_dump (ajuste o numero da versao se for diferente)
set PGDUMP=C:\Program Files\PostgreSQL\17\bin\pg_dump.exe

REM ── Nome do arquivo com a data: backup_2026-07-29.dump ──
for /f "tokens=1-3 delims=/" %%a in ("%date%") do set HOJE=%%c-%%b-%%a
set ARQUIVO=%DESTINO%\backup_%HOJE%.dump

if not exist "%DESTINO%" mkdir "%DESTINO%"

echo.
echo  Gerando backup em %ARQUIVO%
echo.

"%PGDUMP%" "%URL%" -Fc -f "%ARQUIVO%"

if errorlevel 1 (
  echo.
  echo  ERRO: o backup NAO foi gerado. Confira a URL e o caminho do pg_dump.
  echo.
  pause
  exit /b 1
)

echo.
echo  Backup concluido com sucesso.
for %%A in ("%ARQUIVO%") do echo  Tamanho: %%~zA bytes
echo.

REM ── Apaga backups com mais de 60 dias ──
forfiles /p "%DESTINO%" /m *.dump /d -60 /c "cmd /c del @path" 2>nul

echo  Backups com mais de 60 dias foram removidos.
echo.
pause
