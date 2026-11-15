@echo off
rem Launch BADR 2 with the Python bundled in the QGIS / OSGeo4W install.
rem Adjust OSGEO4W_ROOT if QGIS is installed elsewhere.
if "%OSGEO4W_ROOT%"=="" set OSGEO4W_ROOT=C:\OSGeo4W
cd /d "%~dp0"
call "%OSGEO4W_ROOT%\bin\python-qgis.bat" -m badr %*
