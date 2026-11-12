import { ChildProcess, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { app, BrowserWindow, ipcMain, Menu, nativeImage, session, shell, Tray } from "electron";

const isDev = process.env.NOOR_DEV === "1";
const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
// Shared secret between this window and the backend (only when Electron launches the backend itself).
const apiToken = process.env.NOOR_API_TOKEN ?? (process.env.NOOR_SPAWN_BACKEND === "1" ? randomBytes(24).toString("hex") : "");
const TRAY_ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAA30lEQVR4nO1XOxaCMBDcnUfnCTyMvaVHpbT3MJ6AGhoLzMuGmZgo7+GUsPNJQsLG7OhwlTDdL3Pp/en6kDS9lXFtEG9trAbxGvNIVK0vBkjF1LVl+ehhnuNEs4Me5koIKAItQqTwKOEWcX6Ob6Px843+oNfaQ4nEGKfPt4KESzAR+z0yV2vWXui19qwmWAFmZDW1sB8D/wBGQtleSi16/IIVTShbjxkZU/PRSegvA/UoDvUsgfI/YFHSBEtsYZ4DahuJGvPcjCJHbBGCbWxcEflqU7qLtnwXF5PeVzM7PBYLI5gyu52xnAAAAABJRU5ErkJggg==";

let backend: ChildProcess | null = null;
let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;

// ---- system preferences (start with Windows, minimize to tray) --------------------------------------------------------
interface SystemPrefs {
  startAtLogin: boolean;
  minimizeToTray: boolean;
}
const prefsFile = (): string => path.join(app.getPath("userData"), "noor-system.json");

function readPrefs(): SystemPrefs {
  try {
    return { startAtLogin: false, minimizeToTray: false, ...(JSON.parse(fs.readFileSync(prefsFile(), "utf8")) as Partial<SystemPrefs>) };
  } catch {
    return { startAtLogin: app.getLoginItemSettings().openAtLogin, minimizeToTray: false };
  }
}

function applyPrefs(p: SystemPrefs): SystemPrefs {
  fs.writeFileSync(prefsFile(), JSON.stringify(p));
  if (app.isPackaged) app.setLoginItemSettings({ openAtLogin: p.startAtLogin, args: ["--hidden"] });
  return p;
}

ipcMain.handle("system:get", () => ({ ...readPrefs(), startAtLogin: app.isPackaged ? app.getLoginItemSettings().openAtLogin : readPrefs().startAtLogin }));
ipcMain.handle("system:set", (_e, patch: Partial<SystemPrefs>) => applyPrefs({ ...readPrefs(), ...patch }));

// ---- backend ---------------------------------------------------------------------------------------------------------------
function startBackend(): void {
  if (process.env.NOOR_SPAWN_BACKEND !== "1") return; // by default run the backend yourself (see README)
  const root = isDev ? path.resolve(__dirname, "../../..") : path.join(process.resourcesPath, "..");
  const python = process.env.NOOR_PYTHON ?? (process.platform === "win32" ? path.join(root, ".venv", "Scripts", "python.exe") : path.join(root, ".venv", "bin", "python"));
  backend = spawn(python, ["-m", "backend.main"], {
    cwd: root,
    env: { ...process.env, NOOR_API_TOKEN: apiToken, API_TOKEN: apiToken },
    stdio: "inherit",
  });
  backend.on("exit", (code) => console.log(`backend exited (${code})`));
}

// ---- window & tray ------------------------------------------------------------------------------------------------------------
function createWindow(startHidden = false): void {
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    title: "NOOR",
    show: !startHidden,
    backgroundColor: "#04050a", // matches the UI so there is no white flash on launch
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      autoplayPolicy: "no-user-gesture-required", // NOOR speaks without a click first
      backgroundThrottling: false, // keep the microphone pipeline and wake word running while minimised to the tray
      nodeIntegration: false,
      sandbox: false,
      additionalArguments: [`--noor-backend=${backendUrl}`, `--noor-token=${apiToken}`],
    },
  });
  win.setMenuBarVisibility(false);
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  win.on("close", (e) => {
    if (!quitting && readPrefs().minimizeToTray) {
      e.preventDefault(); // keep NOOR alive and listening in the tray
      win?.hide();
    }
  });
  win.on("closed", () => (win = null));
  if (isDev) void win.loadURL("http://localhost:5173");
  else void win.loadFile(path.join(__dirname, "../frontend/index.html"));
}

function showWindow(): void {
  if (!win) createWindow();
  win?.show();
  win?.focus();
}

function createTray(): void {
  tray = new Tray(nativeImage.createFromDataURL(`data:image/png;base64,${TRAY_ICON_B64}`));
  tray.setToolTip("NOOR — Your AI Computer Agent");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open NOOR", click: showWindow },
    { type: "separator" },
    { label: "Quit", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", showWindow);
}

const gotLock = app.requestSingleInstanceLock(); // one NOOR at a time: two would fight over the microphone
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", showWindow);
  app.whenReady().then(() => {
    // Allow microphone access (voice input) inside this app only.
    session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => {
      const p = permission as string;
      cb(p === "media" || p === "microphone" || p === "audio-capture");
    });
    session.defaultSession.setPermissionCheckHandler((_wc, permission) => {
      const p = permission as string;
      return p === "media" || p === "microphone" || p === "audio-capture";
    });
    if (typeof (session.defaultSession as any).setDevicePermissionHandler === "function") {
      (session.defaultSession as any).setDevicePermissionHandler((details: any) => {
        return details.deviceType === "microphone" || details.deviceType === "speaker";
      });
    }
    startBackend();
    createTray();
    createWindow(process.argv.includes("--hidden") && readPrefs().minimizeToTray);
    app.on("activate", () => (BrowserWindow.getAllWindows().length === 0 ? createWindow() : showWindow()));
  });
}
app.on("window-all-closed", () => {
  if (process.platform !== "darwin" && !readPrefs().minimizeToTray) app.quit();
});
app.on("before-quit", () => {
  quitting = true;
  backend?.kill();
});
