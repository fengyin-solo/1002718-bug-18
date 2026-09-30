<template>
  <section class="page" data-module="lashing">
    <header class="page-head">
      <div>
        <h2>绑扎加固管理</h2>
        <p class="page-desc">绑扎提交整单原子落库，断网可凭提交凭证从断点接走；同一绑扎编号重提只接回不重建。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate()">登记绑扎任务</button>
        <button class="btn" type="button" @click="exportRows">导出绑扎加固清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>绑扎编号</span>
        <input v-model="keyword" placeholder="按绑扎编号检索" />
      </label>
      <label class="filter-item">
        <span>绑扎状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-flash': row.id === flashId }">
          <td v-for="column in columns" :key="column">
            <button v-if="column === '绑扎编号'" class="link" type="button" @click="openDetail(row)">
              {{ row[column] ?? '—' }}
            </button>
            <span v-else-if="column === '绑扎状态'">
              <span class="badge" :class="badgeClass(row.status)">{{ row[column] ?? '—' }}</span>
            </span>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">明细</button>
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无绑扎加固数据，可先登记绑扎任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条绑扎加固记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="successMessage" class="success-text">{{ successMessage }}</span>
    </footer>

    <!-- 登记/重提弹窗 -->
    <div v-if="formVisible" class="modal-mask" @click.self="closeForm">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-head">
          <h3>{{ form.id ? `接回绑扎任务 ${form['绑扎编号']}` : '登记绑扎任务' }}</h3>
          <button class="btn ghost" type="button" @click="closeForm">关闭</button>
        </div>
        <div class="modal-body">
          <div class="form-grid">
            <label class="form-field">
              <label>绑扎编号 *</label>
              <input v-model="form['绑扎编号']" placeholder="如 LASH-1001" />
            </label>
            <label class="form-field">
              <label>对应船舶 *</label>
              <input v-model="form['对应船舶']" placeholder="如 远洋荣耀" />
            </label>
            <label class="form-field full">
              <label>箱位范围 *（多个箱位用逗号分隔）</label>
              <textarea v-model="form['箱位范围']" placeholder="如 01-01-02,01-01-04,01-01-06"></textarea>
            </label>
            <label class="form-field">
              <label>绑扎方式</label>
              <input v-model="form['绑扎方式']" placeholder="钢丝绳固定 / 链条绑扎 等" />
            </label>
            <label class="form-field">
              <label>绑扎班组</label>
              <input v-model="form['绑扎班组']" placeholder="如 甲班一组" />
            </label>
            <label class="form-field full">
              <label>绑扎材料（断网重提时已填材料会保留）</label>
              <textarea v-model="form['绑扎材料']" placeholder="如 钢丝绳×6、卸扣×6"></textarea>
            </label>
          </div>
          <p v-if="resumeHint" class="form-tip">{{ resumeHint }}</p>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" type="button" @click="closeForm">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitForm">
            {{ submitting ? '提交中…' : '提交绑扎' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 明细弹窗：列表点进来看到的箱位与列表同源 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-head">
          <h3>绑扎明细 {{ detail['绑扎编号'] }}</h3>
          <button class="btn ghost" type="button" @click="detail = null">关闭</button>
        </div>
        <div class="modal-body">
          <div class="form-grid">
            <div class="form-field"><label>对应船舶</label><div>{{ detail['对应船舶'] || '—' }}</div></div>
            <div class="form-field"><label>绑扎状态</label><div><span class="badge" :class="badgeClass(detail.status)">{{ detail['绑扎状态'] }}</span></div></div>
            <div class="form-field full"><label>箱位范围</label><div>{{ detail['箱位范围'] || '—' }}</div></div>
            <div class="form-field"><label>已绑 / 总箱数</label><div>{{ detail['已绑箱数'] }} / {{ detail['总箱数'] }}</div></div>
            <div class="form-field"><label>绑扎耗时</label><div>{{ detail['绑扎耗时'] || '—' }}</div></div>
            <div class="form-field"><label>绑扎方式</label><div>{{ detail['绑扎方式'] || '—' }}</div></div>
            <div class="form-field"><label>绑扎班组</label><div>{{ detail['绑扎班组'] || '—' }}</div></div>
            <div class="form-field full"><label>绑扎材料</label><div>{{ detail['绑扎材料'] || '—' }}</div></div>
          </div>
          <h4 style="margin:14px 0 6px;font-size:13px;">箱位明细（{{ (detail['箱位明细'] ?? []).length }} 个）</h4>
          <ul class="detail-list">
            <li v-for="slot in detail['箱位明细'] ?? []" :key="slot.slot_no">
              {{ slot.slot_no }} — {{ slot.status }}
            </li>
          </ul>
          <h4 style="margin:14px 0 6px;font-size:13px;">动作流水</h4>
          <ul class="detail-list" v-if="(detail['动作流水'] ?? []).length">
            <li v-for="event in detail['动作流水']" :key="event.id">
              {{ event.occurred_at }} {{ event.action }}（{{ event.operator || '值班管理员' }}）
            </li>
          </ul>
          <p v-else class="form-tip">暂无动作记录</p>
        </div>
        <div class="modal-foot">
          <button
            v-for="action in availableActions(detail)"
            :key="action"
            class="btn"
            :class="{ primary: action === '确认绑扎' }"
            type="button"
            @click="runAction(action, detail)"
          >
            {{ action }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null>
type Slot = { id: number; task_id: number; slot_no: string; status: string }
type LashingEvent = { id: number; task_id: number; action: string; operator: string; occurred_at: string }
type Detail = Row & { '箱位明细'?: Slot[]; '动作流水'?: LashingEvent[]; '已绑箱数'?: number; '总箱数'?: number }

const ENDPOINT = '/api/lashing'
const DRAFT_KEY = 'lashing.submit.draft'
const LAST_ID_KEY = 'lashing.lastId'
const columns = ['绑扎编号', '对应船舶', '箱位范围', '绑扎方式', '绑扎材料', '绑扎班组', '绑扎耗时', '绑扎状态']
const statuses = ['待绑扎', '绑扎中', '已绑扎', '已拆除']
const stats = ref([
  { label: '待绑扎任务', value: 0 },
  { label: '绑扎中任务', value: 0 },
  { label: '已绑扎任务', value: 0 },
  { label: '已绑箱数', value: 0 },
])

const session = useSessionStore()
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const flashId = ref<number | null>(null)

const formVisible = ref(false)
const submitting = ref(false)
const form = reactive<Row>({ '绑扎编号': '', '对应船舶': '', '箱位范围': '', '绑扎方式': '', '绑扎材料': '', '绑扎班组': '' })
const resumeHint = ref('')
const detail = ref<Detail | null>(null)

function badgeClass(status: unknown): string {
  if (status === '已绑扎') return 'lashed'
  if (status === '绑扎中') return 'doing'
  if (status === '已拆除') return 'removed'
  return ''
}

function availableActions(row: Row): string[] {
  switch (row.status) {
    case '待绑扎':
      return ['开始绑扎']
    case '绑扎中':
      return ['确认绑扎', '拆除绑扎']
    case '已绑扎':
      return ['拆除绑扎']
    default:
      return []
  }
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function newToken(): string {
  if (window.crypto && 'randomUUID' in window.crypto) {
    return `SUB-${window.crypto.randomUUID()}`
  }
  return `SUB-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

// 打开登记表单：默认带出上次没提交成功的草稿（连同提交凭证），断网重提从断点接着走
function openCreate(prefill?: Row) {
  errorMessage.value = ''
  Object.assign(form, { '绑扎编号': '', '对应船舶': '', '箱位范围': '', '绑扎方式': '', '绑扎材料': '', '绑扎班组': '' })
  resumeHint.value = ''
  if (prefill) {
    Object.assign(form, prefill)
  } else {
    const saved = localStorage.getItem(DRAFT_KEY)
    if (saved) {
      try {
        const draft = JSON.parse(saved) as { values: Row; token: string }
        Object.assign(form, draft.values)
        resumeHint.value = '检测到上次未完成的绑扎提交，已带出原内容（含绑扎材料），将凭原凭证接回。'
      } catch {
        localStorage.removeItem(DRAFT_KEY)
      }
    }
  }
  formVisible.value = true
}

function closeForm() {
  formVisible.value = false
}

async function submitForm() {
  errorMessage.value = ''
  successMessage.value = ''
  const values: Row = {
    '绑扎编号': form['绑扎编号'],
    '对应船舶': form['对应船舶'],
    '箱位范围': form['箱位范围'],
    '绑扎方式': form['绑扎方式'],
    '绑扎材料': form['绑扎材料'],
    '绑扎班组': form['绑扎班组'],
    operator: session.operator,
  }
  // 凭证跟着草稿走：无论断在 prepare 还是 commit，重试都是同一条提交，不会开新单
  let token = ''
  let attempt = 0
  const saved = localStorage.getItem(DRAFT_KEY)
  if (saved) {
    try {
      const draft = JSON.parse(saved) as { token: string; attempt?: number }
      token = draft.token
      attempt = draft.attempt ?? 0
    } catch {
      token = ''
    }
  }
  if (!token) token = newToken()
  attempt += 1
  localStorage.setItem(DRAFT_KEY, JSON.stringify({ token, values, attempt }))
  submitting.value = true
  try {
    // 第一段：暂存（失败也能用同一 token 重来）
    const prepared = await postJson(`${ENDPOINT}`, { ...values, phase: 'prepare', token })
    const commitToken = prepared.token ?? token
    // 第二段：整单原子落库；这一步要么全落要么不留痕，失败重提仍接同一凭证
    const committed = await postJson(`${ENDPOINT}`, {
      ...values,
      phase: 'commit',
      token: commitToken,
      // attempt>1 说明此前断过：服务端按断点续传处理，而不是另起一单
      retry: attempt > 1 ? 1 : 0,
    })
    localStorage.removeItem(DRAFT_KEY)
    formVisible.value = false
    successMessage.value = committed.message ?? '绑扎任务已提交'
    if (committed.entry?.id) {
      localStorage.setItem(LAST_ID_KEY, String(committed.entry.id))
    }
    await reload()
    if (committed.entry?.id) await openDetailById(Number(committed.entry.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绑扎提交失败，可重新提交，将从断点接着走'
  } finally {
    submitting.value = false
  }
}

async function postJson(path: string, values: Record<string, unknown>) {
  const response = await request(path, { method: 'POST', body: JSON.stringify({ values }) })
  const body = (await response.json().catch(() => ({}))) as { ok?: boolean; message?: string; token?: string; entry?: Detail }
  if (!response.ok || body.ok === false) {
    throw new Error(body.message || `提交未生效（${response.status}），请重试，原有内容不会丢失`)
  }
  return body
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  // 动作也带独立凭证：网络重试不会把同一个拆除/确认执行两遍
  const token = `ACT-${Date.now()}-${Math.random().toString(16).slice(2)}`
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, token, operator: session.operator } }),
    })
    const body = (await response.json().catch(() => ({}))) as { ok?: boolean; message?: string; detail?: string }
    if (response.status === 409) {
      throw new Error(body.detail || '该动作与已落库的动作冲突，以先落库的记录为准')
    }
    if (!response.ok || body.ok === false) {
      throw new Error(body.message || '绑扎加固动作未生效，请稍后重试')
    }
    successMessage.value = body.message || `已${action}`
    localStorage.setItem(LAST_ID_KEY, String(row.id))
    await reload()
    if (detail.value && detail.value.id === row.id) await openDetailById(Number(row.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绑扎加固操作失败'
  }
}

async function openDetailById(id: number) {
  try {
    const response = await request(`${ENDPOINT}/${id}`)
    if (!response.ok) throw new Error('明细读取失败')
    detail.value = (await response.json()) as Detail
    flashId.value = id
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绑扎明细读取失败'
  }
}

function openDetail(row: Row) {
  localStorage.setItem(LAST_ID_KEY, String(row.id))
  void openDetailById(Number(row.id))
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const [listResp, statsResp] = await Promise.all([
      request(`${ENDPOINT}?${query.toString()}`),
      request(`${ENDPOINT}/stats`),
    ])
    if (!listResp.ok) throw new Error('绑扎任务列表读取失败')
    const payload = (await listResp.json()) as { items?: Row[]; total?: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (statsResp.ok) {
      const board = (await statsResp.json()) as Record<string, number>
      stats.value = [
        { label: '待绑扎任务', value: board['待绑扎任务'] ?? 0 },
        { label: '绑扎中任务', value: board['绑扎中任务'] ?? 0 },
        { label: '已绑扎任务', value: board['已绑扎任务'] ?? 0 },
        { label: '已绑箱数', value: board['已绑箱数'] ?? 0 },
      ]
    }
    // 刷新后仍定位到最新那条（重提接回时 id 不变）
    const lastId = Number(localStorage.getItem(LAST_ID_KEY))
    flashId.value = lastId && rows.value.some((row) => Number(row.id) === lastId) ? lastId : null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绑扎加固列表读取失败'
  }
}

onMounted(reload)
</script>
