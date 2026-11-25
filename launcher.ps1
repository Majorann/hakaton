param(
    [Parameter(Mandatory = $true)][ValidateSet('Setup', 'Start', 'Verify')][string]$Mode,
    [string]$InputJson
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$ollamaUri = 'http://127.0.0.1:11434'
$appUri = 'http://127.0.0.1:8765'
$defaultModel = 'qwen3.5:4b'
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $projectKey = [BitConverter]::ToString($sha.ComputeHash(
        [Text.Encoding]::UTF8.GetBytes($project.ToUpperInvariant())
    )).Replace('-', '').Substring(0, 16)
} finally {
    $sha.Dispose()
}
$runtime = Join-Path (Join-Path $env:LOCALAPPDATA 'TransparentVacancy') $projectKey

function Find-Python {
    $candidates = @(Get-Command python.exe, py.exe -CommandType Application -All -ErrorAction SilentlyContinue)
    foreach ($command in $candidates) {
        if ($command.Source -like '*\Microsoft\WindowsApps\*') { continue }
        $prefix = @()
        if ($command.Name -eq 'py.exe') { $prefix = @('-3') }
        try {
            $result = & $command.Source @prefix -X utf8 -c 'import sys; print(sys.executable) if sys.version_info >= (3, 11) else sys.exit(1)' 2>$null
        } catch { continue }
        if ($LASTEXITCODE -eq 0 -and $result -and (Test-Path -LiteralPath $result -PathType Leaf)) {
            return [string]$result
        }
    }
    # Установленный для пользователя Python может работать ещё до появления в PATH.
    $installRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path -LiteralPath $installRoot -PathType Container) {
        foreach ($directory in Get-ChildItem -LiteralPath $installRoot -Directory -Filter 'Python3*' | Sort-Object Name -Descending) {
            $candidate = Join-Path $directory.FullName 'python.exe'
            if (!(Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
            try { & $candidate -X utf8 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>$null }
            catch { continue }
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
}

function Find-Ollama {
    $command = Get-Command ollama.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
}

function Offer-Install($name, $id, $url) {
    $answer = (Read-Host "$name не найден. Установить сейчас? [д/Н]").Trim().ToLowerInvariant()
    if ($answer -notin @('д', 'да', 'y', 'yes')) {
        throw "Установите $name и снова запустите setup.bat. Официальный сайт $url"
    }
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        & winget.exe install --id $id --exact --source winget
        if ($LASTEXITCODE -eq 0) {
            $paths = @($env:Path)
            foreach ($scope in @('Machine', 'User')) {
                $paths += [Environment]::GetEnvironmentVariable('Path', $scope)
            }
            $env:Path = $paths -join ';'
            return
        }
        Write-Host 'WinGet не завершил установку. Открываем официальный сайт.'
    }
    Start-Process $url
    throw "Завершите установку $name в открывшемся окне и повторно запустите setup.bat."
}

function Read-LocalJson($url, $headers = @{}, $timeout = 2, $body) {
    # К локальным службам нужно подключаться напрямую, даже при включённом системном прокси.
    $request = [Net.WebRequest]::Create($url)
    $request.Proxy = $null
    $request.Timeout = $timeout * 1000
    $request.ReadWriteTimeout = $timeout * 1000
    foreach ($name in $headers.Keys) { $request.Headers[$name] = $headers[$name] }
    try {
        if ($body) {
            $bytes = [Text.Encoding]::UTF8.GetBytes($body)
            $request.Method = 'POST'
            $request.ContentType = 'application/json'
            $request.ContentLength = $bytes.Length
            $stream = $request.GetRequestStream()
            try { $stream.Write($bytes, 0, $bytes.Length) }
            finally { $stream.Dispose() }
        }
        $response = $request.GetResponse()
        try {
            $reader = [IO.StreamReader]::new($response.GetResponseStream(), [Text.Encoding]::UTF8)
            try { return ($reader.ReadToEnd() | ConvertFrom-Json) }
            finally { $reader.Dispose() }
        } finally { $response.Dispose() }
    } catch [Net.WebException] {
        if ($_.Exception.Response) { $_.Exception.Response.Dispose() }
        return $null
    }
}

function Ensure-Ollama($ollama, $parallelArchive) {
    if ((Read-LocalJson "$ollamaUri/api/version").version) { return }
    if (!$ollama) { throw 'Ollama недоступна. Запустите setup.bat для установки.' }
    Write-Host 'Запускаем Ollama…'
    $numParallel = $env:OLLAMA_NUM_PARALLEL
    $maxLoadedModels = $env:OLLAMA_MAX_LOADED_MODELS
    try {
        if ($parallelArchive -and !$numParallel) { $env:OLLAMA_NUM_PARALLEL = '2' }
        if ($parallelArchive -and !$maxLoadedModels) { $env:OLLAMA_MAX_LOADED_MODELS = '2' }
        Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardError (Join-Path $runtime 'ollama.log')
    } finally {
        $env:OLLAMA_NUM_PARALLEL = $numParallel
        $env:OLLAMA_MAX_LOADED_MODELS = $maxLoadedModels
    }
    $deadline = [datetime]::UtcNow.AddSeconds(45)
    do {
        if ((Read-LocalJson "$ollamaUri/api/version").version) { return }
        Start-Sleep -Milliseconds 500
    } while ([datetime]::UtcNow -lt $deadline)
    throw "Ollama не ответила на порту 11434. Журнал $(Join-Path $runtime 'ollama.log')"
}

function Has-Model($model) {
    $response = Read-LocalJson "$ollamaUri/api/tags" -timeout 5
    if (!$response) { throw 'Не удалось получить список моделей Ollama. Проверьте, запущена ли Ollama.' }
    $name = if ($model -match ':[^/]+$') { $model } else { "${model}:latest" }
    return @($response.models | Where-Object { $_.name -eq $name -or $_.model -eq $name }).Count -gt 0
}

function Install-Model($ollama, $model) {
    if (Has-Model $model) {
        Write-Host "Модель $model уже скачана."
        return
    }
    Write-Host "Скачиваем $model. Ollama покажет прогресс загрузки."
    & $ollama pull $model
    if ($LASTEXITCODE -ne 0 -or !(Has-Model $model)) {
        throw "Не удалось скачать $model. Проверьте подключение к интернету и свободное место, затем повторите setup.bat."
    }
}

function Get-ProjectSettings($python) {
    $code = @'
import json
from vacancy_report import DEFAULT_CONFIG, read_config, report_path

config = read_config(DEFAULT_CONFIG)
print(json.dumps({
    "model": config["ollama"]["model"],
    "output": str(report_path(config, DEFAULT_CONFIG, "file").resolve()),
    "parallel_archive": bool(config.get("web", {}).get("followup_enabled") and config["ollama"].get("archive_model")),
}))
'@
    Push-Location -LiteralPath $project
    try {
        $result = $code | & $python -X utf8 -
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось прочитать config.toml. Проверьте настройки проекта.' }
        return ($result | ConvertFrom-Json)
    } finally { Pop-Location }
}

function Choose-Json {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object Windows.Forms.OpenFileDialog
    try {
        $dialog.Title = 'Выберите сохранённый ответ API в формате JSON'
        $dialog.Filter = 'JSON (*.json)|*.json|Все файлы (*.*)|*.*'
        if ($dialog.ShowDialog() -eq [Windows.Forms.DialogResult]::OK) { return $dialog.FileName }
    } finally { $dialog.Dispose() }
}

function Read-AppLink($accessFile) {
    if (!(Test-Path -LiteralPath $accessFile -PathType Leaf)) { return }
    $line = Get-Content -LiteralPath $accessFile -TotalCount 1 -ErrorAction SilentlyContinue
    if ($line -match '^http://127\.0\.0\.1:8765/#access=([A-Za-z0-9_-]{43})$') {
        $headers = @{Authorization = "Bearer $($Matches[1])"}
        $state = Read-LocalJson "$appUri/api/state" $headers
        if ($state -and $state.PSObject.Properties['revisions']) { return [string]$line }
    }
}

function Ensure-App($python) {
    $accessFile = Join-Path $runtime 'web-access.log'
    $errors = Join-Path $runtime 'web-errors.log'
    $link = Read-AppLink $accessFile
    if ($link) { return $link }
    if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Порт 8765 уже занят. Если сервер запущен вручную, откройте ссылку из его консоли или остановите его перед запуском start.bat.'
    }
    Write-Host 'Запускаем локальное приложение…'
    $process = Start-Process -FilePath $python -ArgumentList '-X', 'utf8', 'vacancy_web.py' -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput $accessFile -RedirectStandardError $errors -PassThru
    $deadline = [datetime]::UtcNow.AddSeconds(30)
    do {
        $link = Read-AppLink $accessFile
        if ($link) { return $link }
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 350
    } while ([datetime]::UtcNow -lt $deadline)
    if (!$process.HasExited) { $process.Kill() }
    throw "Приложение не запустилось. Журнал $errors"
}

function Test-ModelResponse($model) {
    Write-Host "Проверяем ответ модели $model…"
    $payload = @{
        model = $model
        messages = @(@{role = 'user'; content = 'Reply with the single word OK.'})
        think = $false
        stream = $false
        options = @{num_predict = 16}
    } | ConvertTo-Json -Depth 4 -Compress
    try {
        $response = Read-LocalJson "$ollamaUri/api/chat" -timeout 180 -body $payload
        if (!$response.message.content) { throw 'Пустой ответ' }
    } catch {
        throw "Модель $model не вернула тестовый ответ. $($_.Exception.Message)"
    }
}

try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    if ($Mode -eq 'Verify') {
        if (!$InputJson) { $InputJson = Choose-Json }
        if (!$InputJson) {
            Write-Host 'Выбор файла отменён.'
            exit 0
        }
        $inputFile = Get-Item -LiteralPath $InputJson -ErrorAction SilentlyContinue
        if (!$inputFile -or $inputFile.PSIsContainer) { throw "Входной файл не найден. $InputJson" }
    }

    $python = Find-Python
    if (!$python -and $Mode -eq 'Setup') {
        Offer-Install 'Python 3.11 или новее' 'Python.Python.3.12' 'https://www.python.org/downloads/windows/'
        $python = Find-Python
    }
    if (!$python) { throw 'Python 3.11 или новее не найден. Запустите setup.bat.' }
    if ($Mode -eq 'Setup') { & $python --version }

    $settings = Get-ProjectSettings $python
    $model = $settings.model
    if ($model -notmatch '^[a-zA-Z0-9_.:/-]+$' -or $model.StartsWith('-') -or $model -match '(:cloud|-cloud)$') {
        throw 'В config.toml должно быть указано имя локальной модели Ollama.'
    }
    $ollama = Find-Ollama
    if (!$ollama -and $Mode -eq 'Setup') {
        Offer-Install 'Ollama' 'Ollama.Ollama' 'https://ollama.com/download/windows'
        $ollama = Find-Ollama
        if (!$ollama) { throw 'Ollama не найдена после установки. Закройте это окно и повторите setup.bat.' }
    }

    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $mutex = [Threading.Mutex]::new($false, "Local\TransparentVacancy-$projectKey")
    try {
        try { $locked = $mutex.WaitOne([TimeSpan]::FromSeconds(60)) }
        catch [Threading.AbandonedMutexException] { $locked = $true }
        if (!$locked) { throw 'Дождитесь завершения настройки в другом окне и повторите запуск.' }
        try {
            Ensure-Ollama $ollama $settings.parallel_archive
            if ($Mode -eq 'Setup') {
                Write-Host 'Ollama доступна.'
                Write-Host "1. Использовать $defaultModel (скачать при необходимости)"
                Write-Host '2. Выбрать установленную локальную модель'
                $choice = (Read-Host 'Выберите вариант [1/2]').Trim()
                if ($choice -eq '1') {
                    $model = $defaultModel
                    Install-Model $ollama $model
                } elseif ($choice -eq '2') {
                    $response = Read-LocalJson "$ollamaUri/api/tags" -timeout 5
                    if (!$response) { throw 'Не удалось получить список моделей Ollama.' }
                    $models = @($response.models | ForEach-Object { $_.name } | Where-Object {
                        $_ -and $_ -match '^[a-zA-Z0-9_.:/-]+$' -and ! $_.StartsWith('-') -and $_ -notmatch '(:cloud|-cloud)$'
                    })
                    if (!$models.Count) { throw 'Установленные локальные модели не найдены. Повторите setup.bat и выберите вариант 1.' }
                    for ($i = 0; $i -lt $models.Count; $i++) { Write-Host "$($i + 1). $($models[$i])" }
                    $selection = 0
                    $answer = (Read-Host 'Введите номер модели').Trim()
                    if (![int]::TryParse($answer, [ref]$selection) -or $selection -lt 1 -or $selection -gt $models.Count) {
                        throw 'Неверный номер модели. Повторите setup.bat.'
                    }
                    $model = $models[$selection - 1]
                } else { throw 'Неверный вариант. Повторите setup.bat.' }
                Test-ModelResponse $model
                if ($model -ne $settings.model) {
                    $code = 'import sys, tomllib; from vacancy_report import DEFAULT_CONFIG, write_atomic; from vacancy_web import update_toml; text=DEFAULT_CONFIG.read_text(encoding="utf-8"); text=update_toml(text, "ollama", "model", sys.argv[1]); tomllib.loads(text); write_atomic(DEFAULT_CONFIG, text)'
                    Push-Location -LiteralPath $project
                    try {
                        $code | & $python -X utf8 - $model
                        if ($LASTEXITCODE -ne 0) { throw 'Не удалось сохранить выбранную модель в config.toml.' }
                    } finally { Pop-Location }
                }
            } elseif (!(Has-Model $model)) {
                throw "Модель $model не найдена. Запустите setup.bat."
            }
            if ($Mode -ne 'Verify') { $link = Ensure-App $python }
        } finally { $mutex.ReleaseMutex() }
    } finally { $mutex.Dispose() }

    if ($Mode -eq 'Setup') {
        Write-Host 'Готово. Для работы откройте start.bat.'
    } elseif ($Mode -eq 'Start') {
        Start-Process $link
    } else {
        Write-Host "Проверяем $($inputFile.Name)…"
        & $python -X utf8 (Join-Path $project 'vacancy_report.py') file $inputFile.FullName
        $checkExit = $LASTEXITCODE
        if ($checkExit -eq 2) { Write-Host 'Часть вакансий не проверена. Ошибки перечислены в отчёте.' }
        elseif ($checkExit -ne 0) { throw 'Проверка завершилась с ошибкой. Подробности указаны выше.' }
        Write-Host "Проверочный отчёт сохранён в $($settings.output)"
        exit $checkExit
    }
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
