// Fixed runtime environment: no tokens, proxy URLs, pip config, or provider API-key variables.
const names = new Set(['HOME','USERPROFILE','APPDATA','LOCALAPPDATA','XDG_DATA_HOME','XDG_CACHE_HOME','PATH','TMP','TMPDIR','TEMP','LANG','LC_ALL','SYSTEMROOT','WINDIR','COMSPEC','SYSTEMDRIVE','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMW6432','COMMONPROGRAMFILES','PATHEXT','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE','PROCESSOR_ARCHITEW6432','JARVIS_DATA_DIR','JARVIS_FS_PROJECT_ROOTS','JARVIS_BROWSER_ORIGINS','JARVIS_BROWSER_MODE','JARVIS_BROWSER_CDP_ENDPOINT','JARVIS_PLANNER_MODEL','JARVIS_VOSK_MODEL','QT_QPA_PLATFORM']);
function cleanEnvironment(source) {
 return Object.fromEntries(Object.entries(source).filter(([name,value])=>names.has(name.toUpperCase())&&typeof value==='string'));
}
for (const name of ["JARVIS_VOICE_WAKE_WORD", "JARVIS_VOICE_AUTO_STOP_ON_SILENCE",
  "JARVIS_VOICE_RMS_THRESHOLD", "JARVIS_VOICE_RMS_CALIBRATE"]) names.add(name);
module.exports={cleanEnvironment};
names.add('JARVIS_BROWSER_CDP_URL');
