<#
.SYNOPSIS
音声エンドポイントと、Windows の既定デバイス（再生・録音 × 通常・マルチメディア・通信）を出す。読み取りのみ。

.DESCRIPTION
VB-CABLE を入れると、Windows の既定デバイスが CABLE に変わることがある（VB-Audio の説明・フォーラム）。
変わると、既定を追う他のアプリ（ゲームの音、Discord など）の音が CABLE に流れ、声のトラックに混ざる。
このスクリプトで確かめ、変わっていたら mmsys.cpl（サウンドの設定）で戻す。自動では戻さない。

-SaveBaseline <パス> : 今の既定デバイスを JSON に保存する（導入前の記録用）。
-CompareBaseline <パス> : 保存した既定と比べ、違っていれば終了コード 5 で知らせる。
-ExpectCable : CABLE Input / CABLE Output が無ければ終了コード 6 で知らせる。

出力の最終行は AUDIO_DEVICES で始まる 1 行の要約。

.EXAMPLE
pwsh -NoProfile -File .\tools\check_audio_devices.ps1
pwsh -NoProfile -File .\tools\check_audio_devices.ps1 -CompareBaseline .\data\default_devices_before.json -ExpectCable
#>
param(
    [string]$SaveBaseline = '',
    [string]$CompareBaseline = '',
    [switch]$ExpectCable,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$interopSource = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace VoiceChangerAudio {
    [StructLayout(LayoutKind.Sequential)]
    public struct PropertyKey { public Guid FormatId; public int PropertyId; }

    [StructLayout(LayoutKind.Sequential)]
    public struct PropVariant { public ushort ValueType; public ushort Reserved1; public ushort Reserved2; public ushort Reserved3; public IntPtr Pointer; public IntPtr Pointer2; }

    [Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        int GetCount(out int count);
        int GetAt(int index, out PropertyKey key);
        int GetValue(ref PropertyKey key, out PropVariant value);
        int SetValue(ref PropertyKey key, ref PropVariant value);
        int Commit();
    }

    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice {
        int Activate(ref Guid iid, int context, IntPtr parameters, [MarshalAs(UnmanagedType.IUnknown)] out object instance);
        int OpenPropertyStore(int access, out IPropertyStore properties);
        int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
        int GetState(out int state);
    }

    [Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceCollection {
        int GetCount(out int count);
        int Item(int index, out IMMDevice device);
    }

    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator {
        int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
        int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
    }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class MMDeviceEnumeratorComObject { }

    public class Endpoint { public string Flow; public string Id; public string Name; }

    public static class AudioEndpoints {
        static readonly PropertyKey FriendlyName = new PropertyKey { FormatId = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), PropertyId = 14 };

        static IMMDeviceEnumerator Create() { return (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject(); }

        static string NameOf(IMMDevice device) {
            IPropertyStore store;
            if (device.OpenPropertyStore(0, out store) != 0) return "";
            PropertyKey key = FriendlyName;
            PropVariant value;
            if (store.GetValue(ref key, out value) != 0) return "";
            return value.ValueType == 31 ? Marshal.PtrToStringUni(value.Pointer) : "";
        }

        public static List<Endpoint> ListActive() {
            var result = new List<Endpoint>();
            var enumerator = Create();
            for (int flow = 0; flow < 2; flow++) {
                IMMDeviceCollection collection;
                if (enumerator.EnumAudioEndpoints(flow, 1, out collection) != 0) continue;
                int count; collection.GetCount(out count);
                for (int index = 0; index < count; index++) {
                    IMMDevice device; collection.Item(index, out device);
                    string id; device.GetId(out id);
                    result.Add(new Endpoint { Flow = flow == 0 ? "render" : "capture", Id = id, Name = NameOf(device) });
                }
            }
            return result;
        }

        // role: 0 = 通常（console）、1 = マルチメディア、2 = 通信
        public static Endpoint GetDefault(int flow, int role) {
            IMMDevice device;
            if (Create().GetDefaultAudioEndpoint(flow, role, out device) != 0 || device == null) return null;
            string id; device.GetId(out id);
            return new Endpoint { Flow = flow == 0 ? "render" : "capture", Id = id, Name = NameOf(device) };
        }
    }
}
'@

if (-not ('VoiceChangerAudio.AudioEndpoints' -as [type])) {
    Add-Type -TypeDefinition $interopSource -Language CSharp
}

$roleNames = [ordered]@{ console = 0; multimedia = 1; communications = 2 }
$defaults = [ordered]@{}
$defaultIds = [ordered]@{}
foreach ($flowName in @('render', 'capture')) {
    $flowIndex = if ($flowName -eq 'render') { 0 } else { 1 }
    foreach ($roleEntry in $roleNames.GetEnumerator()) {
        $endpoint = [VoiceChangerAudio.AudioEndpoints]::GetDefault($flowIndex, $roleEntry.Value)
        $defaults["$flowName.$($roleEntry.Key)"] = if ($null -eq $endpoint) { '' } else { $endpoint.Name }
        $defaultIds["$flowName.$($roleEntry.Key)"] = if ($null -eq $endpoint) { '' } else { $endpoint.Id }
    }
}
$activeEndpoints = @([VoiceChangerAudio.AudioEndpoints]::ListActive())

if ($Json) {
    # OBS の device_id（IMMDevice::GetId）と同じ書式の ID を出す。同じ名前の機器が複数あるとき、名前では選べないため
    [ordered]@{ defaults = $defaults; default_ids = $defaultIds; endpoints = @($activeEndpoints | ForEach-Object { [ordered]@{ flow = $_.Flow; name = $_.Name; id = $_.Id } }) } | ConvertTo-Json -Depth 4 -Compress
    exit 0
}

Write-Output '== 有効なエンドポイント'
foreach ($endpoint in $activeEndpoints) {
    Write-Output ('  {0,-8} {1}' -f $endpoint.Flow, $endpoint.Name)
}
Write-Output '== 既定のデバイス'
foreach ($entry in $defaults.GetEnumerator()) {
    Write-Output ('  {0,-24} {1}' -f $entry.Key, $entry.Value)
}

$cableInput = @($activeEndpoints | Where-Object { $_.Flow -eq 'render' -and $_.Name -like 'CABLE Input*' }).Count -gt 0
$cableOutput = @($activeEndpoints | Where-Object { $_.Flow -eq 'capture' -and $_.Name -like 'CABLE Output*' }).Count -gt 0
$cableIsDefault = @($defaults.Values | Where-Object { $_ -like 'CABLE*' }).Count -gt 0
# 再生の既定が CABLE Input だと、ゲームなどの音が仮想マイクに流れ込んで声に混ざる。
# 録音の既定を CABLE Output にするのは、マイクを選べないアプリ（Snipping Tool など）に変換後の声を渡すための正しい設定なので注意しない。
$cableIsRenderDefault = @($defaults.GetEnumerator() | Where-Object { $_.Key -like 'render.*' -and $_.Value -like 'CABLE*' }).Count -gt 0
if ($cableIsRenderDefault) {
    Write-Output '注意: 再生の既定に CABLE が入っています。ゲームなどの音が仮想マイクに流れ込みます。mmsys.cpl の「再生」タブで、普段聴いている機器を「既定のデバイス」「既定の通信デバイス」に戻してください。'
}

$exitCode = 0
if ($SaveBaseline) {
    $baselineDirectory = Split-Path -Path $SaveBaseline -Parent
    if ($baselineDirectory -and -not (Test-Path -LiteralPath $baselineDirectory)) {
        New-Item -ItemType Directory -Path $baselineDirectory -Force | Out-Null
    }
    [ordered]@{ saved = (Get-Date).ToString('s'); defaults = $defaults } | ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath $SaveBaseline -Encoding utf8NoBOM
    Write-Output "既定のデバイスを保存しました: $SaveBaseline"
}
$changedRoles = @()
if ($CompareBaseline) {
    $baseline = Get-Content -LiteralPath $CompareBaseline -Raw -Encoding utf8 | ConvertFrom-Json
    foreach ($entry in $defaults.GetEnumerator()) {
        $before = [string]$baseline.defaults.($entry.Key)
        if ($before -ne [string]$entry.Value) {
            $changedRoles += $entry.Key
            Write-Output ('変わった既定: {0}: {1} → {2}' -f $entry.Key, $before, $entry.Value)
        }
    }
    if ($changedRoles.Count -gt 0) {
        $exitCode = 5
    }
}
if ($ExpectCable -and -not ($cableInput -and $cableOutput)) {
    Write-Output 'CABLE Input / CABLE Output が見つかりません。VB-CABLE の導入と再起動が済んでいるか確認してください。'
    $exitCode = 6
}
Write-Output ('AUDIO_DEVICES cable_input={0} cable_output={1} cable_is_default={2} cable_is_render_default={3} changed_defaults={4} default_render={5} default_capture={6}' -f `
        $cableInput, $cableOutput, $cableIsDefault, $cableIsRenderDefault, ($changedRoles -join ','), $defaults['render.console'], $defaults['capture.console'])
exit $exitCode
