@echo off
rem Launch BADR 2 from source with the Python bundled in QGIS / OSGeo4W.
rem Adjust OSGEO4W_ROOT if QGIS is installed elsewhere.
if "%OSGEO4W_ROOT%"=="" set OSGEO4W_ROOT=C:\OSGeo4W
if exist "%OSGEO4W_ROOT%\bin\python-qgis-ltr.bat" (
  call "%OSGEO4W_ROOT%\bin\python-qgis-ltr.bat" "%~dp0boot.py" %*
) else (
  call "%OSGEO4W_ROOT%\bin\python-qgis.bat" "%~dp0boot.py" %*
)
