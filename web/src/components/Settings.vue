<template>
  <div class="space-y-6">
    <div v-if="showAdminSection" class="glass-card p-5">
      <div class="flex items-center justify-between gap-4 mb-4">
        <div>
          <h2 class="text-lg font-semibold text-white">管理员登录</h2>
          <p class="text-sm text-gray-400 mt-1">
            首次启动先在这里完成主号登录，系统会统一写入单个 state.json 文件，保存邮箱、session、workspace ID、workspace 名称；如果你走了密码登录，也会保留密码供主号 Codex 复用。
          </p>
        </div>
        <span
          class="min-w-[72px] px-3 py-1.5 rounded-full text-xs text-center whitespace-nowrap border"
          :class="adminConfigured
            ? 'bg-green-500/10 text-green-400 border-green-500/20'
            : adminBusy
              ? 'bg-yellow-500/10 text-yellow-300 border-yellow-500/20'
              : 'bg-gray-800 text-gray-400 border-gray-700'"
        >
          {{ adminConfigured ? '已配置' : adminBusy ? '登录中' : '未配置' }}
        </span>
      </div>

      <div v-if="message" class="mb-4 rounded-2xl px-4 py-3 text-sm border" :class="messageClass">
        {{ message }}
      </div>

      <div v-if="adminConfigured && !adminBusy" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
        <div class="px-3 py-3 bg-gray-800/60 border border-gray-800 rounded-lg">
          <div class="text-gray-500 mb-1">管理员邮箱</div>
          <div class="font-mono text-white break-all">{{ props.adminStatus?.email || '-' }}</div>
        </div>
        <div class="px-3 py-3 bg-gray-800/60 border border-gray-800 rounded-lg">
          <div class="text-gray-500 mb-1">Workspace ID</div>
          <div class="font-mono text-white break-all">{{ props.adminStatus?.account_id || '-' }}</div>
        </div>
        <div class="px-3 py-3 bg-gray-800/60 border border-gray-800 rounded-lg md:col-span-2">
          <div class="text-gray-500 mb-1">Workspace 名称</div>
          <div class="text-white">{{ props.adminStatus?.workspace_name || '未识别' }}</div>
        </div>
        <div class="px-3 py-3 bg-gray-800/60 border border-gray-800 rounded-lg md:col-span-2">
          <div class="text-gray-500 mb-1">Session Token</div>
          <div v-if="props.adminStatus?.session_present" class="text-green-400 text-xs">已配置</div>
          <div v-else class="space-y-2">
            <div class="text-amber-400 text-xs">未配置（Team 管理功能需要 session token）</div>
            <div class="text-gray-400 text-xs space-y-2">
              <div>获取方式：</div>
              <ol class="list-decimal list-inside space-y-1">
                <li>
                  在浏览器中打开
                  <a href="https://chatgpt.com" target="_blank" rel="noreferrer" class="text-blue-400 hover:underline">
                    chatgpt.com
                  </a>
                  并登录管理员账号
                </li>
                <li>按 F12 打开开发者工具 → Application → Cookies → chatgpt.com</li>
                <li>找到 <code class="bg-gray-800 px-1 rounded">__Secure-next-auth.session-token</code></li>
                <li>
                  如果有 <code class="bg-gray-800 px-1 rounded">.0</code> 和
                  <code class="bg-gray-800 px-1 rounded">.1</code> 两个，将值按顺序拼接在一起
                </li>
                <li>粘贴到下方输入框</li>
              </ol>
            </div>
            <div class="space-y-2">
              <input
                v-model.trim="sessionToken"
                type="password"
                placeholder="粘贴 session token"
                class="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-white font-mono focus:outline-none focus:border-blue-500"
              />
              <div class="flex justify-end">
                <button
                  @click="importSessionToken"
                  :disabled="submitting || !sessionEmail || !sessionToken"
                  class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded transition disabled:opacity-50"
                >
                  {{ submitting ? '校验中...' : '保存' }}
                </button>
              </div>
            </div>
          </div>
        </div>
        <div class="px-3 py-3 bg-gray-800/60 border border-gray-800 rounded-lg md:col-span-2">
          <div class="text-gray-500 mb-1">管理员密码</div>
          <div class="text-white">{{ props.adminStatus?.password_saved ? '已保存，可用于主号 Codex 登录' : '未保存' }}</div>
        </div>
      </div>

      <div v-if="!adminBusy" class="mt-4">
        <div v-if="!adminConfigured" class="space-y-4">
          <div class="flex flex-col sm:flex-row gap-3">
            <input
              v-model.trim="email"
              type="email"
              autocomplete="username"
              placeholder="输入主号邮箱"
              class="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <button
              @click="startLogin"
              :disabled="submitting || !email"
              class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50"
            >
              {{ submitting ? '提交中...' : '开始登录' }}
            </button>
          </div>

          <div class="border border-gray-800 rounded-xl p-4 bg-gray-800/30">
            <div class="text-sm font-medium text-white">或手动导入 session_token</div>
            <p class="text-xs text-gray-400 mt-1 mb-3">
              适合你已经在浏览器里拿到 <span class="font-mono">__Secure-next-auth.session-token</span> 的场景。系统会校验 token，并自动识别 workspace ID / 名称。
            </p>
            <div class="text-gray-400 text-xs space-y-2 mb-3">
              <div>获取方式：</div>
              <ol class="list-decimal list-inside space-y-1">
                <li>
                  在浏览器中打开
                  <a href="https://chatgpt.com" target="_blank" rel="noreferrer" class="text-blue-400 hover:underline">
                    chatgpt.com
                  </a>
                  并登录管理员账号
                </li>
                <li>按 F12 打开开发者工具 → Application → Cookies → chatgpt.com</li>
                <li>找到 <code class="bg-gray-800 px-1 rounded">__Secure-next-auth.session-token</code></li>
                <li>
                  如果有 <code class="bg-gray-800 px-1 rounded">.0</code> 和
                  <code class="bg-gray-800 px-1 rounded">.1</code> 两个，将值按顺序拼接在一起
                </li>
                <li>粘贴到下方输入框</li>
              </ol>
            </div>
            <div class="space-y-3">
              <input
                v-model.trim="sessionEmail"
                type="email"
                autocomplete="username"
                placeholder="输入主号邮箱"
                class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-cyan-500"
              />
              <textarea
                v-model.trim="sessionToken"
                rows="4"
                spellcheck="false"
                placeholder="粘贴完整 session_token"
                class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white font-mono focus:outline-none focus:border-cyan-500"
              ></textarea>
              <div class="flex justify-end">
                <button
                  @click="importSessionToken"
                  :disabled="submitting || !sessionEmail || !sessionToken"
                  class="px-4 py-2 bg-cyan-700 hover:bg-cyan-600 text-white text-sm rounded-lg transition disabled:opacity-50"
                >
                  {{ submitting ? '校验中...' : '导入 session_token' }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <div v-else-if="!codexBusy" class="flex flex-wrap gap-3">
          <button
            @click="startLogin"
            :disabled="submitting || syncingMain || deletingMainRemoteFiles || !email"
            class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ submitting ? '打开中...' : '重新登录管理员' }}
          </button>
          <button
            @click="loginMainCodex"
            :disabled="submitting || syncingMain || deletingMainRemoteFiles"
            class="px-4 py-2 bg-blue-700 hover:bg-blue-600 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ syncingMain && mainCodexSubmittingAction === 'login' ? '登录中...' : '登录主号 Codex' }}
          </button>
          <button
            @click="syncMainCodex"
            :disabled="submitting || syncingMain || deletingMainRemoteFiles"
            class="px-4 py-2 bg-cyan-700 hover:bg-cyan-600 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ syncingMain && mainCodexSubmittingAction === 'sync' ? '同步中...' : '同步主号 Codex 到已启用远端' }}
          </button>
          <button
            @click="deleteMainCodexFromRemoteFiles"
            :disabled="submitting || syncingMain || deletingMainRemoteFiles"
            class="px-4 py-2 bg-amber-700 hover:bg-amber-600 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ deletingMainRemoteFiles ? '删除中...' : '从已启用远端删除主号文件' }}
          </button>
          <button
            @click="logoutAdmin"
            :disabled="submitting || syncingMain || deletingMainRemoteFiles"
            class="px-4 py-2 bg-rose-700/80 hover:bg-rose-700 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ submitting ? '处理中...' : '清除登录态' }}
          </button>
        </div>
      </div>

      <div v-if="adminBusy" class="space-y-4">
        <div class="text-sm text-gray-300">
          当前邮箱: <span class="font-mono">{{ loginEmail || props.adminStatus?.login_email || props.adminStatus?.email || '-' }}</span>
        </div>

        <div v-if="props.adminStatus?.login_step === 'starting'" class="rounded-xl border border-blue-500/20 bg-blue-500/5 p-4 text-sm space-y-2">
          <div class="text-blue-300 font-medium">正在打开管理员登录页</div>
          <div class="text-gray-400 text-xs leading-relaxed">
            后端已经接管 Playwright 浏览器，正在等待 ChatGPT 登录页加载和步骤识别。页面较慢或触发风控时可能需要几分钟。
          </div>
          <div class="text-gray-500 text-xs">
            如果浏览器窗口已经可操作，可以手动完成当前步骤；待状态推进后再点击「重新识别登录步骤」。
          </div>
        </div>

        <div v-else-if="props.adminStatus?.login_step === 'email_required'" class="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 text-sm space-y-2">
          <div class="text-amber-300 font-medium">邮箱提交后页面未推进</div>
          <div class="text-gray-400 text-xs leading-relaxed">
            程序已尝试提交邮箱但页面仍停留在邮箱输入步骤。可能原因：按钮点击未生效、页面改版、网络延迟或风控拦截。
          </div>
          <div class="text-gray-500 text-xs">
            服务器部署可先点「打开远程浏览器窗口」直接接管 Playwright Chromium；操作后再点「重新识别登录步骤」。如果远程窗口不可用，再用「查看页面快照」确认页面状态。
          </div>
        </div>

        <div v-else-if="props.adminStatus?.login_step === 'password_required'" class="flex flex-col sm:flex-row gap-3">
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            placeholder="输入主号密码"
            :disabled="submitting"
            class="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
          />
          <button
            @click="submitPassword"
            :disabled="submitting || !password"
            class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ submitting ? '提交中...' : '提交密码' }}
          </button>
        </div>

        <div v-else-if="props.adminStatus?.login_step === 'code_required'" class="flex flex-col sm:flex-row gap-3">
          <input
            v-model.trim="code"
            type="text"
            inputmode="numeric"
            autocomplete="one-time-code"
            placeholder="输入邮箱验证码"
            :disabled="submitting"
            class="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
          />
          <button
            @click="submitCode"
            :disabled="submitting || !code"
            class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50 disabled:bg-gray-700 disabled:hover:bg-gray-700"
          >
            {{ submitting ? '提交中...' : '提交验证码' }}
          </button>
        </div>

        <div v-else-if="props.adminStatus?.login_step === 'workspace_required'" class="space-y-3">
          <div class="text-sm text-gray-300">
            请选择要进入的组织 / workspace
          </div>
          <select
            v-model="workspaceOptionId"
            :disabled="submitting"
            class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
          >
            <option disabled value="">请选择组织</option>
            <option
              v-for="opt in props.adminStatus?.workspace_options || []"
              :key="opt.id"
              :value="opt.id"
            >
              {{ opt.label }}{{ opt.kind === 'fallback' ? ' (可能是个人/免费)' : '' }}
            </option>
          </select>
          <button
            @click="submitWorkspace"
            :disabled="submitting || !workspaceOptionId"
            class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50 disabled:bg-gray-700 disabled:hover:bg-gray-700"
          >
            {{ submitting ? '提交中...' : '确认组织选择' }}
          </button>
        </div>

        <div v-if="submitting && adminSubmittingHint" class="text-xs text-blue-300">
          {{ adminSubmittingHint }}
        </div>

        <div class="flex justify-end">
          <button
            @click="cancelLogin"
            :disabled="submitting"
            class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 rounded-lg border border-gray-700 transition disabled:opacity-50"
          >
            取消登录
          </button>
        </div>

        <!-- 排障工具 -->
        <div class="mt-4 border-t border-gray-800 pt-4">
          <div class="flex items-center justify-between mb-3">
            <div class="text-sm font-medium text-white">排障工具</div>
            <span class="text-xs text-gray-500">手动触发，不会自动调用</span>
          </div>

          <div class="flex flex-wrap gap-2 mb-3">
            <button
              @click="inspectPage"
              :disabled="inspecting || submitting"
              class="px-3 py-1.5 text-xs rounded-lg border transition disabled:opacity-50"
              :class="inspecting
                ? 'bg-gray-800 text-gray-500 border-gray-700 cursor-not-allowed'
                : 'bg-indigo-600/10 text-indigo-400 border-indigo-500/30 hover:bg-indigo-600/20'"
            >
              {{ inspecting ? '采集中...' : '查看页面快照' }}
            </button>
            <button
              @click="refreshStep"
              :disabled="refreshing || submitting"
              class="px-3 py-1.5 text-xs rounded-lg border transition disabled:opacity-50"
              :class="refreshing
                ? 'bg-gray-800 text-gray-500 border-gray-700 cursor-not-allowed'
                : 'bg-emerald-600/10 text-emerald-400 border-emerald-500/30 hover:bg-emerald-600/20'"
            >
              {{ refreshing ? '识别中...' : '重新识别登录步骤' }}
            </button>
            <button
              @click="analyzeStuck"
              :disabled="analyzing || submitting"
              class="px-3 py-1.5 text-xs rounded-lg border transition disabled:opacity-50"
              :class="analyzing
                ? 'bg-gray-800 text-gray-500 border-gray-700 cursor-not-allowed'
                : 'bg-amber-600/10 text-amber-400 border-amber-500/30 hover:bg-amber-600/20'"
            >
              {{ analyzing ? '分析中...' : 'AI 分析卡住原因' }}
            </button>
            <button
              @click="loadScreenshots"
              :disabled="screenshotsLoading"
              class="px-3 py-1.5 text-xs rounded-lg border transition disabled:opacity-50"
              :class="screenshotsLoading
                ? 'bg-gray-800 text-gray-500 border-gray-700 cursor-not-allowed'
                : 'bg-cyan-600/10 text-cyan-400 border-cyan-500/30 hover:bg-cyan-600/20'"
            >
              {{ screenshotsLoading ? '加载中...' : '查看截图列表' }}
            </button>
            <button
              @click="openDesktop"
              :disabled="desktopLoading"
              class="px-3 py-1.5 text-xs rounded-lg border transition disabled:opacity-50"
              :class="desktopLoading
                ? 'bg-gray-800 text-gray-500 border-gray-700 cursor-not-allowed'
                : 'bg-sky-600/10 text-sky-400 border-sky-500/30 hover:bg-sky-600/20'"
            >
              {{ desktopLoading ? '打开中...' : '打开远程浏览器窗口' }}
            </button>
          </div>

          <div v-if="desktopStatus" class="mb-3 rounded-xl border border-sky-500/20 bg-sky-500/5 p-3 text-xs text-sky-100">
            <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
              <div>
                <span class="text-sky-300">远程窗口:</span>
                <span class="text-gray-300">{{ desktopStatus.detail || (desktopStatus.enabled ? '可用' : '不可用') }}</span>
                <span v-if="desktopStatus.display" class="ml-2 text-gray-500">DISPLAY={{ desktopStatus.display }}</span>
              </div>
              <a
                v-if="desktopStatus.url"
                :href="desktopStatus.url"
                target="_blank"
                rel="noopener noreferrer"
                class="text-sky-300 hover:underline"
              >
                重新打开
              </a>
            </div>
          </div>

          <!-- 页面快照结果 -->
          <div v-if="snapshot" class="mb-3 rounded-xl border border-gray-800 bg-gray-800/40 p-4 text-xs space-y-2">
            <div class="flex items-center justify-between">
              <div class="text-sm font-medium text-white">页面快照</div>
              <button @click="snapshot = null" class="text-gray-500 hover:text-gray-300">&times;</button>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <div><span class="text-gray-500">URL:</span> <span class="text-gray-300 break-all">{{ snapshot.url || '-' }}</span></div>
              <div><span class="text-gray-500">标题:</span> <span class="text-gray-300">{{ snapshot.title || '-' }}</span></div>
              <div><span class="text-gray-500">检测步骤:</span> <span class="text-gray-300">{{ snapshot.step || '-' }}</span></div>
              <div><span class="text-gray-500">截图:</span>
                <a v-if="snapshot.screenshot" :href="screenshotUrl(snapshot.screenshot)" target="_blank" class="text-blue-400 hover:underline">{{ snapshot.screenshot }}</a>
                <span v-else class="text-gray-500">无</span>
              </div>
            </div>
            <div v-if="snapshot.body" class="mt-2">
              <div class="text-gray-500 mb-1">页面文本摘要:</div>
              <pre class="text-gray-400 whitespace-pre-wrap break-all max-h-32 overflow-y-auto bg-gray-900 rounded p-2">{{ snapshot.body }}</pre>
            </div>
            <div v-if="snapshot.dom" class="mt-2">
              <div class="text-gray-500 mb-1">可见输入框 ({{ snapshot.dom.inputs?.filter(i => i.visible).length || 0 }}):</div>
              <div v-for="(inp, idx) in (snapshot.dom.inputs || []).filter(i => i.visible)" :key="'inp-'+idx" class="text-gray-400 ml-2">
                &lt;{{ inp.tag }}&gt; type={{ inp.type || 'text' }} name={{ inp.name || '-' }} placeholder="{{ inp.placeholder || '-' }}" {{ inp.disabled ? '[disabled]' : '' }}
              </div>
              <div class="text-gray-500 mt-2 mb-1">可见按钮 ({{ snapshot.dom.buttons?.filter(b => b.visible).length || 0 }}):</div>
              <div v-for="(btn, idx) in (snapshot.dom.buttons || []).filter(b => b.visible)" :key="'btn-'+idx" class="text-gray-400 ml-2">
                "{{ btn.text || btn.ariaLabel || '-' }}" {{ btn.disabled ? '[disabled]' : '' }}
              </div>
            </div>
          </div>

          <!-- AI 分析结果 -->
          <div v-if="analysis" class="mb-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 text-xs space-y-2">
            <div class="flex items-center justify-between">
              <div class="text-sm font-medium text-amber-300">
                AI 分析结果
                <span class="ml-2 text-xs text-gray-500">(引擎: {{ analysis.engine || '-' }})</span>
              </div>
              <button @click="analysis = null" class="text-gray-500 hover:text-gray-300">&times;</button>
            </div>
            <div v-if="analysis.analysis" class="text-gray-300 whitespace-pre-wrap leading-relaxed">{{ analysis.analysis }}</div>
            <div v-if="analysis.local_analysis" class="mt-2 border-t border-amber-500/10 pt-2">
              <div class="text-gray-500 mb-1">本地规则分析:</div>
              <div class="text-gray-400">{{ analysis.local_analysis?.summary || '-' }}</div>
              <div v-if="analysis.local_analysis?.findings?.length" class="mt-1 space-y-1">
                <div v-for="(f, idx) in analysis.local_analysis.findings" :key="'f-'+idx" class="text-gray-400">• {{ f }}</div>
              </div>
              <div v-if="analysis.local_analysis?.recommendations?.length" class="mt-1 space-y-1">
                <div class="text-gray-500">建议:</div>
                <div v-for="(r, idx) in analysis.local_analysis.recommendations" :key="'r-'+idx" class="text-emerald-400">→ {{ r }}</div>
              </div>
            </div>
            <div v-if="analysis.external_error" class="text-red-400 text-xs mt-1">外部 AI 调用失败: {{ analysis.external_error }}</div>
          </div>

          <!-- 截图列表 -->
          <div v-if="screenshots && screenshots.length" class="rounded-xl border border-gray-800 bg-gray-800/40 p-4 text-xs">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-medium text-white">截图列表 ({{ screenshots.length }})</div>
              <button @click="screenshots = null" class="text-gray-500 hover:text-gray-300">&times;</button>
            </div>
            <div class="max-h-48 overflow-y-auto space-y-1">
              <div v-for="(ss, idx) in screenshots" :key="'ss-'+idx" class="flex items-center justify-between gap-3 py-1">
                <div class="flex items-center gap-2 min-w-0">
                  <span class="text-gray-600 shrink-0">#{{ idx + 1 }}</span>
                  <a :href="screenshotUrl(ss)" target="_blank" class="text-blue-400 hover:underline truncate">{{ ss.name }}</a>
                  <span class="text-gray-600 shrink-0">{{ formatSize(ss.size) }}</span>
                </div>
                <span class="text-gray-600 shrink-0">{{ formatTime(ss.modified_at) }}</span>
              </div>
            </div>
          </div>
          <div v-else-if="screenshots && !screenshots.length" class="text-xs text-gray-500">
            暂无截图
          </div>
        </div>
      </div>

      <div v-if="codexBusy" class="mt-4 space-y-4 border-t border-gray-800 pt-4">
        <div class="text-sm text-gray-300">
          主号 Codex{{ codexActionLabel }}继续中
        </div>

        <div v-if="props.codexStatus?.step === 'password_required'" class="flex flex-col sm:flex-row gap-3">
          <input
            v-model="codexPassword"
            type="password"
            autocomplete="current-password"
            placeholder="输入主号密码"
            :disabled="syncingMain"
            class="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
          />
          <button
            @click="submitMainCodexPassword"
            :disabled="syncingMain || !codexPassword"
            class="px-4 py-2 bg-cyan-700 hover:bg-cyan-600 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ syncingMain ? '提交中...' : '提交密码' }}
          </button>
        </div>

        <div v-else-if="props.codexStatus?.step === 'code_required'" class="flex flex-col sm:flex-row gap-3">
          <input
            v-model.trim="codexCode"
            type="text"
            inputmode="numeric"
            autocomplete="one-time-code"
            placeholder="输入主号 Codex 验证码"
            :disabled="syncingMain"
            class="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
          />
          <button
            @click="submitMainCodexCode"
            :disabled="syncingMain || !codexCode"
            class="px-4 py-2 bg-cyan-700 hover:bg-cyan-600 text-white text-sm rounded-lg transition disabled:opacity-50"
          >
            {{ syncingMain ? '提交中...' : '提交验证码' }}
          </button>
        </div>

        <div v-if="syncingMain && codexSubmittingHint" class="text-xs text-cyan-300">
          {{ codexSubmittingHint }}
        </div>

        <div class="flex justify-end">
          <button
            @click="cancelMainCodexSync"
            :disabled="syncingMain"
            class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 rounded-lg border border-gray-700 transition disabled:opacity-50"
          >
            取消主号 Codex 登录
          </button>
        </div>
      </div>
    </div>

    <div v-if="showAutoCheckSection" class="glass-card p-5">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold text-white">巡检设置</h2>
        <span v-if="saved" class="text-xs text-green-400 transition">已保存</span>
      </div>

      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-6">
        <div>
          <label class="block text-sm text-gray-400 mb-1">巡检间隔</label>
          <div class="flex items-center gap-2">
            <input v-model.number="form.interval" type="number" min="1"
              class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500" />
            <span class="text-sm text-gray-500 shrink-0">分钟</span>
          </div>
        </div>
        <div>
          <label class="block text-sm text-gray-400 mb-1">总 seat 数</label>
          <div class="flex items-center gap-2">
            <input v-model.number="form.target_seats" type="number" min="1"
              class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500" />
            <span class="text-sm text-gray-500 shrink-0">个</span>
          </div>
        </div>
        <div>
          <label class="block text-sm text-gray-400 mb-1">额度阈值</label>
          <div class="flex items-center gap-2">
            <input v-model.number="form.threshold" type="number" min="1" max="100"
              class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500" />
            <span class="text-sm text-gray-500 shrink-0">%</span>
          </div>
        </div>
        <div>
          <label class="block text-sm text-gray-400 mb-1">触发账号数</label>
          <div class="flex items-center gap-2">
            <input v-model.number="form.min_low" type="number" min="1"
              class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500" />
            <span class="text-sm text-gray-500 shrink-0">个</span>
          </div>
        </div>
        <div>
          <label class="block text-sm text-gray-400 mb-1">手机号验证自动重试</label>
          <select
            v-model="form.retry_add_phone"
            class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
          >
            <option :value="true">开启</option>
            <option :value="false">关闭</option>
          </select>
        </div>
        <div>
          <label class="block text-sm text-gray-400 mb-1">手机号验证最大重试</label>
          <div class="flex items-center gap-2">
            <input
              v-model.number="form.add_phone_max_retries"
              type="number"
              min="1"
              class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <span class="text-sm text-gray-500 shrink-0">次</span>
          </div>
        </div>
      </div>

      <div class="mt-3 flex items-center justify-between gap-3">
        <p class="text-xs text-gray-500">
          每 {{ form.interval }} 分钟检查一次，按 Team 总 seat {{ form.target_seats }} 个做自动轮转 / 补位判断；
          {{ form.min_low }} 个以上账号剩余低于 {{ form.threshold }}% 时自动轮转；
          <span v-if="form.target_seats === 2">seat=2 时会对低额度子号启用 best-effort 预切换，若满员无法先加新号则自动回退到先移后补；</span>
          add_phone {{ form.retry_add_phone ? `开启自动重试（最多 ${form.add_phone_max_retries} 次）` : '保持人工处理' }}
        </p>
        <button @click="save" :disabled="saving"
          class="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition disabled:opacity-50">
          {{ saving ? '保存中...' : '保存' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted } from 'vue'
import { api, authorizedApiUrl } from '../api.js'

const props = defineProps({
  adminStatus: {
    type: Object,
    default: null,
  },
  codexStatus: {
    type: Object,
    default: null,
  },
  section: {
    type: String,
    default: 'all',
  },
})

const emit = defineEmits(['refresh', 'admin-progress'])

const form = ref({ interval: 5, target_seats: 5, threshold: 10, min_low: 2, retry_add_phone: true, add_phone_max_retries: 3 })
const saving = ref(false)
const saved = ref(false)

const email = ref('')
const sessionEmail = ref('')
const sessionToken = ref('')
const password = ref('')
const code = ref('')
const workspaceOptionId = ref('')
const loginEmail = ref('')
const codexPassword = ref('')
const codexCode = ref('')
const submitting = ref(false)
const syncingMain = ref(false)
const mainCodexSubmittingAction = ref('')
const deletingMainRemoteFiles = ref(false)
const message = ref('')
const messageClass = ref('')
const adminSubmittingHint = ref('')
const codexSubmittingHint = ref('')

// 排障工具状态
const inspecting = ref(false)
const refreshing = ref(false)
const analyzing = ref(false)
const screenshotsLoading = ref(false)
const desktopLoading = ref(false)
const snapshot = ref(null)
const analysis = ref(null)
const screenshots = ref(null)
const desktopStatus = ref(null)

const adminConfigured = computed(() => !!props.adminStatus?.configured)
const adminBusy = computed(() => !!props.adminStatus?.login_in_progress)
const codexBusy = computed(() => !!props.codexStatus?.in_progress)
const codexActionLabel = computed(() => props.codexStatus?.action === 'sync' ? '同步' : '登录')
const showAdminSection = computed(() => props.section !== 'auto-check')
const showAutoCheckSection = computed(() => props.section !== 'admin')

watch(
  () => props.adminStatus,
  (next) => {
    if (next?.configured && next.email) {
      email.value = next.email
      sessionEmail.value = next.email
    }
    if (!next?.login_in_progress) {
      password.value = ''
      code.value = ''
      workspaceOptionId.value = ''
      adminSubmittingHint.value = ''
      loginEmail.value = next?.email || loginEmail.value
    }
    if (next?.login_step === 'workspace_required' && !workspaceOptionId.value) {
      const preferred = next?.workspace_options?.find(opt => opt.kind === 'preferred')
      workspaceOptionId.value = preferred?.id || next?.workspace_options?.[0]?.id || ''
    }
  },
  { immediate: true },
)

watch(
  () => props.codexStatus,
  (next) => {
    if (!next?.in_progress) {
      codexPassword.value = ''
      codexCode.value = ''
      codexSubmittingHint.value = ''
    }
  },
  { immediate: true },
)

onMounted(async () => {
  if (showAutoCheckSection.value) {
    await loadAutoCheckConfig()
  }
})

function setMessage(text, type = 'success') {
  message.value = text
  messageClass.value = type === 'success'
    ? 'bg-green-500/10 text-green-400 border-green-500/20'
    : 'bg-red-500/10 text-red-400 border-red-500/20'
  window.clearTimeout(setMessage._timer)
  setMessage._timer = window.setTimeout(() => {
    message.value = ''
  }, 8000)
}

async function loadAutoCheckConfig() {
  try {
    const cfg = await api.getAutoCheckConfig()
    form.value = {
      interval: Math.round(cfg.interval / 60),
      target_seats: cfg.target_seats ?? 5,
      threshold: cfg.threshold,
      min_low: cfg.min_low,
      retry_add_phone: cfg.retry_add_phone ?? true,
      add_phone_max_retries: cfg.add_phone_max_retries ?? 3,
    }
  } catch (e) {
    console.error('加载巡检配置失败:', e)
  }
}

async function startLogin() {
  submitting.value = true
  adminSubmittingHint.value = '正在打开管理员登录页...'
  try {
    loginEmail.value = email.value
    const startPromise = api.startAdminLogin(email.value)
    emit('admin-progress')
    const result = await startPromise
    setMessage(result.status === 'completed' ? '管理员登录完成' : '已进入下一步登录流程')
    emit('admin-progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
    adminSubmittingHint.value = ''
  }
}

async function importSessionToken() {
  submitting.value = true
  adminSubmittingHint.value = '正在校验 session_token 并识别 workspace...'
  try {
    loginEmail.value = sessionEmail.value
    const result = await api.submitAdminSession(sessionEmail.value, sessionToken.value)
    sessionToken.value = ''
    setMessage(result.status === 'completed' ? 'session_token 导入成功' : 'session_token 已提交')
    emit('refresh')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
    adminSubmittingHint.value = ''
  }
}

async function submitPassword() {
  submitting.value = true
  adminSubmittingHint.value = '密码已提交，正在等待登录页响应...'
  try {
    const result = await api.submitAdminPassword(password.value)
    setMessage(result.status === 'completed' ? '管理员登录完成' : '密码已提交，请继续下一步')
    emit('admin-progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
    adminSubmittingHint.value = ''
  }
}

async function submitCode() {
  submitting.value = true
  adminSubmittingHint.value = '验证码已提交，正在等待登录页响应，通常需要 5 到 10 秒...'
  try {
    const result = await api.submitAdminCode(code.value)
    setMessage(result.status === 'completed' ? '管理员登录完成' : '验证码已提交，请继续下一步')
    emit('admin-progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
    adminSubmittingHint.value = ''
  }
}

async function submitWorkspace() {
  submitting.value = true
  adminSubmittingHint.value = '组织选择已提交，正在等待登录页响应...'
  try {
    const result = await api.submitAdminWorkspace(workspaceOptionId.value)
    setMessage(result.status === 'completed' ? '管理员登录完成' : '组织选择已提交，请继续下一步')
    emit('admin-progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
    adminSubmittingHint.value = ''
  }
}

async function cancelLogin() {
  submitting.value = true
  try {
    await api.cancelAdminLogin()
    password.value = ''
    code.value = ''
    snapshot.value = null
    analysis.value = null
    screenshots.value = null
    desktopStatus.value = null
    setMessage('管理员登录已取消')
    emit('refresh')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
  }
}

async function inspectPage() {
  inspecting.value = true
  try {
    snapshot.value = await api.inspectAdminLogin()
  } catch (e) {
    setMessage('获取页面快照失败: ' + e.message, 'error')
  } finally {
    inspecting.value = false
  }
}

async function refreshStep() {
  refreshing.value = true
  try {
    const result = await api.refreshAdminLogin()
    if (result.status === 'completed') {
      setMessage('管理员登录完成')
      emit('refresh')
    } else {
      setMessage('已重新识别登录步骤: ' + (result.status || 'unknown'))
      emit('admin-progress')
    }
  } catch (e) {
    setMessage('重新识别失败: ' + e.message, 'error')
  } finally {
    refreshing.value = false
  }
}

async function analyzeStuck() {
  analyzing.value = true
  try {
    analysis.value = await api.analyzeAdminLogin()
  } catch (e) {
    setMessage('AI 分析失败: ' + e.message, 'error')
  } finally {
    analyzing.value = false
  }
}

async function loadScreenshots() {
  screenshotsLoading.value = true
  try {
    const result = await api.getScreenshots(60)
    screenshots.value = result.items || []
  } catch (e) {
    setMessage('加载截图列表失败: ' + e.message, 'error')
  } finally {
    screenshotsLoading.value = false
  }
}

async function openDesktop() {
  desktopLoading.value = true
  try {
    const result = await api.getDesktopStatus()
    desktopStatus.value = result
    if (!result.enabled || !result.url) {
      setMessage(result.detail || '远程浏览器窗口未启用', 'error')
      return
    }
    if (!result.vnc_ready) {
      setMessage(result.detail || '远程窗口服务还未就绪，已尝试打开页面', 'error')
    }
    window.open(result.url, '_blank', 'noopener,noreferrer')
  } catch (e) {
    setMessage('打开远程浏览器窗口失败: ' + e.message, 'error')
  } finally {
    desktopLoading.value = false
  }
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

function formatTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  return d.toLocaleString()
}

function screenshotUrl(item) {
  const path = typeof item === 'string'
    ? `/screenshots/${encodeURIComponent(item)}`
    : item?.url || `/screenshots/${encodeURIComponent(item?.name || '')}`
  return authorizedApiUrl(path)
}

async function logoutAdmin() {
  submitting.value = true
  try {
    await api.logoutAdmin()
    password.value = ''
    code.value = ''
    setMessage('管理员登录态已清除')
    emit('refresh')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    submitting.value = false
  }
}

async function loginMainCodex() {
  syncingMain.value = true
  mainCodexSubmittingAction.value = 'login'
  codexSubmittingHint.value = '正在打开主号 Codex 登录页...'
  try {
    const result = await api.startMainCodexLogin()
    setMessage(result.status === 'completed' ? (result.message || '主号 Codex 已登录') : '主号 Codex 登录进入下一步')
    emit('admin-progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    syncingMain.value = false
    mainCodexSubmittingAction.value = ''
    codexSubmittingHint.value = ''
  }
}

async function syncMainCodex() {
  syncingMain.value = true
  mainCodexSubmittingAction.value = 'sync'
  codexSubmittingHint.value = '正在打开主号 Codex 登录页...'
  try {
    const result = await api.startMainCodexSync()
    setMessage(result.status === 'completed' ? (result.message || '主号 Codex 已同步') : '主号 Codex 登录进入下一步')
    emit('admin-progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    syncingMain.value = false
    mainCodexSubmittingAction.value = ''
    codexSubmittingHint.value = ''
  }
}

async function submitMainCodexPassword() {
  syncingMain.value = true
  mainCodexSubmittingAction.value = props.codexStatus?.action || 'login'
  codexSubmittingHint.value = '密码已提交，正在等待主号 Codex 登录页响应...'
  try {
    const result = await api.submitMainCodexPassword(codexPassword.value)
    setMessage(result.status === 'completed' ? (result.message || '主号 Codex 已同步') : '主号 Codex 密码已提交')
    emit('admin-progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    syncingMain.value = false
    mainCodexSubmittingAction.value = ''
    codexSubmittingHint.value = ''
  }
}

async function submitMainCodexCode() {
  syncingMain.value = true
  mainCodexSubmittingAction.value = props.codexStatus?.action || 'login'
  codexSubmittingHint.value = '验证码已提交，正在等待主号 Codex 登录页响应，通常需要 5 到 10 秒...'
  try {
    const result = await api.submitMainCodexCode(codexCode.value)
    setMessage(result.status === 'completed' ? (result.message || '主号 Codex 已同步') : '主号 Codex 验证码已提交')
    emit('admin-progress')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    syncingMain.value = false
    mainCodexSubmittingAction.value = ''
    codexSubmittingHint.value = ''
  }
}

async function cancelMainCodexSync() {
  syncingMain.value = true
  try {
    await api.cancelMainCodexSync()
    setMessage('主号 Codex 登录已取消')
    emit('refresh')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    syncingMain.value = false
  }
}

async function deleteMainCodexFromRemoteFiles() {
  deletingMainRemoteFiles.value = true
  try {
    const result = await api.deleteMainCodexFromRemoteFiles()
    setMessage(result.message || '已从已启用远端删除主号文件')
    emit('refresh')
  } catch (e) {
    setMessage(e.message, 'error')
  } finally {
    deletingMainRemoteFiles.value = false
  }
}

async function save() {
  saving.value = true
  saved.value = false
  try {
    const cfg = await api.setAutoCheckConfig({
      interval: form.value.interval * 60,
      target_seats: form.value.target_seats,
      threshold: form.value.threshold,
      min_low: form.value.min_low,
      retry_add_phone: !!form.value.retry_add_phone,
      add_phone_max_retries: form.value.add_phone_max_retries,
    })
    form.value = {
      interval: Math.round(cfg.interval / 60),
      target_seats: cfg.target_seats ?? 5,
      threshold: cfg.threshold,
      min_low: cfg.min_low,
      retry_add_phone: cfg.retry_add_phone ?? true,
      add_phone_max_retries: cfg.add_phone_max_retries ?? 3,
    }
    saved.value = true
    setTimeout(() => { saved.value = false }, 3000)
  } catch (e) {
    console.error('保存失败:', e)
  } finally {
    saving.value = false
  }
}
</script>
