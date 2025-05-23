; Script generated by the Inno Setup Script Wizard.
; SEE THE DOCUMENTATION FOR DETAILS ON CREATING INNO SETUP SCRIPT FILES!

#define MyAppName "Trace Document Tracker"
#define MyAppVersion "1.1"
#define MyAppPublisher "Arnav Rajurkar"
#define MyAppExeName "launcher.exe"
#define MyAppAssocName MyAppName + " File"
#define MyAppAssocExt ".myp"
#define MyAppAssocKey StringChange(MyAppAssocName, " ", "") + MyAppAssocExt

[Setup]
; NOTE: The value of AppId uniquely identifies this application. Do not use the same AppId value in installers for other applications.
; (To generate a new GUID, click Tools | Generate GUID inside the IDE.)
AppId={{211A9B8A-AC41-4FA5-8D3C-CC871CF2B2B1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
;AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; "ArchitecturesAllowed=x64compatible" specifies that Setup cannot run
; on anything but x64 and Windows 11 on Arm.
ArchitecturesAllowed=x64compatible
; "ArchitecturesInstallIn64BitMode=x64compatible" requests that the
; install be done in "64-bit mode" on x64 or Windows 11 on Arm,
; meaning it should use the native 64-bit Program Files directory and
; the 64-bit view of the registry.
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
DisableProgramGroupPage=yes
LicenseFile=C:\Users\Arnav\Documents\document_tracker\LICENSE
; Uncomment the following line to run in non administrative install mode (install for current user only).
;PrivilegesRequired=lowest
OutputBaseFilename=TraceDTSetup
SetupIconFile=C:\Users\Arnav\Documents\document_tracker\trace_icon.ico
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Application files
Source: "C:\Users\Arnav\Documents\document_tracker\dist\launcher.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Users\Arnav\Documents\document_tracker\document_tracker.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Users\Arnav\Documents\document_tracker\entries.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Users\Arnav\Documents\document_tracker\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Users\Arnav\Documents\document_tracker\trace_icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Users\Arnav\Documents\document_tracker\.streamlit\config.toml"; DestDir: "{app}\.streamlit"; Flags: ignoreversion

; Python installation - embed Python for the application
Source: "C:\Users\Arnav\AppData\Local\Programs\Python\Python313\python.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Users\Arnav\AppData\Local\Programs\Python\Python313\python313.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Users\Arnav\AppData\Local\Programs\Python\Python313\pythonw.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Users\Arnav\AppData\Local\Programs\Python\Python313\DLLs\*"; DestDir: "{app}\DLLs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\Users\Arnav\AppData\Local\Programs\Python\Python313\Lib\*"; DestDir: "{app}\Lib"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\Users\Arnav\AppData\Local\Programs\Python\Python313\Scripts\*"; DestDir: "{app}\Scripts"; Flags: ignoreversion recursesubdirs createallsubdirs

; NOTE: Don't use "Flags: ignoreversion" on any shared system files

[Registry]
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocExt}\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppAssocKey}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppAssocName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    SaveStringToFile(ExpandConstant('{app}\GetPipScript.py'),
      'import sys' + #13#10 +
      'import subprocess' + #13#10 +
      'import os' + #13#10 + #13#10 +
      'def run_command(command):' + #13#10 +
      '    print(f"Running: {" ".join(command)}")' + #13#10 +
      '    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)' + #13#10 +
      '    print(f"Return code: {result.returncode}")' + #13#10 +
      '    if result.stdout:' + #13#10 +
      '        print(f"Output: {result.stdout}")' + #13#10 +
      '    if result.stderr:' + #13#10 +
      '        print(f"Error: {result.stderr}")' + #13#10 +
      '    return result.returncode' + #13#10 + #13#10 +
      'def main():' + #13#10 +
      '    # Ensure pip is available' + #13#10 +
      '    try:' + #13#10 +
      '        result = subprocess.run([sys.executable, "-m", "pip", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)' + #13#10 +
      '        if result.returncode != 0:' + #13#10 +
      '            print("Pip not found, installing...")' + #13#10 +
      '            run_command([sys.executable, "-m", "ensurepip", "--upgrade"])' + #13#10 +
      '        else:' + #13#10 +
      '            print("Pip found, upgrading...")' + #13#10 +
      '            run_command([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])' + #13#10 + #13#10 +
      '        # Install requirements' + #13#10 +
      '        req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")' + #13#10 +
      '        if os.path.exists(req_file):' + #13#10 +
      '            print(f"Installing requirements from {req_file}")' + #13#10 +
      '            run_command([sys.executable, "-m", "pip", "install", "-r", req_file])' + #13#10 +
      '            print("Requirements installation complete")' + #13#10 +
      '        else:' + #13#10 +
      '            print(f"Requirements file not found at {req_file}")' + #13#10 +
      '    except Exception as e:' + #13#10 +
      '        print(f"Error: {str(e)}")' + #13#10 +
      '        return 1' + #13#10 +
      '    return 0' + #13#10 + #13#10 +
      'if __name__ == "__main__":' + #13#10 +
      '    sys.exit(main())',
      False);
  end;
end;

[Run]
; Ensure pip is installed and install requirements
Filename: "{app}\python.exe"; Parameters: "{app}\GetPipScript.py"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated

; Launch the application after install
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent