<template>
  <section class="page" data-module="lashing">
    <header class="page-head">
      <div>
        <h2>绑扎加固管理</h2>
        <p class="page-desc">绑扎提交一次落库（单据、箱位、台账），断网重提接回原单；绑扎与拆绑冲突以先落库为准。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">绑扎提交</button>
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
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button
              v-if="row.status === '待绑扎' || row.status === '绑扎中'"
              class="link"
              type="button"
              @click="resubmit(row)"
            >重提</button>
            <button
              v-if="row.status === '待绑扎'"
              class="link"
              type="button"
              @click="runAction('开始绑扎', row)"
            >开始绑扎</button>
            <button
              v-if="row.status === '绑扎中'"
              class="link"
              type="button"
              @click="runAction('确认绑扎', row)"
            >确认绑扎</button>
            <button
              v-if="row.status === '已绑扎'"
              class="link"
              type="button"
              @click="runAction('拆除绑扎', row)"
            >拆除绑扎</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无绑扎加固数据，可先做绑扎提交</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条绑扎加固记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 绑扎提交 / 重提 表单：同绑扎编号复用同一 clientToken，重提接回原单 -->
    <div v-if="formVisible" class="modal-mask" @click.self="closeForm">
      <div class="modal">
        <h3>{{ formMode === 'resubmit' ? '重提绑扎（接回原单）' : '绑扎提交' }}</h3>
        <p class="modal-hint">提交为原子操作：要么单据、箱位、台账一起落库，要么全部不留痕。</p>
        <form @submit.prevent="submitLashing">
          <label class="form-item">
            <span>绑扎编号 *</span>
            <input v-model="form.绑扎编号" :disabled="formMode === 'resubmit'" required />
          </label>
          <label class="form-item">
            <span>对应船舶 *</span>
            <input v-model="form.对应船舶" required />
          </label>
          <label class="form-item">
            <span>箱位范围 *</span>
            <input v-model="form.箱位范围" placeholder="多个箱位用逗号分隔，如 A01-B01,A01-B02" required />
          </label>
          <label class="form-item">
            <span>绑扎方式</span>
            <input v-model="form.绑扎方式" />
          </label>
          <label class="form-item">
            <span>绑扎材料</span>
            <input v-model="form.绑扎材料" placeholder="已填材料在重提时会被保留" />
          </label>
          <label class="form-item">
            <span>绑扎班组</span>
            <input v-model="form.绑扎班组" />
          </label>
          <label class="form-item">
            <span>箱量（留空按箱位范围解析）</span>
            <input v-model="form.箱量" inputmode="numeric" />
          </label>
          <div class="modal-actions">
            <button class="btn" type="button" @click="closeForm">取消</button>
            <button class="btn primary" type="submit" :disabled="submitting">
              {{ submitting ? '提交中…' : '提交绑扎' }}
            </button>
          </div>
          <p v-if="formError" class="error-text">{{ formError }}</p>
        </form>
      </div>
    </div>

    <!-- 详情：与列表同源，刷新读到的仍是同一条 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal">
        <h3>绑扎任务详情</h3>
        <dl class="detail-list">
          <div v-for="[label, value] in detailPairs" :key="label">
            <dt>{{ label }}</dt>
            <dd>{{ value ?? '—' }}</dd>
          </div>
        </dl>
        <div class="modal-actions">
          <button class="btn" type="button" @click="detail = null">关闭</button>
          <button
            v-if="detail.status === '待绑扎'"
            class="btn primary" type="button"
            @click="runAction('开始绑扎', detail)"
          >开始绑扎</button>
          <button
            v-if="detail.status === '绑扎中'"
            class="btn primary" type="button"
            @click="runAction('确认绑扎', detail)"
          >确认绑扎</button>
          <button
            v-if="detail.status === '已绑扎'"
            class="btn primary" type="button"
            @click="runAction('拆除绑扎', detail)"
          >拆除绑扎</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/lashing'
const columns = ['绑扎编号', '对应船舶', '箱位范围', '绑扎方式', '绑扎材料', '绑扎班组', '绑扎耗时', '绑扎状态']
const statuses = ['待绑扎', '绑扎中', '已绑扎', '已拆除']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')

// 看板数字全部由后端随单据重算，不再写死 0。
const stats = ref([
  { label: '待绑扎任务', value: 0 },
  { label: '绑扎中任务', value: 0 },
  { label: '已绑扎任务', value: 0 },
  { label: '已绑箱数', value: 0 },
])

const formVisible = ref(false)
const formMode = ref<'create' | 'resubmit'>('create')
const submitting = ref(false)
const formError = ref('')
const detail = ref<Row | null>(null)

function emptyForm(): Record<string, string> {
  return { 绑扎编号: '', 对应船舶: '', 箱位范围: '', 绑扎方式: '', 绑扎材料: '', 绑扎班组: '', 箱量: '' }
}
const form = reactive<Record<string, string>>(emptyForm())

// 幂等键按绑扎编号记忆：同一张单无论换谁、断几次重提，都接回原来那条。
const tokenByCode: Record<string, string> = {}
function clientToken(code: string): string {
  if (!tokenByCode[code]) {
    tokenByCode[code] = (globalThis.crypto?.randomUUID?.() ?? `req-${Date.now()}-${Math.random()}`)
  }
  return tokenByCode[code]
}

const detailPairs = computed<[string, unknown][]>(() => {
  if (!detail.value) return []
  const d = detail.value
  return [
    ['绑扎编号', d['绑扎编号']],
    ['对应船舶', d['对应船舶']],
    ['箱位范围', d['箱位范围']],
    ['箱量', d['箱量']],
    ['绑扎方式', d['绑扎方式']],
    ['绑扎材料', d['绑扎材料']],
    ['绑扎班组', d['绑扎班组']],
    ['绑扎耗时', d['绑扎耗时']],
    ['绑扎状态', d['绑扎状态']],
    ['数据版本', d['version']],
  ]
})

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  formMode.value = 'create'
  Object.assign(form, emptyForm())
  formError.value = ''
  formVisible.value = true
}

function resubmit(row: Row) {
  formMode.value = 'resubmit'
  Object.assign(form, emptyForm())
  // 重提先带回原单内容（含已填绑扎材料）；服务端也会再次保住非空字段。
  for (const key of Object.keys(form)) {
    const value = row[key]
    if (value !== null && value !== undefined && value !== '—') form[key] = String(value)
  }
  formError.value = ''
  formVisible.value = true
}

function closeForm() {
  if (submitting.value) return
  formVisible.value = false
}

async function submitLashing() {
  formError.value = ''
  submitting.value = true
  const code = form['绑扎编号'].trim()
  const values: Record<string, string> = { ...form, request_id: clientToken(code) }
  try {
    const response = await request(`${ENDPOINT}/submit`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '绑扎提交未成功，请直接点重提，会从断掉的那段接着走')
    }
    formVisible.value = false
    detail.value = null
    await reload()
  } catch (error) {
    // 网络中断时不新建：保留表单，用户原样再点提交即按 request_id / 绑扎编号接续。
    formError.value = error instanceof Error ? error.message : '绑扎提交失败，请重提接续'
  } finally {
    submitting.value = false
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error('绑扎详情读取失败')
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绑扎详情读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, expected_version: row.version } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '绑扎动作未生效，请稍后重试')
    }
    if (detail.value && String(detail.value.id) === String(row.id) && payload.entry) {
      detail.value = payload.entry
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绑扎操作失败'
  }
}

async function loadBoard() {
  try {
    const response = await request(`${ENDPOINT}/board/summary`)
    if (!response.ok) return
    const summary = await response.json()
    stats.value = [
      { label: '待绑扎任务', value: Number(summary['待绑扎任务'] ?? 0) },
      { label: '绑扎中任务', value: Number(summary['绑扎中任务'] ?? 0) },
      { label: '已绑扎任务', value: Number(summary['已绑扎任务'] ?? 0) },
      { label: '已绑箱数', value: Number(summary['已绑箱数'] ?? 0) },
    ]
  } catch {
    // 看板读取失败不阻塞列表，保留上一次数字。
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('绑扎任务列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadBoard()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绑扎加固列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}

.modal {
  width: min(560px, 92vw);
  max-height: 86vh;
  overflow: auto;
  background: var(--color-surface, #fff);
  border-radius: 12px;
  padding: 20px 22px;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.25);
}

.modal-hint {
  margin: 6px 0 14px;
  color: #64748b;
  font-size: 13px;
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}

.form-item span {
  font-size: 13px;
  color: #475569;
}

.form-item input,
.form-item select {
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
}

.form-item input:disabled {
  background: #f1f5f9;
  color: #64748b;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}

.detail-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 18px;
  margin: 12px 0 16px;
}

.detail-list dt {
  font-size: 12px;
  color: #94a3b8;
}

.detail-list dd {
  margin: 2px 0 0;
  font-size: 14px;
  color: #0f172a;
}
</style>
