/// <reference types="vite/client" />
interface JarvisWindowControls {
  minimize(): Promise<void>;
  maximize(): Promise<boolean>;
  close(): Promise<void>;
  isMaximized(): Promise<boolean>;
  mode(): Promise<"normal" | "hud">;
  restore(): Promise<void>;
  subscribeMode(listener: (mode: "normal" | "hud") => void): () => void;
}
interface Window {
  jarvisOverlay?: {
    status(): Promise<import("./components/CompactHud").HudState | null>;
    subscribe(listener: (state: import("./components/CompactHud").HudState | null) => void): () => void;
  };
  jarvisWindow?: JarvisWindowControls;
  jarvis?: import("./runtime").JarvisApi;
}
