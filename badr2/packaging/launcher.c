/* BADR.exe - native Windows launcher for BADR 2.
 *
 * BADR runs on the Python that ships with QGIS.  This launcher finds a QGIS
 * runtime (bundled next to the exe by the installer, or one already installed
 * on the PC), starts BADR with it without showing a console window, and if
 * start-up fails shows a message that points at the log file.
 *
 * Build:  x86_64-w64-mingw32-windres badr.rc -O coff -o badr.res
 *         x86_64-w64-mingw32-gcc -O2 -municode -mwindows launcher.c badr.res -o BADR.exe
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>

#define PATHLEN 1024
#define CMDLEN 8192

static int is_file(const wchar_t *p) {
    DWORD a = GetFileAttributesW(p);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static int is_dir(const wchar_t *p) {
    DWORD a = GetFileAttributesW(p);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

/* <root>\bin\python-qgis-ltr.bat or python-qgis.bat -> out; returns 1 if found */
static int bat_in(const wchar_t *root, wchar_t *out) {
    static const wchar_t *names[] = { L"python-qgis-ltr.bat", L"python-qgis.bat" };
    for (int i = 0; i < 2; i++) {
        _snwprintf(out, PATHLEN, L"%ls\\bin\\%ls", root, names[i]);
        out[PATHLEN - 1] = 0;
        if (is_file(out)) return 1;
    }
    return 0;
}

/* newest "QGIS *" folder under a Program Files directory */
static int bat_in_program_files(const wchar_t *env_name, wchar_t *out) {
    wchar_t base[PATHLEN], pattern[PATHLEN], root[PATHLEN], best[PATHLEN] = L"";
    if (!GetEnvironmentVariableW(env_name, base, PATHLEN)) return 0;
    _snwprintf(pattern, PATHLEN, L"%ls\\QGIS *", base);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && wcscmp(fd.cFileName, best) > 0) {
            _snwprintf(root, PATHLEN, L"%ls\\%ls", base, fd.cFileName);
            wchar_t probe[PATHLEN];
            if (bat_in(root, probe)) wcsncpy(best, fd.cFileName, PATHLEN - 1);
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    if (!best[0]) return 0;
    _snwprintf(root, PATHLEN, L"%ls\\%ls", base, best);
    return bat_in(root, out);
}

static int find_runtime(const wchar_t *exe_dir, wchar_t *out) {
    wchar_t root[PATHLEN];
    _snwprintf(root, PATHLEN, L"%ls\\qgis", exe_dir);          /* bundled by the installer */
    if (bat_in(root, out)) return 1;
    static const wchar_t *envs[] = { L"BADR_QGIS_ROOT", L"OSGEO4W_ROOT" };
    for (int i = 0; i < 2; i++)
        if (GetEnvironmentVariableW(envs[i], root, PATHLEN) && bat_in(root, out)) return 1;
    if (bat_in_program_files(L"ProgramW6432", out)) return 1;
    if (bat_in_program_files(L"ProgramFiles", out)) return 1;
    static const wchar_t *fixed[] = { L"C:\\OSGeo4W", L"C:\\OSGeo4W64" };
    for (int i = 0; i < 2; i++)
        if (bat_in(fixed[i], out)) return 1;
    return 0;
}

/* the part of the command line after the program name */
static const wchar_t *arguments(void) {
    const wchar_t *p = GetCommandLineW();
    if (*p == L'"') { p++; while (*p && *p != L'"') p++; if (*p) p++; }
    else while (*p && *p != L' ' && *p != L'\t') p++;
    while (*p == L' ' || *p == L'\t') p++;
    return p;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmdline, int show) {
    (void)inst; (void)prev; (void)cmdline; (void)show;
    static wchar_t exe_dir[PATHLEN], app_dir[PATHLEN], probe[PATHLEN], bat[PATHLEN];
    static wchar_t log_dir[PATHLEN], log_path[PATHLEN], cmd[CMDLEN], msg[CMDLEN];

    GetModuleFileNameW(NULL, exe_dir, PATHLEN);
    wchar_t *slash = wcsrchr(exe_dir, L'\\');
    if (slash) *slash = 0;

    /* boot.py and the "badr" package sit in <exe>\app (installer) or beside the exe (zip) */
    _snwprintf(app_dir, PATHLEN, L"%ls\\app", exe_dir);
    _snwprintf(probe, PATHLEN, L"%ls\\boot.py", app_dir);
    if (!is_file(probe)) {
        wcsncpy(app_dir, exe_dir, PATHLEN - 1);
        _snwprintf(probe, PATHLEN, L"%ls\\boot.py", app_dir);
        if (!is_file(probe)) {
            MessageBoxW(NULL, L"BADR's program files are missing next to BADR.exe.\n\n"
                        L"Reinstall BADR, or keep BADR.exe in the same folder as the 'badr' folder.",
                        L"BADR", MB_OK | MB_ICONERROR);
            return 2;
        }
    }

    if (!find_runtime(exe_dir, bat)) {
        MessageBoxW(NULL, L"BADR needs the QGIS runtime and could not find one.\n\n"
                    L"Either install BADR with the full installer (QGIS is included), or install "
                    L"QGIS 3.34 or newer from qgis.org and start BADR again.\n\n"
                    L"If QGIS is installed in an unusual place, set the environment variable "
                    L"BADR_QGIS_ROOT to its folder.", L"BADR", MB_OK | MB_ICONERROR);
        return 3;
    }

    SetEnvironmentVariableW(L"PYTHONUNBUFFERED", L"1");
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");

    /* log: %LOCALAPPDATA%\BADR\badr.log */
    HANDLE log = INVALID_HANDLE_VALUE;
    if (GetEnvironmentVariableW(L"LOCALAPPDATA", log_dir, PATHLEN)) {
        wcsncat(log_dir, L"\\BADR", PATHLEN - wcslen(log_dir) - 1);
        CreateDirectoryW(log_dir, NULL);
        _snwprintf(log_path, PATHLEN, L"%ls\\badr.log", log_dir);
        SECURITY_ATTRIBUTES sa = { sizeof(sa), NULL, TRUE };
        log = CreateFileW(log_path, GENERIC_WRITE, FILE_SHARE_READ, &sa, CREATE_ALWAYS,
                          FILE_ATTRIBUTE_NORMAL, NULL);
    }

    /* cmd /s /c ""<bat>" "<app>\boot.py" <args>"
     * The QGIS .bat sets up PATH, GDAL, Qt and Python.  It also resets PYTHONPATH, so BADR is
     * started by script path (boot.py adds the app folder and bundled packages itself). */
    _snwprintf(cmd, CMDLEN, L"cmd.exe /d /s /c \"\"%ls\" \"%ls\\boot.py\" %ls\"", bat, app_dir, arguments());
    cmd[CMDLEN - 1] = 0;

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    if (log != INVALID_HANDLE_VALUE) {
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdOutput = log;
        si.hStdError = log;
        si.hStdInput = NULL;
    }
    if (!CreateProcessW(NULL, cmd, NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, app_dir, &si, &pi)) {
        _snwprintf(msg, CMDLEN, L"Could not start the QGIS Python runtime:\n%ls", bat);
        MessageBoxW(NULL, msg, L"BADR", MB_OK | MB_ICONERROR);
        return 4;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    if (log != INVALID_HANDLE_VALUE) CloseHandle(log);
    if (code != 0 && !GetEnvironmentVariableW(L"BADR_QUIET", probe, PATHLEN)) {
        _snwprintf(msg, CMDLEN, L"BADR stopped with an error (code %lu).\n\nDetails are in:\n%ls",
                   (unsigned long)code, log_path);
        MessageBoxW(NULL, msg, L"BADR", MB_OK | MB_ICONERROR);
    }
    return (int)code;
}
