@echo off
rem Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
rem SPDX-License-Identifier: MIT
rem Launch Python without requiring a host installation. Keep this policy
rem aligned with ipcfg::python_runtime in ipcfg.tcl.
setlocal EnableExtensions EnableDelayedExpansion

if defined IPCFG_PYTHON (
    set "PYEXE=%IPCFG_PYTHON%"
    for %%D in ("%IPCFG_PYTHON%") do set "PYROOT=%%~dpD"
    if not exist "!PYROOT!DLLs" set "PYROOT="
    call :probe "%PYEXE%"
    if errorlevel 1 (
        echo PYTHON_RUNTIME_FAIL: IPCFG_PYTHON is not compatible: %IPCFG_PYTHON% 1>&2
        exit /b 2
    )
    goto run
)

set "VIVADO_ROOT=%IPCFG_VIVADO_ROOT%"
if not defined VIVADO_ROOT set "VIVADO_ROOT=%XILINX_VIVADO%"
if not defined VIVADO_ROOT if defined VIVADO_PATH (
    for %%D in ("%VIVADO_PATH%\..\..") do set "VIVADO_ROOT=%%~fD"
)

set "COUNT=0"
if defined VIVADO_ROOT (
    for /d %%D in ("%VIVADO_ROOT%\tps\win64\python-*") do (
        if exist "%%~fD\python.exe" (
            call :probe "%%~fD\python.exe"
            if not errorlevel 1 (
                set /a COUNT+=1
                set "PYEXE=%%~fD\python.exe"
                set "PYROOT=%%~fD"
            )
        )
        if exist "%%~fD\bin\python.exe" (
            call :probe "%%~fD\bin\python.exe"
            if not errorlevel 1 (
                set /a COUNT+=1
                set "PYEXE=%%~fD\bin\python.exe"
                set "PYROOT=%%~fD"
            )
        )
    )
)

if "%COUNT%"=="1" goto run
if not "%COUNT%"=="0" (
    echo PYTHON_RUNTIME_FAIL: multiple compatible Vivado Python bundles; set IPCFG_PYTHON 1>&2
    exit /b 2
)

if "%IPCFG_REQUIRE_BUNDLED%"=="1" (
    echo PYTHON_RUNTIME_FAIL: no compatible Vivado Python bundle; set IPCFG_VIVADO_ROOT to the installed Vivado root 1>&2
    exit /b 2
)

for %%P in (python3.exe python.exe) do (
    for /f "delims=" %%E in ('where %%P 2^>nul') do (
        call :probe "%%E"
        if not errorlevel 1 (
            set "PYEXE=%%E"
            set "PYROOT="
            goto run
        )
    )
)

echo PYTHON_RUNTIME_FAIL: no Python 3.10+; VIVADO_ROOT=%VIVADO_ROOT% 1>&2
exit /b 2

:probe
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONSTARTUP="
set "PYTHONINSPECT="
"%~1" -c "import argparse,base64,hashlib,json,pathlib,re,sys,typing;raise SystemExit(sys.version_info[:2] ^< (3,10))" >nul 2>&1
exit /b %ERRORLEVEL%

:run
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONSTARTUP="
set "PYTHONINSPECT="
if defined PYROOT set "PATH=%PYROOT%;%PYROOT%\DLLs;%PATH%"
"%PYEXE%" %*
exit /b %ERRORLEVEL%
