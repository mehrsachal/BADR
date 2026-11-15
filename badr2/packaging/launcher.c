/* BADR.exe - native Windows launcher for BADR 2.
 *
 * BADR runs on the Python that ships with QGIS.  This launcher finds a QGIS
 * runtime (bundled next to the exe by the installer, or one already installed
 * on the PC), starts BADR with it without showing a console window, and if
 * start-up fails shows a message that points at the log file.
 *
 * Build:  x86_64-w64-mingw32-windres badr.rc -O coff -o badr.res
 *         x86_64-w64-mingw32-gcc -O2 -municode -mwindows launcher.c badr.res -o BADR.exe -lgdi32
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

/* ---- instant splash -------------------------------------------------------
 * Python + QGIS need a few seconds before they can draw anything, so the
 * launcher shows a small native window with a moving bar straight away.  The
 * application signals a named event once its own splash is on screen and this
 * one closes.  If the event never comes, it closes when the process exits or
 * after SPLASH_MAX_MS. */
#define SPLASH_W 440
#define SPLASH_H 170
#define SPLASH_MAX_MS 90000
static int g_tick = 0;

static LRESULT CALLBACK splash_proc(HWND w, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_TIMER:
        g_tick++;
        InvalidateRect(w, NULL, FALSE);
        return 0;
    case WM_ERASEBKGND:
        return 1;
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(w, &ps);
        RECT r;
        GetClientRect(w, &r);
        HBRUSH bg = CreateSolidBrush(RGB(0x1f, 0x20, 0x23));
        FillRect(dc, &r, bg);
        DeleteObject(bg);
        SetBkMode(dc, TRANSPARENT);
        HFONT title = CreateFontW(-34, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
        HFONT small = CreateFontW(-13, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
        HFONT old = (HFONT)SelectObject(dc, title);
        SetTextColor(dc, RGB(0xe4, 0xe5, 0xe8));
        TextOutW(dc, 28, 22, L"BADR", 4);
        SelectObject(dc, small);
        SetTextColor(dc, RGB(0x8f, 0x91, 0x99));
        TextOutW(dc, 30, 68, L"Planning and analysis", 21);
        TextOutW(dc, 30, 138, L"Starting", 8);
        /* indeterminate bar: a block sliding across a track */
        RECT track = { 28, 124, SPLASH_W - 28, 128 };
        HBRUSH tb = CreateSolidBrush(RGB(0x2a, 0x2b, 0x2f));
        FillRect(dc, &track, tb);
        DeleteObject(tb);
        int span = (track.right - track.left) + 90;
        int x = track.left - 90 + (g_tick * 9) % span;
        RECT block = { x < track.left ? track.left : x, track.top,
                       x + 90 > track.right ? track.right : x + 90, track.bottom };
        if (block.right > block.left) {
            HBRUSH ab = CreateSolidBrush(RGB(0x4a, 0x90, 0xe2));
            FillRect(dc, &block, ab);
            DeleteObject(ab);
        }
        SelectObject(dc, old);
        DeleteObject(title);
        DeleteObject(small);
        EndPaint(w, &ps);
        return 0;
    }
    }
    return DefWindowProcW(w, msg, wp, lp);
}

static HWND splash_open(HINSTANCE inst) {
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = splash_proc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursorW(NULL, (LPCWSTR)IDC_APPSTARTING);
    wc.lpszClassName = L"BadrSplash";
    RegisterClassW(&wc);
    int x = (GetSystemMetrics(SM_CXSCREEN) - SPLASH_W) / 2;
    int y = (GetSystemMetrics(SM_CYSCREEN) - SPLASH_H) / 2;
    HWND w = CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_TOPMOST, L"BadrSplash", L"BADR", WS_POPUP,
                             x, y, SPLASH_W, SPLASH_H, NULL, NULL, inst, NULL);
    if (w) {
        ShowWindow(w, SW_SHOWNOACTIVATE);
        UpdateWindow(w);
        SetTimer(w, 1, 33, NULL);
    }
    return w;
}

/* Animate the splash until the app says it is on screen, the process ends, or time runs out. */
static void splash_run(HWND w, HANDLE process, HANDLE ready) {
    HANDLE handles[2] = { process, ready };
    DWORD start = GetTickCount();
    for (;;) {
        DWORD r = MsgWaitForMultipleObjects(ready ? 2 : 1, handles, FALSE, 100, QS_ALLINPUT);
        if (r == WAIT_OBJECT_0 || r == WAIT_OBJECT_0 + 1) break;
        MSG m;
        while (PeekMessageW(&m, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&m);
            DispatchMessageW(&m);
        }
        if (GetTickCount() - start > SPLASH_MAX_MS) break;
    }
    if (w) DestroyWindow(w);
}


int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmdline, int show) {
    (void)prev; (void)cmdline; (void)show;
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

    /* splash + the event the application sets when its own window is up (skipped for scripted runs) */
    HWND splash = NULL;
    HANDLE ready = NULL;
    if (!GetEnvironmentVariableW(L"BADR_QUIET", probe, PATHLEN)) {
        wchar_t ev[64];
        _snwprintf(ev, 64, L"Local\\BADR2_Ready_%lu", (unsigned long)GetCurrentProcessId());
        ready = CreateEventW(NULL, TRUE, FALSE, ev);
        SetEnvironmentVariableW(L"BADR_SPLASH_EVENT", ev);
        splash = splash_open(inst);
    }

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
        if (splash) DestroyWindow(splash);
        MessageBoxW(NULL, msg, L"BADR", MB_OK | MB_ICONERROR);
        return 4;
    }
    if (splash) splash_run(splash, pi.hProcess, ready);
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
