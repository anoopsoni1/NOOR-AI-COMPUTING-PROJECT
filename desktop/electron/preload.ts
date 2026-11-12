import { contextBridge, ipcRenderer } from "electron";

const arg = (name: string): string => process.argv.find((a) => a.startsWith(`--${name}=`))?.slice(name.length + 3) ?? "";

contextBridge.exposeInMainWorld("noor", {
  backendUrl: arg("noor-backend"),
  apiToken: arg("noor-token"),
  platform: process.platform,
  system: {
    get: () => ipcRenderer.invoke("system:get"),
    set: (patch: { startAtLogin?: boolean; minimizeToTray?: boolean }) => ipcRenderer.invoke("system:set", patch),
  },
});
