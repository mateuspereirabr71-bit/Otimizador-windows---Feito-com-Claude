# -*- coding: utf-8 -*-
"""
WinBoost - Otimizador completo para Windows 10/11
Interface gráfica (tkinter) - sem dependências externas.

Recursos:
  * Painel com CPU / RAM / Disco em tempo real
  * Limpeza profunda (temporários, caches, dumps, lixeira, navegadores...)
  * Ajustes de desempenho (plano de energia, efeitos visuais, Game Mode, MMCSS...)
  * Privacidade / telemetria
  * Serviços desnecessários
  * Gerenciador de inicialização
  * Remoção de bloatware (apps pré-instalados)
  * Manutenção (SFC, DISM, CHKDSK, TRIM, rede, ícones, Windows Update)
  * Ponto de restauração + backup de TUDO que for alterado + botão "Desfazer"
"""
import os
import sys
import re
import json
import glob
import time
import ctypes
import shutil
import tempfile
import threading
import queue
import subprocess
import datetime
import tkinter as tk
from tkinter import ttk, messagebox

IS_WIN = os.name == "nt"
if IS_WIN:
    import winreg
    from ctypes import wintypes

APP_NAME = "WinBoost"
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
BACKUP_FILE = os.path.join(APP_DIR, "backup.json")
os.makedirs(APP_DIR, exist_ok=True)

# ----------------------------------------------------------------------------
# Tema
# ----------------------------------------------------------------------------
BG, SIDE, CARD, CARD2 = "#12141a", "#0c0e13", "#1b1e27", "#252a37"
ACCENT, ACCENT2 = "#4f8cff", "#3a75e6"
FG, MUTED, OK, WARN, ERR = "#e6e9f0", "#8b93a7", "#3ddc97", "#ffb454", "#ff6b6b"
FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI", 10, "bold")
FONT_S = ("Segoe UI", 9)
FONT_H = ("Segoe UI", 18, "bold")
FONT_BIG = ("Segoe UI", 22, "bold")

# ----------------------------------------------------------------------------
# Utilidades de sistema
# ----------------------------------------------------------------------------
CREATE_NO_WINDOW = 0x08000000


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Reabre o programa pedindo elevação (UAC). Retorna True se o pedido foi aceito."""
    if getattr(sys, "frozen", False):
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        params = " ".join(f'"{a}"' for a in sys.argv)
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return ret > 32


def _oem():
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "utf-8"


OEM = _oem() if IS_WIN else "utf-8"


def run(cmd, timeout=600):
    """Executa comando (str => shell, list => direto). Retorna (codigo, saida)."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, encoding=OEM, errors="replace",
            timeout=timeout, shell=isinstance(cmd, str),
            creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


def ps(script, timeout=900):
    return run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout)


def run_stream(cmd, log):
    """Executa e envia a saída linha a linha para o log."""
    try:
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding=OEM, errors="replace", shell=True, creationflags=CREATE_NO_WINDOW)
        for line in p.stdout:
            line = line.replace("\x00", "").strip()
            if line:
                log("   " + line)
        p.wait()
        return p.returncode
    except Exception as e:
        log(f"   [erro] {e}")
        return 1


def fmt_size(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


# ----------------------------------------------------------------------------
# Registro do Windows (com backup automático do valor original)
# ----------------------------------------------------------------------------
if IS_WIN:
    HIVE = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    KIND = {"DWORD": winreg.REG_DWORD, "SZ": winreg.REG_SZ, "BIN": winreg.REG_BINARY}
    W64 = winreg.KEY_WOW64_64KEY


def reg_read(hive, path, name):
    try:
        with winreg.OpenKey(HIVE[hive], path, 0, winreg.KEY_READ | W64) as k:
            return winreg.QueryValueEx(k, name)
    except OSError:
        return None


class Backup:
    """Guarda o estado ORIGINAL de tudo que o app altera (só a 1ª alteração é gravada)."""

    def __init__(self):
        self.d = {"registry": [], "services": {}, "undo_cmds": []}
        try:
            with open(BACKUP_FILE, encoding="utf-8") as f:
                self.d.update(json.load(f))
        except Exception:
            pass

    def save(self):
        try:
            with open(BACKUP_FILE, "w", encoding="utf-8") as f:
                json.dump(self.d, f, indent=1, ensure_ascii=False)
        except Exception:
            pass

    def record_reg(self, hive, path, name, old):
        key = f"{hive}|{path}|{name}".lower()
        if any(r["id"] == key for r in self.d["registry"]):
            return
        rec = {"id": key, "hive": hive, "path": path, "name": name}
        if old is None:
            rec["existed"] = False
        else:
            val, typ = old
            if isinstance(val, (bytes, bytearray)):
                val = bytes(val).hex()
            rec.update(existed=True, type=typ, value=val)
        self.d["registry"].append(rec)

    def add_undo(self, cmd):
        if cmd not in self.d["undo_cmds"]:
            self.d["undo_cmds"].append(cmd)

    def has_data(self):
        return bool(self.d["registry"] or self.d["services"] or self.d["undo_cmds"])

    def restore_all(self, log):
        for r in reversed(self.d["registry"]):
            try:
                if r["existed"]:
                    val = r["value"]
                    if r["type"] == winreg.REG_BINARY:
                        val = bytes.fromhex(val)
                    with winreg.CreateKeyEx(HIVE[r["hive"]], r["path"], 0, winreg.KEY_SET_VALUE | W64) as k:
                        winreg.SetValueEx(k, r["name"], 0, r["type"], val)
                else:
                    try:
                        with winreg.OpenKey(HIVE[r["hive"]], r["path"], 0, winreg.KEY_SET_VALUE | W64) as k:
                            winreg.DeleteValue(k, r["name"])
                    except OSError:
                        pass
            except Exception as e:
                log(f"   falha ao restaurar {r['name']}: {e}")
        for name, mode in self.d["services"].items():
            run(f"sc config {name} start= {mode}")
            log(f"   serviço {name} -> {mode}")
        for cmd in reversed(self.d["undo_cmds"]):
            run(cmd)
        self.d = {"registry": [], "services": {}, "undo_cmds": []}
        self.save()


def reg_set(bk, hive, path, name, value, kind="DWORD"):
    bk.record_reg(hive, path, name, reg_read(hive, path, name))
    if kind == "BIN":
        value = bytes.fromhex(value)
    with winreg.CreateKeyEx(HIVE[hive], path, 0, winreg.KEY_SET_VALUE | W64) as k:
        winreg.SetValueEx(k, name, 0, KIND[kind], value)


# ----------------------------------------------------------------------------
# Métricas do sistema (sem psutil)
# ----------------------------------------------------------------------------
if IS_WIN:
    class MEMSTAT(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def mem_status():
    m = MEMSTAT()
    m.dwLength = ctypes.sizeof(MEMSTAT)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.dwMemoryLoad, m.ullTotalPhys, m.ullAvailPhys


def cpu_times():
    idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
    f = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime
    return f(idle), f(kern) + f(user)


def uptime_text():
    k = ctypes.windll.kernel32
    k.GetTickCount64.restype = ctypes.c_ulonglong
    s = k.GetTickCount64() // 1000
    return f"{s // 86400}d {s % 86400 // 3600}h {s % 3600 // 60}min"


def trim_memory():
    """Esvazia o working set dos processos (libera RAM em cache; efeito temporário)."""
    psapi, k32 = ctypes.windll.psapi, ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    arr = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed))
    count = 0
    for pid in arr[: needed.value // 4]:
        h = k32.OpenProcess(0x0100 | 0x0400, False, pid)  # SET_QUOTA | QUERY_INFORMATION
        if h:
            if psapi.EmptyWorkingSet(h):
                count += 1
            k32.CloseHandle(h)
    return count


def system_info():
    cpu = (reg_read("HKLM", r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") or ("?",))[0]
    prod = (reg_read("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName") or ("Windows",))[0]
    ver = (reg_read("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DisplayVersion") or ("",))[0]
    build = (reg_read("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuild") or ("",))[0]
    if build.isdigit() and int(build) >= 22000:
        prod = prod.replace("Windows 10", "Windows 11")
    return cpu.strip(), f"{prod} {ver} (build {build})"


# ----------------------------------------------------------------------------
# Limpeza
# ----------------------------------------------------------------------------
def _protected(p):
    """Nunca apaga a pasta temporária do próprio executável (PyInstaller)."""
    mp = getattr(sys, "_MEIPASS", None)
    return bool(mp) and os.path.abspath(p).lower().startswith(os.path.abspath(mp).lower())


def path_size(patterns):
    total = 0
    for pat in patterns:
        for p in glob.glob(pat):
            if _protected(p):
                continue
            try:
                if os.path.isfile(p):
                    total += os.path.getsize(p)
                    continue
                for root, dirs, files in os.walk(p):
                    if _protected(root):
                        dirs[:] = []
                        continue
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
            except OSError:
                pass
    return total


def delete_contents(patterns):
    freed = failed = 0
    for pat in patterns:
        for p in glob.glob(pat):
            if _protected(p):
                continue
            if os.path.isfile(p) or os.path.islink(p):
                try:
                    s = os.path.getsize(p)
                    os.remove(p)
                    freed += s
                except OSError:
                    failed += 1
            elif os.path.isdir(p):
                for root, dirs, files in os.walk(p, topdown=False):
                    if _protected(root):
                        continue
                    for f in files:
                        fp = os.path.join(root, f)
                        if _protected(fp):
                            continue
                        try:
                            s = os.path.getsize(fp)
                            os.remove(fp)
                            freed += s
                        except OSError:
                            failed += 1
                    if root != p:
                        try:
                            os.rmdir(root)
                        except OSError:
                            pass
    return freed, failed


def build_clean_targets():
    e = os.environ
    local, win, pdata = e.get("LOCALAPPDATA", ""), e.get("WINDIR", r"C:\Windows"), e.get("PROGRAMDATA", r"C:\ProgramData")
    J = os.path.join

    def chromium(base):
        b = J(base, "User Data", "*")
        return [J(b, "Cache"), J(b, "Code Cache"), J(b, "GPUCache"), J(b, "Service Worker", "CacheStorage")]

    return [
        # (id, nome, descrição, padrão, [caminhos])
        ("user_temp", "Arquivos temporários do usuário", "Pasta %TEMP%", True, [tempfile.gettempdir()]),
        ("win_temp", "Temporários do Windows", r"C:\Windows\Temp", True, [J(win, "Temp")]),
        ("wu_cache", "Cache de download do Windows Update", "Atualizações já instaladas (para o serviço brevemente)", True,
         [J(win, "SoftwareDistribution", "Download")]),
        ("do_cache", "Cache de Otimização de Entrega", "Arquivos de updates P2P", True,
         [J(win, "ServiceProfiles", "NetworkService", "AppData", "Local", "Microsoft", "Windows",
            "DeliveryOptimization", "Cache")]),
        ("thumbs", "Cache de miniaturas", "Reconstruído automaticamente pelo Explorer", True,
         [J(local, "Microsoft", "Windows", "Explorer", "thumbcache_*.db")]),
        ("wer", "Relatórios de erro do Windows", "Windows Error Reporting", True,
         [J(pdata, "Microsoft", "Windows", "WER"), J(local, "Microsoft", "Windows", "WER")]),
        ("dumps", "Dumps de falha (travamentos)", "CrashDumps, Minidump, MEMORY.DMP", True,
         [J(local, "CrashDumps"), J(win, "Minidump"), J(win, "MEMORY.DMP"), J(win, "LiveKernelReports")]),
        ("logs", "Logs antigos do sistema", "CBS e DISM", True,
         [J(win, "Logs", "CBS", "*.log"), J(win, "Logs", "DISM", "*.log")]),
        ("chrome", "Cache do Google Chrome", "Feche o navegador antes", True, chromium(J(local, "Google", "Chrome"))),
        ("edge", "Cache do Microsoft Edge", "Feche o navegador antes", True, chromium(J(local, "Microsoft", "Edge"))),
        ("brave", "Cache do Brave", "Feche o navegador antes", True, chromium(J(local, "BraveSoftware", "Brave-Browser"))),
        ("firefox", "Cache do Firefox", "Feche o navegador antes", True,
         [J(local, "Mozilla", "Firefox", "Profiles", "*", "cache2")]),
        ("shader", "Cache de shaders (DirectX / NVIDIA / AMD)", "Jogos podem recompilar shaders na 1ª abertura", False,
         [J(local, "D3DSCache"), J(local, "NVIDIA", "DXCache"), J(local, "NVIDIA", "GLCache"), J(local, "AMD", "DxCache")]),
        ("recycle", "Esvaziar a Lixeira", "Apaga definitivamente os itens da Lixeira", False, []),
    ]


# ----------------------------------------------------------------------------
# Ajustes (registro / comandos)
# ----------------------------------------------------------------------------
CDM = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
MMCSS = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"

TWEAKS = [
    # ---------------- Desempenho ----------------
    dict(id="power", group="perf", name="Plano de energia: Desempenho Máximo",
         desc="Ativa o plano 'Desempenho Máximo' (ou Alto Desempenho). Em notebook aumenta consumo de bateria.",
         default=True, func="power_plan"),
    dict(id="vfx", group="perf", name="Efeitos visuais para desempenho",
         desc="Desliga animações de janelas, menus e sombras (mantém suavização de fontes). Aplica após sair/entrar na conta.",
         default=True, regs=[
             ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting", 2, "DWORD"),
             ("HKCU", r"Control Panel\Desktop", "UserPreferencesMask", "9012038010000000", "BIN"),
             ("HKCU", r"Control Panel\Desktop", "MenuShowDelay", "0", "SZ"),
             ("HKCU", r"Control Panel\Desktop\WindowMetrics", "MinAnimate", "0", "SZ"),
             ("HKCU", ADV, "TaskbarAnimations", 0, "DWORD"),
             ("HKCU", ADV, "ListviewAlphaSelect", 0, "DWORD"),
             ("HKCU", ADV, "ListviewShadow", 0, "DWORD"),
             ("HKCU", r"Software\Microsoft\Windows\DWM", "EnableAeroPeek", 0, "DWORD")]),
    dict(id="transp", group="perf", name="Desativar efeitos de transparência",
         desc="Remove o desfoque/transparência de menus e barra de tarefas (menos uso de GPU).",
         default=True, regs=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", 0, "DWORD")]),
    dict(id="gamemode", group="perf", name="Ativar Modo de Jogo",
         desc="Prioriza o jogo em primeiro plano e reduz interrupções do Windows.",
         default=True, regs=[("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1, "DWORD"),
                             ("HKCU", r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1, "DWORD")]),
    dict(id="gamedvr", group="perf", name="Desativar Game DVR / gravação em segundo plano",
         desc="Evita que o Windows grave clipes em segundo plano (ganha FPS).",
         default=True, regs=[("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", 0, "DWORD"),
                             ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0, "DWORD"),
                             ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR", 0, "DWORD")]),
    dict(id="hags", group="perf", name="Agendamento de GPU acelerado por hardware (HAGS)",
         desc="Reduz latência em GPUs compatíveis (GTX 10+/RX 5000+). Requer reiniciar.",
         default=False, regs=[("HKLM", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 2, "DWORD")]),
    dict(id="mmcss", group="perf", name="Prioridade multimídia / jogos (MMCSS)",
         desc="Dá mais prioridade de CPU/GPU a jogos e remove o limitador de rede do Windows.",
         default=True, regs=[
             ("HKLM", MMCSS, "SystemResponsiveness", 0, "DWORD"),
             ("HKLM", MMCSS, "NetworkThrottlingIndex", 4294967295, "DWORD"),
             ("HKLM", MMCSS + r"\Tasks\Games", "GPU Priority", 8, "DWORD"),
             ("HKLM", MMCSS + r"\Tasks\Games", "Priority", 6, "DWORD"),
             ("HKLM", MMCSS + r"\Tasks\Games", "Scheduling Category", "High", "SZ"),
             ("HKLM", MMCSS + r"\Tasks\Games", "SFIO Priority", "High", "SZ")]),
    dict(id="prio", group="perf", name="Priorizar o programa em primeiro plano",
         desc="Ajusta o agendador de CPU para favorecer o app que você está usando.",
         default=True, regs=[("HKLM", r"SYSTEM\CurrentControlSet\Control\PriorityControl", "Win32PrioritySeparation", 38, "DWORD")]),
    dict(id="startdelay", group="perf", name="Remover atraso de inicialização dos programas",
         desc="Programas de inicialização abrem sem a espera artificial do Windows.",
         default=True, regs=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize", "StartupDelayInMSec", 0, "DWORD")]),
    dict(id="bgapps", group="perf", name="Bloquear apps em segundo plano",
         desc="Impede apps da Store de rodarem escondidos consumindo CPU/RAM/rede.",
         default=True, regs=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications", "GlobalUserDisabled", 1, "DWORD"),
                             ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\AppPrivacy", "LetAppsRunInBackground", 2, "DWORD")]),
    dict(id="fastshut", group="perf", name="Desligamento mais rápido",
         desc="Reduz o tempo de espera para encerrar serviços e apps travados (2 s).",
         default=True, regs=[("HKLM", r"SYSTEM\CurrentControlSet\Control", "WaitToKillServiceTimeout", "2000", "SZ"),
                             ("HKCU", r"Control Panel\Desktop", "WaitToKillAppTimeout", "2000", "SZ"),
                             ("HKCU", r"Control Panel\Desktop", "HungAppTimeout", "2000", "SZ"),
                             ("HKCU", r"Control Panel\Desktop", "AutoEndTasks", "1", "SZ")]),
    dict(id="lastaccess", group="perf", name="NTFS: parar de gravar 'último acesso'",
         desc="Menos escritas em disco a cada arquivo lido.",
         default=True, cmds=["fsutil behavior set disablelastaccess 1"], undo=["fsutil behavior set disablelastaccess 2"]),
    dict(id="hibernate", group="perf", name="Desativar hibernação (libera GBs em disco)",
         desc="Apaga o hiberfil.sys (tamanho ~40% da RAM). Também desliga a Inicialização Rápida.",
         default=False, cmds=["powercfg /h off"], undo=["powercfg /h on"]),
    dict(id="mouse", group="perf", name="Mouse sem aceleração (precisão 1:1)",
         desc="Desativa 'Aprimorar precisão do ponteiro'. Útil para jogos de tiro.",
         default=False, regs=[("HKCU", r"Control Panel\Mouse", "MouseSpeed", "0", "SZ"),
                              ("HKCU", r"Control Panel\Mouse", "MouseThreshold1", "0", "SZ"),
                              ("HKCU", r"Control Panel\Mouse", "MouseThreshold2", "0", "SZ")]),
    # ---------------- Rede ----------------
    dict(id="flushdns", group="net", name="Limpar cache DNS",
         desc="Resolve problemas de sites que não abrem e renova resoluções antigas.",
         default=True, cmds=["ipconfig /flushdns"]),
    dict(id="dns", group="net", name="Usar DNS rápido (Cloudflare 1.1.1.1)",
         desc="Troca o DNS dos adaptadores ativos. Pode melhorar a velocidade de abertura de sites.",
         default=False,
         cmds=['powershell -NoProfile -Command "Get-NetAdapter | Where-Object Status -eq \'Up\' | '
               'Set-DnsClientServerAddress -ServerAddresses (\'1.1.1.1\',\'1.0.0.1\')"'],
         undo=['powershell -NoProfile -Command "Get-NetAdapter | Where-Object Status -eq \'Up\' | '
               'Set-DnsClientServerAddress -ResetServerAddresses"']),
    # ---------------- Privacidade ----------------
    dict(id="telemetry", group="priv", name="Reduzir telemetria do Windows",
         desc="Envia o mínimo possível de dados de diagnóstico à Microsoft.",
         default=True, regs=[("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", 0, "DWORD"),
                             ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "DoNotShowFeedbackNotifications", 1, "DWORD")]),
    dict(id="adid", group="priv", name="Desativar ID de publicidade",
         desc="Impede anúncios personalizados baseados no seu uso.",
         default=True, regs=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", 0, "DWORD")]),
    dict(id="tips", group="priv", name="Remover sugestões, dicas e apps patrocinados",
         desc="Para de instalar jogos/apps sugeridos e mostrar propaganda no Iniciar e na tela de bloqueio.",
         default=True, regs=[("HKCU", CDM, n, 0, "DWORD") for n in (
             "ContentDeliveryAllowed", "OemPreInstalledAppsEnabled", "PreInstalledAppsEnabled",
             "SilentInstalledAppsEnabled", "SystemPaneSuggestionsEnabled", "SoftLandingEnabled",
             "SubscribedContent-310093Enabled", "SubscribedContent-338387Enabled", "SubscribedContent-338388Enabled",
             "SubscribedContent-338389Enabled", "SubscribedContent-353694Enabled", "SubscribedContent-353696Enabled")]),
    dict(id="activity", group="priv", name="Desativar histórico de atividades",
         desc="O Windows deixa de registrar/sincronizar o que você abre.",
         default=True, regs=[("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "EnableActivityFeed", 0, "DWORD"),
                             ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "PublishUserActivities", 0, "DWORD"),
                             ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "UploadUserActivities", 0, "DWORD")]),
    dict(id="bing", group="priv", name="Pesquisa do Iniciar sem Bing/web",
         desc="A busca do Windows fica mais rápida e só procura no seu PC.",
         default=True, regs=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled", 0, "DWORD"),
                             ("HKCU", r"Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", 1, "DWORD")]),
]

# (id, nome, descrição, [serviços], modo, padrão)
SERVICES = [
    ("diagtrack", "Telemetria (DiagTrack)", "Experiências do Usuário Conectado e Telemetria.", ["DiagTrack"], "disabled", True),
    ("dmwap", "Push de mensagens WAP (dmwappushservice)", "Usado só para telemetria.", ["dmwappushservice"], "disabled", True),
    ("diaghub", "Coletor do Diagnostics Hub", "Coleta de diagnósticos do Visual Studio/Windows.", ["diagnosticshub.standardcollector.service"], "disabled", True),
    ("wer", "Relatório de erros do Windows", "Envia relatórios de falhas.", ["WerSvc"], "disabled", True),
    ("maps", "Mapas baixados", "Atualização de mapas offline.", ["MapsBroker"], "disabled", True),
    ("fax", "Fax", "Obsoleto.", ["Fax"], "disabled", True),
    ("retail", "Modo demonstração de loja", "Só para vitrines de lojas.", ["RetailDemo"], "disabled", True),
    ("remreg", "Registro remoto", "Permite alterar o registro pela rede (risco de segurança).", ["RemoteRegistry"], "disabled", True),
    ("geo", "Serviço de geolocalização", "Desative se não usa localização.", ["lfsvc"], "disabled", False),
    ("sysmain", "SysMain (Superfetch)", "Recomendado desativar apenas em SSD; em HDD pode piorar.", ["SysMain"], "disabled", False),
    ("wsearch", "Indexação de pesquisa (WSearch)", "Libera disco/CPU, mas a busca de arquivos fica mais lenta.", ["WSearch"], "demand", False),
    ("xbox", "Serviços Xbox", "Só desative se NÃO usa Xbox/Game Pass/Microsoft Store Games.",
     ["XblAuthManager", "XblGameSave", "XboxNetApiSvc", "XboxGipSvc"], "disabled", False),
    ("spooler", "Spooler de impressão", "Desative se você não usa impressora (nem PDF virtual).", ["Spooler"], "disabled", False),
]

BLOAT = [
    ("Microsoft.BingNews", "Notícias (Bing)", True), ("Microsoft.BingWeather", "Clima", True),
    ("Microsoft.GetHelp", "Obter Ajuda", True), ("Microsoft.Getstarted", "Dicas", True),
    ("Microsoft.MicrosoftOfficeHub", "Office (atalho)", True),
    ("Microsoft.MicrosoftSolitaireCollection", "Solitaire Collection", True),
    ("Microsoft.MixedReality.Portal", "Portal de Realidade Misturada", True),
    ("Microsoft.People", "Pessoas", True), ("Microsoft.SkypeApp", "Skype", True),
    ("Microsoft.WindowsFeedbackHub", "Central de Feedback", True),
    ("Microsoft.ZuneMusic", "Groove Música / Media Player antigo", True),
    ("Microsoft.ZuneVideo", "Filmes e TV", True), ("Clipchamp.Clipchamp", "Clipchamp", True),
    ("Microsoft.549981C3F5F10", "Cortana", True), ("Microsoft.OneConnect", "Planos móveis", True),
    ("Microsoft.Microsoft3DViewer", "Visualizador 3D", True), ("Microsoft.Print3D", "Print 3D", True),
    ("Microsoft.Wallet", "Microsoft Pay", True), ("king.com.*", "Candy Crush e jogos King", True),
    ("Microsoft.YourPhone", "Vínculo com o Celular", False), ("Microsoft.WindowsMaps", "Mapas", False),
    ("MicrosoftTeams", "Teams (versão pessoal)", False),
]

TOOLS = [
    ("Verificar arquivos do sistema (SFC)", "Procura e repara arquivos corrompidos do Windows.", "sfc /scannow"),
    ("Reparar imagem do Windows (DISM)", "Corrige a imagem do sistema (precisa de internet).", "DISM /Online /Cleanup-Image /RestoreHealth"),
    ("Limpar componentes antigos (DISM)", "Remove versões antigas de componentes do WinSxS.", "DISM /Online /Cleanup-Image /StartComponentCleanup"),
    ("Verificar disco (CHKDSK online)", "Procura erros no sistema de arquivos sem reiniciar.", "chkdsk %SystemDrive% /scan"),
    ("Otimizar unidade (TRIM / desfragmentar)", "TRIM em SSD, desfragmentação em HDD (automático).", "defrag %SystemDrive% /O /V"),
    ("Reiniciar pilha de rede", "Reseta Winsock/IP e DNS. Reinicie o PC depois.",
     "netsh winsock reset & netsh int ip reset & ipconfig /flushdns"),
    ("Reparar Windows Update", "Reseta os componentes do Update (renomeia caches).",
     "net stop wuauserv & net stop bits & net stop cryptsvc & "
     "ren %WINDIR%\\SoftwareDistribution SoftwareDistribution.old & "
     "ren %WINDIR%\\System32\\catroot2 catroot2.old & "
     "net start cryptsvc & net start bits & net start wuauserv"),
    ("Reconstruir cache de ícones", "Corrige ícones em branco/errados (reinicia o Explorer).", "@icons"),
]


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
def mkbtn(parent, text, cmd, accent=False):
    return tk.Button(parent, text=text, command=cmd, bg=ACCENT if accent else CARD2, fg="white",
                     activebackground=ACCENT2 if accent else "#2f3544", activeforeground="white",
                     relief="flat", bd=0, padx=14, pady=8, font=FONT_B, cursor="hand2")


class ScrollFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")


def build_checks(parent, items):
    """items: (id, nome, descrição, padrão). Retorna (vars, labels_direita)."""
    vars_, rights = {}, {}
    for id_, name, desc, default in items:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=6, pady=3)
        row.columnconfigure(0, weight=1)
        v = tk.BooleanVar(value=default)
        tk.Checkbutton(row, text=name, variable=v, bg=CARD, fg=FG, selectcolor=BG, activebackground=CARD,
                       activeforeground=FG, font=FONT_B, anchor="w", bd=0, highlightthickness=0
                       ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 0))
        tk.Label(row, text=desc, bg=CARD, fg=MUTED, font=FONT_S, wraplength=620, justify="left", anchor="w"
                 ).grid(row=1, column=0, sticky="w", padx=34, pady=(0, 8))
        r = tk.Label(row, text="", bg=CARD, fg=OK, font=FONT_B)
        r.grid(row=0, column=1, rowspan=2, padx=12)
        vars_[id_], rights[id_] = v, r
    return vars_, rights


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} - Otimizador de Windows")
        self.geometry("1060x760")
        self.minsize(940, 640)
        self.configure(bg=BG)
        self.bk = Backup()
        self.q = queue.Queue()
        self.busy = False
        self.pages, self.navbtns, self.scrolls = {}, {}, {}
        self.cur = None
        self.clean = build_clean_targets()
        self._last_cpu = cpu_times()
        self._style()
        self._layout()
        self.bind_all("<MouseWheel>", self._wheel)
        self.show("dash")
        self.after(100, self._poll)
        self.after(600, self._tick_dash)
        if not is_admin():
            self.log("[AVISO] Sem privilégios de administrador: vários ajustes vão falhar. Abra o programa como administrador.")
        self.log(f"Pronto. Backups ficam em: {BACKUP_FILE}")

    # ---------- estilo / layout ----------
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Blue.Horizontal.TProgressbar", troughcolor=CARD2, background=ACCENT, bordercolor=CARD2,
                    lightcolor=ACCENT, darkcolor=ACCENT)
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, rowheight=26, borderwidth=0, font=FONT)
        s.configure("Treeview.Heading", background=CARD2, foreground=FG, font=FONT_B, relief="flat")
        s.map("Treeview", background=[("selected", ACCENT2)])
        s.configure("Vertical.TScrollbar", background=CARD2, troughcolor=BG, bordercolor=BG, arrowcolor=FG)

    def _layout(self):
        side = tk.Frame(self, bg=SIDE, width=210)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="⚡ WinBoost", bg=SIDE, fg=ACCENT, font=("Segoe UI", 17, "bold")).pack(pady=(20, 18))
        nav = [("dash", "🏠  Painel"), ("clean", "🧹  Limpeza"), ("perf", "🚀  Desempenho"), ("priv", "🔒  Privacidade"),
               ("serv", "⚙️  Serviços"), ("startup", "⏱️  Inicialização"), ("apps", "🗑️  Bloatware"), ("maint", "🛠️  Manutenção")]
        for key, text in nav:
            b = tk.Button(side, text=text, anchor="w", bg=SIDE, fg=FG, activebackground=CARD, activeforeground=FG,
                          relief="flat", bd=0, padx=20, pady=10, font=FONT, cursor="hand2", command=lambda k=key: self.show(k))
            b.pack(fill="x")
            self.navbtns[key] = b
        right = tk.Frame(self, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        # rodapé: progresso + log
        foot = tk.Frame(right, bg=BG)
        foot.pack(side="bottom", fill="x", padx=14, pady=(0, 10))
        top = tk.Frame(foot, bg=BG)
        top.pack(fill="x", pady=(6, 4))
        self.status = tk.Label(top, text="Pronto", bg=BG, fg=MUTED, font=FONT_S)
        self.status.pack(side="left")
        self.prog = ttk.Progressbar(foot, style="Blue.Horizontal.TProgressbar", maximum=100)
        self.prog.pack(fill="x")
        logf = tk.Frame(foot, bg=CARD)
        logf.pack(fill="x", pady=(6, 0))
        self.logbox = tk.Text(logf, height=9, bg=CARD, fg=FG, insertbackground=FG, relief="flat", font=("Consolas", 9),
                              state="disabled", wrap="word")
        lsb = ttk.Scrollbar(logf, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.logbox.pack(fill="x")
        self.content = tk.Frame(right, bg=BG)
        self.content.pack(side="top", fill="both", expand=True)
        self.build_dash()
        self.build_clean()
        self.build_tweaks("perf", "perf", "Desempenho & Rede", "Ajustes que deixam o Windows mais rápido e responsivo. Tudo é salvo para poder desfazer.", extra="net")
        self.build_tweaks("priv", "priv", "Privacidade", "Reduz telemetria, rastreamento e propagandas do Windows.")
        self.build_services()
        self.build_startup()
        self.build_apps()
        self.build_maint()

    def new_page(self, key, title, sub):
        f = tk.Frame(self.content, bg=BG)
        self.pages[key] = f
        tk.Label(f, text=title, font=FONT_H, bg=BG, fg=FG).pack(anchor="w", padx=20, pady=(16, 0))
        tk.Label(f, text=sub, font=FONT, bg=BG, fg=MUTED, wraplength=760, justify="left").pack(anchor="w", padx=20, pady=(0, 10))
        return f

    def show(self, key):
        if self.cur:
            self.pages[self.cur].pack_forget()
            self.navbtns[self.cur].configure(bg=SIDE, fg=FG)
        self.cur = key
        self.pages[key].pack(fill="both", expand=True)
        self.navbtns[key].configure(bg=CARD, fg=ACCENT)
        if key == "startup":
            self.refresh_startup()

    def _wheel(self, e):
        c = self.scrolls.get(self.cur)
        if c:
            c.yview_scroll(int(-e.delta / 120), "units")

    # ---------- log / thread ----------
    def log(self, msg):
        self.q.put(("log", msg))

    def ui(self, fn):
        self.q.put(("call", fn))

    def setp(self, pct, text=None):
        def f():
            self.prog["value"] = pct
            if text:
                self.status.configure(text=text)
        self.ui(f)

    def _poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end", f"{data}\n")
                    self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                elif kind == "call":
                    data()
                elif kind == "done":
                    self.busy = False
                    self.prog["value"] = 100
                    self.status.configure(text="Concluído ✔")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def run_bg(self, fn, name):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Aguarde a tarefa atual terminar.")
            return
        self.busy = True
        self.prog["value"] = 0
        self.status.configure(text=name + "...")
        self.log(f"\n=== {name} ===")

        def wrapper():
            try:
                fn()
            except Exception as e:
                self.log(f"[ERRO] {e}")
            finally:
                self.bk.save()
                self.q.put(("done", None))
        threading.Thread(target=wrapper, daemon=True).start()

    # ---------- Painel ----------
    def stat_card(self, parent, title):
        c = tk.Frame(parent, bg=CARD)
        c.pack(side="left", fill="both", expand=True, padx=6)
        tk.Label(c, text=title, bg=CARD, fg=MUTED, font=FONT_S).pack(anchor="w", padx=14, pady=(12, 0))
        v = tk.Label(c, text="--", bg=CARD, fg=FG, font=FONT_BIG)
        v.pack(anchor="w", padx=14)
        sub = tk.Label(c, text="", bg=CARD, fg=MUTED, font=FONT_S)
        sub.pack(anchor="w", padx=14)
        bar = ttk.Progressbar(c, style="Blue.Horizontal.TProgressbar", maximum=100)
        bar.pack(fill="x", padx=14, pady=(6, 14))
        return v, sub, bar

    def build_dash(self):
        f = self.new_page("dash", "Painel", "Visão geral do seu PC e otimização completa com um clique.")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", padx=14, pady=4)
        self.cpu_v, self.cpu_s, self.cpu_b = self.stat_card(row, "CPU")
        self.ram_v, self.ram_s, self.ram_b = self.stat_card(row, "MEMÓRIA RAM")
        self.dsk_v, self.dsk_s, self.dsk_b = self.stat_card(row, "DISCO DO SISTEMA")
        info = tk.Frame(f, bg=CARD)
        info.pack(fill="x", padx=20, pady=10)
        cpu, osn = system_info()
        self.up_lbl = tk.Label(info, text="", bg=CARD, fg=FG, font=FONT, justify="left", anchor="w")
        self.up_lbl.pack(anchor="w", padx=14, pady=12)
        self._info_static = f"Sistema: {osn}\nProcessador: {cpu}"
        act = tk.Frame(f, bg=BG)
        act.pack(fill="x", padx=20, pady=6)
        mkbtn(act, "⚡  OTIMIZAR TUDO (1 clique)", self.one_click, accent=True).pack(side="left", padx=(0, 8))
        mkbtn(act, "🛟  Criar ponto de restauração", lambda: self.run_bg(self.restore_point, "Ponto de restauração")).pack(side="left", padx=4)
        mkbtn(act, "🧠  Liberar RAM", self.free_ram).pack(side="left", padx=4)
        tk.Label(f, justify="left", bg=BG, fg=MUTED, font=FONT_S, wraplength=780,
                 text="'Otimizar tudo' cria um ponto de restauração, aplica os ajustes recomendados de desempenho, rede e privacidade, "
                      "desativa serviços seguros de desativar e faz a limpeza. Itens marcados como opcionais NÃO são aplicados. "
                      "Reinicie o PC ao final. Tudo pode ser revertido em Manutenção > Desfazer alterações.").pack(anchor="w", padx=20, pady=8)

    def _tick_dash(self):
        try:
            idle, total = cpu_times()
            li, lt = self._last_cpu
            self._last_cpu = (idle, total)
            cpu = max(0.0, min(100.0, 100.0 * (1 - (idle - li) / max(1, total - lt))))
            self.cpu_v.configure(text=f"{cpu:.0f}%")
            self.cpu_b["value"] = cpu
            self.cpu_s.configure(text=f"{os.cpu_count()} threads")
            load, tot, avail = mem_status()
            self.ram_v.configure(text=f"{load}%")
            self.ram_b["value"] = load
            self.ram_s.configure(text=f"{fmt_size(tot - avail)} de {fmt_size(tot)}")
            drive = os.environ.get("SystemDrive", "C:") + "\\"
            du = shutil.disk_usage(drive)
            pct = du.used / du.total * 100
            self.dsk_v.configure(text=f"{pct:.0f}%")
            self.dsk_b["value"] = pct
            self.dsk_s.configure(text=f"{fmt_size(du.free)} livres de {fmt_size(du.total)}")
            self.up_lbl.configure(text=f"{self._info_static}\nTempo ligado: {uptime_text()}")
        except Exception:
            pass
        self.after(1500, self._tick_dash)

    def free_ram(self):
        before = mem_status()[2]

        def job():
            n = trim_memory()
            time.sleep(1)
            freed = mem_status()[2] - before
            self.log(f"Working set reduzido em {n} processos. RAM livre ganhou ~{fmt_size(max(0, freed))} (efeito temporário).")
        self.run_bg(job, "Liberando RAM")

    def restore_point(self):
        self.log("Criando ponto de restauração (pode levar um minuto)...")
        rc, out = ps(r"Enable-ComputerRestore -Drive 'C:\' -ErrorAction SilentlyContinue; "
                     r"Checkpoint-Computer -Description 'WinBoost' -RestorePointType MODIFY_SETTINGS -ErrorAction Stop")
        if rc == 0:
            self.log("   ponto de restauração criado ✔")
        else:
            self.log("   não foi possível criar (o Windows permite 1 a cada 24h ou a Proteção do Sistema está desligada).")

    # ---------- Limpeza ----------
    def build_clean(self):
        f = self.new_page("clean", "Limpeza", "Remove arquivos inúteis e libera espaço. Use 'Analisar' para ver quanto cada item ocupa.")
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=20, pady=(0, 6))
        mkbtn(bar, "🔍  Analisar", self.scan_clean).pack(side="left", padx=(0, 8))
        mkbtn(bar, "🧹  Limpar selecionados", self.start_clean, accent=True).pack(side="left")
        sf = ScrollFrame(f)
        sf.pack(fill="both", expand=True, padx=14, pady=6)
        self.scrolls["clean"] = sf.canvas
        self.clean_vars, self.clean_lbls = build_checks(sf.inner, [(t[0], t[1], t[2], t[3]) for t in self.clean])

    def scan_clean(self):
        def job():
            total = 0
            for i, (key, name, _d, _df, paths) in enumerate(self.clean):
                if not paths:
                    continue
                s = path_size(paths)
                total += s
                self.ui(lambda k=key, s=s: self.clean_lbls[k].configure(text=fmt_size(s)))
                self.setp((i + 1) / len(self.clean) * 100, f"Analisando {name}")
            self.log(f"Total recuperável (sem Lixeira): {fmt_size(total)}")
        self.run_bg(job, "Analisando")

    def start_clean(self):
        keys = [k for k, v in self.clean_vars.items() if v.get()]
        if not keys:
            return
        self.run_bg(lambda: self.do_clean(keys), "Limpando")

    def do_clean(self, keys, base=0.0, span=100.0):
        total = 0
        targets = {t[0]: t for t in self.clean}
        for i, k in enumerate(keys):
            _k, name, _d, _df, paths = targets[k]
            self.setp(base + span * i / len(keys), f"Limpando {name}")
            if k == "recycle":
                try:
                    ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
                    self.log("• Lixeira esvaziada")
                except Exception as e:
                    self.log(f"• Lixeira: {e}")
                continue
            if k == "wu_cache":
                run("net stop wuauserv /y")
                run("net stop bits /y")
            freed, failed = delete_contents(paths)
            if k == "wu_cache":
                run("net start bits")
                run("net start wuauserv")
            total += freed
            self.log(f"• {name}: {fmt_size(freed)} liberados" + (f" ({failed} arquivos em uso ignorados)" if failed else ""))
            self.ui(lambda kk=k: self.clean_lbls[kk].configure(text="✔"))
        self.log(f"TOTAL LIBERADO: {fmt_size(total)}")
        return total

    # ---------- Ajustes (Desempenho / Privacidade) ----------
    def build_tweaks(self, key, group, title, sub, extra=None):
        f = self.new_page(key, title, sub)
        items = [t for t in TWEAKS if t["group"] in (group, extra)]
        sf = ScrollFrame(f)
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=20, pady=(0, 6))
        sf.pack(fill="both", expand=True, padx=14, pady=6)
        self.scrolls[key] = sf.canvas
        vars_, _ = build_checks(sf.inner, [(t["id"], t["name"], t["desc"], t["default"]) for t in items])
        if not hasattr(self, "tweak_vars"):
            self.tweak_vars = {}
        self.tweak_vars.update(vars_)
        ids = [t["id"] for t in items]
        mkbtn(bar, "✅  Aplicar selecionados", lambda: self.apply_tweaks([i for i in ids if vars_[i].get()]), accent=True).pack(side="left", padx=(0, 8))
        mkbtn(bar, "Marcar recomendados", lambda: [vars_[t["id"]].set(t["default"]) for t in items]).pack(side="left", padx=4)
        mkbtn(bar, "Marcar todos", lambda: [v.set(True) for v in vars_.values()]).pack(side="left", padx=4)
        mkbtn(bar, "Desmarcar todos", lambda: [v.set(False) for v in vars_.values()]).pack(side="left", padx=4)

    def apply_tweaks(self, ids):
        tw = [t for t in TWEAKS if t["id"] in ids]
        if not tw:
            return
        self.run_bg(lambda: self.do_tweaks(tw), "Aplicando ajustes")

    def do_tweaks(self, tweaks, base=0.0, span=100.0):
        for i, t in enumerate(tweaks):
            self.setp(base + span * i / len(tweaks), t["name"])
            self.apply_tweak(t)
        self.log("Alguns ajustes só têm efeito após reiniciar o PC.")

    def apply_tweak(self, t):
        self.log(f"• {t['name']}")
        ok = True
        for h, p, n, v, k in t.get("regs", []):
            try:
                reg_set(self.bk, h, p, n, v, k)
            except Exception as e:
                self.log(f"   falhou ({n}): {e}")
                ok = False
        if t.get("func"):
            getattr(self, t["func"])()
        for c in t.get("cmds", []):
            rc, out = run(c)
            if rc != 0:
                self.log(f"   comando falhou: {out.strip()[:140]}")
                ok = False
        if ok:
            for u in t.get("undo", []):
                self.bk.add_undo(u)

    def power_plan(self):
        ultimate_dest = "99999999-9999-9999-9999-999999999999"
        rc, out = run("powercfg /getactivescheme")
        m = re.search(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", out)
        orig = m.group(1).lower() if m else None
        rc, _ = run(f"powercfg /setactive {ultimate_dest}")
        if rc != 0:
            run(f"powercfg /duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61 {ultimate_dest}")
            rc, _ = run(f"powercfg /setactive {ultimate_dest}")
        if rc != 0:
            rc, _ = run("powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
            self.log("   plano Alto Desempenho ativado" if rc == 0 else "   não foi possível trocar o plano")
        else:
            self.log("   plano Desempenho Máximo ativado")
        if orig and orig != ultimate_dest:
            self.bk.add_undo(f"powercfg /setactive {orig}")

    # ---------- Serviços ----------
    def build_services(self):
        f = self.new_page("serv", "Serviços", "Desativa serviços que quase ninguém usa. O estado original é guardado e pode ser restaurado.")
        sf = ScrollFrame(f)
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=20, pady=(0, 6))
        sf.pack(fill="both", expand=True, padx=14, pady=6)
        self.scrolls["serv"] = sf.canvas
        self.serv_vars, _ = build_checks(sf.inner, [(s[0], s[1], s[2], s[5]) for s in SERVICES])
        mkbtn(bar, "✅  Aplicar selecionados", self.apply_services, accent=True).pack(side="left", padx=(0, 8))
        mkbtn(bar, "Marcar recomendados", lambda: [self.serv_vars[s[0]].set(s[5]) for s in SERVICES]).pack(side="left", padx=4)

    def apply_services(self):
        sel = [s for s in SERVICES if self.serv_vars[s[0]].get()]
        if sel:
            self.run_bg(lambda: self.do_services(sel), "Ajustando serviços")

    def do_services(self, sel, base=0.0, span=100.0):
        for i, (_id, name, _d, svcs, mode, _df) in enumerate(sel):
            self.setp(base + span * i / len(sel), name)
            self.log(f"• {name}")
            for s in svcs:
                self.set_service(s, mode)

    def set_service(self, name, mode):
        path = rf"SYSTEM\CurrentControlSet\Services\{name}"
        r = reg_read("HKLM", path, "Start")
        if r is None:
            self.log(f"   {name}: não existe neste Windows")
            return
        start = r[0]
        if start in (0, 1):
            self.log(f"   {name}: serviço de boot/sistema, ignorado por segurança")
            return
        orig = {2: "auto", 3: "demand", 4: "disabled"}.get(start, "demand")
        d = reg_read("HKLM", path, "DelayedAutostart")
        if start == 2 and d and d[0] == 1:
            orig = "delayed-auto"
        self.bk.d["services"].setdefault(name, orig)
        rc, out = run(f"sc config {name} start= {mode}")
        if mode == "disabled":
            run(f"sc stop {name}")
        self.log(f"   {name}: {orig} -> {mode}" if rc == 0 else f"   {name}: falhou ({out.strip()[:100]})")

    # ---------- Inicialização ----------
    def build_startup(self):
        f = self.new_page("startup", "Inicialização", "Programas que abrem com o Windows. Desativar acelera o boot (não apaga o programa).")
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=20, pady=(0, 6))
        mkbtn(bar, "⛔  Desativar selecionado", lambda: self.toggle_startup(False), accent=True).pack(side="left", padx=(0, 8))
        mkbtn(bar, "✅  Ativar selecionado", lambda: self.toggle_startup(True)).pack(side="left", padx=4)
        mkbtn(bar, "🔄  Atualizar", self.refresh_startup).pack(side="left", padx=4)
        cols = ("nome", "origem", "estado", "comando")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (200, 150, 90, 420)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=20, pady=6)
        self.startup_items = []

    def list_startup(self):
        items = []
        run_p = r"Software\Microsoft\Windows\CurrentVersion\Run"
        appr = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
        sources = [("HKCU", run_p, appr + r"\Run", "Registro (usuário)"),
                   ("HKLM", run_p, appr + r"\Run", "Registro (sistema)"),
                   ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", appr + r"\Run32", "Registro (32-bit)")]
        for hive, path, ap, label in sources:
            try:
                with winreg.OpenKey(HIVE[hive], path, 0, winreg.KEY_READ | W64) as k:
                    i = 0
                    while True:
                        try:
                            n, v, _t = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        i += 1
                        a = reg_read(hive, ap, n)
                        en = not (a and len(a[0]) and a[0][0] & 1)
                        items.append(dict(name=n, cmd=str(v), origin=label, enabled=en, hive=hive, ap=ap))
            except OSError:
                pass
        e = os.environ
        folders = [("Pasta Iniciar (usuário)", os.path.join(e.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"), "HKCU"),
                   ("Pasta Iniciar (todos)", os.path.join(e.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs\StartUp"), "HKLM")]
        for label, folder, hive in folders:
            if os.path.isdir(folder):
                for fn in os.listdir(folder):
                    if fn.lower() == "desktop.ini":
                        continue
                    ap = appr + r"\StartupFolder"
                    a = reg_read(hive, ap, fn)
                    en = not (a and len(a[0]) and a[0][0] & 1)
                    items.append(dict(name=fn, cmd=os.path.join(folder, fn), origin=label, enabled=en, hive=hive, ap=ap))
        return items

    def refresh_startup(self):
        self.tree.delete(*self.tree.get_children())
        self.startup_items = self.list_startup()
        for i, it in enumerate(self.startup_items):
            self.tree.insert("", "end", iid=str(i), values=(it["name"], it["origin"], "Ativo" if it["enabled"] else "Desativado", it["cmd"]))

    def toggle_startup(self, enable):
        sel = self.tree.selection()
        if not sel:
            return
        it = self.startup_items[int(sel[0])]
        data = (b"\x02" if enable else b"\x03") + b"\x00" * 11
        try:
            with winreg.CreateKeyEx(HIVE[it["hive"]], it["ap"], 0, winreg.KEY_SET_VALUE | W64) as k:
                winreg.SetValueEx(k, it["name"], 0, winreg.REG_BINARY, data)
            self.log(f"Inicialização: {it['name']} -> {'ativado' if enable else 'desativado'}")
        except Exception as e:
            self.log(f"[ERRO] {e}")
        self.refresh_startup()

    # ---------- Bloatware ----------
    def build_apps(self):
        f = self.new_page("apps", "Bloatware", "Remove apps pré-instalados que quase ninguém usa. Para reinstalar, use a Microsoft Store.")
        sf = ScrollFrame(f)
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=20, pady=(0, 6))
        sf.pack(fill="both", expand=True, padx=14, pady=6)
        self.scrolls["apps"] = sf.canvas
        self.app_vars, _ = build_checks(sf.inner, [(n, d, n, df) for n, d, df in BLOAT])
        mkbtn(bar, "🗑️  Remover selecionados", self.remove_apps, accent=True).pack(side="left")

    def remove_apps(self):
        sel = [n for n, v in self.app_vars.items() if v.get()]
        if not sel or not messagebox.askyesno(APP_NAME, f"Remover {len(sel)} app(s)? Isso não pode ser desfeito pelo WinBoost."):
            return
        self.run_bg(lambda: self.do_remove_apps(sel), "Removendo apps")

    def do_remove_apps(self, names):
        for i, n in enumerate(names):
            self.setp(i / len(names) * 100, f"Removendo {n}")
            self.log(f"• {n}")
            ps(f"Get-AppxPackage -AllUsers -Name '{n}' | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue; "
               f"Get-AppxProvisionedPackage -Online | Where-Object DisplayName -like '{n}' | "
               f"Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue")

    # ---------- Manutenção ----------
    def build_maint(self):
        f = self.new_page("maint", "Manutenção", "Ferramentas de reparo e o botão de desfazer tudo.")
        sf = ScrollFrame(f)
        sf.pack(fill="both", expand=True, padx=14, pady=6)
        self.scrolls["maint"] = sf.canvas
        box = tk.Frame(sf.inner, bg=CARD)
        box.pack(fill="x", padx=6, pady=3)
        tk.Label(box, text="↩️  Desfazer todas as alterações do WinBoost", bg=CARD, fg=FG, font=FONT_B).pack(anchor="w", padx=14, pady=(10, 0))
        tk.Label(box, text="Restaura registro, serviços, plano de energia e comandos aplicados (não restaura apps removidos nem arquivos limpos).",
                 bg=CARD, fg=MUTED, font=FONT_S, wraplength=640, justify="left").pack(anchor="w", padx=14)
        mkbtn(box, "Desfazer alterações", self.undo_all, accent=True).pack(anchor="w", padx=14, pady=10)
        for name, desc, cmd in TOOLS:
            b = tk.Frame(sf.inner, bg=CARD)
            b.pack(fill="x", padx=6, pady=3)
            b.columnconfigure(0, weight=1)
            tk.Label(b, text=name, bg=CARD, fg=FG, font=FONT_B).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
            tk.Label(b, text=desc, bg=CARD, fg=MUTED, font=FONT_S).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))
            mkbtn(b, "Executar", lambda n=name, c=cmd: self.run_tool(n, c)).grid(row=0, column=1, rowspan=2, padx=12)

    def run_tool(self, name, cmd):
        if cmd == "@icons":
            self.run_bg(self.icon_cache, name)
        else:
            self.run_bg(lambda: (self.setp(30, name), run_stream(cmd, self.log)), name)

    def icon_cache(self):
        self.log("Fechando o Explorer e removendo caches de ícones...")
        run("taskkill /f /im explorer.exe")
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Explorer")
        freed, _ = delete_contents([os.path.join(base, "iconcache*"), os.path.join(base, "thumbcache*")])
        subprocess.Popen("explorer.exe", creationflags=CREATE_NO_WINDOW)
        self.log(f"Cache reconstruído ({fmt_size(freed)} removidos).")

    def undo_all(self):
        if not self.bk.has_data():
            messagebox.showinfo(APP_NAME, "Não há alterações registradas para desfazer.")
            return
        if messagebox.askyesno(APP_NAME, "Restaurar o estado original de tudo que o WinBoost alterou?"):
            self.run_bg(lambda: self.bk.restore_all(self.log), "Desfazendo alterações")

    # ---------- 1 clique ----------
    def one_click(self):
        if not messagebox.askyesno(APP_NAME, "Aplicar a otimização completa recomendada?\n\nUm ponto de restauração será criado e "
                                             "todas as mudanças poderão ser desfeitas depois."):
            return
        tweaks = [t for t in TWEAKS if t["default"]]
        svcs = [s for s in SERVICES if s[5]]
        keys = [t[0] for t in self.clean if t[3]]

        def job():
            self.setp(2, "Ponto de restauração")
            self.restore_point()
            self.do_tweaks(tweaks, 5, 40)
            self.log("-- Serviços --")
            self.do_services(svcs, 45, 15)
            self.log("-- Limpeza --")
            total = self.do_clean(keys, 60, 38)
            self.setp(99, "Finalizando")
            self.log(f"\n✔ OTIMIZAÇÃO COMPLETA. Espaço liberado: {fmt_size(total)}. Reinicie o PC para aplicar tudo.")
        self.run_bg(job, "Otimização completa")


def main():
    if not IS_WIN:
        print("O WinBoost só funciona no Windows.")
        sys.exit(1)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if not is_admin():
        if relaunch_as_admin():
            sys.exit(0)
    App().mainloop()


if __name__ == "__main__":
    main()
