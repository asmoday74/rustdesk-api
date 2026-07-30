# Конфигурация
$source = "\\sve\Svetopaper.exe"
$destination = "C:\Temp\Svetopaper.exe"
$regPathNEW = "HKLM:\SOFTWARE\Classes\.svetopaper support\DefaultIcon"
$serviceName = 'Svetopaper Support'

# Проверка прав администратора
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process PowerShell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"cd '$pwd'; & '$PSCommandPath';`""
    exit
}


# Функция установки новой версии
function Install-NewVersion {
    
    if (Test-Path $regPathNEW) {Write-Output "Уже установлена"}
        else{
    
            Write-Output "Установка новой версии..."
    
            if (!(Test-Path "C:\Temp")) {
                New-Item -ItemType Directory -Force -Path "C:\Temp" | Out-Null
            }
    
            Copy-Item $source -Destination $destination
            Start-Process $destination --silent-install
            Start-Sleep -Seconds 5
            Remove-Item $destination -Force
         }
}

# Функция настройки службы
function Setup-Service {
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    
    if (-not $service) {
        Write-Output "Создание службы..."
        New-Service -Name $serviceName `
                    -BinaryPathName "C:\Program Files\Svetopaper support\Svetopaper support.exe --service" `
                    -DisplayName "Svetopaper Support Service" `
                    -StartupType Automatic
        Start-Sleep -Seconds 5
        $service = Get-Service -Name $serviceName
    }
    
    if ($service.Status -ne 'Running') {
        Write-Output "Запуск службы..."
        Start-Service $serviceName
        Start-Sleep -Seconds 5
    }
}

# Основной процесс
Remove-OldVersion
Install-NewVersion
Setup-Service


Write-Host "Готово!" -ForegroundColor Green


$cpu = (Get-WmiObject Win32_Processor).Name
if ([string]::IsNullOrEmpty($cpu)) { $cpu = "unknown" }

$hostname = $env:COMPUTERNAME
if ([string]::IsNullOrEmpty($hostname)) { $hostname = "unknown" }

$memoryGB = [math]::Round((Get-WmiObject Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 0)
$memory = "${memoryGB}GB"

$os = (Get-WmiObject Win32_OperatingSystem).Caption
if ([string]::IsNullOrEmpty($os)) { $os = "unknown" }

$UsrName = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI' | Select-Object LastLoggedOnUser).LastLoggedOnUser
$username = $UsrName.Split('\')[-1]
if ([string]::IsNullOrEmpty($username)) { $username = "unknown" }

$uuid =  (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Cryptography" -Name "MachineGuid").MachineGuid
$uuidBytes = [System.Text.Encoding]::UTF8.GetBytes($uuid)
$base64 = [Convert]::ToBase64String($uuidBytes)

$version = "1.4.6"


$body = @{
    uuid = $base64
    cpu = $cpu
    hostname = $hostname
    memory = $memory
    os = $os
    username = $username
    version = $version
}

$jsonBody = $body | ConvertTo-Json

$webRequest = [System.Net.WebRequest]::Create("http://rustdesk.svetopaper.com:21114/api/sysinfo")
$webRequest.Method = "POST"
$webRequest.ContentType = "application/json"

$encoding = [System.Text.Encoding]::UTF8
$data = $encoding.GetBytes($jsonBody)
$webRequest.ContentLength = $data.Length

$requestStream = $webRequest.GetRequestStream()
$requestStream.Write($data, 0, $data.Length)
$requestStream.Close()

$response = $webRequest.GetResponse()
$responseStream = $response.GetResponseStream()
$reader = New-Object System.IO.StreamReader($responseStream)
$result = $reader.ReadToEnd()

Write-Host "Ответ: $result" -ForegroundColor Green

