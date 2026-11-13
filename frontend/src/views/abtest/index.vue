<template>
  <section class="page" data-module="abtest">
    <header class="page-head">
      <div>
        <h2>实验设计管理</h2>
        <p class="page-desc">设计A/B实验方案，定义对照组与实验组、分流比例、核心评估指标和样本量要求。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记实验方案</button>
        <button class="btn" type="button" @click="exportRows">导出实验设计清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section v-if="settlement" class="settlement-panel">
      <header class="settlement-head">
        <strong>结项结论沉淀</strong>
        <button class="btn ghost" type="button" @click="settlement = null">收起</button>
      </header>
      <p class="settlement-msg">{{ settlement.message }}</p>
      <dl class="settlement-grid">
        <div><dt>实验版本</dt><dd>{{ settlement.experiment?.实验版本 ?? '—' }}</dd></div>
        <div><dt>结论版本</dt><dd>{{ settlement.conclusion?.结论版本 ?? '—' }}</dd></div>
        <div><dt>核心指标</dt><dd>{{ settlement.conclusion?.核心指标 ?? settlement.experiment?.核心指标 ?? '—' }}</dd></div>
        <div><dt>指标口径版本</dt><dd>{{ settlement.conclusion?.指标口径版本 ?? '—' }}</dd></div>
        <div><dt>显著性结果</dt><dd>{{ settlement.conclusion?.显著性结果 ?? '—' }}</dd></div>
        <div><dt>对比基准</dt><dd>{{ settlement.conclusion?.对比基准 ?? '—' }}</dd></div>
        <div><dt>样本范围</dt><dd>{{ settlement.conclusion?.样本范围 ?? '—' }}</dd></div>
        <div><dt>分析方案</dt><dd>{{ settlement.analysis_plan?.方案编号 ?? '—' }} / {{ settlement.analysis_plan?.方案版本 ?? '—' }}</dd></div>
        <div><dt>分析输出</dt><dd>{{ settlement.output?.输出编号 ?? '—' }} / {{ settlement.output?.输出版本 ?? '—' }}</dd></div>
      </dl>
      <div v-if="settlement['待补充分析项']?.length" class="settlement-pending">
        <strong>待补充分析项</strong>
        <ul>
          <li v-for="item in settlement['待补充分析项']" :key="item">{{ item }}</li>
        </ul>
      </div>
    </section>

    <form v-if="closing" class="filter-bar close-form" @submit.prevent="submitClose">
      <span class="close-title">结项 {{ closing.方案编号 }}</span>
      <label class="filter-item">
        <span>实际样本量</span>
        <input v-model="closeForm.sample" placeholder="需达到最小样本量" />
      </label>
      <label class="filter-item">
        <span>样本范围</span>
        <input v-model="closeForm.range" placeholder="如 2026-10-01 至 2026-10-21 分流用户" />
      </label>
      <label class="filter-item">
        <span>显著性 p 值</span>
        <input v-model="closeForm.pValue" placeholder="如 0.03" />
      </label>
      <label class="filter-item">
        <span>分析方案编号</span>
        <input v-model="closeForm.planCode" placeholder="留空则按实验自动新建" />
      </label>
      <button class="btn primary" type="submit">确认结项</button>
      <button class="btn ghost" type="button" @click="closing = null">取消</button>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
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
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ formatCell(row, column) }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
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
          <td :colspan="columns.length + 1" class="empty-state">暂无实验设计数据，可先登记实验方案</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条实验设计记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | string[] | null>

type Settlement = {
  status: string
  message: string
  experiment?: Row
  conclusion?: Row
  output?: Row
  analysis_plan?: Row
  待补充分析项?: string[]
}

const ENDPOINT = '/api/abtest'
const columns = [
  "方案编号",
  "实验名称",
  "核心指标",
  "最小样本量",
  "实际样本量",
  "实验版本",
  "结论版本",
  "显著性结果",
  "对比基准",
  "样本范围",
  "分析状态",
]
const actions = ["提交方案评审", "启动实验", "结项实验"]
const statuses = ["草稿", "方案评审中", "实验中", "已结项", "已取消"]
const stats = [{"label": "实验方案总数", "value": 0}, {"label": "实验中方案", "value": 0}, {"label": "已结项方案", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const settlement = ref<Settlement | null>(null)
const closing = ref<Row | null>(null)
const closeForm = ref({ sample: '', range: '', pValue: '', planCode: '' })
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function formatCell(row: Row, column: string) {
  const value = row[column]
  if (Array.isArray(value)) {
    return value.length ? value.join('；') : '—'
  }
  return value ?? '—'
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '实验方案登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  if (action === '结项实验') {
    // 结项必须先填实际样本量与样本范围，否则后端无法判断是否达到最小样本量。
    closing.value = row
    closeForm.value = { sample: '', range: '', pValue: '', planCode: '' }
    return
  }
  await postAction(action, row)
}

function submitClose() {
  if (!closing.value) {
    return
  }
  const values: Record<string, string> = {
    action: '结项实验',
    实际样本量: closeForm.value.sample,
    样本范围: closeForm.value.range,
  }
  if (closeForm.value.pValue.trim()) {
    values['p值'] = closeForm.value.pValue.trim()
  }
  if (closeForm.value.planCode.trim()) {
    values['分析方案编号'] = closeForm.value.planCode.trim()
  }
  void postAction('结项实验', closing.value, values).then((ok) => {
    if (ok) {
      closing.value = null
    }
  })
}

async function postAction(action: string, row: Row, values?: Record<string, string>): Promise<boolean> {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, values }),
    })
    if (!response.ok) {
      throw new Error('实验设计动作未生效，请稍后重试')
    }
    const payload = await response.json()
    const result = payload.extra?.settlement ?? null
    settlement.value = result
    if (result) {
      // 结项失败也要把待补充分析项显示出来，不能只刷新列表看不出差异。
      if (result.status === 'enable_failed' || result.status === 'blocked') {
        errorMessage.value = result.message
      }
      await reload()
      return result.status !== 'blocked'
    }
    await reload()
    return true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '实验设计操作失败'
    return false
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('实验方案列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats[0].value = total.value
    stats[1].value = rows.value.filter((row) => row.status === '实验中').length
    stats[2].value = rows.value.filter((row) => row.status === '已结项').length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '实验设计列表读取失败'
  }
}

onMounted(reload)
</script>
