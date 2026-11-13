<template>
  <section class="page" data-module="analysis">
    <header class="page-head">
      <div>
        <h2>分析方案管理</h2>
        <p class="page-desc">定义数据分析方案，包括分析主题、方法选择、显著性检验和输出形式。实验结项后，结论会带同一实验版本、指标口径版本与样本范围启用到这里。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记分析方案</button>
        <button class="btn" type="button" @click="exportRows">导出分析方案清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section v-if="detail" class="settlement-panel">
      <header class="settlement-head">
        <strong>分析方案详情 · {{ detail.analysis_plan?.方案编号 }} / {{ detail.versions?.方案版本 ?? '—' }}</strong>
        <button class="btn ghost" type="button" @click="detail = null">收起</button>
      </header>
      <dl class="settlement-grid">
        <div><dt>关联实验</dt><dd>{{ detail.analysis_plan?.关联实验编号 ?? '—' }}</dd></div>
        <div><dt>实验版本</dt><dd>{{ detail.versions?.实验版本 ?? '—' }}</dd></div>
        <div><dt>结论版本</dt><dd>{{ detail.versions?.结论版本 ?? '—' }}</dd></div>
        <div><dt>核心指标</dt><dd>{{ detail.versions?.核心指标 ?? '—' }}</dd></div>
        <div><dt>指标口径版本</dt><dd>{{ detail.versions?.指标口径版本 ?? '—' }}</dd></div>
        <div><dt>分析状态</dt><dd>{{ detail.analysis_plan?.分析状态 ?? '—' }}</dd></div>
      </dl>
      <div v-if="detail.versions?.['待补充分析项']?.length" class="settlement-pending">
        <strong>待补充分析项</strong>
        <ul>
          <li v-for="item in detail.versions['待补充分析项']" :key="item">{{ item }}</li>
        </ul>
      </div>
      <table class="data-table inner-table">
        <thead>
          <tr><th>分析输出</th><th>输出版本</th><th>指标口径版本</th><th>显著性结果</th><th>样本范围</th><th>状态</th><th>失效原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="output in detail.outputs ?? []" :key="String(output.输出编号)">
            <td>{{ output.输出编号 }}</td>
            <td>{{ output.输出版本 }}</td>
            <td>{{ output.指标口径版本 }}</td>
            <td>{{ output.显著性结果 }}</td>
            <td>{{ output.样本范围 }}</td>
            <td :class="output.status === '有效' ? '' : 'warn-text'">{{ output.status }}</td>
            <td class="muted-text">{{ output.失效原因 ?? '—' }}</td>
          </tr>
          <tr v-if="!(detail.outputs ?? []).length">
            <td colspan="7" class="empty-state">该方案尚未生成分析输出</td>
          </tr>
        </tbody>
      </table>
      <details class="audit-block">
        <summary>审计记录（{{ (detail.audits ?? []).length }} 条）</summary>
        <ul>
          <li v-for="audit in detail.audits ?? []" :key="String(audit.id)">
            {{ audit.时间 }} · {{ audit.动作 }} · {{ audit.结果 }} · {{ audit.说明 }}
          </li>
        </ul>
      </details>
    </section>

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
          <td v-for="column in columns" :key="column" :class="cellClass(row, column)">
            {{ formatCell(row, column) }}
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">查看版本链路</button>
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
          <td :colspan="columns.length + 2" class="empty-state">暂无分析方案数据，可先登记分析方案</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条分析方案记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Cell = string | number | string[] | null
type Row = Record<string, Cell>

type PlanDetail = {
  analysis_plan?: Row
  outputs?: Row[]
  conclusions?: Row[]
  experiment?: Row
  audits?: Row[]
  versions?: Record<string, string | string[] | null>
}

const ENDPOINT = '/api/analysis'
const columns = [
  "方案编号",
  "分析主题",
  "数据集范围",
  "核心指标",
  "对比基准",
  "显著性阈值",
  "方案版本",
  "结论版本",
  "关联实验编号",
  "分析状态",
  "待补充分析项",
]
const actions = ["提交方案评审", "启用方案", "归档方案"]
const statuses = ["草稿", "方案评审中", "已启用", "已归档"]
const stats = [{"label": "分析方案总数", "value": 0}, {"label": "已启用方案", "value": 0}, {"label": "方案评审中", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const detail = ref<PlanDetail | null>(null)
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function formatCell(row: Row, column: string) {
  const value = row[column]
  if (Array.isArray(value)) {
    return value.length ? value.join('；') : '—'
  }
  return value ?? '—'
}

function cellClass(row: Row, column: string) {
  if (column === '待补充分析项') {
    const value = row[column]
    return Array.isArray(value) && value.length ? 'warn-text' : ''
  }
  if (column === '分析状态' && row[column] === '启用失败') {
    return 'warn-text'
  }
  return ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '分析方案登记入口尚未接入审批流'
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/detail`)
    if (!response.ok) {
      throw new Error('分析方案详情读取失败')
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分析方案详情读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('分析方案动作未生效，请稍后重试')
    }
    const payload = await response.json()
    if (action === '启用方案' && payload.message?.includes('失败')) {
      // 启用失败时保留待补充分析项，不能只刷新列表把原因吞掉。
      errorMessage.value = payload.message
    }
    await reload()
    if (detail.value) {
      await openDetail(row)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分析方案操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('分析方案列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats[0].value = total.value
    stats[1].value = rows.value.filter((row) => row.status === '已启用').length
    stats[2].value = rows.value.filter((row) => row.status === '方案评审中').length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分析方案列表读取失败'
  }
}

onMounted(reload)
</script>
