$ErrorActionPreference = "Stop"

$path = "C:\Users\61700\Documents\LD06 project\dashboard\index.html"
$backup = "C:\Users\61700\Documents\LD06 project\dashboard\index.backup-before-drone.html"
$nl = "`r`n"

if (-not (Test-Path -LiteralPath $backup)) {
    Copy-Item -LiteralPath $path -Destination $backup -Force
}

$content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
if ($content.Contains("droneApiBase")) {
    Write-Output "dashboard already updated"
    exit 0
}

$markerLatest = @'
      <div class="panel control-card">
        <h2>最近应答</h2>
'@

$droneCard = @'
      <div class="panel control-card">
        <h2>无人机任务</h2>
        <div class="field" style="margin-bottom: 12px;">
          <label>后端接口</label>
          <input v-model.trim="droneApiBase">
        </div>
        <div class="button-row">
          <button class="primary" @click="startDroneTask" :disabled="droneTask?.running">开始执行任务</button>
          <button class="danger" @click="emergencyLandDrone">紧急降落</button>
          <button @click="refreshDroneTaskStatus">刷新任务状态</button>
        </div>
        <div class="metric">
          <span>任务状态</span>
          <strong><span class="level" :class="droneTaskLevelClass">{{ droneTaskLabel }}</span></strong>
        </div>
        <div class="metric">
          <span>当前阶段</span>
          <strong>{{ droneTask?.message || "-" }}</strong>
        </div>
        <div class="metric">
          <span>电量</span>
          <strong>{{ droneTask?.battery != null ? `${droneTask.battery}%` : "-" }}</strong>
        </div>
        <div class="metric">
          <span>D0 识别</span>
          <strong>{{ droneTask?.ai_result?.success === true ? "成功" : (droneTask?.ai_result ? "未识别" : "-") }}</strong>
        </div>
      </div>

'@

$content = $content.Replace($markerLatest, $droneCard + $markerLatest)

$content = $content.Replace(
    "            weatherEnabled: true,",
    "            weatherEnabled: true,$nl            droneApiBase: `"http://127.0.0.1:5055`",$nl            droneTask: null,$nl            droneTaskPolling: null,"
)

$latestStatusComputed = @'
          latestStatusText() {
            return this.latestStatus ? JSON.stringify(this.latestStatus, null, 2) : "-";
          },
'@

$droneComputed = @'
          latestStatusText() {
            return this.latestStatus ? JSON.stringify(this.latestStatus, null, 2) : "-";
          },
          droneTaskLabel() {
            if (!this.droneTask) return "未连接";
            if (this.droneTask.running) return "执行中";
            if (this.droneTask.success === true) return "完成";
            if (this.droneTask.success === false) return "失败";
            return this.droneTask.phase || "待命";
          },
          droneTaskLevelClass() {
            if (!this.droneTask) return "";
            if (this.droneTask.running) return "caution";
            if (this.droneTask.success === true) return "safe";
            if (this.droneTask.success === false) return "danger";
            return "";
          },
'@

$content = $content.Replace($latestStatusComputed, $droneComputed)

$droneMethods = @'
          async callDroneApi(path, options = {}) {
            const response = await fetch(`${this.droneApiBase}${path}`, {
              headers: { "Content-Type": "application/json" },
              ...options
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok && response.status !== 409) {
              throw new Error(data.error || data.message || `HTTP ${response.status}`);
            }
            return data;
          },
          addDroneLog(topic, payload) {
            this.logs.unshift({
              id: ++this.logCounter,
              time: new Date().toLocaleTimeString(),
              topic,
              payload: JSON.stringify(payload, null, 2)
            });
            this.logs = this.logs.slice(0, 60);
          },
          async refreshDroneTaskStatus() {
            try {
              this.droneTask = await this.callDroneApi("/api/drone/status");
            } catch (error) {
              this.errorMessage = `无人机后端连接失败：${error.message || error}`;
            }
          },
          startDroneTaskPolling() {
            if (this.droneTaskPolling) return;
            this.droneTaskPolling = window.setInterval(async () => {
              await this.refreshDroneTaskStatus();
              if (this.droneTask && !this.droneTask.running) {
                window.clearInterval(this.droneTaskPolling);
                this.droneTaskPolling = null;
              }
            }, 1000);
          },
          async startDroneTask() {
            this.errorMessage = "";
            try {
              this.droneTask = await this.callDroneApi("/api/drone/start-task", { method: "POST" });
              this.addDroneLog("drone/start-task", this.droneTask);
              this.startDroneTaskPolling();
            } catch (error) {
              this.errorMessage = `无人机任务启动失败：${error.message || error}`;
            }
          },
          async emergencyLandDrone() {
            this.errorMessage = "";
            try {
              this.droneTask = await this.callDroneApi("/api/drone/emergency-land", { method: "POST" });
              this.addDroneLog("drone/emergency-land", this.droneTask);
              this.startDroneTaskPolling();
            } catch (error) {
              this.errorMessage = `紧急降落失败：${error.message || error}`;
            }
          },

'@

$content = $content.Replace("          publishCommand(command) {", $droneMethods + "          publishCommand(command) {")

$content = [regex]::Replace(
    $content,
    'mounted\(\)\s*\{\s*this\.connect\(\);\s*\}',
    "mounted() {$nl          this.connect();$nl          this.refreshDroneTaskStatus();$nl        }"
)

[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)
Write-Output "dashboard updated"
